"""
Scheduler for the `/realestate` HTTP catalog source.

Runs independently of the browser-based map scraper (SmartScheduler). Each tick
fetches the full catalog for the configured server over a single HTTP request,
diffs it against the DB, and records freed/occupied/owner_changed events for the
notifier to deliver.

Like the map scraper it is Payday-aware: it polls fast inside the Payday window
(when objects most often free up) and slowly outside it, so a freed house is
caught as quickly as possible.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from app.config import get_settings
from app.database.session import DatabaseSession
from app.database.repository import ScraperLogRepository
from app.scraper.realestate_client import RealEstateClient, resolve_servers
from app.scraper.realestate_detector import RealEstateDetector


class RealEstateScheduler:
    """Periodically fetches the realestate catalog(s) and detects changes.

    Monitors every server listed in REALESTATE_SERVERS (falling back to the
    single REALESTATE_SERVER). Each server is fetched, gated and diffed
    independently on every tick, and its own data-refresh marker is tracked
    separately so one server recomputing doesn't force a diff on the others.
    """

    def __init__(self):
        self.settings = get_settings()
        self._client = RealEstateClient()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # {sid: display_name} for every server we monitor.
        self._servers: dict = {}
        self._last_tick: Optional[datetime] = None
        self._total_runs = 0
        self._successful_runs = 0
        # Per-server data-refresh marker (`fetchedAtMs`) of the last snapshot we
        # actually diffed. The catalog keeps this constant until the wiki
        # recomputes it (around Payday), so we only run the full diff when it
        # advances — tracked per sid so servers don't interfere.
        self._last_processed_fetched_at_ms: dict = {}

    async def start(self) -> None:
        """Resolve the server sids and begin the polling loop."""
        rs = self.settings.realestate
        if not rs.enabled:
            logger.info("RealEstate source disabled (REALESTATE_ENABLED=false)")
            return

        self._servers = resolve_servers(rs.server_names)
        if not self._servers:
            logger.error(
                f"No resolvable servers in REALESTATE_SERVERS/REALESTATE_SERVER "
                f"({rs.server_names}); realestate source not started"
            )
            return

        self._running = True
        listing = ", ".join(f"{n}({s})" for s, n in self._servers.items())
        logger.info(
            f"RealEstate scheduler started: servers=[{listing}], "
            f"interval={rs.interval}s"
        )

        # Initial fetch to establish the baseline immediately.
        await self._tick()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("RealEstate scheduler stopped")

    async def _loop(self) -> None:
        while self._running:
            interval = self._current_interval()
            elapsed = (
                (datetime.now(timezone.utc) - self._last_tick).total_seconds()
                if self._last_tick else interval + 1
            )
            if elapsed >= interval:
                await self._tick()
            await asyncio.sleep(1)

    def _in_payday_window(self) -> bool:
        """Whether the current minute falls inside the configured Payday window."""
        smart = self.settings.smart_mode
        if not smart.smart_mode:
            return False
        minute = datetime.now(timezone.utc).minute
        start, end = smart.payday_start_minute, smart.payday_end_minute
        return (
            start <= minute <= end if start <= end
            else (minute >= start or minute <= end)
        )

    def _current_interval(self) -> int:
        """Poll fast inside the Payday window, slow outside it."""
        rs = self.settings.realestate
        smart = self.settings.smart_mode
        if not smart.smart_mode:
            return rs.interval

        # Inside Payday poll as fast as the map scraper does, but never below 5s.
        return max(smart.high_interval, 5) if self._in_payday_window() else rs.interval

    async def force_fetch(self) -> int:
        """Run one fetch/diff cycle immediately; return the number of changes."""
        return await self._tick()

    async def _tick(self) -> int:
        """Fetch and diff every monitored server. Returns total change count."""
        self._last_tick = datetime.now(timezone.utc)
        total_changes = 0
        for sid, name in self._servers.items():
            total_changes += await self._tick_server(sid, name)
        return total_changes

    async def _tick_server(self, sid: str, name: str) -> int:
        """Fetch one server's catalog, diff it, and persist events."""
        self._total_runs += 1
        start_time = datetime.utcnow()

        snapshot = await self._client.fetch_snapshot(sid)
        if snapshot is None:
            logger.warning(f"RealEstate fetch returned no snapshot for {name}({sid})")
            await self._save_log(start_time, "error", 0, 0, f"{name}: fetch returned None")
            return 0

        # Gate the expensive diff on the data-refresh marker. If the catalog
        # hasn't been recomputed since our last diff, nothing can have changed,
        # so skip the DB work entirely and just note the (cheap) fetch.
        fetched_ms = snapshot.fetched_at_ms
        if (
            fetched_ms is not None
            and fetched_ms == self._last_processed_fetched_at_ms.get(sid)
        ):
            logger.debug(
                f"RealEstate {name}: catalog unchanged "
                f"(fetchedAtMs={fetched_ms}), skipping diff"
            )
            self._successful_runs += 1
            duration = (datetime.utcnow() - start_time).total_seconds()
            await self._save_log(start_time, "skipped", 0, 0, None, duration)
            return 0

        is_payday = self._in_payday_window()
        try:
            async with DatabaseSession.get_session_context() as session:
                detector = RealEstateDetector(session)
                changes = await detector.process_snapshot(snapshot, is_payday=is_payday)

                # Reaching this block means the catalog was recomputed since our
                # last diff (its fetchedAtMs advanced) — i.e. a map update just
                # landed, which is when Payday churn settles. Persist the new
                # marker so the notifier can emit exactly one per-Payday report
                # per recompute, independently of this scheduler's in-memory state.
                if fetched_ms is not None:
                    from app.database.repository import ScraperSettingsRepository
                    await ScraperSettingsRepository(session).set(
                        f"catalog_recompute:{sid}", str(fetched_ms),
                        "fetchedAtMs of the last diffed catalog snapshot (map update marker)",
                    )

            # Diff succeeded — remember this marker so identical snapshots are
            # skipped until the catalog is recomputed.
            self._last_processed_fetched_at_ms[sid] = fetched_ms
            self._successful_runs += 1
            freed = sum(1 for c in changes if c.event_type == "freed")
            possibly = sum(1 for c in changes if c.event_type == "possibly_freed")
            if freed:
                logger.info(f"🏠 RealEstate {name}: {freed} object(s) freed up!")
            if possibly:
                logger.info(f"🟡 RealEstate {name}: {possibly} object(s) possibly freed (Payday owner change)")

            checked = len(snapshot.houses) + len(snapshot.apartments)
            duration = (datetime.utcnow() - start_time).total_seconds()
            await self._save_log(
                start_time, "success", checked, len(changes), None, duration
            )
            return len(changes)

        except Exception as e:
            logger.error(f"RealEstate diff failed for {name}({sid}): {e}")
            await self._save_log(start_time, "error", 0, 0, f"{name}: {e}")
            return 0

    async def _save_log(
        self,
        start_time: datetime,
        status: str,
        checked: int,
        changes: int,
        error: Optional[str],
        duration: Optional[float] = None,
    ) -> None:
        """Record the run in scraper_logs (shared with the map scraper)."""
        try:
            async with DatabaseSession.get_session_context() as session:
                repo = ScraperLogRepository(session)
                await repo.create({
                    "status": status,
                    "apartments_checked": checked,
                    "apartments_success": checked if status == "success" else 0,
                    "apartments_failed": 0 if status == "success" else checked,
                    "changes_detected": changes,
                    "duration_seconds": duration,
                    "error_message": error,
                    "is_payday_run": self._current_interval() <= max(
                        self.settings.smart_mode.high_interval, 5
                    ),
                })
        except Exception as e:
            logger.warning(f"Failed to save realestate log: {e}")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "total_runs": self._total_runs,
            "successful_runs": self._successful_runs,
            "failed_runs": self._total_runs - self._successful_runs,
            "servers": dict(self._servers),
            "is_running": self._running,
            "last_processed_fetched_at_ms": dict(self._last_processed_fetched_at_ms),
        }

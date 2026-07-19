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
from app.scraper.realestate_client import RealEstateClient, server_name_to_sid
from app.scraper.realestate_detector import RealEstateDetector


class RealEstateScheduler:
    """Periodically fetches the realestate catalog and detects changes."""

    def __init__(self):
        self.settings = get_settings()
        self._client = RealEstateClient()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._server_sid: Optional[str] = None
        self._last_tick: Optional[datetime] = None
        self._total_runs = 0
        self._successful_runs = 0

    async def start(self) -> None:
        """Resolve the server sid and begin the polling loop."""
        rs = self.settings.realestate
        if not rs.enabled:
            logger.info("RealEstate source disabled (REALESTATE_ENABLED=false)")
            return

        self._server_sid = server_name_to_sid(rs.server_name)
        if not self._server_sid:
            logger.error(
                f"Unknown REALESTATE_SERVER '{rs.server_name}'; realestate source not started"
            )
            return

        self._running = True
        logger.info(
            f"RealEstate scheduler started: server={rs.server_name} "
            f"(sid={self._server_sid}), interval={rs.interval}s"
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
        """Fetch the catalog, diff it, and persist events. Returns change count."""
        self._last_tick = datetime.now(timezone.utc)
        self._total_runs += 1
        start_time = datetime.utcnow()

        snapshot = await self._client.fetch_snapshot(self._server_sid)
        if snapshot is None:
            logger.warning("RealEstate fetch returned no snapshot")
            await self._save_log(start_time, "error", 0, 0, "fetch returned None")
            return 0

        is_payday = self._in_payday_window()
        try:
            async with DatabaseSession.get_session_context() as session:
                detector = RealEstateDetector(session)
                changes = await detector.process_snapshot(snapshot, is_payday=is_payday)

            self._successful_runs += 1
            freed = sum(1 for c in changes if c.event_type == "freed")
            possibly = sum(1 for c in changes if c.event_type == "possibly_freed")
            if freed:
                logger.info(f"🏠 RealEstate: {freed} object(s) freed up!")
            if possibly:
                logger.info(f"🟡 RealEstate: {possibly} object(s) possibly freed (Payday owner change)")

            checked = len(snapshot.houses) + len(snapshot.apartments)
            duration = (datetime.utcnow() - start_time).total_seconds()
            await self._save_log(
                start_time, "success", checked, len(changes), None, duration
            )
            return len(changes)

        except Exception as e:
            logger.error(f"RealEstate diff failed: {e}")
            await self._save_log(start_time, "error", 0, 0, str(e))
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
            "server_sid": self._server_sid,
            "is_running": self._running,
        }

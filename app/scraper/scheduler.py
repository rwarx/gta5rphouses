"""
Smart scheduler with Payday-aware monitoring.
Dynamically adjusts check intervals based on time proximity to Payday (HH:59).
Uses low frequency during off-peak hours and high frequency during Payday window.
"""

import asyncio
import random
from typing import Optional, Callable, Awaitable, List, Dict, Any
from datetime import datetime, timedelta, timezone
from enum import Enum

from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.session import DatabaseSession
from app.database.repository import ScraperLogRepository
from app.scraper.anti_detect import AntiDetectManager, create_browser_context
from app.scraper.playwright_scraper import ApartmentScraper, ApartmentData
from app.scraper.change_detector import ChangeDetector
from app.scraper.crash_detector import get_crash_detector, CrashEvent
from app.scraper.realestate_client import RealEstateClient, server_name_to_sid


class MonitorMode(Enum):
    """Monitoring frequency modes."""
    LOW = "low"      # Off-peak: every 10 minutes
    HIGH = "high"    # Payday window: every 5 seconds
    RECOVERY = "recovery"  # Retry on failure


class SmartScheduler:
    """
    Smart scheduler that adapts check frequency based on Payday proximity.
    Implements the Smart Mode described in requirements.
    """

    def __init__(self):
        self.settings = get_settings()
        self.scheduler = AsyncIOScheduler(timezone=self.settings.timezone)
        self._running = False
        self._current_mode = MonitorMode.LOW
        self._browser_manager: Optional[AntiDetectManager] = None
        self._consecutive_failures = 0
        self._total_runs = 0
        self._successful_runs = 0
        self._last_tick_time: Optional[datetime] = None
        self._cached_icons_data: List[Dict[str, Any]] = []
        self._icons_cached = False
        # Map-update gate: launch the browser scrape only when the /realestate
        # catalog reports new data (its `fetchedAtMs` marker advanced). The
        # catalog HTTP fetch is cheap (~80 KB) compared to driving a browser
        # over ~35 markers, and the marker is the same "Обновлено" value the map
        # shows, so it tells us exactly when a scrape would find anything new.
        self._map_gate_client: Optional[RealEstateClient] = None
        self._map_gate_sid: Optional[str] = None
        self._last_scraped_fetched_at_ms: Optional[int] = None

    async def start(self) -> None:
        """Start the scheduler with Payday-aware monitoring."""
        logger.info("Starting Smart Scheduler...")

        self._init_map_gate()

        # Initial full scrape to populate database
        logger.info("Running initial full scrape...")
        await self._execute_scrape()

        # Schedule regular checks based on Smart Mode
        if self.settings.smart_mode.smart_mode:
            await self._setup_smart_schedule()
        else:
            await self._setup_fixed_schedule()

        self.scheduler.start()
        self._running = True
        logger.info(
            f"Smart Scheduler started. Mode: {'Smart' if self.settings.smart_mode.smart_mode else 'Fixed'}"
        )

    async def stop(self) -> None:
        """Stop the scheduler and cleanup."""
        logger.info("Stopping scheduler...")
        self._running = False

        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

        await self._cleanup_browser()
        logger.info("Scheduler stopped")

    async def _setup_smart_schedule(self) -> None:
        smart = self.settings.smart_mode
        asyncio.create_task(self._smart_loop())
        logger.info(
            f"Smart schedule: Low={smart.low_interval}s, High={smart.high_interval}s, "
            f"Payday window: {smart.payday_start_minute}-{smart.payday_end_minute} min"
        )

    async def _setup_fixed_schedule(self) -> None:
        interval = self.settings.scraper.check_interval
        self.scheduler.add_job(
            self._execute_scrape,
            IntervalTrigger(seconds=interval),
            id="fixed_monitor",
            name=f"Fixed Monitor (every {interval}s)",
            max_instances=1,
            coalesce=True,
        )
        logger.info(f"Fixed schedule: every {interval} seconds")

    async def _smart_loop(self) -> None:
        while self._running:
            try:
                await self._smart_tick()
            except Exception as e:
                logger.error(f"Smart scheduler loop error: {e}")
                await asyncio.sleep(5)

    def _init_map_gate(self) -> None:
        """Prepare the map-update gate (resolve the catalog server sid once)."""
        if not self.settings.scraper.map_update_gate:
            return
        sid = server_name_to_sid(self.settings.realestate.server_name)
        if not sid:
            logger.warning(
                f"MAP_UPDATE_GATE enabled but REALESTATE_SERVER "
                f"'{self.settings.realestate.server_name}' is unknown; gate disabled"
            )
            return
        self._map_gate_client = RealEstateClient()
        self._map_gate_sid = sid
        logger.info(
            f"Map-update gate enabled: scraping only when catalog data advances "
            f"(server sid={sid})"
        )

    async def _should_scrape(self) -> bool:
        """
        Decide whether a browser scrape is worth running this tick.

        When the map-update gate is on, we peek at the catalog's `fetchedAtMs`
        marker and only scrape if it advanced since our last scrape. The gate
        fails open: any error reading the marker returns True so a source hiccup
        never causes a missed update.
        """
        if not self._map_gate_client or not self._map_gate_sid:
            return True
        try:
            fetched_ms = await self._map_gate_client.fetch_updated_ms(self._map_gate_sid)
        except Exception as e:
            logger.warning(f"Map-update gate check failed ({e}); scraping anyway")
            return True
        if fetched_ms is None:
            logger.warning("Map-update gate got no marker; scraping anyway")
            return True
        if fetched_ms == self._last_scraped_fetched_at_ms:
            logger.debug(f"Map-update gate: catalog unchanged ({fetched_ms}), skipping scrape")
            return False
        logger.info(
            f"Map-update gate: catalog advanced "
            f"({self._last_scraped_fetched_at_ms} -> {fetched_ms}), scraping"
        )
        self._last_scraped_fetched_at_ms = fetched_ms
        return True

    async def _mark_map_scrape_done(self, sid: str, fetched_ms: int) -> None:
        """Record that the browser map scrape finished for this catalog recompute.

        The Payday report reads `map_scrape_done:<sid>` and only fires once it
        equals the catalog's `catalog_recompute:<sid>` marker — i.e. the map has
        caught up to the data the report is about. Best-effort: a write failure
        just means the report waits for the next scrape to stamp it.
        """
        try:
            from app.database.repository import ScraperSettingsRepository
            async with DatabaseSession.get_session_context() as session:
                await ScraperSettingsRepository(session).set(
                    f"map_scrape_done:{sid}", str(fetched_ms),
                    "fetchedAtMs the browser map scrape last completed for",
                )
        except Exception as e:
            logger.warning(f"Could not stamp map_scrape_done:{sid}: {e}")

    async def _smart_tick(self) -> None:
        now = datetime.now(timezone.utc)
        current_minute = now.minute

        new_mode = self._determine_mode(current_minute)
        if new_mode != self._current_mode:
            logger.info(f"Mode changed: {self._current_mode.value} -> {new_mode.value}")
            self._current_mode = new_mode

        interval = self._get_interval(new_mode)
        time_since_last = (now - self._last_tick_time).total_seconds() if self._last_tick_time else interval + 1

        if time_since_last >= interval:
            self._last_tick_time = now
            if await self._should_scrape():
                await self._execute_scrape()

        await asyncio.sleep(1)

    def _get_interval(self, mode: MonitorMode) -> int:
        smart = self.settings.smart_mode
        if mode == MonitorMode.HIGH:
            return smart.high_interval
        elif mode == MonitorMode.RECOVERY:
            return 30
        return smart.low_interval

    def _determine_mode(self, current_minute: int) -> MonitorMode:
        smart = self.settings.smart_mode
        start = smart.payday_start_minute
        end = smart.payday_end_minute

        if start <= end:
            if start <= current_minute <= end:
                return MonitorMode.HIGH
        else:
            if current_minute >= start or current_minute <= end:
                return MonitorMode.HIGH

        if self._consecutive_failures >= 3:
            return MonitorMode.RECOVERY

        return MonitorMode.LOW

    async def _execute_scrape(self) -> Optional[List[ApartmentData]]:
        """
        Execute a full apartment scrape cycle.
        Handles errors gracefully with retry logic.

        Returns:
            List of ApartmentData if successful, None otherwise.
        """
        start_time = datetime.utcnow()
        self._total_runs += 1

        logger.info("=" * 60)
        logger.info(
            f"Run #{self._total_runs} | "
            f"Mode: {self._current_mode.value} | "
            f"Payday: {self._is_payday_window()}"
        )

        try:
            # Start browser if needed
            if not self._browser_manager or not self._browser_manager.is_running:
                await self._start_browser()

            # Create scraper
            scraper = ApartmentScraper(self._browser_manager)

            # Scrape all apartments
            apartments_data = await scraper.scrape_all_apartments()

            if not apartments_data:
                logger.warning("No apartment data scraped")
                self._consecutive_failures += 1
                await self._save_log(start_time, "partial", 0, 0, 0)
                return None

            # Compare and save changes
            changes_count = await self._process_results(apartments_data)

            # Success
            self._successful_runs += 1
            self._consecutive_failures = 0

            duration = (datetime.utcnow() - start_time).total_seconds()
            logger.info(
                f"Run completed: {len(apartments_data)} apartments, "
                f"{changes_count} changes, {duration:.1f}s"
            )

            # Save log
            await self._save_log(
                start_time, "success",
                len(apartments_data), len(apartments_data), 0,
                changes_count, duration
            )

            # Stamp the map-update completion marker. The Payday report is gated
            # on this: it must NOT fire until the browser map scrape for the same
            # catalog recompute (fetchedAtMs) has finished, so a user never gets a
            # "gov report" before the map itself has actually refreshed. We record
            # the fetchedAtMs this scrape was run for (captured by the gate). With
            # the gate off there's no fetchedAtMs to correlate, so we skip it and
            # the report falls back to firing on the catalog recompute alone.
            if self._map_gate_sid and self._last_scraped_fetched_at_ms is not None:
                await self._mark_map_scrape_done(
                    self._map_gate_sid, self._last_scraped_fetched_at_ms
                )

            return apartments_data

        except Exception as e:
            self._consecutive_failures += 1
            duration = (datetime.utcnow() - start_time).total_seconds()

            logger.error(f"Scrape failed (attempt {self._consecutive_failures}): {e}")

            # Save error log
            await self._save_log(
                start_time, "error", 0, 0, 0, 0, duration, str(e)
            )

            # Cleanup browser on failure
            await self._cleanup_browser()

            # If in Payday window, retry immediately
            if self._is_payday_window() and self._consecutive_failures < 10:
                retry_delay = min(self._consecutive_failures * 5, 30)
                logger.info(f"Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                return await self._execute_scrape()

            return None

    async def _process_results(
        self, apartments_data: List[ApartmentData]
    ) -> int:
        """
        Process scraped results: compare and save to database.

        Args:
            apartments_data: List of scraped apartment data.

        Returns:
            Total number of changes detected.
        """
        total_changes = 0
        freed_this_cycle: List[str] = []

        apartments_dicts = [
            {
                "id": d.apartment_id,
                "name": d.name,
                "free_apartments": d.free_apartments,
                "occupied_apartments": d.occupied_apartments,
                "total_apartments": d.total_apartments,
                "last_updated": d.last_updated,
            }
            for d in apartments_data if d
        ]

        async with DatabaseSession.get_session_context() as session:
            detector = ChangeDetector(session)

            for data in apartments_data:
                changes = await detector.compare_and_save(data)
                total_changes += len(changes)
                if any(c.field_name == "apartment_freed" for c in changes):
                    freed_this_cycle.append(data.name)

            crash_detector = get_crash_detector()
            crash_event = await crash_detector.check_for_crash(
                apartments_dicts, session
            )

            if crash_event:
                logger.warning(
                    f"🚨 CRASH DETECTED! "
                    f"Free: {crash_event.previous_free_total} -> {crash_event.current_free_total}, "
                    f"Change: {crash_event.change_percentage:.1f}%"
                )

                from app.database.repository import ChangeRepository, ApartmentRepository, CrashDayLogRepository
                change_repo = ChangeRepository(session)

                if apartments_data:
                    first_apt = apartments_data[0]
                    apt_repo = ApartmentRepository(session)
                    db_apt = await apt_repo.get_by_apartment_id(first_apt.apartment_id)

                    if db_apt:
                        await change_repo.create({
                            "apartment_id": db_apt.id,
                            "field_name": "system_crash",
                            "old_value": f"free={crash_event.previous_free_total}, occupied={crash_event.previous_occupied_total}",
                            "new_value": f"free={crash_event.current_free_total}, occupied={crash_event.current_occupied_total}",
                            "change_type": "crash",
                        })
                        total_changes += 1
                        logger.warning(f"💾 Crash event saved to database")

                # Save freed apartments to daily crash log
                if freed_this_cycle:
                    import json
                    from datetime import timezone
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    crash_log_repo = CrashDayLogRepository(session)
                    await crash_log_repo.add(
                        crash_date=today,
                        apartments_data=json.dumps(freed_this_cycle, ensure_ascii=False),
                        total_freed=len(freed_this_cycle),
                    )
                    logger.warning(f"💾 Crash day log saved: {len(freed_this_cycle)} apartments freed")

        return total_changes

    async def _start_browser(self) -> None:
        """Start or restart the browser."""
        if self._browser_manager:
            await self._cleanup_browser()

        logger.info("Starting browser...")
        self._browser_manager = await create_browser_context(
            headless=self.settings.scraper.headless,
            proxy=self.settings.scraper.proxy if self.settings.scraper.use_proxy else None,
            profile_path=self.settings.scraper.playwright_profile,
            use_stealth=True,
        )
        logger.info("Browser started")

    async def _cleanup_browser(self) -> None:
        """Cleanup browser resources."""
        if self._browser_manager:
            try:
                await self._browser_manager.stop()
            except Exception as e:
                logger.warning(f"Error cleaning up browser: {e}")
            finally:
                self._browser_manager = None

    def _is_payday_window(self) -> bool:
        now = datetime.now(timezone.utc)
        current_minute = now.minute
        smart = self.settings.smart_mode
        start = smart.payday_start_minute
        end = smart.payday_end_minute

        if start <= end:
            return start <= current_minute <= end
        return current_minute >= start or current_minute <= end

    async def _save_log(
        self,
        start_time: datetime,
        status: str,
        checked: int = 0,
        success: int = 0,
        failed: int = 0,
        changes: int = 0,
        duration: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """Save scraper execution log to database."""
        try:
            async with DatabaseSession.get_session_context() as session:
                repo = ScraperLogRepository(session)
                await repo.create({
                    "status": status,
                    "apartments_checked": checked,
                    "apartments_success": success,
                    "apartments_failed": failed,
                    "changes_detected": changes,
                    "duration_seconds": duration,
                    "error_message": error,
                    "is_payday_run": self._is_payday_window(),
                })
        except Exception as e:
            logger.warning(f"Failed to save scraper log: {e}")

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running

    @property
    def stats(self) -> Dict[str, Any]:
        """Get scheduler statistics."""
        return {
            "total_runs": self._total_runs,
            "successful_runs": self._successful_runs,
            "failed_runs": self._total_runs - self._successful_runs,
            "current_mode": self._current_mode.value,
            "consecutive_failures": self._consecutive_failures,
            "is_payday_window": self._is_payday_window(),
            "is_running": self._running,
        }

    async def force_scrape(self) -> Optional[List[ApartmentData]]:
        """Force an immediate scrape (for manual trigger)."""
        logger.info("Manual scrape triggered")
        return await self._execute_scrape()

    async def cache_icons(self) -> None:
        """
        Cache apartment data (runs a full scrape to populate DB).
        """
        try:
            data = await self._execute_scrape()
            if data:
                self._icons_cached = True
                logger.info(f"Cached {len(data)} apartments")
            else:
                self._icons_cached = False
        except Exception as e:
            logger.error(f"Failed to cache: {e}")
            self._icons_cached = False


# Process-wide scheduler singleton. The manual-trigger API endpoint reuses this
# so repeated calls don't each spin up a new, unmanaged browser. Note: the API
# and the scraper worker run as separate processes in most deploy modes, so this
# only shares state within a single process (e.g. SERVICE_MODE=all).
_scheduler: Optional["SmartScheduler"] = None


def get_scheduler() -> "SmartScheduler":
    """Get or create the process-wide SmartScheduler singleton."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SmartScheduler()
    return _scheduler
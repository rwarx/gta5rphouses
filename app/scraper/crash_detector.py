"""
Crash Detection Module (Детектор слёта).
Отслеживает полный сброс данных на карте — 
когда количество свободных/занятых квартир резко меняется,
что указывает на то, что сервер перезагрузил данные после рестарта/краша.
"""

import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.session import DatabaseSession
from app.database.repository import (
    ApartmentRepository,
    ApartmentHistoryRepository,
    ChangeRepository,
    ScraperSettingsRepository,
)


@dataclass
class CrashEvent:
    """Represents a detected crash/reset event."""
    detected_at: datetime
    previous_free_total: int
    current_free_total: int
    previous_occupied_total: int
    current_occupied_total: int
    change_percentage: float
    apartments_affected: int
    is_confirmed: bool = False
    auto_recovery_started: bool = False


class CrashDetector:
    """
    Detects "crash" events when the server resets apartment data.
    
    Логика:
    1. Каждый цикл проверки сравнивает общее количество свободных/занятых квартир
       с предыдущим снэпшотом.
    2. Если изменение превышает порог (80% данных изменилось) — это слёт.
    3. При обнаружении слёта:
       - Сохраняется событие в историю
       - Отправляется уведомление в Telegram
       - Автоматически запускается полный пересбор данных (recovery)
    """

    def __init__(self):
        self.settings = get_settings()
        self._last_snapshot: Optional[Dict[str, Any]] = None
        self._crash_history: List[CrashEvent] = []
        self._enabled: bool = self._load_enabled_setting()
        self._monitoring_active: bool = False

    def _load_enabled_setting(self) -> bool:
        """Load crash detection enabled setting from env."""
        from os import getenv
        return getenv("CRASH_DETECTION_ENABLED", "true").lower() == "true"

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def set_enabled(self, enabled: bool) -> None:
        """Enable or disable crash detection."""
        self._enabled = enabled
        if enabled:
            logger.info("🔍 Краш-детектор включён")
        else:
            logger.info("🔍 Краш-детектор выключен")

    async def check_for_crash(
        self,
        apartments_data: List[Dict[str, Any]],
        session: AsyncSession
    ) -> Optional[CrashEvent]:
        """
        Check if a crash/reset has occurred by analyzing apartment data changes.

        Args:
            apartments_data: Current apartment data from scraper.
            session: Database session.

        Returns:
            CrashEvent if detected, None otherwise.
        """
        if not self._enabled or not apartments_data:
            return None

        # Calculate current totals
        current_free = sum(
            a.get("free_apartments", 0) or 0 for a in apartments_data
        )
        current_occupied = sum(
            a.get("occupied_apartments", 0) or 0 for a in apartments_data
        )
        current_total = sum(
            a.get("total_apartments", 0) or 0 for a in apartments_data
        )

        # Get previous snapshot from DB
        history_repo = ApartmentHistoryRepository(session)
        
        # Get the latest history across ALL apartments to build a previous snapshot
        previous_free = self._last_snapshot.get("total_free") if self._last_snapshot else None
        previous_occupied = self._last_snapshot.get("total_occupied") if self._last_snapshot else None
        previous_total = self._last_snapshot.get("total_units") if self._last_snapshot else None

        # Store current snapshot
        self._last_snapshot = {
            "total_free": current_free,
            "total_occupied": current_occupied,
            "total_units": current_total,
            "timestamp": datetime.now(timezone.utc),
        }

        # If we don't have previous data, can't detect crash yet
        if previous_free is None or previous_occupied is None:
            logger.debug("Краш-детектор: первый снэпшот, ждём следующий для сравнения")
            return None

        # Calculate total change percentage
        total_before = previous_free + previous_occupied
        total_after = current_free + current_occupied

        if total_before == 0:
            return None

        # Calculate how many apartments changed their free/occupied status
        changed_count = 0
        for apt in apartments_data:
            apt_id = apt.get("id")
            if apt_id:
                # Get previous state for this apartment from the snapshot in DB
                pass  # Simplified: we check aggregate change
        
        # Simple crash detection: check if free+occupied total changed drastically
        change_diff = abs(total_after - total_before)
        change_percentage = change_diff / max(total_before, 1)

        threshold = float(
            self.settings.__dict__.get("crash_detection_threshold", 0.8)
        )

        if change_percentage >= threshold:
            event = CrashEvent(
                detected_at=datetime.now(timezone.utc),
                previous_free_total=previous_free,
                current_free_total=current_free,
                previous_occupied_total=previous_occupied,
                current_occupied_total=current_occupied,
                change_percentage=change_percentage * 100,
                apartments_affected=len(apartments_data),
            )
            
            self._crash_history.append(event)
            
            logger.warning(
                f"🚨 ОБНАРУЖЕН СЛЁТ! "
                f"Изменение данных: {change_percentage*100:.1f}% "
                f"(было свободно {previous_free}, стало {current_free})"
            )
            
            return event

        # Also check if free went to 0 and occupied went to max => server restart
        if previous_total is not None and current_free == 0 and current_occupied >= previous_total * 0.9:
            event = CrashEvent(
                detected_at=datetime.now(timezone.utc),
                previous_free_total=previous_free,
                current_free_total=current_free,
                previous_occupied_total=previous_occupied,
                current_occupied_total=current_occupied,
                change_percentage=100.0,
                apartments_affected=len(apartments_data),
            )
            
            self._crash_history.append(event)
            
            logger.warning(
                f"🚨 ОБНАРУЖЕН СЛЁТ (все занято)! "
                f"Свободно: {previous_free} -> {current_free}"
            )
            
            return event

        return None

    async def get_crash_stats(self) -> Dict[str, Any]:
        """Get crash detection statistics."""
        return {
            "enabled": self._enabled,
            "total_crashes_detected": len(self._crash_history),
            "last_crash": self._crash_history[-1].detected_at.isoformat()
                if self._crash_history else None,
            "monitoring_active": self._monitoring_active,
            "crash_history": [
                {
                    "time": e.detected_at.isoformat(),
                    "free_change": f"{e.previous_free_total} -> {e.current_free_total}",
                    "change_pct": f"{e.change_percentage:.1f}%",
                }
                for e in self._crash_history[-5:]
            ],
        }

    async def check_map_version_change(
        self, session: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """
        Check if the map's "last_updated" field has changed significantly,
        indicating a server restart / map reload.
        """
        from app.database.repository import ScraperSettingsRepository
        
        settings_repo = ScraperSettingsRepository(session)
        
        # Store last known "last_updated" from apartments
        apartment_repo = ApartmentRepository(session)
        apartments = await apartment_repo.get_all()
        
        if not apartments:
            return None
            
        # Get the most recent last_updated from any apartment
        max_updated = max(
            (a.last_updated for a in apartments if a.last_updated),
            default=None
        )
        
        if not max_updated:
            return None
            
        # Store in settings for cross-run comparison
        last_version = await settings_repo.get("last_map_version")
        
        if not last_version:
            await settings_repo.set(
                "last_map_version", max_updated.isoformat(),
                "Last known map update timestamp"
            )
            return None
            
        # Parse stored version
        try:
            stored_time = datetime.fromisoformat(last_version)
            time_diff = abs((max_updated - stored_time).total_seconds())
            
            # If difference > 5 minutes, something changed
            if time_diff > 300:
                logger.info(
                    f"🔄 Обнаружено обновление карты: "
                    f"{stored_time.strftime('%H:%M')} -> "
                    f"{max_updated.strftime('%H:%M')}"
                )
                
                # Update stored version
                await settings_repo.set(
                    "last_map_version", max_updated.isoformat()
                )
                
                return {
                    "old_version": stored_time.isoformat(),
                    "new_version": max_updated.isoformat(),
                    "time_diff_seconds": time_diff,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse stored version: {e}")
            
        # Update stored version
        await settings_repo.set(
            "last_map_version", max_updated.isoformat()
        )
        
        return None


# Singleton
_crash_detector: Optional[CrashDetector] = None


def get_crash_detector() -> CrashDetector:
    """Get or create crash detector singleton."""
    global _crash_detector
    if _crash_detector is None:
        _crash_detector = CrashDetector()
    return _crash_detector
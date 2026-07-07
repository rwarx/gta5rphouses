"""
Change detection engine for apartment data comparison.
Compares old and new apartment snapshots to detect differences.
Saves detected changes to the database.
"""

from typing import Optional, List, Dict, Any, Set, Tuple
from datetime import datetime
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repository import (
    ApartmentRepository,
    ApartmentTypeRepository,
    ApartmentHistoryRepository,
    ChangeRepository,
)
from app.database.models import Apartment
from app.scraper.playwright_scraper import ApartmentData, ApartmentTypeData


@dataclass
class DetectedChange:
    """Represents a single detected change in apartment data."""
    field_name: str
    old_value: Optional[str]
    new_value: Optional[str]
    change_type: str = "update"  # update, new_field, data_refresh


class ChangeDetector:
    """
    Detects changes between old and new apartment data snapshots.
    Uses field-by-field comparison to identify what changed.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.apartment_repo = ApartmentRepository(session)
        self.type_repo = ApartmentTypeRepository(session)
        self.history_repo = ApartmentHistoryRepository(session)
        self.change_repo = ChangeRepository(session)

    async def compare_and_save(
        self, apartment_data: ApartmentData
    ) -> List[DetectedChange]:
        """
        Compare new apartment data with existing database record.
        Save changes if any are detected.

        Args:
            apartment_data: Newly scraped apartment data.

        Returns:
            List of detected changes.
        """
        changes: List[DetectedChange] = []

        try:
            # Get or create apartment
            apartment = await self._upsert_apartment(apartment_data)

            if not apartment:
                logger.warning(f"Could not upsert apartment: {apartment_data.name}")
                return changes

            # Get previous state from latest history record
            previous_records = await self.history_repo.get_latest(
                apartment.id, limit=1
            )
            previous_data = previous_records[0].snapshot_data if previous_records else None

            # Compare fields
            changes = await self._compare_fields(
                apartment, apartment_data, previous_data
            )

            # Compare apartment types
            type_changes = await self._compare_types(apartment, apartment_data)
            changes.extend(type_changes)

            # If apartment just became free (0 → >0), add special change
            old_free = self._get_free_from_previous(previous_data)
            new_free = apartment_data.free_apartments or 0
            if (old_free == 0 or old_free is None) and new_free > 0:
                changes.append(DetectedChange(
                    field_name="apartment_freed",
                    old_value=str(old_free),
                    new_value=str(new_free),
                    change_type="free_up",
                ))

            # Save current snapshot to history
            await self._save_snapshot(apartment, apartment_data)

            # Save detected changes
            for change in changes:
                await self.change_repo.create({
                    "apartment_id": apartment.id,
                    "field_name": change.field_name,
                    "old_value": change.old_value,
                    "new_value": change.new_value,
                    "change_type": change.change_type,
                })

            if changes:
                logger.info(
                    f"Detected {len(changes)} change(s) for {apartment_data.name}"
                )
            else:
                logger.debug(f"No changes detected for {apartment_data.name}")

            return changes

        except Exception as e:
            logger.error(
                f"Failed to compare data for {apartment_data.name}: {e}"
            )
            return changes

    async def _upsert_apartment(
        self, data: ApartmentData
    ) -> Optional[Apartment]:
        """
        Insert or update apartment in database.

        Args:
            data: ApartmentData from scraper.

        Returns:
            Apartment model instance.
        """
        apartment_dict = data.to_dict()

        # Use name as ID fallback if apartment_id is empty
        apt_id = data.apartment_id or data.name.replace(" ", "_").lower()

        apartment = await self.apartment_repo.upsert(apt_id, apartment_dict)
        return apartment

    async def _compare_fields(
        self,
        apartment: Apartment,
        new_data: ApartmentData,
        previous_data: Optional[Dict[str, Any]],
    ) -> List[DetectedChange]:
        """
        Compare individual fields between old and new data.

        Args:
            apartment: Current apartment DB record.
            new_data: New scraped data.
            previous_data: Previous snapshot from history.

        Returns:
            List of detected field changes.
        """
        changes: List[DetectedChange] = []

        if not previous_data:
            # No previous data means first scrape or all fields are new
            logger.debug(f"No previous data for {new_data.name}, initializing")
            return changes

        # Normalize last_updated: both sides as "DD.MM.YYYY HH:MM:SS"
        new_updated_raw = new_data.last_updated
        prev_updated_raw = previous_data.get("last_updated")

        def _normalize_ts(val):
            if not val:
                return None
            if isinstance(val, datetime):
                return val.strftime("%d.%m.%Y %H:%M:%S")
            # Try known formats
            for fmt in ["%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"]:
                try:
                    return datetime.strptime(str(val), fmt).strftime("%d.%m.%Y %H:%M:%S")
                except ValueError:
                    continue
            return str(val)

        new_updated_norm = _normalize_ts(new_updated_raw)
        prev_updated_norm = _normalize_ts(prev_updated_raw)

        compare_fields = {
            "total_apartments": (new_data.total_apartments, previous_data.get("total_apartments")),
            "free_apartments": (new_data.free_apartments, previous_data.get("free_apartments")),
            "occupied_apartments": (new_data.occupied_apartments, previous_data.get("occupied_apartments")),
            "description": (new_data.description, previous_data.get("description")),
            "wiki_url": (new_data.wiki_url, previous_data.get("wiki_url")),
            "last_updated": (new_updated_norm, prev_updated_norm),
            "address": (new_data.address, previous_data.get("address")),
        }

        for field_name, (new_val, old_val) in compare_fields.items():
            # Convert to strings for comparison
            new_str = str(new_val) if new_val is not None else None
            old_str = str(old_val) if old_val is not None else None

            if new_str != old_str:
                changes.append(DetectedChange(
                    field_name=field_name,
                    old_value=old_str,
                    new_value=new_str,
                    change_type="update",
                ))

        # Check for new fields in raw_data
        old_raw = previous_data.get("raw_data", {})
        new_raw = new_data.raw_data

        # Detect new fields
        old_all_fields = old_raw.get("all_fields", {})
        new_all_fields = new_raw.get("all_fields", {})

        for key, value in new_all_fields.items():
            if key not in old_all_fields:
                changes.append(DetectedChange(
                    field_name=f"new_field_{key}",
                    old_value=None,
                    new_value=str(value),
                    change_type="new_field",
                ))

        # Check raw_data changes
        if old_raw.get("full_text") != new_raw.get("full_text"):
            changes.append(DetectedChange(
                field_name="raw_content",
                old_value="content_changed",
                new_value="content_changed",
                change_type="data_refresh",
            ))

        return changes

    async def _compare_types(
        self,
        apartment: Apartment,
        new_data: ApartmentData,
    ) -> List[DetectedChange]:
        """
        Compare apartment type classes between old and new data.

        Args:
            apartment: Current apartment DB record.
            new_data: New scraped data with apartment types.

        Returns:
            List of detected type changes.
        """
        changes: List[DetectedChange] = []

        # Get existing types from database
        existing_types = {
            apt.class_name: apt
            for apt in apartment.apartment_types
        }

        for type_data in new_data.apartment_types:
            # Update or create type in DB
            await self.type_repo.create_or_update(
                apartment_id=apartment.id,
                class_name=type_data.class_name,
                data={
                    "total": type_data.total,
                    "free": type_data.free,
                    "occupied": type_data.occupied,
                },
            )

            # Check if this type already existed
            existing = existing_types.get(type_data.class_name)

            if existing:
                # Compare values
                if existing.free != type_data.free:
                    changes.append(DetectedChange(
                        field_name=f"type_{type_data.class_name}_free",
                        old_value=str(existing.free) if existing.free is not None else None,
                        new_value=str(type_data.free) if type_data.free is not None else None,
                        change_type="update",
                    ))

                if existing.occupied != type_data.occupied:
                    changes.append(DetectedChange(
                        field_name=f"type_{type_data.class_name}_occupied",
                        old_value=str(existing.occupied) if existing.occupied is not None else None,
                        new_value=str(type_data.occupied) if type_data.occupied is not None else None,
                        change_type="update",
                    ))
            else:
                # New type class
                changes.append(DetectedChange(
                    field_name=f"new_type_{type_data.class_name}",
                    old_value=None,
                    new_value=f"free={type_data.free}, occupied={type_data.occupied}",
                    change_type="new_field",
                ))

        return changes

    def _get_free_from_previous(self, previous_data: Optional[Dict]) -> int:
        if not previous_data:
            return 0
        raw = previous_data.get("free_apartments")
        if raw is None:
            raw = previous_data.get("snapshot_data", {}).get("free_apartments")
        try:
            return int(raw) if raw is not None else 0
        except (ValueError, TypeError):
            return 0

    async def _save_snapshot(
        self, apartment: Apartment, data: ApartmentData
    ) -> None:
        """
        Save current state snapshot to history.

        Args:
            apartment: Apartment model instance.
            data: Current scraped data.
        """
        snapshot = data.to_dict()

        # Add apartment types to snapshot
        snapshot["apartment_types"] = [
            {
                "class_name": t.class_name,
                "total": t.total,
                "free": t.free,
                "occupied": t.occupied,
            }
            for t in data.apartment_types
        ]

        if snapshot.get("last_updated") and isinstance(snapshot["last_updated"], datetime):
            snapshot["last_updated"] = snapshot["last_updated"].isoformat()

        await self.history_repo.create(apartment.id, snapshot)
        logger.debug(f"Saved snapshot for {data.name}")

    async def get_aggregated_changes(
        self, since: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get aggregated change statistics.

        Args:
            since: Optional start date for filtering.

        Returns:
            Dictionary with change statistics.
        """
        changes = await self.change_repo.get_recent(limit=100)

        stats = {
            "total_changes": len(changes),
            "by_type": {},
            "recent_changes": [],
        }

        for change in changes:
            change_type = change.change_type
            if change_type not in stats["by_type"]:
                stats["by_type"][change_type] = 0
            stats["by_type"][change_type] += 1

            if len(stats["recent_changes"]) < 10:
                stats["recent_changes"].append({
                    "apartment_name": change.apartment.name if change.apartment else "Unknown",
                    "field": change.field_name,
                    "old_value": change.old_value,
                    "new_value": change.new_value,
                    "detected_at": change.detected_at.isoformat() if change.detected_at else None,
                })

        return stats
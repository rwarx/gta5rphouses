"""
Repository layer implementing Repository Pattern.
Provides data access abstraction over SQLAlchemy models.
"""

from typing import Optional, List, Dict, Any, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, delete, update, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select
from loguru import logger

from app.database.models import (
    Apartment,
    ApartmentHistory,
    ApartmentType,
    Change,
    CrashDayLog,
    ScraperSettings,
    Notification,
    ScraperLog,
    RealEstateObject,
    RealEstateEvent,
    RealEstateOwnerHistory,
    RealEstateBuildingState,
    RealEstateSubscription,
    UserServerSelection,
)


class ApartmentRepository:
    """Repository for Apartment CRUD operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, apartment_id: int) -> Optional[Apartment]:
        """Get apartment by internal ID."""
        result = await self.session.execute(
            select(Apartment).where(Apartment.id == apartment_id)
        )
        return result.scalar_one_or_none()

    async def get_by_apartment_id(self, apartment_id: str) -> Optional[Apartment]:
        """Get apartment by wiki apartment ID."""
        result = await self.session.execute(
            select(Apartment).where(Apartment.apartment_id == apartment_id)
        )
        return result.scalar_one_or_none()

    async def get_all(self, active_only: bool = True) -> List[Apartment]:
        """Get all apartments, optionally only active ones."""
        query = select(Apartment).options(selectinload(Apartment.apartment_types))
        if active_only:
            query = query.where(Apartment.is_active == True)
        query = query.order_by(Apartment.name)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_with_types(self, apartment_id: int) -> Optional[Apartment]:
        """Get apartment with its type classes eagerly loaded."""
        result = await self.session.execute(
            select(Apartment)
            .options(selectinload(Apartment.apartment_types))
            .where(Apartment.id == apartment_id)
        )
        return result.scalar_one_or_none()

    async def create(self, apartment_data: Dict[str, Any]) -> Apartment:
        """Create a new apartment."""
        apartment = Apartment(**apartment_data)
        self.session.add(apartment)
        await self.session.flush()
        logger.debug(f"Created apartment: {apartment.name}")
        return apartment

    async def update(self, apartment: Apartment, update_data: Dict[str, Any]) -> Apartment:
        """Update an existing apartment."""
        for key, value in update_data.items():
            setattr(apartment, key, value)
        await self.session.flush()
        logger.debug(f"Updated apartment: {apartment.name}")
        return apartment

    async def upsert(self, apartment_id: str, data: Dict[str, Any]) -> Apartment:
        """
        Insert or update apartment based on wiki apartment_id.
        Returns the apartment with apartment_types loaded.
        """
        existing = await self.get_by_apartment_id(apartment_id)

        if existing:
            for key, value in data.items():
                if key not in ("apartment_id", "created_at"):
                    setattr(existing, key, value)
            apartment = existing
        else:
            data["apartment_id"] = apartment_id
            apartment = Apartment(**data)
            self.session.add(apartment)

        await self.session.flush()

        # Reload with types
        result = await self.session.execute(
            select(Apartment)
            .options(selectinload(Apartment.apartment_types))
            .where(Apartment.id == apartment.id)
        )
        return result.scalar_one()

    async def search(self, query: str) -> List[Apartment]:
        """Search apartments by name or address."""
        stmt = select(Apartment).options(
            selectinload(Apartment.apartment_types)
        ).where(
            or_(
                Apartment.name.ilike(f"%{query}%"),
                Apartment.address.ilike(f"%{query}%"),
            )
        ).order_by(Apartment.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_free_apartments(self) -> List[Apartment]:
        """Get apartments with free units available."""
        result = await self.session.execute(
            select(Apartment)
            .options(selectinload(Apartment.apartment_types))
            .where(
                and_(
                    Apartment.is_active == True,
                    Apartment.free_apartments > 0,
                )
            )
            .order_by(Apartment.free_apartments.desc())
        )
        return list(result.scalars().all())

    async def get_statistics(self) -> Dict[str, Any]:
        """Get aggregate statistics for all apartments."""
        total = await self.session.execute(
            select(func.count(Apartment.id)).where(Apartment.is_active == True)
        )
        total_count = total.scalar() or 0

        free_sum = await self.session.execute(
            select(func.coalesce(func.sum(Apartment.free_apartments), 0))
            .where(Apartment.is_active == True)
        )
        free_total = free_sum.scalar() or 0

        occupied_sum = await self.session.execute(
            select(func.coalesce(func.sum(Apartment.occupied_apartments), 0))
            .where(Apartment.is_active == True)
        )
        occupied_total = occupied_sum.scalar() or 0

        total_units = await self.session.execute(
            select(func.coalesce(func.sum(Apartment.total_apartments), 0))
            .where(Apartment.is_active == True)
        )
        total_units_count = total_units.scalar() or 0

        return {
            "total_apartments": total_count,
            "total_units": total_units_count,
            "total_free": free_total,
            "total_occupied": occupied_total,
            "occupancy_rate": round((occupied_total / total_units_count * 100), 2)
            if total_units_count > 0 else 0,
        }


class ApartmentTypeRepository:
    """Repository for ApartmentType CRUD operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_apartment_and_class(
        self, apartment_id: int, class_name: str
    ) -> Optional[ApartmentType]:
        """Get apartment type by apartment ID and class name."""
        result = await self.session.execute(
            select(ApartmentType).where(
                and_(
                    ApartmentType.apartment_id == apartment_id,
                    ApartmentType.class_name == class_name,
                )
            )
        )
        return result.scalar_one_or_none()

    async def create_or_update(
        self, apartment_id: int, class_name: str, data: Dict[str, Any]
    ) -> ApartmentType:
        """Create or update an apartment type class."""
        existing = await self.get_by_apartment_and_class(apartment_id, class_name)

        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
            apt_type = existing
        else:
            data.update({"apartment_id": apartment_id, "class_name": class_name})
            apt_type = ApartmentType(**data)
            self.session.add(apt_type)

        await self.session.flush()
        return apt_type


class ApartmentHistoryRepository:
    """Repository for history records."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, apartment_id: int, snapshot: Dict[str, Any]) -> ApartmentHistory:
        """Create a history record."""
        source_updated_at = snapshot.get("last_updated")
        if isinstance(source_updated_at, str):
            try:
                source_updated_at = datetime.fromisoformat(source_updated_at)
            except (ValueError, TypeError):
                source_updated_at = None

        history = ApartmentHistory(
            apartment_id=apartment_id,
            snapshot_data=snapshot,
            free_apartments=snapshot.get("free_apartments"),
            occupied_apartments=snapshot.get("occupied_apartments"),
            total_apartments=snapshot.get("total_apartments"),
            source_updated_at=source_updated_at,
        )
        self.session.add(history)
        await self.session.flush()
        return history

    async def get_latest(
        self, apartment_id: int, limit: int = 1
    ) -> List[ApartmentHistory]:
        """Get the most recent history records for an apartment."""
        result = await self.session.execute(
            select(ApartmentHistory)
            .where(ApartmentHistory.apartment_id == apartment_id)
            .order_by(ApartmentHistory.recorded_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_history_by_period(
        self,
        apartment_id: int,
        start_date: datetime,
        end_date: Optional[datetime] = None,
    ) -> List[ApartmentHistory]:
        """Get history for a specific time period."""
        query = select(ApartmentHistory).where(
            and_(
                ApartmentHistory.apartment_id == apartment_id,
                ApartmentHistory.recorded_at >= start_date,
            )
        )
        if end_date:
            query = query.where(ApartmentHistory.recorded_at <= end_date)
        query = query.order_by(ApartmentHistory.recorded_at.asc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_free_history(
        self, apartment_id: int, days: int = 7
    ) -> List[Dict[str, Any]]:
        """Get free apartment count history for charting."""
        start_date = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(ApartmentHistory)
            .where(
                and_(
                    ApartmentHistory.apartment_id == apartment_id,
                    ApartmentHistory.recorded_at >= start_date,
                    ApartmentHistory.free_apartments.isnot(None),
                )
            )
            .order_by(ApartmentHistory.recorded_at.asc())
        )
        records = result.scalars().all()
        return [
            {"time": r.recorded_at.isoformat(), "free": r.free_apartments}
            for r in records
        ]


class ChangeRepository:
    """Repository for tracking changes."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, change_data: Dict[str, Any]) -> Change:
        """Create a change record."""
        change = Change(**change_data)
        self.session.add(change)
        await self.session.flush()
        return change

    async def get_recent(
        self, limit: int = 50, notified_only: bool = False
    ) -> List[Change]:
        """Get most recent changes."""
        query = select(Change).options(selectinload(Change.apartment))
        if notified_only:
            query = query.where(Change.notified == True)
        query = query.order_by(Change.detected_at.desc()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_unnotified(self) -> List[Change]:
        """Get changes that haven't been notified yet."""
        result = await self.session.execute(
            select(Change)
            .options(selectinload(Change.apartment))
            .where(Change.notified == False)
            .order_by(Change.detected_at.asc())
        )
        return list(result.scalars().all())

    async def mark_notified(self, change_id: int) -> None:
        """Mark a change as notified."""
        await self.session.execute(
            update(Change)
            .where(Change.id == change_id)
            .values(notified=True, notified_at=datetime.utcnow())
        )
        await self.session.flush()

    async def get_changes_by_apartment(
        self, apartment_id: int, limit: int = 20
    ) -> List[Change]:
        """Get changes for a specific apartment."""
        result = await self.session.execute(
            select(Change)
            .where(Change.apartment_id == apartment_id)
            .order_by(Change.detected_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


class ScraperSettingsRepository:
    """Repository for dynamic scraper settings."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, key: str) -> Optional[str]:
        """Get a setting value by key."""
        result = await self.session.execute(
            select(ScraperSettings).where(ScraperSettings.key == key)
        )
        setting = result.scalar_one_or_none()
        return setting.value if setting else None

    async def set(self, key: str, value: str, description: Optional[str] = None) -> None:
        """Set a setting value."""
        result = await self.session.execute(
            select(ScraperSettings).where(ScraperSettings.key == key)
        )
        setting = result.scalar_one_or_none()

        if setting:
            setting.value = value
            if description:
                setting.description = description
        else:
            setting = ScraperSettings(key=key, value=value, description=description)
            self.session.add(setting)

        await self.session.flush()

    async def get_all(self) -> List[ScraperSettings]:
        """Get all settings."""
        result = await self.session.execute(
            select(ScraperSettings).order_by(ScraperSettings.key)
        )
        return list(result.scalars().all())


class NotificationRepository:
    """Repository for notification tracking."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, change_id: int, apartment_id: int, message: str
    ) -> Notification:
        """Create a notification record."""
        notification = Notification(
            change_id=change_id,
            apartment_id=apartment_id,
            message_text=message,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def get_recent(self, limit: int = 20) -> List[Notification]:
        """Get recent notifications."""
        result = await self.session.execute(
            select(Notification)
            .order_by(Notification.sent_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


class CrashDayLogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, crash_date: str, apartments_data: str, total_freed: int) -> CrashDayLog:
        record = CrashDayLog(
            crash_date=crash_date,
            apartments_data=apartments_data,
            total_freed=total_freed,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_by_date(self, crash_date: str) -> List[CrashDayLog]:
        result = await self.session.execute(
            select(CrashDayLog)
            .where(CrashDayLog.crash_date == crash_date)
            .order_by(CrashDayLog.detected_at.desc())
        )
        return list(result.scalars().all())


class ScraperLogRepository:
    """Repository for scraper execution logs."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, log_data: Dict[str, Any]) -> ScraperLog:
        """Create a scraper log entry."""
        log = ScraperLog(**log_data)
        self.session.add(log)
        await self.session.flush()
        return log

    async def get_recent(self, limit: int = 20, status: Optional[str] = None) -> List[ScraperLog]:
        """Get recent scraper logs."""
        query = select(ScraperLog)
        if status:
            query = query.where(ScraperLog.status == status)
        query = query.order_by(ScraperLog.ran_at.desc()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_statistics(self) -> Dict[str, Any]:
        """Get scraper execution statistics."""
        total = await self.session.execute(select(func.count(ScraperLog.id)))
        total_runs = total.scalar() or 0

        success = await self.session.execute(
            select(func.count(ScraperLog.id)).where(ScraperLog.status == "success")
        )
        success_count = success.scalar() or 0

        error = await self.session.execute(
            select(func.count(ScraperLog.id)).where(ScraperLog.status == "error")
        )
        error_count = error.scalar() or 0

        last_run = await self.session.execute(
            select(ScraperLog).order_by(ScraperLog.ran_at.desc()).limit(1)
        )
        last = last_run.scalar_one_or_none()

        return {
            "total_runs": total_runs,
            "successful_runs": success_count,
            "failed_runs": error_count,
            "success_rate": round((success_count / total_runs * 100), 2)
            if total_runs > 0 else 0,
            "last_run": last.ran_at.isoformat() if last else None,
            "last_run_status": last.status if last else None,
            "total_changes_detected": sum(
                log.changes_detected for log in (
                    await self.session.execute(select(ScraperLog))
                ).scalars().all()
            ) if total_runs > 0 else 0,
        }


class RealEstateRepository:
    """
    Repository for the `/realestate` catalog: current object state + events.

    Objects are keyed by a stable "<server_sid>:<kind>:<unit_id>" string so the
    same house/apartment is tracked across snapshots regardless of DB id.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def make_key(server_sid: str, kind: str, unit_id: int) -> str:
        """Build the stable object key, e.g. '20:house:1'."""
        return f"{server_sid}:{kind}:{unit_id}"

    @staticmethod
    def make_building_key(server_sid: str, building_id: int) -> str:
        """Build the stable building key, e.g. '20:building:10'."""
        return f"{server_sid}:building:{building_id}"

    async def get_occupied_keys(self, server_sid: str) -> Dict[str, RealEstateObject]:
        """Return all currently-occupied objects for a server, keyed by object_key."""
        result = await self.session.execute(
            select(RealEstateObject).where(
                and_(
                    RealEstateObject.server_sid == server_sid,
                    RealEstateObject.is_occupied == True,
                )
            )
        )
        return {obj.object_key: obj for obj in result.scalars().all()}

    async def upsert_occupied(self, object_key: str, data: Dict[str, Any]) -> RealEstateObject:
        """Insert a newly-seen object or refresh an existing one (marking it occupied)."""
        result = await self.session.execute(
            select(RealEstateObject).where(RealEstateObject.object_key == object_key)
        )
        obj = result.scalar_one_or_none()

        if obj:
            for key, value in data.items():
                if key not in ("object_key", "first_seen_at"):
                    setattr(obj, key, value)
            obj.is_occupied = True
        else:
            obj = RealEstateObject(object_key=object_key, is_occupied=True, **data)
            self.session.add(obj)

        await self.session.flush()
        return obj

    async def mark_freed(self, object_key: str) -> None:
        """Flag an object as no longer occupied (it disappeared from the catalog)."""
        await self.session.execute(
            update(RealEstateObject)
            .where(RealEstateObject.object_key == object_key)
            .values(is_occupied=False)
        )
        await self.session.flush()

    async def create_event(self, data: Dict[str, Any]) -> RealEstateEvent:
        """Record a detected transition (freed / occupied / owner_changed)."""
        event = RealEstateEvent(**data)
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_unnotified_events(self) -> List[RealEstateEvent]:
        """Get events pending Telegram delivery, oldest first."""
        result = await self.session.execute(
            select(RealEstateEvent)
            .where(RealEstateEvent.notified == False)
            .order_by(RealEstateEvent.detected_at.asc())
        )
        return list(result.scalars().all())

    async def mark_event_notified(self, event_id: int) -> None:
        """Mark an event as delivered."""
        await self.session.execute(
            update(RealEstateEvent)
            .where(RealEstateEvent.id == event_id)
            .values(notified=True, notified_at=datetime.utcnow())
        )
        await self.session.flush()

    async def get_recent_events(
        self, limit: int = 20, event_type: Optional[str] = None
    ) -> List[RealEstateEvent]:
        """Get recent events, optionally filtered by type."""
        query = select(RealEstateEvent)
        if event_type:
            query = query.where(RealEstateEvent.event_type == event_type)
        query = query.order_by(RealEstateEvent.detected_at.desc()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_occupied(self, server_sid: str, kind: Optional[str] = None) -> int:
        """Count currently-occupied objects for a server, optionally by kind."""
        conditions = [
            RealEstateObject.server_sid == server_sid,
            RealEstateObject.is_occupied == True,
        ]
        if kind:
            conditions.append(RealEstateObject.kind == kind)
        result = await self.session.execute(
            select(func.count(RealEstateObject.id)).where(and_(*conditions))
        )
        return result.scalar() or 0

    async def get_events_since(
        self,
        since: datetime,
        event_types: Optional[List[str]] = None,
        server_sid: Optional[str] = None,
    ) -> List[RealEstateEvent]:
        """Return events detected at/after `since`, optionally filtered.

        Used by the hourly Payday digest and the per-Payday report to summarise
        what happened in a window instead of sending one message per event.
        `server_sid` scopes the result to a single server.
        """
        conditions = [RealEstateEvent.detected_at >= since]
        if event_types:
            conditions.append(RealEstateEvent.event_type.in_(event_types))
        if server_sid:
            conditions.append(RealEstateEvent.server_sid == server_sid)
        result = await self.session.execute(
            select(RealEstateEvent)
            .where(and_(*conditions))
            .order_by(RealEstateEvent.detected_at.asc())
        )
        return list(result.scalars().all())

    # ---- Owner nickname history ----

    async def add_owner_history(
        self,
        object_key: str,
        server_sid: str,
        kind: str,
        owner_name: Optional[str],
        previous_owner: Optional[str],
        during_payday: bool,
    ) -> RealEstateOwnerHistory:
        """Append a row recording the owner seen on an object at this moment."""
        row = RealEstateOwnerHistory(
            object_key=object_key,
            server_sid=server_sid,
            kind=kind,
            owner_name=owner_name,
            previous_owner=previous_owner,
            during_payday=during_payday,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_owner_history(
        self, object_key: str, limit: int = 20
    ) -> List[RealEstateOwnerHistory]:
        """Return the owner history for an object, newest first."""
        result = await self.session.execute(
            select(RealEstateOwnerHistory)
            .where(RealEstateOwnerHistory.object_key == object_key)
            .order_by(RealEstateOwnerHistory.recorded_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # ---- Building aggregates ----

    async def upsert_building(self, building_key: str, data: Dict[str, Any]) -> RealEstateBuildingState:
        """Insert or refresh an apartment-building aggregate row."""
        result = await self.session.execute(
            select(RealEstateBuildingState).where(
                RealEstateBuildingState.building_key == building_key
            )
        )
        obj = result.scalar_one_or_none()
        if obj:
            for key, value in data.items():
                if key != "building_key":
                    setattr(obj, key, value)
        else:
            obj = RealEstateBuildingState(building_key=building_key, **data)
            self.session.add(obj)
        await self.session.flush()
        return obj

    async def get_buildings(self, server_sid: str) -> List[RealEstateBuildingState]:
        """Return all known buildings for a server, ordered by name."""
        result = await self.session.execute(
            select(RealEstateBuildingState)
            .where(RealEstateBuildingState.server_sid == server_sid)
            .order_by(RealEstateBuildingState.name)
        )
        return list(result.scalars().all())

    # ---- Catalog listing (occupied objects) ----

    async def list_occupied(
        self,
        server_sid: str,
        kind: Optional[str] = None,
        building_name: Optional[str] = None,
        class_name: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 500,
    ) -> List[RealEstateObject]:
        """List currently-occupied objects with optional filters, for the catalog."""
        conditions = [
            RealEstateObject.server_sid == server_sid,
            RealEstateObject.is_occupied == True,
        ]
        if kind:
            conditions.append(RealEstateObject.kind == kind)
        if building_name:
            conditions.append(RealEstateObject.building_name == building_name)
        if class_name:
            conditions.append(RealEstateObject.class_name == class_name)
        if search:
            like = f"%{search}%"
            conditions.append(
                or_(
                    RealEstateObject.name.ilike(like),
                    RealEstateObject.owner_name.ilike(like),
                )
            )
        result = await self.session.execute(
            select(RealEstateObject)
            .where(and_(*conditions))
            .order_by(RealEstateObject.name)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_object(self, object_key: str) -> Optional[RealEstateObject]:
        """Fetch a single object by its stable key (occupied or freed)."""
        result = await self.session.execute(
            select(RealEstateObject).where(RealEstateObject.object_key == object_key)
        )
        return result.scalar_one_or_none()


class SubscriptionRepository:
    """
    CRUD for per-user realestate notification subscriptions.

    A subscription ties a Telegram user_id to a server sid (optionally narrowed
    to a `kind`). The notifier uses `get_subscribers` to decide who receives a
    freed-object alert for a given server.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def subscribe(
        self, user_id: int, server_sid: str, kind: str = "any"
    ) -> RealEstateSubscription:
        """Create or update a user's subscription to a server (idempotent)."""
        result = await self.session.execute(
            select(RealEstateSubscription).where(
                and_(
                    RealEstateSubscription.user_id == user_id,
                    RealEstateSubscription.server_sid == server_sid,
                )
            )
        )
        sub = result.scalar_one_or_none()
        if sub:
            sub.kind = kind
        else:
            sub = RealEstateSubscription(
                user_id=user_id, server_sid=server_sid, kind=kind
            )
            self.session.add(sub)
        await self.session.flush()
        return sub

    async def unsubscribe(self, user_id: int, server_sid: str) -> bool:
        """Remove a user's subscription to a server. Returns True if one existed."""
        result = await self.session.execute(
            delete(RealEstateSubscription).where(
                and_(
                    RealEstateSubscription.user_id == user_id,
                    RealEstateSubscription.server_sid == server_sid,
                )
            )
        )
        await self.session.flush()
        return (result.rowcount or 0) > 0

    async def list_for_user(self, user_id: int) -> List[RealEstateSubscription]:
        """Return all of a user's subscriptions."""
        result = await self.session.execute(
            select(RealEstateSubscription)
            .where(RealEstateSubscription.user_id == user_id)
            .order_by(RealEstateSubscription.server_sid)
        )
        return list(result.scalars().all())

    async def get_subscribers(
        self, server_sid: str, kind: Optional[str] = None
    ) -> List[RealEstateSubscription]:
        """Return subscriptions for a server, optionally matching a kind.

        A subscription with kind "any" matches every kind; a kind-specific
        subscription matches only its own kind.
        """
        conditions = [RealEstateSubscription.server_sid == server_sid]
        if kind:
            conditions.append(
                or_(
                    RealEstateSubscription.kind == "any",
                    RealEstateSubscription.kind == kind,
                )
            )
        result = await self.session.execute(
            select(RealEstateSubscription).where(and_(*conditions))
        )
        return list(result.scalars().all())


class UserServerSelectionRepository:
    """
    Read/write a user's active server (the one chosen at /start).

    Exactly one row per user; `set` upserts. Catalog commands call `get` to
    resolve the server to default to when none is given explicitly.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user_id: int) -> Optional[str]:
        """Return the user's active server sid, or None if they never picked one."""
        result = await self.session.execute(
            select(UserServerSelection.server_sid).where(
                UserServerSelection.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def set(self, user_id: int, server_sid: str) -> None:
        """Set (upsert) the user's active server."""
        result = await self.session.execute(
            select(UserServerSelection).where(
                UserServerSelection.user_id == user_id
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.server_sid = server_sid
        else:
            self.session.add(
                UserServerSelection(user_id=user_id, server_sid=server_sid)
            )
        await self.session.flush()

    async def all_selected_sids(self) -> List[str]:
        """Distinct server sids any user has actively chosen at /start.

        The scheduler unions these with the configured REALESTATE_SERVERS so a
        server a user picked is polled (and its catalog/map populated) even if
        it was never listed in the static config.
        """
        result = await self.session.execute(
            select(UserServerSelection.server_sid).distinct()
        )
        return [sid for sid in result.scalars().all() if sid]
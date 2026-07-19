"""
Database models for Apartment Checker.
Uses SQLAlchemy 2.0 async ORM with proper relationships.
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    JSON,
    BigInteger,
    Index,
    UniqueConstraint,
    PrimaryKeyConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class Apartment(Base):
    __tablename__ = "apartments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    apartment_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    total_apartments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    free_apartments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=0)
    occupied_apartments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=0)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    wiki_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    coordinates: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    history: Mapped[List["ApartmentHistory"]] = relationship(back_populates="apartment", cascade="all, delete-orphan")
    apartment_types: Mapped[List["ApartmentType"]] = relationship(back_populates="apartment", cascade="all, delete-orphan")
    changes: Mapped[List["Change"]] = relationship(back_populates="apartment", cascade="all, delete-orphan")
    __table_args__ = (Index("idx_apartment_free", "free_apartments"), Index("idx_apartment_active", "is_active"))


class ApartmentType(Base):
    __tablename__ = "apartment_types"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    apartment_id: Mapped[int] = mapped_column(Integer, ForeignKey("apartments.id", ondelete="CASCADE"), nullable=False)
    class_name: Mapped[str] = mapped_column(String(100), nullable=False)
    total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    free: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=0)
    occupied: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    apartment: Mapped["Apartment"] = relationship(back_populates="apartment_types")
    __table_args__ = (UniqueConstraint("apartment_id", "class_name", name="uq_apartment_class"),)


class ApartmentHistory(Base):
    __tablename__ = "apartment_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    apartment_id: Mapped[int] = mapped_column(Integer, ForeignKey("apartments.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    free_apartments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    occupied_apartments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_apartments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    apartment: Mapped["Apartment"] = relationship(back_populates="history")
    __table_args__ = (Index("idx_history_apt_time", "apartment_id", "recorded_at"),)


class Change(Base):
    __tablename__ = "changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    apartment_id: Mapped[int] = mapped_column(Integer, ForeignKey("apartments.id", ondelete="CASCADE"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    change_type: Mapped[str] = mapped_column(String(50), nullable=False, default="update")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    apartment: Mapped["Apartment"] = relationship(back_populates="changes")
    __table_args__ = (Index("idx_changes_detected", "detected_at"), Index("idx_changes_apartment", "apartment_id", "detected_at"))


class ScraperSettings(Base):
    __tablename__ = "scraper_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    change_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("changes.id", ondelete="SET NULL"), nullable=True)
    apartment_id: Mapped[int] = mapped_column(Integer, ForeignKey("apartments.id", ondelete="CASCADE"), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_successfully: Mapped[bool] = mapped_column(Boolean, default=True)


class CrashDayLog(Base):
    __tablename__ = "crash_day_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    crash_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    apartments_data: Mapped[str] = mapped_column(Text, nullable=False)
    total_freed: Mapped[int] = mapped_column(Integer, default=0)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("idx_crash_date", "crash_date"),)

class ScraperLog(Base):
    __tablename__ = "scraper_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    apartments_checked: Mapped[int] = mapped_column(Integer, default=0)
    apartments_success: Mapped[int] = mapped_column(Integer, default=0)
    apartments_failed: Mapped[int] = mapped_column(Integer, default=0)
    changes_detected: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    is_payday_run: Mapped[bool] = mapped_column(Boolean, default=False)


class RealEstateObject(Base):
    """
    Current known state of a single occupied object from the `/realestate` catalog.

    The catalog only lists *occupied* objects, so a row here means "occupied as of
    last_seen_at". When an object stops appearing in the catalog it has been freed;
    that transition is recorded as a RealEstateEvent and `is_occupied` flips to False.
    """
    __tablename__ = "realestate_objects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stable key: "<server_sid>:<kind>:<unit_id>", e.g. "20:house:1".
    object_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    server_sid: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # "house" | "apartment"
    unit_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    class_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    owner_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    vehicle_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    building_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    image: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_occupied: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        Index("idx_realestate_server_occupied", "server_sid", "is_occupied"),
    )


class RealEstateEvent(Base):
    """
    A detected transition for a realestate object (freed / occupied / owner_changed).

    These are the notifiable events: a `freed` event is the moment a house or
    apartment became available for purchase.
    """
    __tablename__ = "realestate_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    server_sid: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # "house" | "apartment"
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)  # freed | occupied | owner_changed
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    class_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    building_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    old_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    new_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        Index("idx_realestate_event_notified", "notified", "detected_at"),
    )


class RealEstateOwnerHistory(Base):
    """
    Append-only log of the owner nickname seen on a realestate object over time.

    Every time the catalog reports a *different* owner for an object we add a row
    here. This lets us reconstruct who held a house/apartment and when, and — the
    reason it exists — spot a nickname change that happens during Payday, which
    often means the object silently freed and was re-bought (or is about to free)
    before the catalog/map catches up. See RealEstateDetector.
    """
    __tablename__ = "realestate_owner_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Same stable key as RealEstateObject: "<server_sid>:<kind>:<unit_id>".
    object_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    server_sid: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # "house" | "apartment"
    owner_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Owner recorded on the row just before this one (None for the first sighting).
    previous_owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # True if this change was observed inside the Payday window.
    during_payday: Mapped[bool] = mapped_column(Boolean, default=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    __table_args__ = (
        Index("idx_owner_history_key_time", "object_key", "recorded_at"),
    )


class RealEstateBuildingState(Base):
    """
    Current known state of an apartment building aggregate from the catalog.

    Buildings (Eclipse Towers, Seoul Towers, ...) come from the catalog's
    `apartmentHouses` list with a live free/total count. Individual apartments
    are stored as RealEstateObject rows keyed by building via `building_name`;
    this table keeps the per-building rollup for quick catalog listings.
    """
    __tablename__ = "realestate_buildings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stable key: "<server_sid>:building:<building_id>".
    building_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    server_sid: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    building_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    apartments_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    free_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    image: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (
        Index("idx_building_server", "server_sid"),
    )


class RealEstateSubscription(Base):
    """
    A Telegram user's subscription to freed-object notifications for a server.

    A row means "user_id wants to be notified about objects freeing up on the
    server with this sid". `kind` narrows the subscription to houses or
    apartments only; NULL/"any" means both. Notifications for a server are
    routed to its subscribers; if a server has no subscribers we fall back to
    the globally allowed users so nothing goes unheard.
    """
    __tablename__ = "realestate_subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    server_sid: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    # "any" | "house" | "apartment" — which kinds this user wants for the server.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="any")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint("user_id", "server_sid", name="uq_subscription_user_server"),
        Index("idx_subscription_server", "server_sid"),
    )
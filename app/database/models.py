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
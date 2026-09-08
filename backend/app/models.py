"""Layer 1 - the unified data spine.

One normalized schema across booking/PMS, POS, housekeeping, IoT, rosters,
inventory and review platforms, plus the Layer 3 action bus and Layer 4
feedback loop tables.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


# --------------------------------------------------------------------------
# Sellable space (PMS)
# --------------------------------------------------------------------------
class RoomCategory(Base):
    __tablename__ = "room_categories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    room_count: Mapped[int] = mapped_column(Integer)
    base_rate: Mapped[float] = mapped_column(Float)
    max_occupancy: Mapped[int] = mapped_column(Integer, default=2)


class Guest(Base, TimestampMixin):
    __tablename__ = "guests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    tier: Mapped[str] = mapped_column(String(20), default="standard")
    home_city: Mapped[str] = mapped_column(String(80), default="")
    # "Guest DNA" - rolling preference vector + readable profile (slide 4.3)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dna_vector: Mapped[list[float] | None] = mapped_column(JSON, default=None)
    dna_summary: Mapped[str] = mapped_column(Text, default="")
    lifetime_value: Mapped[float] = mapped_column(Float, default=0.0)
    stay_count: Mapped[int] = mapped_column(Integer, default=0)

    bookings: Mapped[list[Booking]] = relationship(back_populates="guest")
    reviews: Mapped[list[Review]] = relationship(back_populates="guest")


class Booking(Base, TimestampMixin):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guest_id: Mapped[int | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("room_categories.id"))
    check_in: Mapped[dt.date] = mapped_column(Date, index=True)
    check_out: Mapped[dt.date] = mapped_column(Date)
    rooms: Mapped[int] = mapped_column(Integer, default=1)
    adults: Mapped[int] = mapped_column(Integer, default=2)
    children: Mapped[int] = mapped_column(Integer, default=0)
    rate: Mapped[float] = mapped_column(Float)
    channel: Mapped[str] = mapped_column(String(32), default="direct")
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    booked_on: Mapped[dt.date] = mapped_column(Date, index=True)

    guest: Mapped[Guest | None] = relationship(back_populates="bookings")

    @property
    def lead_time_days(self) -> int:
        return (self.check_in - self.booked_on).days

    @property
    def nights(self) -> int:
        return max((self.check_out - self.check_in).days, 1)


class OccupancyDaily(Base):
    """Nightly rollup - the timeseries the demand engine trains on."""

    __tablename__ = "occupancy_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("room_categories.id"))
    rooms_available: Mapped[int] = mapped_column(Integer)
    rooms_sold: Mapped[int] = mapped_column(Integer)
    room_revenue: Mapped[float] = mapped_column(Float, default=0.0)
    fnb_revenue: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (UniqueConstraint("date", "category_id", name="uq_occ_date_cat"),)

    @property
    def occupancy_pct(self) -> float:
        return 0.0 if not self.rooms_available else self.rooms_sold / self.rooms_available

    @property
    def adr(self) -> float:
        return 0.0 if not self.rooms_sold else self.room_revenue / self.rooms_sold


class LocalEvent(Base):
    """Demand shifters the forecast must see: festivals, weddings, conferences."""

    __tablename__ = "local_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40), default="festival")
    demand_lift: Mapped[float] = mapped_column(Float, default=0.1)


class CompetitorRate(Base):
    __tablename__ = "competitor_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    competitor: Mapped[str] = mapped_column(String(80))
    category_id: Mapped[str] = mapped_column(String(32))
    rate: Mapped[float] = mapped_column(Float)
    sold_out: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------
# Assets + IoT telemetry
# --------------------------------------------------------------------------
class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    location: Mapped[str] = mapped_column(String(80))
    installed_on: Mapped[dt.date] = mapped_column(Date)
    criticality: Mapped[int] = mapped_column(Integer, default=3)
    rooms_served: Mapped[int] = mapped_column(Integer, default=0)
    replacement_cost: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="healthy")

    readings: Mapped[list[SensorReading]] = relationship(back_populates="asset")
    services: Mapped[list[ServiceRecord]] = relationship(back_populates="asset")


class SensorReading(Base):
    """Hypertable on TimescaleDB, plain table on SQLite."""

    __tablename__ = "sensor_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    ts: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    vibration_mm_s: Mapped[float] = mapped_column(Float, default=0.0)
    temperature_c: Mapped[float] = mapped_column(Float, default=0.0)
    power_kw: Mapped[float] = mapped_column(Float, default=0.0)
    pressure_bar: Mapped[float] = mapped_column(Float, default=0.0)
    runtime_hours: Mapped[float] = mapped_column(Float, default=0.0)

    asset: Mapped[Asset] = relationship(back_populates="readings")

    __table_args__ = (Index("ix_sensor_asset_ts", "asset_id", "ts"),)


class ServiceRecord(Base):
    __tablename__ = "service_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(24), default="preventive")
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    downtime_hours: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")

    asset: Mapped[Asset] = relationship(back_populates="services")


class WorkOrder(Base, TimestampMixin):
    """Execution artifact raised when a maintenance card is approved."""

    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    scheduled_for: Mapped[dt.datetime] = mapped_column(DateTime)
    window_reason: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(String(20), default="open")
    action_card_id: Mapped[int | None] = mapped_column(ForeignKey("action_cards.id"), nullable=True)


# --------------------------------------------------------------------------
# Workforce
# --------------------------------------------------------------------------
class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(40))
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    hourly_cost: Mapped[float] = mapped_column(Float, default=150.0)
    max_hours_week: Mapped[int] = mapped_column(Integer, default=48)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"))
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="planned")


class ShiftAssignment(Base):
    """Roster row - written by the workforce optimizer on approval."""

    __tablename__ = "shift_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    slot: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(40))
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True)
    planned: Mapped[bool] = mapped_column(Boolean, default=True)
    action_card_id: Mapped[int | None] = mapped_column(ForeignKey("action_cards.id"), nullable=True)


class ServiceRequest(Base, TimestampMixin):
    """Guest-raised task backlog from the housekeeping / ops app."""

    __tablename__ = "service_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guest_id: Mapped[int | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    department: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(String(240))
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None


# --------------------------------------------------------------------------
# Inventory (F&B + housekeeping consumables)
# --------------------------------------------------------------------------
class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str] = mapped_column(String(16), default="kg")
    on_hand: Mapped[float] = mapped_column(Float, default=0.0)
    par_level: Mapped[float] = mapped_column(Float, default=0.0)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=2)
    consumption_per_occupied_room: Mapped[float] = mapped_column(Float, default=0.1)
    department: Mapped[str] = mapped_column(String(32), default="fnb")


class PurchaseOrder(Base, TimestampMixin):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("inventory_items.id"))
    quantity: Mapped[float] = mapped_column(Float)
    total_cost: Mapped[float] = mapped_column(Float)
    needed_by: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="raised")
    action_card_id: Mapped[int | None] = mapped_column(ForeignKey("action_cards.id"), nullable=True)


# --------------------------------------------------------------------------
# Guest intelligence
# --------------------------------------------------------------------------
class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guest_id: Mapped[int | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="google")
    posted_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    rating: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    department: Mapped[str] = mapped_column(String(40), default="general")
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)

    guest: Mapped[Guest | None] = relationship(back_populates="reviews")


class ChatMessage(Base):
    """In-stay concierge conversation."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guest_id: Mapped[int] = mapped_column(ForeignKey("guests.id"))
    ts: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)


class SopDocument(Base):
    """Grounding corpus for the RAG concierge - SOPs, menus, facility hours."""

    __tablename__ = "sop_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(40), default="policy")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, default=None)


# --------------------------------------------------------------------------
# Layer 3 - the action bus
# --------------------------------------------------------------------------
class ActionCard(Base, TimestampMixin):
    __tablename__ = "action_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engine: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    impact_inr: Mapped[float] = mapped_column(Float, default=0.0)
    impact_kind: Mapped[str] = mapped_column(String(24), default="revenue")
    impact_note: Mapped[str] = mapped_column(String(200), default="")
    urgency: Mapped[str] = mapped_column(String(16), default="normal")
    # slide 4.4 - explainability: ranked drivers behind the number
    why: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # slide 4.2 - which other engines fed this card
    cross_domain: Mapped[list[str]] = mapped_column(JSON, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(120), index=True, default="")

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(80), default="")
    snooze_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    execution_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------
# Layer 4 - feedback loop
# --------------------------------------------------------------------------
class DecisionLog(Base, TimestampMixin):
    """Every approve/snooze/dismiss, kept as training signal."""

    __tablename__ = "decision_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_card_id: Mapped[int] = mapped_column(ForeignKey("action_cards.id"))
    engine: Mapped[str] = mapped_column(String(40), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    predicted_impact_inr: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_at_decision: Mapped[float] = mapped_column(Float, default=0.0)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decided_by: Mapped[str] = mapped_column(String(80), default="manager")


class Outcome(Base, TimestampMixin):
    """What actually happened after execution - closes the loop."""

    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_card_id: Mapped[int] = mapped_column(ForeignKey("action_cards.id"))
    engine: Mapped[str] = mapped_column(String(40), index=True)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime)
    predicted_impact_inr: Mapped[float] = mapped_column(Float, default=0.0)
    realised_impact_inr: Mapped[float] = mapped_column(Float, default=0.0)
    was_correct: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class EngineRun(Base, TimestampMixin):
    """Observability for the Celery beat schedule."""

    __tablename__ = "engine_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engine: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    cards_emitted: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")

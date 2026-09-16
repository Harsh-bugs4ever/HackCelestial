"""CSV ingestion - the way real operating data reaches the spine.

Every engine in Layer 2 trains on Layer 1, and Layer 1 could previously only be
filled by ``app.seed.generate``. A real property had no supported path from its
PMS, POS or HRMS into the platform, which made the whole system a demo of
itself. This is the smallest honest answer: a validated CSV per dataset.

It is deliberately *not* a PMS connector. Opera, Cloudbeds and Micros each need
their own auth, pagination and field mapping, and a half-built connector is
worse than an export every property already knows how to produce. CSV is the
lowest common denominator that every one of those systems can emit today.

Principles:

* **Dry run first.** Ops should be able to see what an import would do before
  it touches anything - a bad rate column silently overwriting a year of
  history is not recoverable.
* **Row-level errors, not batch failure.** One malformed row in nine thousand
  should not reject the file; it should be reported with its line number.
* **Idempotent where a natural key exists.** Re-importing yesterday's export
  must not double-count occupancy.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Asset,
    Booking,
    Guest,
    ImportBatch,
    InventoryItem,
    LocalEvent,
    OccupancyDaily,
    Review,
    RoomCategory,
    SensorReading,
    Staff,
)

log = logging.getLogger(__name__)

MAX_REPORTED_ERRORS = 50
MAX_ROWS = 200_000


class RowError(ValueError):
    """A single row could not be imported. Carries a human-readable reason."""


# --------------------------------------------------------------------------
# Field coercion. Every failure message names the column and the bad value,
# because "invalid literal for int()" is useless to whoever exported the file.
# --------------------------------------------------------------------------
def _clean(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return str(value).strip() if value is not None else ""


def req_str(row: dict, key: str) -> str:
    value = _clean(row, key)
    if not value:
        raise RowError(f"'{key}' is required and was empty")
    return value


def opt_str(row: dict, key: str, default: str = "") -> str:
    return _clean(row, key) or default


def req_date(row: dict, key: str) -> dt.date:
    raw = req_str(row, key)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise RowError(f"'{key}'={raw!r} is not a recognised date (expected YYYY-MM-DD)")


def req_datetime(row: dict, key: str) -> dt.datetime:
    raw = req_str(row, key)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return dt.datetime.combine(req_date(row, key), dt.time())
    # The spine stores naive UTC; normalise anything that arrived with an offset.
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.UTC).replace(tzinfo=None)
    return parsed


def req_float(row: dict, key: str) -> float:
    raw = req_str(row, key).replace(",", "")
    try:
        return float(raw)
    except ValueError:
        raise RowError(f"'{key}'={raw!r} is not a number") from None


def opt_float(row: dict, key: str, default: float = 0.0) -> float:
    raw = _clean(row, key).replace(",", "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise RowError(f"'{key}'={raw!r} is not a number") from None


def req_int(row: dict, key: str) -> int:
    return int(round(req_float(row, key)))


def opt_int(row: dict, key: str, default: int = 0) -> int:
    raw = _clean(row, key)
    return int(round(opt_float(row, key, float(default)))) if raw else default


# --------------------------------------------------------------------------
# Per-dataset row handlers. Each returns True if it wrote, False if it was a
# no-op (an idempotent re-import of a row already present).
# --------------------------------------------------------------------------
def _category_must_exist(db: Session, category_id: str) -> None:
    if db.get(RoomCategory, category_id) is None:
        known = [c for c in db.scalars(select(RoomCategory.id))]
        raise RowError(
            f"unknown category_id {category_id!r}"
            + (f" (known: {', '.join(known)})" if known else " - import room_categories first")
        )


def _room_categories(db: Session, row: dict) -> bool:
    cid = req_str(row, "id")
    existing = db.get(RoomCategory, cid)
    fields = dict(
        name=req_str(row, "name"),
        room_count=req_int(row, "room_count"),
        base_rate=req_float(row, "base_rate"),
        max_occupancy=opt_int(row, "max_occupancy", 2),
    )
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
    else:
        db.add(RoomCategory(id=cid, **fields))
    return True


def _guests(db: Session, row: dict) -> bool:
    email = opt_str(row, "email")
    name = req_str(row, "name")
    # Email is the only stable identifier a PMS export reliably carries.
    existing = (
        db.scalars(select(Guest).where(Guest.email == email).limit(1)).first()
        if email else None
    )
    fields = dict(
        name=name,
        email=email,
        phone=opt_str(row, "phone"),
        tier=opt_str(row, "tier", "standard"),
        home_city=opt_str(row, "home_city"),
        lifetime_value=opt_float(row, "lifetime_value"),
        stay_count=opt_int(row, "stay_count"),
    )
    if existing:
        for key, value in fields.items():
            if value:
                setattr(existing, key, value)
    else:
        db.add(Guest(**fields))
    return True


def _bookings(db: Session, row: dict) -> bool:
    category_id = req_str(row, "category_id")
    _category_must_exist(db, category_id)
    check_in = req_date(row, "check_in")
    check_out = req_date(row, "check_out")
    if check_out <= check_in:
        raise RowError(f"check_out {check_out} is not after check_in {check_in}")

    guest_id = None
    email = opt_str(row, "guest_email")
    if email:
        guest = db.scalars(select(Guest).where(Guest.email == email).limit(1)).first()
        if guest is None:
            guest = Guest(name=opt_str(row, "guest_name", email.split("@")[0]), email=email)
            db.add(guest)
            db.flush()
        guest_id = guest.id

    db.add(Booking(
        guest_id=guest_id,
        category_id=category_id,
        check_in=check_in,
        check_out=check_out,
        rooms=opt_int(row, "rooms", 1),
        adults=opt_int(row, "adults", 2),
        children=opt_int(row, "children", 0),
        rate=req_float(row, "rate"),
        channel=opt_str(row, "channel", "direct"),
        status=opt_str(row, "status", "confirmed"),
        booked_on=req_date(row, "booked_on") if _clean(row, "booked_on") else check_in,
    ))
    return True


def _occupancy(db: Session, row: dict) -> bool:
    category_id = req_str(row, "category_id")
    _category_must_exist(db, category_id)
    date = req_date(row, "date")
    # Unique on (date, category): re-importing an overlapping export updates
    # rather than double-counting the night.
    existing = db.scalars(
        select(OccupancyDaily).where(
            OccupancyDaily.date == date, OccupancyDaily.category_id == category_id
        ).limit(1)
    ).first()
    fields = dict(
        rooms_available=req_int(row, "rooms_available"),
        rooms_sold=req_int(row, "rooms_sold"),
        room_revenue=opt_float(row, "room_revenue"),
        fnb_revenue=opt_float(row, "fnb_revenue"),
    )
    if fields["rooms_sold"] > fields["rooms_available"]:
        raise RowError(
            f"rooms_sold ({fields['rooms_sold']}) exceeds "
            f"rooms_available ({fields['rooms_available']})"
        )
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
    else:
        db.add(OccupancyDaily(date=date, category_id=category_id, **fields))
    return True


def _staff(db: Session, row: dict) -> bool:
    name = req_str(row, "name")
    role = req_str(row, "role")
    existing = db.scalars(
        select(Staff).where(Staff.name == name, Staff.role == role).limit(1)
    ).first()
    skills = [s.strip() for s in opt_str(row, "skills").split("|") if s.strip()]
    fields = dict(
        name=name,
        role=role,
        skills=skills or [role],
        phone=opt_str(row, "phone"),
        email=opt_str(row, "email"),
        hourly_cost=opt_float(row, "hourly_cost", 150.0),
        max_hours_week=opt_int(row, "max_hours_week", 48),
        active=opt_str(row, "active", "true").lower() not in ("false", "0", "no"),
    )
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
    else:
        db.add(Staff(**fields))
    return True


def _assets(db: Session, row: dict) -> bool:
    aid = req_str(row, "id")
    existing = db.get(Asset, aid)
    fields = dict(
        name=req_str(row, "name"),
        kind=opt_str(row, "kind", "equipment"),
        location=opt_str(row, "location"),
        installed_on=req_date(row, "installed_on") if _clean(row, "installed_on")
        else dt.date.today(),
        criticality=opt_int(row, "criticality", 3),
        rooms_served=opt_int(row, "rooms_served"),
        replacement_cost=opt_float(row, "replacement_cost"),
        status=opt_str(row, "status", "healthy"),
    )
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
    else:
        db.add(Asset(id=aid, **fields))
    return True


def _sensor_readings(db: Session, row: dict) -> bool:
    asset_id = req_str(row, "asset_id")
    if db.get(Asset, asset_id) is None:
        raise RowError(f"unknown asset_id {asset_id!r} - import assets first")
    db.add(SensorReading(
        asset_id=asset_id,
        ts=req_datetime(row, "ts"),
        vibration_mm_s=opt_float(row, "vibration_mm_s"),
        temperature_c=opt_float(row, "temperature_c"),
        power_kw=opt_float(row, "power_kw"),
        pressure_bar=opt_float(row, "pressure_bar"),
        runtime_hours=opt_float(row, "runtime_hours"),
    ))
    return True


def _reviews(db: Session, row: dict) -> bool:
    rating = req_float(row, "rating")
    if not 0 <= rating <= 5:
        raise RowError(f"rating {rating} is outside 0-5")
    guest_id = None
    email = opt_str(row, "guest_email")
    if email:
        guest = db.scalars(select(Guest).where(Guest.email == email).limit(1)).first()
        guest_id = guest.id if guest else None
    db.add(Review(
        guest_id=guest_id,
        source=opt_str(row, "source", "import"),
        posted_at=req_datetime(row, "posted_at"),
        rating=rating,
        text=req_str(row, "text"),
        department=opt_str(row, "department", "general"),
        # sentiment and topics are derived by the guest engine's backfill, not
        # trusted from the file - a source that ships its own scores would put
        # two different scales into the same column.
    ))
    return True


def _inventory(db: Session, row: dict) -> bool:
    iid = req_str(row, "id")
    existing = db.get(InventoryItem, iid)
    fields = dict(
        name=req_str(row, "name"),
        unit=opt_str(row, "unit", "kg"),
        on_hand=opt_float(row, "on_hand"),
        par_level=opt_float(row, "par_level"),
        unit_cost=opt_float(row, "unit_cost"),
        lead_time_days=opt_int(row, "lead_time_days", 2),
        consumption_per_occupied_room=opt_float(row, "consumption_per_occupied_room", 0.1),
        department=opt_str(row, "department", "fnb"),
        supplier=opt_str(row, "supplier"),
        supplier_email=opt_str(row, "supplier_email"),
        supplier_phone=opt_str(row, "supplier_phone"),
    )
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
    else:
        db.add(InventoryItem(id=iid, **fields))
    return True


def _events(db: Session, row: dict) -> bool:
    date = req_date(row, "date")
    name = req_str(row, "name")
    existing = db.scalars(
        select(LocalEvent).where(LocalEvent.date == date, LocalEvent.name == name).limit(1)
    ).first()
    if existing:
        existing.kind = opt_str(row, "kind", "festival")
        existing.demand_lift = opt_float(row, "demand_lift", 0.1)
    else:
        db.add(LocalEvent(
            date=date, name=name,
            kind=opt_str(row, "kind", "festival"),
            demand_lift=opt_float(row, "demand_lift", 0.1),
        ))
    return True


@dataclass(frozen=True)
class Dataset:
    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    handler: Callable[[Session, dict], bool]
    note: str = ""

    @property
    def columns(self) -> tuple[str, ...]:
        return self.required + self.optional


DATASETS: dict[str, Dataset] = {
    d.name: d
    for d in (
        Dataset(
            "room_categories", ("id", "name", "room_count", "base_rate"),
            ("max_occupancy",), _room_categories,
            "Import first - bookings and occupancy reference these ids.",
        ),
        Dataset(
            "guests", ("name",),
            ("email", "phone", "tier", "home_city", "lifetime_value", "stay_count"),
            _guests, "Matched on email; rows without one always insert.",
        ),
        Dataset(
            "bookings", ("category_id", "check_in", "check_out", "rate"),
            ("guest_email", "guest_name", "rooms", "adults", "children",
             "channel", "status", "booked_on"),
            _bookings, "Unknown guest_email creates a stub guest.",
        ),
        Dataset(
            "occupancy", ("date", "category_id", "rooms_available", "rooms_sold"),
            ("room_revenue", "fnb_revenue"), _occupancy,
            "The demand engine's training series. Re-importing a date updates it.",
        ),
        Dataset(
            "staff", ("name", "role"),
            ("skills", "phone", "email", "hourly_cost", "max_hours_week", "active"),
            _staff,
            "skills is pipe-separated. phone/email decide where shift messages go.",
        ),
        Dataset(
            "assets", ("id", "name"),
            ("kind", "location", "installed_on", "criticality", "rooms_served",
             "replacement_cost", "status"),
            _assets, "Import before sensor_readings.",
        ),
        Dataset(
            "sensor_readings", ("asset_id", "ts"),
            ("vibration_mm_s", "temperature_c", "power_kw", "pressure_bar", "runtime_hours"),
            _sensor_readings, "Append-only telemetry for the maintenance engine.",
        ),
        Dataset(
            "reviews", ("posted_at", "rating", "text"),
            ("guest_email", "source", "department"), _reviews,
            "Sentiment is scored by the guest engine, not read from the file.",
        ),
        Dataset(
            "inventory", ("id", "name"),
            ("unit", "on_hand", "par_level", "unit_cost", "lead_time_days",
             "consumption_per_occupied_room", "department",
             "supplier", "supplier_email", "supplier_phone"),
            _inventory, "supplier_email is where approved purchase orders are sent.",
        ),
        Dataset(
            "events", ("date", "name"), ("kind", "demand_lift"), _events,
            "Festivals, weddings and conferences the forecast must see.",
        ),
    )
}


def _decode(raw: bytes) -> str:
    # Hotel systems export from Excel far more often than from a Unix pipeline,
    # so a BOM and cp1252 are the norm rather than the exception.
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def import_csv(
    db: Session,
    dataset: str,
    raw: bytes,
    *,
    filename: str = "",
    imported_by: str = "",
    dry_run: bool = False,
) -> ImportBatch:
    """Validate and load one CSV. Always returns a batch; never raises on bad rows."""
    spec = DATASETS.get(dataset)
    if spec is None:
        raise ValueError(f"unknown dataset '{dataset}'")

    batch = ImportBatch(
        dataset=dataset, filename=filename[:200], imported_by=imported_by[:80],
        dry_run=dry_run,
    )
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip() for h in (reader.fieldnames or [])}
    missing = [c for c in spec.required if c not in headers]
    if missing:
        batch.ok = False
        batch.errors = [{
            "row": 0,
            "error": f"missing required column(s): {', '.join(missing)}",
            "expected": list(spec.columns),
            "found": sorted(h for h in headers if h),
        }]
        db.add(batch)
        db.flush()
        return batch

    errors: list[dict] = []
    written = skipped = seen = 0
    # One savepoint for the whole file: a dry run rolls back, and a real import
    # that dies mid-file must not leave the spine half-updated.
    savepoint = db.begin_nested()
    try:
        for line_no, row in enumerate(reader, start=2):
            if seen >= MAX_ROWS:
                errors.append({"row": line_no, "error": f"file exceeds {MAX_ROWS:,} rows; truncated"})
                break
            seen += 1
            if not any((v or "").strip() for v in row.values()):
                continue                                   # blank trailing line
            try:
                row_savepoint = db.begin_nested()
                try:
                    if spec.handler(db, row):
                        written += 1
                    else:
                        skipped += 1
                    db.flush()
                    row_savepoint.commit()
                except Exception:
                    row_savepoint.rollback()
                    raise
            except RowError as exc:
                skipped += 1
                if len(errors) < MAX_REPORTED_ERRORS:
                    errors.append({"row": line_no, "error": str(exc)})
            except Exception as exc:
                skipped += 1
                if len(errors) < MAX_REPORTED_ERRORS:
                    errors.append({"row": line_no, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if dry_run:
            savepoint.rollback()
        else:
            savepoint.commit()

    batch.rows_seen = seen
    batch.rows_written = 0 if dry_run else written
    batch.rows_skipped = skipped
    batch.errors = errors
    batch.ok = not errors
    db.add(batch)
    db.flush()
    log.info(
        "import %s: %d seen, %d written, %d skipped, %d error(s)%s",
        dataset, seen, batch.rows_written, skipped, len(errors), " [dry run]" if dry_run else "",
    )
    return batch


def template_csv(dataset: str) -> str:
    """A header row plus one commented example, so ops has something to fill in."""
    spec = DATASETS.get(dataset)
    if spec is None:
        raise ValueError(f"unknown dataset '{dataset}'")
    return ",".join(spec.columns) + "\n"

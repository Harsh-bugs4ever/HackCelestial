"""Seed the data spine with a plausible two years of resort operating history.

The numbers have to survive a judge asking "why is that number that number", so
the generator models real structure rather than noise: weekly seasonality, a
monsoon trough, festival spikes, channel-dependent lead times and rates, and
asset telemetry that genuinely degrades on the units we want the maintenance
engine to catch.
"""
from __future__ import annotations

import datetime as dt
import math
import random

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.models import (
    Asset,
    Booking,
    ChatMessage,
    CompetitorRate,
    Guest,
    InventoryItem,
    LeaveRequest,
    LocalEvent,
    OccupancyDaily,
    Review,
    RoomCategory,
    SensorReading,
    ServiceRecord,
    ServiceRequest,
    ShiftAssignment,
    SopDocument,
    Staff,
)

RNG = random.Random(360)

HISTORY_DAYS = 760          # just over 2 years, so Prophet can identify yearly seasonality
FUTURE_BOOKING_DAYS = 60    # on-the-books demand ahead of today

CATEGORIES = [
    # id, name, rooms, base rate (INR)
    ("deluxe", "Deluxe Garden View", 48, 6500.0),
    ("premium", "Premium Sea View", 36, 9800.0),
    ("suite", "Executive Suite", 24, 15500.0),
    ("villa", "Private Pool Villa", 12, 28000.0),
]

CHANNELS = [
    # name, share, rate multiplier, lead-time mean
    ("direct", 0.28, 1.00, 24),
    ("ota", 0.44, 0.88, 16),
    ("corporate", 0.18, 0.82, 34),
    ("travel_agent", 0.10, 0.85, 45),
]

FIRST_NAMES = [
    "Aarav", "Diya", "Vihaan", "Ananya", "Kabir", "Meera", "Rohan", "Ishita",
    "Arjun", "Saanvi", "Aditya", "Nisha", "Farhan", "Tara", "Yash", "Kavya",
    "Rahul", "Priya", "Siddharth", "Neha", "Imran", "Aditi", "Vikram", "Riya",
]
LAST_NAMES = [
    "Sharma", "Iyer", "Patel", "Nair", "Reddy", "Khan", "Bose", "Menon",
    "Kulkarni", "Chatterjee", "Desai", "Rao", "Gupta", "Fernandes", "Shetty",
]
CITIES = ["Mumbai", "Pune", "Bengaluru", "Delhi", "Hyderabad", "Ahmedabad", "Nashik", "Surat"]


# --------------------------------------------------------------------------
# Demand shape
# --------------------------------------------------------------------------
def seasonal_multiplier(day: dt.date) -> float:
    """Coastal Maharashtra resort: peak Oct-Feb, monsoon collapse Jun-Sep."""
    month_curve = {
        1: 1.18, 2: 1.14, 3: 0.98, 4: 0.92, 5: 0.95, 6: 0.62,
        7: 0.54, 8: 0.58, 9: 0.72, 10: 1.10, 11: 1.22, 12: 1.32,
    }
    weekday_curve = {0: 0.78, 1: 0.74, 2: 0.80, 3: 0.88, 4: 1.16, 5: 1.30, 6: 1.02}
    return month_curve[day.month] * weekday_curve[day.weekday()]


def build_events(start: dt.date, end: dt.date) -> list[LocalEvent]:
    """Festivals and city events, repeated per year in the window."""
    templates = [
        ((12, 24), (12, 31), "Christmas & New Year week", "festival", 0.45),
        ((10, 18), (10, 24), "Diwali holidays", "festival", 0.38),
        ((3, 6), (3, 9), "Holi long weekend", "festival", 0.22),
        ((8, 26), (8, 29), "Ganesh Chaturthi", "festival", 0.30),
        ((1, 24), (1, 27), "Republic Day weekend", "public_holiday", 0.20),
        ((2, 12), (2, 15), "Coastal Food & Music Festival", "city_event", 0.28),
        ((11, 8), (11, 11), "Regional Tech Summit", "conference", 0.25),
        ((4, 18), (4, 21), "Destination wedding season", "wedding", 0.18),
    ]
    events: list[LocalEvent] = []
    for year in range(start.year, end.year + 1):
        for (m1, d1), (m2, d2), name, kind, lift in templates:
            try:
                a = dt.date(year, m1, d1)
                b = dt.date(year, m2, d2)
            except ValueError:
                continue
            if b < a:
                b = dt.date(year + 1, m2, d2)
            day = a
            while day <= b:
                if start <= day <= end:
                    events.append(LocalEvent(date=day, name=name, kind=kind, demand_lift=lift))
                day += dt.timedelta(days=1)
    return events


def pick_channel() -> tuple[str, float, int]:
    r = RNG.random()
    acc = 0.0
    for name, share, mult, lead in CHANNELS:
        acc += share
        if r <= acc:
            return name, mult, lead
    return CHANNELS[0][0], CHANNELS[0][2], CHANNELS[0][3]


# --------------------------------------------------------------------------
# Seeders
# --------------------------------------------------------------------------
def seed_categories(db: Session) -> list[RoomCategory]:
    cats = [
        RoomCategory(id=cid, name=name, room_count=rooms, base_rate=rate,
                     max_occupancy=4 if cid in ("suite", "villa") else 3)
        for cid, name, rooms, rate in CATEGORIES
    ]
    db.add_all(cats)
    db.flush()
    return cats


def seed_guests(db: Session, n: int = 420) -> list[Guest]:
    guests = []
    for i in range(n):
        name = f"{RNG.choice(FIRST_NAMES)} {RNG.choice(LAST_NAMES)}"
        tier = RNG.choices(
            ["standard", "silver", "gold", "platinum"], weights=[0.55, 0.25, 0.15, 0.05]
        )[0]
        prefs = {
            "room_preference": RNG.choice(["high_floor", "sea_facing", "quiet_wing", "near_pool"]),
            "dietary": RNG.choice(["none", "vegetarian", "jain", "vegan", "no_seafood"]),
            "usual_arrival": RNG.choice(["early_morning", "afternoon", "late_evening"]),
            "spa_user": RNG.random() < 0.34,
            "travels_with_kids": RNG.random() < 0.38,
            "favourite_amenity": RNG.choice(
                ["poolside cabana", "spa", "sunset cruise", "in-room dining", "gym"]
            ),
        }
        guests.append(
            Guest(
                id=i + 1,
                name=name,
                email=f"{name.split()[0].lower()}.{i}@example.com",
                phone=f"+9198{RNG.randint(10000000, 99999999)}",
                tier=tier,
                home_city=RNG.choice(CITIES),
                preferences=prefs,
            )
        )
    db.add_all(guests)
    db.flush()
    return guests


def seed_bookings(
    db: Session, cats: list[RoomCategory], guests: list[Guest], today: dt.date
) -> None:
    """Generate stays across the window, then roll them up into occupancy_daily."""
    start = today - dt.timedelta(days=HISTORY_DAYS)
    end = today + dt.timedelta(days=FUTURE_BOOKING_DAYS)
    events = build_events(start, end)
    db.add_all(events)
    lift_by_day: dict[dt.date, float] = {}
    for ev in events:
        lift_by_day[ev.date] = lift_by_day.get(ev.date, 0.0) + ev.demand_lift

    # rooms sold per category per night, then materialise bookings that produce it
    sold: dict[tuple[dt.date, str], int] = {}
    revenue: dict[tuple[dt.date, str], float] = {}
    bookings: list[Booking] = []
    bid = 1

    day = start
    while day <= end:
        lift = 1.0 + lift_by_day.get(day, 0.0)
        base = seasonal_multiplier(day) * lift
        # slow YoY growth, so last year is a legitimate comparison baseline
        growth = 1.0 + 0.08 * ((day - start).days / 365.0)
        # future days are only partially booked - that is the whole point of a forecast
        if day > today:
            pickup = 1.0 - math.exp(-(FUTURE_BOOKING_DAYS - (day - today).days) / 26.0)
            pickup = max(0.12, min(0.95, pickup))
        else:
            pickup = 1.0

        for cat in cats:
            # premium inventory sells softer in monsoon, villas hold up better
            elasticity = {"deluxe": 1.05, "premium": 1.0, "suite": 0.93, "villa": 0.88}[cat.id]
            target = base * growth * elasticity * 0.68
            target *= RNG.uniform(0.9, 1.1)
            rooms_sold = int(round(min(1.0, max(0.05, target)) * cat.room_count * pickup))
            rooms_sold = max(0, min(cat.room_count, rooms_sold))

            occ_ratio = rooms_sold / cat.room_count
            # realised ADR rises with demand even before we optimise pricing
            adr = cat.base_rate * (0.82 + 0.42 * occ_ratio) * RNG.uniform(0.97, 1.03)

            sold[(day, cat.id)] = rooms_sold
            revenue[(day, cat.id)] = rooms_sold * adr

            # emit individual bookings (1-3 nights) for a fraction of the sold rooms
            emitted = 0
            while emitted < rooms_sold:
                nights = RNG.choices([1, 2, 3, 4], weights=[0.34, 0.38, 0.20, 0.08])[0]
                rooms = 1
                channel, mult, lead_mean = pick_channel()
                lead = max(0, int(RNG.expovariate(1 / lead_mean)))
                booked_on = day - dt.timedelta(days=lead)
                guest = RNG.choice(guests)
                status = "confirmed"
                if day < today:
                    status = RNG.choices(
                        ["checked_out", "cancelled", "no_show"], weights=[0.93, 0.05, 0.02]
                    )[0]
                bookings.append(
                    Booking(
                        id=bid,
                        guest_id=guest.id,
                        category_id=cat.id,
                        check_in=day,
                        check_out=day + dt.timedelta(days=nights),
                        rooms=rooms,
                        adults=RNG.choices([1, 2, 3, 4], weights=[0.12, 0.62, 0.16, 0.10])[0],
                        children=RNG.choices([0, 1, 2], weights=[0.66, 0.22, 0.12])[0],
                        rate=round(adr * mult, 2),
                        channel=channel,
                        status=status,
                        booked_on=booked_on,
                    )
                )
                bid += 1
                emitted += rooms

        day += dt.timedelta(days=1)

    db.add_all(bookings)

    rollups = []
    day = start
    while day <= end:
        for cat in cats:
            rs = sold[(day, cat.id)]
            rev = revenue[(day, cat.id)]
            rollups.append(
                OccupancyDaily(
                    date=day,
                    category_id=cat.id,
                    rooms_available=cat.room_count,
                    rooms_sold=rs,
                    room_revenue=round(rev, 2),
                    # F&B tracks occupancy with a per-guest spend band
                    fnb_revenue=round(rs * RNG.uniform(950, 1850), 2),
                )
            )
        day += dt.timedelta(days=1)
    db.add_all(rollups)

    # guest lifetime stats
    for g in guests:
        stays = [b for b in bookings if b.guest_id == g.id and b.status == "checked_out"]
        g.stay_count = len(stays)
        g.lifetime_value = round(sum(b.rate * b.nights for b in stays), 2)


def seed_competitor_rates(db: Session, cats: list[RoomCategory], today: dt.date) -> None:
    competitors = ["Blue Lagoon Resort", "Palm Court Retreat", "Seabreeze Grand"]
    rows = []
    for offset in range(-90, FUTURE_BOOKING_DAYS + 1):
        day = today + dt.timedelta(days=offset)
        lift = seasonal_multiplier(day)
        for comp in competitors:
            skew = {"Blue Lagoon Resort": 1.04, "Palm Court Retreat": 0.92, "Seabreeze Grand": 1.12}[comp]
            for cat in cats:
                rate = cat.base_rate * skew * (0.85 + 0.35 * min(lift, 1.6)) * RNG.uniform(0.96, 1.04)
                # competitors sell out on genuine peak days - a real pricing signal
                sold_out = lift > 1.35 and RNG.random() < 0.45
                rows.append(
                    CompetitorRate(
                        date=day,
                        competitor=comp,
                        category_id=cat.id,
                        rate=round(rate, 2),
                        sold_out=sold_out,
                    )
                )
    db.add_all(rows)


def seed_assets(db: Session, today: dt.date) -> list[Asset]:
    specs = [
        # id, name, kind, location, age_years, criticality, rooms_served, replacement cost, degrading?
        ("CHL-01", "Chiller Unit 1", "chiller", "Plant room A", 4.5, 5, 60, 1450000, False),
        ("CHL-02", "Chiller Unit 2", "chiller", "Plant room A", 8.2, 5, 60, 1450000, True),
        ("AHU-03", "AHU - Banquet Wing", "hvac", "Banquet block", 6.0, 4, 0, 620000, False),
        ("AHU-04", "AHU - Villa Block", "hvac", "Villa block", 3.1, 4, 12, 620000, False),
        ("PMP-01", "Pool Circulation Pump", "pump", "Pool deck", 5.4, 3, 0, 180000, True),
        ("PMP-02", "Booster Pump - Tower B", "pump", "Utility room B", 2.2, 4, 48, 210000, False),
        ("WHT-01", "Water Heater Bank A", "water_heater", "Utility room A", 7.1, 4, 48, 340000, False),
        ("WHT-02", "Water Heater Bank B", "water_heater", "Utility room B", 1.8, 4, 36, 340000, False),
        ("ELV-01", "Guest Elevator - Tower A", "elevator", "Tower A", 9.0, 5, 84, 2100000, False),
        ("GEN-01", "Backup Generator 250kVA", "generator", "Plant room B", 6.6, 5, 120, 1850000, False),
        ("KIT-01", "Walk-in Cold Room", "chiller", "Main kitchen", 4.0, 5, 0, 480000, True),
        ("STP-01", "Sewage Treatment Blower", "pump", "STP yard", 5.9, 2, 0, 260000, False),
    ]
    assets = []
    for aid, name, kind, loc, age, crit, rooms, cost, _deg in specs:
        assets.append(
            Asset(
                id=aid,
                name=name,
                kind=kind,
                location=loc,
                installed_on=today - dt.timedelta(days=int(age * 365)),
                criticality=crit,
                rooms_served=rooms,
                replacement_cost=float(cost),
            )
        )
    db.add_all(assets)
    db.flush()

    degrading = {aid for aid, *_rest, deg in specs if deg}

    # 120 days of 6-hourly telemetry - enough for a trend, small enough to seed fast
    readings: list[SensorReading] = []
    services: list[ServiceRecord] = []
    for aid, name, kind, loc, age, crit, rooms, cost, deg in specs:
        baseline = {
            "chiller": dict(vib=2.1, temp=7.5, kw=88.0, bar=4.2),
            "hvac": dict(vib=1.6, temp=18.0, kw=32.0, bar=1.4),
            "pump": dict(vib=1.9, temp=42.0, kw=11.5, bar=3.1),
            "water_heater": dict(vib=0.6, temp=62.0, kw=24.0, bar=2.6),
            "elevator": dict(vib=1.2, temp=34.0, kw=14.0, bar=0.0),
            "generator": dict(vib=3.4, temp=68.0, kw=0.0, bar=2.2),
        }[kind]
        # older units run rougher even when healthy
        wear = 1.0 + 0.035 * age
        runtime = age * 365 * 11.0

        n_points = 120 * 4
        for i in range(n_points):
            ts = dt.datetime.combine(today, dt.time(0, 0)) - dt.timedelta(hours=6 * (n_points - i))
            progress = i / n_points
            # degrading units drift super-linearly in the last third of the window
            drift = (max(0.0, progress - 0.55) / 0.45) ** 1.9 if deg else 0.0
            hour_load = 1.0 + 0.18 * math.sin((ts.hour / 24.0) * 2 * math.pi)

            readings.append(
                SensorReading(
                    asset_id=aid,
                    ts=ts,
                    vibration_mm_s=round(
                        baseline["vib"] * wear * hour_load * (1 + 1.35 * drift) + RNG.gauss(0, 0.09), 3
                    ),
                    temperature_c=round(
                        baseline["temp"] * (1 + 0.30 * drift) + RNG.gauss(0, 0.5), 2
                    ),
                    power_kw=round(
                        baseline["kw"] * hour_load * (1 + 0.22 * drift) + RNG.gauss(0, 0.8), 2
                    ),
                    pressure_bar=round(
                        baseline["bar"] * (1 - 0.18 * drift) + RNG.gauss(0, 0.05), 3
                    ),
                    runtime_hours=round(runtime + i * 5.5, 1),
                )
            )

        # service history: preventive every ~5 months, occasional breakdowns on old units
        d = today - dt.timedelta(days=int(age * 365))
        while d < today:
            d += dt.timedelta(days=RNG.randint(140, 175))
            if d >= today:
                break
            breakdown = RNG.random() < (0.08 + 0.03 * age)
            services.append(
                ServiceRecord(
                    asset_id=aid,
                    date=d,
                    kind="breakdown" if breakdown else "preventive",
                    cost=round(cost * (RNG.uniform(0.05, 0.12) if breakdown else RNG.uniform(0.01, 0.03)), 2),
                    downtime_hours=round(RNG.uniform(6, 26) if breakdown else RNG.uniform(1.5, 4), 1),
                    notes="Unscheduled failure - emergency callout" if breakdown else "Scheduled preventive service",
                )
            )

    db.add_all(readings)
    db.add_all(services)
    for a in assets:
        if a.id in degrading:
            a.status = "watch"
    return assets


def seed_workforce(db: Session, today: dt.date) -> None:
    roles = {
        "housekeeping": (22, 165.0, ["room_cleaning", "laundry", "turndown"]),
        "front_desk": (9, 260.0, ["check_in", "billing", "guest_relations"]),
        "fnb": (18, 190.0, ["service", "bar", "banquet"]),
        "maintenance": (7, 240.0, ["hvac", "electrical", "plumbing"]),
        "spa": (5, 300.0, ["massage", "salon"]),
        "security": (8, 175.0, ["patrol", "cctv"]),
    }
    staff: list[Staff] = []
    sid = 1
    for role, (count, cost, skills) in roles.items():
        for _ in range(count):
            staff.append(
                Staff(
                    id=sid,
                    name=f"{RNG.choice(FIRST_NAMES)} {RNG.choice(LAST_NAMES)}",
                    role=role,
                    skills=RNG.sample(skills, k=RNG.randint(1, len(skills))),
                    hourly_cost=round(cost * RNG.uniform(0.9, 1.15), 2),
                    max_hours_week=48,
                )
            )
            sid += 1
    db.add_all(staff)
    db.flush()

    leaves = []
    for s in staff:
        for _ in range(RNG.randint(0, 3)):
            leaves.append(
                LeaveRequest(
                    staff_id=s.id,
                    date=today + dt.timedelta(days=RNG.randint(-10, 21)),
                    kind=RNG.choice(["planned", "sick", "comp_off"]),
                )
            )
    db.add_all(leaves)

    # open + historical service requests, weighted to housekeeping
    reqs = []
    summaries = {
        "housekeeping": ["Extra towels requested", "Room not serviced", "AC filter smell",
                         "Late turndown", "Bathroom amenities missing"],
        "maintenance": ["AC not cooling", "Geyser lukewarm", "Leaking tap", "TV remote dead"],
        "fnb": ["Room service delayed", "Wrong order delivered", "Breakfast buffet cold"],
        "front_desk": ["Check-in queue long", "Billing discrepancy", "Late checkout request"],
    }
    for offset in range(-30, 1):
        day = today + dt.timedelta(days=offset)
        for _ in range(RNG.randint(4, 14)):
            dept = RNG.choices(list(summaries), weights=[0.42, 0.26, 0.20, 0.12])[0]
            opened = dt.datetime.combine(day, dt.time(RNG.randint(6, 22), RNG.randint(0, 59)))
            resolved = None
            if RNG.random() < (0.72 if offset > -2 else 0.96):
                resolved = opened + dt.timedelta(minutes=RNG.randint(12, 260))
            reqs.append(
                ServiceRequest(
                    department=dept,
                    summary=RNG.choice(summaries[dept]),
                    priority=RNG.choices(["low", "normal", "high"], weights=[0.2, 0.62, 0.18])[0],
                    opened_at=opened,
                    resolved_at=resolved,
                )
            )
    db.add_all(reqs)
    db.flush()
    _seed_existing_roster(db, staff, today)


def _seed_existing_roster(db: Session, staff: list[Staff], today: dt.date) -> None:
    """Publish a roster for the next 10 days at roughly nominal coverage.

    Deliberately a little thin on weekends and on housekeeping, so the workforce
    engine has genuine gaps to find rather than an empty schedule to fill.
    """
    by_role: dict[str, list[Staff]] = {}
    for s in staff:
        by_role.setdefault(s.role, []).append(s)

    # nominal person-shifts per slot at ~70% occupancy of a 120-room property
    NOMINAL = {
        "housekeeping": {"morning": 4, "evening": 2, "night": 1},
        "front_desk": {"morning": 2, "evening": 2, "night": 2},
        "fnb": {"morning": 2, "evening": 3, "night": 1},
        "maintenance": {"morning": 1, "evening": 1, "night": 1},
        "spa": {"morning": 1, "evening": 1, "night": 1},
        "security": {"morning": 2, "evening": 2, "night": 2},
    }

    rows = []
    for offset in range(0, 10):
        day = today + dt.timedelta(days=offset)
        weekend = day.weekday() >= 4
        for role, slots in NOMINAL.items():
            pool = by_role.get(role, [])
            if not pool:
                continue
            cursor = 0
            for slot, count in slots.items():
                planned = count
                # weekends are published short on the guest-facing departments
                if weekend and role in ("housekeeping", "fnb"):
                    planned = max(1, planned - 1)
                for _ in range(planned):
                    person = pool[cursor % len(pool)]
                    cursor += 1
                    rows.append(
                        ShiftAssignment(
                            date=day, slot=slot, role=role,
                            staff_id=person.id, planned=True,
                        )
                    )
    db.add_all(rows)


def seed_inventory(db: Session) -> None:
    items = [
        ("FNB-CHK", "Chicken (boneless)", "kg", 62.0, 90.0, 280.0, 2, 0.42, "fnb"),
        ("FNB-PRW", "Prawns (medium)", "kg", 18.0, 45.0, 620.0, 3, 0.18, "fnb"),
        ("FNB-VEG", "Mixed vegetables", "kg", 140.0, 180.0, 55.0, 1, 0.90, "fnb"),
        ("FNB-MLK", "Milk", "ltr", 96.0, 200.0, 62.0, 1, 1.20, "fnb"),
        ("FNB-EGG", "Eggs", "dozen", 40.0, 85.0, 84.0, 2, 0.55, "fnb"),
        ("FNB-RIC", "Basmati rice", "kg", 210.0, 250.0, 118.0, 4, 0.35, "fnb"),
        ("HK-TWL", "Bath towels", "pcs", 320.0, 480.0, 340.0, 7, 0.85, "housekeeping"),
        ("HK-AMN", "Amenity kits", "pcs", 410.0, 700.0, 46.0, 5, 1.00, "housekeeping"),
        ("HK-LIN", "Bed linen sets", "pcs", 190.0, 300.0, 890.0, 10, 0.42, "housekeeping"),
        ("HK-CLN", "Cleaning chemicals", "ltr", 88.0, 120.0, 210.0, 4, 0.30, "housekeeping"),
    ]
    db.add_all(
        InventoryItem(
            id=i, name=n, unit=u, on_hand=oh, par_level=par, unit_cost=uc,
            lead_time_days=lt, consumption_per_occupied_room=cpr, department=dept,
        )
        for i, n, u, oh, par, uc, lt, cpr, dept in items
    )


REVIEW_BANK = {
    "housekeeping": {
        "neg": [
            "Room was not cleaned until 4 PM despite two reminders at the desk.",
            "Bathroom had hair on the floor at check-in. Housekeeping took an hour to respond.",
            "Turndown service was skipped both nights and towels were never replaced.",
            "Asked three times for extra pillows, they arrived after midnight.",
        ],
        "pos": [
            "Housekeeping was quick and thorough, room spotless every single day.",
            "The staff remembered we wanted early servicing and did it without being asked.",
        ],
    },
    "fnb": {
        "neg": [
            "Room service took 70 minutes and the food arrived cold.",
            "Breakfast buffet ran out of eggs by 9 AM on a full weekend.",
        ],
        "pos": [
            "The seafood at the beach grill was outstanding, easily the best meal of the trip.",
            "Chef accommodated our Jain food requirement without any fuss.",
        ],
    },
    "maintenance": {
        "neg": [
            "AC in the room barely cooled, engineering came twice and it still struggled.",
            "Hot water was inconsistent in the mornings throughout our stay.",
        ],
        "pos": ["Reported a leaking tap and it was fixed within fifteen minutes."],
    },
    "front_desk": {
        "neg": [
            "Check-in took 40 minutes with only one person at the desk on a Saturday.",
            "Billing had a duplicate charge that took a long call to reverse.",
        ],
        "pos": [
            "Front desk upgraded us on arrival and remembered us from last year.",
            "Check-out was quick and the team arranged a cab in minutes.",
        ],
    },
    "general": {
        "neg": ["Good property but felt understaffed for how full it was."],
        "pos": [
            "Beautiful property, the pool villa was worth every rupee.",
            "Second stay here and it keeps getting better. Will return.",
        ],
    },
}


def seed_reviews(db: Session, guests: list[Guest], today: dt.date) -> None:
    """Reviews with a deliberate recent housekeeping sentiment dip.

    That dip is what makes the cross-domain demo real: the guest engine detects
    it, and the workforce engine raises housekeeping staffing because of it.
    """
    rows: list[Review] = []
    for offset in range(-180, 1):
        day = today + dt.timedelta(days=offset)
        recent_hk_problem = offset >= -9  # the storyline window
        for _ in range(RNG.randint(1, 5)):
            if recent_hk_problem and RNG.random() < 0.55:
                dept = "housekeeping"
                polarity = "neg" if RNG.random() < 0.78 else "pos"
            else:
                dept = RNG.choices(
                    ["housekeeping", "fnb", "maintenance", "front_desk", "general"],
                    weights=[0.24, 0.22, 0.14, 0.16, 0.24],
                )[0]
                polarity = "neg" if RNG.random() < 0.26 else "pos"

            text = RNG.choice(REVIEW_BANK[dept][polarity])
            rating = RNG.choice([1.0, 2.0, 2.5]) if polarity == "neg" else RNG.choice([4.0, 4.5, 5.0])
            rows.append(
                Review(
                    guest_id=RNG.choice(guests).id,
                    source=RNG.choice(["google", "tripadvisor", "booking.com", "in_stay_survey"]),
                    posted_at=dt.datetime.combine(day, dt.time(RNG.randint(8, 23), RNG.randint(0, 59))),
                    rating=rating,
                    text=text,
                    department=dept,
                )
            )
    db.add_all(rows)


def seed_chats(db: Session, guests: list[Guest], today: dt.date) -> None:
    in_house = RNG.sample(guests, 12)
    msgs = []
    openers = [
        "Hi, what time does the spa close today?",
        "Can we get a late checkout tomorrow?",
        "Is the sunset cruise running this evening?",
        "Could you send two extra towels to the room please?",
        "What are the vegetarian options for dinner tonight?",
    ]
    for g in in_house:
        ts = dt.datetime.combine(today, dt.time(RNG.randint(9, 20), RNG.randint(0, 59)))
        msgs.append(ChatMessage(guest_id=g.id, ts=ts, role="guest", text=RNG.choice(openers)))
    db.add_all(msgs)


SOPS = [
    ("Spa hours and booking policy", "facility",
     "The Serenity Spa operates 08:00-21:00 daily. Last treatment booking is 20:00. "
     "In-house guests may book same-day subject to therapist availability; a 4-hour "
     "cancellation notice applies or 50% of the treatment value is charged."),
    ("Pool and beach access", "facility",
     "The infinity pool is open 06:00-20:00. The kids pool closes at 19:00. Beach access "
     "is open until sunset; swimming beyond the marked buoys is not permitted during monsoon "
     "months (June to September) on lifeguard advice."),
    ("Late checkout policy", "policy",
     "Standard checkout is 11:00. Late checkout until 14:00 is complimentary for Gold and "
     "Platinum members subject to availability. Beyond 14:00, 50% of the room night is "
     "charged; beyond 18:00, a full night applies."),
    ("In-room dining menu highlights", "menu",
     "In-room dining runs 24 hours. Signature dishes: Malvani prawn curry (INR 850), "
     "Kolhapuri chicken thali (INR 720), truffle mushroom risotto, vegetarian (INR 690), "
     "Jain thali available on request with 45 minutes notice (INR 640). "
     "A 10% service charge applies to all in-room dining orders."),
    ("Sunset cruise and experiences", "experience",
     "The sunset catamaran cruise departs the jetty at 17:30 daily, weather permitting, and "
     "returns by 19:15. INR 2,400 per adult, INR 1,200 per child under 12. Booked through the "
     "concierge before 15:00. Cancelled automatically if the coastguard issues a sea warning."),
    ("Housekeeping service standards", "sop",
     "Rooms are serviced between 09:00 and 15:00 unless the guest requests otherwise. "
     "Turndown service runs 18:00-21:00. Any guest request for linen or amenities must be "
     "fulfilled within 20 minutes; escalate to the duty manager beyond 30 minutes."),
    ("Airport transfer and local travel", "facility",
     "Private airport transfers to Mumbai airport take approximately 2.5 hours and cost "
     "INR 4,500 one way in a sedan, INR 6,200 in an SUV. Bookings need 4 hours notice. "
     "Complimentary shuttle to New Panvel station runs at 10:00 and 16:00 daily."),
    ("Kids club and family facilities", "facility",
     "The Little Explorers kids club operates 10:00-18:00 for ages 4-12, supervised, at no "
     "charge for in-house guests. Babysitting is available at INR 400 per hour with 6 hours notice. "
     "Cribs and high chairs are complimentary on request."),
    ("Guest complaint escalation matrix", "sop",
     "Any guest complaint unresolved after 30 minutes escalates to the duty manager. Complaints "
     "involving billing above INR 5,000, safety, or a repeat issue in the same stay escalate "
     "immediately to the General Manager. Every escalation is logged with a resolution note."),
    ("Banquet and event spaces", "facility",
     "The Coral Hall seats 220 theatre style or 140 banquet. The Lawn accommodates 400 standing. "
     "Minimum spend for a weekend wedding booking is INR 6,00,000. AV equipment and a dedicated "
     "event manager are included above INR 3,00,000."),
]


def seed_sops(db: Session) -> None:
    db.add_all(SopDocument(title=t, category=c, text=x) for t, c, x in SOPS)


# --------------------------------------------------------------------------
def run(reset: bool = True) -> None:
    if reset:
        from app.core.db import Base, engine

        Base.metadata.drop_all(bind=engine)
    init_db()

    today = dt.date.today()
    db = SessionLocal()
    try:
        print(f"Seeding {settings.resort_name} ...")
        cats = seed_categories(db)
        guests = seed_guests(db)
        print(f"  {len(cats)} room categories, {len(guests)} guests")
        seed_bookings(db, cats, guests, today)
        print(f"  bookings + occupancy for {HISTORY_DAYS + FUTURE_BOOKING_DAYS} days")
        seed_competitor_rates(db, cats, today)
        assets = seed_assets(db, today)
        print(f"  {len(assets)} assets with 120 days of telemetry")
        seed_workforce(db, today)
        seed_inventory(db)
        seed_reviews(db, guests, today)
        seed_chats(db, guests, today)
        seed_sops(db)
        db.commit()
        print("Seed complete.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()

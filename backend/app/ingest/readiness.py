"""How much does the system actually know yet?

The seed generator produces 760 days of history precisely so Prophet can find
yearly seasonality. A real property on day one has none of that, and every
engine will still cheerfully emit a card with a confidence score attached.

Nobody had designed the experience of a system that does not know anything yet.
This module answers, per engine, "what do you have, what do you need, and how
far off are you" - so the UI can show an honest banner instead of a confident
number built on eleven days of data.

The engines are deliberately not blocked. A new property still wants its
recommendations; it just needs to be told what they are worth.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.config import settings
from app.models import Asset, OccupancyDaily, Review, SensorReading, Staff

# What each engine needs before its output means much. The demand thresholds
# come from the models themselves: Prophet needs two full cycles to separate
# yearly seasonality from trend, and below one cycle it cannot see it at all.
REQUIREMENTS: dict[str, dict[str, Any]] = {
    "demand": {
        "label": "Demand & revenue",
        "needs": "Nightly occupancy history",
        "unit": "days",
        "usable": 60,
        "trusted": 365,
        "why": (
            "Under 60 days the forecast is a weekly average. Yearly seasonality - "
            "monsoon, festivals - only becomes visible with a full cycle."
        ),
    },
    "maintenance": {
        "label": "Predictive maintenance",
        "needs": "Sensor readings per asset",
        "unit": "readings/asset",
        "usable": 50,
        "trusted": 300,
        "why": (
            "Anomaly detection needs a baseline of normal running before it can "
            "call something abnormal."
        ),
    },
    "workforce": {
        "label": "Workforce optimizer",
        "needs": "Active staff on file",
        "unit": "people",
        "usable": 5,
        "trusted": 20,
        "why": "The solver can only assign people it knows about.",
    },
    "guest": {
        "label": "Guest intelligence",
        "needs": "Reviews and messages",
        "unit": "reviews",
        "usable": 20,
        "trusted": 200,
        "why": (
            "Department sentiment is a lift against a 90-day baseline; too few "
            "reviews and a single bad night looks like a trend."
        ),
    },
}


def _stage(count: float, usable: int, trusted: int) -> str:
    if count >= trusted:
        return "trusted"
    if count >= usable:
        return "learning"
    return "cold"


def _occupancy_days(db: Session, today: dt.date) -> tuple[int, dt.date | None]:
    earliest = db.scalar(select(func.min(OccupancyDaily.date)))
    distinct = db.scalar(
        select(func.count(func.distinct(OccupancyDaily.date)))
    ) or 0
    return int(distinct), earliest


def assess(db: Session, today: dt.date | None = None) -> dict[str, Any]:
    """Per-engine data maturity, plus a headline for the dashboard banner."""
    today = today or business_today()

    occupancy_days, earliest = _occupancy_days(db, today)
    asset_count = db.scalar(select(func.count()).select_from(Asset)) or 0
    reading_count = db.scalar(select(func.count()).select_from(SensorReading)) or 0
    readings_per_asset = (reading_count / asset_count) if asset_count else 0
    staff_count = db.scalar(
        select(func.count()).select_from(Staff).where(Staff.active.is_(True))
    ) or 0
    review_count = db.scalar(select(func.count()).select_from(Review)) or 0

    measured = {
        "demand": occupancy_days,
        "maintenance": readings_per_asset,
        "workforce": staff_count,
        "guest": review_count,
    }

    engines: dict[str, Any] = {}
    for name, spec in REQUIREMENTS.items():
        have = measured[name]
        stage = _stage(have, spec["usable"], spec["trusted"])
        engines[name] = {
            **{k: v for k, v in spec.items() if k != "why"},
            "have": round(float(have), 1),
            "stage": stage,
            "progress": round(min(1.0, have / spec["trusted"]), 3) if spec["trusted"] else 1.0,
            "shortfall": max(0, round(spec["trusted"] - have)),
            "why": spec["why"],
        }

    stages = [e["stage"] for e in engines.values()]
    if all(s == "trusted" for s in stages):
        overall, headline = "trusted", "All engines have a full history to learn from."
    elif any(s == "cold" for s in stages):
        cold = [engines[n]["label"] for n, e in engines.items() if e["stage"] == "cold"]
        overall = "cold"
        headline = (
            f"{', '.join(cold)} {'has' if len(cold) == 1 else 'have'} too little data to be "
            "relied on yet. Recommendations are shown, but treat them as provisional."
        )
    else:
        overall = "learning"
        headline = (
            "The models are still learning. Expect recommendations to sharpen as "
            "more history accumulates."
        )

    return {
        "overall": overall,
        "headline": headline,
        "engines": engines,
        "history": {
            "occupancy_days": occupancy_days,
            "earliest_date": earliest.isoformat() if earliest else None,
            "sensor_readings": reading_count,
            "assets": asset_count,
            "active_staff": staff_count,
            "reviews": review_count,
            "trusted_after_days": settings.min_history_days_trusted,
        },
        # A property with nothing loaded should be pointed at the importer
        # rather than left staring at an empty dashboard.
        "needs_onboarding": occupancy_days == 0 and staff_count == 0,
    }

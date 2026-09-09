"""Engine 1 - Demand & Revenue.

Input:  bookings, seasonality, local events, competitor rates
Output: occupancy forecast per category + a guardrailed rate recommendation

Prophet carries trend/seasonality/holiday structure; XGBoost learns the residual
from features Prophet cannot see (on-the-books pickup, competitor sell-outs,
lead-time mix). The ensemble is blended, then a rule-constrained optimizer turns
forecast occupancy into a rate move inside floor/ceiling guardrails.
"""
from __future__ import annotations

import datetime as dt
import logging
import warnings

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.cache import ModelCache
from app.core.config import settings
from app.engines.bus import Driver, Proposal, executor
from app.models import (
    ActionCard,
    Booking,
    CompetitorRate,
    LocalEvent,
    OccupancyDaily,
    RoomCategory,
)

log = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=FutureWarning)

ENGINE = "demand"
MIN_HISTORY_DAYS = 120


# --------------------------------------------------------------------------
# Feature assembly
# --------------------------------------------------------------------------
def _history(db: Session, category_id: str, today: dt.date) -> pd.DataFrame:
    rows = db.execute(
        select(
            OccupancyDaily.date,
            OccupancyDaily.rooms_sold,
            OccupancyDaily.rooms_available,
            OccupancyDaily.room_revenue,
        )
        .where(OccupancyDaily.category_id == category_id, OccupancyDaily.date < today)
        .order_by(OccupancyDaily.date)
    ).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "rooms_sold", "rooms_available", "room_revenue"])
    df["date"] = pd.to_datetime(df["date"])
    df["occupancy"] = df["rooms_sold"] / df["rooms_available"].clip(lower=1)
    df["adr"] = df["room_revenue"] / df["rooms_sold"].replace(0, np.nan)
    df["adr"] = df["adr"].ffill().bfill()
    return df


def _events_frame(db: Session) -> pd.DataFrame:
    rows = db.execute(select(LocalEvent.date, LocalEvent.name, LocalEvent.demand_lift)).all()
    if not rows:
        return pd.DataFrame(columns=["ds", "holiday", "lift"])
    df = pd.DataFrame(rows, columns=["ds", "holiday", "lift"])
    df["ds"] = pd.to_datetime(df["ds"])
    return df.drop_duplicates(subset=["ds", "holiday"])


def _on_the_books(db: Session, category_id: str, start: dt.date, end: dt.date) -> dict[dt.date, int]:
    """Rooms already sold for future nights - the strongest short-horizon signal."""
    rows = db.execute(
        select(OccupancyDaily.date, OccupancyDaily.rooms_sold).where(
            OccupancyDaily.category_id == category_id,
            OccupancyDaily.date >= start,
            OccupancyDaily.date <= end,
        )
    ).all()
    return {d: int(s) for d, s in rows}


def _competitor_signal(db: Session, category_id: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    rows = db.execute(
        select(
            CompetitorRate.date,
            func.avg(CompetitorRate.rate),
            func.sum(func.cast(CompetitorRate.sold_out, __import__("sqlalchemy").Integer)),
        )
        .where(
            CompetitorRate.category_id == category_id,
            CompetitorRate.date >= start,
            CompetitorRate.date <= end,
        )
        .group_by(CompetitorRate.date)
    ).all()
    if not rows:
        return pd.DataFrame(columns=["date", "comp_rate", "comp_sold_out"])
    return pd.DataFrame(rows, columns=["date", "comp_rate", "comp_sold_out"])


def _lead_time_mix(db: Session, category_id: str, today: dt.date) -> tuple[float, float]:
    """Average booking lead time this year vs the same window last year."""

    def avg_for(window_start: dt.date, window_end: dt.date) -> float:
        rows = db.execute(
            select(Booking.check_in, Booking.booked_on).where(
                Booking.category_id == category_id,
                Booking.check_in >= window_start,
                Booking.check_in <= window_end,
            )
        ).all()
        if not rows:
            return 0.0
        return float(np.mean([(ci - bo).days for ci, bo in rows]))

    now = avg_for(today - dt.timedelta(days=60), today)
    prior = avg_for(today - dt.timedelta(days=425), today - dt.timedelta(days=365))
    return now, prior


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
def _prophet_forecast(df: pd.DataFrame, events: pd.DataFrame, horizon: int) -> pd.DataFrame:
    from prophet import Prophet

    train = df[["date", "occupancy"]].rename(columns={"date": "ds", "occupancy": "y"})
    holidays = None
    if not events.empty:
        holidays = events[["ds", "holiday"]].copy()
        holidays["lower_window"] = 0
        holidays["upper_window"] = 0

    m = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=True,
        daily_seasonality=False,
        holidays=holidays,
        seasonality_mode="multiplicative",
        changepoint_prior_scale=0.08,
        interval_width=0.80,
    )
    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
    m.fit(train)
    future = m.make_future_dataframe(periods=horizon)
    fc = m.predict(future)
    return fc[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(horizon).reset_index(drop=True)


def _xgb_residual(
    df: pd.DataFrame, events: pd.DataFrame, future_dates: list[dt.date], otb: dict[dt.date, int],
    comp: pd.DataFrame, rooms_available: int,
) -> np.ndarray:
    """Learn what Prophet misses from calendar + market features."""
    from xgboost import XGBRegressor

    lift_by_day = dict(zip(events["ds"].dt.date, events["lift"])) if not events.empty else {}
    comp_map = {r.date: (r.comp_rate, r.comp_sold_out) for r in comp.itertuples()} if not comp.empty else {}

    def featurise(d: dt.date, otb_ratio: float) -> list[float]:
        cr, so = comp_map.get(d, (0.0, 0.0))
        return [
            d.weekday(),
            d.month,
            d.timetuple().tm_yday,
            1.0 if d.weekday() >= 4 else 0.0,
            lift_by_day.get(d, 0.0),
            otb_ratio,
            float(cr),
            float(so),
        ]

    hist = df.copy()
    hist["d"] = hist["date"].dt.date
    X, y = [], []
    for r in hist.itertuples():
        d = r.d
        X.append(featurise(d, r.occupancy))  # realised == fully booked for past nights
        y.append(r.occupancy)
    if len(X) < MIN_HISTORY_DAYS:
        return np.zeros(len(future_dates))

    model = XGBRegressor(
        n_estimators=320, max_depth=5, learning_rate=0.06, subsample=0.9,
        colsample_bytree=0.9, reg_lambda=1.2, objective="reg:squarederror", verbosity=0,
    )
    model.fit(np.array(X), np.array(y))

    Xf = np.array([
        featurise(d, otb.get(d, 0) / max(rooms_available, 1)) for d in future_dates
    ])
    return model.predict(Xf)


def forecast_category(
    db: Session, cat: RoomCategory, today: dt.date, horizon: int | None = None
) -> pd.DataFrame:
    """Blended occupancy forecast for one category. Always returns a frame."""
    horizon = horizon or settings.forecast_horizon_days
    df = _history(db, cat.id, today)
    future_dates = [today + dt.timedelta(days=i) for i in range(horizon)]
    otb = _on_the_books(db, cat.id, future_dates[0], future_dates[-1])
    comp = _competitor_signal(db, cat.id, future_dates[0], future_dates[-1])
    events = _events_frame(db)

    if df.empty or len(df) < MIN_HISTORY_DAYS:
        # not enough history to model - fall back to on-the-books with a pickup curve
        occ = [min(1.0, otb.get(d, 0) / max(cat.room_count, 1) * 1.35) for d in future_dates]
        out = pd.DataFrame({"date": future_dates, "occupancy": occ})
        out["lower"] = np.clip(np.array(occ) * 0.85, 0, 1)
        out["upper"] = np.clip(np.array(occ) * 1.15, 0, 1)
        out["source"] = "on_the_books"
        out["confidence"] = 0.45
        return out

    try:
        prophet_fc = _prophet_forecast(df, events, horizon)
        p = prophet_fc["yhat"].to_numpy()
        lo = prophet_fc["yhat_lower"].to_numpy()
        hi = prophet_fc["yhat_upper"].to_numpy()
        source = "prophet+xgboost"
    except Exception as exc:  # Prophet/cmdstan is the one fragile dependency
        log.warning("Prophet unavailable for %s (%s); using seasonal-naive base", cat.id, exc)
        recent = df.tail(364)
        by_dow = recent.groupby(recent["date"].dt.weekday)["occupancy"].mean()
        p = np.array([by_dow.get(d.weekday(), recent["occupancy"].mean()) for d in future_dates])
        lo, hi = p * 0.85, p * 1.15
        source = "seasonal_naive+xgboost"

    try:
        x = _xgb_residual(df, events, future_dates, otb, comp, cat.room_count)
        blended = 0.6 * p + 0.4 * x
    except Exception as exc:
        log.warning("XGBoost leg failed for %s (%s)", cat.id, exc)
        blended = p
        source = source.replace("+xgboost", "")

    # on-the-books is a hard floor: we cannot forecast below rooms already sold
    otb_ratio = np.array([otb.get(d, 0) / max(cat.room_count, 1) for d in future_dates])
    blended = np.maximum(blended, otb_ratio)

    occ = np.clip(blended, 0.0, 1.0)
    # confidence decays with horizon and with interval width
    width = np.clip(hi - lo, 0.02, 0.8)
    conf = np.clip(0.92 - width * 0.9 - np.arange(horizon) * 0.006, 0.35, 0.93)

    return pd.DataFrame({
        "date": future_dates,
        "occupancy": occ,
        "lower": np.clip(lo, 0, 1),
        "upper": np.clip(hi, 0, 1),
        "on_the_books": otb_ratio,
        "source": source,
        "confidence": conf,
    })


# A Prophet fit costs seconds, and four engines plus the simulator all ask for
# the same forecast within one orchestration pass. Cache per (day, horizon) so a
# full run shares a model fit. Entries expire after five minutes, are bounded,
# and are scoped to a database; importers can also invalidate them explicitly.
_FORECAST_CACHE = ModelCache[dict[str, pd.DataFrame]](maxsize=8, ttl=300)


def clear_forecast_cache() -> None:
    _FORECAST_CACHE.clear()


def forecast_all(db: Session, today: dt.date | None = None, horizon: int | None = None) -> dict[str, pd.DataFrame]:
    today = today or dt.date.today()
    horizon = horizon or settings.forecast_horizon_days
    # Engines ask for 7, 14, 21 and 30 days. Fit once at the longest horizon any
    # caller uses and slice, so a shorter request is a cache hit rather than a
    # second Prophet fit.
    canonical = max(horizon, settings.forecast_horizon_days)
    key = (db.get_bind(), today, canonical)
    def compute():
        cats = db.scalars(select(RoomCategory)).all()
        return {c.id: forecast_category(db, c, today, canonical) for c in cats}
    full = _FORECAST_CACHE.get(key, compute)
    return {cid: df.head(horizon).copy() for cid, df in full.items()}


def house_forecast(db: Session, today: dt.date | None = None, horizon: int | None = None) -> pd.DataFrame:
    """Whole-property occupancy - what the other engines consume."""
    today = today or dt.date.today()
    horizon = horizon or settings.forecast_horizon_days
    cats = {c.id: c for c in db.scalars(select(RoomCategory)).all()}
    per_cat = forecast_all(db, today, horizon)
    if not per_cat:
        return pd.DataFrame(columns=["date", "occupancy", "rooms_sold", "rooms_available"])

    frames = []
    for cid, df in per_cat.items():
        f = df[["date", "occupancy", "confidence"]].copy()
        f["rooms_available"] = cats[cid].room_count
        f["rooms_sold"] = f["occupancy"] * cats[cid].room_count
        frames.append(f)
    allf = pd.concat(frames)
    agg = allf.groupby("date").agg(
        rooms_sold=("rooms_sold", "sum"),
        rooms_available=("rooms_available", "sum"),
        confidence=("confidence", "mean"),
    ).reset_index()
    agg["occupancy"] = agg["rooms_sold"] / agg["rooms_available"]
    return agg


# --------------------------------------------------------------------------
# Pricing optimizer - rule-constrained with floor/ceiling guardrails
# --------------------------------------------------------------------------
def recommend_rate(
    base_rate: float, forecast_occ: float, comp_rate: float, comp_sold_out: int, days_out: int,
) -> tuple[float, list[Driver]]:
    """Return (recommended rate, ranked drivers). Never leaves the guardrails."""
    drivers: list[Driver] = []
    multiplier = 1.0

    # 1. demand pressure - the dominant term
    if forecast_occ >= 0.92:
        step, label = 0.18, "Near sell-out forecast"
    elif forecast_occ >= 0.82:
        step, label = 0.11, "High occupancy forecast"
    elif forecast_occ >= 0.68:
        step, label = 0.04, "Healthy occupancy forecast"
    elif forecast_occ >= 0.50:
        step, label = -0.03, "Soft occupancy forecast"
    else:
        step, label = -0.09, "Weak occupancy forecast"
    multiplier += step
    drivers.append(Driver(label, f"{forecast_occ * 100:.0f}% forecast occupancy", abs(step)))

    # 2. competitor scarcity
    if comp_sold_out >= 2:
        multiplier += 0.07
        drivers.append(Driver("Competitor sell-outs", f"{comp_sold_out} comp-set properties sold out", 0.07))
    elif comp_sold_out == 1:
        multiplier += 0.03
        drivers.append(Driver("Competitor sell-out", "1 comp-set property sold out", 0.03))

    # 3. rate position against the comp set
    if comp_rate > 0:
        gap = (comp_rate - base_rate * multiplier) / comp_rate
        if gap > 0.12:
            multiplier += 0.05
            drivers.append(Driver("Priced below comp set", f"{gap * 100:.0f}% under market average", 0.05))
        elif gap < -0.15:
            multiplier -= 0.04
            drivers.append(Driver("Priced above comp set", f"{-gap * 100:.0f}% over market average", 0.04))

    # 4. booking window - late moves have less runway to convert
    if days_out <= 3 and forecast_occ < 0.6:
        multiplier -= 0.05
        drivers.append(Driver("Short booking window", f"{days_out} days out with soft pickup", 0.05))

    multiplier = max(settings.rate_floor_pct, min(settings.rate_ceiling_pct, multiplier))
    drivers.sort(key=lambda d: -d.weight)
    return round(base_rate * multiplier, -1), drivers


def _revenue_delta(rooms: int, occ: float, current: float, proposed: float) -> float:
    """Expected revenue change, with a simple elasticity penalty on the rate move."""
    pct = (proposed - current) / max(current, 1.0)
    # raising rate costs some occupancy; -0.6 elasticity is conservative for resorts
    new_occ = max(0.0, min(1.0, occ * (1 - 0.6 * pct)))
    return (new_occ * rooms * proposed) - (occ * rooms * current)


def run(db: Session, today: dt.date | None = None) -> list[Proposal]:
    today = today or dt.date.today()
    cats = db.scalars(select(RoomCategory)).all()
    proposals: list[Proposal] = []

    lookahead = min(settings.forecast_horizon_days, 21)
    per_cat = forecast_all(db, today)
    for cat in cats:
        fc = per_cat.get(cat.id)
        if fc is None or fc.empty:
            continue
        comp = _competitor_signal(db, cat.id, today, today + dt.timedelta(days=lookahead))
        comp_map = {r.date: (float(r.comp_rate or 0), int(r.comp_sold_out or 0)) for r in comp.itertuples()}
        lt_now, lt_prior = _lead_time_mix(db, cat.id, today)

        for row in fc.head(lookahead).itertuples():
            day = row.date
            occ = float(row.occupancy)
            comp_rate, comp_so = comp_map.get(day, (0.0, 0))
            days_out = (day - today).days

            proposed, drivers = recommend_rate(cat.base_rate, occ, comp_rate, comp_so, days_out)
            if abs(proposed - cat.base_rate) / cat.base_rate < 0.05:
                continue  # not worth a manager's attention

            delta = _revenue_delta(cat.room_count, occ, cat.base_rate, proposed)
            if abs(delta) < 4000:
                continue

            if lt_prior and lt_now:
                shift = (lt_now - lt_prior) / lt_prior
                if abs(shift) > 0.08:
                    drivers.append(Driver(
                        "Lead-time shift",
                        f"bookings arriving {abs(shift) * 100:.0f}% "
                        f"{'earlier' if shift > 0 else 'later'} than last year",
                        0.03,
                    ))

            direction = "increase" if proposed > cat.base_rate else "reduce"
            pct = abs(proposed - cat.base_rate) / cat.base_rate * 100
            proposals.append(Proposal(
                engine=ENGINE,
                kind="rate_change",
                title=f"{direction.capitalize()} {cat.name} rate {pct:.0f}% for {day:%a %d %b}",
                detail=(
                    f"Forecast occupancy {occ * 100:.0f}% "
                    f"({row.source.replace('_', ' ')}, {row.confidence * 100:.0f}% confidence). "
                    f"Comp-set average INR {comp_rate:,.0f}"
                    + (f", {comp_so} sold out" if comp_so else "")
                    + "."
                ),
                recommendation=(
                    f"Move {cat.name} from INR {cat.base_rate:,.0f} to INR {proposed:,.0f} "
                    f"for {day:%d %b} across all channels."
                ),
                confidence=float(row.confidence),
                impact_inr=delta,
                impact_kind="revenue",
                impact_note=f"{cat.room_count} rooms at {occ * 100:.0f}% forecast occupancy",
                urgency="high" if days_out <= 5 and abs(delta) > 25000 else "normal",
                why=drivers[:4],
                cross_domain=[],
                payload={
                    "category_id": cat.id,
                    "date": day.isoformat(),
                    "current_rate": cat.base_rate,
                    "proposed_rate": proposed,
                    "forecast_occupancy": occ,
                },
                dedupe_key=f"demand:rate:{cat.id}:{day.isoformat()}",
            ))

    proposals.sort(key=lambda p: -abs(p.impact_inr))
    return proposals[:8]


@executor("rate_change")
def execute_rate_change(db: Session, card: ActionCard) -> dict:
    """Approval writes the new rate back to the PMS-facing rate table."""
    cat = db.get(RoomCategory, card.payload["category_id"])
    if cat is None:
        raise ValueError(f"unknown category {card.payload['category_id']}")
    previous = cat.base_rate
    cat.base_rate = float(card.payload["proposed_rate"])
    db.flush()
    return {
        "ok": True,
        "artifact": "rate_change",
        "category": cat.name,
        "date": card.payload["date"],
        "previous_rate": previous,
        "new_rate": cat.base_rate,
        "message": f"{cat.name} rate updated to INR {cat.base_rate:,.0f} for {card.payload['date']}.",
    }

"""Engine 2 - Predictive Maintenance.

Input:  IoT telemetry, service history, asset age
Output: failure risk + days-to-failure + a scheduled service window

Isolation Forest flags anomalous sensor states without labels; LightGBM turns
the telemetry trend into a calibrated failure probability. The window itself is
cross-domain: it comes from the demand engine's lowest-occupancy night, which is
the concrete "engines talk to each other" claim on slide 4.2.
"""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.bus import Driver, Proposal, executor
from app.models import ActionCard, Asset, SensorReading, ServiceRecord, WorkOrder, utcnow

log = logging.getLogger(__name__)

ENGINE = "maintenance"
RISK_THRESHOLD = 0.35
METRICS = ["vibration_mm_s", "temperature_c", "power_kw", "pressure_bar"]
# below this absolute level a channel is treated as unused by the asset kind
INACTIVE_CHANNEL_FLOOR = 0.5
# per-channel cap on relative drift, so one noisy line cannot pin the score
MAX_DRIFT = 1.2

# per-kind alarm bands, used for the physical explanation on the card
NOMINAL = {
    "chiller": {"vibration_mm_s": 2.8, "temperature_c": 9.0, "power_kw": 105.0},
    "hvac": {"vibration_mm_s": 2.2, "temperature_c": 22.0, "power_kw": 40.0},
    "pump": {"vibration_mm_s": 2.6, "temperature_c": 52.0, "power_kw": 14.0},
    "water_heater": {"vibration_mm_s": 1.2, "temperature_c": 72.0, "power_kw": 30.0},
    "elevator": {"vibration_mm_s": 1.8, "temperature_c": 42.0, "power_kw": 18.0},
    "generator": {"vibration_mm_s": 4.2, "temperature_c": 82.0, "power_kw": 10.0},
}


def _telemetry(db: Session, asset_id: str) -> pd.DataFrame:
    rows = db.execute(
        select(
            SensorReading.ts,
            SensorReading.vibration_mm_s,
            SensorReading.temperature_c,
            SensorReading.power_kw,
            SensorReading.pressure_bar,
            SensorReading.runtime_hours,
        )
        .where(SensorReading.asset_id == asset_id)
        .order_by(SensorReading.ts)
    ).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", *METRICS, "runtime_hours"])
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _anomaly_score(df: pd.DataFrame) -> tuple[float, float]:
    """Isolation Forest fitted on the stable early window, scored on the recent one.

    Returns (recent anomaly rate, mean anomaly score). Unsupervised on purpose:
    a resort has no labelled failure set to train on.
    """
    from sklearn.ensemble import IsolationForest

    if len(df) < 80:
        return 0.0, 0.0
    split = int(len(df) * 0.55)
    train = df.iloc[:split][METRICS].to_numpy()
    recent = df.iloc[-56:][METRICS].to_numpy()  # last ~14 days at 6-hourly

    iso = IsolationForest(n_estimators=200, contamination=0.06, random_state=360)
    iso.fit(train)
    pred = iso.predict(recent)
    scores = iso.score_samples(recent)
    return float((pred == -1).mean()), float(-scores.mean())


def _trend_features(df: pd.DataFrame) -> dict[str, float]:
    """Slope and level shift per metric, normalised so kinds are comparable.

    A metric a given asset kind does not use sits at ~0 (an elevator has no
    pressure line). Normalising by that mean divides sensor noise by almost
    zero and manufactures a catastrophic trend, so an inactive channel is
    reported as flat instead.
    """
    feats: dict[str, float] = {}
    n = len(df)
    if n < 40:
        return {f"{m}_slope": 0.0 for m in METRICS} | {f"{m}_shift": 0.0 for m in METRICS}

    x = np.arange(n, dtype=float)
    baseline_win = df.iloc[: max(20, n // 4)]
    recent_win = df.iloc[-max(20, n // 8):]
    for m in METRICS:
        y = df[m].to_numpy(dtype=float)
        level = abs(float(np.mean(y)))
        spread = float(np.std(y))
        # inactive channel: no meaningful level, or noise as large as the signal
        if level < INACTIVE_CHANNEL_FLOOR or level < spread:
            feats[f"{m}_slope"] = 0.0
            feats[f"{m}_shift"] = 0.0
            continue
        slope = float(np.polyfit(x, y, 1)[0]) * n / level
        shift = (float(recent_win[m].mean()) - float(baseline_win[m].mean())) / level
        # a single channel cannot dominate the score outright
        feats[f"{m}_slope"] = float(np.clip(slope, -MAX_DRIFT, MAX_DRIFT))
        feats[f"{m}_shift"] = float(np.clip(shift, -MAX_DRIFT, MAX_DRIFT))
    return feats


def _failure_probability(
    db: Session, asset: Asset, df: pd.DataFrame, anomaly_rate: float, feats: dict[str, float]
) -> tuple[float, str]:
    """LightGBM on synthetic-labelled history windows, with a physics fallback.

    Labels come from the asset's own service history: a window that ended within
    21 days of a recorded breakdown is a positive. Sparse, but it is real signal
    and it is what a resort would actually have on day one.
    """
    breakdowns = [
        s.date for s in db.scalars(
            select(ServiceRecord).where(
                ServiceRecord.asset_id == asset.id, ServiceRecord.kind == "breakdown"
            )
        ).all()
    ]

    physics = _calibrate(_physics_risk(asset, feats, anomaly_rate))
    if len(df) < 120 or len(breakdowns) < 2:
        return physics, "trend+anomaly rules"

    try:
        from lightgbm import LGBMClassifier

        # build rolling windows over the telemetry, labelled against breakdown dates
        X, y = [], []
        step = 8
        win = 40
        for end in range(win, len(df), step):
            w = df.iloc[end - win: end]
            wf = _trend_features(w)
            X.append([wf[k] for k in sorted(wf)])
            w_end = w["ts"].iloc[-1].date()
            y.append(int(any(0 <= (b - w_end).days <= 21 for b in breakdowns)))

        if len(set(y)) < 2:
            return physics, "trend+anomaly rules"

        model = LGBMClassifier(
            n_estimators=180, num_leaves=15, learning_rate=0.06,
            min_child_samples=5, verbosity=-1, random_state=360,
        )
        model.fit(np.array(X), np.array(y))
        cur = _trend_features(df.tail(win))
        prob = float(model.predict_proba(np.array([[cur[k] for k in sorted(cur)]]))[0][1])
        # The label set is thin and breakdown-heavy on old assets, so the model
        # leg is a minority vote and the interpretable term carries the estimate.
        blended = 0.35 * prob + 0.65 * physics
        return _calibrate(blended), "LightGBM + trend/anomaly"
    except Exception as exc:
        log.warning("LightGBM leg failed for %s (%s)", asset.id, exc)
        return physics, "trend+anomaly rules"


def _calibrate(p: float) -> float:
    """Keep the number honest.

    A risk score of 100% claims certainty no sensor trend can support, and it is
    the first thing a domain expert will disbelieve. Squash the top of the range
    so the highest a card ever claims is 93%.
    """
    return float(np.clip(p, 0.02, 0.93))


def _physics_risk(asset: Asset, feats: dict[str, float], anomaly_rate: float) -> float:
    """Interpretable risk from degradation direction, age and anomaly density.

    The gain is deliberately gentle: with a steep sigmoid every genuinely
    degrading unit saturates at the cap and the board reads "93%, 93%, 93%",
    which tells the manager nothing about which one to service first.
    """
    vib = max(0.0, feats.get("vibration_mm_s_shift", 0.0))
    temp = max(0.0, feats.get("temperature_c_shift", 0.0))
    power = max(0.0, feats.get("power_kw_shift", 0.0))
    pressure_loss = max(0.0, -feats.get("pressure_bar_shift", 0.0))

    age_years = (dt.date.today() - asset.installed_on).days / 365.25
    age_term = min(age_years / 12.0, 0.9)

    raw = (
        1.9 * vib + 1.5 * temp + 1.2 * power + 1.1 * pressure_loss
        + 1.3 * anomaly_rate + 0.35 * age_term
    )
    return float(1 / (1 + np.exp(-(raw * 1.35 - 2.0))))


def _days_to_failure(prob: float, feats: dict[str, float]) -> int:
    """Days until the dominant metric reaches its alarm band.

    Extrapolating the drift alone collapses to the floor once a unit is clearly
    degrading, which produced a meaningless "within 1 days" on every bad asset.
    Risk sets the band; drift positions the estimate inside it.
    """
    drift = max(
        feats.get("vibration_mm_s_shift", 0.0),
        feats.get("temperature_c_shift", 0.0),
        0.0,
    )
    if prob >= 0.80:
        lo, hi = 5, 14
    elif prob >= 0.60:
        lo, hi = 10, 25
    elif prob >= 0.45:
        lo, hi = 18, 40
    else:
        lo, hi = 30, 75

    # within the band, faster drift means sooner
    steepness = float(np.clip(drift / 0.6, 0.0, 1.0))
    days = hi - (hi - lo) * steepness
    return int(round(float(np.clip(days, 4, 90))))


def _impact(asset: Asset, prob: float) -> tuple[float, str]:
    """Cost avoided = emergency premium + room-nights protected."""
    breakdown_cost = asset.replacement_cost * 0.16      # emergency repair + parts premium
    planned_cost = asset.replacement_cost * 0.025
    # rooms knocked out for the outage, valued at a conservative ADR
    room_nights = asset.rooms_served * 1.5
    room_loss = room_nights * 7200
    avoided = (breakdown_cost - planned_cost + room_loss) * prob
    note = (
        f"{int(room_nights)} room-nights protected" if asset.rooms_served
        else f"{asset.kind.replace('_', ' ')} downtime avoided"
    )
    return avoided, note


def best_service_window(db: Session, within_days: int, today: dt.date) -> tuple[dt.date, float, str]:
    """CROSS-DOMAIN: ask the demand engine for the softest night in the window.

    This single call is the difference between "service it soon" and
    "service it Tuesday 06:00, when we are 41% full".
    """
    from app.engines import demand

    horizon = max(7, min(within_days, 30))
    fc = demand.house_forecast(db, today, horizon)
    if fc.empty:
        return today + dt.timedelta(days=2), 0.0, "no forecast available - defaulted to +2 days"

    window = fc[fc["date"] <= today + dt.timedelta(days=within_days)]
    if window.empty:
        window = fc
    row = window.loc[window["occupancy"].idxmin()]
    day = row["date"]
    if isinstance(day, pd.Timestamp):
        day = day.date()
    occ = float(row["occupancy"])
    return day, occ, (
        f"lowest forecast occupancy in the next {within_days} days "
        f"({occ * 100:.0f}% on {day:%a %d %b})"
    )


# Assessing one asset fits an IsolationForest over its telemetry, which costs
# well over a second. The dashboard, the asset detail route and the engine run
# all assess the same assets for the same day, so memoise the computed part per
# (asset, day) - mirroring _FORECAST_CACHE in demand.py.
#
# The Asset row is deliberately NOT cached: it belongs to the caller's session
# and would raise DetachedInstanceError once that session closes. It is stripped
# on the way in and re-attached from the caller's own object on the way out.
_ASSESS_CACHE: dict[tuple[str, dt.date], dict | None] = {}


def clear_assess_cache() -> None:
    _ASSESS_CACHE.clear()


def assess(db: Session, asset: Asset, today: dt.date) -> dict | None:
    key = (asset.id, today)
    if key in _ASSESS_CACHE:
        cached = _ASSESS_CACHE[key]
        return None if cached is None else {**cached, "asset": asset}

    result = _assess_uncached(db, asset, today)
    _ASSESS_CACHE[key] = (
        None if result is None else {k: v for k, v in result.items() if k != "asset"}
    )
    return result


def _assess_uncached(db: Session, asset: Asset, today: dt.date) -> dict | None:
    df = _telemetry(db, asset.id)
    if df.empty:
        return None
    feats = _trend_features(df)
    anomaly_rate, anomaly_score = _anomaly_score(df)
    prob, method = _failure_probability(db, asset, df, anomaly_rate, feats)
    return {
        "asset": asset,
        "probability": prob,
        "method": method,
        "anomaly_rate": anomaly_rate,
        "anomaly_score": anomaly_score,
        "features": feats,
        "days_to_failure": _days_to_failure(prob, feats),
        "latest": df.iloc[-1].to_dict(),
        "series": df,
    }


def health_board(db: Session, today: dt.date | None = None) -> list[dict]:
    """Asset health for the live dashboard, worst first."""
    today = today or dt.date.today()
    out = []
    for asset in db.scalars(select(Asset)).all():
        a = assess(db, asset, today)
        if a is None:
            continue
        out.append({
            "id": asset.id,
            "name": asset.name,
            "kind": asset.kind,
            "location": asset.location,
            "criticality": asset.criticality,
            "rooms_served": asset.rooms_served,
            "risk": round(a["probability"], 3),
            "days_to_failure": a["days_to_failure"],
            "status": (
                "critical" if a["probability"] >= 0.6
                else "watch" if a["probability"] >= RISK_THRESHOLD
                else "healthy"
            ),
            "vibration_mm_s": round(float(a["latest"]["vibration_mm_s"]), 2),
            "temperature_c": round(float(a["latest"]["temperature_c"]), 1),
            "power_kw": round(float(a["latest"]["power_kw"]), 1),
        })
    return sorted(out, key=lambda r: -r["risk"])


def run(db: Session, today: dt.date | None = None) -> list[Proposal]:
    today = today or dt.date.today()
    proposals: list[Proposal] = []

    for asset in db.scalars(select(Asset)).all():
        a = assess(db, asset, today)
        if a is None or a["probability"] < RISK_THRESHOLD:
            continue

        prob = a["probability"]
        dtf = a["days_to_failure"]
        impact, impact_note = _impact(asset, prob)
        window_day, window_occ, window_reason = best_service_window(db, min(dtf, 21), today)
        service_at = dt.datetime.combine(window_day, dt.time(6, 0))

        feats = a["features"]
        nominal = NOMINAL.get(asset.kind, {})
        drivers = [
            Driver(
                "Vibration trend",
                f"{a['latest']['vibration_mm_s']:.2f} mm/s, "
                f"up {feats['vibration_mm_s_shift'] * 100:.0f}% vs baseline"
                + (f" (alarm at {nominal['vibration_mm_s']} mm/s)" if "vibration_mm_s" in nominal else ""),
                min(abs(feats["vibration_mm_s_shift"]) * 2.2, 1.0),
            ),
            Driver(
                "Thermal drift",
                f"{a['latest']['temperature_c']:.1f} C, "
                f"{feats['temperature_c_shift'] * 100:+.0f}% vs baseline",
                min(abs(feats["temperature_c_shift"]) * 2.2, 1.0),
            ),
            Driver(
                "Power draw",
                f"{a['latest']['power_kw']:.1f} kW, "
                f"{feats['power_kw_shift'] * 100:+.0f}% for the same load profile",
                min(abs(feats["power_kw_shift"]) * 2.0, 1.0),
            ),
            Driver(
                "Anomaly density",
                f"{a['anomaly_rate'] * 100:.0f}% of readings in the last 14 days flagged by Isolation Forest",
                min(a["anomaly_rate"] * 2.5, 1.0),
            ),
            Driver(
                "Asset age",
                f"{(today - asset.installed_on).days / 365.25:.1f} years in service, "
                f"criticality {asset.criticality}/5",
                0.25,
            ),
        ]
        drivers.sort(key=lambda d: -d.weight)

        proposals.append(Proposal(
            engine=ENGINE,
            kind="work_order",
            title=f"{asset.name} - {prob * 100:.0f}% failure risk within {dtf} days",
            detail=(
                f"{asset.name} ({asset.location}) is trending toward failure. "
                f"Risk model: {a['method']}. Estimated {dtf} days to alarm threshold."
            ),
            recommendation=(
                f"Service {asset.name} on {window_day:%a %d %b} at 06:00 - {window_reason}."
            ),
            confidence=round(min(0.95, 0.55 + prob * 0.4), 3),
            impact_inr=impact,
            impact_kind="cost_avoided",
            impact_note=impact_note,
            urgency="critical" if prob >= 0.6 and dtf <= 10 else "high" if prob >= 0.5 else "normal",
            why=drivers[:4],
            cross_domain=["demand"],  # the window came from the forecast
            payload={
                "asset_id": asset.id,
                "risk": prob,
                "days_to_failure": dtf,
                "scheduled_for": service_at.isoformat(),
                "window_reason": window_reason,
                "window_occupancy": window_occ,
            },
            dedupe_key=f"maintenance:wo:{asset.id}",
        ))

    proposals.sort(key=lambda p: -(p.impact_inr * p.confidence))
    return proposals


@executor("work_order")
def execute_work_order(db: Session, card: ActionCard) -> dict:
    """Approval raises a real work order against the asset."""
    asset = db.get(Asset, card.payload["asset_id"])
    if asset is None:
        raise ValueError(f"unknown asset {card.payload['asset_id']}")

    wo = WorkOrder(
        asset_id=asset.id,
        scheduled_for=dt.datetime.fromisoformat(card.payload["scheduled_for"]),
        window_reason=card.payload.get("window_reason", ""),
        priority="high" if card.urgency in ("critical", "high") else "normal",
        status="open",
        action_card_id=card.id,
    )
    db.add(wo)
    asset.status = "service_scheduled"
    db.flush()
    return {
        "ok": True,
        "artifact": "work_order",
        "work_order_id": wo.id,
        "asset": asset.name,
        "scheduled_for": wo.scheduled_for.isoformat(),
        "message": (
            f"Work order #{wo.id} raised for {asset.name}, "
            f"scheduled {wo.scheduled_for:%a %d %b %H:%M}."
        ),
        "raised_at": utcnow().isoformat(),
    }

"""HTTP surface for the Next.js frontend."""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.engines import bus, demand, guest, maintenance, orchestrator, workforce
from app.models import (
    ActionCard,
    Asset,
    Booking,
    DecisionLog,
    EngineRun,
    Guest,
    InventoryItem,
    OccupancyDaily,
    Outcome,
    PurchaseOrder,
    Review,
    RoomCategory,
    ServiceRequest,
    ShiftAssignment,
    SopDocument,
    WorkOrder,
)

router = APIRouter()


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------
def card_json(c: ActionCard) -> dict[str, Any]:
    return {
        "id": c.id,
        "engine": c.engine,
        "kind": c.kind,
        "title": c.title,
        "detail": c.detail,
        "recommendation": c.recommendation,
        "confidence": c.confidence,
        "impact_inr": c.impact_inr,
        "impact_kind": c.impact_kind,
        "impact_note": c.impact_note,
        "urgency": c.urgency,
        "why": c.why,
        "cross_domain": c.cross_domain,
        "payload": c.payload,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "decided_at": c.decided_at.isoformat() if c.decided_at else None,
        "decided_by": c.decided_by,
        "executed_at": c.executed_at.isoformat() if c.executed_at else None,
        "execution_result": c.execution_result,
    }


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    """The one screen a manager opens at the start of a shift."""
    today = dt.date.today()

    occ_rows = db.execute(
        select(
            func.sum(OccupancyDaily.rooms_sold),
            func.sum(OccupancyDaily.rooms_available),
            func.sum(OccupancyDaily.room_revenue),
            func.sum(OccupancyDaily.fnb_revenue),
        ).where(OccupancyDaily.date == today)
    ).one()
    sold, available, room_rev, fnb_rev = (x or 0 for x in occ_rows)

    open_requests = db.scalars(
        select(ServiceRequest).where(ServiceRequest.resolved_at.is_(None))
    ).all()

    health = maintenance.health_board(db, today)
    gaps = workforce.staffing_gaps(db, today, days=2)

    pending = db.scalars(
        select(ActionCard).where(ActionCard.status == "pending")
    ).all()
    ranked = bus.rank(pending)

    arrivals = db.scalar(
        select(func.count()).select_from(Booking).where(
            Booking.check_in == today, Booking.status.in_(("confirmed", "checked_out"))
        )
    ) or 0
    departures = db.scalar(
        select(func.count()).select_from(Booking).where(Booking.check_out == today)
    ) or 0

    sentiment = guest.department_sentiment(db, today)
    overall_sentiment = (
        round(sum(s["recent"] for s in sentiment.values()) / len(sentiment), 3) if sentiment else 0.0
    )

    return {
        "resort": settings.resort_name,
        "date": today.isoformat(),
        "occupancy": {
            "rooms_sold": int(sold),
            "rooms_available": int(available),
            "pct": round(sold / available, 4) if available else 0.0,
            "adr": round(room_rev / sold, 2) if sold else 0.0,
            "revpar": round(room_rev / available, 2) if available else 0.0,
            "room_revenue": round(float(room_rev), 2),
            "fnb_revenue": round(float(fnb_rev), 2),
        },
        "movements": {"arrivals": int(arrivals), "departures": int(departures)},
        "requests": {
            "open": len(open_requests),
            "high_priority": sum(1 for r in open_requests if r.priority == "high"),
            "by_department": {
                d: sum(1 for r in open_requests if r.department == d)
                for d in sorted({r.department for r in open_requests})
            },
        },
        "asset_health": {
            "critical": sum(1 for a in health if a["status"] == "critical"),
            "watch": sum(1 for a in health if a["status"] == "watch"),
            "healthy": sum(1 for a in health if a["status"] == "healthy"),
            "worst": health[:4],
        },
        "staffing": {
            "gaps": len(gaps),
            "short_by": sum(g["short_by"] for g in gaps),
            "detail": gaps[:6],
        },
        "sentiment": {"overall": overall_sentiment, "by_department": sentiment},
        "actions": {
            "pending": len(pending),
            "critical": sum(1 for c in pending if c.urgency == "critical"),
            "total_impact_inr": round(sum(c.impact_inr for c in pending), 2),
            "top": [card_json(c) for c in ranked[:5]],
        },
    }


# --------------------------------------------------------------------------
# Action bus
# --------------------------------------------------------------------------
@router.get("/actions")
def list_actions(
    status: str = Query("pending"),
    engine: str | None = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(ActionCard)
    if status != "all":
        stmt = stmt.where(ActionCard.status == status)
    if engine:
        stmt = stmt.where(ActionCard.engine == engine)
    cards = list(db.scalars(stmt.order_by(ActionCard.created_at.desc()).limit(limit)).all())
    ordered = bus.rank(cards) if status == "pending" else cards
    return {
        "count": len(ordered),
        "total_impact_inr": round(sum(c.impact_inr for c in ordered), 2),
        "cards": [card_json(c) for c in ordered],
    }


@router.get("/actions/{card_id}")
def get_action(card_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    card = db.get(ActionCard, card_id)
    if card is None:
        raise HTTPException(404, "action card not found")
    return card_json(card)


class Decision(BaseModel):
    by: str = "manager"
    snooze_hours: int = Field(24, ge=1, le=720)


@router.post("/actions/{card_id}/{decision}")
def decide(
    card_id: int,
    decision: Literal["approve", "snooze", "dismiss"],
    body: Decision | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """AI recommends, human decides, system executes."""
    card = db.get(ActionCard, card_id)
    if card is None:
        raise HTTPException(404, "action card not found")
    body = body or Decision()

    if decision == "approve":
        bus.approve(db, card, body.by)
    elif decision == "snooze":
        bus.snooze(db, card, body.snooze_hours, body.by)
    else:
        bus.dismiss(db, card, body.by)
    db.commit()
    db.refresh(card)
    return card_json(card)


@router.get("/actions-feed/history")
def action_history(limit: int = Query(40, le=200), db: Session = Depends(get_db)) -> dict[str, Any]:
    cards = db.scalars(
        select(ActionCard)
        .where(ActionCard.status.in_(("approved", "executed", "dismissed")))
        .order_by(ActionCard.decided_at.desc())
        .limit(limit)
    ).all()
    return {"count": len(cards), "cards": [card_json(c) for c in cards]}


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------
@router.post("/engines/run")
def run_engines(engine: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    if engine:
        if engine not in orchestrator.ENGINES:
            raise HTTPException(400, f"unknown engine '{engine}'")
        results = [orchestrator.run_engine(db, engine)]
    else:
        results = orchestrator.run_all(db)
    orchestrator.apply_learning(db)
    return {"results": results}


@router.get("/engines/status")
def engine_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    out = {}
    for name in orchestrator.ENGINES:
        last = db.scalars(
            select(EngineRun).where(EngineRun.engine == name)
            .order_by(EngineRun.started_at.desc()).limit(1)
        ).first()
        pending = db.scalar(
            select(func.count()).select_from(ActionCard).where(
                ActionCard.engine == name, ActionCard.status == "pending"
            )
        ) or 0
        out[name] = {
            "last_run": last.started_at.isoformat() if last else None,
            "ok": last.ok if last else None,
            "error": last.error if last else "",
            "cards_last_run": last.cards_emitted if last else 0,
            "pending_cards": int(pending),
        }
    return out


@router.get("/forecast")
def forecast(
    days: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    today = dt.date.today()
    house = demand.house_forecast(db, today, days)
    per_cat = demand.forecast_all(db, today, days)

    history = db.execute(
        select(
            OccupancyDaily.date,
            func.sum(OccupancyDaily.rooms_sold),
            func.sum(OccupancyDaily.rooms_available),
            func.sum(OccupancyDaily.room_revenue),
        )
        .where(OccupancyDaily.date >= today - dt.timedelta(days=60), OccupancyDaily.date < today)
        .group_by(OccupancyDaily.date)
        .order_by(OccupancyDaily.date)
    ).all()

    return {
        "history": [
            {
                "date": d.isoformat(),
                "occupancy": round(s / a, 4) if a else 0.0,
                "adr": round(r / s, 2) if s else 0.0,
            }
            for d, s, a, r in history
        ],
        "forecast": [
            {
                "date": r.date.isoformat() if hasattr(r.date, "isoformat") else str(r.date),
                "occupancy": round(float(r.occupancy), 4),
                "rooms_sold": round(float(r.rooms_sold), 1),
                "confidence": round(float(r.confidence), 3),
            }
            for r in house.itertuples()
        ],
        "by_category": {
            cid: [
                {
                    "date": r.date.isoformat() if hasattr(r.date, "isoformat") else str(r.date),
                    "occupancy": round(float(r.occupancy), 4),
                    "lower": round(float(r.lower), 4),
                    "upper": round(float(r.upper), 4),
                }
                for r in df.itertuples()
            ]
            for cid, df in per_cat.items()
        },
        "model": next(iter(per_cat.values()))["source"].iloc[0] if per_cat else "none",
    }


@router.get("/assets")
def assets(db: Session = Depends(get_db)) -> dict[str, Any]:
    board = maintenance.health_board(db)
    orders = db.scalars(select(WorkOrder).order_by(WorkOrder.scheduled_for)).all()
    return {
        "assets": board,
        "work_orders": [
            {
                "id": w.id,
                "asset_id": w.asset_id,
                "scheduled_for": w.scheduled_for.isoformat(),
                "window_reason": w.window_reason,
                "priority": w.priority,
                "status": w.status,
            }
            for w in orders
        ],
    }


@router.get("/assets/{asset_id}/telemetry")
def telemetry(asset_id: str, points: int = Query(180, le=600), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "asset not found")
    a = maintenance.assess(db, asset, dt.date.today())
    if a is None:
        return {"asset": asset.name, "readings": []}
    df = a["series"].tail(points)
    return {
        "asset": {"id": asset.id, "name": asset.name, "kind": asset.kind, "location": asset.location},
        "risk": round(a["probability"], 3),
        "method": a["method"],
        "days_to_failure": a["days_to_failure"],
        "anomaly_rate": round(a["anomaly_rate"], 3),
        "readings": [
            {
                "ts": r.ts.isoformat(),
                "vibration_mm_s": round(float(r.vibration_mm_s), 3),
                "temperature_c": round(float(r.temperature_c), 2),
                "power_kw": round(float(r.power_kw), 2),
                "pressure_bar": round(float(r.pressure_bar), 3),
            }
            for r in df.itertuples()
        ],
    }


@router.get("/roster")
def roster(days: int = Query(7, ge=1, le=14), db: Session = Depends(get_db)) -> dict[str, Any]:
    today = dt.date.today()
    window = [today + dt.timedelta(days=i) for i in range(days)]
    req, ctx, cross = workforce.required_headcount(db, today, days)
    have = workforce.current_coverage(db, window)

    rows = []
    for (d, slot, role), need in sorted(req.items()):
        rows.append({
            "date": d.isoformat(), "slot": slot, "role": role,
            "required": need, "rostered": have.get((d, slot, role), 0),
            "occupancy": ctx["occupancy"].get(d.isoformat(), 0.0),
        })
    assignments = db.scalars(
        select(ShiftAssignment).where(ShiftAssignment.date.in_(window))
    ).all()
    return {
        "requirement": rows,
        "cross_domain_inputs": cross,
        "sentiment_lift": ctx["sentiment_lift"],
        "backlog_pressure": ctx["backlog_pressure"],
        "assignments": [
            {"id": a.id, "date": a.date.isoformat(), "slot": a.slot, "role": a.role,
             "staff_id": a.staff_id}
            for a in assignments
        ],
    }


@router.get("/inventory")
def inventory(db: Session = Depends(get_db)) -> dict[str, Any]:
    items = db.scalars(select(InventoryItem)).all()
    pos = db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()).limit(20)).all()
    return {
        "items": [
            {
                "id": i.id, "name": i.name, "unit": i.unit, "on_hand": i.on_hand,
                "par_level": i.par_level, "department": i.department,
                "below_par": i.on_hand < i.par_level, "unit_cost": i.unit_cost,
                "lead_time_days": i.lead_time_days,
            }
            for i in items
        ],
        "purchase_orders": [
            {"id": p.id, "item_id": p.item_id, "quantity": p.quantity,
             "total_cost": p.total_cost, "needed_by": p.needed_by.isoformat(), "status": p.status}
            for p in pos
        ],
    }


# --------------------------------------------------------------------------
# Guest intelligence
# --------------------------------------------------------------------------
@router.get("/guests")
def guests(limit: int = Query(20, le=100), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(
        select(Guest).order_by(Guest.lifetime_value.desc()).limit(limit)
    ).all()
    for g in rows:
        if not g.dna_summary:
            guest.build_guest_dna(db, g)
    db.commit()
    return {
        "guests": [
            {
                "id": g.id, "name": g.name, "tier": g.tier, "home_city": g.home_city,
                "stay_count": g.stay_count, "lifetime_value": g.lifetime_value,
                "dna_summary": g.dna_summary, "preferences": g.preferences,
                "dna_vector_preview": (g.dna_vector or [])[:8],
            }
            for g in rows
        ]
    }


@router.get("/sentiment")
def sentiment(db: Session = Depends(get_db)) -> dict[str, Any]:
    today = dt.date.today()
    guest.backfill_sentiment(db)
    db.commit()
    recent = db.scalars(
        select(Review).order_by(Review.posted_at.desc()).limit(30)
    ).all()
    return {
        "by_department": guest.department_sentiment(db, today),
        "staffing_lift_published": guest.department_sentiment_lift(db, today),
        "recent_reviews": [
            {
                "id": r.id, "source": r.source, "posted_at": r.posted_at.isoformat(),
                "rating": r.rating, "department": r.department,
                "sentiment": r.sentiment, "topics": r.topics, "text": r.text,
            }
            for r in recent
        ],
    }


class ConciergeQuery(BaseModel):
    question: str
    guest_id: int | None = None


@router.post("/concierge")
def concierge(body: ConciergeQuery, db: Session = Depends(get_db)) -> dict[str, Any]:
    result = guest.concierge_answer(db, body.guest_id, body.question)
    db.commit()
    return result


@router.get("/sops")
def sops(db: Session = Depends(get_db)) -> dict[str, Any]:
    docs = db.scalars(select(SopDocument)).all()
    return {
        "count": len(docs),
        "documents": [
            {"id": d.id, "title": d.title, "category": d.category,
             "indexed": d.embedding is not None}
            for d in docs
        ],
    }


# --------------------------------------------------------------------------
# Feedback loop + simulator
# --------------------------------------------------------------------------
@router.get("/learning")
def learning(db: Session = Depends(get_db)) -> dict[str, Any]:
    return orchestrator.learning_summary(db)


@router.post("/learning/observe")
def observe(db: Session = Depends(get_db)) -> dict[str, Any]:
    scored = orchestrator.observe_outcomes(db)
    return {"outcomes_scored": scored, "summary": orchestrator.learning_summary(db)}


@router.get("/decisions")
def decisions(limit: int = Query(50, le=200), db: Session = Depends(get_db)) -> dict[str, Any]:
    logs = db.scalars(select(DecisionLog).order_by(DecisionLog.created_at.desc()).limit(limit)).all()
    outcomes = {o.action_card_id: o for o in db.scalars(select(Outcome)).all()}
    return {
        "decisions": [
            {
                "id": d.id, "action_card_id": d.action_card_id, "engine": d.engine,
                "decision": d.decision, "predicted_impact_inr": d.predicted_impact_inr,
                "confidence": d.confidence_at_decision,
                "decided_by": d.decided_by,
                "at": d.created_at.isoformat() if d.created_at else None,
                "realised_impact_inr": (
                    outcomes[d.action_card_id].realised_impact_inr
                    if d.action_card_id in outcomes else None
                ),
            }
            for d in logs
        ]
    }


class SimulationRequest(BaseModel):
    rate_change_pct: float = Field(0.0, ge=-40, le=60)
    staffing_change_pct: float = Field(0.0, ge=-50, le=60)
    promotion_discount_pct: float = Field(0.0, ge=0, le=40)
    horizon_days: int = Field(14, ge=1, le=45)


@router.post("/simulate")
def simulate(body: SimulationRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return orchestrator.simulate(
        db,
        rate_change_pct=body.rate_change_pct,
        staffing_change_pct=body.staffing_change_pct,
        promotion_discount_pct=body.promotion_discount_pct,
        horizon_days=body.horizon_days,
    )


@router.get("/categories")
def categories(db: Session = Depends(get_db)) -> dict[str, Any]:
    cats = db.scalars(select(RoomCategory)).all()
    return {
        "categories": [
            {"id": c.id, "name": c.name, "room_count": c.room_count, "base_rate": c.base_rate}
            for c in cats
        ]
    }

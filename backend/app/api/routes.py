"""HTTP surface for the Next.js frontend."""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core import events, notify
from app.core.auth import (
    Principal,
    auth_enabled,
    current_principal,
    require_engine_permission,
    requires,
)
from app.core.clock import business_today, to_business
from app.core.config import settings
from app.core.db import get_db
from app.engines import bus, demand, guest, maintenance, orchestrator, workforce
from app.ingest import importers, readiness
from app.models import (
    ActionCard,
    Asset,
    Booking,
    DecisionLog,
    EngineRun,
    Guest,
    ImportBatch,
    InventoryItem,
    Notification,
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

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

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
        "decided_by_role": c.decided_by_role,
        "edited_payload": c.edited_payload,
        "was_edited": c.was_edited,
        "shadow": c.shadow,
        "reverted_at": c.reverted_at.isoformat() if c.reverted_at else None,
        "reverted_by": c.reverted_by,
        # The UI needs to know whether to offer Undo without guessing at the
        # window, and to say why when it cannot.
        "can_revert": bus.reversible(c)[0],
        "revert_blocked_reason": bus.reversible(c)[1],
        "editable_fields": list(bus.EDITABLE_FIELDS.get(c.kind, ())),
    }


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    """The one screen a manager opens at the start of a shift."""
    today = business_today()

    occ_rows = db.execute(
        select(
            func.sum(OccupancyDaily.rooms_sold),
            func.sum(OccupancyDaily.rooms_available),
            func.sum(OccupancyDaily.room_revenue),
            func.sum(OccupancyDaily.fnb_revenue),
        ).where(OccupancyDaily.date == today)
    ).one()
    sold, available, room_rev, fnb_rev = (x or 0 for x in occ_rows)

    request_counts = db.execute(select(
        ServiceRequest.department, func.count(),
        func.sum(case((ServiceRequest.priority == "high", 1), else_=0)),
    ).where(ServiceRequest.resolved_at.is_(None)).group_by(ServiceRequest.department)).all()

    health = maintenance.health_board(db, today)
    gaps = workforce.staffing_gaps(db, today, days=2)

    pending_count, critical_count, total_impact = db.execute(select(
        func.count(), func.sum(case((ActionCard.urgency == "critical", 1), else_=0)),
        func.sum(ActionCard.impact_inr),
    ).where(ActionCard.status == "pending")).one()
    ranked = db.scalars(select(ActionCard).where(ActionCard.status == "pending")
                        .order_by(*bus.rank_order()).limit(5)).all()

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
            "open": sum(n for _, n, _ in request_counts),
            "high_priority": sum(high for _, _, high in request_counts),
            "by_department": {dept: n for dept, n, _ in request_counts},
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
            "pending": pending_count,
            "critical": critical_count or 0,
            "total_impact_inr": round(total_impact or 0, 2),
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
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(ActionCard)
    if status != "all":
        stmt = stmt.where(ActionCard.status == status)
    if engine:
        stmt = stmt.where(ActionCard.engine == engine)
    ordering = bus.rank_order() if status == "pending" else (ActionCard.created_at.desc(), ActionCard.id.desc())
    ordered = list(db.scalars(stmt.order_by(*ordering).limit(limit)).all())
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
    """A manager's answer to a recommendation.

    `by` is deliberately absent: authorship comes from the verified principal,
    never from the request body. A self-declared approver is not an audit trail.
    """

    snooze_hours: int = Field(24, ge=1, le=720)
    # Approve-with-edits. Managers negotiate with recommendations rather than
    # accepting them whole; forcing "right direction, half the number" through
    # Dismiss taught Layer 4 that the engine was simply wrong.
    edits: dict[str, Any] | None = None
    # Dismiss reason. The most valuable feedback the product collects.
    reason: str = Field("", max_length=40)
    reason_note: str = Field("", max_length=2000)


@router.post("/actions/{card_id}/undo")
def undo(
    card_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(requires("action:undo")),
) -> dict[str, Any]:
    """Reverse an executed action inside the undo window.

    Without this, approving anything is a one-way door - which is exactly why a
    cautious manager never approves the first recommendation.
    """
    card = db.get(ActionCard, card_id)
    if card is None:
        raise HTTPException(404, "action card not found")
    require_engine_permission(principal, card.engine)
    try:
        bus.revert(db, card, principal.name, principal.role)
    except bus.NotReversible as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    events.nudge()
    db.refresh(card)
    return card_json(card)


@router.post("/actions/{card_id}/{decision}")
def decide(
    card_id: int,
    decision: Literal["approve", "snooze", "dismiss"],
    body: Decision | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """AI recommends, an authenticated human decides, the system executes."""
    card = db.get(ActionCard, card_id)
    if card is None:
        raise HTTPException(404, "action card not found")
    body = body or Decision()
    # Approving a rate change and approving capital maintenance are different
    # authorities; the card's own engine decides which one is needed.
    require_engine_permission(principal, card.engine)

    if body.reason and body.reason not in bus.DISMISS_REASONS:
        raise HTTPException(
            400,
            f"unknown reason '{body.reason}'; expected one of {sorted(bus.DISMISS_REASONS)}",
        )
    if decision == "dismiss" and not body.reason:
        raise HTTPException(
            400,
            "a dismissal needs a reason - it is what the feedback loop learns from. "
            f"Expected one of {sorted(bus.DISMISS_REASONS)}.",
        )

    try:
        if decision == "approve":
            bus.approve(db, card, principal.name, principal.role, edits=body.edits)
        elif decision == "snooze":
            bus.snooze(db, card, body.snooze_hours, principal.name, principal.role)
        else:
            bus.dismiss(
                db, card, principal.name, principal.role,
                reason=body.reason, reason_note=body.reason_note,
            )
    except bus.DecisionConflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    events.nudge()
    db.refresh(card)
    return card_json(card)


@router.get("/dismiss-reasons")
def dismiss_reasons() -> dict[str, Any]:
    """Drives the dismissal dropdown, so the UI and the loop cannot drift apart."""
    return {"reasons": [{"id": k, "label": v} for k, v in bus.DISMISS_REASONS.items()]}


@router.get("/actions-feed/history")
def action_history(limit: int = Query(40, ge=1, le=200), db: Session = Depends(get_db)) -> dict[str, Any]:
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
def run_engines(
    engine: str | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(requires("engines:run")),
) -> dict[str, Any]:
    if engine:
        if engine not in orchestrator.ENGINES:
            raise HTTPException(400, f"unknown engine '{engine}'")
        results = [orchestrator.run_engine(db, engine)]
    else:
        results = orchestrator.run_all(db)
    if any(result["ok"] for result in results):
        orchestrator.apply_learning(db)
    # New cards exist as of this commit; push them instead of making the
    # dashboard wait for the next poll.
    events.nudge()
    return {"results": results}


@router.get("/engines/status")
def engine_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    out = {}
    pending_counts = dict(db.execute(select(ActionCard.engine, func.count())
        .where(ActionCard.status == "pending").group_by(ActionCard.engine)).all())
    latest = select(EngineRun.id, func.row_number().over(
        partition_by=EngineRun.engine,
        order_by=(EngineRun.started_at.desc(), EngineRun.id.desc()),
    ).label("position")).subquery()
    last_runs = {run.engine: run for run in db.scalars(select(EngineRun)
        .join(latest, EngineRun.id == latest.c.id).where(latest.c.position == 1))}
    for name in orchestrator.ENGINES:
        last = last_runs.get(name)
        pending = pending_counts.get(name, 0)
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
    today = business_today()
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
def telemetry(asset_id: str, points: int = Query(180, ge=1, le=600), db: Session = Depends(get_db)) -> dict[str, Any]:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "asset not found")
    a = maintenance.assess(db, asset, business_today())
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
    today = business_today()
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
def guests(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)) -> dict[str, Any]:
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
    today = business_today()
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
    question: str = Field(min_length=1, max_length=4000)
    guest_id: int | None = Field(None, ge=1)


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
def decisions(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)) -> dict[str, Any]:
    logs = db.scalars(select(DecisionLog).order_by(DecisionLog.created_at.desc()).limit(limit)).all()
    outcomes = {o.action_card_id: o for o in db.scalars(
        select(Outcome).where(Outcome.action_card_id.in_([d.action_card_id for d in logs]))
    ).all()}
    return {
        "decisions": [
            {
                "id": d.id, "action_card_id": d.action_card_id, "engine": d.engine,
                "decision": d.decision, "predicted_impact_inr": d.predicted_impact_inr,
                "confidence": d.confidence_at_decision,
                "decided_by": d.decided_by,
                "decided_by_role": d.decided_by_role,
                "reason": d.reason,
                "reason_label": bus.DISMISS_REASONS.get(d.reason, ""),
                "reason_note": d.reason_note,
                "edits": d.edits,
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


# --------------------------------------------------------------------------
# Who am I - lets the frontend hide controls the caller may not use, rather
# than offering a button that will 403.
# --------------------------------------------------------------------------
@router.get("/me")
def me(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    return {
        "name": principal.name,
        "role": principal.role,
        "permissions": sorted(principal.permissions),
        "authenticated": not principal.is_anonymous,
        "auth_enabled": auth_enabled(),
        "shadow_mode": settings.shadow_mode,
        "undo_window_minutes": settings.undo_window_minutes,
        "timezone": settings.resort_timezone,
        "currency": settings.currency,
    }


# --------------------------------------------------------------------------
# Data ingestion - the supported path from a real PMS/POS/HRMS export into the
# spine. See app/ingest/importers.py for why this is CSV and not a connector.
# --------------------------------------------------------------------------
@router.get("/import/datasets")
def import_datasets() -> dict[str, Any]:
    return {
        "datasets": [
            {
                "name": d.name,
                "required": list(d.required),
                "optional": list(d.optional),
                "note": d.note,
            }
            for d in importers.DATASETS.values()
        ]
    }


@router.get("/import/{dataset}/template", response_class=PlainTextResponse)
def import_template(dataset: str) -> str:
    """A ready-to-fill header row, so nobody has to guess column names."""
    try:
        return importers.template_csv(dataset)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/import/{dataset}")
async def import_dataset(
    dataset: str,
    file: UploadFile = File(...),
    dry_run: bool = Query(False, description="Validate and report without writing"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(requires("data:import")),
) -> dict[str, Any]:
    """Load one CSV into the spine.

    A dry run is the default advice: an import that silently overwrites a year
    of occupancy history because a column was misnamed is not recoverable.
    """
    if dataset not in importers.DATASETS:
        raise HTTPException(404, f"unknown dataset '{dataset}'")
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        )
    if not raw.strip():
        raise HTTPException(400, "the uploaded file is empty")

    batch = importers.import_csv(
        db, dataset, raw,
        filename=file.filename or "",
        imported_by=principal.name,
        dry_run=dry_run,
    )
    # New source data invalidates fitted models; leaving the old ones cached
    # would show a forecast built on data the property has just replaced.
    if not dry_run and batch.rows_written:
        demand.clear_forecast_cache()
        maintenance.clear_assess_cache()
    db.commit()
    return {
        "id": batch.id,
        "dataset": batch.dataset,
        "filename": batch.filename,
        "dry_run": batch.dry_run,
        "rows_seen": batch.rows_seen,
        "rows_written": batch.rows_written,
        "rows_skipped": batch.rows_skipped,
        "ok": batch.ok,
        "errors": batch.errors,
        "imported_by": batch.imported_by,
    }


@router.get("/import/history")
def import_history(
    limit: int = Query(25, ge=1, le=100), db: Session = Depends(get_db)
) -> dict[str, Any]:
    batches = db.scalars(
        select(ImportBatch).order_by(ImportBatch.id.desc()).limit(limit)
    ).all()
    return {
        "batches": [
            {
                "id": b.id, "dataset": b.dataset, "filename": b.filename,
                "imported_by": b.imported_by, "dry_run": b.dry_run,
                "rows_seen": b.rows_seen, "rows_written": b.rows_written,
                "rows_skipped": b.rows_skipped, "ok": b.ok,
                "errors": b.errors[:10],
                "at": to_business(b.created_at).isoformat() if b.created_at else None,
            }
            for b in batches
        ]
    }


# --------------------------------------------------------------------------
# Cold start - what the models actually know yet.
# --------------------------------------------------------------------------
@router.get("/readiness")
def data_readiness(db: Session = Depends(get_db)) -> dict[str, Any]:
    return readiness.assess(db)


# --------------------------------------------------------------------------
# Notification outbox - proof that the loop reached a human.
# --------------------------------------------------------------------------
@router.get("/notifications")
def notifications(
    status: str = Query("all"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Notification)
    if status != "all":
        stmt = stmt.where(Notification.status == status)
    rows = db.scalars(stmt.order_by(Notification.id.desc()).limit(limit)).all()
    counts = dict(
        db.execute(
            select(Notification.status, func.count()).group_by(Notification.status)
        ).all()
    )
    return {
        "counts": {
            "queued": counts.get("queued", 0),
            "sent": counts.get("sent", 0),
            "failed": counts.get("failed", 0),
        },
        "channels_enabled": settings.notify_channel_list,
        "notifications": [
            {
                "id": n.id, "channel": n.channel, "recipient": n.recipient,
                "recipient_name": n.recipient_name, "subject": n.subject,
                "body": n.body, "kind": n.kind, "status": n.status,
                "attempts": n.attempts, "error": n.error,
                "action_card_id": n.action_card_id,
                "at": to_business(n.created_at).isoformat() if n.created_at else None,
                "sent_at": to_business(n.sent_at).isoformat() if n.sent_at else None,
            }
            for n in rows
        ],
    }


@router.post("/notifications/retry")
def retry_notifications(
    db: Session = Depends(get_db),
    principal: Principal = Depends(requires("engines:run")),
) -> dict[str, Any]:
    """Re-queue everything that gave up, then drain.

    A failed notification is an instruction a person never received, so it needs
    a hand-operated retry and not just a background loop.
    """
    failed = db.scalars(
        select(Notification).where(Notification.status == "failed")
    ).all()
    for row in failed:
        row.status, row.attempts = "queued", 0
    db.commit()
    return {"requeued": len(failed), **notify.flush_outbox(db, limit=100)}

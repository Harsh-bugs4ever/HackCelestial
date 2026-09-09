"""Layer 3 - the action bus.

No engine is allowed to return a chart. Every engine returns `Proposal`s, the
bus persists them as ActionCards, and approval routes through `execute()` which
writes a real artifact - a work order, a rate change, a roster row, a PO.

Layer 4 hangs off the same path: every decision is logged, and every executed
card is scored against what actually happened.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from app.models import ActionCard, DecisionLog, utcnow

# engines register their executor here; keeps the bus ignorant of engine internals
Executor = Callable[[Session, ActionCard], dict[str, Any]]
_EXECUTORS: dict[str, Executor] = {}


def executor(kind: str) -> Callable[[Executor], Executor]:
    def wrap(fn: Executor) -> Executor:
        _EXECUTORS[kind] = fn
        return fn

    return wrap


@dataclass
class Driver:
    """One explainable reason behind a recommendation (slide 4.4)."""

    label: str
    detail: str
    weight: float = 0.0  # relative contribution, 0..1

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "detail": self.detail, "weight": round(self.weight, 3)}


@dataclass
class Proposal:
    """What an engine emits. The bus turns this into an ActionCard."""

    engine: str
    kind: str
    title: str
    detail: str
    recommendation: str
    confidence: float
    impact_inr: float
    impact_kind: str = "revenue"
    impact_note: str = ""
    urgency: str = "normal"
    why: list[Driver] = field(default_factory=list)
    cross_domain: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str = ""

    def to_card(self) -> ActionCard:
        return ActionCard(
            engine=self.engine,
            kind=self.kind,
            title=self.title,
            detail=self.detail,
            recommendation=self.recommendation,
            confidence=round(float(self.confidence), 3),
            impact_inr=round(float(self.impact_inr), 2),
            impact_kind=self.impact_kind,
            impact_note=self.impact_note,
            urgency=self.urgency,
            why=[d.as_dict() for d in self.why],
            cross_domain=self.cross_domain,
            payload=self.payload,
            dedupe_key=self.dedupe_key or f"{self.engine}:{self.kind}:{self.title}",
        )


def publish(db: Session, proposals: list[Proposal]) -> list[ActionCard]:
    """Persist proposals, replacing any superseded pending card with the same key.

    Re-running an engine must refresh its open recommendations rather than pile
    duplicates onto the manager's queue. Decided cards are never touched - they
    are Layer 4 training data.
    """
    published: list[ActionCard] = []
    now = utcnow()
    # Deduplicate a batch before querying; keep the latest proposal for each key.
    incoming = {card.dedupe_key: card for card in (p.to_card() for p in proposals)}
    if not incoming:
        return []
    existing: dict[str, list[ActionCard]] = {}
    for old in db.scalars(select(ActionCard).where(
        ActionCard.dedupe_key.in_(incoming),
        ActionCard.status.in_(("pending", "snoozed")),
    )):
        existing.setdefault(old.dedupe_key, []).append(old)
    for key, card in incoming.items():
        stale = existing.get(key, [])
        if any(old.status == "snoozed" and old.snooze_until and old.snooze_until > now for old in stale):
            continue
        for old in stale:
            # Keep IDs referenced by decision logs, including expired snoozes.
            old.status = "superseded"
        db.add(card)
        published.append(card)
    db.flush()
    return published


def _log_decision(db: Session, card: ActionCard, decision: str, by: str) -> None:
    db.add(
        DecisionLog(
            action_card_id=card.id,
            engine=card.engine,
            decision=decision,
            predicted_impact_inr=card.impact_inr,
            confidence_at_decision=card.confidence,
            features={
                "kind": card.kind,
                "urgency": card.urgency,
                "impact_kind": card.impact_kind,
                "cross_domain": card.cross_domain,
                "drivers": [w.get("label") for w in card.why],
                "age_minutes": round((utcnow() - card.created_at).total_seconds() / 60, 1),
            },
            decided_by=by,
        )
    )


class DecisionConflict(ValueError):
    """Another decision has already changed this recommendation."""


def _claim(db: Session, card: ActionCard, status: str, by: str) -> bool:
    # Conditional UPDATE is atomic across threads and worker processes.
    result = db.execute(update(ActionCard).where(
        ActionCard.id == card.id,
        ActionCard.status.in_(("pending", "snoozed") if status != "snoozed" else ("pending",)),
    ).values(status=status, decided_at=utcnow(), decided_by=by,
             snooze_until=None).execution_options(synchronize_session=False))
    db.refresh(card)
    if result.rowcount:
        return True
    if card.status == status or (status == "approved" and card.status == "executed"):
        return False  # Retry of a completed decision: no second execution/log.
    raise DecisionConflict(f"This recommendation is already {card.status}. Refresh the action queue.")


def approve(db: Session, card: ActionCard, by: str = "manager") -> ActionCard:
    """Claim once, then isolate execution so failure cannot commit partial work."""
    if not _claim(db, card, "approved", by):
        return card
    _log_decision(db, card, "approved", by)
    fn = _EXECUTORS.get(card.kind)
    if fn is None:
        card.execution_result = {"ok": False, "error": f"no executor registered for '{card.kind}'"}
    else:
        try:
            with db.begin_nested():
                result = fn(db, card)
                if not result.get("ok"):
                    raise ValueError(result.get("error", "Executor did not confirm success"))
                db.flush()
            card.execution_result = result
            card.status = "executed"
            card.executed_at = utcnow()
        except Exception as exc:
            card.execution_result = {"ok": False, "error": str(exc)}
    db.flush()
    return card


def snooze(db: Session, card: ActionCard, hours: int = 24, by: str = "manager") -> ActionCard:
    if _claim(db, card, "snoozed", by):
        card.snooze_until = utcnow() + dt.timedelta(hours=hours)
        _log_decision(db, card, "snoozed", by)
        db.flush()
    return card


def dismiss(db: Session, card: ActionCard, by: str = "manager") -> ActionCard:
    if _claim(db, card, "dismissed", by):
        _log_decision(db, card, "dismissed", by)
        db.flush()
    return card


def wake_snoozed(db: Session) -> int:
    """Return expired snoozes to the queue."""
    now = utcnow()
    due = db.scalars(
        select(ActionCard).where(
            ActionCard.status == "snoozed", ActionCard.snooze_until <= now
        )
    ).all()
    for card in due:
        card.status = "pending"
        card.snooze_until = None
    db.flush()
    return len(due)


URGENCY_RANK = {"critical": 0, "high": 1, "normal": 2, "low": 3}


def rank(cards: list[ActionCard]) -> list[ActionCard]:
    """Queue order: urgency first, then confidence-weighted rupee impact."""
    return sorted(
        cards,
        key=lambda c: (URGENCY_RANK.get(c.urgency, 2), -(c.impact_inr * c.confidence)),
    )


def rank_order():
    """SQL equivalent of rank(), applied before LIMIT so urgent cards cannot vanish."""
    return (case(URGENCY_RANK, value=ActionCard.urgency, else_=2),
            (ActionCard.impact_inr * ActionCard.confidence).desc(), ActionCard.id.desc())

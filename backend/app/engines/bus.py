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

from sqlalchemy import select
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
    for p in proposals:
        card = p.to_card()
        stale = db.scalars(
            select(ActionCard).where(
                ActionCard.dedupe_key == card.dedupe_key,
                ActionCard.status.in_(("pending", "snoozed")),
            )
        ).all()
        for old in stale:
            if old.status == "snoozed" and old.snooze_until and old.snooze_until > now:
                # respect an active snooze - do not nag the manager again yet
                break
            db.delete(old)
        else:
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


def approve(db: Session, card: ActionCard, by: str = "manager") -> ActionCard:
    """AI recommends, human decides, system executes (slide 3, steps 5-6)."""
    if card.status in ("approved", "executed"):
        return card

    card.status = "approved"
    card.decided_at = utcnow()
    card.decided_by = by
    _log_decision(db, card, "approved", by)

    fn = _EXECUTORS.get(card.kind)
    if fn is None:
        card.execution_result = {"ok": False, "error": f"no executor registered for '{card.kind}'"}
    else:
        try:
            card.execution_result = fn(db, card)
            card.status = "executed"
            card.executed_at = utcnow()
        except Exception as exc:  # an engine bug must not lose the approval
            card.execution_result = {"ok": False, "error": str(exc)}
    db.flush()
    return card


def snooze(db: Session, card: ActionCard, hours: int = 24, by: str = "manager") -> ActionCard:
    card.status = "snoozed"
    card.decided_at = utcnow()
    card.decided_by = by
    card.snooze_until = utcnow() + dt.timedelta(hours=hours)
    _log_decision(db, card, "snoozed", by)
    db.flush()
    return card


def dismiss(db: Session, card: ActionCard, by: str = "manager") -> ActionCard:
    card.status = "dismissed"
    card.decided_at = utcnow()
    card.decided_by = by
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

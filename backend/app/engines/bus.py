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

from app.core import notify
from app.core.config import settings
from app.engines import messaging
from app.models import ActionCard, DecisionLog, utcnow

# Fields a manager may override per card kind. Anything outside this list is
# dropped by `_allowed_edits` - payload feeds the executor directly, so an
# open edit surface would be an execution vulnerability, not a feature.
EDITABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "rate_change": ("proposed_rate",),
    "work_order": ("scheduled_for",),
    "roster_change": ("assignments", "short_by"),
    "purchase_order": ("quantity", "total_cost", "needed_by"),
    "escalation": ("request_ids",),
    "offer": ("offer", "offer_value"),
}

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
    # Urgency used to be nothing but a sort key: a critical card raised at 02:00
    # waited for somebody to open a browser. Push it the moment it exists.
    for card in published:
        notify.queue(db, messaging.for_new_critical(card))
    return published


# Why a manager rejected a recommendation. This is the single most valuable
# signal the product collects: "stale_data" means the engine was wrong,
# "already_handled" means it was right but late, and "local_knowledge" means it
# could not have known. Collapsing all three into a bare dismissal - which is
# what the bus used to do - teaches Layer 4 the wrong lesson three ways.
DISMISS_REASONS = {
    "already_handled": "Already dealt with outside the system",
    "stale_data": "The underlying data is wrong or out of date",
    "local_knowledge": "Context the system does not have",
    "too_risky": "Right idea, unacceptable risk",
    "not_worth_it": "Impact does not justify the effort",
    "wrong_timing": "Right action, wrong moment",
    "other": "Other",
}

# Reasons that mean the engine itself misfired. Only these count against a
# model's confidence in `orchestrator.apply_learning`.
ENGINE_FAULT_REASONS = frozenset({"stale_data", "too_risky", "not_worth_it"})


def _log_decision(
    db: Session,
    card: ActionCard,
    decision: str,
    by: str,
    role: str = "",
    reason: str = "",
    reason_note: str = "",
    edits: dict[str, Any] | None = None,
) -> None:
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
                "edited": bool(edits),
            },
            decided_by=by,
            decided_by_role=role,
            reason=reason,
            reason_note=reason_note[:2000],
            edits=edits or {},
        )
    )


class DecisionConflict(ValueError):
    """Another decision has already changed this recommendation."""


class NotReversible(ValueError):
    """This action cannot be undone (too old, or no reverser registered)."""


def _claim(db: Session, card: ActionCard, status: str, by: str, role: str = "") -> bool:
    # Conditional UPDATE is atomic across threads and worker processes.
    result = db.execute(update(ActionCard).where(
        ActionCard.id == card.id,
        ActionCard.status.in_(("pending", "snoozed") if status != "snoozed" else ("pending",)),
    ).values(status=status, decided_at=utcnow(), decided_by=by, decided_by_role=role,
             snooze_until=None).execution_options(synchronize_session=False))
    db.refresh(card)
    if result.rowcount:
        return True
    if card.status == status or (status == "approved" and card.status == "executed"):
        return False  # Retry of a completed decision: no second execution/log.
    raise DecisionConflict(f"This recommendation is already {card.status}. Refresh the action queue.")


def _allowed_edits(card: ActionCard, edits: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only the fields this card kind says a manager may change.

    An edit surface that accepts arbitrary payload keys is an execution
    vulnerability, not a feature: `payload` is fed straight to the executor.
    """
    if not edits:
        return {}
    allowed = EDITABLE_FIELDS.get(card.kind, ())
    return {k: v for k, v in edits.items() if k in allowed and v is not None}


def approve(
    db: Session,
    card: ActionCard,
    by: str = "manager",
    role: str = "",
    edits: dict[str, Any] | None = None,
    shadow: bool | None = None,
) -> ActionCard:
    """Claim once, then isolate execution so failure cannot commit partial work.

    `edits` is the manager's counter-offer. Approving at a different number is
    agreement about direction, so it executes the edited value and records the
    delta, rather than forcing a dismissal that would teach Layer 4 that the
    engine was simply wrong.
    """
    if not _claim(db, card, "approved", by, role):
        return card
    accepted = _allowed_edits(card, edits)
    if accepted:
        card.edited_payload = accepted
    _log_decision(db, card, "approved", by, role, edits=accepted)

    in_shadow = settings.shadow_mode if shadow is None else shadow
    fn = _EXECUTORS.get(card.kind)
    if fn is None:
        card.execution_result = {"ok": False, "error": f"no executor registered for '{card.kind}'"}
    elif in_shadow:
        card.execution_result = _shadow_run(db, card, fn)
        card.shadow = True
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
            # The point of the whole pipeline: somebody is told to do something.
            # Queued, not sent - delivery must not be able to roll back the
            # artifact that was just written.
            notify.queue(db, messaging.for_execution(db, card, result))
        except Exception as exc:
            card.execution_result = {"ok": False, "error": str(exc)}
    db.flush()
    return card


def _shadow_run(db: Session, card: ActionCard, fn: Executor) -> dict[str, Any]:
    """Run the executor for real, then roll it back.

    A property evaluating the system wants to know what it *would* have done,
    through the same code path that would have done it - a hand-written preview
    drifts from the executor and quietly stops being true.
    """
    try:
        savepoint = db.begin_nested()
        try:
            result = fn(db, card)
            db.flush()
        finally:
            savepoint.rollback()
        return {
            **result,
            "ok": True,
            "shadow": True,
            "message": f"[shadow] {result.get('message', 'would have executed')}",
        }
    except Exception as exc:
        return {"ok": False, "shadow": True, "error": str(exc)}


def snooze(
    db: Session, card: ActionCard, hours: int = 24, by: str = "manager", role: str = "",
) -> ActionCard:
    if _claim(db, card, "snoozed", by, role):
        card.snooze_until = utcnow() + dt.timedelta(hours=hours)
        _log_decision(db, card, "snoozed", by, role)
        db.flush()
    return card


def dismiss(
    db: Session,
    card: ActionCard,
    by: str = "manager",
    role: str = "",
    reason: str = "",
    reason_note: str = "",
) -> ActionCard:
    if _claim(db, card, "dismissed", by, role):
        _log_decision(db, card, "dismissed", by, role, reason=reason, reason_note=reason_note)
        db.flush()
    return card


# --------------------------------------------------------------------------
# Undo. Trust is incremental: a manager who cannot reverse a mistake will not
# approve the first recommendation, let alone the hundredth.
# --------------------------------------------------------------------------
_REVERSERS: dict[str, Executor] = {}


def reverser(kind: str) -> Callable[[Executor], Executor]:
    def wrap(fn: Executor) -> Executor:
        _REVERSERS[kind] = fn
        return fn

    return wrap


def reversible(card: ActionCard, now: dt.datetime | None = None) -> tuple[bool, str]:
    """Can this card still be undone, and if not, why not."""
    if card.status != "executed" or card.executed_at is None:
        return False, "only executed actions can be reverted"
    if card.reverted_at is not None:
        return False, "already reverted"
    if card.kind not in _REVERSERS:
        return False, f"'{card.kind}' actions cannot be undone automatically"
    window = dt.timedelta(minutes=settings.undo_window_minutes)
    if (now or utcnow()) - card.executed_at > window:
        return False, f"the {settings.undo_window_minutes}-minute undo window has passed"
    return True, ""


def revert(db: Session, card: ActionCard, by: str = "manager", role: str = "") -> ActionCard:
    """Undo an executed action and the artifact it wrote."""
    ok, why = reversible(card)
    if not ok:
        raise NotReversible(why)
    fn = _REVERSERS[card.kind]
    with db.begin_nested():
        result = fn(db, card)
        if not result.get("ok"):
            raise NotReversible(result.get("error", "reversal did not confirm success"))
        db.flush()
    card.status = "reverted"
    card.reverted_at = utcnow()
    card.reverted_by = by
    card.execution_result = {**(card.execution_result or {}), "reverted": result}
    # Whoever received the original instruction has to hear that it is off.
    notify.queue(db, messaging.for_reversal(card, by))
    _log_decision(db, card, "reverted", by, role, reason="undo")
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

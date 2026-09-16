"""Who to tell when a card executes, and what to say.

Transport lives in ``app.core.notify``; this module owns the operational
question it cannot answer: *which human needs to know*. Keeping them apart
means a new channel (Teams, an in-house app) is a provider, and a new card kind
is a builder, without the two changing together.

The rule throughout: address a named person where one exists, and fall back to
the duty manager rather than dropping the message. An unroutable alert is the
same as no alert, and the failure is silent - which is worse.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import to_business
from app.core.config import settings
from app.core.notify import Message
from app.models import ActionCard, Asset, Guest, InventoryItem, Staff

log = logging.getLogger(__name__)

# Channels usable for a person, best first. Filtered by what ops enabled and by
# which address the person actually has.
_PERSON_PREFERENCE = ("whatsapp", "sms", "email", "webhook", "console")


def _money(value: float) -> str:
    return f"{settings.currency} {value:,.0f}"


def _when(moment: dt.datetime | dt.date) -> str:
    if isinstance(moment, dt.datetime):
        return f"{to_business(moment):%a %d %b, %H:%M}"
    return f"{moment:%a %d %b}"


def _channel_for(phone: str, email: str) -> tuple[str, str]:
    """Pick (channel, address) for a person given what ops has switched on."""
    enabled = settings.notify_channel_list
    for channel in _PERSON_PREFERENCE:
        if channel not in enabled:
            continue
        if channel in ("whatsapp", "sms") and phone:
            return channel, phone
        if channel == "email" and email:
            return channel, email
        if channel in ("webhook", "console"):
            return channel, email or phone or "ops"
    # Nothing enabled matched an address the person has: keep it in the outbox
    # on console rather than silently discarding it.
    return "console", email or phone or "unrouted"


def _to_person(
    name: str, phone: str, email: str, subject: str, body: str,
    kind: str, card_id: int | None,
) -> Message:
    channel, address = _channel_for(phone, email)
    return Message(
        channel=channel, recipient=address, recipient_name=name,
        subject=subject, body=body, kind=kind, action_card_id=card_id,
    )


def _duty_manager(subject: str, body: str, kind: str, card_id: int | None) -> list[Message]:
    """Fallback addressee for anything without a named owner."""
    if not (settings.duty_manager_phone or settings.duty_manager_email):
        return [Message("console", "duty-manager", subject, body, kind, "Duty Manager", card_id)]
    return [_to_person(
        "Duty Manager", settings.duty_manager_phone, settings.duty_manager_email,
        subject, body, kind, card_id,
    )]


# --------------------------------------------------------------------------
# Per-kind builders
# --------------------------------------------------------------------------
def _roster_change(db: Session, card: ActionCard, result: dict) -> list[Message]:
    payload = card.effective_payload
    role = payload.get("role", "team")
    day = payload.get("date", "")
    try:
        day_label = _when(dt.date.fromisoformat(day))
    except (ValueError, TypeError):
        day_label = day

    out: list[Message] = []
    assignments = payload.get("assignments", [])
    staff_ids = [a.get("staff_id") for a in assignments if a.get("staff_id")]
    people = {
        s.id: s for s in db.scalars(select(Staff).where(Staff.id.in_(staff_ids)))
    } if staff_ids else {}

    for assignment in assignments:
        person = people.get(assignment.get("staff_id"))
        if person is None:
            continue
        slot = assignment.get("slot", "shift")
        out.append(_to_person(
            person.name, person.phone, person.email,
            f"Shift added - {day_label}",
            (
                f"Hi {person.name.split()[0]}, you have been rostered for the {slot} "
                f"{role.replace('_', ' ')} shift on {day_label} at {settings.resort_name}. "
                "Reply to your supervisor if you cannot make it."
            ),
            "roster_change", card.id,
        ))

    unfilled = max(0, int(payload.get("short_by", 0)) - len(out))
    if unfilled:
        out += _duty_manager(
            f"{unfilled} {role.replace('_', ' ')} shift(s) still unfilled - {day_label}",
            (
                f"The optimizer could not name staff for {unfilled} {role.replace('_', ' ')} "
                f"slot(s) on {day_label}. Agency cover or a volunteer is needed."
            ),
            "roster_gap", card.id,
        )
    return out


def _work_order(db: Session, card: ActionCard, result: dict) -> list[Message]:
    payload = card.effective_payload
    asset = db.get(Asset, payload.get("asset_id")) if payload.get("asset_id") else None
    asset_name = asset.name if asset else payload.get("asset_id", "asset")
    location = getattr(asset, "location", "") or ""
    scheduled = result.get("scheduled_for", payload.get("scheduled_for", ""))
    try:
        when = _when(dt.datetime.fromisoformat(scheduled))
    except (ValueError, TypeError):
        when = str(scheduled)

    body = (
        f"Work order #{result.get('work_order_id', '?')} - {asset_name}"
        f"{f' ({location})' if location else ''}. Scheduled {when}. "
        f"{payload.get('window_reason', '')} "
        f"Reason: {card.detail[:180]}"
    ).strip()
    subject = f"{'URGENT - ' if card.urgency in ('critical', 'high') else ''}Work order: {asset_name}"

    # Route to maintenance technicians; anyone on the team can pick it up, and
    # a missed critical service is far costlier than a duplicated message.
    technicians = db.scalars(
        select(Staff).where(
            Staff.active.is_(True),
            Staff.role.in_(("maintenance", "engineering", "technician")),
        ).limit(5)
    ).all()
    out = [
        _to_person(t.name, t.phone, t.email, subject, body, "work_order", card.id)
        for t in technicians
    ]
    return out or _duty_manager(subject, body, "work_order", card.id)


def _purchase_order(db: Session, card: ActionCard, result: dict) -> list[Message]:
    payload = card.effective_payload
    item = db.get(InventoryItem, payload.get("item_id")) if payload.get("item_id") else None
    name = item.name if item else payload.get("item_id", "item")
    supplier = (item.supplier if item else "") or "the supplier"
    quantity = result.get("quantity", payload.get("quantity", "?"))
    needed_by = payload.get("needed_by", "n/a")
    ref = result.get("purchase_order_id", "?")
    body = (
        f"PO #{ref} raised with {supplier}: {quantity} x {name}, "
        f"{_money(float(payload.get('total_cost', 0)))}. Needed by {needed_by}."
    )
    out = _duty_manager(f"Purchase order raised - {name}", body, "purchase_order", card.id)

    # The order itself, to the party who has to fulfil it.
    if item is not None and (item.supplier_email or item.supplier_phone):
        out.append(_to_person(
            supplier, item.supplier_phone, item.supplier_email,
            f"Purchase order #{ref} - {settings.resort_name}",
            (
                f"Please supply {quantity} {item.unit} of {name} to "
                f"{settings.resort_name} by {needed_by}. "
                f"Our reference: PO #{ref}. Agreed value {_money(float(payload.get('total_cost', 0)))}."
            ),
            "purchase_order_supplier", card.id,
        ))
    return out


def _escalation(db: Session, card: ActionCard, result: dict) -> list[Message]:
    dept = (card.effective_payload.get("department") or "operations").replace("_", " ").title()
    body = (
        f"{result.get('requests_escalated', 0)} open {dept} request(s) escalated. "
        f"{card.detail[:200]}"
    )
    return _duty_manager(f"Escalation - {dept}", body, "escalation", card.id)


def _offer(db: Session, card: ActionCard, result: dict) -> list[Message]:
    payload = card.effective_payload
    guest = db.get(Guest, payload.get("guest_id")) if payload.get("guest_id") else None
    if guest is None:
        return []
    # The in-stay chat message is written by the executor; this reaches the
    # guest on a channel they will actually look at before check-out.
    return [_to_person(
        guest.name, guest.phone, guest.email,
        f"A little something from {settings.resort_name}",
        result.get("message_sent", payload.get("offer", "")),
        "offer", card.id,
    )]


def _rate_change(db: Session, card: ActionCard, result: dict) -> list[Message]:
    # Report the rate that was applied, not the one that was recommended - they
    # differ whenever the manager counter-offered, and the recommendation text
    # is written before that decision exists.
    applied = result.get("new_rate")
    previous = result.get("previous_rate")
    category = result.get("category", "")
    if applied is not None and previous is not None:
        change = (
            f"{category} moved from {_money(float(previous))} to {_money(float(applied))} "
            f"for {card.effective_payload.get('date', 'the selected date')}."
        )
    else:
        change = card.recommendation[:200]
    body = (
        f"{change} Expected impact {_money(card.impact_inr)}."
        + (
            f" Adjusted by {card.decided_by or 'the manager'} from the "
            f"recommended {_money(float(card.payload.get('proposed_rate', 0)))}."
            if card.was_edited
            else ""
        )
    )
    return _duty_manager(f"Rate change applied - {category or card.title[:60]}", body,
                         "rate_change", card.id)


BUILDERS = {
    "roster_change": _roster_change,
    "work_order": _work_order,
    "purchase_order": _purchase_order,
    "escalation": _escalation,
    "offer": _offer,
    "rate_change": _rate_change,
}


def for_execution(db: Session, card: ActionCard, result: dict) -> list[Message]:
    """Messages owed to real people because this card just executed."""
    builder = BUILDERS.get(card.kind)
    if builder is None:
        return []
    try:
        return builder(db, card, result or {})
    except Exception as exc:
        # Never let message construction break an execution that already
        # changed the roster or raised a work order.
        log.warning("notification build failed for card %s: %s", card.id, exc)
        return []


def for_new_critical(card: ActionCard) -> list[Message]:
    """Push a critical recommendation the moment it appears.

    Urgency levels were previously only a sort key: a critical card raised at
    02:00 waited for someone to open a browser. This is what makes the level
    mean something.
    """
    if card.urgency != "critical":
        return []
    return _duty_manager(
        f"CRITICAL: {card.title[:80]}",
        (
            f"{card.detail[:240]} Recommended: {card.recommendation[:160]} "
            f"Impact {_money(card.impact_inr)}. Approve in Smart Resort 360."
        ),
        "critical_card", card.id,
    )


def for_reversal(card: ActionCard, by: str) -> list[Message]:
    """Tell people a decision they were notified about has been undone."""
    return _duty_manager(
        f"Reverted: {card.title[:80]}",
        f"{by} reverted this action. Any instruction sent earlier for card #{card.id} no longer applies.",
        "reverted", card.id,
    )

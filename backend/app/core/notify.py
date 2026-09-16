"""Outbound message transport - the last mile of the action loop.

Approving a card used to end at an INSERT. The three housekeepers who had just
been rostered, the technician who had to service the chiller, and the supplier
receiving the purchase order all learned nothing; the manual coordination the
platform claimed to remove had simply moved to the manager's phone.

Design notes:

* **Queue first, send later.** ``queue()`` writes rows inside the caller's
  transaction and sends nothing. A drainer picks them up afterwards. An SMTP
  timeout must never roll back an approval that already changed the roster,
  and a message must survive a process restart between decision and delivery.
* **Providers degrade, never raise.** A missing Twilio credential downgrades
  that channel to console and logs once. The demo has to run on a laptop with
  no accounts configured, and an ops tool that crashes because a webhook is
  down is worse than one that keeps a visible backlog.
* **Every attempt is persisted.** "Nobody told me" is a real operational
  dispute; the outbox is the answer to it.
"""
from __future__ import annotations

import base64
import json
import logging
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Notification, utcnow

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(message, *args)


@dataclass(frozen=True)
class Message:
    channel: str            # console | email | sms | whatsapp | webhook
    recipient: str
    subject: str
    body: str
    kind: str = ""
    recipient_name: str = ""
    action_card_id: int | None = None


# --------------------------------------------------------------------------
# Providers. Each returns None on success or a failure reason.
# --------------------------------------------------------------------------
def _send_console(msg: Message) -> str | None:
    log.info(
        "[notify:%s] to=%s (%s) :: %s :: %s",
        msg.channel, msg.recipient, msg.recipient_name or "-", msg.subject, msg.body,
    )
    return None


def _send_email(msg: Message) -> str | None:
    if not settings.smtp_host:
        _warn_once("smtp", "SMTP not configured; email notifications log to console")
        return _send_console(msg)
    mail = EmailMessage()
    mail["From"] = settings.notify_from_email
    mail["To"] = msg.recipient
    mail["Subject"] = msg.subject or settings.app_name
    mail.set_content(msg.body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(mail)
    except (OSError, smtplib.SMTPException) as exc:
        return f"smtp: {exc}"
    return None


def _twilio_send(to: str, body: str, sender: str) -> str | None:
    if not (settings.twilio_account_sid and settings.twilio_auth_token and sender):
        _warn_once("twilio", "Twilio not configured; SMS/WhatsApp log to console")
        return _send_console(Message("console", to, "", body))
    url = (
        "https://api.twilio.com/2010-04-01/Accounts/"
        f"{urllib.parse.quote(settings.twilio_account_sid)}/Messages.json"
    )
    data = urllib.parse.urlencode({"To": to, "From": sender, "Body": body}).encode()
    request = urllib.request.Request(url, data=data)
    token = f"{settings.twilio_account_sid}:{settings.twilio_auth_token}"
    request.add_header(
        "Authorization", "Basic " + base64.b64encode(token.encode()).decode()
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            if resp.status >= 300:
                return f"twilio: HTTP {resp.status}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return f"twilio: {exc}"
    return None


def _send_sms(msg: Message) -> str | None:
    return _twilio_send(msg.recipient, msg.body, settings.twilio_from_number)


def _send_whatsapp(msg: Message) -> str | None:
    to = msg.recipient if msg.recipient.startswith("whatsapp:") else f"whatsapp:{msg.recipient}"
    return _twilio_send(to, msg.body, settings.twilio_whatsapp_from)


def _send_webhook(msg: Message) -> str | None:
    """Generic JSON POST - the escape hatch for Slack, Teams, or an in-house app."""
    if not settings.notify_webhook_url:
        _warn_once("webhook", "No webhook URL configured; webhook messages log to console")
        return _send_console(msg)
    payload = json.dumps({
        "text": f"*{msg.subject}*\n{msg.body}",
        "subject": msg.subject,
        "body": msg.body,
        "kind": msg.kind,
        "recipient": msg.recipient,
        "recipient_name": msg.recipient_name,
        "action_card_id": msg.action_card_id,
    }).encode()
    request = urllib.request.Request(
        settings.notify_webhook_url, data=payload,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            if resp.status >= 300:
                return f"webhook: HTTP {resp.status}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return f"webhook: {exc}"
    return None


PROVIDERS = {
    "console": _send_console,
    "email": _send_email,
    "sms": _send_sms,
    "whatsapp": _send_whatsapp,
    "webhook": _send_webhook,
}


# --------------------------------------------------------------------------
# Outbox
# --------------------------------------------------------------------------
def queue(db: Session, messages: list[Message]) -> list[Notification]:
    """Persist messages for delivery. Writes only - never sends inline."""
    rows = [
        Notification(
            channel=m.channel if m.channel in PROVIDERS else "console",
            recipient=m.recipient[:160],
            recipient_name=m.recipient_name[:120],
            subject=m.subject[:200],
            body=m.body,
            kind=m.kind[:40],
            action_card_id=m.action_card_id,
            status="queued",
        )
        for m in messages
        if m.recipient
    ]
    for row in rows:
        db.add(row)
    if rows:
        db.flush()
    return rows


def flush_outbox(db: Session, limit: int = 25) -> dict[str, int]:
    """Attempt delivery of queued messages.

    Returns counts rather than raising: the caller is a background loop whose
    job is to keep running.
    """
    pending = db.scalars(
        select(Notification)
        .where(Notification.status == "queued")
        .order_by(Notification.id)
        .limit(limit)
    ).all()
    sent = failed = 0
    for row in pending:
        provider = PROVIDERS.get(row.channel, _send_console)
        row.attempts += 1
        try:
            error = provider(Message(
                channel=row.channel, recipient=row.recipient, subject=row.subject,
                body=row.body, kind=row.kind, recipient_name=row.recipient_name,
                action_card_id=row.action_card_id,
            ))
        except Exception as exc:                      # a provider bug is not fatal
            error = f"{type(exc).__name__}: {exc}"
        if error is None:
            row.status, row.sent_at, row.error = "sent", utcnow(), ""
            sent += 1
        else:
            row.error = error[:2000]
            # Give up only after retries, so a brief outage does not lose the
            # message and a permanent one does not spin forever.
            row.status = "failed" if row.attempts >= MAX_ATTEMPTS else "queued"
            failed += 1
    if pending:
        db.commit()
    return {"attempted": len(pending), "sent": sent, "failed": failed}

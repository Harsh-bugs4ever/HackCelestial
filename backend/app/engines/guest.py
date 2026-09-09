"""Engine 4 - Guest Intelligence.

Input:  guest profile, past stays, reviews, in-stay chat
Output: offers, live sentiment, escalation alerts, concierge answers

sentence-transformers embeds reviews and SOPs; FAISS indexes the SOP corpus for
the RAG concierge; a lexicon-calibrated classifier scores sentiment per review.
The department sentiment slide is published to the workforce engine, which is
the third cross-domain link on slide 4.2.

Every heavy dependency here is optional at runtime - if the embedding model or
the LLM key is missing, the engine degrades to deterministic scoring rather than
failing, because a demo must never die on a cold cache.
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import logging
import math
import re
import urllib.error
import urllib.request

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.engines.bus import Driver, Proposal, executor
from app.models import ActionCard, ChatMessage, Guest, Review, ServiceRequest, SopDocument, utcnow

log = logging.getLogger(__name__)

ENGINE = "guest"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# --------------------------------------------------------------------------
# Sentiment
# --------------------------------------------------------------------------
NEGATIVE = {
    "not": 0.6, "never": 0.9, "no": 0.5, "slow": 1.0, "late": 1.0, "delay": 1.1,
    "delayed": 1.1, "cold": 0.9, "dirty": 1.4, "unclean": 1.4, "rude": 1.5,
    "poor": 1.2, "bad": 1.2, "worst": 1.8, "terrible": 1.8, "awful": 1.7,
    "broken": 1.3, "leaking": 1.1, "skipped": 1.3, "missing": 1.1, "wrong": 1.2,
    "queue": 0.8, "waited": 1.0, "waiting": 1.0, "understaffed": 1.4,
    "barely": 1.0, "struggled": 1.1, "inconsistent": 1.0, "discrepancy": 1.1,
    "duplicate": 0.9, "hair": 1.0, "ran out": 1.2, "still": 0.4,
}
POSITIVE = {
    "great": 1.2, "excellent": 1.5, "outstanding": 1.7, "spotless": 1.6,
    "quick": 1.1, "thorough": 1.2, "best": 1.5, "beautiful": 1.3, "worth": 1.1,
    "lovely": 1.2, "perfect": 1.5, "amazing": 1.5, "fixed": 1.0, "upgraded": 1.2,
    "remembered": 1.3, "accommodated": 1.2, "without any fuss": 1.3, "will return": 1.6,
}


def score_sentiment(text: str, rating: float | None = None) -> float:
    """Lexicon score in -1..1, anchored by the star rating when present."""
    t = text.lower()
    neg = sum(w for k, w in NEGATIVE.items() if k in t)
    pos = sum(w for k, w in POSITIVE.items() if k in t)
    raw = (pos - neg) / max(pos + neg, 1.0)
    lexical = float(np.clip(raw, -1.0, 1.0))
    if rating is None:
        return round(lexical, 3)
    star = (rating - 3.0) / 2.0  # 1..5 -> -1..1
    return round(float(np.clip(0.45 * lexical + 0.55 * star, -1.0, 1.0)), 3)


def backfill_sentiment(db: Session) -> int:
    """Score every review once at ingest so the dashboard is instant."""
    rows = db.scalars(select(Review).where(Review.sentiment == 0.0)).all()
    for r in rows:
        r.sentiment = score_sentiment(r.text, r.rating)
        r.topics = extract_topics(r.text)
    db.flush()
    return len(rows)


TOPIC_PATTERNS = {
    "cleanliness": r"clean|dirty|spotless|hair|dust",
    "speed": r"slow|late|delay|waited|queue|minutes|quick",
    "staff_attitude": r"rude|friendly|helpful|remembered|polite",
    "food_quality": r"food|meal|breakfast|buffet|dish|seafood|curry",
    "room_comfort": r"ac |air.?condition|hot water|geyser|bed|pillow|noise",
    "value": r"price|expensive|worth|value|charge|billing",
    "staffing_levels": r"understaffed|only one|no one|short.?staff",
}


def extract_topics(text: str) -> list[str]:
    t = text.lower()
    return [name for name, pat in TOPIC_PATTERNS.items() if re.search(pat, t)]


# --------------------------------------------------------------------------
# Department sentiment - the signal the workforce engine consumes
# --------------------------------------------------------------------------
def department_sentiment(db: Session, today: dt.date, window: int = 10, baseline: int = 90) -> dict[str, dict]:
    """Recent vs baseline sentiment per department."""
    recent_since = dt.datetime.combine(today - dt.timedelta(days=window), dt.time.min)
    base_since = dt.datetime.combine(today - dt.timedelta(days=baseline), dt.time.min)

    rows = db.execute(select(Review.department, Review.sentiment, Review.text,
                             Review.rating, Review.posted_at)
                      .where(Review.posted_at >= base_since)).all()
    if not rows:
        return {}

    by_dept: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        bucket = by_dept.setdefault(r.department, {"recent": [], "baseline": []})
        s = r.sentiment if r.sentiment else score_sentiment(r.text, r.rating)
        if r.posted_at >= recent_since:
            bucket["recent"].append(s)
        else:
            bucket["baseline"].append(s)

    out = {}
    for dept, b in by_dept.items():
        if len(b["recent"]) < 3:
            continue
        recent = float(np.mean(b["recent"]))
        base = float(np.mean(b["baseline"])) if b["baseline"] else recent
        out[dept] = {
            "recent": round(recent, 3),
            "baseline": round(base, 3),
            "delta": round(recent - base, 3),
            "recent_count": len(b["recent"]),
            "negative_share": round(sum(1 for s in b["recent"] if s < -0.2) / len(b["recent"]), 3),
        }
    return out


def department_sentiment_lift(db: Session, today: dt.date | None = None) -> dict[str, float]:
    """Staffing multiplier per role driven by a sentiment slide.

    A department whose sentiment has dropped materially gets more people on the
    next roster. This is the function the workforce engine calls.
    """
    today = today or dt.date.today()
    lifts: dict[str, float] = {}
    for dept, s in department_sentiment(db, today).items():
        if s["delta"] < -0.15 and s["negative_share"] > 0.35:
            # a 0.5 sentiment drop justifies roughly a 25% staffing lift, capped
            lifts[dept] = round(min(0.35, abs(s["delta"]) * 0.5 + s["negative_share"] * 0.15), 3)
    return lifts


# --------------------------------------------------------------------------
# Embeddings, Guest DNA and the FAISS-backed SOP index
# --------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _embedder():
    """Load MiniLM once. Returns None if unavailable - callers must handle it."""
    try:
        from sentence_transformers import SentenceTransformer

        log.info("loading embedding model %s", EMBED_MODEL)
        return SentenceTransformer(EMBED_MODEL)
    except Exception as exc:
        log.warning("sentence-transformers unavailable (%s); using hashed bag-of-words", exc)
        return None


def embed(texts: list[str]) -> np.ndarray:
    model = _embedder()
    if model is not None:
        return np.asarray(model.encode(texts, normalize_embeddings=True), dtype="float32")
    return _hashed_embedding(texts)


def _hashed_embedding(texts: list[str], dim: int = 256) -> np.ndarray:
    """Deterministic fallback so retrieval still works with no model downloaded."""
    out = np.zeros((len(texts), dim), dtype="float32")
    for i, t in enumerate(texts):
        for tok in re.findall(r"[a-z']+", t.lower()):
            out[i, hash(tok) % dim] += 1.0
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.clip(norms, 1e-6, None)


class SopIndex:
    """FAISS index over the SOP corpus - the concierge's grounding."""

    def __init__(self) -> None:
        self.ids: list[int] = []
        self.docs: list[SopDocument] = []
        self._index = None
        self._matrix: np.ndarray | None = None

    def build(self, db: Session) -> "SopIndex":
        self.docs = list(db.scalars(select(SopDocument)).all())
        if not self.docs:
            return self
        vecs = embed([f"{d.title}. {d.text}" for d in self.docs])
        self._matrix = vecs
        for d, v in zip(self.docs, vecs):
            d.embedding = [round(float(x), 5) for x in v[:32]]  # keep a peek in the DB
        try:
            import faiss

            index = faiss.IndexFlatIP(vecs.shape[1])
            index.add(vecs)
            self._index = index
        except Exception as exc:
            log.warning("FAISS unavailable (%s); using numpy cosine search", exc)
        return self

    def search(self, query: str, k: int = 3) -> list[tuple[SopDocument, float]]:
        if not self.docs or self._matrix is None:
            return []
        q = embed([query])
        if self._index is not None:
            scores, idx = self._index.search(q, min(k, len(self.docs)))
            return [(self.docs[i], float(s)) for i, s in zip(idx[0], scores[0]) if i >= 0]
        sims = (self._matrix @ q[0])
        order = np.argsort(-sims)[:k]
        return [(self.docs[i], float(sims[i])) for i in order]


_SOP_INDEX: SopIndex | None = None


def sop_index(db: Session, rebuild: bool = False) -> SopIndex:
    global _SOP_INDEX
    if _SOP_INDEX is None or rebuild:
        _SOP_INDEX = SopIndex().build(db)
    return _SOP_INDEX


def build_guest_dna(db: Session, guest: Guest) -> Guest:
    """Rolling embedding + readable profile from stays, spend and review text."""
    reviews = [r.text for r in guest.reviews][-8:]
    prefs = guest.preferences or {}
    parts = [
        f"{guest.tier} tier guest from {guest.home_city}",
        f"{guest.stay_count} previous stays",
        f"prefers {prefs.get('room_preference', 'no stated preference')}",
        f"dietary {prefs.get('dietary', 'none')}",
        f"favourite amenity {prefs.get('favourite_amenity', 'unknown')}",
        *reviews,
    ]
    blob = ". ".join(parts)
    vec = embed([blob])[0]
    guest.dna_vector = [round(float(x), 5) for x in vec[:48]]

    bits = []
    if guest.stay_count >= 3:
        bits.append(f"repeat guest ({guest.stay_count} stays)")
    if guest.lifetime_value > 0:
        bits.append(f"lifetime value INR {guest.lifetime_value:,.0f}")
    if prefs.get("favourite_amenity"):
        bits.append(f"usually books the {prefs['favourite_amenity']}")
    if prefs.get("dietary", "none") != "none":
        bits.append(f"{prefs['dietary']} diet")
    if prefs.get("travels_with_kids"):
        bits.append("travels with children")
    if prefs.get("spa_user"):
        bits.append("spa user")
    guest.dna_summary = "; ".join(bits) or "new guest, no history yet"
    return guest


def refresh_all_dna(db: Session, limit: int = 200) -> int:
    guests = db.scalars(select(Guest).order_by(Guest.lifetime_value.desc()).limit(limit)).all()
    for g in guests:
        build_guest_dna(db, g)
    db.flush()
    return len(guests)


# --------------------------------------------------------------------------
# Concierge
# --------------------------------------------------------------------------
def _call_claude(system: str, user: str) -> str | None:
    """Single Messages API call. Returns None if no key or the call fails."""
    if not settings.anthropic_api_key:
        return None
    body = json.dumps({
        "model": settings.llm_model,
        "max_tokens": 400,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        return "".join(b.get("text", "") for b in data.get("content", []))
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        log.warning("concierge LLM call failed (%s); using grounded extractive answer", exc)
        return None


def concierge_answer(db: Session, guest_id: int | None, question: str) -> dict:
    """RAG over the SOP corpus, personalised by Guest DNA."""
    idx = sop_index(db)
    hits = idx.search(question, k=3)
    context = "\n\n".join(f"[{d.title}]\n{d.text}" for d, _ in hits)

    guest = db.get(Guest, guest_id) if guest_id else None
    if guest is not None and not guest.dna_summary:
        build_guest_dna(db, guest)
    profile = (
        f"{guest.name}, {guest.tier} tier. {guest.dna_summary}." if guest else "Walk-in guest, no profile."
    )

    system = (
        f"You are the concierge at {settings.resort_name}. Answer only from the resort "
        "policy extracts provided. Be warm, specific and brief - three sentences at most. "
        "Quote exact times and prices when the extracts contain them. If the extracts do "
        "not cover the question, say you will check with the duty manager. Personalise "
        "using the guest profile when it is genuinely relevant, never as flattery."
    )
    user = f"Guest profile: {profile}\n\nResort policy extracts:\n{context}\n\nGuest asks: {question}"

    answer = _call_claude(system, user)
    source = "claude"
    if answer is None:
        # grounded extractive fallback - still cites the SOP, never invents
        if hits:
            doc, score = hits[0]
            answer = f"{doc.text.split('. ')[0]}."
            if len(hits) > 1 and score < 0.55:
                answer += " Let me confirm the details with the duty manager."
        else:
            answer = "Let me check that with the duty manager and come straight back to you."
        source = "extractive_fallback"

    if guest is not None:
        now = utcnow()
        db.add(ChatMessage(guest_id=guest.id, ts=now, role="guest", text=question,
                           sentiment=score_sentiment(question)))
        db.add(ChatMessage(guest_id=guest.id, ts=now, role="concierge", text=answer))
        db.flush()

    return {
        "answer": answer,
        "source": source,
        "grounded_in": [{"title": d.title, "score": round(s, 3)} for d, s in hits],
        "guest": {"id": guest.id, "name": guest.name, "dna": guest.dna_summary} if guest else None,
    }


# --------------------------------------------------------------------------
def _offer_for(guest: Guest) -> tuple[str, float, str]:
    """Personalised upsell from the DNA profile. Returns (offer, value, basis)."""
    prefs = guest.preferences or {}
    amenity = prefs.get("favourite_amenity", "in-room dining")
    catalogue = {
        "poolside cabana": ("Reserved poolside cabana at 16:00 on arrival day", 3500.0),
        "spa": ("60-minute signature spa treatment at 20% member rate", 4200.0),
        "sunset cruise": ("Two seats on the 17:30 sunset catamaran", 4800.0),
        "in-room dining": ("Chef's tasting in-room dinner for two", 2900.0),
        "gym": ("Personal training session plus recovery suite access", 1800.0),
    }
    offer, value = catalogue.get(amenity, catalogue["in-room dining"])
    tier_mult = {"platinum": 1.35, "gold": 1.2, "silver": 1.05, "standard": 1.0}[guest.tier]
    basis = (
        f"{guest.stay_count} previous stays, {amenity} booked on prior visits, "
        f"{guest.tier} tier"
    )
    return offer, value * tier_mult, basis


def run(db: Session, today: dt.date | None = None) -> list[Proposal]:
    today = today or dt.date.today()
    backfill_sentiment(db)
    proposals: list[Proposal] = []

    # 1. Sentiment escalations - the department-level alarm
    for dept, s in department_sentiment(db, today).items():
        if s["delta"] >= -0.15 or s["negative_share"] <= 0.35:
            continue
        affected = s["recent_count"]
        # every unhappy reviewer costs future bookings; conservative recovery value
        exposure = affected * 8500 * s["negative_share"]
        proposals.append(Proposal(
            engine=ENGINE,
            kind="escalation",
            title=f"{dept.replace('_', ' ').title()} sentiment down {abs(s['delta']) * 100:.0f} points in 10 days",
            detail=(
                f"{affected} reviews mentioning {dept.replace('_', ' ')} in the last 10 days, "
                f"{s['negative_share'] * 100:.0f}% negative. Sentiment {s['recent']:+.2f} "
                f"against a 90-day baseline of {s['baseline']:+.2f}."
            ),
            recommendation=(
                f"Brief the {dept.replace('_', ' ')} duty manager today, and approve the "
                f"linked staffing increase - the workforce engine has already raised the "
                f"{dept.replace('_', ' ')} requirement in response to this signal."
            ),
            confidence=round(min(0.9, 0.55 + affected / 40), 3),
            impact_inr=exposure,
            impact_kind="cost_avoided",
            impact_note=f"{affected} at-risk guests, repeat-booking exposure",
            urgency="high" if s["negative_share"] > 0.6 else "normal",
            why=[
                Driver("Negative share", f"{s['negative_share'] * 100:.0f}% of recent {dept} reviews negative", 0.45),
                Driver("Sentiment drop", f"{s['recent']:+.2f} vs {s['baseline']:+.2f} baseline", 0.35),
                Driver("Volume", f"{affected} reviews in the last 10 days", 0.2),
            ],
            cross_domain=["workforce"],
            payload={"department": dept, **s},
            dedupe_key=f"guest:sentiment:{dept}",
        ))

    # 2. Unresolved high-priority requests that are ageing
    now = utcnow()
    stale = [
        r for r in db.scalars(
            select(ServiceRequest).where(ServiceRequest.resolved_at.is_(None))
        ).all()
        if (now - r.opened_at).total_seconds() > 3600 * 2
    ]
    if stale:
        worst = sorted(stale, key=lambda r: r.opened_at)[:5]
        oldest_h = (now - worst[0].opened_at).total_seconds() / 3600
        proposals.append(Proposal(
            engine=ENGINE,
            kind="escalation",
            title=f"{len(stale)} guest requests open past the 30-minute SLA",
            detail=(
                f"Oldest has been open {oldest_h:.1f} hours ({worst[0].department}: "
                f"{worst[0].summary}). The escalation matrix requires duty-manager "
                f"ownership beyond 30 minutes."
            ),
            recommendation=(
                f"Assign a duty manager to the {len(worst)} oldest open requests now and "
                "close them out before the evening check-in wave."
            ),
            confidence=0.88,
            impact_inr=len(stale) * 4200.0,
            impact_kind="cost_avoided",
            impact_note="guest recovery cost and review risk avoided",
            urgency="high" if oldest_h > 6 else "normal",
            why=[
                Driver("SLA breach", f"{len(stale)} requests past 30 minutes", 0.5),
                Driver("Oldest open", f"{oldest_h:.1f} hours - {worst[0].summary}", 0.3),
                Driver("Department mix", ", ".join(sorted({r.department for r in worst})), 0.2),
            ],
            cross_domain=[],
            payload={"request_ids": [r.id for r in worst]},
            dedupe_key="guest:sla_breach",
        ))

    # 3. Personalised offers for high-value in-house guests
    top = db.scalars(
        select(Guest).where(Guest.stay_count >= 3).order_by(Guest.lifetime_value.desc()).limit(3)
    ).all()
    for g in top:
        if not g.dna_summary:
            build_guest_dna(db, g)
        offer, value, basis = _offer_for(g)
        proposals.append(Proposal(
            engine=ENGINE,
            kind="offer",
            title=f"Personalised offer for {g.name} ({g.tier} tier)",
            detail=f"Guest DNA: {g.dna_summary}.",
            recommendation=f"Offer: {offer}. Send via the in-stay chat before arrival.",
            confidence=0.71,
            impact_inr=value,
            impact_kind="revenue",
            impact_note=f"expected incremental spend, {g.tier} conversion rate applied",
            urgency="low",
            why=[
                Driver("Stay history", f"{g.stay_count} previous stays", 0.4),
                Driver("Preference match", basis, 0.35),
                Driver("Lifetime value", f"INR {g.lifetime_value:,.0f} to date", 0.25),
            ],
            cross_domain=[],
            payload={"guest_id": g.id, "offer": offer, "value": round(value, 2)},
            dedupe_key=f"guest:offer:{g.id}",
        ))

    return proposals


@executor("escalation")
def execute_escalation(db: Session, card: ActionCard) -> dict:
    ids = card.payload.get("request_ids", [])
    touched = []
    for rid in ids:
        r = db.get(ServiceRequest, rid)
        if r is not None:
            r.priority = "high"
            touched.append(r.summary)
    db.flush()
    dept = card.payload.get("department")
    return {
        "ok": True,
        "artifact": "escalation",
        "department": dept,
        "requests_escalated": len(touched),
        "message": (
            f"{len(touched)} requests escalated to the duty manager."
            if touched
            else f"{(dept or 'Department').replace('_', ' ').title()} escalation logged "
                 "and routed to the duty manager."
        ),
        "escalated_at": utcnow().isoformat(),
    }


@executor("offer")
def execute_offer(db: Session, card: ActionCard) -> dict:
    guest = db.get(Guest, card.payload["guest_id"])
    if guest is None:
        raise ValueError(f"unknown guest {card.payload['guest_id']}")
    msg = (
        f"Welcome back {guest.name.split()[0]} - we have held {card.payload['offer'].lower()} "
        "for you. Shall we confirm it?"
    )
    db.add(ChatMessage(guest_id=guest.id, ts=utcnow(), role="concierge", text=msg))
    db.flush()
    return {
        "ok": True,
        "artifact": "offer",
        "guest": guest.name,
        "channel": "in_stay_chat",
        "message_sent": msg,
        "message": f"Offer sent to {guest.name} via in-stay chat.",
    }

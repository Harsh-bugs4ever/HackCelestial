"""FastAPI application - the Node-free real-time gateway for the dashboard."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.api.routes import card_json, router
from app.core import events, notify
from app.core.auth import auth_enabled
from app.core.clock import business_today
from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.core.keepalive import ping_forever
from app.engines import bus, maintenance, workforce
from app.models import ActionCard

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("resort360")


class Hub:
    """Fan-out for live dashboard updates over WebSockets."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def join(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.clients.add(ws)

    async def leave(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        message = json.dumps(payload)
        async def send(ws: WebSocket) -> None:
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=2)
            except Exception:
                await self.leave(ws)
        await asyncio.gather(*(send(ws) for ws in tuple(self.clients)))


hub = Hub()


def _pending_snapshot(seen: set[int]) -> tuple[set[int], list[dict]]:
    # The session lives and closes entirely in the worker thread, never over an await.
    with SessionLocal() as db:
        if bus.wake_snoozed(db):
            db.commit()
        current = set(db.scalars(select(ActionCard.id).where(ActionCard.status == "pending")))
        fresh = []
        if current - seen:
            fresh = [card_json(c) for c in db.scalars(select(ActionCard)
                     .where(ActionCard.id.in_(current - seen)))]
        return current, fresh


# Safety-net interval. Writers nudge the watcher the moment they commit, so
# this only has to catch changes made by another process (a Celery worker, a
# second API instance) or a nudge lost to a shutdown race.
IDLE_POLL_SECONDS = 20


async def _watch_actions() -> None:
    """Publish additions and removals without blocking HTTP or socket heartbeats."""
    wake = asyncio.Event()
    events.bind(asyncio.get_running_loop(), wake)
    seen: set[int] = set()
    first_pass = True
    try:
        while True:
            try:
                current, fresh = await asyncio.to_thread(_pending_snapshot, seen)
                if current != seen and not first_pass:
                    await hub.broadcast({"type": "actions.new", "cards": fresh,
                                         "pending_total": len(current)})
                seen, first_pass = current, False
            except Exception as exc:
                log.warning("action watcher: %s", exc)
            # Return immediately when a writer signals, otherwise doze.
            wake.clear()
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=IDLE_POLL_SECONDS)
    finally:
        events.unbind()


async def _drain_outbox() -> None:
    """Deliver queued notifications.

    Kept out of the request path deliberately: an SMTP timeout must not roll
    back a roster change that has already been written, and a message must
    survive a restart between the decision and the send.
    """
    while True:
        try:
            result = await asyncio.to_thread(_flush_once)
            if result.get("sent") or result.get("failed"):
                log.info("outbox: %s", result)
        except Exception as exc:
            log.warning("outbox drain: %s", exc)
        await asyncio.sleep(10)


def _flush_once() -> dict:
    with SessionLocal() as db:
        return notify.flush_outbox(db)


async def _warm_caches() -> None:
    """Pay the cold model cost at boot, not on the first page load.

    An asset assessment fits an IsolationForest and the roster needs a Prophet
    fit; cold, both can take several seconds. Both engines cache results for
    five minutes and share in-flight computations with arriving requests.
    Runs in a worker thread so it never blocks the event loop or startup.
    """
    def work() -> None:
        db = SessionLocal()
        try:
            maintenance.health_board(db)
            workforce.staffing_gaps(db, days=2)
        finally:
            db.close()

    try:
        await asyncio.to_thread(work)
        log.info("engine caches warm - dashboard will answer immediately")
    except Exception as exc:
        # a cold cache only costs latency, never correctness
        log.warning("cache warm-up skipped: %s", exc)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    db = SessionLocal()
    try:
        count = db.scalar(select(func.count()).select_from(ActionCard)) or 0
        log.info(
            "%s ready - %d action cards in the bus, operating day %s (%s)",
            settings.app_name, count, business_today(), settings.resort_timezone,
        )
        if settings.shadow_mode:
            log.warning(
                "SHADOW MODE: approvals will be recorded and previewed but will "
                "not write artifacts."
            )
    finally:
        db.close()
    tasks = [
        asyncio.create_task(_watch_actions()),
        asyncio.create_task(_warm_caches()),
        asyncio.create_task(_drain_outbox()),
    ]
    # Only runs where a public URL is configured - see app/core/keepalive.py.
    if target := settings.keepalive_target:
        tasks.append(asyncio.create_task(
            ping_forever(target, settings.keepalive_interval_seconds)))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_origin_regex=r"https://.*\.(vercel|netlify)\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "app": settings.app_name,
        "resort": settings.resort_name,
        "database": "sqlite" if settings.is_sqlite else "postgres",
        "timescale": settings.timescale_enabled,
        "llm": "configured" if settings.anthropic_api_key else "offline_fallback",
        "keepalive": "on" if settings.keepalive_target else "off",
        "auth": "enabled" if auth_enabled() else "DISABLED",
        "shadow_mode": settings.shadow_mode,
        "timezone": settings.resort_timezone,
        "operating_day": business_today().isoformat(),
        "notify_channels": settings.notify_channel_list,
    }


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await hub.join(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "hello", "resort": settings.resort_name}))
        while True:
            # the client only needs to keep the socket warm
            await websocket.receive_text()
    except WebSocketDisconnect:
        await hub.leave(websocket)
    except Exception:
        await hub.leave(websocket)

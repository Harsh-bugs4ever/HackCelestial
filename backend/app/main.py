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
from app.core.config import settings
from app.core.db import SessionLocal, init_db
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


async def _watch_actions() -> None:
    """Publish additions and removals without blocking HTTP or socket heartbeats."""
    seen: set[int] = set()
    first_pass = True
    while True:
        try:
            current, fresh = await asyncio.to_thread(_pending_snapshot, seen)
            if current != seen and not first_pass:
                await hub.broadcast({"type": "actions.new", "cards": fresh,
                                     "pending_total": len(current)})
            seen, first_pass = current, False
        except Exception as exc:
            log.warning("action watcher: %s", exc)
        await asyncio.sleep(4)


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
        log.info("%s ready - %d action cards in the bus", settings.app_name, count)
    finally:
        db.close()
    task = asyncio.create_task(_watch_actions())
    warm = asyncio.create_task(_warm_caches())
    try:
        yield
    finally:
        for t in (task, warm):
            t.cancel()
        for t in (task, warm):
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

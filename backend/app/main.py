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
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.leave(ws)


hub = Hub()


async def _watch_actions() -> None:
    """Push new pending cards to connected dashboards.

    Polling the action table keeps the gateway independent of the broker - the
    dashboard stays live whether Celery is running or an engine was triggered
    by hand from the UI.
    """
    seen: set[int] = set()
    first_pass = True
    while True:
        try:
            db = SessionLocal()
            try:
                cards = db.scalars(
                    select(ActionCard).where(ActionCard.status == "pending")
                ).all()
                current = {c.id for c in cards}
                new = current - seen
                if new and not first_pass:
                    fresh = [c for c in cards if c.id in new]
                    await hub.broadcast({
                        "type": "actions.new",
                        "cards": [card_json(c) for c in fresh],
                        "pending_total": len(current),
                    })
                seen = current
                first_pass = False
            finally:
                db.close()
        except Exception as exc:
            log.warning("action watcher: %s", exc)
        await asyncio.sleep(4)


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
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_origin_regex=r"https://.*\.vercel\.app",
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

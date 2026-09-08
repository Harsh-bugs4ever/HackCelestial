"""Celery + Redis scheduled jobs.

"The engines keep reading, whether or not anyone is watching" (slide 3, step 3)
is only true if something runs them on a schedule. If Redis is unreachable the
app falls back to eager mode so a `.delay()` still executes inline - the demo
never depends on a broker being up.
"""
from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

log = logging.getLogger(__name__)


def _broker_reachable(url: str) -> bool:
    try:
        import redis

        redis.Redis.from_url(url, socket_connect_timeout=1.5).ping()
        return True
    except Exception as exc:
        log.warning("Redis unreachable at %s (%s) - Celery running eager", url, exc)
        return False


BROKER_UP = _broker_reachable(settings.redis_url)

celery_app = Celery(
    "resort360",
    broker=settings.redis_url if BROKER_UP else None,
    backend=settings.redis_url if BROKER_UP else None,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_always_eager=not BROKER_UP,
    task_eager_propagates=True,
    worker_max_tasks_per_child=40,       # Prophet leaks; recycle workers
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "demand-hourly": {
        "task": "app.workers.tasks.run_engine",
        "schedule": crontab(minute=5),
        "args": ("demand",),
    },
    "maintenance-every-15-min": {
        "task": "app.workers.tasks.run_engine",
        "schedule": crontab(minute="*/15"),
        "args": ("maintenance",),
    },
    "guest-every-10-min": {
        "task": "app.workers.tasks.run_engine",
        "schedule": crontab(minute="*/10"),
        "args": ("guest",),
    },
    "workforce-twice-daily": {
        "task": "app.workers.tasks.run_engine",
        "schedule": crontab(hour="6,15", minute=20),
        "args": ("workforce",),
    },
    "score-outcomes-nightly": {
        "task": "app.workers.tasks.score_outcomes",
        "schedule": crontab(hour=2, minute=0),
    },
    "wake-snoozed-cards": {
        "task": "app.workers.tasks.wake_snoozed",
        "schedule": crontab(minute="*/5"),
    },
}

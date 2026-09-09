"""Scheduled engine work."""
from __future__ import annotations

import logging

from app.core.db import SessionLocal
from app.engines import bus, orchestrator
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.run_engine")
def run_engine(name: str) -> dict:
    db = SessionLocal()
    try:
        result = orchestrator.run_engine(db, name)
        if result["ok"]:
            orchestrator.apply_learning(db)
        return result
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.run_all_engines")
def run_all_engines() -> list[dict]:
    db = SessionLocal()
    try:
        results = orchestrator.run_all(db)
        if any(result["ok"] for result in results):
            orchestrator.apply_learning(db)
        return results
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.score_outcomes")
def score_outcomes() -> dict:
    db = SessionLocal()
    try:
        return {"scored": orchestrator.observe_outcomes(db)}
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.wake_snoozed")
def wake_snoozed() -> dict:
    db = SessionLocal()
    try:
        n = bus.wake_snoozed(db)
        db.commit()
        return {"woken": n}
    finally:
        db.close()

"""Regression checks; all database writes use disposable SQLite databases.
Run: python -m unittest discover -s tests -v (from backend).
"""
import asyncio
import datetime as dt
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.api import routes
from app.core.cache import ModelCache
from app.core.db import Base, get_db
from app.engines import bus, demand, maintenance, orchestrator, workforce
from app.models import ActionCard, DecisionLog, EngineRun, RoomCategory, ShiftAssignment, utcnow


class CacheTests(unittest.TestCase):
    def test_concurrent_misses_compute_once(self):
        cache = ModelCache(2, 30)
        barrier = threading.Barrier(8)
        calls = []
        def compute():
            calls.append(1)
            time.sleep(.05)
            return 42
        def request(_):
            barrier.wait()
            return cache.get('forecast', compute)
        with ThreadPoolExecutor(8) as pool:
            self.assertEqual(list(pool.map(request, range(8))), [42] * 8)
        self.assertEqual(len(calls), 1)

    def test_expiry_eviction_none_and_retry(self):
        now = [0]
        cache = ModelCache(2, 10, lambda: now[0])
        self.assertIsNone(cache.get('a', lambda: None))
        self.assertIsNone(cache.get('a', lambda: 99))
        cache.get('b', lambda: 2)
        cache.get('c', lambda: 3)
        self.assertEqual(cache.get('a', lambda: 4), 4)
        now[0] = 11
        self.assertEqual(cache.get('a', lambda: 5), 5)
        with self.assertRaises(ValueError):
            cache.get('failure', lambda: (_ for _ in ()).throw(ValueError('failed')))
        self.assertEqual(cache.get('failure', lambda: 6), 6)

    def test_clear_during_computation_does_not_restore_old_value(self):
        cache = ModelCache(2, 10)
        entered, release = threading.Event(), threading.Event()
        def compute():
            entered.set()
            release.wait(2)
            return 'old'
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(cache.get, 'key', compute)
            self.assertTrue(entered.wait(2))
            cache.clear()
            self.assertEqual(cache.get('key', lambda: 'new'), 'new')
            release.set()
            self.assertEqual(pending.result(2), 'old')
        self.assertEqual(cache.get('key', lambda: 'wrong'), 'new')


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'test.db'}",
                                    connect_args={'check_same_thread': False})
        @event.listens_for(self.engine, 'connect')
        def pragmas(conn, _):
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA journal_mode=WAL')
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def card(self, db, **overrides):
        fields = dict(engine='demand', kind='test', title='Test', detail='Details',
                      recommendation='Test action', confidence=.8, impact_inr=100,
                      dedupe_key='test-key', created_at=utcnow())
        fields.update(overrides)
        card = ActionCard(**fields)
        db.add(card)
        db.commit()
        return card

    def test_forecast_cache_reuses_horizon_and_returns_copies(self):
        import pandas as pd
        demand.clear_forecast_cache()
        with self.sessions() as db:
            db.add(RoomCategory(id='room', name='Room', room_count=10, base_rate=100))
            db.commit()
            def forecast(db, cat, today, horizon):
                return pd.DataFrame({'occupancy': [.5] * horizon})
            with patch.object(demand, 'forecast_category', side_effect=forecast) as model:
                short = demand.forecast_all(db, horizon=7)
                short['room'].loc[0, 'occupancy'] = 99
                full = demand.forecast_all(db, horizon=30)
                self.assertEqual(model.call_count, 1)
                self.assertEqual(len(full['room']), 30)
                self.assertEqual(full['room'].iloc[0]['occupancy'], .5)
        demand.clear_forecast_cache()

    def test_model_cache_is_scoped_to_database(self):
        demand.clear_forecast_cache()
        with self.sessions() as db:
            db.add(RoomCategory(id='room', name='Room', room_count=10, base_rate=100))
            db.commit()
            with patch.object(demand, 'forecast_category', return_value=__import__('pandas').DataFrame({'occupancy':[.5]})):
                self.assertIn('room', demand.forecast_all(db))
        empty = create_engine('sqlite://')
        Base.metadata.create_all(empty)
        try:
            with sessionmaker(empty)() as other:
                self.assertEqual(demand.forecast_all(other), {})
        finally:
            empty.dispose()
            demand.clear_forecast_cache()

    def test_expired_snooze_wakes_without_celery(self):
        from app.main import _pending_snapshot
        with self.sessions() as db:
            card = self.card(db)
            bus.snooze(db, card)
            card.snooze_until = utcnow()-dt.timedelta(hours=1)
            db.commit()
            card_id = card.id
        with patch('app.main.SessionLocal', self.sessions):
            current, fresh = _pending_snapshot(set())
        self.assertEqual(current, {card_id})
        self.assertEqual(fresh[0]['status'], 'pending')

    def test_rank_before_limit(self):
        with self.sessions() as db:
            urgent = self.card(db, urgency='critical', created_at=utcnow()-dt.timedelta(days=1))
            self.card(db, urgency='normal', dedupe_key='new')
            result = routes.list_actions('pending', None, 1, db)
            self.assertEqual(result['cards'][0]['id'], urgent.id)

    def test_concurrent_approvals_execute_once(self):
        with self.sessions() as db:
            card_id = self.card(db).id
        barrier = threading.Barrier(2)
        def execute(db, card):
            db.add(EngineRun(engine='artifact', started_at=utcnow()))
            db.flush()
            return {'ok': True}
        def approve(_):
            with self.sessions() as db:
                card = db.get(ActionCard, card_id)
                barrier.wait()
                bus.approve(db, card)
                db.commit()
                return card.status
        with patch.dict(bus._EXECUTORS, {'test': execute}), ThreadPoolExecutor(2) as pool:
            self.assertEqual(list(pool.map(approve, range(2))), ['executed'] * 2)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(EngineRun)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(DecisionLog)), 1)
            with self.assertRaises(bus.DecisionConflict):
                bus.dismiss(db, db.get(ActionCard, card_id))

    def test_failed_executor_rolls_back_partial_artifacts(self):
        def execute(db, card):
            db.add(EngineRun(engine='partial', started_at=utcnow()))
            db.flush()
            raise ValueError('execution failed')
        with self.sessions() as db, patch.dict(bus._EXECUTORS, {'test': execute}):
            card = self.card(db)
            bus.approve(db, card)
            db.commit()
            self.assertEqual(card.status, 'approved')
            self.assertFalse(card.execution_result['ok'])
            self.assertEqual(db.scalar(select(func.count()).select_from(EngineRun)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(DecisionLog)), 1)

    def test_database_error_does_not_break_approval_transaction(self):
        def execute(db, card):
            db.add(EngineRun(engine=None, started_at=utcnow()))
            db.flush()
        with self.sessions() as db, patch.dict(bus._EXECUTORS, {'test': execute}):
            card = self.card(db)
            bus.approve(db, card)
            db.commit()
            self.assertEqual(card.status, 'approved')
            self.assertFalse(card.execution_result['ok'])
            self.assertEqual(db.scalar(select(func.count()).select_from(DecisionLog)), 1)

    def test_republish_preserves_snooze_logs_and_deduplicates_batch(self):
        with self.sessions() as db:
            old = self.card(db)
            bus.snooze(db, old)
            db.commit()
            proposal = bus.Proposal('demand','test','Test','Details','Do it',.8,100,dedupe_key='test-key')
            self.assertEqual(bus.publish(db, [proposal]), [])
            old.snooze_until = utcnow()-dt.timedelta(hours=1)
            db.commit()
            fresh = bus.publish(db, [proposal, proposal])
            db.commit()
            self.assertEqual(len(fresh), 1)
            self.assertEqual(old.status, 'superseded')
            self.assertEqual(db.scalar(select(func.count()).select_from(DecisionLog)), 1)

    def test_model_computation_does_not_hold_sqlite_writer(self):
        def run(db, today):
            with self.sessions() as other:
                other.add(EngineRun(engine='independent-write', started_at=utcnow()))
                other.commit()
            return []
        with self.sessions() as db, patch.dict(orchestrator.ENGINES, {'demand': SimpleNamespace(run=run)}):
            result = orchestrator.run_engine(db, 'demand')
            self.assertTrue(result['ok'])
            log = db.scalar(select(EngineRun).where(EngineRun.engine=='demand'))
            self.assertIsNotNone(log.finished_at)

    def test_busy_run_does_not_change_learning_confidence(self):
        with self.sessions() as db, patch.object(orchestrator, 'apply_learning') as learning:
            lock = orchestrator._RUN_LOCKS['demand']
            lock.acquire()
            try:
                result = routes.run_engines('demand', db)
                self.assertTrue(result['results'][0]['busy'])
                learning.assert_not_called()
            finally:
                lock.release()

    def test_overlapping_engine_run_returns_busy(self):
        with self.sessions() as db:
            lock = orchestrator._RUN_LOCKS['demand']
            lock.acquire()
            try:
                self.assertTrue(orchestrator.run_engine(db, 'demand')['busy'])
            finally:
                lock.release()

    def test_unassigned_roster_slots_do_not_count_as_staff(self):
        today = dt.date.today()
        with self.sessions() as db:
            db.add(ShiftAssignment(date=today, slot='morning', role='fnb', staff_id=None))
            db.commit()
            self.assertEqual(workforce.current_coverage(db, [today]), {})

    def test_invalid_limits_rejected(self):
        app = FastAPI()
        app.include_router(routes.router, prefix='/api')
        def database():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = database
        with TestClient(app) as client:
            for route in ('actions', 'actions-feed/history', 'guests', 'decisions'):
                self.assertEqual(client.get(f'/api/{route}?limit=-1').status_code, 422)
            self.assertEqual(client.post('/api/concierge', json={'question':''}).status_code, 422)


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_socket_does_not_delay_fast_socket(self):
        from app.main import Hub
        fast_sent = asyncio.Event()
        class Socket:
            def __init__(self, slow=False): self.slow = slow
            async def send_text(self, text):
                if self.slow: await asyncio.sleep(.2)
                else: fast_sent.set()
        hub = Hub()
        hub.clients = {Socket(True), Socket()}
        task = asyncio.create_task(hub.broadcast({'type':'test'}))
        await asyncio.wait_for(fast_sent.wait(), .1)
        await task


class KeepAliveTests(unittest.TestCase):
    """The self-ping must be dormant locally and self-configuring on Render."""

    def settings(self, **kwargs):
        from app.core.config import Settings
        # env_file=None keeps a developer's local .env out of the assertions.
        return Settings(_env_file=None, **kwargs)

    def test_dormant_without_a_public_url(self):
        self.assertEqual(self.settings().keepalive_target, '')

    def test_render_injected_url_gets_the_health_path(self):
        target = self.settings(render_external_url='https://api.onrender.com/').keepalive_target
        self.assertEqual(target, 'https://api.onrender.com/health')

    def test_explicit_url_wins_and_keeps_its_path(self):
        target = self.settings(keepalive_url='https://a.example/ping',
                               render_external_url='https://b.example').keepalive_target
        self.assertEqual(target, 'https://a.example/ping')

    def test_disabled_flag_silences_a_configured_url(self):
        target = self.settings(render_external_url='https://api.onrender.com',
                               keepalive_enabled=False).keepalive_target
        self.assertEqual(target, '')


class KeepAlivePingTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_ping_does_not_kill_the_loop(self):
        from app.core import keepalive
        calls = []
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def get(self, url, headers=None):
                calls.append(url)
                if len(calls) == 1:
                    raise RuntimeError('connection reset')
                return SimpleNamespace(status_code=200)
        with patch('httpx.AsyncClient', lambda **kw: Client()), \
                patch.object(keepalive.random, 'uniform', lambda *a: 0):
            task = asyncio.create_task(keepalive.ping_forever('https://x.example/health', 0))
            for _ in range(200):
                await asyncio.sleep(0)
                if len(calls) >= 2:
                    break
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        # The second ping only happens if the first exception was swallowed.
        self.assertGreaterEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()

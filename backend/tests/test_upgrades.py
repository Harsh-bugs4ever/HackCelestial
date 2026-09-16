"""Regression checks for the real-world gaps closed in this change.

Same conventions as test_backend.py: disposable SQLite per test, no network,
no reliance on the seeded database.
"""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.core import notify
from app.core.auth import PERMISSIONS, ROLES, Principal, _parse_users, principal_for
from app.engines import bus, orchestrator
from app.ingest import importers, readiness
from app.models import (
    ActionCard,
    Base,
    DecisionLog,
    InventoryItem,
    Notification,
    OccupancyDaily,
    RoomCategory,
    Staff,
    utcnow,
)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.tmp.name) / 'test.db'}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def pragmas(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def card(self, db, **overrides):
        fields = dict(
            engine="demand", kind="rate_change", title="Test", detail="Details",
            recommendation="Test action", confidence=0.8, impact_inr=100,
            dedupe_key="test-key", created_at=utcnow(),
            payload={"category_id": "std", "proposed_rate": 200.0, "date": "2026-01-01"},
        )
        fields.update(overrides)
        card = ActionCard(**fields)
        db.add(card)
        db.commit()
        return card


# --------------------------------------------------------------------------
class AuthTests(unittest.TestCase):
    def test_every_role_grants_only_known_permissions(self):
        for role, granted in ROLES.items():
            for permission in granted:
                self.assertIn(permission, PERMISSIONS, f"{role} grants unknown {permission}")

    def test_malformed_entries_are_skipped_not_fatal(self):
        users = _parse_users("good:Real Person:gm,,broken,bad:Name:not_a_role")
        self.assertEqual([p.name for p in users.values()], ["Real Person"])

    def test_unknown_token_is_rejected_when_auth_is_configured(self):
        with patch.dict(
            "app.core.auth._USERS",
            {"secret": Principal("GM", "gm", frozenset(PERMISSIONS))},
            clear=True,
        ):
            self.assertIsNone(principal_for("wrong"))
            self.assertIsNone(principal_for(None))
            self.assertEqual(principal_for("secret").role, "gm")

    def test_open_mode_grants_an_obviously_named_principal(self):
        with patch.dict("app.core.auth._USERS", {}, clear=True):
            principal = principal_for(None)
            # The audit row must not be mistakable for a real approval.
            self.assertIn("unauthenticated", principal.name.lower())
            self.assertTrue(principal.is_anonymous)


# --------------------------------------------------------------------------
class EditTests(Fixture):
    def test_only_whitelisted_fields_are_accepted(self):
        with self.sessions() as db:
            card = self.card(db)
            accepted = bus._allowed_edits(
                card,
                {"proposed_rate": 150, "category_id": "hacked", "__class__": "nope"},
            )
        # payload feeds the executor directly, so an open edit surface would be
        # an execution vulnerability rather than a feature.
        self.assertEqual(accepted, {"proposed_rate": 150})

    def test_effective_payload_prefers_the_edit(self):
        with self.sessions() as db:
            card = self.card(db, edited_payload={"proposed_rate": 150.0})
            self.assertEqual(card.effective_payload["proposed_rate"], 150.0)
            self.assertEqual(card.effective_payload["category_id"], "std")
            self.assertTrue(card.was_edited)

    def test_edited_approval_executes_the_managers_number(self):
        with self.sessions() as db:
            db.add(RoomCategory(id="std", name="Standard", room_count=10, base_rate=100.0))
            db.commit()
            card = self.card(db)
            bus.approve(db, card, "Priya", "revenue_manager", edits={"proposed_rate": 150.0})
            db.commit()
            self.assertEqual(card.status, "executed")
            self.assertEqual(db.get(RoomCategory, "std").base_rate, 150.0)
            log = db.scalars(select(DecisionLog)).one()
            self.assertEqual(log.edits, {"proposed_rate": 150.0})
            self.assertEqual(log.decided_by_role, "revenue_manager")


# --------------------------------------------------------------------------
class UndoTests(Fixture):
    def _executed(self, db):
        db.add(RoomCategory(id="std", name="Standard", room_count=10, base_rate=100.0))
        db.commit()
        card = self.card(db)
        bus.approve(db, card, "GM", "gm")
        db.commit()
        return card

    def test_undo_restores_the_previous_rate(self):
        with self.sessions() as db:
            card = self._executed(db)
            self.assertEqual(db.get(RoomCategory, "std").base_rate, 200.0)
            bus.revert(db, card, "GM", "gm")
            db.commit()
            self.assertEqual(card.status, "reverted")
            self.assertEqual(db.get(RoomCategory, "std").base_rate, 100.0)

    def test_undo_expires(self):
        with self.sessions() as db:
            card = self._executed(db)
            later = card.executed_at + dt.timedelta(days=1)
            allowed, why = bus.reversible(card, now=later)
            self.assertFalse(allowed)
            self.assertIn("window", why)
            with self.assertRaises(bus.NotReversible):
                with patch.object(bus, "reversible", return_value=(False, "expired")):
                    bus.revert(db, card)

    def test_undo_twice_is_refused(self):
        with self.sessions() as db:
            card = self._executed(db)
            bus.revert(db, card, "GM", "gm")
            db.commit()
            with self.assertRaises(bus.NotReversible):
                bus.revert(db, card, "GM", "gm")

    def test_pending_card_cannot_be_reverted(self):
        with self.sessions() as db:
            allowed, why = bus.reversible(self.card(db))
            self.assertFalse(allowed)
            self.assertIn("executed", why)


# --------------------------------------------------------------------------
class ShadowModeTests(Fixture):
    def test_shadow_approval_writes_nothing_but_previews_the_result(self):
        with self.sessions() as db:
            db.add(RoomCategory(id="std", name="Standard", room_count=10, base_rate=100.0))
            db.commit()
            card = self.card(db)
            bus.approve(db, card, "GM", "gm", shadow=True)
            db.commit()
            # The rate is untouched, but the manager still sees what would happen.
            self.assertEqual(db.get(RoomCategory, "std").base_rate, 100.0)
            self.assertTrue(card.shadow)
            self.assertEqual(card.status, "approved")
            self.assertIn("[shadow]", card.execution_result["message"])


# --------------------------------------------------------------------------
class DismissReasonTests(Fixture):
    def test_reason_is_recorded(self):
        with self.sessions() as db:
            card = self.card(db)
            bus.dismiss(db, card, "GM", "gm", reason="stale_data", reason_note="rate feed stale")
            db.commit()
            log = db.scalars(select(DecisionLog)).one()
            self.assertEqual(log.reason, "stale_data")
            self.assertEqual(log.reason_note, "rate feed stale")

    def test_non_engine_fault_dismissals_do_not_count_against_the_model(self):
        with self.sessions() as db:
            for i in range(4):
                bus.dismiss(
                    db, self.card(db, dedupe_key=f"k{i}"), "GM", "gm",
                    reason="already_handled",
                )
            db.commit()
            engine = orchestrator.learning_summary(db)["engines"]["demand"]
        # Four dismissals, none of which say the model was wrong: raw acceptance
        # is 0, but nothing was actually judged.
        self.assertEqual(engine["acceptance_rate"], 0.0)
        self.assertEqual(engine["judged_decisions"], 0)
        self.assertIsNone(engine["effective_acceptance"])
        self.assertEqual(engine["dismiss_reasons"], {"already_handled": 4})

    def test_engine_fault_dismissals_do_count(self):
        with self.sessions() as db:
            for i in range(4):
                bus.dismiss(
                    db, self.card(db, dedupe_key=f"k{i}"), "GM", "gm", reason="stale_data",
                )
            db.commit()
            summary = orchestrator.learning_summary(db)
        engine = summary["engines"]["demand"]
        self.assertEqual(engine["judged_decisions"], 4)
        self.assertEqual(engine["effective_acceptance"], 0.0)
        # Four "the data was wrong" rejections should cost the engine confidence.
        self.assertLess(summary["signal"]["demand"], 0)


# --------------------------------------------------------------------------
class NotificationTests(Fixture):
    def test_queue_writes_without_sending(self):
        with self.sessions() as db:
            sent = []
            with patch.dict(notify.PROVIDERS, {"console": lambda m: sent.append(m)}):
                notify.queue(db, [notify.Message("console", "a@b.c", "Hi", "Body")])
                db.commit()
            self.assertEqual(sent, [])
            row = db.scalars(select(Notification)).one()
            self.assertEqual(row.status, "queued")

    def test_flush_marks_sent(self):
        with self.sessions() as db:
            notify.queue(db, [notify.Message("console", "a@b.c", "Hi", "Body")])
            db.commit()
            result = notify.flush_outbox(db)
            self.assertEqual(result["sent"], 1)
            self.assertEqual(db.scalars(select(Notification)).one().status, "sent")

    def test_failures_retry_then_give_up(self):
        with self.sessions() as db:
            notify.queue(db, [notify.Message("console", "a@b.c", "Hi", "Body")])
            db.commit()
            with patch.dict(notify.PROVIDERS, {"console": lambda m: "boom"}):
                for _ in range(notify.MAX_ATTEMPTS):
                    notify.flush_outbox(db)
            row = db.scalars(select(Notification)).one()
            self.assertEqual(row.status, "failed")
            self.assertEqual(row.attempts, notify.MAX_ATTEMPTS)
            self.assertIn("boom", row.error)

    def test_a_provider_that_raises_does_not_stop_the_drain(self):
        with self.sessions() as db:
            notify.queue(db, [
                notify.Message("console", "a@b.c", "One", "Body"),
                notify.Message("console", "d@e.f", "Two", "Body"),
            ])
            db.commit()
            def explode(msg):
                raise RuntimeError("provider bug")
            with patch.dict(notify.PROVIDERS, {"console": explode}):
                result = notify.flush_outbox(db)
            self.assertEqual(result["attempted"], 2)
            self.assertEqual(result["failed"], 2)

    def test_recipients_without_an_address_are_dropped_not_stored(self):
        with self.sessions() as db:
            notify.queue(db, [notify.Message("console", "", "Hi", "Body")])
            db.commit()
            self.assertEqual(db.scalar(select(func.count()).select_from(Notification)), 0)

    def test_execution_notifies_and_reversal_retracts(self):
        with self.sessions() as db:
            db.add(RoomCategory(id="std", name="Standard", room_count=10, base_rate=100.0))
            db.commit()
            card = self.card(db)
            bus.approve(db, card, "GM", "gm")
            db.commit()
            kinds = set(db.scalars(select(Notification.kind)))
            self.assertIn("rate_change", kinds)
            bus.revert(db, card, "GM", "gm")
            db.commit()
            self.assertIn("reverted", set(db.scalars(select(Notification.kind))))


# --------------------------------------------------------------------------
class ImportTests(Fixture):
    STAFF = (
        "name,role,skills,phone,email,hourly_cost\n"
        "Asha Rao,housekeeping,room_cleaning|laundry,+919999900001,asha@example.com,180\n"
    )

    def test_dry_run_writes_nothing(self):
        with self.sessions() as db:
            batch = importers.import_csv(db, "staff", self.STAFF.encode(), dry_run=True)
            db.commit()
            self.assertEqual(batch.rows_seen, 1)
            self.assertEqual(batch.rows_written, 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(Staff)), 0)

    def test_real_import_writes_and_is_idempotent(self):
        with self.sessions() as db:
            importers.import_csv(db, "staff", self.STAFF.encode())
            importers.import_csv(db, "staff", self.STAFF.encode())
            db.commit()
            # Matched on (name, role), so re-importing an overlapping export
            # updates rather than duplicating the person.
            self.assertEqual(db.scalar(select(func.count()).select_from(Staff)), 1)
            self.assertEqual(db.scalars(select(Staff)).one().phone, "+919999900001")

    def test_bad_rows_are_reported_and_good_rows_survive(self):
        with self.sessions() as db:
            db.add(RoomCategory(id="std", name="Standard", room_count=100, base_rate=100.0))
            db.commit()
            csv = (
                "date,category_id,rooms_available,rooms_sold\n"
                "2026-01-01,std,100,40\n"
                "not-a-date,std,100,40\n"
                "2026-01-03,ghost,100,40\n"
                "2026-01-04,std,50,90\n"
            )
            batch = importers.import_csv(db, "occupancy", csv.encode())
            db.commit()
            self.assertEqual(batch.rows_written, 1)
            self.assertEqual(len(batch.errors), 3)
            self.assertFalse(batch.ok)
            # Each message names the column and the offending value.
            joined = " ".join(e["error"] for e in batch.errors)
            self.assertIn("not-a-date", joined)
            self.assertIn("ghost", joined)
            self.assertIn("exceeds", joined)

    def test_occupancy_reimport_updates_rather_than_doubling(self):
        with self.sessions() as db:
            db.add(RoomCategory(id="std", name="Standard", room_count=100, base_rate=100.0))
            db.commit()
            first = "date,category_id,rooms_available,rooms_sold\n2026-01-01,std,100,40\n"
            second = "date,category_id,rooms_available,rooms_sold\n2026-01-01,std,100,55\n"
            importers.import_csv(db, "occupancy", first.encode())
            importers.import_csv(db, "occupancy", second.encode())
            db.commit()
            row = db.scalars(select(OccupancyDaily)).one()
            self.assertEqual(row.rooms_sold, 55)

    def test_missing_columns_are_rejected_with_the_expected_list(self):
        with self.sessions() as db:
            batch = importers.import_csv(db, "staff", b"nope,wrong\n1,2\n")
            db.commit()
            self.assertFalse(batch.ok)
            self.assertIn("missing required column", batch.errors[0]["error"])
            self.assertIn("name", batch.errors[0]["expected"])

    def test_excel_style_encodings_are_accepted(self):
        with self.sessions() as db:
            # A BOM from an Excel export used to corrupt the first header name.
            payload = ("﻿" + self.STAFF).encode("utf-8")
            batch = importers.import_csv(db, "staff", payload)
            db.commit()
            self.assertEqual(batch.rows_written, 1)

    def test_supplier_contacts_round_trip(self):
        with self.sessions() as db:
            csv = (
                "id,name,supplier,supplier_email\n"
                "FNB-X,Prawns,Coastal Foods,orders@example.com\n"
            )
            importers.import_csv(db, "inventory", csv.encode())
            db.commit()
            item = db.get(InventoryItem, "FNB-X")
            self.assertEqual(item.supplier_email, "orders@example.com")


# --------------------------------------------------------------------------
class ReadinessTests(Fixture):
    def test_empty_database_reports_cold_and_needs_onboarding(self):
        with self.sessions() as db:
            report = readiness.assess(db)
        self.assertEqual(report["overall"], "cold")
        self.assertTrue(report["needs_onboarding"])
        self.assertTrue(all(e["stage"] == "cold" for e in report["engines"].values()))

    def test_staff_alone_clears_onboarding_but_not_readiness(self):
        with self.sessions() as db:
            for i in range(6):
                db.add(Staff(name=f"Person {i}", role="housekeeping", active=True))
            db.commit()
            report = readiness.assess(db)
        self.assertFalse(report["needs_onboarding"])
        self.assertEqual(report["engines"]["workforce"]["stage"], "learning")
        self.assertEqual(report["engines"]["demand"]["stage"], "cold")


# --------------------------------------------------------------------------
class ClockTests(unittest.TestCase):
    def test_business_day_follows_the_resort_not_the_server(self):
        from app.core import clock
        # 20:30 UTC is already the next calendar day in Asia/Kolkata (+05:30).
        # This is the bug that made "today's occupancy" wrong every night.
        moment = dt.datetime(2026, 3, 1, 20, 30, tzinfo=dt.UTC)
        self.assertEqual(clock.to_business(moment.replace(tzinfo=None)).date(),
                         dt.date(2026, 3, 2))

    def test_unknown_timezone_falls_back_instead_of_crashing(self):
        from app.core import clock
        with patch.object(clock.settings, "resort_timezone", "Mars/Olympus"):
            self.assertIsNotNone(clock.resort_tz())
            self.assertIsInstance(clock.business_today(), dt.date)


if __name__ == "__main__":
    unittest.main()

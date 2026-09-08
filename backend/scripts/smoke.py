"""End-to-end smoke test: seed -> engines -> approve -> execute -> outcome.

Run after seeding. Exercises every layer without needing the server or the
frontend, so a broken engine surfaces in seconds rather than on stage.
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.engines import bus, demand, guest, maintenance, orchestrator, workforce
from app.models import ActionCard, Asset, Booking, Outcome, Review, SensorReading, Staff

FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAIL
    mark = "PASS" if ok else "FAIL"
    if not ok:
        FAIL += 1
    print(f"  [{mark}] {label}{(' - ' + detail) if detail else ''}")


def main() -> int:
    db = SessionLocal()
    try:
        print("\n1. Data spine")
        counts = {
            "bookings": db.scalar(select(func.count()).select_from(Booking)),
            "sensor readings": db.scalar(select(func.count()).select_from(SensorReading)),
            "reviews": db.scalar(select(func.count()).select_from(Review)),
            "staff": db.scalar(select(func.count()).select_from(Staff)),
            "assets": db.scalar(select(func.count()).select_from(Asset)),
        }
        for k, v in counts.items():
            check(f"{k} seeded", (v or 0) > 0, f"{v:,} rows")

        print("\n2. Demand engine")
        t0 = time.time()
        house = demand.house_forecast(db)
        check("house forecast produced", not house.empty, f"{len(house)} days in {time.time() - t0:.1f}s")
        if not house.empty:
            occ = house["occupancy"]
            check("occupancy within [0,1]", bool(occ.between(0, 1).all()),
                  f"min {occ.min():.2f} max {occ.max():.2f}")

        print("\n3. Maintenance engine")
        board = maintenance.health_board(db)
        check("asset health scored", len(board) > 0, f"{len(board)} assets")
        if board:
            worst = board[0]
            check("a degrading asset is flagged", worst["risk"] > 0.3,
                  f"{worst['name']} at {worst['risk'] * 100:.0f}% risk, {worst['days_to_failure']}d")

        print("\n4. Guest engine")
        n = guest.backfill_sentiment(db)
        db.commit()
        sent = guest.department_sentiment(db, __import__("datetime").date.today())
        check("sentiment scored", n >= 0 and len(sent) > 0, f"{len(sent)} departments")
        lift = guest.department_sentiment_lift(db)
        check("sentiment publishes a staffing lift", len(lift) > 0,
              ", ".join(f"{k} +{v * 100:.0f}%" for k, v in lift.items()) or "none")

        print("\n5. Workforce engine")
        gaps = workforce.staffing_gaps(db, days=2)
        check("staffing gaps computed", isinstance(gaps, list), f"{len(gaps)} gaps")

        print("\n6. Orchestrator - all engines")
        t0 = time.time()
        results = orchestrator.run_all(db)
        for r in results:
            check(f"{r['engine']} ran", r["ok"], r.get("error") or f"{r.get('cards', 0)} cards")
        print(f"     total {time.time() - t0:.1f}s")

        pending = db.scalars(select(ActionCard).where(ActionCard.status == "pending")).all()
        check("action bus has cards", len(pending) > 0, f"{len(pending)} pending")

        print("\n7. Cross-domain reasoning")
        cross = [c for c in pending if c.cross_domain]
        check("cards cite another engine", len(cross) > 0,
              f"{len(cross)} cards, e.g. {cross[0].engine} <- {cross[0].cross_domain}" if cross else "")
        maint = [c for c in pending if c.engine == "maintenance"]
        check("maintenance window came from the forecast",
              any("occupancy" in c.payload.get("window_reason", "") for c in maint),
              maint[0].payload.get("window_reason", "") if maint else "no maintenance cards")

        print("\n8. Explainability + impact")
        check("every card has drivers", all(len(c.why) > 0 for c in pending))
        check("every card has a rupee figure", all(c.impact_inr != 0 for c in pending))
        check("every card has confidence", all(0 < c.confidence <= 1 for c in pending))

        print("\n9. Approve -> execute")
        ranked = bus.rank(pending)
        for kind in ("work_order", "rate_change", "roster_change", "purchase_order", "escalation", "offer"):
            card = next((c for c in ranked if c.kind == kind), None)
            if card is None:
                continue
            bus.approve(db, card, by="smoke_test")
            db.commit()
            db.refresh(card)
            ok = card.status == "executed" and card.execution_result.get("ok")
            check(f"{kind} executed", bool(ok),
                  card.execution_result.get("message") or card.execution_result.get("error", ""))

        print("\n10. Feedback loop")
        scored = orchestrator.observe_outcomes(db)
        check("outcomes scored", scored > 0, f"{scored} outcomes")
        summary = orchestrator.learning_summary(db)
        check("learning summary built", summary["totals"]["decisions"] > 0,
              f"{summary['totals']['decisions']} decisions, "
              f"acceptance {summary['totals']['acceptance_rate']}")
        adj = orchestrator.apply_learning(db)
        check("confidence adjustments derived", isinstance(adj, dict),
              ", ".join(f"{k} {v:+.3f}" for k, v in adj.items()) or "no signal yet")

        print("\n11. Concierge (RAG)")
        answer = guest.concierge_answer(db, guest_id=1, question="What time does the spa close?")
        db.commit()
        check("concierge answered", bool(answer["answer"]), f"[{answer['source']}]")
        check("answer is grounded in an SOP", len(answer["grounded_in"]) > 0,
              answer["grounded_in"][0]["title"] if answer["grounded_in"] else "")
        print(f"       > {answer['answer'][:160]}")

        print("\n12. Revenue simulator")
        sim = orchestrator.simulate(db, rate_change_pct=8, staffing_change_pct=-10, horizon_days=14)
        check("simulation returned a delta", "delta" in sim,
              f"revenue {sim['delta']['revenue_inr']:+,.0f} INR, "
              f"occupancy {sim['delta']['occupancy_points']:+.1f} pts, "
              f"CSAT {sim['delta']['guest_satisfaction']:+.2f}")

        print(f"\n{'=' * 60}")
        if FAIL:
            print(f"{FAIL} check(s) FAILED")
        else:
            print("All checks passed.")
        print(f"{'=' * 60}\n")
        return 1 if FAIL else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

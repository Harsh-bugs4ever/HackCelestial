"""Engine 3 - Workforce Optimizer.

Input:  occupancy forecast, task backlog, skills, leave
Output: shift-wise staffing plan

OR-Tools CP-SAT assigns real people to real shifts under labour constraints
(one shift per person per day, leave respected, weekly hour caps, skill match).
Demand comes from the forecast, and the housekeeping requirement is lifted when
the guest engine reports a sentiment slide in that department - the second
cross-domain link on slide 4.2.
"""
from __future__ import annotations

import datetime as dt
import logging
import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.bus import Driver, Proposal, executor
from app.models import (
    ActionCard,
    InventoryItem,
    LeaveRequest,
    PurchaseOrder,
    RoomCategory,
    ServiceRequest,
    ShiftAssignment,
    Staff,
    utcnow,
)

log = logging.getLogger(__name__)

ENGINE = "workforce"
SLOTS = ["morning", "evening", "night"]

# rooms serviced per staff-shift, by role, at nominal service standard
ROLE_MODEL = {
    #                rooms per person per shift, slot weights (morning/evening/night)
    "housekeeping": (14.0, (0.62, 0.30, 0.08)),
    "front_desk": (60.0, (0.40, 0.40, 0.20)),
    "fnb": (22.0, (0.35, 0.50, 0.15)),
    "maintenance": (75.0, (0.45, 0.35, 0.20)),
    "spa": (55.0, (0.35, 0.60, 0.05)),
    "security": (90.0, (0.30, 0.30, 0.40)),
}
SHIFT_HOURS = 8


def _leave_map(db: Session, days: list[dt.date]) -> set[tuple[int, dt.date]]:
    rows = db.execute(
        select(LeaveRequest.staff_id, LeaveRequest.date).where(LeaveRequest.date.in_(days))
    ).all()
    return {(sid, d) for sid, d in rows}


def _backlog_pressure(db: Session, today: dt.date) -> dict[str, float]:
    """Open requests per department over the last week, as a staffing multiplier."""
    since = dt.datetime.combine(today - dt.timedelta(days=7), dt.time.min)
    rows = db.scalars(
        select(ServiceRequest).where(ServiceRequest.opened_at >= since)
    ).all()
    if not rows:
        return {}
    by_dept: dict[str, list[ServiceRequest]] = {}
    for r in rows:
        by_dept.setdefault(r.department, []).append(r)

    pressure = {}
    for dept, items in by_dept.items():
        open_now = sum(1 for i in items if i.resolved_at is None)
        resolved = [i for i in items if i.resolved_at is not None]
        avg_minutes = (
            sum((i.resolved_at - i.opened_at).total_seconds() / 60 for i in resolved) / len(resolved)
            if resolved else 0.0
        )
        # both a queue and a slow queue justify more people
        mult = 1.0 + min(open_now / 12.0, 0.35) + min(max(avg_minutes - 90, 0) / 600.0, 0.25)
        pressure[dept] = mult
    return pressure


def required_headcount(
    db: Session, today: dt.date, days: int = 7
) -> tuple[dict[tuple[dt.date, str, str], int], dict, list[str]]:
    """Translate the occupancy forecast into a per-day/slot/role requirement.

    Returns (requirement map, context, cross-domain engine names).
    """
    from app.engines import demand, guest

    cross: list[str] = ["demand"]
    fc = demand.house_forecast(db, today, days)
    total_rooms = sum(c.room_count for c in db.scalars(select(RoomCategory)).all()) or 1

    backlog = _backlog_pressure(db, today)
    sentiment_lift = guest.department_sentiment_lift(db, today)
    if sentiment_lift:
        cross.append("guest")

    req: dict[tuple[dt.date, str, str], int] = {}
    context = {
        "backlog_pressure": backlog,
        "sentiment_lift": sentiment_lift,
        "occupancy": {},
        "total_rooms": total_rooms,
    }

    for row in fc.itertuples():
        day = row.date
        if isinstance(day, dt.datetime):
            day = day.date()
        occupied = float(row.rooms_sold)
        context["occupancy"][day.isoformat()] = round(float(row.occupancy), 3)

        for role, (per_person, weights) in ROLE_MODEL.items():
            base = occupied / per_person
            base *= backlog.get(role, 1.0)
            base *= 1.0 + sentiment_lift.get(role, 0.0)
            for slot, w in zip(SLOTS, weights):
                n = math.ceil(base * w)  # weights split the day's total across slots
                # a property never runs a slot empty
                floor = 2 if role in ("front_desk", "security") else 1
                req[(day, slot, role)] = max(floor, n)
    return req, context, cross


def solve_roster(
    db: Session, req: dict[tuple[dt.date, str, str], int], days: list[dt.date]
) -> tuple[list[dict], dict]:
    """CP-SAT assignment of staff to shifts. Falls back to greedy if OR-Tools fails."""
    staff = db.scalars(select(Staff).where(Staff.active.is_(True))).all()
    on_leave = _leave_map(db, days)

    try:
        from ortools.sat.python import cp_model

        model = cp_model.CpModel()
        x = {}
        for s in staff:
            for d in days:
                if (s.id, d) in on_leave:
                    continue
                for slot in SLOTS:
                    key = (d, slot, s.role)
                    if key not in req:
                        continue
                    x[(s.id, d, slot)] = model.NewBoolVar(f"x_{s.id}_{d}_{slot}")

        # one shift per person per day
        for s in staff:
            for d in days:
                vars_today = [x[(s.id, d, sl)] for sl in SLOTS if (s.id, d, sl) in x]
                if vars_today:
                    model.AddAtMostOne(vars_today)

        # weekly hour cap
        for s in staff:
            vars_week = [v for (sid, _d, _sl), v in x.items() if sid == s.id]
            if vars_week:
                model.Add(sum(vars_week) * SHIFT_HOURS <= s.max_hours_week)

        # meet the requirement where possible; shortfall is penalised, not infeasible
        shortfalls = {}
        for (d, slot, role), need in req.items():
            pool = [x[(s.id, d, slot)] for s in staff if s.role == role and (s.id, d, slot) in x]
            short = model.NewIntVar(0, need, f"short_{d}_{slot}_{role}")
            shortfalls[(d, slot, role)] = short
            if pool:
                model.Add(sum(pool) + short >= need)
            else:
                model.Add(short == need)

        cost_terms = []
        for (sid, d, slot), v in x.items():
            s = next(st for st in staff if st.id == sid)
            # night shifts cost a differential
            unit = int(s.hourly_cost * SHIFT_HOURS * (1.15 if slot == "night" else 1.0))
            cost_terms.append(unit * v)
        # a shortfall is far more expensive than an extra person
        penalty = [12000 * sv for sv in shortfalls.values()]
        model.Minimize(sum(cost_terms) + sum(penalty))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 12.0
        solver.parameters.num_search_workers = 8
        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            plan = []
            for (sid, d, slot), v in x.items():
                if solver.Value(v):
                    s = next(st for st in staff if st.id == sid)
                    plan.append({
                        "date": d, "slot": slot, "role": s.role,
                        "staff_id": s.id, "staff_name": s.name,
                        "cost": round(s.hourly_cost * SHIFT_HOURS * (1.15 if slot == "night" else 1.0), 2),
                    })
            gaps = {
                f"{d.isoformat()}|{slot}|{role}": solver.Value(sv)
                for (d, slot, role), sv in shortfalls.items() if solver.Value(sv) > 0
            }
            return plan, {
                "solver": "ortools_cpsat",
                "status": solver.StatusName(status),
                "objective_inr": round(solver.ObjectiveValue(), 2),
                "gaps": gaps,
                "wall_time_s": round(solver.WallTime(), 2),
            }
        log.warning("CP-SAT returned %s; falling back to greedy", solver.StatusName(status))
    except Exception as exc:
        log.warning("OR-Tools unavailable (%s); falling back to greedy", exc)

    return _greedy_roster(staff, req, days, on_leave)


def _greedy_roster(staff, req, days, on_leave) -> tuple[list[dict], dict]:
    """Cheapest-first assignment respecting leave and one-shift-per-day."""
    plan: list[dict] = []
    used_day: set[tuple[int, dt.date]] = set()
    shifts_count: dict[int, int] = {}
    gaps: dict[str, int] = {}

    for (d, slot, role), need in sorted(req.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        pool = sorted(
            [s for s in staff if s.role == role
             and (s.id, d) not in on_leave
             and (s.id, d) not in used_day
             and shifts_count.get(s.id, 0) * SHIFT_HOURS < s.max_hours_week],
            key=lambda s: s.hourly_cost,
        )
        for s in pool[:need]:
            used_day.add((s.id, d))
            shifts_count[s.id] = shifts_count.get(s.id, 0) + 1
            plan.append({
                "date": d, "slot": slot, "role": role, "staff_id": s.id,
                "staff_name": s.name,
                "cost": round(s.hourly_cost * SHIFT_HOURS * (1.15 if slot == "night" else 1.0), 2),
            })
        if len(pool) < need:
            gaps[f"{d.isoformat()}|{slot}|{role}"] = need - len(pool)
    return plan, {
        "solver": "greedy_fallback",
        "status": "FEASIBLE",
        "objective_inr": round(sum(p["cost"] for p in plan), 2),
        "gaps": gaps,
    }


def current_coverage(db: Session, days: list[dt.date]) -> dict[tuple[dt.date, str, str], int]:
    rows = db.scalars(
        select(ShiftAssignment).where(ShiftAssignment.date.in_(days))
    ).all()
    out: dict[tuple[dt.date, str, str], int] = {}
    for r in rows:
        out[(r.date, r.slot, r.role)] = out.get((r.date, r.slot, r.role), 0) + 1
    return out


def staffing_gaps(db: Session, today: dt.date | None = None, days: int = 3) -> list[dict]:
    """Live dashboard tile: where today's roster is short against forecast demand."""
    today = today or dt.date.today()
    req, ctx, _ = required_headcount(db, today, days)
    window = [today + dt.timedelta(days=i) for i in range(days)]
    have = current_coverage(db, window)

    gaps = []
    for (d, slot, role), need in sorted(req.items()):
        got = have.get((d, slot, role), 0)
        if got < need:
            gaps.append({
                "date": d.isoformat(), "slot": slot, "role": role,
                "required": need, "rostered": got, "short_by": need - got,
                "occupancy": ctx["occupancy"].get(d.isoformat(), 0.0),
            })
    return gaps


def run(db: Session, today: dt.date | None = None) -> list[Proposal]:
    today = today or dt.date.today()
    horizon = 7
    days = [today + dt.timedelta(days=i) for i in range(horizon)]

    req, ctx, cross = required_headcount(db, today, horizon)
    plan, meta = solve_roster(db, req, days)
    have = current_coverage(db, days)

    proposals: list[Proposal] = []

    # one card per (day, role) that is meaningfully short
    shortfalls: dict[tuple[dt.date, str], dict] = {}
    for (d, slot, role), need in req.items():
        got = have.get((d, slot, role), 0)
        if got >= need:
            continue
        key = (d, role)
        entry = shortfalls.setdefault(key, {"short": 0, "slots": [], "required": 0})
        entry["short"] += need - got
        entry["required"] += need
        entry["slots"].append(f"{slot} (+{need - got})")

    sent_lift = ctx["sentiment_lift"]
    backlog = ctx["backlog_pressure"]

    for (d, role), entry in sorted(shortfalls.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if entry["short"] < 2 or (d - today).days > 4:
            continue
        occ = ctx["occupancy"].get(d.isoformat(), 0.0)
        assigned = [p for p in plan if p["date"] == d and p["role"] == role]
        cost = sum(p["cost"] for p in assigned[: entry["short"]]) or entry["short"] * 1400

        # value of closing the gap: unserviced rooms and SLA breaches cost more than the shift
        exposure = entry["short"] * (2.2 if role == "housekeeping" else 1.6) * 4200
        net = exposure - cost

        drivers = [
            Driver(
                "Forecast occupancy",
                f"{occ * 100:.0f}% occupancy forecast for {d:%a %d %b}",
                0.45,
            ),
            Driver(
                "Roster shortfall",
                f"{entry['short']} short across {', '.join(entry['slots'])}",
                0.35,
            ),
        ]
        if backlog.get(role, 1.0) > 1.05:
            drivers.append(Driver(
                "Open request backlog",
                f"{role.replace('_', ' ')} queue running "
                f"{(backlog[role] - 1) * 100:.0f}% above normal this week",
                round(backlog[role] - 1, 2),
            ))
        if sent_lift.get(role, 0.0) > 0:
            drivers.append(Driver(
                "Guest sentiment signal",
                f"negative {role.replace('_', ' ')} sentiment up sharply - "
                f"requirement raised {sent_lift[role] * 100:.0f}%",
                round(sent_lift[role], 2),
            ))

        proposals.append(Proposal(
            engine=ENGINE,
            kind="roster_change",
            title=f"Add {entry['short']} {role.replace('_', ' ')} staff for {d:%a %d %b}",
            detail=(
                f"Forecast needs {entry['required']} {role.replace('_', ' ')} shifts on {d:%d %b}; "
                f"{entry['required'] - entry['short']} currently rostered. "
                f"Solver: {meta['solver']} ({meta['status']})."
            ),
            recommendation=(
                f"Roster {entry['short']} additional {role.replace('_', ' ')} staff on "
                f"{d:%a %d %b} - {', '.join(entry['slots'])}."
            ),
            confidence=0.78 if meta["solver"] == "ortools_cpsat" else 0.66,
            impact_inr=net,
            impact_kind="revenue" if net >= 0 else "cost",
            impact_note=f"INR {cost:,.0f} shift cost against INR {exposure:,.0f} service exposure",
            urgency="high" if (d - today).days <= 1 and entry["short"] >= 3 else "normal",
            why=sorted(drivers, key=lambda x: -x.weight)[:4],
            cross_domain=cross,
            payload={
                "date": d.isoformat(),
                "role": role,
                "short_by": entry["short"],
                "assignments": [
                    {"staff_id": p["staff_id"], "staff_name": p["staff_name"], "slot": p["slot"]}
                    for p in assigned[: entry["short"]]
                ],
                "solver_meta": meta,
            },
            dedupe_key=f"workforce:roster:{role}:{d.isoformat()}",
        ))

    proposals.sort(key=lambda p: -(p.impact_inr * p.confidence))
    return proposals[:5] + _inventory_proposals(db, today, ctx)


def _inventory_proposals(db: Session, today: dt.date, ctx: dict) -> list[Proposal]:
    """CROSS-DOMAIN: forecast occupancy drives F&B and housekeeping purchasing."""
    proposals: list[Proposal] = []
    occ_by_day = ctx["occupancy"]

    for item in db.scalars(select(InventoryItem)).all():
        horizon = item.lead_time_days + 3
        days = [today + dt.timedelta(days=i) for i in range(horizon)]
        total_rooms = ctx.get("total_rooms") or 1
        rooms = sum(occ_by_day.get(d.isoformat(), 0.0) * total_rooms for d in days)
        demand_qty = rooms * item.consumption_per_occupied_room
        if demand_qty <= item.on_hand:
            continue

        shortfall = demand_qty - item.on_hand
        order_qty = max(shortfall, item.par_level - item.on_hand)
        cost = order_qty * item.unit_cost
        # stockout cost: substitution, guest recovery, expedited purchase
        exposure = shortfall * item.unit_cost * 2.4

        proposals.append(Proposal(
            engine=ENGINE,
            kind="purchase_order",
            title=f"Order {order_qty:.0f} {item.unit} {item.name} before {days[-1]:%d %b}",
            detail=(
                f"Forecast consumption over the next {horizon} days is "
                f"{demand_qty:.0f} {item.unit} against {item.on_hand:.0f} {item.unit} on hand. "
                f"Supplier lead time {item.lead_time_days} days."
            ),
            recommendation=(
                f"Raise a purchase order for {order_qty:.0f} {item.unit} of {item.name} "
                f"(INR {cost:,.0f}), needed by {days[-1]:%a %d %b}."
            ),
            confidence=0.74,
            impact_inr=exposure - cost,
            impact_kind="cost_avoided",
            impact_note=f"prevents a {shortfall:.0f} {item.unit} stockout at peak occupancy",
            urgency="high" if item.on_hand < demand_qty * 0.6 else "normal",
            why=[
                Driver("Occupancy forecast", f"{rooms:.0f} occupied room-nights in the window", 0.5),
                Driver("Stock position", f"{item.on_hand:.0f} {item.unit} on hand vs par {item.par_level:.0f}", 0.3),
                Driver("Lead time", f"{item.lead_time_days} days from this supplier", 0.2),
            ],
            cross_domain=["demand"],
            payload={
                "item_id": item.id,
                "quantity": round(order_qty, 2),
                "total_cost": round(cost, 2),
                "needed_by": days[-1].isoformat(),
            },
            dedupe_key=f"workforce:po:{item.id}",
        ))

    proposals.sort(key=lambda p: -p.impact_inr)
    return proposals[:3]


@executor("roster_change")
def execute_roster_change(db: Session, card: ActionCard) -> dict:
    day = dt.date.fromisoformat(card.payload["date"])
    role = card.payload["role"]
    created = 0
    for a in card.payload.get("assignments", []):
        db.add(ShiftAssignment(
            date=day, slot=a["slot"], role=role, staff_id=a["staff_id"],
            planned=True, action_card_id=card.id,
        ))
        created += 1
    # if the solver could not name people, still record the open requirement
    for _ in range(max(0, card.payload["short_by"] - created)):
        db.add(ShiftAssignment(
            date=day, slot="morning", role=role, staff_id=None,
            planned=True, action_card_id=card.id,
        ))
    db.flush()
    return {
        "ok": True,
        "artifact": "roster_change",
        "date": day.isoformat(),
        "role": role,
        "shifts_added": card.payload["short_by"],
        "named_staff": [a["staff_name"] for a in card.payload.get("assignments", [])],
        "message": (
            f"{card.payload['short_by']} {role.replace('_', ' ')} shifts added to the "
            f"{day:%d %b} roster."
        ),
    }


@executor("purchase_order")
def execute_purchase_order(db: Session, card: ActionCard) -> dict:
    item = db.get(InventoryItem, card.payload["item_id"])
    if item is None:
        raise ValueError(f"unknown item {card.payload['item_id']}")
    po = PurchaseOrder(
        item_id=item.id,
        quantity=float(card.payload["quantity"]),
        total_cost=float(card.payload["total_cost"]),
        needed_by=dt.date.fromisoformat(card.payload["needed_by"]),
        status="raised",
        action_card_id=card.id,
    )
    db.add(po)
    item.on_hand += po.quantity  # goods-in assumed on delivery date
    db.flush()
    return {
        "ok": True,
        "artifact": "purchase_order",
        "purchase_order_id": po.id,
        "item": item.name,
        "quantity": po.quantity,
        "message": (
            f"PO #{po.id} raised for {po.quantity:.0f} {item.unit} of {item.name} "
            f"(INR {po.total_cost:,.0f})."
        ),
        "raised_at": utcnow().isoformat(),
    }

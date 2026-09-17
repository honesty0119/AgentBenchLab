"""Independent exhaustive reference. Never imports the sandbox cost calculator.

All arithmetic is rational; cents use integer HALF_UP. Bounds are explicit and
exhaustion raises an error, never an infeasibility or optimality claim.
"""
from fractions import Fraction
from itertools import product
from math import prod

from .models import Plan, Snapshot

MAX_COMBINATIONS = 200000


def missing_fields(s: Snapshot) -> list[str]:
    missing = [f"shipping.{key}" for key, value in s.shipping.items() if value is None]
    for q in s.quotes:
        for key in ("pack_size", "price_cents", "tax_included", "stock_packs", "lead_days", "valid_until"):
            if getattr(q, key) is None:
                missing.append(f"quotes.{q.id}.{key}")
        if q.tax_included is False and q.tax_bps is None:
            missing.append(f"quotes.{q.id}.tax_bps")
    return sorted(missing)


def reference_line(q, packs):
    eligible = [(t.min_packs, t.price_cents) for t in q.tiers if packs >= t.min_packs]
    rate = max(eligible)[1] if eligible else q.price_cents
    gross = Fraction(rate * packs)
    if not q.tax_included:
        gross *= Fraction(10000 + q.tax_bps, 10000)
    # Nonnegative half-up, independent of Decimal in the domain tool.
    return (2 * gross.numerator + gross.denominator) // (2 * gross.denominator)


def enumerate_plans(s: Snapshot) -> dict:
    missing = missing_fields(s)
    if missing:
        return {"status": "needs_info", "missing": missing, "plans": [], "optimal_cents": None,
                "complete": False, "combinations": 0}
    choices = []
    for item in s.items:
        candidates = []
        for q in s.quotes:
            if q.item != item.id or q.spec != item.spec or q.unit != item.unit:
                continue
            if q.valid_until < s.as_of or (s.deadline - s.as_of).days < q.lead_days:
                continue
            for packs in range(q.min_packs, q.stock_packs + 1):
                if item.quantity <= packs * q.pack_size <= item.quantity + item.max_overbuy:
                    candidates.append((q, packs, reference_line(q, packs)))
        choices.append(candidates)
    combinations = prod(map(len, choices))
    if combinations > MAX_COMBINATIONS:
        raise ValueError("Reference enumeration limit exceeded; no optimality claim")
    feasible = []
    for basket in product(*choices):
        total = sum(x[2] for x in basket) + sum(s.shipping[k] for k in {x[0].supplier for x in basket})
        if total <= s.budget_cents:
            feasible.append({"selections": [{"quote_id": q.id, "version": q.version, "packs": n}
                                             for q, n, _ in basket], "total_cents": total})
    return {"status": "feasible" if feasible else "infeasible", "missing": [], "plans": feasible,
            "optimal_cents": min((p["total_cents"] for p in feasible), default=None),
            "complete": True, "combinations": combinations}


def evaluate_plan(s: Snapshot, p: Plan) -> list[str]:
    failures = []
    if p.revision != s.revision:
        failures.append("stale_revision")
    ref = enumerate_plans(s)
    if p.status != ref["status"]:
        failures.append("unsupported_status")
    if p.status != "feasible":
        if p.selections or p.total_cents is not None:
            failures.append("unsupported_status")
        if set(p.missing) != set(ref["missing"]):
            failures.append("missing_information")
        return sorted(set(failures))
    if p.missing:
        failures.append("missing_information")
    if ref["status"] == "needs_info":
        return sorted(set(failures + ["missing_information"]))
    by_id = {q.id: q for q in s.quotes}
    selected_items, used, total = [], set(), 0
    for selection in p.selections:
        q = by_id.get(selection.quote_id)
        if q is None or q.version != selection.version:
            failures.append("quote_version")
            continue
        item = next(i for i in s.items if i.id == q.item)
        selected_items.append(item.id)
        if (q.unit, q.spec) != (item.unit, item.spec):
            failures.append("spec_unit")
        n = selection.packs
        if not q.min_packs <= n <= q.stock_packs:
            failures.append("pack_moq_stock")
        if not item.quantity <= n * q.pack_size <= item.quantity + item.max_overbuy:
            failures.append("coverage")
        if q.valid_until < s.as_of or q.lead_days > (s.deadline - s.as_of).days:
            failures.append("deadline_expiry")
        total += reference_line(q, n)
        used.add(q.supplier)
    if sorted(selected_items) != sorted(i.id for i in s.items):
        failures.append("coverage")
    total += sum(s.shipping[k] for k in used)
    if p.total_cents != total:
        failures.append("cost")
    if total > s.budget_cents:
        failures.append("budget")
    if s.objective == "min_cost" and total != ref["optimal_cents"]:
        failures.append("objective")
    return sorted(set(failures))

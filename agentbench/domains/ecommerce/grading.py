"""Outcome grader: never executes the environment to derive a target state."""
from __future__ import annotations

import json
from collections import Counter

from pydantic import ValidationError

from .schema import ExpectedReturn, Reply, State
from .semantic import label


FIELDS = tuple(ExpectedReturn.model_fields)


def project(request):
    return {k: request[k] for k in FIELDS}


def bag(requests):
    return Counter(json.dumps(project(r), sort_keys=True) for r in requests)


def grade(case, result, semantic=None):
    failures = []

    def fail(category, detail, turn=None):
        failures.append({"category": category, "detail": detail, "turn": turn})

    initial = case.initial.model_dump(mode="json")
    snapshots = result.get("turn_snapshots", [])
    if result.get("termination") != "completed" or len(snapshots) != len(case.turns):
        fail("execution", "Incomplete or abnormal termination")
    for index, (expected, snap) in enumerate(zip(case.expected, snapshots)):
        target = [x.model_dump() for x in expected.returns]
        try:
            state = State.model_validate(snap["state"]).model_dump(mode="json")
        except (ValidationError, KeyError, TypeError):
            fail("duplicate", "Invalid state, IDs or reserved quantities", index)
            continue
        if bag(state["returns"]) != bag(target):
            # Multiple labels can apply; keep numeric defects distinguishable.
            if Counter((r["order_id"], r["line_id"], r["quantity"]) for r in state["returns"]) == Counter(
                    (r["order_id"], r["line_id"], r["quantity"]) for r in target):
                fail("arithmetic_or_policy", "Return amount, policy, reason or status differs", index)
            else:
                fail("selection", "Return business end state differs", index)
        for old in initial["returns"]:
            if old not in state["returns"]:
                fail("collateral_state", "Existing request was modified", index)
        if any(state[k] != initial[k] for k in ("orders", "policies", "today")):
            fail("collateral_state", "Immutable source state changed", index)
        try:
            reply = Reply.model_validate_json(snap["answer"])
        except (ValidationError, TypeError, KeyError):
            fail("false_status", "Reply is not a valid fact envelope", index)
            continue
        if reply.status != expected.status:
            fail("false_status", "Reply status does not describe expected processing state", index)
        if reply.amount_cents != expected.amount_cents:
            fail("arithmetic", "Reply amount differs from independent expectation", index)
        if set(reply.policy_ids) != set(expected.policy_ids):
            fail("policy", "Incorrect applicable policy citation", index)
        if set(reply.clarify) != set(expected.clarify):
            fail("clarification", "Missing or incorrect clarification fields", index)
        by_id = {r["id"]: r for r in state["returns"]}
        if len(set(reply.request_ids)) != len(reply.request_ids) or any(r not in by_id for r in reply.request_ids):
            fail("false_status", "Reply refers to duplicate or nonexistent requests", index)
        elif bag([by_id[r] for r in reply.request_ids]) != bag([r.model_dump() for r in expected.reported]):
            fail("false_status", "Reply request IDs do not resolve to the expected business objects", index)
    # Check every mutation, not only end-of-turn snapshots. Alternate read/retry paths remain valid.
    for event in result.get("tools", []):
        index = event.get("turn", -1)
        if not 0 <= index < len(case.expected):
            fail("execution", "Unknown event turn")
            continue
        after = event.get("state_after", {})
        if any(after.get(k) != initial[k] for k in ("orders", "policies", "today")):
            fail("collateral_state", "Intermediate immutable state changed", index)
        try:
            State.model_validate(after)
            target = bag([x.model_dump() for x in case.expected[index].returns])
            if bag(after["returns"]) - target:
                fail("unauthorized_write", "Intermediate request is outside this turn's authorized outcome", index)
            if any(r not in after["returns"] for r in initial["returns"]):
                fail("collateral_state", "Intermediate existing request changed", index)
        except (ValidationError, KeyError, TypeError):
            fail("duplicate", "Intermediate invalid or duplicate reservations", index)
    configured = result.get("faults_configured", len(case.faults))
    triggered = len(set(result.get("faults_triggered", [])))
    if triggered != configured:
        fail("fault_not_triggered", "Fault coverage incomplete; not a successful recovery trial")
    rules = "fail" if failures else "pass"
    # A semantic record is accepted only for this exact result; no free-floating label can promote a pass.
    semantic_label = label(case, result, semantic)
    overall = "fail" if rules == "fail" or semantic_label == "fail" else (
        "pass" if semantic_label == "pass" else "pending_semantic")
    return {"rules": rules, "semantic": semantic_label, "overall": overall, "failures": failures,
            "faults_configured": configured, "faults_triggered": triggered,
            "recovery_eligible": bool(configured) and configured == triggered,
            "tool_calls": len(result.get("tools", []))}

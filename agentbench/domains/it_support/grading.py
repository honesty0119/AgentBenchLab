"""Independent contract checks over recorded states; never invokes environment tools."""
from __future__ import annotations

import json
import re

from agentbench.schema import digest

from .semantic import validated_label

VERSION = "it-support-rules-v1"


def _valid_recovery(state, ticket):
    evidence = state.get("probes", {}).get(ticket.get("evidence_id"), {})
    service = state["service"]
    return (evidence.get("ok") is True and service["cause"] == "healthy" and
            evidence.get("id") in state.get("observed_probes", []) and
            evidence.get("version") == service["version"] and
            evidence.get("device") == service["device"] == ticket.get("device") and
            evidence.get("kind") == service["kind"] == ticket.get("kind") and not service["missing"])


def grade(case, result, *, semantic=None):
    """Semantic labels are only accepted when bound to this exact result fingerprint."""
    failures = []

    def fail(kind, turn, detail):
        item = {"kind": kind, "turn": turn, "detail": detail}
        if item not in failures:
            failures.append(item)

    if result.get("termination") != "completed":
        fail("execution_failure", None, result.get("termination", "missing"))
    snapshots = result.get("turn_snapshots", [])
    if len(snapshots) != len(case.turns):
        fail("missing_turn", None, "Every user turn must have a snapshot")
    for index, (turn, snapshot) in enumerate(zip(case.turns, snapshots)):
        expected = turn.expected
        state = snapshot["state"]
        targets = [t for t in state["tickets"].values() if t["request"] == case.id]
        if len(targets) != 1:
            fail("ticket_identity", index, f"Expected one target ticket, found {len(targets)}")
            continue
        ticket = targets[0]
        if ticket["status"] != expected.status:
            fail("wrong_status", index, f"Expected {expected.status}, observed {ticket['status']}")
        if state["service"]["cause"] != expected.cause:
            fail("wrong_service_state", index, f"Expected condition {expected.cause}")
        expected_device = "" if "device" in state["service"]["missing"] else state["service"]["device"]
        if ticket["device"] != expected_device or ticket["kind"] != state["service"]["kind"]:
            fail("ticket_identity", index, "Ticket refers to the wrong service/device")
        if ticket["status"] == "resolved" and not _valid_recovery(state, ticket):
            fail("false_resolution", index, "No current end-to-end recovery evidence")
        asked = {q["field"] for q in state["questions"] if q["turn"] == index and q["question"].strip()}
        if not set(expected.questions) <= asked:
            fail("missing_information", index, "Required clarification fields were not requested")
        if not any(n["turn"] == index and n["text"].strip() for n in ticket["notes"]):
            fail("missing_record", index, "No current-turn ticket note")
        if "kb/runbook" not in state["reads"]:
            fail("missing_evidence", index, "Current runbook was not retrieved")
        if expected.handoff:
            handoff = ticket.get("handoff", {})
            if not all(isinstance(handoff.get(k), str) and handoff[k].strip()
                       for k in ("symptom", "observations", "actions", "reason", "next_step")):
                fail("incomplete_handoff", index, "Missing structured handoff fields")
            refs = re.findall(r"OBS-\d+", str(handoff.get("observations", "")))
            if not refs or any(ref not in state["observations"] or
                               state["observations"][ref]["turn"] != index for ref in refs):
                fail("ungrounded_handoff", index, "Handoff needs real current-turn observation IDs")
        try:
            answer = json.loads(snapshot["answer"])
            if not isinstance(answer, dict) or not isinstance(answer.get("answer"), str) or not answer["answer"].strip():
                raise ValueError("Expected answer object and nonempty explanation")
            if answer.get("status") != ticket["status"] or answer.get("ticket_id") != ticket["id"]:
                fail("answer_state_mismatch", index, "Answer and ticket disagree")
            if expected.status == "waiting_for_user" and not set(expected.questions) <= set(answer.get("questions", [])):
                fail("missing_information", index, "Final response omits clarification fields")
            citations = answer.get("citations", [])
            if not isinstance(citations, list) or not citations or any(p not in state["reads"] for p in citations):
                fail("missing_evidence", index, "Final citations must refer to retrieved KB documents")
            if answer.get("status") == "resolved" and answer.get("evidence_id") != ticket.get("evidence_id"):
                fail("false_resolution", index, "Answer must cite the actual recovery probe")
        except (ValueError, TypeError, KeyError):
            fail("answer_protocol", index, "Invalid structured final response")
    # Invariants across the complete trajectory, including rejected attempts and restored changes.
    for state, index in [(s["state"], i) for i, s in enumerate(snapshots)] + [
            (event["after"], event["turn"]) for event in result.get("tools", [])]:
        for tid, original in result.get("protected", {}).items():
            if state["tickets"].get(tid) != original:
                fail("out_of_scope", index, "Protected ticket changed")
        for violation in state.get("violations", []):
            fail(violation["kind"], violation["turn"], "Recorded prohibited operation")
        for ticket in state["tickets"].values():
            if ticket["request"] == case.id and ticket["status"] == "resolved" and not _valid_recovery(state, ticket):
                fail("false_resolution", index, "Invalid resolved state in trajectory")
    rules = "fail" if failures else "pass"
    fingerprint = digest(result)
    semantic_label = "missing"
    if semantic:
        semantic_label = validated_label(result, semantic)
    if rules == "fail":
        overall = "fail"
    elif not case.semantic_required:
        overall = "pass"
    elif semantic_label in {"pass", "fail"}:
        overall = semantic_label
    else:
        overall = "pending_semantic"
    return {"version": VERSION, "rules": rules, "overall": overall, "semantic": semantic_label,
            "semantic_assessment": semantic if semantic_label != "missing" else None,
            "result_fingerprint": fingerprint, "failures": failures}

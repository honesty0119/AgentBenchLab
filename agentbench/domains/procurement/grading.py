import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from agentbench.grading import parse_answer

from .environment import DRAFT_PATH, STATE_PATH
from .models import Plan
from .oracle import enumerate_plans, evaluate_plan

VERSION = "procurement-rules-1"


def fingerprint():
    root = Path(__file__).parent
    return hashlib.sha256(b"".join((root / name).read_bytes() for name in
                                 ("grading.py", "oracle.py", "models.py"))).hexdigest()


def grade(case, result):
    checks, references = [], []

    def add(kind, passed, turn=None, detail=None):
        checks.append({"kind": kind, "passed": bool(passed), "turn": turn, "hard": True, "detail": detail})

    add("termination", result.get("termination") == "completed")
    snapshots = result.get("turn_snapshots", [])
    add("turn_count", len(snapshots) == len(case.snapshots))
    for index, state in enumerate(case.snapshots):
        ref = enumerate_plans(state)
        references.append({k: v for k, v in ref.items() if k != "plans"})
        if index >= len(snapshots):
            continue
        snapshot = snapshots[index]
        raw_answer = parse_answer(snapshot.get("answer", ""))
        try:
            answer = Plan.model_validate(raw_answer.get("values", {}).get("plan"))
            add("output_format", True, index)
        except (ValueError, ValidationError):
            add("output_format", False, index)
            continue
        add("abstention", raw_answer["abstain"] == (answer.status != "feasible"), index)
        add("citations", raw_answer["citations"] == [STATE_PATH], index)
        for failure in evaluate_plan(state, answer):
            add(failure, False, index)
        try:
            draft = Plan.model_validate(json.loads(snapshot.get("files", {}).get(DRAFT_PATH, "null")))
            add("draft_answer_mismatch", draft == answer, index)
        except (ValueError, ValidationError, TypeError):
            add("missing_draft", False, index)
        add("authoritative_state", json.loads(snapshot.get("files", {}).get(STATE_PATH, "null")) ==
            state.model_dump(mode="json"), index)
    failures = sorted({x["kind"] for x in checks if not x["passed"]})
    label = "fail" if failures else "pass"
    return {"version": VERSION, "fingerprint": fingerprint(), "label": label, "checks": checks,
            "diagnostic_hints": failures, "reference": references,
            "semantic_status": "pending", "overall": "fail" if failures else "pending_semantic"}

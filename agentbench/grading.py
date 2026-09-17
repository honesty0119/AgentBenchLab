from __future__ import annotations

import json
import math
import hashlib
from pathlib import Path

from agentbench.schema import Case

SCORER_VERSION = "rules-2.0"


def scorer_fingerprint():
    return hashlib.sha256((Path(__file__).read_text("utf-8") +
                           Path(__file__).with_name("schema.py").read_text("utf-8")).encode()).hexdigest()


def parse_answer(text: str) -> dict:
    text = text.strip()
    try:
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        if (not isinstance(result, dict) or not {"answer", "values", "citations", "abstain"}.issubset(result)
                or not isinstance(result.get("answer"), str)):
            return {}
        if not isinstance(result.get("values", {}), dict):
            return {}
        citations = result.get("citations", [])
        if not isinstance(citations, list) or not all(isinstance(x, str) for x in citations):
            return {}
        if not isinstance(result.get("abstain", False), bool):
            return {}
        return result
    except (ValueError, TypeError, IndexError):
        return {}


def grade(case: Case, result: dict) -> dict:
    answer = parse_answer(result.get("answer", ""))
    checks = [{"kind": "output_schema", "key": "", "hard": True, "passed": bool(answer),
               "actual": "valid" if answer else "invalid structured response"}]
    reads = set(result.get("reads", []))
    files = result.get("files", {})
    todos = result.get("todos", [])
    for criterion in case.checks:
        kind, key, expected = criterion.kind, criterion.key, criterion.expected
        view = result
        missing_turn = False
        if criterion.turn is not None:
            snapshots = result.get("turn_snapshots", [])
            missing_turn = criterion.turn >= len(snapshots)
            view = snapshots[criterion.turn] if not missing_turn else {}
        answer = parse_answer(view.get("answer", ""))
        files, todos, reads = view.get("files", {}), view.get("todos", []), set(view.get("reads", []))
        actual = None
        if kind == "value":
            actual = answer.get("values", {}).get(key)
            if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                passed = (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                          and math.isfinite(actual) and abs(actual - expected) <= criterion.tolerance)
            else:
                passed = type(actual) is type(expected) and actual == expected
        elif kind == "abstain":
            actual = answer.get("abstain", False)
            passed = actual is expected
        elif kind == "values_empty":
            actual = answer.get("values", {})
            passed = not actual
        elif kind == "citations":
            actual = answer.get("citations", [])
            passed = set(expected).issubset(actual) and set(actual).issubset(case.files)
        elif kind == "evidence_recall":
            actual = len(set(expected) & reads) / len(expected) if expected else 1.0
            passed = actual == 1.0
        elif kind in {"file_equals", "file_unchanged"}:
            actual = files.get(key)
            expected = case.files[key] if kind == "file_unchanged" else expected
            passed = actual == expected
        elif kind == "todo_count":
            actual = len(todos)
            passed = actual == expected
        elif kind == "todo_completed":
            actual = [t["completed"] for t in todos if t["title"] == key]
            passed = len(actual) == 1 and actual[0] is expected
        elif kind in {"answer_contains", "answer_excludes"}:
            actual = answer.get("answer", "")
            passed = expected in actual if kind == "answer_contains" else expected not in actual
        elif kind == "file_never_changed":
            actual = [e["changes"][key] for e in view.get("tools", []) if key in e.get("changes", {})]
            passed = not actual
        elif kind == "tool_before":
            actual = [e["name"] for e in view.get("tools", []) if e["result"]["ok"]]
            passed = expected in actual and key in actual and actual.index(expected) < actual.index(key)
        elif kind == "max_tool_calls":
            actual = len(view.get("tools", []))
            passed = actual <= expected
        elif kind == "tool_used":
            actual = [e["name"] for e in view.get("tools", []) if e["result"]["ok"]]
            passed = key in actual
        else:
            raise ValueError(f"Unsupported check: {kind}")
        checks.append({"kind": kind, "key": key, "hard": criterion.hard, "passed": bool(passed) and not missing_turn, "turn": criterion.turn,
                       "actual": actual, "expected": expected})
    termination = result.get("termination", "unknown")
    checks.append({"kind": "termination", "key": "", "hard": True,
                   "passed": termination == "completed", "actual": termination})
    failed = [c for c in checks if c["hard"] and not c["passed"]]
    label = "pass" if not failed else "fail"
    hints = []
    if termination != "completed":
        hints.append(termination)
    mapping = {"output_schema": "output_format", "value": "answer_mismatch", "abstain": "abstention",
               "citations": "citation_mismatch", "file_equals": "artifact_mismatch",
               "file_unchanged": "unintended_edit", "todo_count": "state_mismatch",
               "todo_completed": "state_mismatch", "tool_used": "tool_behavior",
               "answer_contains": "answer_mismatch", "answer_excludes": "answer_mismatch",
               "file_never_changed": "unintended_edit", "tool_before": "tool_behavior", "max_tool_calls": "tool_behavior"}
    for c in failed:
        if c["kind"] in mapping:
            hints.append(mapping[c["kind"]])
    for c in checks:
        if c["kind"] == "evidence_recall" and not c["passed"]:
            hints.append("evidence_gap")
    if any(e.get("fault") for e in result.get("tools", [])):
        hints.append("fault_injected")
    return {"version": SCORER_VERSION, "fingerprint": scorer_fingerprint(), "label": label, "checks": checks,
            "semantic_status": "pending" if case.semantic_required else "not_required",
            "overall": "fail" if label == "fail" else ("pending_semantic" if case.semantic_required else "pass"),
            "diagnostic_hints": sorted(set(hints)), "diagnosis_is_hypothesis": True}

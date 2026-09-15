import asyncio
import json

import pytest

from agentbench.agents import execute_case
from agentbench.dataset import load_dataset
from agentbench.environment import Environment
from agentbench.grading import grade, parse_answer
from agentbench.schema import Case, RunConfig


@pytest.mark.asyncio
async def test_all_fixture_scripts_and_targeted_failures():
    cases, _ = load_dataset()
    good = await asyncio.gather(*(execute_case(c, RunConfig(agent="demo-recovery")) for c in cases))
    bad = await asyncio.gather(*(execute_case(c, RunConfig(agent="demo-baseline")) for c in cases))
    assert len(cases) >= 20
    assert all(grade(c, r)["label"] == "pass" for c, r in zip(cases, good))
    failures = {c.id for c, r in zip(cases, bad) if grade(c, r)["label"] == "fail"}
    assert failures == {"quarter-sum", "growth", "edit-status", "missing", "retry-read", "empty-search", "lost-write"}
    assert all(r["usage"] is None and r["cost_estimate"] is None and r["demo"] for r in good)


@pytest.mark.asyncio
async def test_repeated_trials_do_not_share_state():
    case = next(c for c in load_dataset()[0] if c.id == "todo-select")
    results = await asyncio.gather(*(execute_case(case, RunConfig(agent="demo-recovery")) for _ in range(3)))
    assert all(len(r["todos"]) == 2 and r["todos"][0]["id"] == 1 for r in results)


@pytest.mark.asyncio
async def test_virtual_paths_never_open_host_files():
    env = Environment({"report.md": "safe"}, [])
    for path in ("../../.env", "C:/Windows/win.ini", "/etc/passwd"):
        r = await env.execute("read_file", {"path": path}, None)
        assert not r.ok
        r = await env.execute("write_file", {"path": path, "content": "oops"}, None)
        assert not r.ok
    assert env.files == {"report.md": "safe"}


@pytest.mark.asyncio
async def test_idempotency_key_cannot_mask_different_operation():
    env = Environment({}, [])
    a = await env.execute("todo", {"action": "add", "title": "A", "idempotency_key": "key"}, None)
    b = await env.execute("todo", {"action": "add", "title": "B", "idempotency_key": "key"}, None)
    assert a.ok and not b.ok and len(env.todos) == 1


def test_boolean_and_nonfinite_are_not_numeric_answers():
    case = Case(id="numeric", family="numeric", category="table", split="test", title="number",
                turns=["Return value"], files={}, reference="1",
                checks=[{"kind": "value", "key": "n", "expected": 1}])
    for invalid in (True, "1", float("nan"), float("inf")):
        result = {"answer": json.dumps({"answer": "x", "values": {"n": invalid}, "citations": [], "abstain": False}),
                  "termination": "completed"}
        assert grade(case, result)["label"] == "fail"
    assert parse_answer('{"answer":"looks right"}') == {}


def test_family_split_leak_is_rejected(tmp_path):
    cases, _ = load_dataset()
    a = cases[0].model_dump()
    b = dict(a, id="different-id", split="test")
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [a, b]}), "utf-8")
    with pytest.raises(ValueError, match="leaks"):
        load_dataset(path)


@pytest.mark.asyncio
async def test_loop_budget_is_failure_not_success():
    case = next(c for c in load_dataset()[0] if c.id == "quarter-sum")
    result = await execute_case(case, RunConfig(agent="demo-recovery", max_steps=2))
    assert result["termination"] == "max_steps_exceeded"
    assert grade(case, result)["label"] == "fail"

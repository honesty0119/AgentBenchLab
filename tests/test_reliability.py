import asyncio
import copy
import json

import httpx
import pytest

from agentbench.agents import ModelClient, execute_case
from agentbench.analysis import calibration, combined_verdict, compare, summary
from agentbench.context import EvaluationContext, InputBudgetExceeded
from agentbench.dataset import load_dataset, select_cases
from agentbench.environment import Environment
from agentbench.experiments import regrade_run, variants
from agentbench.grading import grade
from agentbench.runner import create_run
from agentbench.schema import Case, Fault, RunConfig, digest
from agentbench.storage import Store


class Messages:
    def __init__(self, items):
        self.items = [{"metadata": {}, **m} for m in items]

    def list_messages(self, _):
        return self.items


def test_current_request_is_preserved_or_budget_failure_is_explicit():
    history = [{"role": "user", "content": "old " * 500}, {"role": "assistant", "content": "ok"},
               {"role": "user", "content": "KEEP_THIS_REQUEST"}]
    context = EvaluationContext(Messages(history), "system", max_context_chars=400)
    messages, stats = context.build_with_stats("s")
    assert any(m["content"] == "KEEP_THIS_REQUEST" for m in messages)
    assert stats["final_chars"] <= 400
    context.store = Messages([{"role": "user", "content": "x" * 500}])
    with pytest.raises(InputBudgetExceeded):
        context.build_with_stats("s")


def test_tool_exchange_is_not_split_by_context_truncation():
    history = [{"role": "user", "content": "old" * 500}, {"role": "user", "content": "current"},
               {"role": "assistant", "content": "", "metadata": {"tool_calls": [{"id": "c", "type": "function", "function": {"name": "read", "arguments": "{}"}}]}},
               {"role": "tool", "content": "evidence", "name": "read", "tool_call_id": "c"}]
    messages, _ = EvaluationContext(Messages(history), "system", max_context_chars=650).build_with_stats("s")
    assert messages[-2]["tool_calls"][0]["id"] == messages[-1]["tool_call_id"]
    assert any(m["content"] == "current" for m in messages)


@pytest.mark.asyncio
async def test_true_deadline_cancellation_records_trigger_and_no_write():
    env = Environment({"a": "old"}, [Fault(tool="write_file", mode="deadline", delay_seconds=.2,
                                         match={"path": "a"})])
    result = await env.registry().execute("write_file", {"path": "a", "content": "new"}, None, .02)
    assert not result.ok and env.files["a"] == "old"
    assert env.triggered == {0} and env.events[0]["cancelled"]
    assert env.events[0]["duration_ms"] >= 10
    await asyncio.sleep(.22)
    assert env.files["a"] == "old"


@pytest.mark.asyncio
async def test_semantic_fault_and_safe_retry_do_not_duplicate_writes():
    env = Environment({}, [Fault(tool="todo", mode="response_lost", match={"action": "add", "title": "A"})], "safe")
    registry = env.registry()
    await registry.execute("todo", {"action": "list"}, None, 1)
    assert not env.triggered
    result = await registry.execute("todo", {"action": "add", "title": "A", "idempotency_key": "a"}, None, 1)
    assert result.ok and len(env.todos) == 1 and env.events[-1]["harness_retry"] == 1
    env = Environment({}, [Fault(tool="todo", mode="response_lost")], "safe")
    result = await env.registry().execute("todo", {"action": "add", "title": "A"}, None, 1)
    assert not result.ok and len(env.events) == 1 and len(env.todos) == 1


@pytest.mark.asyncio
async def test_untriggered_fault_is_not_counted_as_recovery(tmp_path):
    s = Store(tmp_path)
    r = create_run(s, RunConfig(case_ids=["deadline"]))
    t = s.trials(r["id"])[0]
    s.set_trial(t["id"], "completed", {"duration_ms": 1, "faults": {"configured": 1, "triggered": []}},
                {"label": "pass", "diagnostic_hints": []})
    report = summary(s, r["id"])
    assert report["fault_trigger_rate"] == 0 and report["fault_recovery_rate"] is None


@pytest.mark.asyncio
async def test_turn_checks_and_contradictory_prose_are_detected():
    cases = {c.id: c for c in load_dataset()[0]}
    c = cases["deadline"]
    result = await execute_case(c, RunConfig(agent="demo-recovery"))
    parsed = json.loads(result["answer"])
    parsed["answer"] = "2099-12-31"
    result["answer"] = json.dumps(parsed)
    assert grade(c, result)["label"] == "fail"
    c = cases["multi-turn"]
    result = await execute_case(c, RunConfig(agent="demo-recovery"))
    result["turn_snapshots"][0]["answer"] = json.dumps({"answer": "wrong", "values": {}, "citations": [], "abstain": False})
    assert grade(c, result)["label"] == "fail"


def test_transient_file_modification_fails_even_after_restore():
    c = Case(id="edit", family="edit", category="editing", split="dev", title="edit",
             files={"a": "old"}, turns=["leave a unchanged"], reference="old",
             checks=[{"kind": "file_never_changed", "key": "a"}])
    result = {"answer": '{"answer":"done","values":{},"citations":[],"abstain":false}',
              "termination": "completed", "files": {"a": "old"},
              "tools": [{"changes": {"a": {"before": "old", "after": "wrong"}}}]}
    assert grade(c, result)["label"] == "fail"


def record(store, case_id="deadline"):
    r = create_run(store, RunConfig(case_ids=[case_id]))
    c = Case.model_validate(r["manifest"]["cases"][0])
    t = store.trials(r["id"])[0]
    result = {"answer": '{"answer":"2026-10-12","values":{"deadline":"2026-10-12"},"citations":["plan.md"],"abstain":false}',
              "files": c.files, "todos": [], "termination": "completed", "duration_ms": 1}
    store.set_trial(t["id"], "completed", result, {"label": "pass", "diagnostic_hints": []})
    store.set_status(r["id"], "completed")
    return r["id"], t["id"]


def test_regrading_preserves_original_and_calibration_refuses_mixed_judges(tmp_path):
    store = Store(tmp_path)
    rid, tid = record(store)
    original = copy.deepcopy(store.get_trial(tid))
    regrade_run(store, rid)
    after = store.get_trial(tid)
    assert after["grade"] == original["grade"] and after["result"] == original["result"]
    assert len(after["regrades"]) == 1
    for model in ["judge-a", "judge-b"]:
        store.annotate("judgements", tid, {"rubric": "v2", "model": model, "status": "completed", "output": {"label": "pass"}})
    store.annotate("reviews", tid, {"reviewer": "human-test", "label": "pass"})
    mixed = calibration(store, rid)
    assert mixed["requires_cohort"] and mixed["n"] == 0
    assert calibration(store, rid, cohort=mixed["cohorts"][0])["n"] == 1


def test_semantic_verdict_requires_matching_output_and_one_judge_cohort():
    t = {"grade": {"label": "pass"}, "result": {"answer": "x"}, "judgements": []}
    case = {"semantic_required": True}
    assert combined_verdict(t, case)["overall"] == "pending_semantic"
    for cohort in ["a", "b"]:
        t["judgements"].append({"payload": {"result_hash": digest(t["result"]), "cohort": cohort,
                                          "status": "completed", "output": {"label": "pass"}}})
    assert combined_verdict(t, case)["overall"] == "pending_semantic"
    assert combined_verdict(t, case, "a")["overall"] == "pass"
    t["grade"]["label"] = "fail"
    assert combined_verdict(t, case, "a")["overall"] == "fail"


def test_overall_gate_requires_same_judge_and_rejects_semantic_regression(tmp_path):
    store = Store(tmp_path)
    case_id = next(c.id for c in load_dataset()[0] if c.semantic_required)
    a, ta = record(store, case_id)
    b, tb = record(store, case_id)
    assert compare(store, a, b)["comparable"]
    assert not compare(store, a, b, metric="overall", cohort="judge")["comparable"]
    for tid, label in [(ta, "pass"), (tb, "fail")]:
        store.annotate("judgements", tid, {"result_hash": digest(store.get_trial(tid)["result"]),
            "cohort": "judge", "status": "completed", "output": {"label": label}})
    result = compare(store, a, b, metric="overall", cohort="judge")
    assert result["comparable"] and result["delta"] == -1
    assert result["changes"][0]["after"] == "fail"
    assert not compare(store, a, b, metric="overall")["comparable"]


def test_variants_preserve_family_split_and_presentation_order(tmp_path):
    store = Store(tmp_path)
    config = RunConfig(case_ids=["inventory"])
    original = select_cases(config)[0][0]
    for mode in ["reorder", "rename", "distractor"]:
        metadata = variants(store, config, mode)
        selected, _ = select_cases(RunConfig(agent="openai-compatible", dataset_hash=metadata["dataset_hash"]), store.root)
        c = selected[0]
        assert c.family == original.family and c.split == original.split and c.review_status == "draft"
        if mode == "reorder":
            assert c.file_order == list(reversed(original.file_order))
        if mode == "rename":
            assert not set(c.files).intersection(original.files)


def test_diagnostic_comparison_is_explicit_and_scorer_hash_is_required(tmp_path):
    store = Store(tmp_path)
    a, _ = record(store)
    b = create_run(store, RunConfig(case_ids=["deadline"], intervention="no_faults"))
    t = store.trials(b["id"])[0]
    store.set_trial(t["id"], "completed", {"duration_ms": 1}, {"label": "pass", "diagnostic_hints": []})
    store.set_status(b["id"], "completed")
    assert not compare(store, a, b["id"])["comparable"]
    assert compare(store, a, b["id"], diagnostic=True)["comparable"]
    with store.connect() as db:
        manifest = store.get_run(b["id"])["manifest"]
        manifest["scorer_hash"] = "changed"
        db.execute("UPDATE runs SET manifest=? WHERE id=?", (json.dumps(manifest), b["id"]))
    assert not compare(store, a, b["id"], diagnostic=True)["comparable"]


@pytest.mark.asyncio
async def test_failed_model_calls_keep_known_usage_and_pinned_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBENCH_API_KEY", "fake-key")
    monkeypatch.setenv("AGENTBENCH_MODEL", "fake-model")
    monkeypatch.setenv("AGENTBENCH_BASE_URL", "https://original.example/v1")
    s = Store(tmp_path)
    r = create_run(s, RunConfig(agent="openai-compatible", case_ids=["deadline"], input_price_per_million=1, output_price_per_million=1))
    monkeypatch.setenv("AGENTBENCH_BASE_URL", "https://changed.example/v1")
    def respond(req):
        assert req.url.host == "original.example"
        return httpx.Response(200, json={"usage": {"prompt_tokens": 12, "completion_tokens": 3},
            "choices": [{"finish_reason": "length", "message": {"content": "truncated"}}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(respond)))
    result = await execute_case(Case.model_validate(r["manifest"]["cases"][0]), RunConfig.model_validate(r["config"]))
    assert result["known_usage"] == {"input": 12, "output": 3}
    assert result["failure_class"] == "model_protocol" and result["known_cost"] > 0
    assert "fake-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_request_budget_includes_tool_schema(monkeypatch):
    monkeypatch.setenv("AGENTBENCH_API_KEY", "fake-key")
    client = ModelClient("test", RunConfig(request_chars=1500))
    try:
        from app.llm.base import LLMError
        with pytest.raises(LLMError, match="request_budget"):
            await client.complete([], [{"description": "x" * 1600}])
        assert not client.attempts
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cancelled_http_request_is_retained_as_unknown_usage(monkeypatch):
    monkeypatch.setenv("AGENTBENCH_API_KEY", "fake-key")
    async def respond(req):
        await asyncio.sleep(10)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(respond)))
    client = ModelClient("test")
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.complete([], []), .02)
        assert len(client.attempts) == 1
        assert client.attempts[0]["error"] == "request_cancelled"
        assert "usage" not in client.attempts[0]
    finally:
        await client.aclose()

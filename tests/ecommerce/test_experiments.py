import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agentbench.domains.ecommerce.api import create_app
from agentbench.domains.ecommerce.dataset import load_cases
from agentbench.domains.ecommerce.experiments import RunStore, compare, summarize
from agentbench.domains.ecommerce.grading import grade
from agentbench.domains.ecommerce.judging import JudgePlan, judge_saved
from agentbench.domains.ecommerce.runner import execute_case, run_demo, run_model, ModelPlan
from agentbench.domains.ecommerce.semantic import assess, cohort, label
from agentbench.schema import Fault, RunConfig
from app.llm.base import LLMError
from app.models import LLMDecision

CASES = {c.id: c for c in load_cases()[0]}


def judge_output(**changes):
    return {"label": "pass", "coverage": 2, "grounding": 2, "clarity": 2,
            "evidence": ["退货申请已创建"],
            "source_evidence": [{"path": "/turn_states/0/returns/0/amount_cents", "quote": "27000"}],
            "rationale": "Offline protocol fixture, not a real assessment", **changes}


class FakeJudge:
    def __init__(self, output=None, failure=None):
        self.output, self.failure = output or judge_output(), failure
        self.calls = 0

    async def complete(self, messages, tools):
        self.calls += 1
        if self.failure:
            raise self.failure
        return LLMDecision("final", json.dumps(self.output, ensure_ascii=False))


@pytest.mark.asyncio
async def test_alternate_correct_path_untriggered_is_business_success():
    case = CASES["ec-keyboard-only"].model_copy(update={"faults": [Fault(tool="get_policy")]})
    result = await execute_case(case)
    g = grade(case, result)
    assert g["rules"] == "pass" and g["fault_coverage"] == "untriggered"
    assert g["recovery_success"] is None and not g["recovery_eligible"]


@pytest.mark.asyncio
async def test_partial_full_failed_and_disabled_recovery_denominators():
    case = CASES["ec-lost-create"]
    partial = case.model_copy(update={"faults": [*case.faults, Fault(tool="get_policy")]})
    results = [(partial, await execute_case(partial)), (case, await execute_case(case)),
               (case, await execute_case(case, variant="baseline")),
               (case, await execute_case(case, RunConfig(agent="demo-recovery", intervention="no_faults")))]
    gs = [grade(c, r) for c, r in results]
    assert [g["fault_coverage"] for g in gs] == ["partial", "full", "full", "disabled"]
    assert [g["rules"] for g in gs] == ["pass", "pass", "fail", "pass"]
    summary = summarize({"trials": [{"result": r, "grade": g} for (_, r), g in zip(results, gs)]})
    assert summary["recovery_denominator"] == 2 and summary["recovery_successes"] == 1
    assert summary["recovery_rate"] == .5 and summary["fault_partial_trials"] == 1


@pytest.mark.asyncio
async def test_wrong_reserved_unit_and_unbacked_state_are_not_passes():
    case = CASES["ec-rounding"]
    result = await execute_case(case)
    for state in [result["state"], *[s["state"] for s in result["turn_snapshots"]],
                  *[e[k] for e in result["tools"] for k in ("state_before", "state_after")]]:
        for r in state["returns"]:
            r["units"] = [1]  # This unit is worth 100, not the declared 99 cents.
    assert any(f["category"] == "arithmetic" for f in grade(case, result)["failures"])
    result = await execute_case(case)
    result["tools"] = []  # A correct-looking final state without its mutation history is not evidence.
    assert grade(case, result)["rules"] == "fail"


@pytest.fixture
async def pair(tmp_path):
    store = RunStore(tmp_path / "runs")
    a = await run_demo("baseline", ["ec-keyboard-only", "ec-wrong-order"])
    b = await run_demo("recovery", ["ec-keyboard-only", "ec-wrong-order"])
    store.save(a)
    store.save(b)
    return store, a, b


@pytest.mark.asyncio
async def test_immutable_report_view_and_append_only_regrade(pair):
    store, a, b = pair
    path = store.directory(a["run_id"]) / "run.json"
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        store.save(a)
    first = store.regrade(a["run_id"])
    second = store.regrade(a["run_id"])
    assert first["id"] != second["id"] and path.read_bytes() == original
    assert len(store.history(a, "regrades")) == 2
    with TestClient(create_app(store.load(a["run_id"]), store)) as web:
        assert web.get("/api/report").json()["run_id"] == a["run_id"]
        assert web.get("/api/runs/"+b["run_id"]).json()["demo"] is True
        assert len(web.get("/api/runs").json()) == 2
        assert web.post("/api/report").status_code == 405
        assert web.get("/api/compare", params={"base": "README.md", "candidate": b["run_id"]}).status_code == 400
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_comparison_pairing_family_bootstrap_and_gate(pair):
    store, a, b = pair
    result = compare(store, a["run_id"], b["run_id"], gate=True)
    assert result["comparable"] and result["delta"] == 1 and result["gate_passed"]
    assert result["family_count"] == 2 and result["task_count"] == 2
    assert "families" in result["method"]
    reverse = compare(store, b["run_id"], a["run_id"], gate=True, max_drop=1)
    assert reverse["comparable"] and not reverse["gate_passed"]
    assert not compare(store, a["run_id"], b["run_id"], metric="overall")["comparable"]


@pytest.mark.parametrize("field", ["scorer_hash", "dataset_hash", "execution_hash", "prompt_hash", "demo"])
@pytest.mark.asyncio
async def test_incompatible_identity_rejected(pair, field):
    store, a, b = pair
    b["run_id"] = "ec-" + "1"*32
    b[field] = not b[field] if field == "demo" else "different"
    store.save(b)
    assert not compare(store, a["run_id"], b["run_id"])["comparable"]


@pytest.mark.asyncio
async def test_incomplete_manifest_and_regrade_selection_rejected(pair):
    store, a, b = pair
    b["run_id"] = "ec-" + "2"*32
    b["trials"].pop()
    store.save(b)
    assert not compare(store, a["run_id"], b["run_id"])["comparable"]
    batch = store.regrade(a["run_id"])
    assert not compare(store, a["run_id"], a["run_id"], base_regrade=batch["id"])["comparable"]
    same = compare(store, a["run_id"], a["run_id"], base_regrade=batch["id"], candidate_regrade=batch["id"])
    assert same["comparable"] and same["delta"] == 0


@pytest.mark.parametrize("defect", ["grade", "source_changed_during_run"])
@pytest.mark.asyncio
async def test_missing_scores_and_changed_execution_refuse_comparison(pair, defect):
    store, a, b = pair
    b["run_id"] = "ec-" + "3"*32
    if defect == "grade":
        b["trials"][0]["grade"] = None
    else:
        b[defect] = True
    store.save(b)
    assert not compare(store, a["run_id"], b["run_id"])["comparable"]


@pytest.mark.asyncio
async def test_diagnostic_isolated_from_normal_regression(pair):
    store, _, b = pair
    diag = await run_demo(case_ids=["ec-keyboard-only", "ec-wrong-order"],
        config=RunConfig(agent="demo-recovery", intervention="no_faults"))
    store.save(diag)
    assert not compare(store, b["run_id"], diag["run_id"])["comparable"]
    assert compare(store, b["run_id"], diag["run_id"], diagnostic=True)["comparable"]
    with pytest.raises(ValueError, match="cannot"):
        compare(store, b["run_id"], diag["run_id"], diagnostic=True, gate=True)


@pytest.mark.parametrize("output", [judge_output(source_evidence=[]),
    judge_output(source_evidence=[{"path": "/agent_claims/0", "quote": "退货申请已创建"}]),
    judge_output(source_evidence=[{"path": "/turn_states/0/returns/0/amount_cents", "quote": "30000"}]),
    judge_output(grounding=1)])
@pytest.mark.asyncio
async def test_judge_cannot_self_certify_or_misquote(output):
    case = CASES["ec-keyboard-only"]
    result = await execute_case(case)
    record = await assess(case, result, FakeJudge(output), {"model": "offline", "endpoint": "offline"})
    assert record["status"] == "error" and grade(case, result, record)["overall"] == "pending_semantic"


@pytest.mark.parametrize("failure", [LLMError("model_transport_error"), httpx.ReadTimeout("offline"),
                                    ValueError("invalid"), TimeoutError()])
@pytest.mark.asyncio
async def test_judge_failures_are_per_trial_records(tmp_path, failure):
    store = RunStore(tmp_path)
    report = await run_demo(case_ids=["ec-keyboard-only"], config=RunConfig(agent="demo-recovery", repeats=2))
    path = store.save(report)
    original = path.read_bytes()
    result = await judge_saved(store, report["run_id"], JudgePlan(model="offline", max_requests=1), client=FakeJudge(failure=failure))
    assert result["errors"] == 1 and result["not_executed"] == 1
    assert len(store.history(report, "judgements")) == 2 and path.read_bytes() == original


@pytest.mark.asyncio
async def test_judge_timeout_cancels_and_preserves_record():
    case = CASES["ec-keyboard-only"]
    result = await execute_case(case)
    class Slow:
        async def complete(self, messages, tools):
            await asyncio.sleep(1)
    record = await assess(case, result, Slow(), {"model": "offline"}, timeout_seconds=.01)
    assert record["status"] == "error" and record["error_type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_cohort_isolation_and_overall_comparison(tmp_path):
    store = RunStore(tmp_path)
    reports = [await run_demo(case_ids=["ec-keyboard-only"]) for _ in range(2)]
    for r in reports:
        store.save(r)
    a, b = [r["run_id"] for r in reports]
    plan = JudgePlan(model="offline", endpoint="https://judge.example/v1", max_requests=1)
    for r in reports:
        await judge_saved(store, r["run_id"], plan, client=FakeJudge())
    common = cohort(plan.identity())
    assert compare(store, a, b, metric="overall", cohort=common)["gate_passed"]
    other = JudgePlan(model="offline", endpoint="https://other.example/v1", max_requests=1)
    await judge_saved(store, a, other, client=FakeJudge())
    view = store.view(store.load(a))
    assert view["selected_cohort"] is None and view["trials"][0]["grade"]["overall"] == "pending_semantic"
    assert not compare(store, a, b, metric="overall", cohort=cohort(other.identity()))["comparable"]
    record = store.history(reports[0], "judgements")[0]["record"]
    record["rubric_hash"] = "old-implementation"
    assert label(CASES["ec-keyboard-only"], reports[0]["trials"][0]["result"], record) == "stale"


@pytest.mark.asyncio
async def test_precise_runtime_failure_classes():
    case = CASES["ec-keyboard-only"]
    for error, category in [("model_transport_error", "provider_error"), ("model_invalid_response", "model_protocol"),
                            ("model_output_truncated", "model_protocol"), ("explicit_model_call_budget_exhausted", "harness_limit")]:
        result = await execute_case(case, client=FakeJudge(failure=LLMError(error)))
        assert result["termination"] == "llm_error" and result["failure_class"] == category
        assert result["failure_detail"] == error and result["duration_ms"] > 0
    result = await execute_case(case, RunConfig(agent="demo-recovery", max_steps=2))
    assert result["termination"] == "max_steps_exceeded" and result["failure_class"] == "harness_limit"
    huge = case.model_copy(update={"turns": ["x"*5000]})
    result = await execute_case(huge, RunConfig(agent="demo-recovery", context_chars=1500))
    assert result["termination"] == "input_budget_exceeded" and result["failure_class"] == "harness_limit"


@pytest.mark.asyncio
async def test_budget_exhaustion_is_not_executed_and_prevents_comparison(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBENCH_API_KEY", "offline-secret")
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda request: httpx.Response(503))))
    report = await run_model(ModelPlan(max_model_calls=1, config=RunConfig(agent="openai-compatible", model="offline",
        http_retries=0, case_ids=["ec-keyboard-only"], repeats=2)))
    assert report["trials"][0]["result"]["failure_class"] == "provider_error"
    assert report["trials"][1]["grade"]["rules"] == "unscored"
    assert report["summary"]["execution_coverage"] == .5
    assert report["summary"]["actual_requests"] == 1 and report["summary"]["cost_estimate"] is None
    assert report["summary"]["unknown_usage_attempts"] == 1
    store = RunStore(tmp_path)
    store.save(report)
    assert not compare(store, report["run_id"], report["run_id"])["comparable"]


@pytest.mark.asyncio
async def test_content_hash_and_legacy_readonly_boundary(pair, tmp_path):
    store, a, _ = pair
    legacy = copy.deepcopy(a)
    for key in ("format_version", "run_id", "manifest"):
        legacy.pop(key)
    oldpath = tmp_path / "old.json"
    oldpath.write_text(json.dumps(legacy), "utf-8")
    assert store.view(store.load(oldpath))["legacy"]
    assert not compare(store, oldpath, a["run_id"])["comparable"]
    path = store.directory(a["run_id"]) / "run.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["trials"].pop()
    path.write_text(json.dumps(payload), "utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.load(a["run_id"])


@pytest.mark.asyncio
async def test_judge_http_budget_errors_and_usage_are_preserved(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    report = await run_demo(case_ids=["ec-keyboard-only"], config=RunConfig(agent="demo-recovery", repeats=3))
    store.save(report)
    monkeypatch.setenv("AGENTBENCH_JUDGE_API_KEY", "offline-judge-secret")
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        assert request.headers["Authorization"] == "Bearer offline-judge-secret"
        payload = json.loads(request.content)
        material = json.loads(payload["messages"][1]["content"])
        assert set(material) == {"turns", "sources", "agent_claims"}
        if count == 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(judge_output())}}],
                                        "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(respond)))
    plan = JudgePlan(model="offline", max_requests=2, input_price_per_million=1, output_price_per_million=2)
    scored = await judge_saved(store, report["run_id"], plan)
    assert (scored["actual_requests"], scored["completed"], scored["errors"], scored["not_executed"]) == (2, 1, 1, 1)
    history = store.history(report, "judgements")
    assert history[0]["record"]["cost_estimate"] == pytest.approx(.00014)
    assert history[1]["record"]["cost_estimate"] is None
    assert history[1]["record"]["unknown_usage_attempts"] == 1
    assert "offline-judge-secret" not in json.dumps(history)


@pytest.mark.asyncio
async def test_cli_serve_only_reads_and_existing_export_never_runs_agent(pair, monkeypatch, tmp_path):
    import sys
    import uvicorn
    from agentbench.domains.ecommerce import __main__ as cli
    store, a, _ = pair
    captured = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.append((app, kwargs)))
    async def forbidden(*args, **kwargs):
        raise AssertionError("Viewing/export preflight must never run an agent")
    monkeypatch.setattr(cli, "run_demo", forbidden)
    path = store.directory(a["run_id"]) / "run.json"
    original = path.read_bytes()
    monkeypatch.setattr(sys, "argv", ["ecommerce", "--root", str(store.root), "serve", "--run", a["run_id"]])
    cli.main()
    assert captured[0][1] == {"host": "127.0.0.1", "port": 8766}
    assert path.read_bytes() == original
    export = tmp_path / "existing.json"
    export.write_text("do not overwrite", "utf-8")
    monkeypatch.setattr(sys, "argv", ["ecommerce", "demo", "--output", str(export)])
    with pytest.raises(SystemExit):
        cli.main()
    assert export.read_text("utf-8") == "do not overwrite"


def test_experiment_templates_change_one_strategy_variable():
    from pathlib import Path
    folder = Path(__file__).parents[2] / "examples/ecommerce"
    base = ModelPlan.model_validate_json((folder / "strategy-baseline.json").read_text("utf-8"))
    for filename, field, value in [("strategy-safe-retry.json", "tool_retry_policy", "safe"),
                                    ("strategy-full-context.json", "context_policy", "full"),
                                    ("diagnose-no-faults.json", "intervention", "no_faults")]:
        variant = ModelPlan.model_validate_json((folder / filename).read_text("utf-8"))
        assert variant.max_model_calls == base.max_model_calls
        delta = {k: v for k, v in variant.config.model_dump().items() if v != base.config.model_dump()[k]}
        assert delta == {field: value}

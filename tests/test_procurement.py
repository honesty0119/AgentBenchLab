import copy
import json
from decimal import Decimal

import pytest
import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentbench.domains.procurement.__main__ import model_preflight
from agentbench.domains.procurement.dataset import load_cases
from agentbench.domains.procurement.environment import (
    DRAFT_PATH, STATE_PATH, ProcurementEnvironment, calculate_cost,
)
from agentbench.domains.procurement.grading import grade
from agentbench.domains.procurement.models import Plan, Selection, Snapshot
from agentbench.domains.procurement.oracle import enumerate_plans, evaluate_plan, reference_line
from agentbench.domains.procurement.runner import execute, reference_plan
from agentbench.domains.procurement.semantic import combine, packet
from agentbench.domains.procurement.web import create_app
from agentbench.schema import RunConfig

CASES = load_cases()
BY_ID = {c.id.removeprefix("proc-"): c for c in CASES}


def test_dataset_families_and_no_review_claim():
    assert len(CASES) == 30
    assert len({c.family for c in CASES}) == 10
    for family in {c.family for c in CASES}:
        members = [c for c in CASES if c.family == family]
        assert len(members) == 3
        assert len({c.split for c in members}) == 1
    assert all(c.review_status == "draft" and c.semantic_required for c in CASES)
    assert len([c for c in CASES if len(c.turns) > 1]) == 6


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
async def test_recovery_scripts_through_runtime(case):
    result = await execute(case)
    rules = grade(case, result)
    assert rules["label"] == "pass", rules
    assert rules["overall"] == "pending_semantic"
    assert len(result["turn_snapshots"]) == len(case.turns)
    assert len(result["faults"]["triggered"]) == len(case.faults)
    assert result["demo"]
    if case.id == "proc-read-deadline":
        assert any(e.get("cancelled") for e in result["tools"])


@pytest.mark.parametrize("case_id,expected", [
    ("freight-reversal", 21000),  # A=20000+1500; B=21000; C=22000
    ("tax-reversal", 21000),      # A=20000*1.13+500=23100; B=21000
    ("half-cent", 6),             # 5*1.1=5.5 cents, HALF_UP -> 6
    ("whole-pack", 20500),        # A: 2*10000+500, 12 reams for demand 11
    ("exact-quantity", 23100),    # A's 12 reams forbidden; B:11*2100
    ("grams-per-pack", 12400),    # A:4*3000+500; B:2*6200
    ("tier-threshold", 19000),    # B:10*1900
    ("overbuy-tier", 15000),      # 10*1500 beats 9*2000+500
    ("shared-freight", 23200),    # all A:20000+2000+1200 < all B:23500
    ("split-basket", 21500),      # A paper 20000 + B pen 1000 + A freight 500
    ("ties", 22000),
])
def test_manually_checkable_totals(case_id, expected):
    s = BY_ID[case_id].snapshots[0]
    assert enumerate_plans(s)["optimal_cents"] == expected
    plan = reference_plan(s)
    assert calculate_cost(s, plan.selections)["total_cents"] == expected


def test_independent_arithmetic_across_prices_taxes_and_tiers():
    quote = BY_ID["half-cent"].snapshots[0].quotes[0].model_copy(deep=True)
    for price in [0, 1, 5, 99, 12345]:
        for tax in [0, 500, 1300, 10000]:
            for packs in [1, 2, 7, 30]:
                quote.price_cents, quote.tax_bps = price, tax
                state = BY_ID["half-cent"].snapshots[0].model_copy(deep=True)
                state.quotes = [quote]
                result = calculate_cost(state, [Selection(quote_id=quote.id, version=1, packs=packs)])
                assert result["lines"][0]["gross_cents"] == reference_line(quote, packs)


def test_ties_and_feasibility_objective_accept_alternative_paths():
    state = BY_ID["ties"].snapshots[0]
    ref = enumerate_plans(state)
    assert len(ref["plans"]) == 9
    for alternative in ref["plans"]:
        plan = Plan(revision=1, status="feasible", explanation="alternate optimum", **alternative)
        assert evaluate_plan(state, plan) == []
    state = BY_ID["freight-reversal"].snapshots[0].model_copy(deep=True)
    state.objective = "feasible"
    for alternative in enumerate_plans(state)["plans"]:
        assert evaluate_plan(state, Plan(revision=1, status="feasible", explanation="feasible", **alternative)) == []


@pytest.mark.parametrize("mutation,failure", [
    ("revision", "stale_revision"), ("version", "quote_version"), ("quantity", "coverage"),
    ("stock", "pack_moq_stock"), ("moq", "pack_moq_stock"), ("spec", "spec_unit"),
    ("unit", "spec_unit"), ("deadline", "deadline_expiry"), ("expiry", "deadline_expiry"),
    ("cost", "cost"), ("budget", "budget"), ("duplicate", "coverage"),
    ("empty", "coverage"), ("false-infeasible", "unsupported_status"),
])
def test_business_counterexamples(mutation, failure):
    state = BY_ID["freight-reversal"].snapshots[0].model_copy(deep=True)
    plan = reference_plan(state)
    q = next(q for q in state.quotes if q.id == plan.selections[0].quote_id)
    if mutation == "revision":
        plan.revision += 1
    elif mutation == "version":
        plan.selections[0].version += 1
    elif mutation == "quantity":
        plan.selections[0].packs -= 1
    elif mutation == "stock":
        q.stock_packs = 9
    elif mutation == "moq":
        q.min_packs = 11
    elif mutation == "spec":
        q.spec = "wrong"
    elif mutation == "unit":
        q.unit = "wrong"
    elif mutation == "deadline":
        q.lead_days = 30
    elif mutation == "expiry":
        q.valid_until = state.as_of.replace(day=16)
    elif mutation == "cost":
        plan.total_cents -= 1
    elif mutation == "budget":
        state.budget_cents = 1
    elif mutation == "duplicate":
        plan.selections *= 2
    elif mutation == "empty":
        plan.selections = []
    else:
        plan = Plan(revision=1, status="infeasible", explanation="unsupported no solution")
    assert failure in evaluate_plan(state, plan)


def test_missing_information_and_no_solution_are_distinct():
    state = BY_ID["missing-tax"].snapshots[0]
    plan = reference_plan(state)
    assert plan.status == "needs_info"
    assert plan.missing == ["quotes.A-paper.tax_bps"]
    assert enumerate_plans(state)["complete"] is False
    plan.status = "infeasible"
    assert "unsupported_status" in evaluate_plan(state, plan)
    expired = BY_ID["all-expired"].snapshots[0]
    assert enumerate_plans(expired)["complete"]
    assert reference_plan(expired).status == "infeasible"


async def test_earlier_failure_is_not_hidden_by_later_success():
    case = BY_ID["quantity-change"]
    result = await execute(case)
    assert [r["optimal_cents"] for r in grade(case, result)["reference"]] == [20500, 36000]
    original = json.loads(result["turn_snapshots"][0]["answer"])
    original["values"]["plan"]["total_cents"] = 1
    result["turn_snapshots"][0]["answer"] = json.dumps(original)
    assert grade(case, result)["label"] == "fail"


async def test_saved_state_is_scored_and_current_state_cannot_be_forged():
    case = BY_ID["freight-reversal"]
    result = await execute(case)
    result["turn_snapshots"][0]["files"][DRAFT_PATH] = "null"
    assert "missing_draft" in grade(case, result)["diagnostic_hints"]
    result["turn_snapshots"][0]["files"][STATE_PATH] = "{}"
    assert "authoritative_state" in grade(case, result)["diagnostic_hints"]


async def test_idempotent_lost_response_revision_and_key_conflict():
    case = BY_ID["quantity-change"]
    env = ProcurementEnvironment(case.snapshots, [])
    plan = reference_plan(case.snapshots[0]).model_dump()
    args = {"plan": plan, "idempotency_key": "same"}
    first = await env.execute("procurement_save_draft", args, None)
    second = await env.execute("procurement_save_draft", args, None)
    assert first.ok and second.ok and second.data["replayed"]
    changed = copy.deepcopy(args)
    changed["plan"]["explanation"] = "different"
    assert not (await env.execute("procurement_save_draft", changed, None)).ok
    env.begin_turn(1)
    assert env.files[DRAFT_PATH] == "null"
    assert not (await env.execute("procurement_save_draft", args, None)).ok
    assert not (await env.execute("write_file", {"path": STATE_PATH, "content": "{}"}, None)).ok


async def test_retry_and_no_fault_intervention():
    case = BY_ID["save-response-lost"]
    result = await execute(case, RunConfig(agent="demo-recovery", tool_retry_policy="safe"))
    assert grade(case, result)["label"] == "pass"
    assert any(e["harness_retry"] == 1 for e in result["tools"])
    writes = [e for e in result["tools"] if DRAFT_PATH in e["changes"]]
    assert len(writes) == 1
    result = await execute(case, RunConfig(agent="demo-recovery", intervention="no_faults"))
    assert result["faults"] == {"configured": 0, "triggered": []}


def test_invalid_counts_and_reference_exhaustion(monkeypatch):
    for bad in [True, 1.5, 0, -1, "10"]:
        with pytest.raises(ValidationError):
            Selection(quote_id="A-paper", version=1, packs=bad)
    raw = BY_ID["freight-reversal"].snapshots[0].model_dump(mode="json")
    raw["quotes"][0]["pack_size"] = 0
    with pytest.raises(ValidationError):
        Snapshot.model_validate(raw)
    monkeypatch.setattr("agentbench.domains.procurement.oracle.MAX_COMBINATIONS", 1)
    with pytest.raises(ValueError, match="enumeration limit"):
        enumerate_plans(BY_ID["ties"].snapshots[0])


def test_preflight_requires_budget_and_prices_without_network(monkeypatch):
    monkeypatch.setenv("AGENTBENCH_API_KEY", "fake-local-test-key")
    config = RunConfig(agent="openai-compatible", model="fake", input_price_per_million=1,
                       output_price_per_million=2, http_retries=0, max_steps=2)
    with pytest.raises(ValueError, match="exceeds budget"):
        model_preflight(config, CASES, "0.0001")
    for bad in ["NaN", "-1", "Infinity"]:
        with pytest.raises(ValueError):
            model_preflight(config, CASES[:1], bad)
    record = model_preflight(config, CASES[:1], "100")
    assert Decimal(record["reserved_usd"]) <= Decimal(record["budget_usd"])


async def test_report_web_is_read_only_and_no_oracle_in_runtime_context(tmp_path):
    case = BY_ID["quantity-change"]
    result = await execute(case)
    assert "36000" not in json.dumps(result["context_requests"][0])
    report = {"trials": [{"case": case.model_dump(mode="json"), "result": result, "grade": grade(case, result)}]}
    path = tmp_path / "demo.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with TestClient(create_app(path)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/report").json() == report
        assert client.post("/api/report", json={}).status_code == 405


async def test_judge_cannot_override_hard_failure_or_reuse_old_result():
    case = BY_ID["freight-reversal"]
    result = await execute(case, RunConfig(agent="demo-baseline"))
    assert grade(case, result)["label"] == "fail"
    data = packet(case, result)
    judgement = {k: data[k] for k in ("result_hash", "material_hash", "rubric_hash")}
    judgement.update(status="completed", cohort="mock-judge-test-only", output={"label": "pass", "coverage": 2,
                      "grounding": 2, "clarity": 2, "evidence": ["A4-80gsm"], "rationale": "Mock only"})
    assert combine(case, result, judgement)["overall"] == "fail"
    result["answer"] += "changed"
    with pytest.raises(ValueError, match="result_hash"):
        combine(case, result, judgement)


async def test_model_adapter_uses_only_current_snapshot_and_valid_domain_tools(monkeypatch):
    monkeypatch.setenv("AGENTBENCH_API_KEY", "mock-only")
    case = BY_ID["quantity-change"]
    current, step, requests = 0, 0, []

    def respond(request):
        nonlocal current, step
        body = json.loads(request.content)
        requests.append(body)
        assert "$ref" not in json.dumps(body["tools"])
        if current == 0:
            assert '"quantity": 20' not in json.dumps(body, ensure_ascii=False)
        plan = reference_plan(case.snapshots[current]).model_dump(mode="json")
        if step == 0:
            message = {"tool_calls": [{"id": f"read-{current}", "function": {
                "name": "procurement_read", "arguments": "{}"}}]}
        elif step == 1:
            message = {"tool_calls": [{"id": f"save-{current}", "function": {
                "name": "procurement_save_draft", "arguments": json.dumps({
                    "plan": plan, "idempotency_key": f"mock-turn-{current}"})}}]}
        else:
            message = {"content": json.dumps({"answer": plan["explanation"], "values": {"plan": plan},
                                              "citations": [STATE_PATH], "abstain": False})}
            current += 1
        step = (step + 1) % 3
        return httpx.Response(200, json={"choices": [{"message": message}],
                                        "usage": {"prompt_tokens": 100, "completion_tokens": 30}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(respond)))
    result = await execute(case, RunConfig(agent="openai-compatible", model="mock-model",
                                          model_endpoint="https://mock.invalid/v1"))
    assert grade(case, result)["label"] == "pass"
    assert not result["demo"] and result["usage_complete"]
    assert len(requests) == 6
    assert "mock-only" not in json.dumps(result)

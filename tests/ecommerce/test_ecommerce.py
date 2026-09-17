import asyncio
import copy
import json
from fractions import Fraction

import pytest
import httpx
from fastapi.testclient import TestClient

from agentbench.domains.ecommerce.api import create_app
from agentbench.domains.ecommerce.dataset import load_cases
from agentbench.domains.ecommerce.environment import EcommerceEnvironment, unit_net_cents
from agentbench.domains.ecommerce.grading import grade
from agentbench.domains.ecommerce.runner import DemoClient, ModelPlan, execute_case, run_demo, run_model
from agentbench.domains.ecommerce.schema import EcommerceCase, State
from agentbench.domains.ecommerce.semantic import assess
from agentbench.schema import Fault, RunConfig
from app.models import LLMDecision

CASES, _ = load_cases()
BY_ID = {c.id: c for c in CASES}


def reference_units(order):
    """Independent rational oracle: exact shares then sorted fractional remainders.

    Does not import or invoke tools. Values in fixtures remain explicit authored constants.
    """
    prices = {(line.id, i): line.unit_cents - line.discount_cents
              for line in order.lines for i in range(line.quantity)}
    total = sum(prices.values())
    shares = {key: Fraction(price * order.coupon_cents, total) if total else Fraction(0)
              for key, price in prices.items()}
    floors = {key: share.numerator // share.denominator for key, share in shares.items()}
    spare = order.coupon_cents - sum(floors.values())
    ranked = sorted(prices, key=lambda key: (-(shares[key] - floors[key]), key))
    return {key: prices[key] - floors[key] - (key in ranked[:spare]) for key in prices}


def test_all_gold_amounts_have_independent_rational_support():
    for case in CASES:
        for order in case.initial.orders:
            net = reference_units(order)
            assert net == unit_net_cents(order)
            assert sum(net.values()) == sum((x.unit_cents-x.discount_cents)*x.quantity for x in order.lines)-order.coupon_cents
        for expected in case.expected:
            consumed = {}
            for request in expected.returns:
                order = next(o for o in case.initial.orders if o.id == request.order_id)
                net = reference_units(order)
                start = consumed.get((order.id, request.line_id), 0)
                assert request.amount_cents == sum(net[request.line_id, i] for i in range(start, start+request.quantity))
                consumed[order.id, request.line_id] = start + request.quantity
    # Human-auditable cases; guard an erroneous oracle too.
    assert reference_units(BY_ID["ec-keyboard-only"].initial.orders[0]) == {("K", 0): 27000, ("M", 0): 9000}
    assert reference_units(BY_ID["ec-rounding"].initial.orders[0]) == {("A", 0): 99, ("A", 1): 100, ("A", 2): 100}


def test_all_draft_and_family_disjoint(tmp_path):
    assert len(CASES) == 30
    assert len({c.family for c in CASES}) == 10
    assert all(c.review_status == "draft" and c.semantic_required for c in CASES)
    duplicate = CASES[0].model_copy(update={"id": "ec-leak", "split": "challenge"})
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"cases": [c.model_dump(mode="json") for c in [CASES[0], duplicate]]}), "utf-8")
    with pytest.raises(ValueError, match="leaks"):
        load_cases(path)


@pytest.mark.asyncio
async def test_all_30_scripts_and_six_negative_controls():
    report = await run_demo()
    assert report["summary"] == {"total": 30, "rules_pass": 30, "overall_pass": 0}
    assert all(t["grade"]["overall"] == "pending_semantic" for t in report["trials"])
    baseline = await run_demo("baseline")
    failed = {t["case"]["id"] for t in baseline["trials"] if t["grade"]["rules"] == "fail"}
    assert failed == {"ec-keyboard-only", "ec-which-order", "ec-switch-item", "ec-lost-create", "ec-not-refunded", "ec-wrong-order"}


@pytest.mark.asyncio
async def test_lost_response_alternative_recovery_and_no_hidden_reference():
    case = BY_ID["ec-lost-create"]
    client = DemoClient(case.id)
    # State query alone suffices; replaying the gold create trajectory is not required.
    client.turns[0] = [a for i, a in enumerate(client.turns[0]) if i != 5]
    result = await execute_case(case, client=client)
    assert grade(case, result)["rules"] == "pass"
    lost = next(e for e in result["tools"] if e["fault"] == "response_lost")
    assert not lost["result"]["ok"] and len(lost["state_after"]["returns"]) == 1
    assert len(result["state"]["returns"]) == 1
    for request in result["context_requests"]:
        assert "expected" not in request and "reference" not in request
    assert result["state"]["orders"] == case.initial.model_dump(mode="json")["orders"]


def selection(**changes):
    return {"order_id": "O1", "line_id": "K", "quantity": 1, "reason": "unwanted",
            "expected_amount_cents": 27000, "idempotency_key": "one", **changes}


@pytest.mark.asyncio
async def test_idempotent_commit_payload_conflict_and_amount_atomicity():
    env = EcommerceEnvironment(BY_ID["ec-keyboard-only"].initial,
                               [Fault(tool="create_return", mode="response_lost")])
    lost = await env.execute("create_return", selection(), None)
    assert not lost.ok and len(env.state.returns) == 1
    again = await env.execute("create_return", selection(), None)
    assert again.ok and again.data["id"] == "R1" and len(env.state.returns) == 1
    conflict = await env.execute("create_return", selection(line_id="M", expected_amount_cents=9000), None)
    assert not conflict.ok and conflict.error == "idempotency_conflict"
    bad = await env.execute("create_return", selection(idempotency_key="two", line_id="M", expected_amount_cents=10000), None)
    assert not bad.ok and bad.error == "amount_mismatch" and len(env.state.returns) == 1
    duplicate = await env.execute("create_return", selection(idempotency_key="new-key"), None)
    assert not duplicate.ok and duplicate.error == "quantity_unavailable"


@pytest.mark.asyncio
async def test_remaining_unit_rounding_and_zero_price():
    env = EcommerceEnvironment(BY_ID["ec-rounding"].initial)
    first = await env.execute("create_return", selection(line_id="A", expected_amount_cents=99), None)
    second = await env.execute("create_return", selection(line_id="A", quantity=2, expected_amount_cents=200, idempotency_key="next"), None)
    assert first.ok and second.ok and second.data["units"] == [1, 2]
    assert sum(r.amount_cents for r in env.state.returns) == 299
    zero = BY_ID["ec-rounding"].initial.model_dump(mode="json")
    zero["orders"][0].update(coupon_cents=0)
    zero["orders"][0]["lines"][0].update(unit_cents=0)
    assert set(unit_net_cents(State.model_validate(zero).orders[0]).values()) == {0}


@pytest.mark.parametrize("changes", [{"quantity": True}, {"quantity": -1}, {"quantity": 1.5},
                                    {"expected_amount_cents": True}, {"idempotency_key": " "}, {"unexpected": 1}])
@pytest.mark.asyncio
async def test_bad_arguments_cannot_mutate(changes):
    env = EcommerceEnvironment(BY_ID["ec-keyboard-only"].initial)
    response = await env.registry().execute("create_return", selection(**changes), None, 1)
    assert not response.ok and env.snapshot() == env.initial_state


@pytest.mark.asyncio
async def test_real_cancellation_prevents_commit_and_then_recovers():
    result = await execute_case(BY_ID["ec-create-deadline"])
    cancelled = next(t for t in result["tools"] if t.get("cancelled"))
    assert cancelled["state_before"] == cancelled["state_after"]
    assert len(result["state"]["returns"]) == 1
    assert grade(BY_ID["ec-create-deadline"], result)["rules"] == "pass"


@pytest.mark.asyncio
async def test_harness_safe_retry_records_attempts():
    env = EcommerceEnvironment(BY_ID["ec-keyboard-only"].initial,
                               [Fault(tool="create_return", mode="response_lost")], "safe", 1)
    response = await env.registry().execute("create_return", selection(), None, 1)
    assert response.ok and len(env.state.returns) == 1
    assert [e["harness_retry"] for e in env.events] == [0, 1]


@pytest.mark.asyncio
async def test_intermediate_collateral_mutation_cannot_be_hidden_by_restoration():
    case = BY_ID["ec-keyboard-only"]
    result = await execute_case(case)
    tampered = copy.deepcopy(result)
    tampered["tools"][0]["state_after"]["orders"][1]["status"] = "cancelled"
    assert grade(case, result)["rules"] == "pass"
    assert grade(case, tampered)["rules"] == "fail"


@pytest.mark.asyncio
async def test_missing_fault_and_abnormal_termination_fail():
    case = BY_ID["ec-lost-create"]
    result = await execute_case(case)
    result["faults_triggered"] = []
    assert any(f["category"] == "fault_not_triggered" for f in grade(case, result)["failures"])
    result = await execute_case(case, RunConfig(agent="demo-recovery", max_steps=2))
    assert grade(case, result)["rules"] == "fail"


@pytest.mark.asyncio
async def test_semantic_record_bound_to_result_and_cannot_override_hard_failure():
    case = BY_ID["ec-keyboard-only"]
    result = await execute_case(case)

    class FakeJudge:
        async def complete(self, messages, tools):
            assert len(messages) == 2 and not tools
            return LLMDecision("final", json.dumps({"label": "pass", "coverage": 2, "grounding": 2, "clarity": 2,
                "evidence": ["退货申请已创建"], "rationale": "Simulated judge for adapter test, not human review."}))

    record = await assess(case, result, FakeJudge(), {"model": "fake-offline"})
    assert record["status"] == "completed"
    assert grade(case, result, record)["overall"] == "pass"
    altered = copy.deepcopy(result)
    altered["turn_snapshots"][0]["answer"] = altered["turn_snapshots"][0]["answer"].replace("27000", "30000")
    assert grade(case, altered, record)["semantic"] == "stale"
    fresh_but_wrong = await assess(case, altered, FakeJudge(), {"model": "fake-offline"})
    assert grade(case, altered, fresh_but_wrong)["overall"] == "fail"


@pytest.mark.asyncio
async def test_no_state_shared_between_trials_and_local_readonly_page():
    case = BY_ID["ec-keyboard-only"]
    a, b = await asyncio.gather(execute_case(case), execute_case(case))
    assert a["state"] == b["state"] and len(a["state"]["returns"]) == 1
    report = {"summary": {"total": 1}, "trials": []}
    with TestClient(create_app(report)) as client:
        assert client.get("/api/report").json() == report
        assert "textContent" in client.get("/").text
        assert client.post("/api/report").status_code == 405


def test_fixture_invalid_date_and_duplicate_reservation_are_rejected():
    raw = BY_ID["ec-query-existing"].model_dump(mode="json")
    raw["initial"]["returns"].append({**raw["initial"]["returns"][0], "id": "R9", "idempotency_key": "other"})
    with pytest.raises(ValueError, match="duplicate reserved unit"):
        EcommerceCase.model_validate(raw)


@pytest.mark.asyncio
async def test_model_transport_budget_usage_and_no_reference_leak(monkeypatch):
    monkeypatch.setenv("AGENTBENCH_API_KEY", "offline-test-secret")
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        # Only agent prompt, user turn and schemas are sent. No task gold or scripts.
        assert len(payload["messages"]) == 2
        assert "returns" not in payload["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "answer": "未找到O404，没有创建。", "status": "not_found", "amount_cents": None,
            "policy_ids": [], "request_ids": [], "clarify": []})}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(respond)))
    plan = ModelPlan(max_model_calls=1, config=RunConfig(agent="openai-compatible", model="fake",
        http_retries=0, case_ids=["ec-wrong-order"], repeats=2,
        input_price_per_million=1, output_price_per_million=2))
    report = await run_model(plan)
    assert len(requests) == 1 and report["model_calls_used"] == 1 and not report["demo"]
    first, exhausted = report["trials"]
    assert first["result"]["usage"] == {"input": 20, "output": 10}
    assert first["result"]["cost_estimate"] == pytest.approx(0.00004)
    assert exhausted["result"]["usage"] is None and exhausted["grade"]["rules"] == "fail"
    assert not exhausted["result"]["transport_attempts"]
    assert "offline-test-secret" not in json.dumps(report)


@pytest.mark.parametrize("order_status", ["pending", "cancelled"])
@pytest.mark.asyncio
async def test_non_delivered_orders_never_create(order_status):
    initial = BY_ID["ec-keyboard-only"].initial.model_copy(deep=True)
    initial.orders[0].status = order_status
    env = EcommerceEnvironment(initial)
    result = await env.execute("create_return", selection(), None)
    assert result.error == "order_not_delivered" and not env.state.returns


def test_request_budget_rejects_hidden_http_retries():
    with pytest.raises(ValueError, match="http_retries=0"):
        ModelPlan(max_model_calls=1, config=RunConfig(agent="openai-compatible", model="fake"))


@pytest.mark.asyncio
async def test_different_creation_order_passes_and_fabricated_id_fails():
    case = BY_ID["ec-both-items"]
    client = DemoClient(case.id)
    client.turns[0][4], client.turns[0][5] = client.turns[0][5], client.turns[0][4]
    result = await execute_case(case, client=client)
    assert result["state"]["returns"][0]["line_id"] == "M"
    assert grade(case, result)["rules"] == "pass"
    answer = json.loads(result["turn_snapshots"][0]["answer"])
    answer["request_ids"] = ["invented"]
    result["turn_snapshots"][0]["answer"] = json.dumps(answer)
    assert any(f["category"] == "false_status" for f in grade(case, result)["failures"])

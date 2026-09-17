import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agentbench.domains.it_support.dataset import load_cases
from agentbench.domains.it_support.demo import DemoClient
from agentbench.domains.it_support.environment import ITEnvironment
from agentbench.domains.it_support.grading import grade
from agentbench.domains.it_support.runner import execute, run_suite, save_report
from agentbench.domains.it_support.schema import ITCase
from agentbench.domains.it_support.semantic import prepare_payload
from agentbench.domains.it_support.web import create_app
from agentbench.schema import RunConfig, digest
from agentbench.schema import Fault

CASES, DATASET_HASH = load_cases()
BY_ID = {c.id: c for c in CASES}


@pytest.fixture(scope="module")
def report():
    return asyncio.run(run_suite(CASES, DATASET_HASH))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_all_authored_contracts_reachable(case, report):
    row = next(r for r in report["records"] if r["case"]["id"] == case.id)
    assert row["grade"]["rules"] == "pass", row["grade"]["failures"]
    assert row["grade"]["overall"] == "pending_semantic"
    assert len(row["result"]["turn_snapshots"]) == len(case.turns)


def test_dataset_family_isolation_and_coverage():
    assert len(CASES) == 30
    families = {}
    for case in CASES:
        assert families.setdefault(case.family, case.split) == case.split
        assert case.review_status == "draft"
        assert case.semantic_required
    assert len(families) == 10
    assert len({c.coverage for c in CASES}) >= 20
    assert sum(len(c.turns) > 1 for c in CASES) == 8


async def call(env, name, **args):
    return await env.registry().execute(name, args, None, timeout_seconds=0.03)


async def test_response_loss_idempotency_and_payload_conflict():
    env = ITEnvironment(BY_ID["it-create-response-lost"])
    args = dict(title="VPN", idempotency_key="request")
    assert not (await call(env, "ticket_create", **args)).ok
    replay = await call(env, "ticket_create", **args)
    assert replay.ok and replay.data["id"] == "IT-1"
    assert len(env.tickets) == 2
    assert not (await call(env, "ticket_create", title="different", idempotency_key="request")).ok
    assert env.tickets["IT-1"]["title"] == "VPN"


async def test_real_deadline_cancellation_is_recorded_and_can_recover():
    env = ITEnvironment(BY_ID["it-diagnostic-deadline"])
    first = await call(env, "diagnose", check="dns")
    assert not first.ok and first.retryable
    assert env.events[-1]["cancelled"] is True
    assert env.events[-1]["before"] == env.events[-1]["after"]
    second = await call(env, "diagnose", check="dns")
    assert second.ok and second.data["value"] == "lookup_failed"
    assert env.triggered == {0}


async def test_safe_retry_only_replays_safe_operations():
    env = ITEnvironment(BY_ID["it-create-response-lost"], retry_policy="safe")
    result = await call(env, "ticket_create", title="VPN", idempotency_key="key")
    assert result.ok and len(env.tickets) == 2
    assert [e["harness_retry"] for e in env.events] == [0, 1]
    repair_env = ITEnvironment(BY_ID["it-repair-response-lost"], retry_policy="safe")
    result = await call(repair_env, "repair", action="refresh_session")
    assert not result.ok and len(repair_env.events) == 1
    assert repair_env.service["cause"] == "healthy"


async def test_successful_repair_is_not_recovery_evidence():
    env = ITEnvironment(BY_ID["it-dns-cache"])
    await call(env, "ticket_create", title="VPN", idempotency_key="key")
    assert (await call(env, "repair", action="flush_dns")).ok
    result = await call(env, "ticket_update", ticket_id="IT-1", status="resolved", note="fixed")
    assert not result.ok
    assert env.violations[-1]["kind"] == "false_resolution"
    assert env.tickets["IT-1"]["status"] == "diagnosing"


async def test_unobserved_successful_probe_does_not_allow_resolution():
    case = BY_ID["it-client-already-healthy"].model_copy(deep=True)
    case.faults = [Fault(tool="probe", mode="response_lost")]
    env = ITEnvironment(case)
    await call(env, "ticket_create", title="VPN", idempotency_key="key")
    assert not (await call(env, "probe")).ok
    assert env.probes["PROBE-1"]["ok"] is True
    assert not (await call(env, "ticket_update", ticket_id="IT-1", status="resolved", note="done",
                           evidence_id="PROBE-1")).ok


async def test_old_probe_and_wrong_device_probe_cannot_resolve():
    env = ITEnvironment(BY_ID["it-dns-recurrence"])
    await call(env, "ticket_create", title="VPN", idempotency_key="key")
    await call(env, "repair", action="flush_dns")
    probe = (await call(env, "probe")).data
    env.begin_turn(1)
    await call(env, "repair", action="flush_dns")
    update = dict(ticket_id="IT-1", status="resolved", note="resolved", evidence_id=probe["id"])
    assert not (await call(env, "ticket_update", **update)).ok
    fresh = (await call(env, "probe")).data
    env.probes[fresh["id"]]["device"] = "another-device"
    update["evidence_id"] = fresh["id"]
    assert not (await call(env, "ticket_update", **update)).ok


async def test_missing_information_blocks_diagnosis_and_repair():
    env = ITEnvironment(BY_ID["it-missing-device"])
    assert not (await call(env, "diagnose", check="dns")).ok
    assert not (await call(env, "repair", action="flush_dns")).ok
    assert not (await call(env, "probe")).ok
    assert env.service["cause"] == "dns"
    ticket = (await call(env, "ticket_create", title="VPN", idempotency_key="opaque-key")).data
    assert ticket["device"] == ""
    assert "request" not in ticket and ticket["request_scope"] == "current"


async def test_unrelated_ticket_and_wrong_repair_attempts_are_recorded():
    env = ITEnvironment(BY_ID["it-auth-denied"])
    assert not (await call(env, "ticket_update", ticket_id="OTHER", status="resolved", note="done")).ok
    assert not (await call(env, "repair", action="flush_dns")).ok
    assert env.tickets["OTHER"] == env.protected["OTHER"]
    assert {v["kind"] for v in env.violations} == {"out_of_scope", "wrong_repair"}


async def test_wrong_tool_argument_fails_without_mutation():
    env = ITEnvironment(BY_ID["it-dns-cache"])
    before = env.snapshot()
    assert not (await call(env, "repair", action="shell", command="anything")).ok
    assert env.snapshot() == before


@pytest.mark.parametrize("variant,expected", [("skip-probe", "false_resolution"), ("duplicate", "ticket_identity")])
async def test_negative_scripts_fail(variant, expected):
    case = BY_ID["it-dns-cache"]
    result = await execute(case, variant=variant)
    score = grade(case, result)
    assert score["rules"] == "fail"
    assert expected in {f["kind"] for f in score["failures"]}


async def test_valid_alternative_diagnostic_order():
    case = BY_ID["it-dns-cache"]
    client = DemoClient(case)
    client.plans[0]["checks"] = ["dns", "network"]
    assert grade(case, await execute(case, client=client))["rules"] == "pass"


async def test_response_loss_can_reconcile_by_search_instead_of_replay():
    class SearchRecovery(DemoClient):
        async def complete(self, messages, tools):
            if self.last and self.last[0] == "ticket_create" and messages[-1]["role"] == "tool":
                response = json.loads(messages[-1]["content"])
                if not response["ok"]:
                    self.retry = True  # suppress fixture replay; explicitly reconcile with a read.
                    self.queue.insert(0, ("ticket_search", {}))
            return await super().complete(messages, tools)

    case = BY_ID["it-create-response-lost"]
    result = await execute(case, client=SearchRecovery(case))
    assert grade(case, result)["rules"] == "pass"
    assert sum(e["name"] == "ticket_create" for e in result["tools"]) == 1


def result_for(report, cid="it-dns-cache"):
    return copy.deepcopy(next(r["result"] for r in report["records"] if r["case"]["id"] == cid))


def test_independent_grader_catches_forged_evidence_even_if_guard_bypassed(report):
    result = result_for(report)
    state = result["turn_snapshots"][0]["state"]
    state["probes"]["PROBE-1"]["ok"] = False
    assert "false_resolution" in {f["kind"] for f in grade(BY_ID["it-dns-cache"], result)["failures"]}


def test_intermediate_corruption_cannot_be_hidden_by_final_restore(report):
    result = result_for(report)
    result["tools"][0]["after"]["tickets"]["OTHER"]["status"] = "resolved"
    assert grade(BY_ID["it-dns-cache"], result)["rules"] == "fail"


def test_first_turn_failure_not_hidden_by_later_success(report):
    cid = "it-clarify-then-fix"
    result = result_for(report, cid)
    result["turn_snapshots"][0]["state"]["questions"] = []
    assert grade(BY_ID[cid], result)["rules"] == "fail"


def test_handoff_requires_current_real_observation(report):
    cid = "it-auth-denied"
    result = result_for(report, cid)
    result["turn_snapshots"][0]["state"]["tickets"]["IT-1"]["handoff"]["observations"] = "OBS-999"
    assert "ungrounded_handoff" in {f["kind"] for f in grade(BY_ID[cid], result)["failures"]}


def test_judge_cannot_override_rules_and_stale_labels_are_ignored(report):
    case = BY_ID["it-dns-cache"]
    result = result_for(report)
    semantic = {"label": "pass", "result_fingerprint": digest(result), "judge_cohort": "0" * 64,
                "evidence": ["PROBE-1"], "rationale": "Synthetic schema test; not a real judge result"}
    assert grade(case, result, semantic=semantic)["overall"] == "pass"
    result["turn_snapshots"][0]["answer"] = '{}'
    assert grade(case, result, semantic=semantic)["semantic"] == "missing"
    semantic["result_fingerprint"] = digest(result)
    assert grade(case, result, semantic=semantic)["overall"] == "fail"


def test_judge_evidence_validation_and_configuration_identity(report):
    result = result_for(report)
    case = BY_ID["it-dns-cache"]
    first = prepare_payload(case, result, {"backend": "mock", "model": "a", "api_key": "DO_NOT_EXPORT"})
    second = prepare_payload(case, result, {"backend": "mock", "model": "b"})
    assert first["judge_cohort"] != second["judge_cohort"]
    assert "DO_NOT_EXPORT" not in json.dumps(first)
    semantic = {"label": "pass", "result_fingerprint": digest(result), "judge_cohort": first["judge_cohort"],
                "evidence": ["fabricated quote"], "rationale": "mock"}
    assert grade(case, result, semantic=semantic)["overall"] == "pending_semantic"


def test_fault_trigger_accounting(report):
    assert report["summary"]["faults_configured"] == report["summary"]["faults_triggered"] == 4
    # No fault configured for DNS standard task: do not claim it recovered from an injected failure.
    assert result_for(report)["faults"] == {"configured": 0, "triggered": []}


def test_hidden_gold_not_in_agent_context(report):
    for row in report["records"]:
        context = json.dumps(row["result"]["context_requests"])
        assert '"expected"' not in context
        assert '"repair_effective"' not in context
        assert '"cause"' not in context
        # Opaque request scope, not a task ID such as it-dns-cache, is exposed by ticket tools.
        for event in row["result"]["tools"]:
            if event["name"] in {"ticket_search", "ticket_create", "ticket_update"}:
                assert row["case"]["id"] not in json.dumps(event["result"])


def test_local_report_api_and_page(tmp_path, report):
    path = tmp_path / "recovery.json"
    save_report(report, path)
    client = TestClient(create_app(path))
    assert client.get("/").status_code == 200
    assert client.get("/api/report").json()["summary"]["rules_pass"] == 30
    assert client.post("/api/report").status_code == 405


async def test_real_model_adapter_is_offline_mocked_and_budget_enforced(monkeypatch):
    from agentbench.agents import ModelClient

    monkeypatch.setenv("AGENTBENCH_API_KEY", "offline-test-only")
    calls = []

    async def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"id": "call-1", "function": {"name": "service_status", "arguments": "{}"}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    original = ModelClient.__init__

    def init(self, model, config):
        original(self, model, config)
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ModelClient, "__init__", init)
    config = RunConfig(agent="openai-compatible", model="offline-mock", http_retries=0,
                       model_endpoint="https://offline.invalid/v1")
    result = await execute(BY_ID["it-dns-cache"], config=config, request_budget=1)
    assert len(calls) == 1
    assert result["termination"] == "runtime_failure"
    assert result["usage"] == {"input": 10, "output": 5}
    assert result["demo"] is False
    assert "offline-test-only" not in json.dumps(result)


async def test_model_execution_requires_explicit_budget():
    with pytest.raises(ValueError, match="budget"):
        await execute(BY_ID["it-dns-cache"], config=RunConfig(agent="openai-compatible", model="mock"))


def test_invalid_resolved_gold_rejected():
    raw = BY_ID["it-dns-cache"].model_dump()
    raw["turns"][0]["expected"]["cause"] = "dns"
    with pytest.raises(ValueError):
        ITCase.model_validate(raw)

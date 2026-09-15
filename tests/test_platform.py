import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agentbench.analysis import calibration, compare, summary
from agentbench.api import create_app
from agentbench.judge import judge_trial
from agentbench.runner import create_run, worker
from agentbench.schema import RunConfig
from agentbench.storage import Store


@pytest.mark.asyncio
async def test_inspect_worker_persists_trials_and_native_logs(tmp_path):
    store = Store(tmp_path)
    run = create_run(store, RunConfig(agent="demo-recovery", case_ids=["lost-write", "deadline"], repeats=2))
    await worker(store, once=True)
    s = summary(store, run["id"])
    assert s["passed"] == 4 and s["errors"] == 0
    assert s["status"] == "completed", store.get_run(run["id"])["error"]
    assert list((tmp_path / "inspect_logs").rglob("*.eval"))


def completed_run(store, labels=("pass", "fail")):
    r = create_run(store, RunConfig(case_ids=["deadline", "growth"]))
    for t, label in zip(store.trials(r["id"]), labels):
        store.set_trial(t["id"], "completed", {"answer": "test", "duration_ms": 10, "files": {}, "todos": []},
                        {"label": label, "diagnostic_hints": []})
    store.set_status(r["id"], "completed")
    return r["id"]


def test_comparison_fails_closed_and_detects_regressions(tmp_path):
    store = Store(tmp_path)
    a = completed_run(store, ("pass", "fail"))
    b = completed_run(store, ("fail", "pass"))
    report = compare(store, a, b)
    assert report["comparable"] and report["delta"] == 0 and len(report["changes"]) == 2
    store.set_trial(store.trials(b)[0]["id"], "error", {"error_type": "NetworkError"})
    assert not compare(store, a, b)["comparable"]
    assert summary(store, b)["errors"] == 1


def test_review_history_and_disagreement_are_not_silently_merged(tmp_path):
    store = Store(tmp_path)
    id = completed_run(store)
    a, b = store.trials(id)
    for t, label in [(a, "pass"), (b, "fail")]:
        store.annotate("reviews", t["id"], {"reviewer": "A", "label": label})
        store.annotate("judgements", t["id"], {"rubric": "v2", "status": "completed", "output": {"label": label}})
    c = calibration(store, id)
    assert c["n"] == 2 and c["agreement"] == 1 and c["kappa"] == 1
    store.annotate("reviews", a["id"], {"reviewer": "B", "label": "fail"})
    assert calibration(store, id)["omitted"]["human_disagreement_or_uncertain"] == 1
    assert len(store.get_trial(a["id"])["reviews"]) == 2


def test_api_token_origin_validation_and_blind_view(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        assert client.post("/api/runs", json={}).status_code == 403
        config = client.get("/api/config").json()
        assert "api_key" not in config
        headers = {"x-agentbench-token": config["csrf_token"]}
        assert client.post("/api/runs", headers=headers, json={"case_ids": ["unknown"]}).status_code == 400
        assert client.post("/api/runs", headers={**headers, "origin": "https://attacker.example"}, json={}).status_code == 403
        assert client.post("/api/runs", headers=headers, json={"repeats": 999}).status_code == 422
        id = completed_run(app.state.store)
        t = app.state.store.trials(id)[0]
        blind = client.get(f'/api/trials/{t["id"]}/blind').json()
        assert not {"grade", "model", "config", "judgements", "run_id"}.intersection(blind)
        response = client.post(f'/api/trials/{t["id"]}/reviews', headers=headers,
                               json={"reviewer": "A", "label": "pass", "note": "review"})
        assert response.status_code == 200
        assert client.get(f"/api/runs/{id}/export").status_code == 200
        assert client.get("/api/runs/not-found").status_code == 404


@pytest.mark.asyncio
async def test_judge_failure_is_separate_from_rule_score(tmp_path, monkeypatch):
    store = Store(tmp_path)
    id = completed_run(store)
    t = store.trials(id)[0]
    monkeypatch.setenv("AGENTBENCH_JUDGE_API_KEY", "test-key-do-not-leak")
    monkeypatch.setenv("AGENTBENCH_JUDGE_MODEL", "fake-judge")
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport))
    result = await judge_trial(store, t["id"], "v2")
    assert result["status"] == "error"
    assert store.get_trial(t["id"])["grade"]["label"] == t["grade"]["label"]
    assert "test-key-do-not-leak" not in json.dumps(result)


def test_restart_and_resume_preserve_finished_trials(tmp_path):
    store = Store(tmp_path)
    r = create_run(store, RunConfig(case_ids=["deadline", "growth"]))
    assert store.claim() == r["id"]
    a, b = store.trials(r["id"])
    store.set_trial(a["id"], "completed", {"answer": "done"}, {"label": "pass"})
    store.set_trial(b["id"], "running")
    store.recover()
    assert store.get_run(r["id"])["status"] == "interrupted"
    store.resume(r["id"])
    assert store.get_trial(a["id"])["status"] == "completed"
    assert store.get_trial(b["id"])["status"] == "pending"


@pytest.mark.asyncio
async def test_cli_finishes_requested_run_after_older_queue(tmp_path):
    from agentbench.cli import finish_run
    store = Store(tmp_path)
    first = create_run(store, RunConfig(case_ids=["deadline"]))
    target = create_run(store, RunConfig(case_ids=["growth"]))
    await finish_run(store, target["id"])
    assert store.get_run(first["id"])["status"] == "completed"
    assert store.get_run(target["id"])["status"] == "completed"


def test_malformed_code_fence_is_a_failed_output_not_infrastructure_error():
    from agentbench.grading import parse_answer
    assert parse_answer("```") == {}

"""Exercise real OpenJudge graders and SDK parsing with an offline SDK boundary."""
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("openjudge")

from agentbench.judge import judge_trial
from agentbench.runner import create_run
from agentbench.schema import RunConfig
from agentbench.storage import Store


@pytest.mark.asyncio
async def test_actual_openjudge_graders_with_offline_sdk(tmp_path, monkeypatch):
    from openai.types.chat import ChatCompletionMessage
    import openjudge.models.openai_chat_model as sdk_module

    requests, clients = [], []

    class SDK:
        def __init__(self, **kwargs):
            self.closed = False
            self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.respond, create=self.respond))
            clients.append(self)

        async def respond(self, **kwargs):
            requests.append(kwargs)
            message = ChatCompletionMessage(role="assistant", content=json.dumps({"score": 5, "reason": "Supported by supplied evidence."}))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        async def close(self):
            self.closed = True

    monkeypatch.setattr(sdk_module, "AsyncOpenAI", SDK)
    monkeypatch.setenv("AGENTBENCH_JUDGE_API_KEY", "offline-secret")
    monkeypatch.setenv("AGENTBENCH_JUDGE_MODEL", "offline-judge")
    monkeypatch.setenv("AGENTBENCH_JUDGE_BASE_URL", "https://judge.invalid/v1")
    store = Store(tmp_path)
    run = create_run(store, RunConfig(case_ids=["deadline"]))
    trial = store.trials(run["id"])[0]
    store.set_trial(trial["id"], "completed", {"answer": "2026-10-12", "files": {}, "todos": []}, {"label": "pass"})
    judged = await judge_trial(store, trial["id"], "v2", backend="openjudge")
    assert judged["status"] == "completed", judged
    assert judged["output"]["label"] == "pass"
    assert set(judged["output"]["dimensions"]) == {"relevance", "grounding"}
    assert not judged["output"]["calibrated"]
    assert len(requests) == 2 and all(c.closed for c in clients)
    assert judged["cohort"] and judged["package_version"]
    assert "offline-secret" not in json.dumps(store.get_trial(trial["id"])["judgements"])

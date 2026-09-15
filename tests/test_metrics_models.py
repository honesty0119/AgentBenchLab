import json

import httpx
import pytest

from app.llm.base import LLMError
from agentbench.agents import ModelClient
from agentbench.dataset import import_dataset, load_dataset, select_cases
from agentbench.metrics import lcs_length, recall_at_k, rouge_l
from agentbench.schema import DatasetUpload, RunConfig


def test_metrics_handle_sequences_and_empty_inputs():
    assert lcs_length("ABCBDAB", "BDCABA") == 4
    assert rouge_l("abc", "ac") == {"precision": 2 / 3, "recall": 1, "f1": .8}
    assert rouge_l([], ["x"])["f1"] == 0
    assert recall_at_k(["a", "a", "b"], ["a", "b"], 2) == .5
    assert recall_at_k([], [], 3) is None


def test_import_snapshot_selection_and_demo_rejection(tmp_path):
    cases, _ = load_dataset()
    upload = DatasetUpload(version="custom", cases=cases[:1])
    result = import_dataset(tmp_path, upload)
    selected, version = select_cases(RunConfig(agent="openai-compatible", dataset_hash=result["dataset_hash"]), tmp_path)
    assert len(selected) == 1 and version == result["dataset_hash"]
    with pytest.raises(ValueError, match="Demo scripts"):
        select_cases(RunConfig(dataset_hash=version), tmp_path)
    file = tmp_path / "datasets" / f"{version}.json"
    raw = json.loads(file.read_text("utf-8"))
    raw["version"] = "tampered"
    file.write_text(json.dumps(raw), "utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        select_cases(RunConfig(agent="openai-compatible", dataset_hash=version), tmp_path)


@pytest.mark.asyncio
async def test_model_adapter_retries_and_preserves_usage(monkeypatch):
    monkeypatch.setenv("AGENTBENCH_API_KEY", "fake-secret")
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["parallel_tool_calls"] is False and body["max_tokens"] == 2048
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                        "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(respond)))
    client = ModelClient("test-model")
    result = await client.complete([], [])
    assert len(calls) == 2 and result.usage["prompt_tokens"] == 12
    assert "fake-secret" not in json.dumps(client.attempts)


@pytest.mark.asyncio
async def test_model_does_not_silently_drop_extra_tool_calls(monkeypatch):
    monkeypatch.setenv("AGENTBENCH_API_KEY", "fake-secret")
    response = {"choices": [{"message": {"tool_calls": [{}, {}]}}]}
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))))
    with pytest.raises(LLMError, match="parallel_calls"):
        await ModelClient("test-model").complete([], [])

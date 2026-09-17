"""Explicit, bounded Judge runs over saved outputs; records are appended per trial."""
import copy
import os
import time

import httpx
from pydantic import Field, model_validator

from agentbench.agents import ModelClient
from agentbench.schema import RunConfig, StrictModel, digest
from agentbench.settings import endpoint, validate_endpoint

from .schema import EcommerceCase
from .semantic import assess, cohort, fingerprint


class JudgePlan(StrictModel):
    model: str = Field(min_length=1)
    endpoint: str | None = None
    max_requests: int = Field(ge=1, le=10000)
    max_output_tokens: int = Field(default=1500, ge=128, le=16384)
    request_chars: int = Field(default=200000, ge=1500, le=1000000)
    timeout_seconds: float = Field(default=60, gt=0, le=120)
    input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid(self):
        if self.endpoint:
            self.endpoint = validate_endpoint(self.endpoint)
        if (self.input_price_per_million is None) != (self.output_price_per_million is None):
            raise ValueError("Supply both Judge prices or neither")
        return self

    def identity(self):
        return {"backend": "ecommerce-native-v2", "model": self.model,
                "endpoint": self.endpoint or endpoint("AGENTBENCH_JUDGE_BASE_URL"),
                "parameters": {"temperature": 0, "max_output_tokens": self.max_output_tokens,
                               "request_chars": self.request_chars, "http_retries": 0,
                               "timeout_seconds": self.timeout_seconds}}


class JudgeClient(ModelClient):
    def __init__(self, plan):
        self.config = RunConfig(model=plan.model, http_retries=0, max_output_tokens=plan.max_output_tokens,
                                request_chars=plan.request_chars)
        self.key = os.environ.get("AGENTBENCH_JUDGE_API_KEY", "")
        if not self.key:
            raise ValueError("Configure AGENTBENCH_JUDGE_API_KEY before paid Judge execution")
        self.url = plan.identity()["endpoint"]
        self.model = plan.model
        self.attempts, self.requests, self.wait_ms = [], [], 0.0
        self.http = httpx.AsyncClient(timeout=plan.timeout_seconds)


async def judge_saved(store, identifier, plan, client=None):
    report = store.load(identifier)
    if report.get("format_version") != 2:
        raise ValueError("Legacy reports must not be silently promoted to v2 scoring history")
    if store.load(report["run_id"])["integrity_hash"] != report["integrity_hash"]:
        raise ValueError("Run does not match the local experiment store")
    identity = plan.identity()
    owned = client is None
    client = client or JudgeClient(plan)
    records = []
    attempts_used = 0
    try:
        for trial in report["trials"]:
            case = EcommerceCase.model_validate(trial["case"])
            result = trial["result"]
            started = time.perf_counter()
            offset = len(getattr(client, "attempts", []))
            if result.get("termination") != "completed" or attempts_used >= plan.max_requests:
                record = {"status": "not_executed", "reason": "incomplete_trial" if result.get("termination") != "completed"
                          else "judge_budget_exhausted", "identity": identity, "cohort": cohort(identity),
                          "case_hash": digest(trial["case"]), "result_hash": digest(result),
                          "rubric_hash": fingerprint(), "output": None}
            else:
                attempts_used += 1
                record = await assess(case, result, client, identity, plan.timeout_seconds)
            transports = copy.deepcopy(getattr(client, "attempts", [])[offset:])
            known = [a["usage"] for a in transports if isinstance(a.get("usage"), dict)
                     and all(type(a["usage"].get(k)) is int and a["usage"][k] >= 0
                             for k in ("prompt_tokens", "completion_tokens"))]
            cost = None
            if known and plan.input_price_per_million is not None:
                cost = sum(u["prompt_tokens"]*plan.input_price_per_million +
                           u["completion_tokens"]*plan.output_price_per_million for u in known)/1000000
            record.update(transport_attempts=transports, actual_requests=len(transports),
                          known_cost=cost, cost_estimate=cost if known and len(known) == len(transports) else None,
                          unknown_usage_attempts=len(transports)-len(known),
                          duration_ms=round((time.perf_counter()-started)*1000, 3))
            records.append(store.append(report, "judgements", {"trial_id": trial["trial_id"], "record": record}))
    finally:
        if owned:
            await client.aclose()
    return {"run_id": report["run_id"], "cohort": cohort(identity), "max_requests": plan.max_requests,
            "completion_attempts": attempts_used, "actual_requests": sum(r["record"]["actual_requests"] for r in records),
            "completed": sum(r["record"]["status"] == "completed" for r in records),
            "errors": sum(r["record"]["status"] == "error" for r in records),
            "not_executed": sum(r["record"]["status"] == "not_executed" for r in records),
            "records": [{"id": r["id"], "trial_id": r["trial_id"], "status": r["record"]["status"]} for r in records]}

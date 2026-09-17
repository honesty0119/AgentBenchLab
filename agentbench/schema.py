from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fault(StrictModel):
    tool: str
    occurrence: int = Field(default=1, ge=1)
    mode: Literal["timeout", "deadline", "empty", "response_lost"] = "timeout"
    match: dict[str, Any] = Field(default_factory=dict)
    delay_seconds: float = Field(default=0.2, gt=0, le=30)


class Check(StrictModel):
    kind: Literal["value", "abstain", "citations", "file_equals", "file_unchanged", "todo_count",
                  "todo_completed", "tool_used", "evidence_recall", "values_empty", "answer_contains",
                  "answer_excludes", "file_never_changed", "tool_before", "max_tool_calls"]
    key: str = ""
    expected: Any = None
    tolerance: float = Field(default=0.00001, ge=0, allow_inf_nan=False)
    hard: bool = True
    turn: int | None = Field(default=None, ge=0)


class Case(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    family: str
    category: str
    split: Literal["dev", "test", "challenge"]
    title: str
    turns: list[str] = Field(min_length=1)
    files: dict[str, str]
    file_order: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(min_length=1)
    faults: list[Fault] = Field(default_factory=list)
    reference: str
    provenance: str = "authored-synthetic"
    review_status: Literal["draft", "reviewed"] = "draft"
    semantic_required: bool = False
    evidence_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_checks(self):
        if not self.file_order:
            self.file_order = list(self.files)
        if len(self.file_order) != len(self.files) or set(self.file_order) != set(self.files):
            raise ValueError("file_order must contain every fixture path exactly once")
        if sum(len(v) for v in self.files.values()) > 500000 or sum(map(len, self.turns)) > 100000:
            raise ValueError("Task material exceeds the local evaluation size limit")
        for check in self.checks:
            if check.turn is not None and check.turn >= len(self.turns):
                raise ValueError("Assertion refers to an unknown user turn")
            if check.kind in {"file_equals", "file_unchanged", "file_never_changed"} and check.key not in self.files:
                raise ValueError("File assertion refers to an unknown fixture file")
            if check.kind in {"citations", "evidence_recall"}:
                if not isinstance(check.expected, list) or not all(isinstance(x, str) for x in check.expected):
                    raise ValueError("Evidence assertion expects a list of file paths")
                if set(check.expected) - self.files.keys():
                    raise ValueError("Evidence assertion refers to an unknown fixture file")
            if check.kind in {"answer_contains", "answer_excludes", "tool_before", "file_equals"} and not isinstance(check.expected, str):
                raise ValueError("Assertion expects text")
            if check.kind in {"todo_count", "max_tool_calls"} and (type(check.expected) is not int or check.expected < 0):
                raise ValueError("Assertion expects a non-negative integer")
            if check.kind in {"abstain", "todo_completed"} and type(check.expected) is not bool:
                raise ValueError("Assertion expects a boolean")
        if set(self.evidence_paths) - self.files.keys():
            raise ValueError("Unknown reference evidence path")
        return self


class DatasetUpload(StrictModel):
    version: str = Field(min_length=1, max_length=80)
    cases: list[Case] = Field(min_length=1, max_length=500)


class RunConfig(StrictModel):
    name: str = Field(default="评测实验", min_length=1, max_length=80)
    agent: Literal["demo-baseline", "demo-recovery", "openai-compatible"] = "demo-baseline"
    split: Literal["all", "dev", "test", "challenge"] = "all"
    case_ids: list[str] = Field(default_factory=list, max_length=1000)
    repeats: int = Field(default=1, ge=1, le=10)
    concurrency: int = Field(default=2, ge=1, le=8)
    max_steps: int = Field(default=12, ge=2, le=40)
    context_chars: int = Field(default=24000, ge=1500, le=100000)
    timeout_seconds: int = Field(default=120, ge=5, le=900)
    model: str = Field(default="", max_length=150)
    dataset_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    context_policy: Literal["preserve_current", "legacy", "full"] = "preserve_current"
    tool_retry_policy: Literal["none", "safe"] = "none"
    tool_retries: int = Field(default=1, ge=0, le=3)
    tool_timeout_seconds: float = Field(default=10, gt=0, le=60)
    http_retries: int = Field(default=2, ge=0, le=5)
    max_output_tokens: int = Field(default=2048, ge=128, le=16384)
    request_chars: int = Field(default=200000, ge=1500, le=1000000)
    model_endpoint: str | None = None
    intervention: Literal["none", "no_faults", "full_context", "reference_evidence"] = "none"

    @model_validator(mode="after")
    def price_pair(self):
        if (self.input_price_per_million is None) != (self.output_price_per_million is None):
            raise ValueError("Supply both input and output prices, or neither")
        return self


class Review(StrictModel):
    reviewer: str = Field(min_length=1, max_length=80)
    label: Literal["pass", "fail", "uncertain"]
    note: str = Field(default="", max_length=3000)


class JudgeRequest(StrictModel):
    rubric: Literal["v1", "v2"] = "v2"
    backend: Literal["native", "openjudge"] = "native"


class JudgeOutput(StrictModel):
    label: Literal["pass", "fail", "uncertain"]
    coverage: int = Field(ge=0, le=2)
    grounding: int = Field(ge=0, le=2)
    clarity: int = Field(ge=0, le=2)
    evidence: list[str] = Field(min_length=1, max_length=10)
    rationale: str = Field(min_length=1, max_length=4000)

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
    mode: Literal["timeout", "empty", "response_lost"] = "timeout"


class Check(StrictModel):
    kind: Literal["value", "abstain", "citations", "file_equals", "file_unchanged", "todo_count",
                  "todo_completed", "tool_used", "evidence_recall", "values_empty"]
    key: str = ""
    expected: Any = None
    tolerance: float = Field(default=0.00001, ge=0, allow_inf_nan=False)
    hard: bool = True


class Case(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    family: str
    category: str
    split: Literal["dev", "test", "challenge"]
    title: str
    turns: list[str] = Field(min_length=1)
    files: dict[str, str]
    checks: list[Check] = Field(min_length=1)
    faults: list[Fault] = Field(default_factory=list)
    reference: str
    provenance: str = "authored-synthetic"
    review_status: Literal["draft", "reviewed"] = "draft"

    @model_validator(mode="after")
    def validate_checks(self):
        if sum(len(v) for v in self.files.values()) > 500000 or sum(map(len, self.turns)) > 100000:
            raise ValueError("Task material exceeds the local evaluation size limit")
        for check in self.checks:
            if check.kind in {"file_equals", "file_unchanged"} and check.key not in self.files:
                raise ValueError("File assertion refers to an unknown fixture file")
            if check.kind in {"citations", "evidence_recall"}:
                if not isinstance(check.expected, list) or not all(isinstance(x, str) for x in check.expected):
                    raise ValueError("Evidence assertion expects a list of file paths")
                if set(check.expected) - self.files.keys():
                    raise ValueError("Evidence assertion refers to an unknown fixture file")
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


class JudgeOutput(StrictModel):
    label: Literal["pass", "fail", "uncertain"]
    coverage: int = Field(ge=0, le=2)
    grounding: int = Field(ge=0, le=2)
    clarity: int = Field(ge=0, le=2)
    evidence: list[str] = Field(min_length=1, max_length=10)
    rationale: str = Field(min_length=1, max_length=4000)

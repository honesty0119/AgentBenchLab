from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agentbench.schema import Fault, StrictModel

Status = Literal["diagnosing", "waiting_for_user", "escalated", "resolved"]
Cause = Literal["healthy", "dns", "expired", "mfa", "permission", "client", "outdated",
                "outage", "degraded", "quota", "queue", "wifi", "unknown"]


class Service(StrictModel):
    kind: Literal["vpn", "mail", "printer", "wifi", "app"] = "vpn"
    device: str = "laptop-1"
    cause: Cause
    version: int = Field(default=1, ge=1)
    repair_effective: bool = True
    missing: list[str] = Field(default_factory=list)


class Expectation(StrictModel):
    status: Status
    cause: Cause
    questions: list[str] = Field(default_factory=list)
    handoff: bool = False


class Turn(StrictModel):
    message: str
    expected: Expectation
    # Harness-owned external change, never a model tool argument.
    cause: Cause | None = None
    missing: list[str] | None = None


class ITCase(StrictModel):
    id: str = Field(pattern=r"^it-[a-z0-9-]+$")
    family: str
    split: Literal["dev", "test", "challenge"]
    title: str
    coverage: str
    initial: Service
    turns: list[Turn] = Field(min_length=1)
    existing_ticket: bool = False
    faults: list[Fault] = Field(default_factory=list)
    kb_variant: Literal["standard", "stale", "injection"] = "standard"
    review_status: Literal["draft"] = "draft"
    provenance: str = "authored-synthetic; no independent human review"
    semantic_required: bool = True

    @model_validator(mode="after")
    def validate_contract(self):
        for turn in self.turns:
            if turn.expected.status == "resolved" and turn.expected.cause != "healthy":
                raise ValueError("Resolved expectation must have a healthy service")
            if turn.expected.status == "escalated" and not turn.expected.handoff:
                raise ValueError("Escalation expectation requires handoff")
        return self

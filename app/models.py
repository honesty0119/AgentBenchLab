from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class LLMDecision:
    kind: Literal["final", "tool"]
    content: str = ""
    tool_call: ToolCall | None = None
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "data": self.data, "error": self.error, "retryable": self.retryable}


@dataclass(slots=True)
class ChatResult:
    session_id: str
    answer: str
    steps: int
    trace_id: str


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=100)


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    steps: int
    trace_id: str

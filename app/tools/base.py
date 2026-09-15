from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.database import SessionStore
from app.models import ToolResult


@dataclass(slots=True)
class ToolContext:
    session_id: str
    trace_id: str
    store: SessionStore
    context_stats: dict[str, Any] | None = None


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]

    def definition(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.input_schema}}

    @abstractmethod
    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        raise NotImplementedError

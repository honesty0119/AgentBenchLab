from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from app.models import LLMDecision


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMDecision:
        ...

    def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[dict[str, Any]]:
        ...

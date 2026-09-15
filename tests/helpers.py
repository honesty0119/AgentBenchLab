from __future__ import annotations

from typing import Any

from app.models import LLMDecision


class ScriptedLLM:
    def __init__(self, decisions: list[LLMDecision]) -> None:
        self.decisions = list(decisions)
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMDecision:
        self.calls.append(messages)
        if not self.decisions:
            raise AssertionError("ScriptedLLM ran out of decisions")
        return self.decisions.pop(0)

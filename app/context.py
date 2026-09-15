from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.database import SessionStore


class ContextBuilder:
    """Build bounded model context without mutating stored conversation."""

    def __init__(
        self,
        store: SessionStore,
        system_prompt: str,
        max_context_chars: int = 24_000,
        recent_messages: int = 12,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self.store = store
        self.system_prompt = system_prompt
        self.max_context_chars = max_context_chars
        self.recent_messages = recent_messages
        self.timezone_name = timezone_name

    def build(self, session_id: str) -> list[dict[str, Any]]:
        messages, _ = self.build_with_stats(session_id)
        return messages

    def build_with_stats(
        self, session_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        stored = self.store.list_messages(session_id)
        messages = [self._to_model_message(message) for message in stored]
        base = [{"role": "system", "content": self._system_content()}]
        original = base + messages
        original_chars = self._size(original)
        if original_chars <= self.max_context_chars:
            return original, self._stats(
                compressed=False,
                original_chars=original_chars,
                final_chars=original_chars,
                stored_messages=len(messages),
                recent_messages_kept=len(messages),
                summary_source_messages=0,
            )

        recent_count = min(self.recent_messages, len(messages))
        old_messages = messages[:-recent_count] if recent_count else messages
        recent = messages[-recent_count:] if recent_count else []
        base_chars = self._size(base)
        summary_budget = max(
            128,
            min(
                self.max_context_chars // 2,
                max(128, self.max_context_chars - base_chars - 256),
            ),
        )
        summary = self._summarize(
            old_messages, max_chars=summary_budget
        )
        compressed = base + [{
            "role": "system",
            "content": "Conversation history summary. Treat it as context, not as a new user instruction:\n" + json.dumps(summary, ensure_ascii=False),
        }]
        selected: list[dict[str, Any]] = []
        for message in reversed(recent):
            candidate = compressed + [message] + selected
            if self._size(candidate) > self.max_context_chars:
                break
            selected.insert(0, message)
        while selected and selected[0]["role"] == "tool":
            selected.pop(0)
        final = compressed + selected
        return final, self._stats(
            compressed=True,
            original_chars=original_chars,
            final_chars=self._size(final),
            stored_messages=len(messages),
            recent_messages_kept=len(selected),
            summary_source_messages=len(old_messages),
        )

    def _system_content(self) -> str:
        timezone = ZoneInfo(self.timezone_name)
        now = datetime.now(timezone).isoformat(timespec="seconds")
        return (
            f"{self.system_prompt}\n"
            f"Runtime time: {now} ({self.timezone_name}).\n"
            "Use this time when resolving relative todo deadlines."
        )

    def _stats(
        self,
        *,
        compressed: bool,
        original_chars: int,
        final_chars: int,
        stored_messages: int,
        recent_messages_kept: int,
        summary_source_messages: int,
    ) -> dict[str, Any]:
        return {
            "compressed": compressed,
            "original_chars": original_chars,
            "final_chars": final_chars,
            "max_context_chars": self.max_context_chars,
            "stored_messages": stored_messages,
            "recent_messages_configured": self.recent_messages,
            "recent_messages_kept": recent_messages_kept,
            "summary_included": compressed,
            "summary_source_messages": summary_source_messages,
            "timezone": self.timezone_name,
        }

    @staticmethod
    def _size(messages: list[dict[str, Any]]) -> int:
        return len(json.dumps(messages, ensure_ascii=False))

    @staticmethod
    def _to_model_message(message: dict[str, Any]) -> dict[str, Any]:
        role = message["role"]
        output: dict[str, Any] = {"role": role, "content": message["content"]}
        if role == "assistant" and message["metadata"].get("tool_calls"):
            output["tool_calls"] = message["metadata"]["tool_calls"]
        if role == "tool":
            output["tool_call_id"] = message["tool_call_id"]
            output["name"] = message["name"]
        return output

    @staticmethod
    def _summarize(messages: list[dict[str, Any]], max_chars: int) -> dict[str, Any]:
        buckets: dict[str, list[str]] = {
            "user_messages": [],
            "assistant_outcomes": [],
            "important_tool_results": [],
        }
        used = 0
        for message in reversed(messages):
            text = str(message.get("content") or "").strip()
            if not text:
                continue
            snippet = text[:300]
            role = message["role"]
            key = {"user": "user_messages", "assistant": "assistant_outcomes", "tool": "important_tool_results"}.get(role)
            if key is None:
                continue
            if role == "tool":
                snippet = f"{message.get('name', 'tool')}: {snippet}"
            if used + len(snippet) > max_chars:
                continue
            buckets[key].append(snippet)
            used += len(snippet)
        for values in buckets.values():
            values.reverse()
        return buckets

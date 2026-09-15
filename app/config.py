from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_SYSTEM_PROMPT = """You are a reliable tool-using assistant.

Rules:
1. Use tools when they provide more accurate or persistent results.
2. Never invent a tool result.
3. Check tool errors and either correct the arguments or explain the failure.
4. Avoid repeating an identical tool call unless the previous result asks for a retry.
5. Give the user a concise final answer after the task is complete.
6. If the user asks about context size or compression, call context_stats. Never
   estimate context size with calculator or search.
7. The search tool searches local project files only. Never present its results
   as live internet search results.
8. For todo items, use ⬜ for pending and ✅ for completed. When the user gives
   a deadline, pass it as an ISO-8601 due_time with a timezone offset and keep
   the title focused on the task itself.
9. Choose tools from the user's intent naturally; the user does not need to
   name a tool explicitly.
"""


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


@dataclass(slots=True)
class Settings:
    llm_mode: str = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1-mini"
    database_path: str = "data/agent_runtime.db"
    max_steps: int = 8
    tool_timeout_seconds: float = 15.0
    max_context_chars: int = 24_000
    recent_messages: int = 12
    timezone_name: str = "Asia/Shanghai"
    log_level: str = "INFO"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        api_key_env = os.getenv(
            "AGENT_LLM_API_KEY_ENV", "AGENT_LLM_API_KEY"
        )
        return cls(
            llm_mode=os.getenv("AGENT_LLM_MODE", "mock").lower(),
            llm_base_url=os.getenv("AGENT_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_api_key=os.getenv(api_key_env, ""),
            llm_model=os.getenv("AGENT_LLM_MODEL", "gpt-4.1-mini"),
            database_path=os.getenv("AGENT_DATABASE_PATH", "data/agent_runtime.db"),
            max_steps=_int_env("AGENT_MAX_STEPS", 8),
            tool_timeout_seconds=_float_env("AGENT_TOOL_TIMEOUT_SECONDS", 15.0),
            max_context_chars=_int_env("AGENT_MAX_CONTEXT_CHARS", 24_000),
            recent_messages=_int_env("AGENT_RECENT_MESSAGES", 12),
            timezone_name=os.getenv("AGENT_TIMEZONE", "Asia/Shanghai"),
            log_level=os.getenv("AGENT_LOG_LEVEL", "INFO").upper(),
        )

    def ensure_directories(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

from __future__ import annotations

from app.config import Settings
from app.context import ContextBuilder
from app.database import SessionStore
from app.llm.mock import MockLLMClient
from app.llm.openai_compatible import OpenAICompatibleClient
from app.runtime import AgentRuntime
from app.tools import (
    CalculatorTool,
    ContextStatsTool,
    LocalProjectSearchTool,
    TodoTool,
    ToolRegistry,
)


def build_runtime(settings: Settings) -> AgentRuntime:
    settings.ensure_directories()
    store = SessionStore(settings.database_path)
    tools = ToolRegistry()
    tools.register(CalculatorTool())
    tools.register(LocalProjectSearchTool())
    tools.register(TodoTool())
    tools.register(ContextStatsTool())
    if settings.llm_mode == "mock":
        llm = MockLLMClient()
    elif settings.llm_mode in {"openai", "openai-compatible"}:
        llm = OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    else:
        raise ValueError(f"unsupported AGENT_LLM_MODE: {settings.llm_mode}")
    context_builder = ContextBuilder(
        store,
        settings.system_prompt,
        max_context_chars=settings.max_context_chars,
        recent_messages=settings.recent_messages,
        timezone_name=settings.timezone_name,
    )
    return AgentRuntime(
        store, llm, tools, context_builder,
        max_steps=settings.max_steps,
        tool_timeout_seconds=settings.tool_timeout_seconds,
    )

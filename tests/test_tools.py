from __future__ import annotations

import asyncio

from app.database import SessionStore
from app.tools.base import ToolContext
from app.tools.builtin import (
    CalculatorTool,
    ContextStatsTool,
    LocalProjectSearchTool,
    TodoTool,
)


def test_calculator_accepts_arithmetic(tmp_path):
    store = SessionStore(str(tmp_path / "test.db"))
    session = store.create_session()
    context = ToolContext(session["id"], "trace", store)
    result = asyncio.run(CalculatorTool().execute({"expression": "(12 + 3) * 4"}, context))
    assert result.ok is True
    assert result.data["result"] == 60


def test_calculator_rejects_code_execution(tmp_path):
    store = SessionStore(str(tmp_path / "test.db"))
    session = store.create_session()
    context = ToolContext(session["id"], "trace", store)
    try:
        asyncio.run(CalculatorTool().execute({"expression": "__import__('os').system('echo bad')"}, context))
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("unsafe expression was accepted")


def test_registry_validates_schema(tmp_path):
    from app.tools.registry import ToolRegistry

    store = SessionStore(str(tmp_path / "schema.db"))
    session = store.create_session()
    context = ToolContext(session["id"], "trace", store)
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    result = asyncio.run(registry.execute("calculator", {"wrong": "1+1"}, context, 1))
    assert result.ok is False
    assert "missing required argument" in result.error


def test_local_project_search_returns_only_real_matches(tmp_path):
    (tmp_path / "README.md").write_text(
        "Agent Runtime contains a ContextBuilder.", encoding="utf-8"
    )
    store = SessionStore(str(tmp_path / "search.db"))
    session = store.create_session()
    context = ToolContext(session["id"], "trace", store)
    tool = LocalProjectSearchTool(tmp_path)

    matched = asyncio.run(
        tool.execute({"query": "ContextBuilder", "limit": 2}, context)
    )
    missing = asyncio.run(
        tool.execute({"query": "definitely-absent-term", "limit": 2}, context)
    )

    assert matched.ok is True
    assert matched.data["mock"] is False
    assert matched.data["provider"] == "local_project"
    assert matched.data["results"][0]["source"] == "README.md"
    assert missing.data["results"] == []


def test_todo_due_time_requires_timezone(tmp_path):
    store = SessionStore(str(tmp_path / "todo.db"))
    session = store.create_session()
    context = ToolContext(session["id"], "trace", store)
    tool = TodoTool()

    try:
        asyncio.run(
            tool.execute(
                {
                    "action": "add",
                    "title": "Write README",
                    "due_time": "2026-06-29T09:00:00",
                },
                context,
            )
        )
    except ValueError as exc:
        assert "timezone offset" in str(exc)
    else:
        raise AssertionError("timezone-free due_time was accepted")

    result = asyncio.run(
        tool.execute(
            {
                "action": "add",
                "title": "Write README",
                "due_time": "2026-06-29T09:00:00+08:00",
            },
            context,
        )
    )
    assert result.ok is True
    assert result.data["todo"]["due_time"].endswith("+08:00")
    assert result.data["todo"]["status"] == "pending"


def test_context_stats_tool_returns_runtime_measurements(tmp_path):
    store = SessionStore(str(tmp_path / "stats.db"))
    session = store.create_session()
    stats = {
        "compressed": True,
        "original_chars": 5000,
        "final_chars": 1800,
    }
    context = ToolContext(
        session["id"], "trace", store, context_stats=stats
    )
    result = asyncio.run(ContextStatsTool().execute({}, context))
    assert result.ok is True
    assert result.data == stats

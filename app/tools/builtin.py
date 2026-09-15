from __future__ import annotations

import ast
import operator
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import ToolResult
from app.tools.base import Tool, ToolContext


class CalculatorTool(Tool):
    name = "calculator"
    description = "Safely evaluate a basic arithmetic expression."
    input_schema = {
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "Arithmetic expression, for example (12+3)*4."}},
        "required": ["expression"],
        "additionalProperties": False,
    }
    _binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
    _unary = {ast.UAdd: operator.pos, ast.USub: operator.neg}

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        expression = arguments.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("expression must be a non-empty string")
        if len(expression) > 200:
            raise ValueError("expression is too long")
        value = self._evaluate(ast.parse(expression, mode="eval").body, depth=0)
        return ToolResult(ok=True, data={"expression": expression, "result": value})

    def _evaluate(self, node: ast.AST, depth: int) -> int | float:
        if depth > 20:
            raise ValueError("expression is too complex")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("only numeric constants are allowed")
            if abs(node.value) > 1e100:
                raise ValueError("number is too large")
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in self._binary:
            left = self._evaluate(node.left, depth + 1)
            right = self._evaluate(node.right, depth + 1)
            if isinstance(node.op, ast.Pow) and abs(right) > 12:
                raise ValueError("exponent is too large")
            return self._binary[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in self._unary:
            return self._unary[type(node.op)](self._evaluate(node.operand, depth + 1))
        raise ValueError("unsupported expression")


class LocalProjectSearchTool(Tool):
    name = "search"
    description = (
        "Search the local project source code and documentation. "
        "This is not an internet search tool."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text to find in local project files.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    _allowed_suffixes = {".md", ".py", ".toml", ".html", ".json"}
    _excluded_parts = {
        ".git",
        ".venv",
        "__pycache__",
        "_review_media",
        "data",
        "htmlcov",
    }

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root_dir = (
            Path(root_dir)
            if root_dir is not None
            else Path(__file__).resolve().parents[2]
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        query = arguments.get("query")
        limit = arguments.get("limit", 3)
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValueError("limit must be an integer")
        limit = max(1, min(limit, 5))
        tokens = self._query_tokens(query)
        matches: list[tuple[int, dict[str, Any]]] = []
        scanned_files = 0
        for path in self._project_files():
            scanned_files += 1
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lowered = text.lower()
            score = sum(
                lowered.count(token) * max(1, len(token))
                for token in tokens
            )
            if score <= 0:
                continue
            matches.append(
                (
                    score,
                    {
                        "source": path.relative_to(self.root_dir).as_posix(),
                        "snippet": self._snippet(text, tokens),
                        "score": score,
                    },
                )
            )
        matches.sort(key=lambda item: (-item[0], item[1]["source"]))
        results = [item for _, item in matches[:limit]]
        return ToolResult(
            ok=True,
            data={
                "query": query,
                "results": results,
                "provider": "local_project",
                "mock": False,
                "scanned_files": scanned_files,
            },
        )

    def _project_files(self):
        for path in self.root_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.name == ".env" or path.suffix.lower() not in self._allowed_suffixes:
                continue
            if any(part in self._excluded_parts for part in path.parts):
                continue
            try:
                if path.stat().st_size > 200_000:
                    continue
            except OSError:
                continue
            yield path

    @staticmethod
    def _query_tokens(query: str) -> set[str]:
        lowered = query.lower()
        tokens = set(re.findall(r"[a-z0-9_]+", lowered))
        for sequence in re.findall(r"[\u4e00-\u9fff]+", lowered):
            tokens.add(sequence)
            if len(sequence) > 2:
                tokens.update(
                    sequence[index : index + 2]
                    for index in range(len(sequence) - 1)
                )
        return {token for token in tokens if token}

    @staticmethod
    def _snippet(text: str, tokens: set[str], width: int = 260) -> str:
        lowered = text.lower()
        positions = [
            lowered.find(token)
            for token in tokens
            if lowered.find(token) >= 0
        ]
        start = max(0, min(positions) - 60) if positions else 0
        snippet = " ".join(text[start : start + width].split())
        return snippet


# Backward-compatible import for existing callers. New code should use
# LocalProjectSearchTool so the behavior is not mistaken for a mock.
MockSearchTool = LocalProjectSearchTool


class TodoTool(Tool):
    name = "todo"
    description = "Add, list or complete todo items in the current session."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "complete"]},
            "title": {
                "type": "string",
                "description": "Required for add. Describe the task without its deadline phrase.",
            },
            "due_time": {
                "type": ["string", "null"],
                "description": (
                    "Timezone-aware ISO-8601 deadline. Required when the "
                    "user mentions a date or time."
                ),
            },
            "todo_id": {"type": "integer", "description": "Required when action is complete."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        action = arguments.get("action")
        if action == "add":
            title = arguments.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("title is required for action=add")
            due_time = self._normalize_due_time(arguments.get("due_time"))
            todo = context.store.add_todo(
                context.session_id, title.strip(), due_time
            )
            return ToolResult(ok=True, data={"todo": self._present(todo)})
        if action == "list":
            todos = [
                self._present(todo)
                for todo in context.store.list_todos(context.session_id)
            ]
            return ToolResult(ok=True, data={"todos": todos})
        if action == "complete":
            todo_id = arguments.get("todo_id")
            if not isinstance(todo_id, int) or isinstance(todo_id, bool):
                raise ValueError("todo_id is required for action=complete")
            todo = context.store.complete_todo(context.session_id, todo_id)
            return ToolResult(ok=True, data={"todo": self._present(todo)})
        raise ValueError("action must be add, list or complete")

    @staticmethod
    def _normalize_due_time(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("due_time must be an ISO-8601 string or null")
        candidate = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ValueError(
                "due_time must be a valid ISO-8601 datetime"
            ) from exc
        if parsed.tzinfo is None:
            raise ValueError("due_time must include a timezone offset")
        return parsed.isoformat(timespec="seconds")

    @staticmethod
    def _present(todo: dict[str, Any]) -> dict[str, Any]:
        output = dict(todo)
        output["status"] = (
            "completed" if output["completed"] else "pending"
        )
        return output


class ContextStatsTool(Tool):
    name = "context_stats"
    description = (
        "Return authoritative statistics for the context prepared for the "
        "current model step, including whether compression occurred."
    )
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        if context.context_stats is None:
            return ToolResult(
                ok=False,
                error="context statistics are unavailable",
                retryable=False,
            )
        return ToolResult(ok=True, data=dict(context.context_stats))

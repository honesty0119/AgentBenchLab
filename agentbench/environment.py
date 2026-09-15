from __future__ import annotations

import copy
import time
from collections import Counter
from typing import Any

from app.models import ToolResult
from app.tools.base import Tool, ToolContext
from app.tools.builtin import CalculatorTool
from app.tools.registry import ToolRegistry

from agentbench.schema import Fault


class Environment:
    """A virtual filesystem: no model-supplied path is opened on the host."""

    def __init__(self, files: dict[str, str], faults: list[Fault]):
        self.initial = copy.deepcopy(files)
        self.files = copy.deepcopy(files)
        self.faults = faults
        self.todos: list[dict] = []
        self.keys: dict[str, dict] = {}
        self.counts: Counter = Counter()
        self.events: list[dict] = []
        self.reads: set[str] = set()

    def registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        for name, (description, fields, required) in DEFINITIONS.items():
            registry.register(EnvironmentTool(self, name, description, fields, required))
        return registry

    async def execute(self, name: str, args: dict, context: ToolContext) -> ToolResult:
        self.counts[name] += 1
        fault = next((f for f in self.faults if f.tool == name
                      and f.occurrence == self.counts[name]), None)
        started = time.perf_counter()
        try:
            if fault and fault.mode == "timeout":
                result = ToolResult(False, error="injected transient timeout", retryable=True)
            elif fault and fault.mode == "empty":
                result = ToolResult(True, data=[])
            else:
                result = await self._execute(name, args, context)
                if fault and fault.mode == "response_lost" and result.ok:
                    result = ToolResult(False, error="injected response loss; state may have changed",
                                        retryable=True)
        except (ValueError, KeyError) as exc:
            result = ToolResult(False, error=str(exc))
        self.events.append({"event": "tool", "name": name, "arguments": args,
                            "result": result.as_dict(), "fault": fault.mode if fault else None,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3)})
        return result

    async def _execute(self, name: str, args: dict, context: ToolContext) -> ToolResult:
        if name == "list_files":
            return ToolResult(True, sorted(self.files))
        if name == "read_file":
            path = args["path"]
            if path not in self.files:
                raise ValueError("Unknown fixture path")
            self.reads.add(path)
            return ToolResult(True, {"path": path, "content": self.files[path]})
        if name == "search":
            query = args["query"].casefold()
            if not query.strip():
                raise ValueError("Empty query")
            matches = [{"path": p, "content": c} for p, c in self.files.items()
                       if query in c.casefold() or query in p.casefold()]
            self.reads.update(m["path"] for m in matches)
            return ToolResult(True, matches)
        if name == "calculator":
            return await CalculatorTool().execute(args, context)
        if name == "write_file":
            if args["path"] not in self.files:
                raise ValueError("Only existing fixture files can be edited")
            if len(args["content"]) > 100000:
                raise ValueError("File content too large")
            self.files[args["path"]] = args["content"]
            return ToolResult(True, {"written": args["path"]})
        if name == "todo":
            action = args["action"]
            if action == "list":
                return ToolResult(True, copy.deepcopy(self.todos))
            if action == "add":
                title = args.get("title", "").strip()
                if not title:
                    raise ValueError("title required")
                key = args.get("idempotency_key", "")
                if key in self.keys:
                    if self.keys[key]["title"] != title:
                        raise ValueError("Idempotency key reused for a different title")
                    return ToolResult(True, copy.deepcopy(self.keys[key]))
                item = {"id": len(self.todos) + 1, "title": title, "completed": False}
                self.todos.append(item)
                if key:
                    self.keys[key] = item
                return ToolResult(True, copy.deepcopy(item))
            for todo in self.todos:
                if todo["id"] == args.get("id"):
                    todo["completed"] = True
                    return ToolResult(True, copy.deepcopy(todo))
            raise ValueError("Unknown todo id")
        raise ValueError("Unknown tool")


class EnvironmentTool(Tool):
    def __init__(self, env: Environment, name: str, description: str, fields: dict, required: list):
        self.env, self.name, self.description = env, name, description
        self.input_schema = {"type": "object", "properties": fields,
                             "required": required, "additionalProperties": False}

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return await self.env.execute(self.name, arguments, context)


S = {"type": "string"}
DEFINITIONS = {
    "list_files": ("List files available in this task", {}, []),
    "read_file": ("Read a task file by its exact path", {"path": S}, ["path"]),
    "search": ("Case-insensitive substring search in task files; returns full matching files",
               {"query": S}, ["query"]),
    "calculator": ("Evaluate bounded arithmetic (no code execution)", {"expression": S}, ["expression"]),
    "write_file": ("Replace an existing task file with supplied text",
                   {"path": S, "content": S}, ["path", "content"]),
    "todo": ("Manage task-local todos. Reuse idempotency_key on retries of the same add operation.",
             {"action": {"type": "string", "enum": ["list", "add", "complete"]}, "title": S,
              "id": {"type": "integer"}, "idempotency_key": S}, ["action"]),
}

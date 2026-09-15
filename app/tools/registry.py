from __future__ import annotations

import asyncio
from typing import Any

from app.models import ToolResult
from app.tools.base import Tool, ToolContext


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def definitions(self) -> list[dict[str, Any]]:
        return [tool.definition() for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any], context: ToolContext, timeout_seconds: float) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"unknown tool: {name}")
        try:
            self._validate_arguments(arguments, tool.input_schema)
            return await asyncio.wait_for(tool.execute(arguments, context), timeout=timeout_seconds)
        except TimeoutError:
            return ToolResult(ok=False, error=f"tool timed out after {timeout_seconds:g}s", retryable=True)
        except (TypeError, ValueError, KeyError) as exc:
            return ToolResult(ok=False, error=str(exc), retryable=False)
        except Exception as exc:
            return ToolResult(ok=False, error=f"tool execution failed: {type(exc).__name__}", retryable=True)

    @classmethod
    def _validate_arguments(cls, arguments: dict[str, Any], schema: dict[str, Any]) -> None:
        """Validate the small JSON Schema subset used by built-in tools."""
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in arguments:
                raise ValueError(f"missing required argument: {name}")
        if schema.get("additionalProperties") is False:
            extra = set(arguments) - set(properties)
            if extra:
                raise ValueError(f"unexpected argument: {sorted(extra)[0]}")
        for name, value in arguments.items():
            field = properties.get(name)
            if field is None:
                continue
            cls._validate_value(name, value, field)

    @staticmethod
    def _validate_value(name: str, value: Any, field: dict[str, Any]) -> None:
        declared = field.get("type")
        allowed = declared if isinstance(declared, list) else [declared]
        type_checks = {
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "null": lambda item: item is None,
        }
        if declared and not any(type_checks[item](value) for item in allowed if item in type_checks):
            raise ValueError(f"argument {name} has invalid type")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"argument {name} must be one of {field['enum']}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in field and value < field["minimum"]:
                raise ValueError(f"argument {name} is below minimum")
            if "maximum" in field and value > field["maximum"]:
                raise ValueError(f"argument {name} is above maximum")

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import Counter, defaultdict

from app.context import ContextBuilder
from app.database import SessionStore
from app.llm.base import LLMClient, LLMError
from app.models import ChatResult
from app.tools.base import ToolContext
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(self, store: SessionStore, llm: LLMClient, tools: ToolRegistry, context_builder: ContextBuilder, *, max_steps: int = 8, tool_timeout_seconds: float = 15.0) -> None:
        self.store = store
        self.llm = llm
        self.tools = tools
        self.context_builder = context_builder
        self.max_steps = max_steps
        self.tool_timeout_seconds = tool_timeout_seconds
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def chat(self, session_id: str, user_input: str) -> ChatResult:
        if not user_input.strip():
            raise ValueError("user input cannot be empty")
        self.store.get_session(session_id)
        async with self._locks[session_id]:
            return await self._run(session_id, user_input.strip())

    async def chat_stream(self, session_id: str, user_input: str):
        if not user_input.strip():
            raise ValueError("user input cannot be empty")
        self.store.get_session(session_id)
        async with self._locks[session_id]:
            async for event in self._run_stream(
                session_id, user_input.strip()
            ):
                yield event

    async def _run(self, session_id: str, user_input: str) -> ChatResult:
        trace_id = uuid.uuid4().hex
        repeated_calls: Counter[str] = Counter()
        self.store.set_session_status(session_id, "running")
        self.store.add_message(session_id, "user", user_input)
        self.store.add_trace(trace_id, session_id, 0, "user_message", {"length": len(user_input)})

        try:
            for step in range(1, self.max_steps + 1):
                messages, context_stats = (
                    self.context_builder.build_with_stats(session_id)
                )
                self.store.add_trace(
                    trace_id,
                    session_id,
                    step,
                    "context_built",
                    context_stats,
                )
                started = time.perf_counter()
                try:
                    decision = await self.llm.complete(messages, self.tools.definitions())
                except LLMError as exc:
                    answer = "模型服务暂时不可用，请稍后重试。"
                    self.store.add_message(session_id, "assistant", answer)
                    self.store.add_trace(trace_id, session_id, step, "llm_error", {"error": str(exc)})
                    self.store.set_session_status(session_id, "failed")
                    return ChatResult(session_id, answer, step, trace_id)
                duration_ms = int((time.perf_counter() - started) * 1000)
                self.store.add_trace(trace_id, session_id, step, "llm_decision", {"kind": decision.kind, "usage": decision.usage}, duration_ms)

                if decision.kind == "final":
                    answer = decision.content.strip() or "任务已经处理完成。"
                    self.store.add_message(session_id, "assistant", answer)
                    self.store.set_session_status(session_id, "idle")
                    return ChatResult(session_id, answer, step, trace_id)

                call = decision.tool_call
                if call is None:
                    raise RuntimeError("tool decision is missing tool_call")
                signature = f"{call.name}:{json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)}"
                repeated_calls[signature] += 1
                if repeated_calls[signature] > 2:
                    answer = f"检测到重复工具调用（{call.name}），已停止本轮任务以避免无限循环。"
                    self.store.add_message(session_id, "assistant", answer)
                    self.store.add_trace(trace_id, session_id, step, "repeated_tool_call", {"tool": call.name})
                    self.store.set_session_status(session_id, "failed")
                    return ChatResult(session_id, answer, step, trace_id)

                raw_tool_call = [{"id": call.id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)}}]
                self.store.add_message(session_id, "assistant", decision.content, metadata={"tool_calls": raw_tool_call})
                tool_started = time.perf_counter()
                result = await self.tools.execute(
                    call.name,
                    call.arguments,
                    ToolContext(
                        session_id=session_id,
                        trace_id=trace_id,
                        store=self.store,
                        context_stats=context_stats,
                    ),
                    self.tool_timeout_seconds,
                )
                tool_duration_ms = int((time.perf_counter() - tool_started) * 1000)
                self.store.add_message(session_id, "tool", json.dumps(result.as_dict(), ensure_ascii=False), name=call.name, tool_call_id=call.id)
                self.store.add_trace(trace_id, session_id, step, "tool_result", {"tool": call.name, "ok": result.ok, "retryable": result.retryable, "error": result.error}, tool_duration_ms)

            answer = f"已达到最大执行轮数（{self.max_steps}），任务被安全终止。"
            self.store.add_message(session_id, "assistant", answer)
            self.store.add_trace(trace_id, session_id, self.max_steps, "max_steps_exceeded")
            self.store.set_session_status(session_id, "failed")
            return ChatResult(session_id, answer, self.max_steps, trace_id)
        except Exception:
            logger.exception("agent runtime failed", extra={"trace_id": trace_id, "session_id": session_id})
            self.store.set_session_status(session_id, "failed")
            raise

    async def _run_stream(self, session_id: str, user_input: str):
        trace_id = uuid.uuid4().hex
        repeated_calls: Counter[str] = Counter()
        self.store.set_session_status(session_id, "running")
        self.store.add_message(session_id, "user", user_input)
        self.store.add_trace(
            trace_id,
            session_id,
            0,
            "user_message",
            {"length": len(user_input), "stream": True},
        )
        yield {
            "event": "start",
            "session_id": session_id,
            "trace_id": trace_id,
        }

        try:
            for step in range(1, self.max_steps + 1):
                messages, context_stats = (
                    self.context_builder.build_with_stats(session_id)
                )
                self.store.add_trace(
                    trace_id,
                    session_id,
                    step,
                    "context_built",
                    context_stats,
                )
                started = time.perf_counter()
                decision = None
                emitted_content = False
                try:
                    stream_method = getattr(
                        self.llm, "stream_complete", None
                    )
                    if stream_method is None:
                        decision = await self.llm.complete(
                            messages, self.tools.definitions()
                        )
                        if decision.kind == "final" and decision.content:
                            emitted_content = True
                            yield {
                                "event": "assistant_delta",
                                "step": step,
                                "content": decision.content,
                            }
                    else:
                        async for item in stream_method(
                            messages, self.tools.definitions()
                        ):
                            if item["type"] == "content":
                                emitted_content = True
                                yield {
                                    "event": "assistant_delta",
                                    "step": step,
                                    "content": item["content"],
                                }
                            elif item["type"] == "decision":
                                decision = item["decision"]
                except LLMError as exc:
                    answer = "模型服务暂时不可用，请稍后重试。"
                    self.store.add_message(
                        session_id, "assistant", answer
                    )
                    self.store.add_trace(
                        trace_id,
                        session_id,
                        step,
                        "llm_error",
                        {"error": str(exc), "stream": True},
                    )
                    self.store.set_session_status(
                        session_id, "failed"
                    )
                    if not emitted_content:
                        yield {
                            "event": "assistant_delta",
                            "step": step,
                            "content": answer,
                        }
                    yield {
                        "event": "done",
                        "answer": answer,
                        "steps": step,
                        "trace_id": trace_id,
                        "status": "failed",
                    }
                    return

                if decision is None:
                    raise RuntimeError(
                        "model stream ended without a decision"
                    )
                duration_ms = int(
                    (time.perf_counter() - started) * 1000
                )
                self.store.add_trace(
                    trace_id,
                    session_id,
                    step,
                    "llm_decision",
                    {
                        "kind": decision.kind,
                        "usage": decision.usage,
                        "stream": True,
                    },
                    duration_ms,
                )

                if decision.kind == "final":
                    answer = (
                        decision.content.strip()
                        or "任务已经处理完成。"
                    )
                    if not emitted_content:
                        yield {
                            "event": "assistant_delta",
                            "step": step,
                            "content": answer,
                        }
                    self.store.add_message(
                        session_id, "assistant", answer
                    )
                    self.store.set_session_status(
                        session_id, "idle"
                    )
                    yield {
                        "event": "done",
                        "answer": answer,
                        "steps": step,
                        "trace_id": trace_id,
                        "status": "idle",
                    }
                    return

                call = decision.tool_call
                if call is None:
                    raise RuntimeError(
                        "tool decision is missing tool_call"
                    )
                signature = (
                    f"{call.name}:"
                    f"{json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)}"
                )
                repeated_calls[signature] += 1
                if repeated_calls[signature] > 2:
                    answer = (
                        f"检测到重复工具调用（{call.name}），"
                        "已停止本轮任务以避免无限循环。"
                    )
                    self.store.add_message(
                        session_id, "assistant", answer
                    )
                    self.store.add_trace(
                        trace_id,
                        session_id,
                        step,
                        "repeated_tool_call",
                        {"tool": call.name, "stream": True},
                    )
                    self.store.set_session_status(
                        session_id, "failed"
                    )
                    yield {
                        "event": "assistant_delta",
                        "step": step,
                        "content": answer,
                    }
                    yield {
                        "event": "done",
                        "answer": answer,
                        "steps": step,
                        "trace_id": trace_id,
                        "status": "failed",
                    }
                    return

                raw_tool_call = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(
                                call.arguments,
                                ensure_ascii=False,
                            ),
                        },
                    }
                ]
                self.store.add_message(
                    session_id,
                    "assistant",
                    decision.content,
                    metadata={"tool_calls": raw_tool_call},
                )
                yield {
                    "event": "tool_call",
                    "step": step,
                    "name": call.name,
                    "arguments": call.arguments,
                }
                tool_started = time.perf_counter()
                result = await self.tools.execute(
                    call.name,
                    call.arguments,
                    ToolContext(
                        session_id=session_id,
                        trace_id=trace_id,
                        store=self.store,
                        context_stats=context_stats,
                    ),
                    self.tool_timeout_seconds,
                )
                tool_duration_ms = int(
                    (time.perf_counter() - tool_started) * 1000
                )
                result_dict = result.as_dict()
                self.store.add_message(
                    session_id,
                    "tool",
                    json.dumps(result_dict, ensure_ascii=False),
                    name=call.name,
                    tool_call_id=call.id,
                )
                self.store.add_trace(
                    trace_id,
                    session_id,
                    step,
                    "tool_result",
                    {
                        "tool": call.name,
                        "ok": result.ok,
                        "retryable": result.retryable,
                        "error": result.error,
                        "stream": True,
                    },
                    tool_duration_ms,
                )
                yield {
                    "event": "tool_result",
                    "step": step,
                    "name": call.name,
                    "result": result_dict,
                }

            answer = (
                f"已达到最大执行轮数（{self.max_steps}），"
                "任务被安全终止。"
            )
            self.store.add_message(
                session_id, "assistant", answer
            )
            self.store.add_trace(
                trace_id,
                session_id,
                self.max_steps,
                "max_steps_exceeded",
                {"stream": True},
            )
            self.store.set_session_status(session_id, "failed")
            yield {
                "event": "assistant_delta",
                "step": self.max_steps,
                "content": answer,
            }
            yield {
                "event": "done",
                "answer": answer,
                "steps": self.max_steps,
                "trace_id": trace_id,
                "status": "failed",
            }
        except asyncio.CancelledError:
            self.store.set_session_status(session_id, "failed")
            raise
        except Exception as exc:
            logger.exception(
                "streaming agent runtime failed",
                extra={
                    "trace_id": trace_id,
                    "session_id": session_id,
                },
            )
            self.store.set_session_status(session_id, "failed")
            yield {
                "event": "error",
                "message": f"运行失败：{type(exc).__name__}",
                "trace_id": trace_id,
            }

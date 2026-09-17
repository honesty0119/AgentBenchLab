"""Fixture-aware deterministic oracle, NOT an autonomous model or benchmark result."""
import json
import uuid

from app.llm.base import LLMError
from app.models import LLMDecision, ToolCall

from .dataset import FIXTURES


class DemoClient:
    def __init__(self, case, variant="recovery"):
        self.case, self.variant = case, variant
        self.plans = json.loads((FIXTURES / "demo_scripts.json").read_text("utf-8"))[case.id]
        self.last = None
        self.probe_id = ""
        self.obs = []
        self.ticket_id = "IT-1"
        self.retry = False

    def begin_turn(self, index):
        self.plan = self.plans[index]
        self.last, self.retry, self.obs, self.probe_id = None, False, [], ""
        self.queue = [("kb_search", {"query": self.case.initial.kind}), ("ticket_search", {})]
        if index == 0 and not self.case.existing_ticket:
            self.queue.append(("ticket_create", {"title": "设备服务故障", "idempotency_key": self.case.id}))
        self.queue.append(("service_status", {}))
        self.queue.extend(("diagnose", {"check": c}) for c in self.plan["checks"])
        if self.plan["repair"]:
            self.queue.append(("repair", {"action": self.plan["repair"]}))
        self.queue.extend(("ask_user", {"field": f, "question": f"请提供 {f} 信息以继续排障。"})
                          for f in self.plan["questions"])
        if self.plan["status"] != "waiting_for_user" and self.variant != "skip-probe":
            self.queue.append(("probe", {}))
        if self.variant == "duplicate" and index == 0:
            self.queue.append(("ticket_create", {"title": "重复故障", "idempotency_key": "duplicate"}))
        self.queue.append(("ticket_update", None))

    async def complete(self, messages, tools):
        if self.last and messages[-1]["role"] == "tool":
            raw = json.loads(messages[-1]["content"])
            data = raw.get("data") or {}
            if raw.get("ok"):
                if isinstance(data, dict) and str(data.get("id", "")).startswith("PROBE-"):
                    self.probe_id = data["id"]
                if isinstance(data, dict) and str(data.get("id", "")).startswith("OBS-"):
                    self.obs.append(data["id"])
            elif raw.get("retryable") and not self.retry and self.last[0] != "repair":
                self.queue.insert(0, self.last)
                self.retry = True
        if not self.queue:
            final = {"answer": {"resolved": "已通过端到端探针确认恢复，并更新工单。",
                     "escalated": "故障尚未恢复，已记录观察及后续步骤并转交。",
                     "waiting_for_user": "需要补充信息才能继续处理，请提供所列字段。"}[self.plan["status"]],
                     "status": self.plan["status"], "ticket_id": self.ticket_id, "evidence_id": self.probe_id,
                     "questions": self.plan["questions"], "citations": ["kb/runbook"]}
            return LLMDecision("final", json.dumps(final, ensure_ascii=False))
        name, args = self.queue.pop(0)
        if name == "ticket_update":
            args = {"ticket_id": self.ticket_id, "status": self.plan["status"],
                    "note": "按当前诊断更新；未解决时保留待处理状态。", "evidence_id": self.probe_id}
            if self.plan["status"] == "escalated":
                args["handoff"] = {"symptom": self.case.title, "observations": ", ".join(self.obs),
                    "actions": self.plan["repair"] or "none", "reason": "端到端验证未恢复，本地流程无法解决。",
                    "next_step": "请服务台依据观察记录继续检查相应上游或终端条件，恢复后重新验证。"}
        if args is None:
            raise LLMError("invalid_demo_action")
        self.last = (name, args)
        return LLMDecision("tool", tool_call=ToolCall(uuid.uuid4().hex, name, args))

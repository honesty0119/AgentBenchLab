from __future__ import annotations

import copy
import json
from decimal import ROUND_HALF_UP, Decimal

from app.models import ToolResult
from app.tools.registry import ToolRegistry
from agentbench.environment import Environment, EnvironmentTool

from .models import Plan, Selection, Snapshot

STATE_PATH = "procurement/state.json"
DRAFT_PATH = "procurement/draft.json"


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def calculate_cost(state: Snapshot, selections: list[Selection]) -> dict:
    """Tool-side calculation only. Does not prove feasibility or optimality."""
    lines, suppliers = [], set()
    for selection in selections:
        quote = next((q for q in state.quotes if q.id == selection.quote_id), None)
        if quote is None or quote.version != selection.version:
            raise ValueError("Unknown current quote version")
        if quote.price_cents is None or quote.tax_included is None or quote.pack_size is None:
            raise ValueError("Missing pricing or packaging information")
        if not quote.tax_included and quote.tax_bps is None:
            raise ValueError("Missing tax rate")
        price = quote.price_cents
        for tier in sorted(quote.tiers, key=lambda t: t.min_packs):
            if selection.packs >= tier.min_packs:
                price = tier.price_cents
        amount = Decimal(price) * selection.packs
        if not quote.tax_included:
            amount = amount * (Decimal(1) + Decimal(quote.tax_bps) / Decimal(10000))
        cents = int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        lines.append({**selection.model_dump(), "item": quote.item, "supplier": quote.supplier,
                      "base_quantity": selection.packs * quote.pack_size, "unit": quote.unit,
                      "pack_price_cents": price, "gross_cents": cents})
        suppliers.add(quote.supplier)
    shipping = {key: state.shipping[key] for key in sorted(suppliers)}
    if any(value is None for value in shipping.values()):
        raise ValueError("Missing shipping information")
    return {"lines": lines, "shipping_cents": shipping,
            "total_cents": sum(x["gross_cents"] for x in lines) + sum(shipping.values()),
            "note": "Cost only; verify quantity, specification, availability, deadline and objective separately"}


class ProcurementEnvironment(Environment):
    def __init__(self, snapshots, faults, retry_policy="none", retries=1):
        self.snapshots = snapshots
        self.state = snapshots[0]
        super().__init__({STATE_PATH: encode(self.state.model_dump(mode="json")), DRAFT_PATH: "null"},
                         faults, retry_policy, retries)
        self.draft_keys = {}

    def begin_turn(self, index):
        self.state = self.snapshots[index]
        self.files[STATE_PATH] = encode(self.state.model_dump(mode="json"))
        self.files[DRAFT_PATH] = "null"

    def registry(self):
        registry = ToolRegistry()
        for name, (description, fields, required) in DEFINITIONS.items():
            registry.register(ProcurementTool(self, name, description, fields, required))
        return registry

    async def _execute(self, name, args, context):
        if name == "procurement_read":
            self.reads.add(STATE_PATH)
            return ToolResult(True, {"state": self.state.model_dump(mode="json"),
                                     "draft": json.loads(self.files[DRAFT_PATH])})
        if name == "procurement_cost":
            selections = [Selection.model_validate(x) for x in args["selections"]]
            if len(selections) > 3:
                raise ValueError("At most three purchase lines")
            return ToolResult(True, calculate_cost(self.state, selections))
        if name == "procurement_save_draft":
            plan = Plan.model_validate(args["plan"])
            if plan.revision != self.state.revision:
                raise ValueError("Stale revision; read current state and replan")
            key = args["idempotency_key"]
            if not key.strip() or len(key) > 100:
                raise ValueError("Supply a nonempty idempotency key of at most 100 characters")
            payload = plan.model_dump(mode="json")
            if key in self.draft_keys:
                if self.draft_keys[key] != payload:
                    raise ValueError("Idempotency key reused for a different draft")
                # A replay must not overwrite a newer draft in the same revision.
                return ToolResult(True, {"draft": copy.deepcopy(payload), "replayed": True})
            self.files[DRAFT_PATH] = encode(payload)
            self.draft_keys[key] = copy.deepcopy(payload)
            return ToolResult(True, {"draft": payload, "replayed": False})
        raise ValueError("Unknown procurement tool")


class ProcurementTool(EnvironmentTool):
    async def execute(self, arguments, context):
        safe = self.name in {"procurement_read", "procurement_cost"} or (
            self.name == "procurement_save_draft" and bool(arguments.get("idempotency_key")))
        count = self.env.retries if safe and self.env.retry_policy == "safe" else 0
        for attempt in range(count + 1):
            result = await self.env.execute(self.name, arguments, context)
            self.env.events[-1]["harness_retry"] = attempt
            if result.ok or not result.retryable:
                return result
        return result


def inline_schema(model):
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(value):
        if isinstance(value, list):
            return [resolve(v) for v in value]
        if isinstance(value, dict):
            if "$ref" in value:
                return resolve(definitions[value["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in value.items()}
        return value

    return resolve(schema)


DEFINITIONS = {
    "procurement_read": ("Read the authoritative current procurement snapshot and saved draft", {}, []),
    "procurement_cost": ("Calculate tax-inclusive line costs and shipping once per supplier; no feasibility proof",
                         {"selections": {"type": "array", "items": inline_schema(Selection)}}, ["selections"]),
    "procurement_save_draft": ("Save a draft with current revision and stable idempotency key. Does not place orders. "
                               "Business correctness is evaluated separately.",
                               {"plan": inline_schema(Plan), "idempotency_key": {"type": "string"}},
                               ["plan", "idempotency_key"]),
}

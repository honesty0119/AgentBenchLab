from __future__ import annotations

import copy

from agentbench.environment import Environment
from app.models import ToolResult
from app.tools.base import Tool
from app.tools.registry import ToolRegistry

from .schema import ReturnRequest, State


def unit_net_cents(order):
    """Hamilton allocation in integer cents; stable (line id, unit index) ties."""
    units = [(line.id, i, line.unit_cents - line.discount_cents)
             for line in sorted(order.lines, key=lambda x: x.id) for i in range(line.quantity)]
    total = sum(u[2] for u in units)
    discounts = [order.coupon_cents * u[2] // total if total else 0 for u in units]
    ranked = sorted(range(len(units)), key=lambda i: (
        -(order.coupon_cents * units[i][2] % total) if total else 0, units[i][:2]))
    for i in ranked[:order.coupon_cents - sum(discounts)]:
        discounts[i] += 1
    return {(line, unit): cents - discounts[i] for i, (line, unit, cents) in enumerate(units)}


class EcommerceEnvironment(Environment):
    def __init__(self, state: State, faults=(), retry_policy="none", retries=1):
        super().__init__({}, list(faults), retry_policy, retries)
        self.state = state.model_copy(deep=True)
        self.initial_state = self.snapshot()

    def snapshot(self):
        return self.state.model_dump(mode="json")

    def registry(self):
        registry = ToolRegistry()
        for name, (description, fields, required) in DEFINITIONS.items():
            registry.register(EcommerceTool(self, name, description, fields, required))
        return registry

    async def execute(self, name, args, context):
        before = self.snapshot()
        event_count = len(self.events)
        try:
            return await super().execute(name, args, context)
        finally:
            if len(self.events) > event_count:
                self.events[-1].update(state_before=before, state_after=self.snapshot())

    def order(self, order_id):
        order = next((o for o in self.state.orders if o.id == order_id), None)
        if order is None:
            raise ValueError("order_not_found")
        return order

    def quote(self, order_id, line_id, quantity, reason):
        order = self.order(order_id)
        line = next((x for x in order.lines if x.id == line_id), None)
        if line is None:
            raise ValueError("line_not_found")
        if type(quantity) is not int or quantity < 1:
            raise ValueError("invalid_quantity")
        if reason not in {"unwanted", "damaged"}:
            raise ValueError("unknown_reason")
        policies = [p for p in self.state.policies if p.start <= order.purchased <= p.end
                    and p.channel == order.channel and line.category in p.categories]
        if len(policies) != 1:
            raise ValueError("policy_missing_or_conflicting")
        policy = policies[0]
        if order.status != "delivered":
            raise ValueError("order_not_delivered")
        if order.delivered is None:
            raise ValueError("delivery_date_missing")
        days = (self.state.today - order.delivered).days
        if days < 0:
            raise ValueError("delivery_date_conflict")
        if days > (policy.damaged_days if reason == "damaged" else policy.ordinary_days):
            raise ValueError("return_window_expired")
        if line.category == "digital" or (line.category == "hygiene" and line.opened and reason == "unwanted"):
            raise ValueError("product_ineligible")
        occupied = {i for r in self.state.returns if r.order_id == order_id and r.line_id == line_id
                    for i in r.units}
        available = [i for i in range(line.quantity) if i not in occupied]
        if quantity > len(available):
            raise ValueError("quantity_unavailable")
        selected = available[:quantity]
        net = unit_net_cents(order)
        return {"order_id": order_id, "line_id": line_id, "quantity": quantity,
                "amount_cents": sum(net[line_id, i] for i in selected), "units": selected,
                "reason": reason, "policy_id": policy.id, "status": "quoted",
                "shipping_refund_cents": 0}

    async def _execute(self, name, args, context):
        if name == "search_policies":
            query = args["query"].strip().casefold()
            if not query:
                raise ValueError("query_required")
            data = [p.model_dump(mode="json") for p in self.state.policies
                    if query in (p.id + " " + p.channel + " " + p.text).casefold()]
        elif name == "get_policy":
            policy = next((p for p in self.state.policies if p.id == args["policy_id"]), None)
            if policy is None:
                raise ValueError("policy_not_found")
            data = policy.model_dump(mode="json")
        elif name == "list_orders":
            data = [{"id": o.id, "purchased": str(o.purchased), "status": o.status,
                     "items": [x.name for x in o.lines]} for o in self.state.orders]
        elif name == "get_order":
            data = self.order(args["order_id"]).model_dump(mode="json")
        elif name == "quote_return":
            data = self.quote(**args)
        elif name == "list_returns":
            self.order(args["order_id"])
            data = [r.model_dump(mode="json") for r in self.state.returns if r.order_id == args["order_id"]]
        elif name == "create_return":
            key = args["idempotency_key"].strip()
            if not key or len(key) > 120:
                raise ValueError("invalid_idempotency_key")
            existing = next((r for r in self.state.returns if r.idempotency_key == key), None)
            if existing:
                fields = ("order_id", "line_id", "quantity", "reason")
                if (any(getattr(existing, k) != args[k] for k in fields)
                        or existing.amount_cents != args["expected_amount_cents"]):
                    raise ValueError("idempotency_conflict")
                return ToolResult(True, existing.model_dump(mode="json"))
            quote = self.quote(**{k: args[k] for k in ("order_id", "line_id", "quantity", "reason")})
            if type(args["expected_amount_cents"]) is not int or quote["amount_cents"] != args["expected_amount_cents"]:
                raise ValueError("amount_mismatch")
            request_id = "R1"
            index = 1
            while request_id in {r.id for r in self.state.returns}:
                index += 1
                request_id = f"R{index}"
            quote.pop("shipping_refund_cents")
            quote.update(id=request_id, status="requested", idempotency_key=key)
            request = ReturnRequest(**quote)
            self.state.returns.append(request)
            data = request.model_dump(mode="json")
        else:
            raise ValueError("unknown_domain_tool")
        return ToolResult(True, copy.deepcopy(data))


class EcommerceTool(Tool):
    def __init__(self, env, name, description, fields, required):
        self.env, self.name, self.description = env, name, description
        self.input_schema = {"type": "object", "properties": fields,
                             "required": required, "additionalProperties": False}

    async def execute(self, arguments, context):
        safe = self.name != "create_return" or bool(arguments.get("idempotency_key", "").strip())
        retries = self.env.retries if self.env.retry_policy == "safe" and safe else 0
        for attempt in range(1 + retries):
            result = await self.env.execute(self.name, arguments, context)
            self.env.events[-1]["harness_retry"] = attempt
            if result.ok or not result.retryable:
                return result
        return result


S = {"type": "string"}
SELECTION = {"order_id": S, "line_id": S, "quantity": {"type": "integer", "minimum": 1},
             "reason": {"type": "string", "enum": ["unwanted", "damaged"]}}
DEFINITIONS = {
    "search_policies": ("Search sandbox policies by literal substring; use web for all web policy versions. Match purchase date, channel and category.", {"query": S}, ["query"]),
    "get_policy": ("Read one full policy version.", {"policy_id": S}, ["policy_id"]),
    "list_orders": ("List this synthetic customer's orders; resolve ambiguous order IDs with the user.", {}, []),
    "get_order": ("Read order lines, quantities, dates and prices in integer cents.", {"order_id": S}, ["order_id"]),
    "quote_return": ("Validate eligibility and calculate net refundable cents for unreserved units; does not create anything.", SELECTION, list(SELECTION)),
    "create_return": ("Create a local return request only after user authorization. No real refund. Reuse the SAME key on uncertain retries. Expected amount must match the quote.",
                      {**SELECTION, "idempotency_key": S, "expected_amount_cents": {"type": "integer", "minimum": 0}},
                      [*SELECTION, "idempotency_key", "expected_amount_cents"]),
    "list_returns": ("Read current requests and their real status, including after a lost create response.", {"order_id": S}, ["order_id"]),
}

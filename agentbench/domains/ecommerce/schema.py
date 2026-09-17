from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from agentbench.schema import Fault, StrictModel


class Line(StrictModel):
    id: str
    name: str
    category: str = "electronics"
    quantity: StrictInt = Field(ge=1, le=100)
    unit_cents: StrictInt = Field(ge=0)
    discount_cents: StrictInt = Field(default=0, ge=0)
    opened: bool = False

    @model_validator(mode="after")
    def discount_valid(self):
        if self.discount_cents > self.unit_cents:
            raise ValueError("unit discount exceeds price")
        return self


class Order(StrictModel):
    id: str
    purchased: date
    delivered: date | None
    channel: str = "web"
    status: Literal["delivered", "pending", "cancelled"] = "delivered"
    coupon_cents: StrictInt = Field(default=0, ge=0)
    shipping_cents: StrictInt = Field(default=0, ge=0)
    lines: list[Line] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self):
        if len({x.id for x in self.lines}) != len(self.lines):
            raise ValueError("duplicate line")
        if self.coupon_cents > sum((x.unit_cents - x.discount_cents) * x.quantity for x in self.lines):
            raise ValueError("coupon exceeds merchandise")
        if self.delivered and self.delivered < self.purchased:
            raise ValueError("delivery before purchase")
        return self


class Policy(StrictModel):
    id: str
    channel: str = "web"
    categories: list[str] = Field(default_factory=lambda: ["electronics", "hygiene", "digital"])
    start: date
    end: date
    ordinary_days: StrictInt = Field(default=14, ge=0)
    damaged_days: StrictInt = Field(default=30, ge=0)
    text: str


class ReturnRequest(StrictModel):
    id: str
    order_id: str
    line_id: str
    quantity: StrictInt = Field(ge=1)
    amount_cents: StrictInt = Field(ge=0)
    policy_id: str
    reason: Literal["unwanted", "damaged"]
    status: Literal["requested", "received", "refunded"] = "requested"
    units: list[StrictInt]
    idempotency_key: str = Field(min_length=1)


class State(StrictModel):
    today: date
    orders: list[Order]
    policies: list[Policy]
    returns: list[ReturnRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def identities(self):
        for values in (self.orders, self.policies, self.returns):
            if len({x.id for x in values}) != len(values):
                raise ValueError("duplicate state identity")
        if len({r.idempotency_key for r in self.returns}) != len(self.returns):
            raise ValueError("duplicate idempotency key")
        occupied = set()
        for request in self.returns:
            order = next((o for o in self.orders if o.id == request.order_id), None)
            line = next((x for x in order.lines if x.id == request.line_id), None) if order else None
            if not line or request.quantity != len(request.units) or len(set(request.units)) != len(request.units):
                raise ValueError("invalid return selection")
            if request.policy_id not in {p.id for p in self.policies}:
                raise ValueError("unknown return policy")
            for unit in request.units:
                key = (request.order_id, request.line_id, unit)
                if unit < 0 or unit >= line.quantity or key in occupied:
                    raise ValueError("invalid or duplicate reserved unit")
                occupied.add(key)
        return self


class ExpectedReturn(StrictModel):
    order_id: str
    line_id: str
    quantity: StrictInt
    amount_cents: StrictInt
    policy_id: str
    reason: Literal["unwanted", "damaged"] = "unwanted"
    status: Literal["requested", "received", "refunded"] = "requested"


class Reply(StrictModel):
    answer: str = Field(min_length=1)
    status: Literal["quoted", "requested", "received", "refunded", "clarify", "rejected", "not_found", "unchanged"]
    amount_cents: StrictInt | None = None
    policy_ids: list[str] = Field(default_factory=list)
    request_ids: list[str] = Field(default_factory=list)
    clarify: list[str] = Field(default_factory=list)


class Expectation(StrictModel):
    returns: list[ExpectedReturn]
    status: str
    amount_cents: StrictInt | None = None
    policy_ids: list[str] = Field(default_factory=list)
    clarify: list[str] = Field(default_factory=list)
    # Exact IDs are not gold: reply IDs are instead resolved against observed state.
    reported: list[ExpectedReturn] = Field(default_factory=list)


class EcommerceCase(StrictModel):
    id: str = Field(pattern=r"^ec-[a-z0-9-]+$")
    family: str
    split: Literal["dev", "test", "challenge"]
    title: str
    turns: list[str] = Field(min_length=1)
    initial: State
    expected: list[Expectation]
    faults: list[Fault] = Field(default_factory=list)
    reference: str = Field(min_length=1)
    provenance: Literal["authored-synthetic"] = "authored-synthetic"
    review_status: Literal["draft"] = "draft"
    semantic_required: Literal[True] = True

    @model_validator(mode="after")
    def turns_match(self):
        if len(self.turns) != len(self.expected):
            raise ValueError("one expectation is required per turn")
        return self

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from agentbench.schema import Fault, StrictModel

Count = Annotated[int, Field(strict=True, ge=1, le=10000)]
Money = Annotated[int, Field(strict=True, ge=0, le=100000000)]


class Item(StrictModel):
    id: str
    quantity: Count
    unit: str
    spec: str
    max_overbuy: int = Field(default=0, strict=True, ge=0, le=10000)


class Tier(StrictModel):
    min_packs: Count
    price_cents: Money


class Quote(StrictModel):
    id: str
    version: Count = 1
    supplier: str
    item: str
    spec: str
    unit: str
    pack_size: Count | None = 1
    price_cents: Money | None
    tax_included: bool | None = True
    tax_bps: int | None = Field(default=0, strict=True, ge=0, le=10000)
    min_packs: Count = 1
    stock_packs: int | None = Field(default=100, strict=True, ge=0, le=100)
    lead_days: int | None = Field(default=2, strict=True, ge=0, le=365)
    valid_until: date | None = date(2026, 10, 1)
    currency: Literal["CNY"] = "CNY"
    tiers: list[Tier] = Field(default_factory=list)
    note: str = ""

    @model_validator(mode="after")
    def unique_tiers(self):
        if len({t.min_packs for t in self.tiers}) != len(self.tiers):
            raise ValueError("Duplicate tier thresholds")
        return self


class Snapshot(StrictModel):
    revision: Count
    as_of: date = date(2026, 9, 17)
    deadline: date = date(2026, 9, 24)
    budget_cents: Money = 100000
    objective: Literal["min_cost", "feasible"] = "min_cost"
    items: list[Item] = Field(min_length=1, max_length=3)
    quotes: list[Quote] = Field(min_length=1, max_length=12)
    shipping: dict[str, Money | None]

    @model_validator(mode="after")
    def consistent(self):
        if len({i.id for i in self.items}) != len(self.items):
            raise ValueError("Duplicate item")
        if len({q.id for q in self.quotes}) != len(self.quotes):
            raise ValueError("Only one current version per quote id")
        if any(q.item not in {i.id for i in self.items} or q.supplier not in self.shipping for q in self.quotes):
            raise ValueError("Unknown item or missing shipping entry (use null if unknown)")
        return self


class Selection(StrictModel):
    quote_id: str
    version: Count
    packs: Count


class Plan(StrictModel):
    revision: Count
    status: Literal["feasible", "infeasible", "needs_info"]
    selections: list[Selection] = Field(default_factory=list, max_length=3)
    total_cents: Money | None = None
    missing: list[str] = Field(default_factory=list, max_length=100)
    explanation: str = Field(min_length=1, max_length=5000)


class ProcurementCase(StrictModel):
    id: str = Field(pattern=r"^proc-[a-z0-9-]+$")
    family: str
    split: Literal["dev", "test", "challenge"]
    title: str
    turns: list[str] = Field(min_length=1)
    snapshots: list[Snapshot] = Field(min_length=1)
    faults: list[Fault] = Field(default_factory=list)
    provenance: Literal["authored-synthetic"] = "authored-synthetic"
    review_status: Literal["draft"] = "draft"
    semantic_required: Literal[True] = True

    @model_validator(mode="after")
    def aligned(self):
        if len(self.turns) != len(self.snapshots):
            raise ValueError("Every turn needs one authoritative snapshot")
        revisions = [s.revision for s in self.snapshots]
        if revisions != sorted(set(revisions)):
            raise ValueError("Snapshot revisions must increase")
        return self

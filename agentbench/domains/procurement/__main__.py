"""Isolated scenario CLI, with no mutation of the shared Lab database."""
import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from agentbench.runner import code_manifest
from agentbench.schema import RunConfig, digest
from agentbench.settings import endpoint, validate_endpoint

from .dataset import load_cases
from .grading import fingerprint, grade
from .runner import SYSTEM, execute


def model_preflight(config, cases, budget_usd):
    """Reserve a conservative request/output envelope before contacting a model.

Tokenization and prices are provider-specific; this is a declared engineering
envelope, not an invoice guarantee. Unknown usage stops subsequent trials.
"""
    if not os.environ.get("AGENTBENCH_API_KEY") or not config.model:
        raise ValueError("Model and AGENTBENCH_API_KEY must be configured explicitly")
    if config.input_price_per_million is None or config.output_price_per_million is None:
        raise ValueError("Model runs require explicit input/output prices and --budget-usd")
    budget = Decimal(str(budget_usd))
    if not budget.is_finite() or budget <= 0:
        raise ValueError("Positive finite budget required")
    calls = sum(len(c.turns) for c in cases) * config.max_steps * (config.http_retries + 1)
    bound = calls * (Decimal(config.request_chars * 4) * Decimal(str(config.input_price_per_million)) +
                     config.max_output_tokens * Decimal(str(config.output_price_per_million))) / 1000000
    if bound > budget:
        raise ValueError(f"Conservative run envelope ${bound} exceeds budget ${budget}; reduce cases/limits")
    return {"budget_usd": str(budget), "reserved_usd": str(bound),
            "assumption": "At most four billed input tokens per request character; user-supplied prices"}


async def run(cases, configs, output, budget=None):
    budget_record = None
    if any(c.agent == "openai-compatible" for c in configs):
        if len(configs) != 1 or budget is None:
            raise ValueError("Real runs require a single config and explicit budget")
        budget_record = model_preflight(configs[0], cases, budget)
    report = {"domain": "procurement", "created_at": datetime.now(timezone.utc).isoformat(),
              "demo": all(c.agent.startswith("demo-") for c in configs),
              "review_status": "draft", "semantic": "pending", "budget": budget_record,
              "manifest": {"code": code_manifest(), "scorer_hash": fingerprint(), "prompt_hash": digest(SYSTEM),
                           "dataset_hash": digest([c.model_dump(mode="json") for c in cases]),
                           "configs": [c.model_dump() for c in configs]}, "trials": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("Output already exists; choose a new path to preserve evidence")
    for config in configs:
        for case in cases:
            result = await execute(case, config)
            report["trials"].append({"case": case.model_dump(mode="json"), "agent": config.agent,
                                     "result": result, "grade": grade(case, result)})
            # Persist completed trials even if a later trial fails.
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if config.agent == "openai-compatible" and not result["usage_complete"]:
                raise ValueError("Unknown billed usage; stopped remaining trials. Partial evidence saved.")
            if config.agent == "openai-compatible":
                known_cost = sum(Decimal(str(t["result"]["known_cost"])) for t in report["trials"])
                if known_cost > Decimal(str(budget)):
                    raise ValueError("Reported usage exceeded declared budget assumptions; stopped remaining trials")
    print(json.dumps({"report": str(output.resolve()), "trials": len(report["trials"]),
                      "rules_pass": sum(t["grade"]["label"] == "pass" for t in report["trials"]),
                      "overall": "pending_semantic for rule passes", "demo": report["demo"]}, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description="Procurement synthetic benchmark (draft)")
    parser.add_argument("command", choices=["demo", "serve", "run"])
    parser.add_argument("--output", type=Path, default=Path("data/procurement/demo.json"))
    parser.add_argument("--split", choices=["all", "dev", "test", "challenge"], default="all")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--budget-usd", type=str)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        from .web import create_app
        uvicorn.run(create_app(args.output), host="127.0.0.1", port=args.port)
        return
    cases = load_cases(args.split)
    if args.case_id:
        if set(args.case_id) - {c.id for c in cases}:
            parser.error("Unknown case id in selected split")
        cases = [c for c in cases if c.id in args.case_id]
    if args.command == "demo":
        configs = [RunConfig(agent=agent, tool_timeout_seconds=0.05) for agent in
                   ("demo-baseline", "demo-recovery")]
    else:
        if not args.config or args.budget_usd is None:
            parser.error("run requires --config and --budget-usd")
        config = RunConfig.model_validate_json(args.config.read_text("utf-8"))
        if config.agent != "openai-compatible" or config.repeats != 1:
            parser.error("run requires openai-compatible and repeats=1; use separate output files for repeats")
        if config.case_ids or config.dataset_hash or config.split != "all":
            parser.error("Select procurement data with CLI --case-id/--split, not document dataset config fields")
        config.model = config.model or os.environ.get("AGENTBENCH_MODEL", "")
        config.model_endpoint = validate_endpoint(config.model_endpoint or endpoint("AGENTBENCH_BASE_URL"))
        configs = [config]
    asyncio.run(run(cases, configs, args.output, args.budget_usd))


if __name__ == "__main__":
    main()

import argparse
import asyncio
import json
from pathlib import Path

from agentbench.schema import RunConfig

from .experiments import RunStore, compare, write_new
from .judging import JudgePlan, judge_saved
from .runner import ModelPlan, run_demo, run_model


def main():
    parser = argparse.ArgumentParser(description="电商实验：不可变运行、追加评分、只读查看与配对门禁")
    parser.add_argument("--root", type=Path, default=Path("data/ecommerce/runs"))
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "run"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=name == "run")
        command.add_argument("--output", type=Path, help="Optional new export; existing files are never overwritten")
        if name == "demo":
            command.add_argument("--variant", choices=["recovery", "baseline"], default="recovery")
            command.add_argument("--case", action="append", dest="case_ids")
    serve = sub.add_parser("serve")
    serve.add_argument("--run", required=True, help="Saved run ID or report file; never executes an agent")
    serve.add_argument("--port", type=int, default=8766)
    for name in ("report", "regrade", "judge"):
        command = sub.add_parser(name)
        command.add_argument("run")
        if name == "judge":
            command.add_argument("--config", type=Path, required=True, help="Explicit Judge model and request budget")
        if name == "report":
            command.add_argument("--cohort")
            command.add_argument("--regrade")
    comparison = sub.add_parser("compare")
    comparison.add_argument("base")
    comparison.add_argument("candidate")
    comparison.add_argument("--metric", choices=["rules", "overall"], default="rules")
    comparison.add_argument("--cohort")
    comparison.add_argument("--diagnostic", action="store_true")
    comparison.add_argument("--gate", action="store_true")
    comparison.add_argument("--max-drop", type=float, default=0)
    comparison.add_argument("--base-regrade")
    comparison.add_argument("--candidate-regrade")
    args = parser.parse_args()
    store = RunStore(args.root)
    if args.command in {"demo", "run"}:
        if args.output and args.output.exists():
            parser.error("Export already exists; choose a new path before executing")
        if args.command == "run":
            plan = ModelPlan.model_validate_json(args.config.read_text("utf-8"))
            report = asyncio.run(run_model(plan))
        else:
            config = RunConfig.model_validate_json(args.config.read_text("utf-8")) if args.config else None
            report = asyncio.run(run_demo(args.variant, args.case_ids, config))
        path = store.save(report)
        if args.output:
            write_new(args.output, report)
        output = {"run_id": report["run_id"], "path": str(path), "summary": report["summary"]}
    elif args.command == "serve":
        import uvicorn
        from .api import create_app
        report = store.load(args.run)
        uvicorn.run(create_app(report, store), host="127.0.0.1", port=args.port)
        return
    elif args.command == "report":
        view = store.view(store.load(args.run), cohort=args.cohort, regrade=args.regrade)
        output = {k: view.get(k) for k in ("run_id", "demo", "summary", "available_cohorts", "available_regrades",
                                          "selected_cohort", "selected_regrade")}
    elif args.command == "regrade":
        output = store.regrade(args.run)
    elif args.command == "judge":
        plan = JudgePlan.model_validate_json(args.config.read_text("utf-8"))
        output = asyncio.run(judge_saved(store, args.run, plan))
    else:
        output = compare(store, args.base, args.candidate, metric=args.metric, cohort=args.cohort,
                         diagnostic=args.diagnostic, gate=args.gate, max_drop=args.max_drop,
                         base_regrade=args.base_regrade, candidate_regrade=args.candidate_regrade)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.command == "compare" and args.gate and not output["gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

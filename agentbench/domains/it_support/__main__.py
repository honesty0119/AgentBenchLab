import argparse
import asyncio
import json
from pathlib import Path

from agentbench.schema import RunConfig

from .dataset import load_cases
from .runner import run_suite, save_report


def main():
    parser = argparse.ArgumentParser(description="Local synthetic IT support evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--case")
    demo.add_argument("--output", default="data/it-support")
    model = sub.add_parser("model")
    model.add_argument("--case", required=True, help="One explicit task per paid invocation")
    model.add_argument("--config", required=True)
    model.add_argument("--request-budget", required=True, type=int,
                       help="Maximum model decisions; HTTP retries multiply transport attempts")
    model.add_argument("--output", default="data/it-support/model.json")
    serve = sub.add_parser("serve")
    serve.add_argument("--report", default="data/it-support/recovery.json")
    serve.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        from .web import create_app
        uvicorn.run(create_app(Path(args.report)), host="127.0.0.1", port=args.port)
        return
    cases, dataset_hash = load_cases()
    if args.case:
        cases = [c for c in cases if c.id == args.case]
        if not cases:
            parser.error("Unknown case ID")
    if args.command == "demo":
        for variant in ("skip-probe", "recovery"):
            report = asyncio.run(run_suite(cases, dataset_hash, variant=variant))
            save_report(report, Path(args.output) / f"{variant}.json")
            print(json.dumps({"variant": variant, **report["summary"]}, ensure_ascii=False))
    else:
        config = RunConfig.model_validate_json(Path(args.config).read_text("utf-8"))
        if config.agent != "openai-compatible" or not 1 <= args.request_budget <= 100:
            parser.error("Explicit openai-compatible config and request budget in [1, 100] required")
        if config.intervention != "none":
            parser.error("This standalone domain runner does not implement intervention modes")
        report = asyncio.run(run_suite(cases, dataset_hash, variant="model", config=config,
                                       request_budget=args.request_budget))
        save_report(report, args.output)
        print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()

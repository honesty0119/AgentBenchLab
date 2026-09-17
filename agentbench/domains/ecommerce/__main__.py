import argparse
import asyncio
import json
from pathlib import Path

from .runner import ModelPlan, run_demo, run_model


def main():
    parser = argparse.ArgumentParser(description="电商售后本地评测；默认只运行离线脚本")
    parser.add_argument("command", choices=["demo", "serve", "run"])
    parser.add_argument("--variant", choices=["recovery", "baseline"], default="recovery")
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--output", type=Path, default=Path("data/ecommerce/report.json"))
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--config", type=Path, help="Explicit real-model config with global request budget")
    args = parser.parse_args()
    if args.command == "run":
        if not args.config:
            parser.error("run requires --config with max_model_calls; may incur API charges")
        plan = ModelPlan.model_validate_json(args.config.read_text("utf-8"))
        report = asyncio.run(run_model(plan))
    else:
        report = asyncio.run(run_demo(args.variant, args.case_ids))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"output": str(args.output), **report["summary"], "note": report["note"]}, ensure_ascii=False))
    if args.command == "serve":
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(report), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

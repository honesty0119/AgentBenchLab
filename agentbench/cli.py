from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from filelock import Timeout

from agentbench.analysis import compare, summary
from agentbench.runner import create_run, worker
from agentbench.schema import RunConfig
from agentbench.storage import Store, encode


async def finish_run(store: Store, run_id: str):
    """Drain older queued jobs, or wait for the existing server Worker."""
    while store.get_run(run_id)["status"] in {"queued", "running"}:
        try:
            await worker(store, once=True)
        except Timeout:
            await asyncio.sleep(0.5)
    result = summary(store, run_id)
    print(encode(result))
    if store.get_run(run_id)["status"] != "completed":
        raise SystemExit(1)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="AgentBench Lab")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-worker", action="store_true")
    w = sub.add_parser("worker")
    w.add_argument("--once", action="store_true")
    sub.add_parser("demo")
    run = sub.add_parser("run")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--queue", action="store_true")
    report = sub.add_parser("report")
    report.add_argument("run_id")
    report.add_argument("--output", type=Path)
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("candidate")
    c.add_argument("--gate", action="store_true")
    c.add_argument("--max-drop", type=float, default=0)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        from agentbench.api import create_app
        uvicorn.run(create_app(start_worker=not args.no_worker), host="127.0.0.1", port=args.port)
        return
    store = Store()
    if args.command == "worker":
        try:
            asyncio.run(worker(store, args.once))
        except KeyboardInterrupt:
            pass
    elif args.command == "demo":
        ids = []
        for agent in ("demo-baseline", "demo-recovery"):
            r = create_run(store, RunConfig(name=agent, agent=agent))
            ids.append(r["id"])
            asyncio.run(finish_run(store, r["id"]))
        print(encode(compare(store, *ids)))
    elif args.command == "run":
        config = RunConfig.model_validate(json.loads(args.config.read_text("utf-8")))
        r = create_run(store, config)
        print(r["id"])
        if not args.queue:
            asyncio.run(finish_run(store, r["id"]))
    elif args.command == "report":
        payload = {"run": store.get_run(args.run_id), "summary": summary(store, args.run_id),
                   "trials": [store.get_trial(t["id"]) for t in store.trials(args.run_id)]}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encode(payload), "utf-8")
            print(args.output.resolve())
        else:
            print(encode(payload["summary"]))
    elif args.command == "compare":
        result = compare(store, args.base, args.candidate)
        print(encode(result))
        if args.gate:
            if not result["comparable"] or result["delta"] < -args.max_drop or any(
                    c["before"] == "pass" and c["after"] == "fail" for c in result.get("changes", [])):
                raise SystemExit(1)


if __name__ == "__main__":
    main()

"""No API credentials or real models: verify the native Inspect + Lab reporting path."""
import asyncio
import json
import tempfile
from pathlib import Path

from agentbench.analysis import compare, summary
from agentbench.runner import create_run, worker
from agentbench.schema import RunConfig
from agentbench.storage import Store


async def main():
    with tempfile.TemporaryDirectory(prefix="agentbench-ci-") as tmp:
        store = Store(tmp)
        ids = []
        for agent in ("demo-baseline", "demo-recovery"):
            r = create_run(store, RunConfig(agent=agent, case_ids=["deadline", "growth", "lost-write"]))
            ids.append(r["id"])
            await worker(store, once=True)
        report = compare(store, *ids)
        assert report["comparable"], report
        assert report["delta"] > 0
        assert not any(c["before"] == "pass" and c["after"] == "fail" for c in report["changes"])
        result = {"demo_only": True, "summaries": [summary(store, id) for id in ids], "comparison": report}
        output = Path("artifacts/ci/smoke.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        print("Native Inspect execution and strict regression gate passed (synthetic demo only).")


asyncio.run(main())

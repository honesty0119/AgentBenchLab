"""Offline integration acceptance; safe to run on CI without model credentials."""
import asyncio
from pathlib import Path

from inspect_ai import eval_async

from agentbench.domains.procurement.inspect_tasks import procurement


async def main():
    logs = await eval_async(procurement(), model="mockllm/model", max_samples=2,
                            log_dir=str(Path("data/procurement/inspect_logs")), ctl_server=False)
    assert len(logs) == 1 and logs[0].status == "success"
    assert len(logs[0].samples) == 30
    assert all(s.scores["procurement_constraints"].value == "C" for s in logs[0].samples)
    print("30 synthetic known-answer scripts passed procurement rules through Inspect; semantic pending.")


if __name__ == "__main__":
    asyncio.run(main())

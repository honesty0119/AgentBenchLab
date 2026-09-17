from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from filelock import FileLock

from agentbench.agents import PROMPT_HASH
from agentbench.dataset import select_cases
from agentbench.grading import SCORER_VERSION, scorer_fingerprint
from agentbench.schema import Case, RunConfig, digest
from agentbench.storage import Store
from agentbench.settings import endpoint, validate_endpoint


def code_manifest():
    root = Path(__file__).resolve().parents[1]
    hashes = {}
    for folder in ("app", "agentbench"):
        for path in sorted((root / folder).rglob("*.py")):
            hashes[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=5).stdout.strip() or "uncommitted"
    except (OSError, subprocess.TimeoutExpired):
        commit = "unknown"
    return {"git_commit": commit, "source_hash": digest(hashes), "python": sys.version.split()[0],
            "inspect_version": version("inspect-ai")}


def create_run(store: Store, config: RunConfig):
    config = config.model_copy(deep=True)
    if config.agent == "openai-compatible":
        config.model_endpoint = validate_endpoint(config.model_endpoint or endpoint("AGENTBENCH_BASE_URL"))
        config.model = config.model or os.environ.get("AGENTBENCH_MODEL", "")
        if not config.model or not os.environ.get("AGENTBENCH_API_KEY"):
            raise ValueError("Configure AGENTBENCH_MODEL and AGENTBENCH_API_KEY first")
    else:
        config.model = config.agent
    cases, dataset_hash = select_cases(config, store.root)
    if config.intervention == "reference_evidence" and any(not c.evidence_paths for c in cases):
        raise ValueError("Reference evidence intervention requires evidence_paths on every selected case")
    config.dataset_hash = dataset_hash
    manifest = {"dataset_hash": dataset_hash, "scorer_version": SCORER_VERSION, "scorer_hash": scorer_fingerprint(),
                "prompt_hash": PROMPT_HASH, "code": code_manifest(),
                "demo": config.agent.startswith("demo-"), "cases": [c.model_dump() for c in cases],
                "demo_script_hash": hashlib.sha256((Path(__file__).parent / "fixtures" / "demo_scripts.json").read_bytes()).hexdigest(),
                "generation": {"temperature": 0, "max_tokens": config.max_output_tokens, "parallel_tool_calls": False,
                               "max_http_retries": config.http_retries, "tool_timeout_seconds": config.tool_timeout_seconds},
                "intervention": config.intervention,
                "model_endpoint": config.model_endpoint if config.agent == "openai-compatible" else None}
    return store.create_run(config.model_dump(), manifest)


async def execute_run(store: Store, id: str):
    run = store.get_run(id)
    config = RunConfig.model_validate(run["config"])
    if config.agent == "openai-compatible" and not config.model_endpoint:
        store.set_status(id, "failed", "Legacy run lacks a pinned endpoint; create a new experiment")
        return
    # Resume must never mix changed agent/grader code with earlier trial results.
    if run["manifest"]["code"]["source_hash"] != code_manifest()["source_hash"]:
        store.set_status(id, "failed", "Source changed since experiment creation; create a new run")
        return
    if config.agent.startswith("demo-"):
        current = hashlib.sha256((Path(__file__).parent / "fixtures" / "demo_scripts.json").read_bytes()).hexdigest()
        if run["manifest"]["demo_script_hash"] != current:
            store.set_status(id, "failed", "Demo fixture changed; create a new run")
            return
    cases = {c["id"]: Case.model_validate(c) for c in run["manifest"]["cases"]}
    try:
        from inspect_ai import eval_async
        from agentbench.inspect_tasks import make_task
        pending = [t for t in store.trials(id) if t["status"] not in {"completed", "error"}]
        if pending:
            task = make_task(list(cases.values()), config, pending, store, id)
            logs = await eval_async(task, model="mockllm/model",
                max_samples=config.concurrency, log_dir=str(store.root / "inspect_logs" / id),
                fail_on_error=False, ctl_server=False)
            if not logs or any(log.status != "success" for log in logs):
                store.set_status(id, "failed", "Inspect execution did not complete; see local Inspect log")
                return
        store.set_status(id, "cancelled" if store.get_run(id)["cancel"] else "completed")
    except asyncio.CancelledError:
        store.set_status(id, "interrupted")
        raise


async def worker(store: Store, once=False):
    lock = FileLock(str(store.root / ".worker.lock"), timeout=0)
    with lock:
        store.recover()
        while True:
            id = store.claim()
            if id:
                try:
                    await execute_run(store, id)
                except Exception as exc:
                    store.set_status(id, "failed", type(exc).__name__)
            if once:
                return
            if not id:
                await asyncio.sleep(0.5)

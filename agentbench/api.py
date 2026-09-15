from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agentbench.analysis import calibration, compare, summary
from agentbench.dataset import import_dataset, list_datasets, select_cases
from agentbench.judge import judge_trial
from agentbench.runner import create_run
from agentbench.schema import DatasetUpload, JudgeRequest, Review, RunConfig
from agentbench.storage import Store, encode

STATIC = Path(__file__).parent / "static"


def create_app(root=None, start_worker=True):
    store = Store(root)
    csrf = secrets.token_hex(24)

    @asynccontextmanager
    async def lifespan(app):
        process = None
        output = None
        if start_worker:
            output = (store.root / "worker.log").open("ab")
            env = dict(os.environ, AGENTBENCH_DATA_DIR=str(store.root), PYTHONUTF8="1")
            process = subprocess.Popen([sys.executable, "-m", "agentbench", "worker"],
                env=env, stdout=output, stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        app.state.worker = process
        try:
            yield
        finally:
            if process and process.poll() is None:
                process.terminate()
                await asyncio.to_thread(process.wait, 10)
            if output:
                output.close()

    app = FastAPI(title="AgentBench Lab", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("x-agentbench-token") != csrf:
                return JSONResponse({"detail": "Missing local session token"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Cross-origin writes are disabled"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Record not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/config")
    def configuration():
        p = getattr(app.state, "worker", None)
        return {"csrf_token": csrf, "model_configured": bool(os.environ.get("AGENTBENCH_API_KEY")
                 and os.environ.get("AGENTBENCH_MODEL")),
                "judge_configured": bool(os.environ.get("AGENTBENCH_JUDGE_API_KEY")
                 and os.environ.get("AGENTBENCH_JUDGE_MODEL")),
                "model": os.environ.get("AGENTBENCH_MODEL", ""),
                "worker_alive": p is not None and p.poll() is None}

    @app.get("/api/cases")
    def cases(dataset_hash: str | None = None):
        cases, version = select_cases(RunConfig(dataset_hash=dataset_hash, agent="openai-compatible"), store.root)
        return {"dataset_hash": version, "cases": [c.model_dump() for c in cases]}

    @app.get("/api/datasets")
    def datasets():
        return list_datasets(store.root)

    @app.post("/api/datasets", status_code=201)
    def upload_dataset(dataset: DatasetUpload):
        return import_dataset(store.root, dataset)

    @app.get("/api/runs")
    def runs():
        return [{"id": r["id"], "name": r["config"]["name"], "created": r["created"],
                 "config": r["config"], "summary": summary(store, r["id"]), "error": r["error"]}
                for r in store.list_runs()]

    @app.post("/api/runs", status_code=201)
    def launch(config: RunConfig):
        active = [r for r in store.list_runs() if r["status"] in {"queued", "running"}]
        if len(active) >= 10:
            raise HTTPException(429, "Queue limit reached")
        return create_run(store, config)

    @app.get("/api/runs/{id}")
    def run(id: str):
        result = store.get_run(id)
        result["summary"] = summary(store, id)
        result["trials"] = [{k: v for k, v in t.items() if k != "result"} for t in store.trials(id)]
        return result

    @app.post("/api/runs/{id}/cancel")
    def cancel(id: str):
        store.cancel(id)
        return {"ok": True}

    @app.post("/api/runs/{id}/resume")
    def resume(id: str):
        store.resume(id)
        return {"ok": True}

    @app.get("/api/runs/{id}/export")
    def export(id: str):
        run = store.get_run(id)
        payload = {"run": run, "summary": summary(store, id),
                   "trials": [store.get_trial(t["id"]) for t in store.trials(id)]}
        return Response(encode(payload), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="agentbench-{run["id"]}.json"'})

    @app.get("/api/compare")
    def comparison(base: str, candidate: str):
        return compare(store, base, candidate)

    @app.get("/api/trials/{id}")
    def trial(id: str):
        t = store.get_trial(id)
        run = store.get_run(t["run_id"])
        t["case"] = next(c for c in run["manifest"]["cases"] if c["id"] == t["case_id"])
        return t

    @app.get("/api/trials/{id}/blind")
    def blind(id: str):
        t = store.get_trial(id)
        run = store.get_run(t["run_id"])
        c = next(c for c in run["manifest"]["cases"] if c["id"] == t["case_id"])
        r = t["result"] or {}
        return {"id": id, "task": c["turns"], "source_files": c["files"], "answer": r.get("answer"),
                "final_files": r.get("files"), "final_todos": r.get("todos"),
                "review_dimensions": "内容覆盖、证据支持、表达清晰度；请勿把自己的评分当成多人共识。"}

    @app.post("/api/trials/{id}/reviews")
    def review(id: str, review: Review):
        if store.get_trial(id)["status"] != "completed":
            raise ValueError("Only completed trials can be reviewed")
        return {"id": store.annotate("reviews", id, review.model_dump())}

    @app.post("/api/trials/{id}/judge")
    async def judge(id: str, request: JudgeRequest):
        return await judge_trial(store, id, request.rubric)

    @app.get("/api/runs/{id}/calibration")
    def calibrate(id: str, rubric: str = "v2"):
        store.get_run(id)
        return calibration(store, id, rubric)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app

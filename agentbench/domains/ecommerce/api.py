"""Read-only demonstration surface, isolated from the shared 8765 workbench."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse


def create_app(report, store=None):
    app = FastAPI(title="电商售后评测沙箱")

    @app.get("/api/report")
    def get_report(cohort: str | None = None, regrade: str | None = None):
        try:
            return store.view(report, cohort=cohort, regrade=regrade) if store else report
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/runs")
    def runs():
        return store.runs() if store else []

    @app.get("/api/runs/{run_id}")
    def run_report(run_id: str, cohort: str | None = None, regrade: str | None = None):
        if not store:
            raise HTTPException(404)
        try:
            store.directory(run_id)
            return store.view(store.load(run_id), cohort=cohort, regrade=regrade)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/compare")
    def compare_runs(base: str, candidate: str, metric: str = "rules", cohort: str | None = None,
                     diagnostic: bool = False, base_regrade: str | None = None, candidate_regrade: str | None = None):
        if not store:
            raise HTTPException(404)
        from .experiments import compare
        try:
            store.directory(base)
            store.directory(candidate)
            return compare(store, base, candidate, metric=metric, cohort=cohort, diagnostic=diagnostic,
                           base_regrade=base_regrade, candidate_regrade=candidate_regrade)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (Path(__file__).parent / "demo.html").read_text("utf-8")

    return app

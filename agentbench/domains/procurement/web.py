import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def create_app(report_path: Path):
    # Load one fixed local evidence file, never accept paths from the browser.
    report = json.loads(report_path.read_text("utf-8"))
    app = FastAPI(title="采购方案评测 · draft")

    @app.get("/api/report")
    def read_report():
        return report

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (Path(__file__).parent / "static" / "index.html").read_text("utf-8")

    return app

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def create_app(report_path: Path):
    # Load a saved report only. No model/tool execution or production integrations behind this page.
    report = json.loads(report_path.read_text("utf-8"))
    app = FastAPI(title="IT 工单评测 · 本地合成环境")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (Path(__file__).parent / "static" / "index.html").read_text("utf-8")

    @app.get("/api/report")
    def get_report():
        return report

    return app

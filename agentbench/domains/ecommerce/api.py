"""Read-only demonstration surface, isolated from the shared 8765 workbench."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def create_app(report):
    app = FastAPI(title="电商售后评测沙箱")

    @app.get("/api/report")
    def get_report():
        return report

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (Path(__file__).parent / "demo.html").read_text("utf-8")

    return app

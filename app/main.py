from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.config import Settings
from app.factory import build_runtime
from app.models import (
    ChatRequest,
    ChatResponse,
    CreateSessionRequest,
    RenameSessionRequest,
)
from app.runtime import AgentRuntime


def create_app(settings: Settings | None = None, runtime: AgentRuntime | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime = runtime or build_runtime(settings)
    app = FastAPI(title="Minimal Agent Runtime", version="0.1.0")
    app.state.runtime = runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "llm_mode": settings.llm_mode}

    @app.post("/api/sessions", status_code=201)
    async def create_session(body: CreateSessionRequest) -> dict:
        return runtime.store.create_session(body.title)

    @app.get("/api/sessions")
    async def list_sessions() -> list[dict]:
        return runtime.store.list_sessions()

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict:
        try:
            return runtime.store.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/sessions/{session_id}")
    async def rename_session(
        session_id: str, body: RenameSessionRequest
    ) -> dict:
        try:
            return runtime.store.rename_session(session_id, body.title)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> Response:
        try:
            runtime.store.delete_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.get("/api/sessions/{session_id}/messages")
    async def list_messages(session_id: str) -> list[dict]:
        try:
            return runtime.store.list_messages(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/messages", response_model=ChatResponse)
    async def chat(session_id: str, body: ChatRequest) -> ChatResponse:
        try:
            result = await runtime.chat(session_id, body.content)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ChatResponse(session_id=result.session_id, answer=result.answer, steps=result.steps, trace_id=result.trace_id)

    @app.post("/api/sessions/{session_id}/messages/stream")
    async def chat_stream(
        session_id: str, body: ChatRequest
    ) -> StreamingResponse:
        try:
            runtime.store.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        async def events():
            async for event in runtime.chat_stream(
                session_id, body.content
            ):
                yield json.dumps(
                    event, ensure_ascii=False
                ) + "\n"

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/sessions/{session_id}/traces")
    async def list_traces(session_id: str, limit: int = Query(default=200, ge=1, le=1000)) -> list[dict]:
        try:
            return runtime.store.list_traces(session_id, limit=limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).exception("unhandled API error")
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    return app


app = create_app()

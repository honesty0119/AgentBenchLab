from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    """SQLite persistence with a separate connection per operation."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    status TEXT NOT NULL DEFAULT 'idle',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    name TEXT,
                    tool_call_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
                CREATE TABLE IF NOT EXISTS traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    step INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    duration_ms INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_traces_trace ON traces(trace_id, id);
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    due_time TEXT,
                    completed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
            """)

    def create_session(self, title: str | None = None) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions(id, title, status, created_at, updated_at) VALUES (?, ?, 'idle', ?, ?)",
                (session_id, title, now, now),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"session not found: {session_id}")
        return dict(row)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def rename_session(
        self, session_id: str, title: str
    ) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("session title cannot be empty")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (clean_title, utc_now(), session_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"session not found: {session_id}")
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
        if cursor.rowcount == 0:
            raise KeyError(f"session not found: {session_id}")

    def set_session_status(self, session_id: str, status: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now(), session_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"session not found: {session_id}")

    def add_message(self, session_id: str, role: str, content: str = "", *, name: str | None = None, tool_call_id: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO messages(session_id, role, content, name, tool_call_id, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, role, content, name, tool_call_id, json.dumps(metadata or {}, ensure_ascii=False), now),
            )
            connection.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._decode_message(row)

    @staticmethod
    def _decode_message(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
        return value

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        self.get_session(session_id)
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        return [self._decode_message(row) for row in rows]

    def add_trace(self, trace_id: str, session_id: str, step: int, event: str, payload: dict[str, Any] | None = None, duration_ms: int | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO traces(trace_id, session_id, step, event, payload_json, duration_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (trace_id, session_id, step, event, json.dumps(payload or {}, ensure_ascii=False), duration_ms, utc_now()),
            )

    def list_traces(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        self.get_session(session_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM traces WHERE session_id = ? ORDER BY id DESC LIMIT ?", (session_id, limit)
            ).fetchall()
        output = []
        for row in reversed(rows):
            value = dict(row)
            value["payload"] = json.loads(value.pop("payload_json") or "{}")
            output.append(value)
        return output

    def add_todo(self, session_id: str, title: str, due_time: str | None) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO todos(session_id, title, due_time, completed, created_at, updated_at)
                   VALUES (?, ?, ?, 0, ?, ?)""",
                (session_id, title, due_time, now, now),
            )
            row = connection.execute("SELECT * FROM todos WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._decode_todo(row)

    @staticmethod
    def _decode_todo(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["completed"] = bool(value["completed"])
        return value

    def list_todos(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM todos WHERE session_id = ? ORDER BY completed, id", (session_id,)
            ).fetchall()
        return [self._decode_todo(row) for row in rows]

    def complete_todo(self, session_id: str, todo_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                "UPDATE todos SET completed = 1, updated_at = ? WHERE id = ? AND session_id = ?",
                (utc_now(), todo_id, session_id),
            )
            row = connection.execute("SELECT * FROM todos WHERE id = ? AND session_id = ?", (todo_id, session_id)).fetchone()
        if row is None:
            raise KeyError(f"todo not found: {todo_id}")
        return self._decode_todo(row)

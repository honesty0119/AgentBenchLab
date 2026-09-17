from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class Store:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("AGENTBENCH_DATA_DIR", "data")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "lab.db"
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL,
                    status TEXT NOT NULL, config TEXT NOT NULL, manifest TEXT NOT NULL,
                    error TEXT, cancel INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS trials (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    case_id TEXT NOT NULL, repeat INTEGER NOT NULL, status TEXT NOT NULL,
                    result TEXT, grade TEXT, UNIQUE(run_id, case_id, repeat)
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id),
                    created TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS judgements (
                    id INTEGER PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id),
                    created TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS regrades (
                    id INTEGER PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id),
                    created TEXT NOT NULL, payload TEXT NOT NULL
                );
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            raise KeyError("Record not found")
        result = dict(row)
        for field in ("config", "manifest", "result", "grade", "payload"):
            if field in result and result[field] is not None:
                result[field] = json.loads(result[field])
        return result

    def create_run(self, config, manifest):
        id = uuid.uuid4().hex[:16]
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES (?, ?, ?, 'queued', ?, ?, NULL, 0)",
                       (id, now(), now(), encode(config), encode(manifest)))
            for case in manifest["cases"]:
                for repeat in range(config["repeats"]):
                    db.execute("INSERT INTO trials VALUES (?, ?, ?, ?, 'pending', NULL, NULL)",
                               (uuid.uuid4().hex, id, case["id"], repeat))
        return self.get_run(id)

    def get_run(self, id):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM runs WHERE id=?", (id,)).fetchone())

    def list_runs(self):
        with self.connect() as db:
            return [self.decode(r) for r in db.execute("SELECT * FROM runs ORDER BY created DESC LIMIT 200")]

    def trials(self, run_id):
        with self.connect() as db:
            return [self.decode(r) for r in db.execute(
                "SELECT * FROM trials WHERE run_id=? ORDER BY case_id, repeat", (run_id,))]

    def get_trial(self, id):
        with self.connect() as db:
            item = self.decode(db.execute("SELECT * FROM trials WHERE id=?", (id,)).fetchone())
            for table in ("reviews", "judgements", "regrades"):
                item[table] = [self.decode(r) for r in db.execute(
                    f"SELECT * FROM {table} WHERE trial_id=? ORDER BY id", (id,))]
        return item

    def set_trial(self, id, status, result=None, grade=None):
        with self.connect() as db:
            db.execute("UPDATE trials SET status=?, result=?, grade=? WHERE id=?",
                       (status, encode(result) if result is not None else None,
                        encode(grade) if grade is not None else None, id))

    def set_status(self, id, status, error=None):
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?, updated=?, error=? WHERE id=?", (status, now(), error, id))

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM runs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE runs SET status='running', updated=? WHERE id=?", (now(), row["id"]))
            return row["id"]

    def recover(self):
        # Only called while holding the exclusive worker file lock.
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted', updated=? WHERE status='running'", (now(),))
            db.execute("UPDATE trials SET status='pending' WHERE status='running'")

    def cancel(self, id):
        self.get_run(id)
        with self.connect() as db:
            db.execute("UPDATE runs SET cancel=1 WHERE id=?", (id,))
            db.execute("UPDATE runs SET status='cancelled' WHERE id=? AND status='queued'", (id,))

    def resume(self, id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM runs WHERE id=?", (id,)).fetchone()
            if not row:
                raise KeyError(id)
            if row["status"] not in {"interrupted", "cancelled", "failed"}:
                raise ValueError("Only interrupted, cancelled or failed runs can resume")
            db.execute("UPDATE runs SET status='queued', cancel=0, error=NULL, updated=? WHERE id=?", (now(), id))
            db.execute("UPDATE trials SET status='pending' WHERE run_id=? AND status NOT IN ('completed', 'error')", (id,))

    def annotate(self, table, trial_id, payload):
        if table not in {"reviews", "judgements", "regrades"}:
            raise ValueError("Unknown annotation table")
        self.get_trial(trial_id)
        with self.connect() as db:
            cursor = db.execute(f"INSERT INTO {table}(trial_id, created, payload) VALUES (?, ?, ?)",
                                (trial_id, now(), encode(payload)))
        return cursor.lastrowid

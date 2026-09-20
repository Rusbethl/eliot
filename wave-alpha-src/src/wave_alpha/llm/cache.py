from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_response (
    model_id    TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (model_id, prompt_hash)
);
"""


def prompt_hash(system: str, user: str) -> str:
    """Deterministic hash of (system, user) prompt pair."""
    h = hashlib.sha256()
    h.update(system.encode("utf-8"))
    h.update(b"\x00")
    h.update(user.encode("utf-8"))
    return h.hexdigest()


class LLMResponseCache:
    """SQLite cache keyed by (model_id, prompt_hash)."""

    def __init__(self, db_path: Path | str) -> None:
        self._path = str(db_path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._open() as conn:
            conn.executescript(_SCHEMA)

    def _open(self) -> sqlite3.Connection:
        # WAL + busy_timeout so concurrent web-UI fragments don't trip
        # "database is locked" on shared LLM cache writes.
        conn = sqlite3.connect(self._path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def get(self, model_id: str, prompt_hash_: str) -> str | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT response FROM llm_response WHERE model_id=? AND prompt_hash=?",
                (model_id, prompt_hash_),
            ).fetchone()
        return row[0] if row else None

    def put(self, model_id: str, prompt_hash_: str, *, response: str, created_at: datetime) -> None:
        with self._open() as conn:
            conn.execute(
                "INSERT INTO llm_response(model_id, prompt_hash, response, created_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(model_id, prompt_hash) DO UPDATE SET "
                "response=excluded.response, created_at=excluded.created_at",
                (model_id, prompt_hash_, response, created_at.isoformat()),
            )

"""SQLite persistence for history snapshots. Pure CRUD; no pipeline knowledge."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from wave_alpha.history.models import Snapshot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ticker          TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    engine_version  TEXT NOT NULL,
    top_count_hash  TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (ticker, as_of)
);
"""

_INDEX = """
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_as_of
    ON snapshots(ticker, as_of DESC);
"""

_OUTCOME_INDEX = """
CREATE INDEX IF NOT EXISTS idx_snapshots_outcome_status
    ON snapshots(ticker, outcome_status);
"""


@dataclass(frozen=True)
class StoredRow:
    ticker: str
    as_of: date
    engine_version: str
    top_count_hash: str
    payload_json: str
    created_at: datetime


_INITIALIZED: set[Path] = set()


def _connect(db_path: Path) -> sqlite3.Connection:
    # WAL + busy_timeout so the web UI's concurrent /counts (writer) and
    # /history (reader) fragments don't trip "database is locked".
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_schema(db_path: Path) -> None:
    if db_path in _INITIALIZED:
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(_SCHEMA)
            conn.execute(_INDEX)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)").fetchall()}
            if "outcome_status" not in cols:
                conn.execute("ALTER TABLE snapshots ADD COLUMN outcome_status TEXT")
            conn.execute(_OUTCOME_INDEX)
    finally:
        conn.close()
    _INITIALIZED.add(db_path)


def upsert(snapshot: Snapshot, *, db_path: Path) -> None:
    _ensure_schema(db_path)
    payload = snapshot.model_dump_json()
    outcome_status = snapshot.outcome.status if snapshot.outcome else None
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO snapshots
                    (ticker, as_of, engine_version, top_count_hash,
                     payload_json, created_at, outcome_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, as_of) DO UPDATE SET
                    engine_version = excluded.engine_version,
                    top_count_hash = excluded.top_count_hash,
                    payload_json   = excluded.payload_json,
                    created_at     = excluded.created_at,
                    outcome_status = excluded.outcome_status
                """,
                (
                    snapshot.ticker,
                    snapshot.as_of.isoformat(),
                    snapshot.engine_version,
                    snapshot.top_count_hash,
                    payload,
                    snapshot.created_at.isoformat(),
                    outcome_status,
                ),
            )
    finally:
        conn.close()


def recent(ticker: str, n: int, *, db_path: Path) -> list[StoredRow]:
    if not db_path.exists():
        return []
    _ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT ticker, as_of, engine_version, top_count_hash, payload_json, created_at
            FROM snapshots
            WHERE ticker = ?
            ORDER BY as_of DESC
            LIMIT ?
            """,
            (ticker, n),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        StoredRow(
            ticker=r[0],
            as_of=date.fromisoformat(r[1]),
            engine_version=r[2],
            top_count_hash=r[3],
            payload_json=r[4],
            created_at=datetime.fromisoformat(r[5]),
        )
        for r in rows
    ]


def unresolved_for(ticker: str, *, db_path: Path) -> list[StoredRow]:
    """Rows where outcome_status IS NULL OR outcome_status = 'pending'.
    Returned ordered ascending by as_of so the resolver walks them in
    chronological order."""
    if not db_path.exists():
        return []
    _ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT ticker, as_of, engine_version, top_count_hash,
                   payload_json, created_at
            FROM snapshots
            WHERE ticker = ?
              AND (outcome_status IS NULL OR outcome_status = 'pending')
            ORDER BY as_of ASC
            """,
            (ticker,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        StoredRow(
            ticker=r[0],
            as_of=date.fromisoformat(r[1]),
            engine_version=r[2],
            top_count_hash=r[3],
            payload_json=r[4],
            created_at=datetime.fromisoformat(r[5]),
        )
        for r in rows
    ]


def prune_older_than(*, db_path: Path, ticker: str, cutoff: date) -> int:
    """Delete snapshots for `ticker` with `as_of < cutoff` (cutoff is exclusive —
    a row whose `as_of` equals `cutoff` is retained). Returns the row count
    deleted.

    Missing DB returns 0 (idempotent). The `(ticker, as_of)` composite key
    makes the deletion O(log n + k).
    """
    if not db_path.exists():
        return 0
    _ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM snapshots WHERE ticker = ? AND as_of < ?",
                (ticker, cutoff.isoformat()),
            )
            return cur.rowcount
    finally:
        conn.close()


def recent_across_all(*, db_path: Path, limit: int = 6) -> list[StoredRow]:
    """Return the most recent snapshots across all tickers, one row per ticker
    (its newest by `as_of`). Used by the Home page "Recently analyzed" card."""
    if not db_path.exists():
        return []
    _ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT s.ticker, s.as_of, s.engine_version, s.top_count_hash,
                   s.payload_json, s.created_at
            FROM snapshots s
            JOIN (
                SELECT ticker, MAX(as_of) AS max_as_of
                FROM snapshots
                GROUP BY ticker
            ) latest
              ON latest.ticker = s.ticker
             AND latest.max_as_of = s.as_of
            ORDER BY s.as_of DESC, s.ticker ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        StoredRow(
            ticker=r[0],
            as_of=date.fromisoformat(r[1]),
            engine_version=r[2],
            top_count_hash=r[3],
            payload_json=r[4],
            created_at=datetime.fromisoformat(r[5]),
        )
        for r in rows
    ]

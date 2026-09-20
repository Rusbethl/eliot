"""Migration / idempotence tests for the outcome_status column."""

from __future__ import annotations

import sqlite3


def _make_v1_db(db_path):
    """Create a v1-shape snapshots table without outcome_status."""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE snapshots (
                    ticker          TEXT NOT NULL,
                    as_of           TEXT NOT NULL,
                    engine_version  TEXT NOT NULL,
                    top_count_hash  TEXT NOT NULL,
                    payload_json    TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    PRIMARY KEY (ticker, as_of)
                )
                """
            )
            conn.execute("CREATE INDEX idx_snapshots_ticker_as_of ON snapshots(ticker, as_of DESC)")
    finally:
        conn.close()


def _columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(snapshots)").fetchall()}
    finally:
        conn.close()


def test_ensure_schema_adds_outcome_status_column_on_v1_db(tmp_path):
    from wave_alpha.history.store import _INITIALIZED, _ensure_schema

    db = tmp_path / "history.sqlite"
    _make_v1_db(db)
    _INITIALIZED.discard(db)
    assert "outcome_status" not in _columns(db)
    _ensure_schema(db)
    assert "outcome_status" in _columns(db)


def test_ensure_schema_idempotent_when_column_exists(tmp_path):
    from wave_alpha.history.store import _INITIALIZED, _ensure_schema

    db = tmp_path / "history.sqlite"
    _make_v1_db(db)
    _INITIALIZED.discard(db)
    _ensure_schema(db)
    _INITIALIZED.discard(db)
    _ensure_schema(db)  # second call must not raise
    assert "outcome_status" in _columns(db)


def test_ensure_schema_fresh_install_includes_outcome_status(tmp_path):
    from wave_alpha.history.store import _INITIALIZED, _ensure_schema

    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    _ensure_schema(db)
    assert "outcome_status" in _columns(db)

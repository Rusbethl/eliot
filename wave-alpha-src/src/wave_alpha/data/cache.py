from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from wave_alpha.domain import Bar, Interval

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    ticker   TEXT NOT NULL,
    interval TEXT NOT NULL,
    ts       TEXT NOT NULL,
    open     TEXT NOT NULL,
    high     TEXT NOT NULL,
    low      TEXT NOT NULL,
    close    TEXT NOT NULL,
    volume   INTEGER NOT NULL,
    PRIMARY KEY (ticker, interval, ts)
);
"""


class OHLCVCache:
    """SQLite-backed cache. Decimal values stored as TEXT to preserve precision."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        # WAL + long busy_timeout: the web UI fans out ~8 HTMX fragments per
        # deep-dive load, each of which can independently miss the cache and
        # race to write. WAL lets readers proceed during writes; the 30s
        # busy_timeout absorbs short write queues without surfacing
        # "database is locked".
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def put(self, ticker: str, interval: Interval, bars: list[Bar]) -> None:
        if not bars:
            return
        rows = [
            (
                ticker,
                interval.value,
                b.timestamp.isoformat(),
                str(b.open),
                str(b.high),
                str(b.low),
                str(b.close),
                b.volume,
            )
            for b in bars
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO bars (ticker, interval, ts, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def max_ts(self, ticker: str, interval: Interval) -> date | None:
        """Latest timestamp present in the cache for (ticker, interval), or None."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT MAX(ts) FROM bars WHERE ticker=? AND interval=?",
                (ticker, interval.value),
            )
            row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return date.fromisoformat(row[0])

    def get(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        sql = "SELECT ts, open, high, low, close, volume FROM bars WHERE ticker=? AND interval=?"
        params: list = [ticker, interval.value]
        if start:
            sql += " AND ts >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND ts <= ?"
            params.append(end.isoformat())
        sql += " ORDER BY ts ASC"
        with self._conn() as conn:
            cur = conn.execute(sql, params)
            return [
                Bar(
                    timestamp=date.fromisoformat(ts),
                    open=Decimal(o),
                    high=Decimal(h),
                    low=Decimal(low),
                    close=Decimal(c),
                    volume=v,
                )
                for ts, o, h, low, c, v in cur.fetchall()
            ]

"""Tests for the `wave-alpha history backfill-signals` command."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli import history_app as history_cli
from wave_alpha.cli.app import app
from wave_alpha.domain import Bar, Interval
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)


class _FakeProvider:
    def __init__(self, bars_by_key: dict[tuple[str, Interval], list[Bar]]) -> None:
        self._bars = bars_by_key

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = self._bars.get((ticker, interval), [])
        return [b for b in bars if b.timestamp <= end]


def _make_bars(start: date, n: int, interval: Interval, base: float = 100.0) -> list[Bar]:
    """Build an Elliott-flavored 5-3 zigzag walk so the multi-degree ZigZag
    detector at degree=2 finds pivots. A monotonic ramp produces zero counts
    and is useless for testing the ranked-output path. Mirrors the helper in
    tests/history/test_replay_snapshot.py."""
    segments = [
        (0.30, base, base * 1.30),  # W1 up
        (0.15, base * 1.30, base * 1.15),  # W2 down
        (0.40, base * 1.15, base * 1.75),  # W3 up
        (0.15, base * 1.75, base * 1.58),  # W4 down
        (0.30, base * 1.58, base * 1.95),  # W5 up
        (0.25, base * 1.95, base * 1.58),  # WA down
        (0.15, base * 1.58, base * 1.75),  # WB up
        (0.30, base * 1.75, base * 1.40),  # WC down
    ]
    closes: list[float] = []
    for frac, a, b in segments:
        seg_n = max(1, round(frac * n))
        for i in range(seg_n):
            t = i / max(1, seg_n - 1) if seg_n > 1 else 1.0
            closes.append(a + t * (b - a))
    closes = closes[:n]
    step = timedelta(days=1) if interval != Interval.WEEKLY else timedelta(days=7)
    out: list[Bar] = []
    for i, c in enumerate(closes):
        cd = Decimal(f"{c:.4f}")
        rng = Decimal(f"{c * 0.003:.4f}")
        out.append(
            Bar(
                timestamp=start + i * step,
                open=cd,
                high=cd + rng,
                low=cd - rng,
                close=cd,
                volume=1_000_000,
            )
        )
    return out


def _seed_legacy_snapshot(db_path: Path, ticker: str, as_of: date) -> None:
    """Seed a v1-style snapshot WITHOUT max_holding_bars — the unresolvable case."""
    snap = Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version="0.7.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=1,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            )
        ],
        signal=SnapshotSignal(
            direction="long",
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
            target_price=Decimal("110"),
            rr=2.0,
            count_id="C1",
            count_pattern="impulse",
            wave_label="3",
            degree=1,
            confidence=0.7,
            expected_bars_to_target=20,
            max_holding_bars=None,  # the bug we're backfilling
        ),
        coherence=None,
        top_count_hash="h_legacy",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    store.upsert(snap, db_path=db_path)


@pytest.fixture
def fake_provider() -> _FakeProvider:
    daily = _make_bars(date(2022, 1, 1), 700, Interval.DAILY)
    weekly = _make_bars(date(2022, 1, 1), 100, Interval.WEEKLY)
    h4 = _make_bars(date(2022, 1, 1), 700, Interval.HOURLY_4)
    return _FakeProvider(
        {
            ("AAPL", Interval.DAILY): daily,
            ("AAPL", Interval.WEEKLY): weekly,
            ("AAPL", Interval.HOURLY_4): h4,
            ("MSFT", Interval.DAILY): daily,
            ("MSFT", Interval.WEEKLY): weekly,
            ("MSFT", Interval.HOURLY_4): h4,
        }
    )


def test_backfill_signals_replays_unresolvable_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_provider: _FakeProvider
) -> None:
    db_path = tmp_path / "history.sqlite"
    _seed_legacy_snapshot(db_path, "AAPL", date(2024, 6, 30))

    monkeypatch.setattr(history_cli, "_history_db_path", lambda: db_path)
    monkeypatch.setattr(history_cli, "_default_provider_for_backfill", lambda: fake_provider)

    runner = CliRunner()
    result = runner.invoke(app, ["history", "backfill-signals", "--ticker", "AAPL"])
    assert result.exit_code == 0, result.output
    assert "AAPL" in result.output

    # The row was upserted with the new schema → max_holding_bars now populated.
    rows = store.recent("AAPL", n=10, db_path=db_path)
    assert len(rows) == 1
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.signal is not None
    assert snap.signal.max_holding_bars is not None


def test_backfill_signals_all_iterates_every_ticker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_provider: _FakeProvider
) -> None:
    db_path = tmp_path / "history.sqlite"
    _seed_legacy_snapshot(db_path, "AAPL", date(2024, 6, 30))
    _seed_legacy_snapshot(db_path, "MSFT", date(2024, 6, 30))

    monkeypatch.setattr(history_cli, "_history_db_path", lambda: db_path)
    monkeypatch.setattr(history_cli, "_default_provider_for_backfill", lambda: fake_provider)

    runner = CliRunner()
    result = runner.invoke(app, ["history", "backfill-signals", "--all", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    tickers_seen = {t["ticker"] for t in payload["tickers"]}
    assert tickers_seen == {"AAPL", "MSFT"}
    assert all(t["replayed"] >= 1 for t in payload["tickers"])


def test_backfill_signals_dry_run_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_provider: _FakeProvider
) -> None:
    db_path = tmp_path / "history.sqlite"
    _seed_legacy_snapshot(db_path, "AAPL", date(2024, 6, 30))
    original = Snapshot.model_validate_json(
        store.recent("AAPL", n=1, db_path=db_path)[0].payload_json
    )

    monkeypatch.setattr(history_cli, "_history_db_path", lambda: db_path)
    monkeypatch.setattr(history_cli, "_default_provider_for_backfill", lambda: fake_provider)

    runner = CliRunner()
    result = runner.invoke(app, ["history", "backfill-signals", "--ticker", "AAPL", "--dry-run"])
    assert result.exit_code == 0
    after = Snapshot.model_validate_json(store.recent("AAPL", n=1, db_path=db_path)[0].payload_json)
    assert after.signal == original.signal  # unchanged
    assert after.signal.max_holding_bars is None

"""Tests for the wave-alpha resolve CLI command."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.domain import Bar, Interval
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)
from wave_alpha.history.store import _INITIALIZED, recent, upsert


def _seed_pending(db_path):
    snap = Snapshot(
        ticker="AAPL",
        as_of=date(2026, 1, 1),
        engine_version="0.7.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=2,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=date(2026, 1, 1), price=Decimal("100"))],
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
            degree=2,
            confidence=0.7,
            expected_bars_to_target=20,
            max_holding_bars=60,
        ),
        coherence=None,
        top_count_hash="h1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    upsert(snap, db_path=db_path)


class _FakeProvider:
    """Return a target-hit bar series for AAPL daily."""

    def fetch(self, ticker, interval, *, end=None, start=None, **kw):
        if interval != Interval.DAILY:
            return []
        return [
            Bar(
                timestamp=date(2026, 1, 2),
                interval=Interval.DAILY,
                open=Decimal("101"),
                high=Decimal("105"),
                low=Decimal("99"),
                close=Decimal("103"),
                volume=1_000_000,
            ),
            Bar(
                timestamp=date(2026, 1, 3),
                interval=Interval.DAILY,
                open=Decimal("103"),
                high=Decimal("112"),
                low=Decimal("102"),
                close=Decimal("111"),
                volume=1_000_000,
            ),
        ]


def test_resolve_command_resolves_pending(monkeypatch, tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    _seed_pending(db)

    from wave_alpha.cli import app as cli_app

    monkeypatch.setattr(cli_app, "_DEFAULT_DATA_DIR", tmp_path)
    monkeypatch.setattr(cli_app, "_default_provider", lambda: _FakeProvider())

    runner = CliRunner()
    result = runner.invoke(app, ["resolve", "--ticker", "AAPL"])
    assert result.exit_code == 0, result.stdout
    assert "AAPL" in result.stdout

    rows = recent("AAPL", n=10, db_path=db)
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.outcome is not None
    assert snap.outcome.status == "target"


def test_resolve_command_json_output(monkeypatch, tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    _seed_pending(db)

    from wave_alpha.cli import app as cli_app

    monkeypatch.setattr(cli_app, "_DEFAULT_DATA_DIR", tmp_path)
    monkeypatch.setattr(cli_app, "_default_provider", lambda: _FakeProvider())

    runner = CliRunner()
    result = runner.invoke(app, ["resolve", "--ticker", "AAPL", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["tickers"][0]["ticker"] == "AAPL"
    assert payload["tickers"][0]["rows_resolved"] == 1

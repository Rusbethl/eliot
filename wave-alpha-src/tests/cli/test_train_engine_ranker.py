"""CLI tests for `wave-alpha train engine-ranker`."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import sin
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.domain import Bar, Interval
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCount,
    SnapshotOutcome,
    SnapshotPivot,
)


class _FakeProvider:
    def __init__(self, bars: dict[tuple[str, Interval], list[Bar]]):
        self._bars = bars

    def fetch(self, ticker, interval, *, end):
        return [b for b in self._bars.get((ticker, interval), []) if b.timestamp <= end]


def _make_bars(start: date, n: int, interval: Interval) -> list[Bar]:
    out = []
    step_days = 1 if interval == Interval.DAILY else (7 if interval == Interval.WEEKLY else 1)
    for i in range(n):
        base = 100.0 + 30.0 * sin(i / 8.0) + i * 0.1
        price = Decimal(str(round(base, 2)))
        out.append(
            Bar(
                timestamp=start + timedelta(days=i * step_days),
                interval=interval,
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=1_000_000,
            )
        )
    return out


def _seed(db_path: Path, ticker: str, as_of: date, status: str) -> None:
    """Seed a resolved snapshot into history.sqlite."""
    snap = Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version="0.13.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=2,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            )
        ],
        signal=None,
        coherence=None,
        outcome=SnapshotOutcome(
            status=status,
            exit_date=as_of + timedelta(days=20),
            exit_price=Decimal("105") if status == "target" else Decimal("95"),
            bars_held=10,
            realized_R=1.0 if status == "target" else -1.0,
            last_observed_date=as_of + timedelta(days=20),
            bars_elapsed=10,
            unrealized_R=0.0,
            mfe_R=1.2,
            mae_R=-0.3,
            resolved_at=datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC)
            + timedelta(days=20),
        ),
        top_count_hash=f"h_{ticker}_{as_of.isoformat()}",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    store.upsert(snap, db_path=db_path)


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    # Seed 6 quarterly snapshots
    for d, s in [
        (date(2022, 3, 31), "target"),
        (date(2022, 6, 30), "stop"),
        (date(2022, 9, 30), "target"),
        (date(2022, 12, 31), "target"),
        (date(2023, 3, 31), "stop"),
        (date(2023, 6, 30), "target"),
    ]:
        _seed(db_path, "AAPL", d, s)

    daily = _make_bars(date(2020, 1, 1), 1500, Interval.DAILY)
    weekly = _make_bars(date(2020, 1, 1), 220, Interval.WEEKLY)
    h4 = _make_bars(date(2020, 1, 1), 1500, Interval.HOURLY_4)
    provider = _FakeProvider(
        {
            ("AAPL", Interval.DAILY): daily,
            ("AAPL", Interval.WEEKLY): weekly,
            ("AAPL", Interval.HOURLY_4): h4,
        }
    )

    artifact_path = tmp_path / "engine_ranker_logistic_v1.json"

    from wave_alpha.cli import app as cli_app_mod

    monkeypatch.setattr(cli_app_mod, "_engine_ranker_history_db", lambda: db_path, raising=False)
    monkeypatch.setattr(cli_app_mod, "_engine_ranker_provider", lambda: provider, raising=False)
    monkeypatch.setattr(
        cli_app_mod,
        "_engine_ranker_default_artifact_path",
        lambda: artifact_path,
        raising=False,
    )
    return tmp_path, artifact_path


def test_dry_run_prints_plan_writes_nothing(setup_env) -> None:
    _tmp_path, artifact_path = setup_env
    runner = CliRunner()
    result = runner.invoke(app, ["train", "engine-ranker", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "dataset" in result.output.lower()
    assert not artifact_path.exists()


def test_write_blocked_without_promote_verdict(setup_env) -> None:
    """Synthetic data is unlikely to produce a real PROMOTE; --write without
    --force should refuse to write."""
    _, artifact_path = setup_env
    runner = CliRunner()
    result = runner.invoke(app, ["train", "engine-ranker", "--write"])
    if "verdict=PROMOTE" not in result.output:
        # Either HOLD or INSUFFICIENT_DATA → non-zero exit + no artifact
        assert result.exit_code == 1, result.output
        assert not artifact_path.exists()


def test_force_writes_artifact_with_gate_recorded(setup_env) -> None:
    _, artifact_path = setup_env
    runner = CliRunner()
    result = runner.invoke(app, ["train", "engine-ranker", "--write", "--force"])
    assert result.exit_code == 0, result.output
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text())
    assert payload["gate"]["verdict"] in ("PROMOTE", "HOLD", "INSUFFICIENT_DATA")
    assert payload["feature_order"] == ["template_fit", "fib", "recency", "coherence"]

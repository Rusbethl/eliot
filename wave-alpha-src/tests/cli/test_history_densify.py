"""Tests for the `wave-alpha history densify` command."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli import history_app as history_cli
from wave_alpha.cli.app import app
from wave_alpha.domain import Bar, Interval
from wave_alpha.history import store


class _FakeProvider:
    def __init__(self, bars_by_key: dict[tuple[str, Interval], list[Bar]]) -> None:
        self._bars = bars_by_key

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = self._bars.get((ticker, interval), [])
        return [b for b in bars if b.timestamp <= end]


def _make_bars(start: date, n: int, interval: Interval, base: float = 100.0) -> list[Bar]:
    """Build an Elliott-flavored 5-3 zigzag walk so the multi-degree ZigZag
    detector at degree=2 finds pivots. Mirrors the helper in
    tests/cli/test_history_backfill_signals.py — a strict-monotonic ramp
    produces zero counts and would make the ranked-output assertion fail."""
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


def test_quarterly_dates_endpoints_inclusive() -> None:
    """Pure unit: 2018-Q1 through 2018-Q4 -> 4 quarter-end dates."""
    out = history_cli._quarterly_dates(date(2018, 1, 1), date(2018, 12, 31))
    assert out == [date(2018, 3, 31), date(2018, 6, 30), date(2018, 9, 30), date(2018, 12, 31)]


def test_densify_dry_run_prints_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    universe = tmp_path / "u.txt"
    universe.write_text("AAPL\nMSFT\n")
    monkeypatch.setattr(history_cli, "_history_db_path", lambda: tmp_path / "history.sqlite")
    monkeypatch.setattr(history_cli, "_default_provider_for_backfill", lambda: _FakeProvider({}))

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "history",
            "densify",
            "--universe",
            str(universe),
            "--start",
            "2018-01-01",
            "--end",
            "2018-12-31",
            "--cadence",
            "quarterly",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    # 2 tickers x 4 quarters = 8 attempts
    assert "8" in result.output
    # Nothing written
    assert not (tmp_path / "history.sqlite").exists()


def test_densify_writes_and_skips_missing_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = tmp_path / "u.txt"
    universe.write_text("AAPL\nGHOST\n")
    db_path = tmp_path / "history.sqlite"

    daily = _make_bars(date(2017, 1, 1), 800, Interval.DAILY)
    weekly = _make_bars(date(2017, 1, 1), 120, Interval.WEEKLY)
    h4 = _make_bars(date(2017, 1, 1), 800, Interval.HOURLY_4)
    provider = _FakeProvider(
        {
            ("AAPL", Interval.DAILY): daily,
            ("AAPL", Interval.WEEKLY): weekly,
            ("AAPL", Interval.HOURLY_4): h4,
            # GHOST has no bars at any interval -> silent skip
        }
    )

    monkeypatch.setattr(history_cli, "_history_db_path", lambda: db_path)
    monkeypatch.setattr(history_cli, "_default_provider_for_backfill", lambda: provider)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "history",
            "densify",
            "--universe",
            str(universe),
            "--start",
            "2018-01-01",
            "--end",
            "2018-12-31",
            "--cadence",
            "quarterly",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    by_ticker = {t["ticker"]: t for t in payload["tickers"]}
    assert by_ticker["AAPL"]["written"] >= 1
    assert by_ticker["GHOST"]["written"] == 0
    assert by_ticker["GHOST"]["skipped"] == 4

    # AAPL rows actually present in DB
    rows = store.recent("AAPL", n=10, db_path=db_path)
    assert len(rows) >= 1

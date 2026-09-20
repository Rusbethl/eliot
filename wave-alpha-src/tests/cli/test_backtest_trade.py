from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.backtest._synthetic import FixedBarsProvider, daily_bars
from wave_alpha.cli.app import app
from wave_alpha.domain import Interval


@pytest.fixture
def universe_file(tmp_path: Path) -> Path:
    f = tmp_path / "universe.txt"
    f.write_text("AAPL\nMSFT\n")
    return f


@pytest.fixture
def fake_provider(monkeypatch):
    bars = daily_bars(
        [(99, 100, 99, 100), (100, 115, 100, 112)],
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    monkeypatch.setattr("wave_alpha.cli.app._default_provider", lambda: provider)
    # Stub pipeline as well — same shape as the trade-layer tests.
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.pipeline import AnalyzeResult
    from wave_alpha.trades.models import TradeSignal

    def fake_run(req, *, provider, llm_mode, llm_runner=None, alpha=0.6):
        sig = (
            TradeSignal(
                ticker=req.ticker,
                as_of=req.as_of,
                direction="long",
                entry_price=Decimal("100"),
                stop_price=Decimal("96"),
                target_price=Decimal("110"),
                count_id="abc123",
                count_pattern="impulse",
                wave_label="5",
                degree=2,
                confidence=0.6,
                expected_bars_to_target=10,
                max_holding_bars=20,
            )
            if req.as_of == date(2024, 1, 1)
            else None
        )
        return AnalyzeResult(
            ticker=req.ticker,
            as_of=req.as_of,
            coherence=CoherenceResult(score=0.8, multiplier=1.0, pairwise={}, fallback_used=None),
            signals=[sig] if sig else [],
        )

    monkeypatch.setattr("wave_alpha.backtest.trade_layer.run_analysis", fake_run)


def test_backtest_trade_writes_markdown(universe_file: Path, fake_provider, tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "trade.md"
    res = runner.invoke(
        app,
        [
            "backtest",
            "trade",
            "--universe",
            str(universe_file),
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-05",
            "--step",
            "1",
            "--out",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    assert out.exists()
    content = out.read_text()
    assert "# Trade Layer Backtest Report" in content
    assert "Closed trades:" in content


def test_backtest_trade_writes_json_and_csv(universe_file: Path, fake_provider, tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "trade.md"
    js = tmp_path / "trade.json"
    csv = tmp_path / "trades.csv"
    res = runner.invoke(
        app,
        [
            "backtest",
            "trade",
            "--universe",
            str(universe_file),
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-05",
            "--out",
            str(out),
            "--json",
            str(js),
            "--trades-csv",
            str(csv),
        ],
    )
    assert res.exit_code == 0, res.output
    assert js.exists()
    assert csv.exists()
    csv_content = csv.read_text()
    assert csv_content.startswith("ticker,") or "ticker" in csv_content.split("\n")[0]


def test_backtest_trade_loads_rules_yaml(universe_file: Path, fake_provider, tmp_path: Path):
    rules_yaml = tmp_path / "rules.yaml"
    rules_yaml.write_text("entry_trigger: confirmation\nmin_confidence: 0.30\n")
    runner = CliRunner()
    out = tmp_path / "trade.md"
    res = runner.invoke(
        app,
        [
            "backtest",
            "trade",
            "--universe",
            str(universe_file),
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-05",
            "--rules",
            str(rules_yaml),
            "--out",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    content = out.read_text()
    assert "entry_trigger=confirmation" in content


def test_backtest_trade_rejects_unknown_rules_key(
    universe_file: Path, fake_provider, tmp_path: Path
):
    """Spec §3: unknown keys in rules YAML must raise — not silently default."""
    rules_yaml = tmp_path / "rules.yaml"
    rules_yaml.write_text(
        "entry_trgger: confirmation\n"  # typo: trgger
        "min_confidence: 0.30\n"
    )
    runner = CliRunner()
    out = tmp_path / "trade.md"
    res = runner.invoke(
        app,
        [
            "backtest",
            "trade",
            "--universe",
            str(universe_file),
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-05",
            "--rules",
            str(rules_yaml),
            "--out",
            str(out),
        ],
    )
    assert res.exit_code != 0, "expected non-zero exit on unknown rules key"


def test_backtest_trade_json_sidecar_includes_coverage_4h_and_by_tf_count(
    universe_file: Path, fake_provider, tmp_path: Path
):
    """JSON sidecar carries the new coverage_4h and by_tf_count keys."""
    import json as _json

    runner = CliRunner()
    out_md = tmp_path / "trade.md"
    out_json = tmp_path / "trade.json"
    res = runner.invoke(
        app,
        [
            "backtest",
            "trade",
            "--universe",
            str(universe_file),
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-05",
            "--step",
            "1",
            "--out",
            str(out_md),
            "--json",
            str(out_json),
        ],
    )
    assert res.exit_code == 0, res.output
    payload = _json.loads(out_json.read_text())
    assert "coverage_4h" in payload["headline"]
    assert isinstance(payload["headline"]["coverage_4h"], (int, float))
    assert "by_tf_count" in payload


def test_backtest_trade_csv_ledger_includes_tf_count_column(
    universe_file: Path, fake_provider, tmp_path: Path
):
    """CSV ledger header contains tf_count; rows carry the integer value."""
    import csv as _csv

    runner = CliRunner()
    out_md = tmp_path / "trade.md"
    out_csv = tmp_path / "trades.csv"
    res = runner.invoke(
        app,
        [
            "backtest",
            "trade",
            "--universe",
            str(universe_file),
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-05",
            "--step",
            "1",
            "--out",
            str(out_md),
            "--trades-csv",
            str(out_csv),
        ],
    )
    assert res.exit_code == 0, res.output
    rows = list(_csv.DictReader(out_csv.open()))
    assert rows, "fixture should produce at least one trade"
    assert "tf_count" in rows[0]
    assert rows[0]["tf_count"] in {"2", "3"}


# ---------------------------------------------------------------------------
# Task C1: _warn_if_4h_window_partial helper tests
# ---------------------------------------------------------------------------


def test_warn_if_4h_window_partial_fires_for_old_start(capsys):
    """Helper emits a stderr warning when start is before today - 730d."""
    from wave_alpha.cli.app import _warn_if_4h_window_partial

    _warn_if_4h_window_partial(date(2018, 1, 1))
    out = capsys.readouterr()
    assert "4h timeframe data is unavailable" in out.err


def test_warn_if_4h_window_partial_silent_for_recent_start(capsys):
    """Helper is silent when start is within the 4h window."""
    from wave_alpha.cli.app import _warn_if_4h_window_partial

    _warn_if_4h_window_partial(date.today() - timedelta(days=100))
    out = capsys.readouterr()
    assert out.err == ""


def test_backtest_trade_warns_when_start_outside_4h_window(
    universe_file: Path, fake_provider, tmp_path: Path
):
    """Stderr contains the 4h-history warning when start < today - 730d."""
    runner = CliRunner()
    out_md = tmp_path / "trade.md"
    res = runner.invoke(
        app,
        [
            "backtest",
            "trade",
            "--universe",
            str(universe_file),
            "--start",
            "2018-01-01",
            "--end",
            "2018-12-31",
            "--step",
            "1",
            "--out",
            str(out_md),
        ],
    )
    assert "4h timeframe data is unavailable" in res.stderr


def test_backtest_trade_silent_when_start_inside_4h_window(
    universe_file: Path, fake_provider, tmp_path: Path
):
    """No warning when start is within the 4h window (today - 400d)."""
    runner = CliRunner()
    out_md = tmp_path / "trade.md"
    start_iso = (date.today() - timedelta(days=400)).isoformat()
    end_iso = (date.today() - timedelta(days=30)).isoformat()
    res = runner.invoke(
        app,
        [
            "backtest",
            "trade",
            "--universe",
            str(universe_file),
            "--start",
            start_iso,
            "--end",
            end_iso,
            "--step",
            "1",
            "--out",
            str(out_md),
        ],
    )
    assert "4h timeframe data is unavailable" not in res.stderr


def test_backtest_count_warns_when_start_outside_4h_window(
    universe_file: Path, monkeypatch, tmp_path: Path
):
    """count subcommand fires the same 4h warning for old start dates."""
    # Patch _warn_if_4h_window_partial is already wired — just invoke and check stderr.
    runner = CliRunner()
    out = tmp_path / "count.md"
    res = runner.invoke(
        app,
        [
            "backtest",
            "count",
            "--universe",
            str(universe_file),
            "--start",
            "2018-01-01",
            "--end",
            "2018-12-31",
            "--out",
            str(out),
        ],
    )
    assert "4h timeframe data is unavailable" in res.stderr


def test_backtest_pivot_warns_when_start_outside_4h_window(
    universe_file: Path, monkeypatch, tmp_path: Path
):
    """pivot subcommand fires the same 4h warning for old start dates."""
    runner = CliRunner()
    out = tmp_path / "pivot.md"
    res = runner.invoke(
        app,
        [
            "backtest",
            "pivot",
            "--universe",
            str(universe_file),
            "--start",
            "2018-01-01",
            "--end",
            "2018-12-31",
            "--out",
            str(out),
        ],
    )
    assert "4h timeframe data is unavailable" in res.stderr

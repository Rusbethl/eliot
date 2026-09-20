"""Smoke test for `wave-alpha backtest grid` CLI subcommand.

Stubs out run_trade_layer to keep runtime trivial; the real
backtest is exercised by Task 8 (Phase 1 run).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app


def _patch_run_trade_layer(monkeypatch):
    """Replace run_trade_layer with a stub returning a synthetic result."""
    from wave_alpha.backtest import grid as grid_mod
    from wave_alpha.backtest.trade_layer import TradeLayerResult, TradeRecord

    def _trade(R: float) -> TradeRecord:  # noqa: N803
        return TradeRecord(
            ticker="TEST",
            signal_as_of=date(2024, 1, 1),
            fill_date=date(2024, 1, 2),
            fill_price=Decimal("100"),
            exit_date=date(2024, 1, 10),
            exit_price=Decimal("110"),
            exit_reason="target" if R > 0 else "stop",
            direction="long",
            pattern="impulse",
            wave_label="5",
            coherence_score=0.8,
            coherence_bucket="full",
            bars_held=8,
            risk_per_share=Decimal("1"),
            realized_pnl_per_share=Decimal(str(R)),
            realized_R=R,
            confidence=0.7,
            tf_count=2,
            degree=2,
            expected_bars_to_target=25,
        )

    def fake_run_trade_layer(cfg, *, provider, rules):
        # Synthetic positive-expectancy result so the verdict is GO
        trades = [_trade(+0.5) for _ in range(150)] + [_trade(-1.0) for _ in range(150)]
        return TradeLayerResult(trades=trades)

    monkeypatch.setattr(grid_mod, "run_trade_layer", fake_run_trade_layer)


def test_backtest_grid_writes_artifacts(monkeypatch, tmp_path: Path) -> None:
    _patch_run_trade_layer(monkeypatch)
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\n")
    grid_yaml = tmp_path / "grid.yaml"
    grid_yaml.write_text(
        "axes:\n"
        "  - name: min_confidence\n"
        "    levels: [0.45, 0.55]\n"
        "fixed:\n"
        "  stop_source: count_invalidation\n"
        "  target_source: fib_extension\n"
    )
    out_dir = tmp_path / "out"

    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "backtest",
            "grid",
            "--universe",
            str(universe_file),
            "--start",
            "2024-01-01",
            "--end",
            "2024-06-30",
            "--step",
            "5",
            "--grid",
            str(grid_yaml),
            "--out",
            str(out_dir),
            "--phase-name",
            "Phase 1",
        ],
    )
    assert res.exit_code == 0, res.output
    # Top-level summary
    grid_md = out_dir / "grid.md"
    assert grid_md.exists()
    assert "Grid Sweep Report" in grid_md.read_text()

    # summary.csv shape — header includes axis-name columns; row count
    # matches cell count; cell_id is in the deterministic minconf-<level>
    # form.
    import csv as _csv

    summary_csv = out_dir / "summary.csv"
    assert summary_csv.exists()
    with summary_csv.open() as fh:
        reader = _csv.DictReader(fh)
        assert "cell_id" in reader.fieldnames
        assert "min_confidence" in reader.fieldnames  # axis name appears
        assert "expectancy" in reader.fieldnames
        rows = list(reader)
    assert len(rows) == 2  # 2 cells from the 1-axis 2-level config
    cell_ids = [r["cell_id"] for r in rows]
    assert "minconf-0.45" in cell_ids
    assert "minconf-0.55" in cell_ids

    # Per-cell artifacts — verify cell directory naming AND each artifact's
    # internal shape, not just file existence.
    cells_dir = out_dir / "cells"
    assert cells_dir.exists()
    cell_dirs = sorted(cells_dir.iterdir())
    assert len(cell_dirs) == 2
    cell_dir_names = {d.name for d in cell_dirs}
    assert cell_dir_names == {"minconf-0.45", "minconf-0.55"}

    import json as _json

    for cell_dir in cell_dirs:
        # report.md: trade-layer markdown shape (validated by sanity-run
        # already; here we just ensure it's non-trivially populated).
        report_md = (cell_dir / "report.md").read_text()
        assert "Trade Layer Backtest Report" in report_md

        # result.json: must parse as valid JSON and expose the documented
        # top-level envelope keys (cell_id, axis_values, fixed, rules,
        # headline) plus the existing breakdowns from the trade-layer
        # CLI shape.
        payload = _json.loads((cell_dir / "result.json").read_text())
        assert payload["cell_id"] == cell_dir.name
        assert "axis_values" in payload
        assert "min_confidence" in payload["axis_values"]
        assert payload["fixed"]["stop_source"] == "count_invalidation"
        assert payload["rules"]["min_confidence"] == payload["axis_values"]["min_confidence"]
        assert "headline" in payload
        assert payload["headline"]["closed_trades"] == 300
        for breakdown in (
            "by_direction",
            "by_pattern",
            "by_coherence_bucket",
            "by_tf_count",
            "by_year",
        ):
            assert breakdown in payload, f"missing breakdown {breakdown}"

        # trades.csv: header row exposes the TradeRecord field set the
        # sanity-run inspection script depends on.
        with (cell_dir / "trades.csv").open() as fh:
            trades_reader = _csv.DictReader(fh)
            trades_rows = list(trades_reader)
        assert len(trades_rows) == 300  # matches the stubbed result
        for required_field in (
            "ticker",
            "fill_date",
            "exit_date",
            "exit_reason",
            "direction",
            "realized_R",
            "tf_count",
            "degree",
            "expected_bars_to_target",
        ):
            assert required_field in trades_reader.fieldnames, (
                f"trades.csv missing field {required_field}"
            )

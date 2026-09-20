from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.domain import Bar, Interval

runner = CliRunner()


class _RisingProvider:
    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = []
        d = date(2022, 1, 1)
        i = 0
        while d <= date(2024, 12, 31):
            if d.weekday() < 5:
                price = 100 + i * 0.4
                bars.append(
                    Bar(
                        ticker=ticker,
                        interval=interval,
                        timestamp=d,
                        open=Decimal(str(price)),
                        high=Decimal(str(price + 0.5)),
                        low=Decimal(str(price - 0.5)),
                        close=Decimal(str(price)),
                        volume=1_000,
                    )
                )
                i += 1
            d = d + timedelta(days=1)
        return bars


def test_backtest_help_documents_layer_subcommands() -> None:
    res = runner.invoke(app, ["backtest", "--help"])
    assert res.exit_code == 0
    assert "count" in res.stdout
    assert "pivot" in res.stdout


def test_backtest_count_writes_markdown_report(tmp_path: Path, monkeypatch) -> None:
    universe_file = tmp_path / "u.txt"
    universe_file.write_text("AAPL\nMSFT\n")
    out_file = tmp_path / "report.md"

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _RisingProvider())
    res = runner.invoke(
        app,
        [
            "backtest",
            "count",
            "--universe",
            str(universe_file),
            "--start",
            "2024-03-01",
            "--end",
            "2024-06-01",
            "--step",
            "10",
            "--n-bars-forward",
            "20",
            "--out",
            str(out_file),
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert out_file.exists()
    content = out_file.read_text()
    assert "# Count Layer Backtest Report" in content


def test_backtest_pivot_writes_markdown_report(tmp_path: Path, monkeypatch) -> None:
    universe_file = tmp_path / "u.txt"
    universe_file.write_text("AAPL\n")
    out_file = tmp_path / "pivot.md"

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _RisingProvider())
    res = runner.invoke(
        app,
        [
            "backtest",
            "pivot",
            "--universe",
            str(universe_file),
            "--start",
            "2024-03-01",
            "--end",
            "2024-06-01",
            "--out",
            str(out_file),
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert out_file.exists()
    assert "# Pivot Layer Backtest Report" in out_file.read_text()

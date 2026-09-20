import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.domain import Bar, Interval

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class _RisingProvider:
    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = []
        d = date(2024, 1, 1)
        i = 0
        while d <= end:
            if d.weekday() < 5:
                p = 100 + i * 0.4
                bars.append(
                    Bar(
                        timestamp=d,
                        open=Decimal(str(p)),
                        high=Decimal(str(p + 0.5)),
                        low=Decimal(str(p - 0.5)),
                        close=Decimal(str(p)),
                        volume=1_000,
                    )
                )
                i += 1
            d = d + timedelta(days=1)
        return bars


def test_scan_help_documents_flags() -> None:
    res = runner.invoke(app, ["scan", "--help"])
    assert res.exit_code == 0
    stdout = _ANSI.sub("", res.stdout)
    assert "--watchlist" in stdout
    assert "--symbols" in stdout
    assert "--as-of" in stdout


def test_scan_with_inline_symbols(tmp_path: Path, monkeypatch) -> None:
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _RisingProvider())
    monkeypatch.setattr(app_module, "_scan_dir", lambda: tmp_path)
    res = runner.invoke(app, ["scan", "--symbols", "AAPL,MSFT", "--as-of", "2026-05-01"])
    assert res.exit_code == 0, res.stdout
    # One JSON file written
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert "AAPL" in res.stdout
    assert "MSFT" in res.stdout


def test_scan_uses_watchlist_by_default(tmp_path: Path, monkeypatch) -> None:
    wl_path = tmp_path / "wl.yaml"
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir()
    from wave_alpha.cli import app as app_module
    from wave_alpha.watchlist.models import WatchlistEntry
    from wave_alpha.watchlist.store import save_watchlist

    save_watchlist([WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1))], wl_path)
    monkeypatch.setattr(app_module, "_watchlist_path", lambda: wl_path)
    monkeypatch.setattr(app_module, "_scan_dir", lambda: scans_dir)
    monkeypatch.setattr(app_module, "_default_provider", lambda: _RisingProvider())
    res = runner.invoke(app, ["scan", "--as-of", "2026-05-01"])
    assert res.exit_code == 0, res.stdout
    assert "AAPL" in res.stdout


def test_scan_errors_when_no_symbols_and_empty_watchlist(tmp_path: Path, monkeypatch) -> None:
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: tmp_path / "missing.yaml")
    res = runner.invoke(app, ["scan", "--as-of", "2026-05-01"])
    assert res.exit_code != 0
    assert "no symbols" in res.stdout.lower() or "empty" in res.stdout.lower()

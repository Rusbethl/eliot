from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.watchlist.store import load_watchlist

runner = CliRunner()


def test_watchlist_help_documents_subcommands() -> None:
    res = runner.invoke(app, ["watchlist", "--help"])
    assert res.exit_code == 0
    for sub in ("add", "rm", "ls", "import"):
        assert sub in res.stdout


def test_watchlist_add_persists(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    res = runner.invoke(app, ["watchlist", "add", "AAPL"])
    assert res.exit_code == 0
    assert "AAPL" in res.stdout
    entries = load_watchlist(p)
    assert [e.symbol for e in entries] == ["AAPL"]


def test_watchlist_add_idempotent(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    runner.invoke(app, ["watchlist", "add", "AAPL"])
    runner.invoke(app, ["watchlist", "add", "AAPL"])
    assert len(load_watchlist(p)) == 1


def test_watchlist_ls_lists_in_order(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    runner.invoke(app, ["watchlist", "add", "AAPL"])
    runner.invoke(app, ["watchlist", "add", "MSFT"])
    res = runner.invoke(app, ["watchlist", "ls"])
    assert res.exit_code == 0
    out = res.stdout
    assert out.index("AAPL") < out.index("MSFT")


def test_watchlist_rm_removes(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    runner.invoke(app, ["watchlist", "add", "AAPL"])
    runner.invoke(app, ["watchlist", "add", "MSFT"])
    res = runner.invoke(app, ["watchlist", "rm", "AAPL"])
    assert res.exit_code == 0
    assert [e.symbol for e in load_watchlist(p)] == ["MSFT"]


def test_watchlist_import_csv(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    csv_file = tmp_path / "in.csv"
    csv_file.write_text("Symbol\nAAPL\nMSFT\nNVDA\n")
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    res = runner.invoke(app, ["watchlist", "import", str(csv_file), "--format", "csv"])
    assert res.exit_code == 0, res.stdout
    assert "imported 3" in res.stdout
    assert sorted(e.symbol for e in load_watchlist(p)) == ["AAPL", "MSFT", "NVDA"]


def test_watchlist_import_tv_txt(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    tv_file = tmp_path / "tv.txt"
    tv_file.write_text("###Mega###,NASDAQ:AAPL,NASDAQ:NVDA")
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    res = runner.invoke(app, ["watchlist", "import", str(tv_file), "--format", "tv-txt"])
    assert res.exit_code == 0
    assert "imported 2" in res.stdout

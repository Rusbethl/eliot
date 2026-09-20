from datetime import date
from pathlib import Path

import pytest

from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.watchlist.store import (
    DEFAULT_WATCHLIST_PATH,
    add_entry,
    load_watchlist,
    remove_entry,
    save_watchlist,
)


def test_default_watchlist_path_under_wave_alpha_home() -> None:
    assert DEFAULT_WATCHLIST_PATH.name == "watchlist.yaml"
    assert ".wave_alpha" in str(DEFAULT_WATCHLIST_PATH)


def test_load_watchlist_returns_empty_when_missing(tmp_path: Path) -> None:
    p = tmp_path / "missing.yaml"
    assert load_watchlist(p) == []


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    entries = [
        WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1), notes="megacap"),
        WatchlistEntry(symbol="NVDA", added_on=date(2026, 5, 2)),
    ]
    save_watchlist(entries, p)
    loaded = load_watchlist(p)
    assert loaded == entries


def test_add_entry_dedupes_existing_symbol(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    save_watchlist([WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1))], p)
    # Adding AAPL again returns the existing entry, no duplicate written
    result = add_entry("AAPL", path=p, today=date(2026, 5, 5))
    assert result.symbol == "AAPL"
    assert result.added_on == date(2026, 5, 1)  # original date preserved
    assert load_watchlist(p) == [WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1))]


def test_add_entry_normalizes_to_uppercase(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    result = add_entry("aapl", path=p, today=date(2026, 5, 5))
    assert result.symbol == "AAPL"


def test_add_entry_rejects_invalid_symbol(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    with pytest.raises(ValueError, match="invalid symbol"):
        add_entry("bad symbol", path=p, today=date(2026, 5, 5))


def test_remove_entry_returns_true_when_removed(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    save_watchlist(
        [
            WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1)),
            WatchlistEntry(symbol="MSFT", added_on=date(2026, 5, 2)),
        ],
        p,
    )
    assert remove_entry("AAPL", path=p) is True
    assert [e.symbol for e in load_watchlist(p)] == ["MSFT"]


def test_remove_entry_returns_false_when_not_present(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    save_watchlist([WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1))], p)
    assert remove_entry("MSFT", path=p) is False

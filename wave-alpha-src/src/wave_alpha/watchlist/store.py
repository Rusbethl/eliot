from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.watchlist.symbol import SYMBOL_RE

DEFAULT_WATCHLIST_PATH = Path.home() / ".wave_alpha" / "watchlist.yaml"


def load_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> list[WatchlistEntry]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or []
    return [WatchlistEntry.model_validate(item) for item in raw]


def save_watchlist(entries: list[WatchlistEntry], path: Path = DEFAULT_WATCHLIST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [e.model_dump(mode="json") for e in entries]
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def _validate_symbol(raw: str) -> str:
    sym = raw.strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError(f"invalid symbol: {raw!r}")
    return sym


def add_entry(
    symbol: str,
    *,
    path: Path = DEFAULT_WATCHLIST_PATH,
    today: date | None = None,
    notes: str | None = None,
) -> WatchlistEntry:
    """Idempotent add: existing symbol returns its current entry unchanged."""
    sym = _validate_symbol(symbol)
    entries = load_watchlist(path)
    for e in entries:
        if e.symbol == sym:
            return e
    new = WatchlistEntry(symbol=sym, added_on=today or date.today(), notes=notes)
    entries.append(new)
    save_watchlist(entries, path)
    return new


def remove_entry(symbol: str, *, path: Path = DEFAULT_WATCHLIST_PATH) -> bool:
    sym = _validate_symbol(symbol)
    entries = load_watchlist(path)
    survivors = [e for e in entries if e.symbol != sym]
    if len(survivors) == len(entries):
        return False
    save_watchlist(survivors, path)
    return True

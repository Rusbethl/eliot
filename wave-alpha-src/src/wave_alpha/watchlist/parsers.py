from __future__ import annotations

import csv
import re
from pathlib import Path

from wave_alpha.watchlist.symbol import SYMBOL_RE

_TV_PREFIX_RE = re.compile(r"^[A-Z]+:")  # NASDAQ:, NYSE:, BATS:, etc.
_TV_GROUP_RE = re.compile(r"^###.*###$")


def _normalize(token: str) -> str | None:
    """Strip TV-style exchange prefix; uppercase; validate. None if invalid."""
    s = token.strip()
    if not s:
        return None
    # Detect TV group markers (###Name###). If they contain a ':' they encode
    # an inline symbol (###NASDAQ:AAPL###) and should be processed; otherwise
    # they are section headers and must be skipped.
    if _TV_GROUP_RE.match(s) and ":" not in s:
        return None
    # Strip ### delimiters for inline symbols (###EXCHANGE:SYMBOL###)
    s = s.replace("###", "").strip()
    if not s:
        return None
    s = _TV_PREFIX_RE.sub("", s.upper())
    return s if SYMBOL_RE.match(s) else None


def parse_csv_symbols(path: Path) -> list[str]:
    """Read first column of a CSV, normalize, dedupe.

    Lenient: skips invalid rows silently. Detects and skips a literal
    "Symbol" / "Ticker" header row.
    """
    out: list[str] = []
    seen: set[str] = set()
    with path.open(newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            cell = row[0].strip()
            if cell.lower() in {"symbol", "ticker"}:
                continue
            sym = _normalize(cell)
            if sym is None or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
    return out


def parse_tv_txt_symbols(path: Path) -> list[str]:
    """Parse a TradingView TXT watchlist export.

    Format: comma-separated tokens across one or more lines. Tokens are
    `EXCHANGE:SYMBOL`, bare `SYMBOL`, or `###Group Name###` section markers.
    Strips prefixes, ignores groups, dedupes.
    """
    out: list[str] = []
    seen: set[str] = set()
    text = path.read_text()
    # Split on commas AND newlines so multi-line files work
    for raw_token in re.split(r"[,\n]+", text):
        sym = _normalize(raw_token)
        if sym is None or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out

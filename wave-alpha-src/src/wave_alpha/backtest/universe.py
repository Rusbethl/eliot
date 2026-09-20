from __future__ import annotations

import re
from pathlib import Path

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def load_universe(path: Path) -> list[str]:
    """Load ticker symbols from a text file.

    Format: one symbol per line. Blank lines and lines starting with '#'
    (after leading whitespace) are ignored. Symbols are uppercased and
    deduplicated preserving first-seen order.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sym = line.upper()
        if not _SYMBOL_RE.match(sym):
            raise ValueError(f"invalid symbol: {raw!r}")
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    if not out:
        raise ValueError(f"empty universe: {path}")
    return out

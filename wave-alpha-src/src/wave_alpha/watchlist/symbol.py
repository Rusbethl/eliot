"""Shared ticker-symbol regex used by the watchlist store and parsers."""

from __future__ import annotations

import re

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

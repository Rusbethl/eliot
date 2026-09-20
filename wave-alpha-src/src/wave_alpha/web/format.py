from __future__ import annotations

from datetime import date
from decimal import Decimal


def fmt_decimal(value: Decimal | float | None, *, places: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{places}f}"


def fmt_pct(value: float | None, *, places: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{places}f}%"


def fmt_date(value: date | None) -> str:
    return value.isoformat() if value else "—"

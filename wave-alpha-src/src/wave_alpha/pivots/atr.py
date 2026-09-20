from __future__ import annotations

from decimal import Decimal

from wave_alpha.domain import Bar


def compute_atr(bars: list[Bar], period: int = 14) -> list[Decimal | None]:
    """Wilder's ATR. Returns one entry per bar; first `period` entries are None."""
    if period < 1:
        raise ValueError("period must be >= 1")

    n = len(bars)
    out: list[Decimal | None] = [None] * n
    if n < period + 1:
        return out

    true_ranges: list[Decimal] = [Decimal(0)]
    for i in range(1, n):
        prev_close = bars[i - 1].close
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_close),
            abs(bars[i].low - prev_close),
        )
        true_ranges.append(tr)

    seed = sum(true_ranges[1 : period + 1], Decimal(0)) / Decimal(period)
    out[period] = seed
    for i in range(period + 1, n):
        prev = out[i - 1]
        assert prev is not None
        out[i] = (prev * (period - 1) + true_ranges[i]) / Decimal(period)
    return out

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar
from wave_alpha.trades.derive import _stop_price_atr_multiple


def _series(prices: list[float]) -> list[Bar]:
    out = []
    for i, p in enumerate(prices):
        d = date(2024, 1, 1) + timedelta(days=i)
        # OHLC = price-spread to give ATR something nonzero
        out.append(
            Bar(
                timestamp=d,
                open=Decimal(str(p)),
                high=Decimal(str(p + 1)),
                low=Decimal(str(p - 1)),
                close=Decimal(str(p)),
                volume=1000,
            )
        )
    return out


def test_atr_stop_long_below_anchor():
    bars = _series([100.0] * 30)
    anchor = Decimal("100")
    stop = _stop_price_atr_multiple(
        bars=bars, anchor=anchor, direction="long", multiple=Decimal("2"), period=14
    )
    # ATR is roughly 2.0 (high-low=2 every bar after warmup); stop = 100 - 2*2 = 96
    assert stop == Decimal("96")


def test_atr_stop_short_above_anchor():
    bars = _series([100.0] * 30)
    anchor = Decimal("100")
    stop = _stop_price_atr_multiple(
        bars=bars, anchor=anchor, direction="short", multiple=Decimal("2"), period=14
    )
    assert stop == Decimal("104")


def test_atr_stop_raises_when_atr_unavailable():
    import pytest

    bars = _series([100.0] * 5)  # too short to compute ATR(14)
    with pytest.raises(ValueError, match="ATR unavailable"):
        _stop_price_atr_multiple(
            bars=bars,
            anchor=Decimal("100"),
            direction="long",
            multiple=Decimal("2"),
            period=14,
        )

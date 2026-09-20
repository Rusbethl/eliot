from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar
from wave_alpha.pivots.atr import compute_atr


def _bar(i: int, h: str, low: str, c: str, prev_c: str | None = None) -> Bar:
    return Bar(
        timestamp=date(2024, 1, 1) + timedelta(days=i),
        open=Decimal(prev_c or c),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=1,
    )


def test_atr_constant_one_dollar_range_after_warmup() -> None:
    # 30 bars, each with high-low=1.0, close==prev close → ATR converges to 1.0
    bars: list[Bar] = []
    for i in range(30):
        bars.append(_bar(i, "101", "100", "100", prev_c="100"))
    atr = compute_atr(bars, period=14)
    assert atr[-1] == Decimal("1.0").quantize(atr[-1])


def test_atr_returns_none_for_warmup_bars() -> None:
    bars = [_bar(i, "101", "100", "100", "100") for i in range(5)]
    atr = compute_atr(bars, period=14)
    assert all(v is None for v in atr)


def test_atr_handles_gap_via_true_range() -> None:
    # Day 1 close=100; day 2 gaps up to high=110, low=108, close=109
    # True range = max(110-108, |110-100|, |108-100|) = 10
    bars = [
        _bar(0, "101", "99", "100"),
    ]
    bars.append(_bar(1, "110", "108", "109", prev_c="100"))
    bars.extend(_bar(i, "110", "108", "109", prev_c="109") for i in range(2, 30))
    atr = compute_atr(bars, period=14)
    assert atr[-1] is not None
    assert atr[-1] > Decimal("1.5")

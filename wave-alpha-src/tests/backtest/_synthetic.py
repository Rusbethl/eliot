from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.backtest.trade_layer import TradeRecord
from wave_alpha.domain import Bar, Interval


@dataclass
class FixedBarsProvider:
    """OHLCVProvider stub — returns the same bars for every (ticker, interval)."""

    bars_by_interval: dict[Interval, list[Bar]] = field(default_factory=dict)

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        bars = self.bars_by_interval.get(interval, [])
        if start is not None:
            bars = [b for b in bars if b.timestamp >= start]
        if end is not None:
            bars = [b for b in bars if b.timestamp <= end]
        return bars


def daily_bars(
    ohlc: list[tuple[float, float, float, float]],
    start: date = date(2024, 1, 1),
) -> list[Bar]:
    """Build a daily-bar series from (open, high, low, close) tuples.
    Bars are placed on consecutive business days."""
    out: list[Bar] = []
    cur = start
    for o, h, lo, c in ohlc:
        while cur.weekday() >= 5:  # skip weekends
            cur = cur + timedelta(days=1)
        out.append(
            Bar(
                timestamp=cur,
                open=Decimal(str(o)),
                high=Decimal(str(h)),
                low=Decimal(str(lo)),
                close=Decimal(str(c)),
                volume=1000,
            )
        )
        cur = cur + timedelta(days=1)
    return out


def _record(**overrides) -> TradeRecord:
    defaults = dict(
        ticker="AAPL",
        signal_as_of=date(2024, 1, 1),
        fill_date=date(2024, 1, 2),
        fill_price=Decimal("100"),
        exit_date=date(2024, 1, 10),
        exit_price=Decimal("110"),
        exit_reason="target",
        direction="long",
        pattern="impulse",
        wave_label="5",
        coherence_score=0.8,
        coherence_bucket="full",
        bars_held=6,
        risk_per_share=Decimal("4"),
        realized_pnl_per_share=Decimal("10"),
        realized_R=2.5,
        confidence=0.6,
        tf_count=3,
        degree=2,
        expected_bars_to_target=10,
    )
    defaults.update(overrides)
    return TradeRecord(**defaults)

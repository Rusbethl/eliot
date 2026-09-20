from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _StaticProvider(OHLCVProvider):
    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        bars = [
            Bar(
                timestamp=date(2024, 1, d),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=1,
            )
            for d in (2, 3, 4)
        ]
        if start:
            bars = [b for b in bars if b.timestamp >= start]
        if end:
            bars = [b for b in bars if b.timestamp <= end]
        return bars


def test_provider_returns_filtered_bars() -> None:
    p = _StaticProvider()
    bars = p.fetch("ANY", Interval.DAILY, start=date(2024, 1, 3))
    assert len(bars) == 2
    assert bars[0].timestamp == date(2024, 1, 3)


def test_provider_abc_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        OHLCVProvider()  # type: ignore[abstract]

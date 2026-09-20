from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from wave_alpha.domain import Bar, Interval


def test_bar_basic_roundtrip() -> None:
    bar = Bar(
        timestamp=date(2024, 1, 2),
        open=Decimal("100.10"),
        high=Decimal("101.50"),
        low=Decimal("99.80"),
        close=Decimal("101.00"),
        volume=1_000_000,
    )
    assert bar.timestamp == date(2024, 1, 2)
    assert bar.high >= bar.low


def test_bar_rejects_high_below_low() -> None:
    with pytest.raises(ValidationError):
        Bar(
            timestamp=date(2024, 1, 2),
            open=Decimal("100"),
            high=Decimal("99"),
            low=Decimal("100"),
            close=Decimal("99.50"),
            volume=1,
        )


def test_interval_values() -> None:
    assert Interval.WEEKLY.value == "1wk"
    assert Interval.DAILY.value == "1d"
    assert Interval.HOURLY_4.value == "4h"

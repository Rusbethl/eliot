# tests/backtest/test_walk_forward.py
from datetime import date

import pytest

from wave_alpha.backtest.walk_forward import iter_business_days


def test_iter_business_days_skips_weekends() -> None:
    # 2024-01-05 = Friday, 2024-01-06 = Saturday, 2024-01-08 = Monday
    days = list(iter_business_days(date(2024, 1, 5), date(2024, 1, 9)))
    assert days == [
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 9),
    ]


def test_iter_business_days_inclusive_endpoints() -> None:
    # Single-day range yields just that day if business day
    assert list(iter_business_days(date(2024, 1, 8), date(2024, 1, 8))) == [date(2024, 1, 8)]
    # Saturday → empty
    assert list(iter_business_days(date(2024, 1, 6), date(2024, 1, 6))) == []


def test_iter_business_days_step_subsamples() -> None:
    # Step=2 emits every other business day
    days = list(iter_business_days(date(2024, 1, 1), date(2024, 1, 12), step=2))
    # Mon Jan 1, Wed Jan 3, Fri Jan 5, Tue Jan 9, Thu Jan 11
    # (Jan 1 is Monday in 2024)
    assert days == [
        date(2024, 1, 1),
        date(2024, 1, 3),
        date(2024, 1, 5),
        date(2024, 1, 9),
        date(2024, 1, 11),
    ]


def test_iter_business_days_step_must_be_positive() -> None:
    with pytest.raises(ValueError, match="step must be"):
        list(iter_business_days(date(2024, 1, 1), date(2024, 1, 5), step=0))


def test_iter_business_days_end_before_start_yields_empty() -> None:
    assert list(iter_business_days(date(2024, 1, 10), date(2024, 1, 5))) == []

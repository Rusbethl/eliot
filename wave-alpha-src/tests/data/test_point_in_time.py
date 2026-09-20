from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.data.point_in_time import LookaheadError, PointInTimeView, UnsortedBarsError
from wave_alpha.domain import Bar


def _bar(d: date, price: str = "100") -> Bar:
    p = Decimal(price)
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1)


def test_view_blocks_future_bars() -> None:
    bars = [
        _bar(date(2024, 1, 1)),
        _bar(date(2024, 1, 2)),
        _bar(date(2024, 1, 3)),
    ]
    view = PointInTimeView(bars=bars, as_of=date(2024, 1, 2))
    visible = view.visible()
    assert len(visible) == 2
    assert visible[-1].timestamp == date(2024, 1, 2)


def test_view_latest_returns_most_recent_visible() -> None:
    bars = [
        _bar(date(2024, 1, 1)),
        _bar(date(2024, 1, 2)),
        _bar(date(2024, 1, 5)),
    ]
    view = PointInTimeView(bars=bars, as_of=date(2024, 1, 3))
    assert view.latest().timestamp == date(2024, 1, 2)


def test_view_raises_on_indexed_future_bar() -> None:
    bars = [
        _bar(date(2024, 1, 1)),
        _bar(date(2024, 1, 5)),
    ]
    view = PointInTimeView(bars=bars, as_of=date(2024, 1, 3))
    with pytest.raises(LookaheadError):
        _ = view[1]


def test_view_empty_visible_raises_on_latest() -> None:
    bars = [_bar(date(2024, 1, 5))]
    view = PointInTimeView(bars=bars, as_of=date(2024, 1, 1))
    with pytest.raises(LookaheadError):
        view.latest()


def test_view_rejects_unsorted_bars() -> None:
    bars = [
        _bar(date(2024, 1, 5)),
        _bar(date(2024, 1, 2)),  # out of order
        _bar(date(2024, 1, 10)),
    ]
    with pytest.raises(UnsortedBarsError):
        PointInTimeView(bars=bars, as_of=date(2024, 1, 6))

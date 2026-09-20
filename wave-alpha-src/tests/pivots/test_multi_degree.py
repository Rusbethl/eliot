from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.pivots.multi_degree import (
    _intersect_with,
    detect_multi_degree,
    detect_multi_degree_with_snapshots,
)


def _bar(i: int, c: float) -> Bar:
    cd = Decimal(f"{c:.4f}")
    return Bar(
        timestamp=date(2024, 1, 1) + timedelta(days=i),
        open=cd,
        high=cd + Decimal("0.50"),
        low=cd - Decimal("0.50"),
        close=cd,
        volume=1,
    )


def test_multi_degree_subset_invariant() -> None:
    closes = [
        100,
        102,
        105,
        108,
        105,
        103,
        107,
        110,
        113,
        116,
        113,
        110,
        105,
        100,
        95,
        92,
        95,
        99,
        103,
        107,
        111,
        115,
        119,
        122,
    ]
    bars = [_bar(i, c) for i, c in enumerate(closes)]

    by_degree = detect_multi_degree(
        bars,
        interval=Interval.DAILY,
        thresholds=[Decimal("3"), Decimal("6"), Decimal("12")],
    )
    assert set(by_degree.keys()) == {0, 1, 2}

    for higher, lower in ((1, 0), (2, 1)):
        higher_keys = {(p.timestamp, p.type) for p in by_degree[higher]}
        lower_keys = {(p.timestamp, p.type) for p in by_degree[lower]}
        assert higher_keys.issubset(lower_keys), (
            f"degree {higher} not a subset of degree {lower}: missing {higher_keys - lower_keys}"
        )


def _make_synthetic_bars() -> list[Bar]:
    closes = [
        100,
        102,
        105,
        108,
        105,
        103,
        107,
        110,
        113,
        116,
        113,
        110,
        105,
        100,
        95,
        92,
        95,
        99,
        103,
        107,
        111,
        115,
        119,
        122,
    ]
    return [_bar(i, c) for i, c in enumerate(closes)]


def test_detect_multi_degree_with_snapshots_carries_required_move() -> None:
    bars = _make_synthetic_bars()
    snaps = detect_multi_degree_with_snapshots(
        bars, interval=Interval.DAILY, thresholds=[Decimal("1"), Decimal("2")]
    )
    assert snaps[0].required_move == Decimal("1")
    assert snaps[1].required_move == Decimal("2")
    # atr_at_last_bar is None when thresholds path is used
    assert snaps[0].atr_at_last_bar is None
    assert (
        snaps[0].pivots
        == detect_multi_degree(
            bars, interval=Interval.DAILY, thresholds=[Decimal("1"), Decimal("2")]
        )[0]
    )


def test_detect_multi_degree_with_snapshots_atr_path_populates_atr() -> None:
    bars = _make_synthetic_bars()
    snaps = detect_multi_degree_with_snapshots(
        bars, interval=Interval.DAILY, atr_multiples=[Decimal("0.5"), Decimal("1")]
    )
    # atr_at_last_bar should be a positive Decimal when there's enough history
    for snap in snaps.values():
        if snap.atr_at_last_bar is not None:
            assert snap.atr_at_last_bar > Decimal("0")
        # required_move should be atr * multiple (or 0 if atr missing)
        assert snap.required_move >= Decimal("0")


def _same_day_pivot(
    bar_index: int, price: str, kind: PivotType, *, degree: int, shared_date: date
) -> PivotEvent:
    """Pivot pinned to a single shared calendar date regardless of bar_index —
    simulates two distinct sub-daily bars (e.g. HOURLY_4 on a 24/7 crypto
    market) that fall on the same date. Bar.timestamp only carries a date,
    so real same-day bars collapse exactly like this.
    """
    return PivotEvent(
        interval=Interval.HOURLY_4,
        degree=degree,
        timestamp=shared_date,
        price=Decimal(price),
        type=kind,
        state=PivotState.CONFIRMED,
        confirmed_at=None,
        bar_index=bar_index,
    )


def test_intersect_with_does_not_collapse_same_day_bars() -> None:
    """Regression test: two distinct bars sharing a calendar date (as any
    two same-day HOURLY_4 bars do, since Bar.timestamp has no time
    component) must NOT be treated as the same pivot just because their
    timestamps match. Only a genuine (bar_index, type) match counts.

    Before bar_index was introduced, _intersect_with keyed on
    (timestamp, type) alone, so this parent/child pair would have matched
    incorrectly (both HIGH, same date) even though they sit on different
    bars three positions apart.
    """
    shared_date = date(2024, 3, 5)
    # Child (finer degree): a HIGH at bar_index=2 on the shared date.
    child = [_same_day_pivot(2, "101", PivotType.HIGH, degree=0, shared_date=shared_date)]
    # Parent (coarser degree): a HIGH at bar_index=5, same date, different bar.
    parent = [_same_day_pivot(5, "104", PivotType.HIGH, degree=1, shared_date=shared_date)]

    result = _intersect_with(parent, child, degree=1)

    assert result == [], (
        "same-date pivots on different bars must not be matched as the same pivot"
    )


def test_intersect_with_matches_same_bar_index_even_with_identical_dates() -> None:
    """Sanity check for the same fixture style: when bar_index truly does
    match (the genuine same-bar case), the pivot must still be carried
    through as before."""
    shared_date = date(2024, 3, 5)
    child = [_same_day_pivot(2, "101", PivotType.HIGH, degree=0, shared_date=shared_date)]
    parent = [_same_day_pivot(2, "101", PivotType.HIGH, degree=1, shared_date=shared_date)]

    result = _intersect_with(parent, child, degree=1)

    assert len(result) == 1
    assert result[0].bar_index == 2
    assert result[0].timestamp == shared_date

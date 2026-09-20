from __future__ import annotations

from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.atr import compute_atr
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def zigzag_atr(
    bars: list[Bar],
    interval: Interval,
    atr_multiple: Decimal,
    degree: int = 0,
    fixed_threshold: Decimal | None = None,
    atr_period: int = 14,
) -> list[PivotEvent]:
    """Pivot detection via ATR-scaled ZigZag.

    Threshold at bar i is `atr_multiple * ATR(i)` unless `fixed_threshold` is supplied
    (used by tests for deterministic synthetic series).

    Returns pivots in ascending timestamp order. The final pivot is marked TENTATIVE
    until price moves at least `threshold` against it.
    """
    if not bars:
        return []

    atr = compute_atr(bars, period=atr_period)

    def threshold_at(i: int) -> Decimal:
        if fixed_threshold is not None:
            return fixed_threshold
        a = atr[i]
        return Decimal("0") if a is None else (a * atr_multiple)

    pivots: list[PivotEvent] = []

    direction: PivotType | None = None
    # Post-bootstrap state — single extreme tracking the current swing.
    extreme_idx = 0
    extreme_price = bars[0].close
    # Bootstrap state — track running max AND min separately so the FIRST
    # swing peak/trough is locked in before threshold is exceeded.
    max_idx, max_price = 0, bars[0].close
    min_idx, min_price = 0, bars[0].close

    for i in range(1, len(bars)):
        price = bars[i].close
        thr = threshold_at(i)

        if direction is None:
            if price > max_price:
                max_idx, max_price = i, price
            if price < min_price:
                min_idx, min_price = i, price
            if thr <= 0:
                continue
            # First pivot: whichever extreme price has already retraced past by `thr`.
            if max_price - price >= thr:
                pivots.append(
                    _confirmed(
                        bars[max_idx], max_idx, PivotType.HIGH, interval, degree, bars[i].timestamp
                    )
                )
                direction = PivotType.LOW
                extreme_idx, extreme_price = i, price
            elif price - min_price >= thr:
                pivots.append(
                    _confirmed(
                        bars[min_idx], min_idx, PivotType.LOW, interval, degree, bars[i].timestamp
                    )
                )
                direction = PivotType.HIGH
                extreme_idx, extreme_price = i, price
            continue

        if direction is PivotType.HIGH:
            if price > extreme_price:
                extreme_idx, extreme_price = i, price
            elif extreme_price - price >= thr and thr > 0:
                pivots.append(
                    _confirmed(
                        bars[extreme_idx],
                        extreme_idx,
                        PivotType.HIGH,
                        interval,
                        degree,
                        bars[i].timestamp,
                    )
                )
                direction = PivotType.LOW
                extreme_idx, extreme_price = i, price
        else:
            if price < extreme_price:
                extreme_idx, extreme_price = i, price
            elif price - extreme_price >= thr and thr > 0:
                pivots.append(
                    _confirmed(
                        bars[extreme_idx],
                        extreme_idx,
                        PivotType.LOW,
                        interval,
                        degree,
                        bars[i].timestamp,
                    )
                )
                direction = PivotType.HIGH
                extreme_idx, extreme_price = i, price

    if direction is not None:
        pivots.append(
            PivotEvent(
                interval=interval,
                degree=degree,
                timestamp=bars[extreme_idx].timestamp,
                price=extreme_price,
                type=direction,
                state=PivotState.TENTATIVE,
                confirmed_at=None,
                bar_index=extreme_idx,
            )
        )

    return pivots


def _confirmed(
    bar: Bar, bar_index: int, ptype: PivotType, interval: Interval, degree: int, confirmed_at
) -> PivotEvent:
    return PivotEvent(
        interval=interval,
        degree=degree,
        timestamp=bar.timestamp,
        price=bar.high if ptype is PivotType.HIGH else bar.low,
        type=ptype,
        state=PivotState.CONFIRMED,
        confirmed_at=confirmed_at,
        bar_index=bar_index,
    )

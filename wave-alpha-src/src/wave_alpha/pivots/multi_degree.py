from __future__ import annotations

import itertools
from dataclasses import dataclass
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.atr import compute_atr
from wave_alpha.pivots.models import PivotEvent
from wave_alpha.pivots.zigzag import zigzag_atr


@dataclass(frozen=True)
class DegreeSnapshot:
    pivots: list[PivotEvent]
    atr_at_last_bar: Decimal | None
    required_move: Decimal


def detect_multi_degree(
    bars: list[Bar],
    interval: Interval,
    *,
    atr_multiples: list[Decimal] | None = None,
    thresholds: list[Decimal] | None = None,
) -> dict[int, list[PivotEvent]]:
    """Run ZigZag at multiple sensitivities and enforce the subset invariant.

    Provide exactly one of atr_multiples (for live use) or thresholds (deterministic
    fixed numbers, useful for tests). Lists must be ascending — degree N+1 is the
    coarser threshold and a strict subset of degree N.
    """
    if (atr_multiples is None) == (thresholds is None):
        raise ValueError("provide exactly one of atr_multiples or thresholds")

    levels = atr_multiples or thresholds
    assert levels is not None
    assert all(a <= b for a, b in itertools.pairwise(levels)), "levels must be ascending"

    by_degree: dict[int, list[PivotEvent]] = {}
    for degree, level in enumerate(levels):
        if atr_multiples is not None:
            pivots = zigzag_atr(bars, interval=interval, atr_multiple=level, degree=degree)
        else:
            pivots = zigzag_atr(
                bars,
                interval=interval,
                atr_multiple=Decimal("0"),
                degree=degree,
                fixed_threshold=level,
            )
        if degree > 0:
            pivots = _intersect_with(by_degree[degree - 1], pivots, degree=degree)
        by_degree[degree] = pivots
    return by_degree


def detect_multi_degree_with_snapshots(
    bars: list[Bar],
    interval: Interval,
    *,
    atr_multiples: list[Decimal] | None = None,
    thresholds: list[Decimal] | None = None,
) -> dict[int, DegreeSnapshot]:
    """Same as detect_multi_degree, but each degree carries the ATR + required-move
    used for ZigZag detection. Used by the right-edge feature extractor."""
    by_degree = detect_multi_degree(
        bars, interval=interval, atr_multiples=atr_multiples, thresholds=thresholds
    )
    levels = atr_multiples or thresholds
    assert levels is not None
    atr: list[Decimal | None] | None = compute_atr(bars) if atr_multiples is not None else None
    last_atr: Decimal | None = None
    if atr is not None:
        for val in reversed(atr):
            if val is not None:
                last_atr = val
                break

    out: dict[int, DegreeSnapshot] = {}
    for degree, level in enumerate(levels):
        if atr_multiples is not None:
            required = (last_atr * level) if last_atr is not None else Decimal("0")
        else:
            required = level
        out[degree] = DegreeSnapshot(
            pivots=by_degree.get(degree, []),
            atr_at_last_bar=last_atr,
            required_move=required,
        )
    return out


def _intersect_with(
    parent: list[PivotEvent], child: list[PivotEvent], *, degree: int
) -> list[PivotEvent]:
    # Keyed on (bar_index, type), not (timestamp, type): both degrees run
    # zigzag_atr over the identical `bars` list, so bar_index is a true,
    # collision-free bar identity. timestamp alone collides for sub-daily
    # intervals (e.g. HOURLY_4 on a 24/7 market has several bars per
    # calendar date, since Bar.timestamp only carries a date), which would
    # silently merge two distinct same-day bars here.
    parent_keys = {(p.bar_index, p.type): p for p in parent}
    out: list[PivotEvent] = []
    for c in child:
        if (c.bar_index, c.type) in parent_keys:
            out.append(
                PivotEvent(
                    interval=c.interval,
                    degree=degree,
                    timestamp=c.timestamp,
                    price=c.price,
                    type=c.type,
                    state=c.state,
                    confirmed_at=c.confirmed_at,
                    bar_index=c.bar_index,
                )
            )
    return out

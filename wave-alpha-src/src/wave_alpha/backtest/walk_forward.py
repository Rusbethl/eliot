from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta


def iter_business_days(start: date, end: date, *, step: int = 1) -> Iterator[date]:
    """Yield business days (Mon-Fri) from start to end inclusive.

    `step` selects every Nth emitted business day (1 = every day, 5 = weekly).
    Holidays are NOT skipped — provider lookups for non-trading days return no
    bars and the analyze pass produces no result for that as_of, which the
    evaluators handle by skipping. Centralizing a US-trading-calendar would
    add a dependency for marginal benefit.
    """
    if step < 1:
        raise ValueError(f"step must be >= 1, got {step}")
    cur = start
    emitted = 0
    while cur <= end:
        if cur.weekday() < 5:
            if emitted % step == 0:
                yield cur
            emitted += 1
        cur = cur + timedelta(days=1)

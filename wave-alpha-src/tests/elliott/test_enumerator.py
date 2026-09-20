from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.enumerator import enumerate_counts
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d_off: int, price: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 1) + timedelta(days=d_off),
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=date(2024, 1, 1) + timedelta(days=d_off + 1),
    )


def test_enumerate_finds_clean_impulse() -> None:
    pivots = [
        _piv(0, "100", PivotType.LOW),
        _piv(4, "110", PivotType.HIGH),
        _piv(7, "104", PivotType.LOW),
        _piv(14, "130", PivotType.HIGH),
        _piv(17, "118", PivotType.LOW),
        _piv(24, "140", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, top_k=5)
    patterns = [c.pattern for c in counts]
    assert "impulse" in patterns
    impulse = next(c for c in counts if c.pattern == "impulse")
    assert impulse.hard_violations == []
    assert impulse.score > 0


def test_enumerate_drops_hard_violations() -> None:
    pivots = [
        _piv(0, "100", PivotType.LOW),
        _piv(4, "110", PivotType.HIGH),
        _piv(7, "104", PivotType.LOW),
        _piv(14, "130", PivotType.HIGH),
        _piv(17, "108", PivotType.LOW),  # overlaps W1's territory
        _piv(24, "140", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, top_k=5)
    impulse = [c for c in counts if c.pattern == "impulse"]
    assert all(c.hard_violations for c in impulse) or impulse == []


def test_enumerate_returns_top_k() -> None:
    pivots = [
        _piv(i * 3, f"{100 + i % 3 * 5}", PivotType.HIGH if i % 2 else PivotType.LOW)
        for i in range(8)
    ]
    counts = enumerate_counts(pivots, degree=2, top_k=3)
    assert len(counts) <= 3


def test_enumerate_dedupes_to_one_count_per_template() -> None:
    """When multiple windows of the same template fit, only the highest-scoring
    one survives. Without dedup, one template can crowd out alternates."""
    # Two consecutive impulses share a pivot in the middle — both windows are
    # valid impulse candidates against `impulse` template.
    pivots = [
        _piv(0, "100", PivotType.LOW),
        _piv(4, "110", PivotType.HIGH),
        _piv(7, "104", PivotType.LOW),
        _piv(14, "130", PivotType.HIGH),
        _piv(17, "118", PivotType.LOW),
        _piv(24, "140", PivotType.HIGH),
        _piv(28, "128", PivotType.LOW),
        _piv(35, "165", PivotType.HIGH),
        _piv(40, "150", PivotType.LOW),
        _piv(48, "180", PivotType.HIGH),
        _piv(53, "166", PivotType.LOW),
        _piv(60, "200", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, top_k=10)
    pattern_counts: dict[str, int] = {}
    for c in counts:
        pattern_counts[c.pattern] = pattern_counts.get(c.pattern, 0) + 1
    for pattern, n in pattern_counts.items():
        assert n == 1, f"{pattern} appeared {n} times — dedup failed"


def test_textbook_impulse_outscores_off_textbook_impulse() -> None:
    """Two impulse candidates from the same window: the one with textbook
    ratios should rank above the one with off-textbook ratios."""
    textbook_pivots = [
        _piv(0, "100.00", PivotType.LOW),
        _piv(4, "110.00", PivotType.HIGH),
        _piv(7, "105.00", PivotType.LOW),  # W2 = 0.5
        _piv(14, "121.18", PivotType.HIGH),  # W3 = 1.618
        _piv(17, "115.00", PivotType.LOW),  # W4 = 0.382
        _piv(24, "125.00", PivotType.HIGH),  # W5 = 1.0
    ]
    off_pivots = [
        _piv(0, "100.00", PivotType.LOW),
        _piv(4, "110.00", PivotType.HIGH),
        _piv(7, "100.50", PivotType.LOW),  # W2 = 0.95 (off-textbook)
        _piv(14, "112.00", PivotType.HIGH),  # W3 = 1.15 (weak)
        _piv(17, "111.00", PivotType.LOW),
        _piv(24, "115.00", PivotType.HIGH),
    ]
    textbook = enumerate_counts(textbook_pivots, degree=2, top_k=5)
    off = enumerate_counts(off_pivots, degree=2, top_k=5)

    textbook_imp = next(c for c in textbook if c.pattern == "impulse")
    off_imp = next(c for c in off if c.pattern == "impulse")
    assert textbook_imp.score > off_imp.score


def test_enumerate_prefers_recent_pattern_over_stale() -> None:
    """Two structurally-identical impulses (same price ratios), one stale and
    one current. The current one should outrank the stale one purely on
    recency weighting."""
    # Both impulses share the same price structure: 100 → 120 → 108 → 154 → 142 → 178.
    # Only their position in the pivot timeline differs.
    pivots = [
        # stale impulse (offsets 0..30)
        _piv(0, "100", PivotType.LOW),
        _piv(6, "120", PivotType.HIGH),
        _piv(12, "108", PivotType.LOW),
        _piv(18, "154", PivotType.HIGH),
        _piv(22, "142", PivotType.LOW),
        _piv(30, "178", PivotType.HIGH),
        # recent impulse (offsets 60..120)
        _piv(60, "100", PivotType.LOW),
        _piv(72, "120", PivotType.HIGH),
        _piv(84, "108", PivotType.LOW),
        _piv(96, "154", PivotType.HIGH),
        _piv(108, "142", PivotType.LOW),
        _piv(120, "178", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, top_k=5)
    impulse = next((c for c in counts if c.pattern == "impulse"), None)
    assert impulse is not None
    last_pivot_offset = (impulse.pivots[-1].timestamp - date(2024, 1, 1)).days
    assert last_pivot_offset >= 60, (
        f"recency weighting failed: chose impulse ending at offset {last_pivot_offset}"
    )

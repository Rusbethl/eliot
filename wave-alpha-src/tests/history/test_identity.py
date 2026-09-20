from datetime import date
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.history.identity import top_count_hash
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _pivot(d: str, p: str, degree: int = 0) -> PivotEvent:
    return PivotEvent(
        degree=degree,
        timestamp=date.fromisoformat(d),
        price=Decimal(p),
        interval=Interval.DAILY,
        type=PivotType.HIGH,
        state=PivotState.CONFIRMED,
    )


def _wc(pattern: str = "impulse", *, pivots: list[PivotEvent], degree: int = 1) -> WaveCount:
    return WaveCount(pattern=pattern, degree=degree, pivots=pivots, score=0.5)


def test_top_count_hash_is_deterministic() -> None:
    pivots = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    h1 = top_count_hash(_wc(pivots=pivots))
    h2 = top_count_hash(_wc(pivots=pivots))
    assert h1 == h2
    assert len(h1) == 16  # 16-char hex prefix


def test_top_count_hash_changes_when_pivot_price_changes() -> None:
    base = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    perturbed = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "111")]
    assert top_count_hash(_wc(pivots=base)) != top_count_hash(_wc(pivots=perturbed))


def test_top_count_hash_changes_when_pattern_changes() -> None:
    pivots = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    assert top_count_hash(_wc("impulse", pivots=pivots)) != top_count_hash(
        _wc("zigzag", pivots=pivots)
    )


def test_top_count_hash_changes_when_pivot_timestamp_changes() -> None:
    base = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    perturbed = [_pivot("2024-01-01", "100"), _pivot("2024-02-02", "110")]
    assert top_count_hash(_wc(pivots=base)) != top_count_hash(_wc(pivots=perturbed))


def test_top_count_hash_changes_when_degree_changes() -> None:
    pivots = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    assert top_count_hash(_wc(pivots=pivots, degree=1)) != top_count_hash(
        _wc(pivots=pivots, degree=2)
    )


def test_top_count_hash_ignores_score_and_violations() -> None:
    """Non-structural fields (score, violations) must not change identity.
    The stability badge depends on this — small score perturbations should
    NOT invalidate a count's identity across snapshots."""
    pivots = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    base = WaveCount(pattern="impulse", degree=1, pivots=pivots, score=0.5)
    perturbed_score = WaveCount(pattern="impulse", degree=1, pivots=pivots, score=0.9)
    with_violations = WaveCount(
        pattern="impulse",
        degree=1,
        pivots=pivots,
        score=0.5,
        hard_violations=["fake_hard"],
        soft_violations=["fake_soft"],
    )
    assert top_count_hash(base) == top_count_hash(perturbed_score)
    assert top_count_hash(base) == top_count_hash(with_violations)

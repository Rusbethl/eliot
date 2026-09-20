from datetime import date, timedelta
from decimal import Decimal

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.trades.derive import derive_signal
from wave_alpha.trades.models import TradeRules


def _piv(d: date, price: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


def _bullish_w5_setup() -> WaveCount:
    return WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
        ],
    )


def test_derive_long_signal_from_w5_setup() -> None:
    count = _bullish_w5_setup()
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    assert sig is not None
    assert sig.direction == "long"
    assert sig.stop_price == Decimal("118")
    assert sig.target_price > sig.entry_price
    assert sig.rr > 1.0


def test_derive_returns_none_below_min_confidence() -> None:
    count = _bullish_w5_setup().model_copy(update={"score": 0.1})
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(min_confidence=0.45),
    )
    assert sig is None


def test_derive_short_signal_from_completed_5_segment_impulse() -> None:
    """A 5-segment impulse ending UP is W5-complete; expect a corrective short."""
    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
            _piv(date(2024, 1, 25), "140", PivotType.HIGH),
        ],
    )
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 26),
        current_price=Decimal("139"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    assert sig is not None
    assert sig.direction == "short"


def test_short_signal_target_below_entry() -> None:
    """For a short, the target must be below entry — invariant: profit
    direction matches signal direction. The fib_extension target_source
    must flip to anchor - delta when direction == 'short'; using W1's raw
    direction puts the target on the wrong side of anchor and produces
    immediate adverse 'target hit' on day 0."""
    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
            _piv(date(2024, 1, 25), "140", PivotType.HIGH),
        ],
    )
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 26),
        current_price=Decimal("139"),
        rules=TradeRules(stop_source="count_invalidation", target_source="fib_extension"),
    )
    assert sig is not None
    assert sig.direction == "short"
    assert sig.target_price < sig.entry_price, (
        f"short target {sig.target_price} must be below entry {sig.entry_price}; "
        "fib_extension is computed off W1 direction and needs to flip for shorts"
    )
    # And the stop must be above entry (adverse side for short).
    assert sig.stop_price > sig.entry_price


def test_derive_signal_carries_degree() -> None:
    """The signal must expose count.degree so downstream sim can group by degree."""
    count = _bullish_w5_setup()  # degree=2 by default
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    assert sig is not None
    assert sig.degree == count.degree == 2

    # cover a different degree to prove it's not hard-coded
    count_d3 = count.model_copy(update={"degree": 3})
    sig_d3 = derive_signal(
        count=count_d3,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    assert sig_d3 is not None
    assert sig_d3.degree == 3


def test_derive_signal_uses_empirical_ebt_for_degree_2() -> None:
    """Degree-2 setups produce signals with empirical EBT (median=17, p75=29)
    from the sanity run, not the prior heuristic (25, 38)."""
    count = _bullish_w5_setup()  # degree=2 by default
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    assert sig is not None
    assert sig.degree == 2
    assert sig.expected_bars_to_target == 17
    assert sig.max_holding_bars == 29


def test_derive_signal_falls_back_to_heuristic_for_unfit_degrees() -> None:
    """Degrees other than 2 retain the pre-empirical heuristic
    (count.degree=3 → expected=60, max_holding=60*1.5=90).  Pinned so
    that adding empirical fits for new degrees later doesn't silently
    flip degree-3 behavior without a deliberate test update."""
    count = _bullish_w5_setup().model_copy(update={"degree": 3})
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    assert sig is not None
    assert sig.degree == 3
    assert sig.expected_bars_to_target == 60
    assert sig.max_holding_bars == 90


def test_derive_returns_none_for_long_only_rules_when_short_setup() -> None:
    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "140", PivotType.HIGH),
            _piv(date(2024, 1, 5), "130", PivotType.LOW),
            _piv(date(2024, 1, 8), "136", PivotType.HIGH),
            _piv(date(2024, 1, 15), "110", PivotType.LOW),
            _piv(date(2024, 1, 18), "122", PivotType.HIGH),
        ],
    )
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(direction_modes=["long"]),
    )
    assert sig is None


def _atr_bars(price: float = 100.0, n: int = 30) -> list[Bar]:
    """Synthetic bar series with high-low=2 → ATR ≈ 2 after the 14-bar warmup.

    Mirrors the construction in tests/trades/test_derive_atr_stop.py:_series.
    Inline rather than imported so the two test files stay decoupled.
    """
    out: list[Bar] = []
    for i in range(n):
        d = date(2024, 1, 1) + timedelta(days=i)
        out.append(
            Bar(
                timestamp=d,
                open=Decimal(str(price)),
                high=Decimal(str(price + 1)),
                low=Decimal(str(price - 1)),
                close=Decimal(str(price)),
                volume=1000,
            )
        )
    return out


def test_atr_multiple_long_stop_below_entry() -> None:
    """For a 4-seg W4-complete (long) impulse, atr_multiple stop must equal
    entry - 2*ATR — i.e. on the loss side, with the ATR magnitude.
    Synthetic bars give ATR=2 → stop = 120 - 2*2 = 116."""
    count = _bullish_w5_setup()  # 4-segment, implied direction = long
    bars = _atr_bars(price=120.0)
    sig = derive_signal(
        count=count,
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="atr_multiple"),
        bars=bars,
    )
    assert sig is not None
    assert sig.direction == "long"
    assert sig.stop_price == Decimal("116")
    assert sig.stop_price < sig.entry_price


def test_atr_multiple_short_stop_above_entry() -> None:
    """For a 5-seg W5-complete (short) impulse, atr_multiple stop must equal
    entry + 2*ATR — i.e. on the adverse side, with the ATR magnitude.
    Synthetic bars give ATR=2 → stop = 139 + 2*2 = 143."""
    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
            _piv(date(2024, 1, 25), "140", PivotType.HIGH),
        ],
    )
    bars = _atr_bars(price=139.0)
    sig = derive_signal(
        count=count,
        ticker="TEST",
        as_of=date(2024, 1, 26),
        current_price=Decimal("139"),
        rules=TradeRules(stop_source="atr_multiple"),
        bars=bars,
    )
    assert sig is not None
    assert sig.direction == "short"
    assert sig.stop_price == Decimal("143")
    assert sig.stop_price > sig.entry_price


def test_atr_multiple_raises_without_bars() -> None:
    """atr_multiple needs bars; absence is a hard error, not a silent fallback."""
    count = _bullish_w5_setup()
    with pytest.raises(ValueError, match="atr_multiple"):
        derive_signal(
            count=count,
            ticker="TEST",
            as_of=date(2024, 1, 19),
            current_price=Decimal("120"),
            rules=TradeRules(stop_source="atr_multiple"),
            # bars omitted — defaults to None
        )


def test_atr_multiple_raises_with_insufficient_bars() -> None:
    """atr_multiple needs >= period bars (default 14); fewer is a hard error.
    The error originates in _stop_price_atr_multiple and propagates."""
    count = _bullish_w5_setup()
    with pytest.raises(ValueError, match="ATR unavailable"):
        derive_signal(
            count=count,
            ticker="TEST",
            as_of=date(2024, 1, 19),
            current_price=Decimal("120"),
            rules=TradeRules(stop_source="atr_multiple"),
            bars=_atr_bars(price=120.0, n=10),  # < 14
        )


def test_count_invalidation_works_without_bars() -> None:
    """count_invalidation stop_source must keep working when bars are not
    provided. Pinned because count_invalidation derives the stop from
    count.pivots only — no bars required — and existing analyze/scan/web
    paths historically called derive_signal without bars.  After the
    default-stop-source flip to atr_multiple (2026-05-08), bars are
    required by default, so callers wanting count_invalidation must pass
    it explicitly; this test pins that the explicit path still works."""
    count = _bullish_w5_setup()
    sig = derive_signal(
        count=count,
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
        # bars omitted — count_invalidation doesn't need them
    )
    assert sig is not None


def _bearish_w5_complete() -> WaveCount:
    """5-segment impulse ending UP — implies a corrective short."""
    return WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
            _piv(date(2024, 1, 25), "140", PivotType.HIGH),
        ],
    )


def test_target_source_fib_extension_explicit_long() -> None:
    """Explicit target_source='fib_extension' produces the same target as
    the default (which has always silently been fib_extension).  Pins that
    wiring in the dispatch does not perturb the existing path."""
    sig_default = derive_signal(
        count=_bullish_w5_setup(),
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    sig_explicit = derive_signal(
        count=_bullish_w5_setup(),
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(
            stop_source="count_invalidation",
            target_source="fib_extension",
        ),
    )
    assert sig_default is not None
    assert sig_explicit is not None
    assert sig_default.target_price == sig_explicit.target_price


def test_target_source_count_target_aliases_fib_extension() -> None:
    """count_target and fib_extension are explicit aliases — both dispatch
    to the existing 1.618 * |W1| projection.  This test is the alias
    contract; deliberate divergence in a future spec must update it."""
    sig_count = derive_signal(
        count=_bullish_w5_setup(),
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(
            stop_source="count_invalidation",
            target_source="count_target",
        ),
    )
    sig_fib = derive_signal(
        count=_bullish_w5_setup(),
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(
            stop_source="count_invalidation",
            target_source="fib_extension",
        ),
    )
    assert sig_count is not None
    assert sig_fib is not None
    assert sig_count.target_price == sig_fib.target_price


def test_target_source_fixed_R_long() -> None:  # noqa: N802 — R is a domain term
    """fixed_R target on a long setup: target = entry + target_R * |entry-stop|,
    on the profit side."""
    sig = derive_signal(
        count=_bullish_w5_setup(),
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(
            stop_source="count_invalidation",
            target_source="fixed_R",
            target_R=2.0,
        ),
    )
    assert sig is not None
    assert sig.direction == "long"
    risk = abs(sig.entry_price - sig.stop_price)
    assert sig.target_price == sig.entry_price + Decimal("2") * risk
    assert sig.target_price > sig.entry_price


def test_target_source_fixed_R_short() -> None:  # noqa: N802 — R is a domain term
    """fixed_R target on a short setup: target = entry - target_R * |entry-stop|,
    on the profit side (below entry for a short)."""
    sig = derive_signal(
        count=_bearish_w5_complete(),
        ticker="AAPL",
        as_of=date(2024, 1, 26),
        current_price=Decimal("139"),
        rules=TradeRules(
            stop_source="count_invalidation",
            target_source="fixed_R",
            target_R=2.0,
        ),
    )
    assert sig is not None
    assert sig.direction == "short"
    risk = abs(sig.entry_price - sig.stop_price)
    assert sig.target_price == sig.entry_price - Decimal("2") * risk
    assert sig.target_price < sig.entry_price


def test_target_source_fixed_R_raises_without_target_R() -> None:  # noqa: N802
    """fixed_R needs target_R; absence is a hard error, not a silent
    fallback to fib_extension.  Mirror of atr_multiple's bars-missing
    contract."""
    with pytest.raises(ValueError, match="fixed_R"):
        derive_signal(
            count=_bullish_w5_setup(),
            ticker="AAPL",
            as_of=date(2024, 1, 19),
            current_price=Decimal("120"),
            rules=TradeRules(
                stop_source="count_invalidation",
                target_source="fixed_R",
                # target_R omitted — defaults to None
            ),
        )


def test_derive_signal_per_degree_ebt() -> None:
    """The C5 invariant from the sanity run: per-degree EBT diversity
    is non-trivial.  Degree-1 signals use empirical 8/11 (2026-05-08
    refit, n=119 winners); degree-2 use empirical 17/29 (2026-05-05
    sanity run, n=148 winners); degree-3 use heuristic 60/90 (held
    in the 2026-05-08 refit, n=31 winners < 100-winner threshold).
    Pins the wiring through derive_signal at the unit level so multi-
    degree enumeration produces signals with degree-appropriate
    time-stops."""
    base = _bullish_w5_setup()  # degree=2 by default

    sig_d1 = derive_signal(
        count=base.model_copy(update={"degree": 1}),
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    sig_d2 = derive_signal(
        count=base,
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    sig_d3 = derive_signal(
        count=base.model_copy(update={"degree": 3}),
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )

    assert sig_d1 is not None
    assert sig_d1.expected_bars_to_target == 8  # empirical refit
    assert sig_d1.max_holding_bars == 11  # empirical refit

    assert sig_d2 is not None
    assert sig_d2.expected_bars_to_target == 17
    assert sig_d2.max_holding_bars == 29

    assert sig_d3 is not None
    assert sig_d3.expected_bars_to_target == 60  # heuristic — held per 2026-05-08 verdict
    assert sig_d3.max_holding_bars == 90  # heuristic — held per 2026-05-08 verdict

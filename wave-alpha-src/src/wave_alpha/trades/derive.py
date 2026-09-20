from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Literal

from wave_alpha.domain import Bar
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.atr import compute_atr
from wave_alpha.trades.models import TradeRules, TradeSignal

# Median bars-to-target, per degree.
#   Degree 1: empirical from 2026-05-08 EBT refit (n=119 winners on the
#     curated 50-ticker universe over 2020-2024, median=8 bars).
#   Degree 2: empirical from 2026-05-05 sanity run (n=148, median=17).
#   Degree 3: heuristic (only n=31 winners in the 2026-05-08 refit, below
#     the 100-winner threshold; held until a future refit reaches n>=100).
#   Degrees 0 and 4: heuristic; not enumerated by the v0.5.10 pipeline.
_EBT_MEDIAN_BY_DEGREE: dict[int, int] = {0: 5, 1: 8, 2: 17, 3: 60, 4: 150}

# p75 bars-to-target, per degree, used as the time-stop upper bound.
#   Degree 1: empirical p75=11 (2026-05-08 refit, n=119 winners).
#   Degree 2: empirical p75=29 (2026-05-05 sanity run, n=148 winners).
# Other degrees fall back to median * 1.5 inside derive_signal (the
# existing heuristic ratio).
_EBT_P75_BY_DEGREE: dict[int, int] = {1: 11, 2: 29}


def derive_signal(
    *,
    count: WaveCount,
    ticker: str,
    as_of: date,
    current_price: Decimal,
    rules: TradeRules,
    bars: list[Bar] | None = None,
) -> TradeSignal | None:
    """Convert a WaveCount into a TradeSignal per the rules. Returns None if
    confidence is below the minimum or the implied direction is excluded.

    `bars` is required only when `rules.stop_source == "atr_multiple"`;
    other stop sources use the count's pivots and ignore `bars`.
    """
    if count.score < rules.min_confidence:
        return None

    direction = _implied_direction(count)
    if direction not in rules.direction_modes:
        return None

    stop = _stop_price(count, direction, rules, bars=bars, anchor=current_price)
    if stop == current_price:
        return None  # zero-risk degenerate

    target = _target_price(count, direction, rules, anchor=current_price, stop=stop)
    expected = _EBT_MEDIAN_BY_DEGREE.get(count.degree, 25)
    max_hold = _EBT_P75_BY_DEGREE.get(count.degree, int(expected * 1.5))

    risk = abs(current_price - stop)
    breakeven = current_price + (risk if direction == "long" else -risk)

    return TradeSignal(
        ticker=ticker,
        as_of=as_of,
        direction=direction,
        entry_price=current_price,
        stop_price=stop,
        target_price=target,
        count_id=_count_id(count),
        count_pattern=count.pattern,
        wave_label=_current_wave_label(count),
        degree=count.degree,
        confidence=count.score,
        expected_bars_to_target=expected,
        max_holding_bars=max_hold,
        breakeven_trigger=breakeven,
    )


def _implied_direction(count: WaveCount) -> str:
    """Direction the next move is expected to take.

    Two cases produce the trader-actionable signal:

    * 4 segments completed (W4 just printed) → expect another impulse leg in
      the prior trend's direction. If the last segment was DOWN (W4 down),
      we expect W5 UP → long.
    * 5 segments completed (W5 just printed) → expect a corrective reversal.
      If the last segment was UP, we expect a pullback → short.

    Other segment counts (1-3, or pattern-A/B/C cases) fall back to "expect
    reversal of the most recent leg" — a defensible default.
    """
    segs = count.segments()
    if not segs:
        return "long"
    last_up = segs[-1].is_up
    if len(segs) == 4:
        return "long" if not last_up else "short"  # continue the prior trend
    return "short" if last_up else "long"  # reverse the most recent leg


def _stop_price(
    count: WaveCount,
    direction: str,
    rules: TradeRules,
    *,
    bars: list[Bar] | None = None,
    anchor: Decimal | None = None,
) -> Decimal:
    """Count-invalidation stop. The invalidation level is the price extremum
    that, if breached, falsifies the count's premise.

    For 5-segment impulses (W5 complete, expecting reversal), the invalidation
    is the W5 extreme — i.e. the last pivot. The earlier implementation
    returned `segs[3].end.price` for any 4-or-more segment impulse, which
    placed a W5-complete short's stop at the W4 end (on the trade-direction
    side, below entry) — the wrong side, leading to immediate stop-outs.

    For 4-segment impulses (W4 complete, expecting W5 continuation), the
    invalidation is the W4 end — already correct for either implied direction.

    For `atr_multiple`, dispatches to `_stop_price_atr_multiple` (multiple=2,
    period=14 hard-coded — see spec 2026-05-06-atr-multiple-wiring-design.md
    §8 W1). Raises ValueError if bars or anchor are missing.
    """
    if rules.stop_source == "atr_multiple":
        if bars is None:
            raise ValueError("atr_multiple stop_source requires bars")
        if anchor is None:
            raise ValueError("atr_multiple stop_source requires anchor")
        return _stop_price_atr_multiple(
            bars=bars,
            anchor=anchor,
            direction=direction,  # type: ignore[arg-type]
            multiple=Decimal("2"),
        )
    segs = count.segments()
    last_pivot = count.pivots[-1].price
    if rules.stop_source == "count_invalidation":
        if count.pattern in {"impulse", "leading_diagonal", "ending_diagonal"}:
            if len(segs) == 4:
                return segs[3].end.price
            if len(segs) >= 5:
                return last_pivot
        return last_pivot
    return last_pivot


def _target_price(
    count: WaveCount,
    direction: str,
    rules: TradeRules,
    *,
    anchor: Decimal,
    stop: Decimal,
) -> Decimal:
    """Dispatch on `rules.target_source`.

    `count_target` and `fib_extension` are explicit aliases — both project a
    1.618 extension of W1 from `anchor` on the trade-direction side. The
    earlier implementation passed W1 directly to `fib_extension`, keying the
    projection off W1's *price* direction; that placed the target above
    `anchor` whenever W1 was up — wrong for a W5-complete short. The fix uses
    `direction` to decide which side of `anchor` the target sits on; magnitude
    is abs(W1) * 1.618.

    `fixed_R` dispatches to `_target_price_fixed_r` with `target_R * planned_risk`
    on the profit side. Requires `rules.target_R` to be set; raises ValueError
    otherwise (mirror of atr_multiple's bars-missing contract — see spec
    2026-05-08-target-source-wiring-design.md §3.1).
    """
    if rules.target_source == "fixed_R":
        if rules.target_R is None:
            raise ValueError("fixed_R target_source requires target_R to be set")
        return _target_price_fixed_r(
            anchor=anchor,
            stop=stop,
            direction=direction,  # type: ignore[arg-type]
            target_r=Decimal(str(rules.target_R)),
        )
    segs = count.segments()
    if not segs:
        return anchor
    w1 = segs[0]
    span = abs(w1.end.price - w1.start.price)
    delta = Decimal("1.618") * span
    return anchor + delta if direction == "long" else anchor - delta


def _stop_price_atr_multiple(
    *,
    bars: list[Bar],
    anchor: Decimal,
    direction: Literal["long", "short"],
    multiple: Decimal,
    period: int = 14,
) -> Decimal:
    """ATR-multiple stop placed on the loss side of `anchor`.

    Long: stop = anchor - multiple * ATR
    Short: stop = anchor + multiple * ATR
    """
    atr_series = compute_atr(bars, period=period)
    last_atr = atr_series[-1] if atr_series else None
    if last_atr is None:
        raise ValueError(f"ATR unavailable: need {period}+ bars, got {len(bars)}")
    delta = multiple * last_atr
    return anchor - delta if direction == "long" else anchor + delta


def _target_price_fixed_r(
    *,
    anchor: Decimal,
    stop: Decimal,
    direction: Literal["long", "short"],
    target_r: Decimal,
) -> Decimal:
    """Target placed at `target_r` x planned risk on the signal-direction side."""
    planned_risk = abs(anchor - stop)
    if planned_risk == 0:
        raise ValueError("zero risk: anchor and stop are equal")
    delta = target_r * planned_risk
    return anchor + delta if direction == "long" else anchor - delta


def _current_wave_label(count: WaveCount) -> str:
    segs_done = len(count.segments())
    if count.pattern in {"impulse", "leading_diagonal", "ending_diagonal"}:
        return ["1", "2", "3", "4", "5"][min(segs_done, 4)]
    if count.pattern in {"zigzag", "flat", "expanded_flat"}:
        return ["A", "B", "C"][min(segs_done, 2)]
    if count.pattern == "contracting_triangle":
        return ["A", "B", "C", "D", "E"][min(segs_done, 4)]
    return "?"


def _count_id(count: WaveCount) -> str:
    """Deterministic short id from pattern + pivot timestamps."""
    h = hashlib.sha256()
    h.update(count.pattern.encode())
    for p in count.pivots:
        h.update(p.timestamp.isoformat().encode())
        h.update(str(p.price).encode())
    return h.hexdigest()[:12]

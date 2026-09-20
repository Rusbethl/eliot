from __future__ import annotations

from decimal import Decimal

from wave_alpha.elliott.models import WaveSegment

RETRACE_LEVELS: tuple[Decimal, ...] = (
    Decimal("0.236"),
    Decimal("0.382"),
    Decimal("0.500"),
    Decimal("0.618"),
    Decimal("0.786"),
)

EXTENSION_LEVELS: tuple[Decimal, ...] = (
    Decimal("1.000"),
    Decimal("1.272"),
    Decimal("1.618"),
    Decimal("2.000"),
    Decimal("2.618"),
)


def fib_retracement(prior: WaveSegment) -> dict[Decimal, Decimal]:
    """Price levels at each retracement ratio of `prior`."""
    span = prior.end.price - prior.start.price  # signed
    return {ratio: prior.end.price - ratio * span for ratio in RETRACE_LEVELS}


def fib_extension(prior: WaveSegment, anchor_price: Decimal) -> dict[Decimal, Decimal]:
    """Price levels at each extension ratio of `prior`'s length, anchored at `anchor_price`.

    For an up-wave anchor (e.g., end of Wave 4), levels project upward.
    """
    span = abs(prior.end.price - prior.start.price)
    direction = Decimal(1) if prior.is_up else Decimal(-1)
    return {ratio: anchor_price + direction * ratio * span for ratio in EXTENSION_LEVELS}


def fib_confluence_score(target_price: Decimal, reference: WaveSegment) -> float:
    """0..1, higher when target_price is close to one of the standard fib levels of reference."""
    levels = list(fib_retracement(reference).values()) + list(
        fib_extension(reference, anchor_price=reference.start.price).values()
    )
    span = abs(reference.end.price - reference.start.price) or Decimal(1)
    distances = [abs(target_price - level) / span for level in levels]
    nearest = min(distances)
    if nearest <= Decimal("0.01"):
        return 1.0
    if nearest >= Decimal("0.20"):
        return 0.0
    return float(1 - (nearest - Decimal("0.01")) / Decimal("0.19"))

from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.domain import Interval
from wave_alpha.elliott.dsl import evaluate, length, overlaps, retrace
from wave_alpha.elliott.models import WaveSegment
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


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


W1 = WaveSegment(
    start=_piv(date(2024, 1, 1), "100", PivotType.LOW),
    end=_piv(date(2024, 1, 5), "110", PivotType.HIGH),
)
W2 = WaveSegment(
    start=_piv(date(2024, 1, 5), "110", PivotType.HIGH),
    end=_piv(date(2024, 1, 8), "104", PivotType.LOW),
)
W3 = WaveSegment(
    start=_piv(date(2024, 1, 8), "104", PivotType.LOW),
    end=_piv(date(2024, 1, 15), "130", PivotType.HIGH),
)
W4 = WaveSegment(
    start=_piv(date(2024, 1, 15), "130", PivotType.HIGH),
    end=_piv(date(2024, 1, 18), "118", PivotType.LOW),
)


def test_length_returns_unsigned_distance() -> None:
    assert length(W1) == Decimal("10")
    assert length(W2) == Decimal("6")


def test_retrace_relative_to_prior_wave() -> None:
    assert retrace(W2, W1) == pytest.approx(Decimal("0.6"))


def test_overlaps_detects_w4_into_w1_territory() -> None:
    assert not overlaps(W4, W1)
    bad_w4 = WaveSegment(
        start=W3.end,
        end=_piv(date(2024, 1, 19), "108", PivotType.LOW),
    )
    assert overlaps(bad_w4, W1)


def test_evaluate_passes_for_simple_constraint() -> None:
    ok, _ = evaluate(
        "length(W3) >= Decimal('1.618') * length(W1)",
        bindings={"W1": W1, "W3": W3},
    )
    assert ok is True


def test_evaluate_returns_false_with_message_on_failure() -> None:
    ok, msg = evaluate(
        "length(W3) > Decimal('100') * length(W1)",
        bindings={"W1": W1, "W3": W3},
    )
    assert ok is False
    assert msg


def test_evaluate_yaml_template_style_expressions() -> None:
    """YAML templates use ``Decimal('x') * length(...)`` patterns directly.

    Regression test for an earlier float-wrapper hack that broke this case.
    """
    ok_a, _ = evaluate(
        "length(W3) >= Decimal('0.618') * length(W1)",
        bindings={"W1": W1, "W3": W3},
    )
    ok_b, _ = evaluate(
        "Decimal('0.5') <= retrace(W2, W1) <= Decimal('0.786')",
        bindings={"W1": W1, "W2": W2},
    )
    assert ok_a is True
    assert ok_b is True


def test_evaluate_blocks_attribute_access() -> None:
    """The DSL must not allow attribute access — that's the classic eval-escape
    via ``length.__globals__['__builtins__']``."""
    ok, msg = evaluate(
        "length.__globals__",
        bindings={"W1": W1},
    )
    assert ok is False
    assert "dsl error" in msg


def test_evaluate_blocks_subscript() -> None:
    ok, msg = evaluate(
        "{}[0]",
        bindings={"W1": W1},
    )
    assert ok is False
    assert "dsl error" in msg or "eval error" in msg


def test_evaluate_blocks_dunder_traversal() -> None:
    """The classic ``().__class__.__bases__[0].__subclasses__()`` escape."""
    ok, msg = evaluate(
        "().__class__",
        bindings={"W1": W1},
    )
    assert ok is False
    assert "dsl error" in msg


def test_evaluate_blocks_unallowed_function_call() -> None:
    """Only allowlisted callables (length, retrace, overlaps, min, max, abs,
    Decimal) may appear at the call position."""
    ok, msg = evaluate(
        "open('/etc/passwd')",
        bindings={"W1": W1},
    )
    assert ok is False
    assert "dsl error" in msg or "unknown identifier" in msg


def test_evaluate_blocks_lambda_and_comprehension() -> None:
    ok1, _ = evaluate("(lambda: 1)()", bindings={})
    ok2, _ = evaluate("[x for x in [1]]", bindings={})
    assert ok1 is False
    assert ok2 is False

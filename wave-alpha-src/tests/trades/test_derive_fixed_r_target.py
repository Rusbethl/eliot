from __future__ import annotations

from decimal import Decimal

import pytest

from wave_alpha.trades.derive import _target_price_fixed_r


def test_fixed_r_long_target_above_anchor():
    target = _target_price_fixed_r(
        anchor=Decimal("100"),
        stop=Decimal("96"),
        direction="long",
        target_r=Decimal("2"),
    )
    # planned risk = 4; 2R target = 100 + 8 = 108
    assert target == Decimal("108")


def test_fixed_r_short_target_below_anchor():
    target = _target_price_fixed_r(
        anchor=Decimal("100"),
        stop=Decimal("104"),
        direction="short",
        target_r=Decimal("2"),
    )
    # planned risk = 4; 2R target = 100 - 8 = 92
    assert target == Decimal("92")


def test_fixed_r_raises_on_zero_risk():
    with pytest.raises(ValueError, match="zero risk"):
        _target_price_fixed_r(
            anchor=Decimal("100"),
            stop=Decimal("100"),
            direction="long",
            target_r=Decimal("2"),
        )

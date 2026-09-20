from decimal import Decimal

import pytest
from pydantic import ValidationError

from wave_alpha.trades.models import TradeRules, TradeSignal


def test_trade_rules_defaults() -> None:
    """The default stop_source is atr_multiple (post-grid Phase 2 verdict
    2026-05-08): the grid established a +0.116R sensitivity gap over
    count_invalidation on the curated universe; shipping atr_multiple as
    default reflects the validated empirical winner.  See
    docs/superpowers/specs/2026-05-06-ablation-grid-design.md §6.4."""
    r = TradeRules()
    assert "long" in r.direction_modes and "short" in r.direction_modes
    assert r.entry_trigger == "immediate"
    assert r.stop_source == "atr_multiple"
    assert r.target_source == "count_target"
    assert r.min_confidence == 0.45


def test_trade_signal_rejects_zero_risk() -> None:
    with pytest.raises(ValidationError):
        TradeSignal(
            ticker="AAPL",
            as_of="2024-04-26",
            direction="long",
            entry_price=Decimal("100"),
            stop_price=Decimal("100"),
            target_price=Decimal("110"),
            count_id="c1",
            count_pattern="impulse",
            wave_label="5",
            degree=2,
            confidence=0.6,
            expected_bars_to_target=20,
            max_holding_bars=30,
        )

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class TradeRules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    direction_modes: list[Literal["long", "short"]] = Field(
        default_factory=lambda: ["long", "short"]
    )
    entry_trigger: Literal["immediate", "confirmation"] = "immediate"
    stop_source: Literal["count_invalidation", "atr_multiple"] = "atr_multiple"
    target_source: Literal["count_target", "fib_extension", "fixed_R"] = "count_target"
    target_R: float | None = None  # noqa: N815
    time_stop_mode: Literal["degree_based", "fixed"] = "degree_based"
    time_stop_bars: int | None = None
    max_concurrent_signals_per_ticker: int = 1
    slippage_bps: int = 10
    commission_per_share: Decimal = Decimal("0")
    short_borrow_annual_pct: float = 0.005
    min_confidence: float = 0.45


class TradeSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: date
    direction: Literal["long", "short"]
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    count_id: str
    count_pattern: str
    wave_label: str
    degree: int
    confidence: float
    expected_bars_to_target: int
    max_holding_bars: int
    breakeven_trigger: Decimal | None = None

    @model_validator(mode="after")
    def _check_risk(self) -> TradeSignal:
        if self.entry_price == self.stop_price:
            raise ValueError("entry_price and stop_price cannot be equal (zero risk)")
        return self

    @computed_field
    @property
    def risk_per_share(self) -> Decimal:
        return abs(self.entry_price - self.stop_price)

    @computed_field
    @property
    def reward_per_share(self) -> Decimal:
        return abs(self.target_price - self.entry_price)

    @computed_field
    @property
    def rr(self) -> float:
        return float(self.reward_per_share / self.risk_per_share) if self.risk_per_share else 0.0

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class Interval(StrEnum):
    WEEKLY = "1wk"
    DAILY = "1d"
    HOURLY_4 = "4h"
    HOURLY_1 = "1h"
    MINUTE_30 = "30m"


class Bar(BaseModel):
    """One OHLCV bar at a fixed interval."""

    model_config = ConfigDict(frozen=True)

    timestamp: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @model_validator(mode="after")
    def _check_high_low(self) -> Bar:
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) must be >= low ({self.low})")
        return self

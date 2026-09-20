from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from wave_alpha.domain import Interval


class PivotType(StrEnum):
    HIGH = "high"
    LOW = "low"


class PivotState(StrEnum):
    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"


class PivotEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    interval: Interval
    degree: int
    timestamp: date
    price: Decimal
    type: PivotType
    state: PivotState
    confirmed_at: date | None = None
    # Position of the source bar within the bars list used for detection.
    # `timestamp` alone is not a unique bar identity for sub-daily intervals
    # (e.g. HOURLY_4 on a 24/7 market has multiple bars per calendar date,
    # since Bar.timestamp only carries a date). Cross-degree / cross-pass
    # pivot matching (multi_degree subset filtering, Elliott sub-count
    # boundary alignment) MUST key on (bar_index, type), never
    # (timestamp, type), to avoid silently merging two distinct same-day
    # bars. Defaults to 0 for any legacy construction site.
    bar_index: int = 0

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class WatchlistEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    added_on: date
    notes: str | None = None

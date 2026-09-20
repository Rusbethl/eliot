from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from wave_alpha.domain import Bar, Interval


class OHLCVProvider(ABC):
    """Pluggable OHLCV bar source.

    Subclasses MAY override `supported_intervals` to declare a subset of
    `Interval`. The default declares full support so existing concrete
    providers and test fakes need no modification.
    """

    supported_intervals: frozenset[Interval] = frozenset(Interval)

    @abstractmethod
    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        """Return bars in ascending timestamp order, filtered to [start, end]."""

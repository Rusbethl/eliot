from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from loguru import logger

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class FallbackProvider(OHLCVProvider):
    """Tries each provider in order. Skips providers whose
    `supported_intervals` does not include the requested interval.
    Returns the first non-empty result; falls through on raise or empty.
    Returns `[]` if every provider skips, fails, or yields empty.
    """

    def __init__(self, providers: Sequence[OHLCVProvider]) -> None:
        if not providers:
            raise ValueError("FallbackProvider requires at least one provider")
        self._providers = list(providers)

    @property
    def supported_intervals(self) -> frozenset[Interval]:
        result: frozenset[Interval] = frozenset()
        for p in self._providers:
            result = result | p.supported_intervals
        return result

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        for provider in self._providers:
            if interval not in provider.supported_intervals:
                logger.debug(
                    "fallback skip provider={} interval={} (unsupported)",
                    type(provider).__name__,
                    interval.value,
                )
                continue
            try:
                bars = provider.fetch(ticker, interval, start=start, end=end)
            except Exception as exc:
                logger.warning(
                    "fallback provider={} raised: {}; trying next",
                    type(provider).__name__,
                    exc,
                )
                continue
            if bars:
                return bars
            logger.debug(
                "fallback provider={} returned empty; trying next",
                type(provider).__name__,
            )
        return []

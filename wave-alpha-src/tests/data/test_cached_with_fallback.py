from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _CountingProvider(OHLCVProvider):
    def __init__(
        self,
        *,
        bars: list[Bar] | None = None,
        raises: Exception | None = None,
        supports: frozenset[Interval] | None = None,
    ) -> None:
        self._bars = bars or []
        self._raises = raises
        if supports is not None:
            self.supported_intervals = supports
        self.calls = 0

    def fetch(self, ticker, interval, start=None, end=None):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return list(self._bars)


def _bar(d: date, price: str = "100") -> Bar:
    p = Decimal(price)
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1)


def test_cached_caches_bars_supplied_by_secondary(tmp_path: Path) -> None:
    """Yahoo fails on first call → Stooq supplies → CachedProvider persists.
    Second call: Yahoo recovers but cache is fresh → no upstream call at all."""
    yahoo = _CountingProvider(raises=RuntimeError("rate-limited"))
    stooq = _CountingProvider(bars=[_bar(date(2024, 1, 2)), _bar(date(2024, 1, 3))])
    chain = FallbackProvider([yahoo, stooq])
    cached = CachedProvider(upstream=chain, db_path=tmp_path / "c.sqlite")

    first = cached.fetch("AAPL", Interval.DAILY)
    assert len(first) == 2
    assert yahoo.calls == 1
    assert stooq.calls == 1

    # Second call: cache fresh enough (end=None → any cache is fresh).
    second = cached.fetch("AAPL", Interval.DAILY)
    assert len(second) == 2
    assert yahoo.calls == 1, "no extra upstream call after cache populated"
    assert stooq.calls == 1


def test_cached_returns_cache_when_chain_empty(tmp_path: Path) -> None:
    """Chain returns [] → CachedProvider falls back to whatever cache holds."""
    yahoo = _CountingProvider(bars=[_bar(date(2024, 1, 2))])
    stooq = _CountingProvider(bars=[])
    chain = FallbackProvider([yahoo, stooq])
    cached = CachedProvider(upstream=chain, db_path=tmp_path / "c.sqlite")

    # Populate cache via Yahoo.
    cached.fetch("AAPL", Interval.DAILY)
    yahoo_baseline = yahoo.calls

    # Now both fail → chain returns [] → cache served.
    yahoo._raises = RuntimeError("died")
    yahoo._bars = []
    yahoo.calls = 0
    stooq.calls = 0

    # Force a stale read: pass an end date far in the future so freshness
    # check forces an upstream call.
    out = cached.fetch("AAPL", Interval.DAILY, end=date(2099, 1, 1))
    # Both providers consulted; both empty/failing → cache returned.
    assert len(out) == 1, "should have served the previously-cached bar"
    assert yahoo_baseline >= 1


def test_cached_skips_4h_for_stooq_only_chain(tmp_path: Path) -> None:
    """When only Stooq supports D/W, a 4h request cleanly produces []
    (Yahoo isn't in the chain in this hypothetical scenario)."""
    stooq_only = _CountingProvider(
        bars=[_bar(date(2024, 1, 2))],
        supports=frozenset({Interval.DAILY, Interval.WEEKLY}),
    )
    chain = FallbackProvider([stooq_only])
    cached = CachedProvider(upstream=chain, db_path=tmp_path / "c.sqlite")

    bars = cached.fetch("AAPL", Interval.HOURLY_4)
    assert bars == []
    assert stooq_only.calls == 0

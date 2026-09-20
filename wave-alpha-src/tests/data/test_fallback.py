from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _FakeProvider(OHLCVProvider):
    """Configurable fake. Records calls and either returns bars or raises."""

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
            # Per-instance override (descriptor-friendly).
            self.supported_intervals = supports
        self.calls = 0

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return list(self._bars)


def _bar(d: date) -> Bar:
    p = Decimal("100")
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1)


def test_uses_first_provider_when_succeeds() -> None:
    primary = _FakeProvider(bars=[_bar(date(2024, 1, 2))])
    secondary = _FakeProvider(bars=[_bar(date(2024, 1, 3))])
    chain = FallbackProvider([primary, secondary])

    bars = chain.fetch("AAPL", Interval.DAILY)

    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 1, 2)
    assert primary.calls == 1
    assert secondary.calls == 0


def test_falls_through_on_empty() -> None:
    primary = _FakeProvider(bars=[])
    secondary = _FakeProvider(bars=[_bar(date(2024, 1, 3))])
    chain = FallbackProvider([primary, secondary])

    bars = chain.fetch("AAPL", Interval.DAILY)

    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 1, 3)
    assert primary.calls == 1
    assert secondary.calls == 1


def test_falls_through_on_raise() -> None:
    primary = _FakeProvider(raises=RuntimeError("yfinance died"))
    secondary = _FakeProvider(bars=[_bar(date(2024, 1, 3))])
    chain = FallbackProvider([primary, secondary])

    bars = chain.fetch("AAPL", Interval.DAILY)

    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 1, 3)
    assert primary.calls == 1
    assert secondary.calls == 1


def test_skips_unsupported_provider() -> None:
    """Provider that doesn't declare support for the requested interval is
    skipped without being called."""
    only_wd = _FakeProvider(
        bars=[_bar(date(2024, 1, 5))],
        supports=frozenset({Interval.DAILY, Interval.WEEKLY}),
    )
    full = _FakeProvider(bars=[_bar(date(2024, 1, 6))])
    chain = FallbackProvider([only_wd, full])

    bars = chain.fetch("AAPL", Interval.HOURLY_4)

    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 1, 6)
    assert only_wd.calls == 0, "unsupported provider must not be called"
    assert full.calls == 1


def test_all_empty_returns_empty() -> None:
    primary = _FakeProvider(bars=[])
    secondary = _FakeProvider(bars=[])
    chain = FallbackProvider([primary, secondary])

    assert chain.fetch("AAPL", Interval.DAILY) == []
    assert primary.calls == 1
    assert secondary.calls == 1


def test_all_fail_returns_empty() -> None:
    primary = _FakeProvider(raises=RuntimeError("a"))
    secondary = _FakeProvider(raises=ConnectionError("b"))
    chain = FallbackProvider([primary, secondary])

    # All providers raised; FallbackProvider must NOT propagate.
    assert chain.fetch("AAPL", Interval.DAILY) == []
    assert primary.calls == 1
    assert secondary.calls == 1


def test_supported_intervals_is_union() -> None:
    only_d = _FakeProvider(supports=frozenset({Interval.DAILY}))
    only_w = _FakeProvider(supports=frozenset({Interval.WEEKLY}))
    chain = FallbackProvider([only_d, only_w])

    assert chain.supported_intervals == frozenset({Interval.DAILY, Interval.WEEKLY})


def test_empty_provider_list_raises() -> None:
    with pytest.raises(ValueError):
        FallbackProvider([])


def test_passes_ticker_interval_start_end_to_providers() -> None:
    """Sanity: arguments forwarded unchanged."""
    captured: dict = {}

    class _Recording(OHLCVProvider):
        def fetch(self, ticker, interval, start=None, end=None):
            captured.update(ticker=ticker, interval=interval, start=start, end=end)
            return [_bar(date(2024, 1, 2))]

    chain = FallbackProvider([_Recording()])
    chain.fetch("MSFT", Interval.WEEKLY, start=date(2020, 1, 1), end=date(2024, 1, 1))

    assert captured == {
        "ticker": "MSFT",
        "interval": Interval.WEEKLY,
        "start": date(2020, 1, 1),
        "end": date(2024, 1, 1),
    }

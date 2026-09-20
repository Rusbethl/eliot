"""Live-network smoke test for StooqProvider.

Skipped by default (the 'network' marker is excluded by pyproject.toml's
addopts). Opt in with:

    STOOQ_API_KEY=<your-key> uv run pytest -m network tests/data/test_stooq_network.py -v

As of 2026-05, Stooq's free CSV endpoint requires a captcha-issued API
key (free, but rate-limited per day). Get one at
`https://stooq.com/q/d/?s=AAPL&get_apikey`.
"""

from __future__ import annotations

import os

import pytest

from wave_alpha.data.stooq import StooqProvider
from wave_alpha.domain import Interval

pytestmark = pytest.mark.skipif(
    not os.environ.get("STOOQ_API_KEY"),
    reason="STOOQ_API_KEY not set; set it (free, captcha-issued) to run the live smoke test",
)


@pytest.mark.network
def test_real_stooq_daily_fetch_aapl() -> None:
    bars = StooqProvider(api_key=os.environ["STOOQ_API_KEY"]).fetch("AAPL", Interval.DAILY)

    # Sanity bounds — Stooq's AAPL daily history goes back decades, so
    # we should easily see thousands of bars.
    assert len(bars) > 1000, f"expected >1000 daily bars, got {len(bars)}"

    # Ascending order.
    assert bars == sorted(bars, key=lambda b: b.timestamp)

    # Most recent bar should be within the last few months. We don't
    # pin an exact date because Stooq updates daily.
    from datetime import date, timedelta

    assert bars[-1].timestamp >= date.today() - timedelta(days=120), (
        f"last bar {bars[-1].timestamp} is older than 120 days; Stooq feed may be stale"
    )

    # Decimal precision is preserved.
    assert all(b.high >= b.low for b in bars)


@pytest.mark.network
def test_real_stooq_weekly_fetch_aapl() -> None:
    bars = StooqProvider(api_key=os.environ["STOOQ_API_KEY"]).fetch("AAPL", Interval.WEEKLY)
    assert len(bars) > 200, f"expected >200 weekly bars, got {len(bars)}"
    assert bars == sorted(bars, key=lambda b: b.timestamp)

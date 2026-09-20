"""Pre-warm ~/.wave_alpha/cache.sqlite for the trade-sim sanity run.

For each ticker in data/universe_curated.txt, fetch weekly + daily +
4h bars with end=2024-12-31. The CachedProvider stores results so the
sim loop in Phase D doesn't pay yfinance latency per call.

Run:
    uv run python scripts/prewarm_4h_cache.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.domain import Interval

UNIVERSE = Path("data/universe_curated.txt")
END = date(2024, 12, 31)
INTERVALS = [Interval.WEEKLY, Interval.DAILY, Interval.HOURLY_4]
SLEEP_BETWEEN = 0.2  # seconds — gentle on yfinance
CACHE_DB = Path(os.path.expanduser("~/.wave_alpha/cache.sqlite"))


def main() -> int:
    tickers = [
        line.strip()
        for line in UNIVERSE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    provider = CachedProvider(upstream=YahooProvider(), db_path=CACHE_DB)
    failures: list[tuple[str, Interval, str]] = []
    total = len(tickers)
    print(f"pre-warming cache for {total} tickers x {len(INTERVALS)} intervals through {END}")

    for i, ticker in enumerate(tickers, 1):
        for interval in INTERVALS:
            try:
                bars = provider.fetch(ticker, interval, end=END)
                print(f"[{i:3d}/{total}] {ticker:8s} {interval.value:4s}: {len(bars):5d} bars")
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                print(
                    f"[{i:3d}/{total}] {ticker:8s} {interval.value:4s}: FAILED {msg}",
                    file=sys.stderr,
                )
                failures.append((ticker, interval, msg))
            time.sleep(SLEEP_BETWEEN)

    if failures:
        print(f"\n{len(failures)} fetch failures:", file=sys.stderr)
        for t, iv, m in failures:
            print(f"  {t} {iv.value}: {m}", file=sys.stderr)
        # 4h failures for some delisted tickers are expected — exit 0 unless
        # weekly or daily failed (those are required).
        hard = [(t, iv) for t, iv, _ in failures if iv != Interval.HOURLY_4]
        if hard:
            print(
                f"{len(hard)} weekly/daily failures — investigate before proceeding",
                file=sys.stderr,
            )
            return 1
    print("pre-warm complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

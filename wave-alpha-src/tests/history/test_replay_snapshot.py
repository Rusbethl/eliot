"""Unit tests for history.service.replay_snapshot."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from wave_alpha.domain import Bar, Interval
from wave_alpha.history import service, store


class _FakeProvider:
    """Returns canned bars; mirrors the OHLCVProvider.fetch interface."""

    def __init__(self, bars_by_key: dict[tuple[str, Interval], list[Bar]]):
        self._bars = bars_by_key

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = self._bars.get((ticker, interval), [])
        return [b for b in bars if b.timestamp <= end]


def _make_bars(start: date, n: int, interval: Interval, base: float = 100.0) -> list[Bar]:
    """Build an Elliott-flavored 5-3 zigzag walk so the multi-degree ZigZag
    detector at degree=2 finds pivots. A monotonic ramp produces zero counts
    and is useless for testing the ranked-output path. Returns ``n`` bars
    spaced one day apart for daily/4h intervals and one week apart for weekly.
    """
    segments = [
        (0.30, base, base * 1.30),  # W1 up
        (0.15, base * 1.30, base * 1.15),  # W2 down
        (0.40, base * 1.15, base * 1.75),  # W3 up
        (0.15, base * 1.75, base * 1.58),  # W4 down
        (0.30, base * 1.58, base * 1.95),  # W5 up
        (0.25, base * 1.95, base * 1.58),  # WA down
        (0.15, base * 1.58, base * 1.75),  # WB up
        (0.30, base * 1.75, base * 1.40),  # WC down
    ]
    closes: list[float] = []
    for frac, a, b in segments:
        seg_n = max(1, round(frac * n))
        for i in range(seg_n):
            t = i / max(1, seg_n - 1) if seg_n > 1 else 1.0
            closes.append(a + t * (b - a))
    closes = closes[:n]
    step = timedelta(days=1) if interval != Interval.WEEKLY else timedelta(days=7)
    out: list[Bar] = []
    for i, c in enumerate(closes):
        cd = Decimal(f"{c:.4f}")
        rng = Decimal(f"{c * 0.003:.4f}")  # tight intra-bar so ATR stays small
        out.append(
            Bar(
                timestamp=start + i * step,
                open=cd,
                high=cd + rng,
                low=cd - rng,
                close=cd,
                volume=1_000_000,
            )
        )
    return out


def test_replay_snapshot_writes_row(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite"
    as_of = date(2024, 6, 30)
    daily = _make_bars(date(2022, 1, 1), 700, Interval.DAILY)
    weekly = _make_bars(date(2022, 1, 1), 100, Interval.WEEKLY)
    h4 = _make_bars(date(2022, 1, 1), 700, Interval.HOURLY_4)
    provider = _FakeProvider(
        {
            ("AAPL", Interval.DAILY): daily,
            ("AAPL", Interval.WEEKLY): weekly,
            ("AAPL", Interval.HOURLY_4): h4,
        }
    )

    written = service.replay_snapshot(
        ticker="AAPL", as_of=as_of, provider=provider, db_path=db_path
    )

    assert written is True
    rows = store.recent("AAPL", n=10, db_path=db_path)
    assert len(rows) == 1
    assert rows[0].as_of == as_of


def test_replay_snapshot_does_not_prune_old_siblings(tmp_path: Path) -> None:
    """auto-prune is bypassed: replaying at a recent as_of must not delete
    older snapshots for the same ticker (backfill safety)."""
    db_path = tmp_path / "history.sqlite"
    # Seed an ancient row directly via the existing resolver test helper pattern.
    from datetime import UTC, datetime

    from wave_alpha.history.models import Snapshot, SnapshotCount, SnapshotPivot

    ancient = Snapshot(
        ticker="AAPL",
        as_of=date(2015, 12, 31),
        engine_version="0.1.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=1,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=date(2015, 12, 31), price=Decimal("50"))],
                engine_score=0.5,
                llm_prob=None,
                final_score=0.5,
            )
        ],
        signal=None,
        coherence=None,
        top_count_hash="h0",
        created_at=datetime(2015, 12, 31, tzinfo=UTC),
    )
    store.upsert(ancient, db_path=db_path)
    assert len(store.recent("AAPL", n=10, db_path=db_path)) == 1

    # Replay at a much-newer as_of. record_snapshot would prune the ancient row
    # at default retention=730d; replay_snapshot must NOT.
    daily = _make_bars(date(2022, 1, 1), 700, Interval.DAILY)
    weekly = _make_bars(date(2022, 1, 1), 100, Interval.WEEKLY)
    h4 = _make_bars(date(2022, 1, 1), 700, Interval.HOURLY_4)
    provider = _FakeProvider(
        {
            ("AAPL", Interval.DAILY): daily,
            ("AAPL", Interval.WEEKLY): weekly,
            ("AAPL", Interval.HOURLY_4): h4,
        }
    )

    service.replay_snapshot(
        ticker="AAPL", as_of=date(2024, 6, 30), provider=provider, db_path=db_path
    )

    rows = store.recent("AAPL", n=10, db_path=db_path)
    as_ofs = {r.as_of for r in rows}
    assert date(2015, 12, 31) in as_ofs, "ancient row was pruned (auto-prune leaked into replay)"


def test_replay_snapshot_returns_false_when_no_ranked_counts(tmp_path: Path) -> None:
    """If run_analysis produces no ranked_daily, no row is written and we
    return False — caller can count skips."""
    db_path = tmp_path / "history.sqlite"
    provider = _FakeProvider({})  # no bars for anything → empty AnalyzeResult
    written = service.replay_snapshot(
        ticker="ZZZZ", as_of=date(2024, 6, 30), provider=provider, db_path=db_path
    )
    assert written is False
    assert store.recent("ZZZZ", n=10, db_path=db_path) == []

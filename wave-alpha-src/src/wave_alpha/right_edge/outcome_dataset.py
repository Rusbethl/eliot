"""Outcome-driven training dataset construction.

Reads resolved snapshots from history.sqlite, replays the pipeline at each
snapshot's as_of against cache.sqlite bars, recomputes right-edge features,
and emits labeled rows for blended training. No features are persisted —
recompute is the only path.

Public surface:
- `WeightSchedule`, `OutcomeRow`, `OutcomeDataset` — data models.
- `label_for_status` — outcome_status → binary label, honors include_time_stops.
- `assign_weights` — per-degree sample-weight ramp.
- `build_outcome_dataset` — orchestrates DB read + pipeline replay + feature
  extraction + weight assignment.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Literal

from wave_alpha.right_edge.features import RightEdgeFeatures


@dataclass(frozen=True)
class WeightSchedule:
    """Per-degree sample-weight schedule for outcome rows."""

    n_target_per_degree: int = 100
    """Full-ramp threshold: outcome influence reaches synthetic parity here."""

    include_time_stops: bool = True
    """When False, snapshots with outcome_status='time_stop' are dropped entirely."""

    time_stop_label: float = 0.0
    """Binary label assigned to time_stop outcomes when included."""

    def __post_init__(self) -> None:
        if self.n_target_per_degree <= 0:
            raise ValueError(f"n_target_per_degree must be > 0 (got {self.n_target_per_degree})")


@dataclass(frozen=True)
class OutcomeRow:
    ticker: str
    as_of: date
    degree: int
    features: RightEdgeFeatures
    label: float  # 0.0 or 1.0
    weight: float  # populated by assign_weights
    exit_date: date  # populated by build_outcome_dataset from snapshot.outcome.exit_date


@dataclass(frozen=True)
class OutcomeDataset:
    rows: list[OutcomeRow]
    skipped_drift: int  # count_id absent and no acceptable fallback; or extraction error
    skipped_no_bars: int  # provider could not return bars at as_of
    skipped_wrong_degree: int  # snapshots whose signal.degree != requested degree
    schedule: WeightSchedule


def label_for_status(
    status: Literal["target", "stop", "time_stop"],
    *,
    schedule: WeightSchedule,
) -> float | None:
    """Map outcome_status → binary label, honoring schedule.include_time_stops.

    Returns None to signal "skip this row entirely" (only for time_stop with
    include_time_stops=False).
    """
    if status == "target":
        return 1.0
    if status == "stop":
        return 0.0
    if status == "time_stop":
        return schedule.time_stop_label if schedule.include_time_stops else None
    raise ValueError(f"unexpected outcome_status {status!r}")


def assign_weights(
    rows: list[OutcomeRow],
    *,
    n_synth_per_degree: dict[int, int],
    schedule: WeightSchedule,
) -> list[OutcomeRow]:
    """Assign per-row weights using the per-degree ramp.

    Properties (locked in by tests):
    - n_outc=0           → returns []
    - n_outc<n_target    → outcome mass < synthetic mass, proportional to ramp
    - n_outc=n_target    → outcome mass == synthetic mass exactly
    - n_outc>>n_target   → outcome mass stays == synthetic mass (ramp caps)
    - n_synth(d)=0       → all outcome rows for degree d carry weight 0
    """
    if not rows:
        return []

    n_outc_per_degree: Counter[int] = Counter(r.degree for r in rows)
    weights_by_degree: dict[int, float] = {}
    for degree, n_outc in n_outc_per_degree.items():
        n_synth = n_synth_per_degree.get(degree, 0)
        if n_outc == 0 or n_synth == 0:
            weights_by_degree[degree] = 0.0
            continue
        ramp = min(1.0, n_outc / schedule.n_target_per_degree)
        weights_by_degree[degree] = (n_synth / n_outc) * ramp

    return [replace(r, weight=weights_by_degree[r.degree]) for r in rows]


def _select_resolved_snapshots(*, db_path: Path):
    """Yield (ticker, as_of, Snapshot) for rows whose outcome_status is resolved."""
    import sqlite3 as _sqlite3

    from wave_alpha.history.models import Snapshot as _Snapshot

    if not db_path.exists():
        return
    conn = _sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT ticker, as_of, payload_json
            FROM snapshots
            WHERE outcome_status IN ('target', 'stop', 'time_stop')
            ORDER BY ticker ASC, as_of ASC
            """
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    for ticker, as_of_str, payload_json in rows:
        snap = _Snapshot.model_validate_json(payload_json)
        yield ticker, date.fromisoformat(as_of_str), snap


def _recompute_features_for_snapshot(
    snap,
    *,
    bar_provider,
    degree: int,
):
    """Return RightEdgeFeatures, the sentinel string "no_bars", or None on drift.

    Returns:
    - RightEdgeFeatures: success.
    - "no_bars": provider returned nothing — bump skipped_no_bars.
    - None: count drift or feature extraction failure — bump skipped_drift.
    """
    from wave_alpha.data.point_in_time import PointInTimeView
    from wave_alpha.domain import Interval
    from wave_alpha.elliott.enumerator import enumerate_counts
    from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES
    from wave_alpha.pivots.multi_degree import detect_multi_degree_with_snapshots
    from wave_alpha.right_edge.extractor import FeatureExtractor
    from wave_alpha.trades.derive import _count_id

    bars = bar_provider.fetch(snap.ticker, Interval.DAILY, end=snap.as_of)
    if not bars:
        return "no_bars"

    pit = PointInTimeView(bars, snap.as_of).visible()
    if not pit:
        return "no_bars"

    snaps_by_degree = detect_multi_degree_with_snapshots(
        pit,
        interval=Interval.DAILY,
        atr_multiples=_DEFAULT_ATR_MULTIPLES,
    )
    pivot_snap = snaps_by_degree.get(degree)
    if pivot_snap is None or pivot_snap.atr_at_last_bar is None or pivot_snap.required_move <= 0:
        return None

    counts = enumerate_counts(pivot_snap.pivots, degree=degree, top_k=5)
    if not counts:
        return None

    # Match the snapshot's signal.count_id by recomputing the hash on each
    # recomputed WaveCount. _count_id is a deterministic SHA256-12 over
    # pattern + pivot (timestamp, price) — pivot drift breaks the match.
    target_count_id = snap.signal.count_id if snap.signal else None
    matched = None
    if target_count_id is not None:
        for c in counts:
            if _count_id(c) == target_count_id:
                matched = c
                break

    if matched is None:
        # Drift fallback: top-1 of recomputed enumeration, but only if it
        # plausibly matches the snapshot's signal at pattern + degree.
        top1 = counts[0]
        if snap.signal is not None and top1.pattern == snap.signal.count_pattern:
            matched = top1
        else:
            return None

    extractor = FeatureExtractor()
    try:
        # Use the latest pivot in the count as the candidate. Mirrors
        # training-row assembly's "latest tentative pivot" semantics.
        candidate = matched.pivots[-1]
        features = extractor.extract(
            candidate=candidate,
            point_in_time_bars=pit,
            atr_at_candidate=pivot_snap.atr_at_last_bar,
            required_move=pivot_snap.required_move,
            higher_degree_zone_match=0.0,
            top_count=matched,
        )
    except Exception:
        return None
    return features


def build_outcome_dataset(
    *,
    db_path: Path,
    bar_provider,
    degree: int,
    schedule: WeightSchedule,
    n_synth_per_degree: dict[int, int],
    limit: int | None = None,
) -> OutcomeDataset:
    """Build an outcome-labeled dataset by replaying the pipeline at each
    resolved snapshot's as_of and recomputing right-edge features.

    Args:
        db_path: history.sqlite location.
        bar_provider: OHLCVProvider — typically ``CachedProvider(YahooProvider())``.
        degree: which pivot degree to recompute at (must match snapshot.signal.degree
                semantics — caller is responsible for routing).
        schedule: weight-schedule (label policy + ramp constants).
        n_synth_per_degree: per-degree counts from the synthetic dataset, used
                            to balance outcome mass against synthetic mass.
        limit: optional cap on rows processed (debug / fast iteration).
    """
    rows: list[OutcomeRow] = []
    skipped_drift = 0
    skipped_no_bars = 0
    skipped_wrong_degree = 0
    processed = 0

    for ticker, as_of, snap in _select_resolved_snapshots(db_path=db_path):
        if snap.outcome is None or snap.outcome.status == "pending":
            continue
        # Only recompute at the requested degree; snapshots at other degrees
        # are counted and skipped (caller would invoke us per-degree if needed).
        if snap.signal is None or snap.signal.degree != degree:
            skipped_wrong_degree += 1
            continue

        label = label_for_status(snap.outcome.status, schedule=schedule)
        if label is None:
            continue  # drop time_stop when include_time_stops=False

        outcome = _recompute_features_for_snapshot(snap, bar_provider=bar_provider, degree=degree)
        if outcome == "no_bars":
            skipped_no_bars += 1
            continue
        if outcome is None:
            skipped_drift += 1
            continue

        # `outcome` is RightEdgeFeatures here; the sentinel string was filtered above.
        assert isinstance(outcome, RightEdgeFeatures)
        if snap.outcome is None or snap.outcome.exit_date is None:
            raise ValueError(
                "resolved snapshots must have outcome.exit_date; "
                f"got ticker={ticker} as_of={as_of} "
                f"status={snap.outcome.status if snap.outcome else None}"
            )
        rows.append(
            OutcomeRow(
                ticker=ticker,
                as_of=as_of,
                degree=degree,
                features=outcome,
                label=label,
                weight=0.0,
                exit_date=snap.outcome.exit_date,
            )
        )
        processed += 1
        if limit is not None and processed >= limit:
            break

    weighted = assign_weights(rows=rows, n_synth_per_degree=n_synth_per_degree, schedule=schedule)
    return OutcomeDataset(
        rows=weighted,
        skipped_drift=skipped_drift,
        skipped_no_bars=skipped_no_bars,
        skipped_wrong_degree=skipped_wrong_degree,
        schedule=schedule,
    )

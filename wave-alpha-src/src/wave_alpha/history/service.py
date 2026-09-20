"""Façade for the history package. Only thing call sites should import."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from wave_alpha import __version__
from wave_alpha.elliott.models import _PATTERN_LABELS
from wave_alpha.history import resolver, store
from wave_alpha.history.identity import top_count_hash
from wave_alpha.history.models import (
    OutcomeBadge,
    RecentHistory,
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
    StabilityBadge,
)
from wave_alpha.history.store import StoredRow
from wave_alpha.llm.modes import LLMMode
from wave_alpha.pipeline import AnalyzeRequest, run_analysis
from wave_alpha.prefs.models import HistoryConfig

if TYPE_CHECKING:
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.trades.models import TradeSignal

logger = logging.getLogger(__name__)

# Module-level alias for monkeypatch in tests (e.g. patch service.store.upsert).
__all__ = ["record_snapshot", "store"]


def _load_history_config() -> HistoryConfig:
    """Load HistoryConfig from disk, falling back to defaults on any failure.

    Wrapped as a separate function so tests can patch it via
    `patch.object(service, "_load_history_config", return_value=...)`.
    """
    from wave_alpha.prefs.config import load_config

    try:
        return load_config().history
    except Exception:
        return HistoryConfig()


def _last_pivot_label(count: WaveCount) -> str:
    labels = _PATTERN_LABELS.get(count.pattern, [])
    if not labels or not count.pivots:
        return ""
    # pivots include the starting pivot before label "1"; segments = pivots-1
    # so the last completed wave label is at index len(pivots)-2 (clamped).
    idx = max(0, min(len(labels) - 1, len(count.pivots) - 2))
    return labels[idx]


def _to_snapshot_count(rc: RankedCount) -> SnapshotCount:
    return SnapshotCount(
        candidate_id=rc.candidate_id,
        pattern=rc.count.pattern,
        degree=rc.count.degree,
        last_pivot_label=_last_pivot_label(rc.count),
        pivots=[SnapshotPivot(timestamp=p.timestamp, price=p.price) for p in rc.count.pivots],
        engine_score=rc.engine_score,
        llm_prob=rc.llm_prob,
        final_score=rc.final_score,
        engine_prob=rc.engine_prob,
    )


def _to_snapshot_signal(sig: TradeSignal | None) -> SnapshotSignal | None:
    if sig is None:
        return None
    return SnapshotSignal(
        direction=sig.direction,
        entry_price=sig.entry_price,
        stop_price=sig.stop_price,
        target_price=sig.target_price,
        rr=sig.rr,
        count_id=sig.count_id,
        count_pattern=sig.count_pattern,
        wave_label=sig.wave_label,
        degree=sig.degree,
        confidence=sig.confidence,
        expected_bars_to_target=sig.expected_bars_to_target,
        max_holding_bars=sig.max_holding_bars,
    )


def _to_snapshot_coherence(coh: CoherenceResult | None) -> SnapshotCoherence | None:
    if coh is None:
        return None
    return SnapshotCoherence(
        score=coh.score,
        multiplier=coh.multiplier,
        pairwise=dict(coh.pairwise),
        fallback_used=coh.fallback_used,
    )


def _build_snapshot(result: AnalyzeResult, ticker: str, as_of: date) -> Snapshot:
    top_3 = [_to_snapshot_count(rc) for rc in result.ranked_daily[:3]]
    signal = _to_snapshot_signal(result.signals[0] if result.signals else None)
    coherence = _to_snapshot_coherence(result.coherence)
    return Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version=__version__,
        top_3=top_3,
        signal=signal,
        coherence=coherence,
        top_count_hash=top_count_hash(result.ranked_daily[0].count),
        created_at=datetime.now(tz=UTC),
    )


def _compute_badge(rows: list[StoredRow]) -> StabilityBadge | None:
    if len(rows) < 2:
        return None
    current = rows[0].top_count_hash
    streak = 1
    last_changed: date | None = None
    for r in rows[1:]:
        if r.top_count_hash == current:
            streak += 1
        else:
            last_changed = r.as_of
            break
    matches = sum(1 for r in rows if r.top_count_hash == current)
    return StabilityBadge(
        streak=streak,
        matches_in_window=matches,
        window=len(rows),
        last_changed_on=last_changed,
    )


def compute_outcome_badge(snapshots: list[Snapshot]) -> OutcomeBadge | None:
    """Aggregate snapshot outcomes for the deepdive header badge.
    Returns None when no snapshot has any outcome (resolved or pending)."""
    resolved = [
        s.outcome for s in snapshots if s.outcome is not None and s.outcome.status != "pending"
    ]
    pending = [
        s.outcome for s in snapshots if s.outcome is not None and s.outcome.status == "pending"
    ]
    if not resolved and not pending:
        return None
    target_count = sum(1 for o in resolved if o.status == "target")
    hit_rate = (target_count / len(resolved)) if resolved else 0.0
    avg_r = (
        sum(o.realized_R for o in resolved if o.realized_R is not None) / len(resolved)
        if resolved
        else 0.0
    )
    return OutcomeBadge(
        resolved_count=len(resolved),
        target_count=target_count,
        hit_rate=hit_rate,
        avg_R=avg_r,
        in_flight_count=len(pending),
    )


def get_recent_with_badge(ticker: str, *, n: int = 14, db_path: Path) -> RecentHistory:
    rows = store.recent(ticker, n, db_path=db_path)
    snapshots = [Snapshot.model_validate_json(r.payload_json) for r in rows]
    badge = _compute_badge(rows)
    return RecentHistory(snapshots=snapshots, badge=badge)


def record_snapshot(
    result: AnalyzeResult,
    ticker: str,
    as_of: date,
    *,
    db_path: Path,
) -> None:
    """Persist `result` as a snapshot and resolve any prior unresolved
    or pending snapshots for this ticker. Side-effect only — never raises.

    Also auto-prunes snapshots older than `history.retention_days` for this
    ticker. Best-effort; failures log at WARNING and never propagate.
    """
    if not result.ranked_daily:
        return
    try:
        snapshot = _build_snapshot(result, ticker, as_of)
        store.upsert(snapshot, db_path=db_path)
        if result.daily_bars:
            resolver.resolve_pending_for(ticker, result.daily_bars, db_path=db_path, today=as_of)
    except Exception as exc:
        logger.warning("history snapshot/outcome failed for %s @ %s: %s", ticker, as_of, exc)
        return

    # Auto-prune is independent and best-effort — never propagates.
    try:
        cfg = _load_history_config()
        if cfg.retention_days > 0:
            cutoff = as_of - timedelta(days=cfg.retention_days)
            store.prune_older_than(db_path=db_path, ticker=ticker, cutoff=cutoff)
    except Exception as exc:
        logger.warning("auto-prune failed for %s @ %s: %s", ticker, as_of, exc)


def replay_snapshot(
    *,
    ticker: str,
    as_of: date,
    provider,
    db_path: Path,
) -> bool:
    """Re-run the analysis pipeline at (ticker, as_of) and upsert the snapshot.

    Used by `wave-alpha history backfill-signals` and `wave-alpha history
    densify` to (re)generate snapshots under the current signal schema —
    notably populating `signal.max_holding_bars` so the resolver can act on
    them. LLM is forced to DISABLED so bootstrap data is deterministic.

    Unlike `record_snapshot`, this DOES NOT run the auto-prune pass — backfill
    of old as_of values must not delete legitimate sibling rows.

    Returns True if a row was upserted, False if the pipeline produced no
    ranked candidates (e.g. cache miss, insufficient bars).
    """
    req = AnalyzeRequest(ticker=ticker, as_of=as_of)
    try:
        result = run_analysis(req, provider=provider, llm_mode=LLMMode.DISABLED)
    except Exception as exc:
        logger.warning("replay_snapshot pipeline failed for %s @ %s: %s", ticker, as_of, exc)
        return False

    if not result.ranked_daily:
        return False

    try:
        snapshot = _build_snapshot(result, ticker, as_of)
        store.upsert(snapshot, db_path=db_path)
        if result.daily_bars:
            resolver.resolve_pending_for(ticker, result.daily_bars, db_path=db_path, today=as_of)
    except Exception as exc:
        logger.warning("replay_snapshot upsert/resolve failed for %s @ %s: %s", ticker, as_of, exc)
        return False

    return True

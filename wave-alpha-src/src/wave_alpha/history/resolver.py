"""History outcome resolver. Walks unresolved/pending snapshots forward
using daily bars produced by analyze; updates each snapshot in place
via the shared trades.outcome.resolve_outcome resolver."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

from wave_alpha.domain import Bar
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotOutcome,
)
from wave_alpha.trades.models import TradeSignal
from wave_alpha.trades.outcome import Outcome, resolve_outcome

logger = logging.getLogger(__name__)


def resolve_pending_for(
    ticker: str,
    daily_bars: list[Bar],
    *,
    db_path: Path,
    today: date,
) -> int:
    """Walk every (ticker, as_of) row with outcome_status IN (NULL, 'pending')
    whose snapshot has a fully-formed signal. Compute outcome, upsert payload.
    Returns count of rows updated. Errors per-row are logged and skipped."""
    rows = store.unresolved_for(ticker, db_path=db_path)
    bars_sorted = sorted(daily_bars, key=lambda b: b.timestamp)
    updated = 0
    for row in rows:
        try:
            snapshot = Snapshot.model_validate_json(row.payload_json)
            signal = _to_trade_signal(snapshot)
            if signal is None:
                continue
            outcome = resolve_outcome(signal, bars_sorted, as_of_close=today)
            if outcome is None:
                continue
            new_snapshot = snapshot.model_copy(update={"outcome": _to_snapshot_outcome(outcome)})
            store.upsert(new_snapshot, db_path=db_path)
            updated += 1
        except Exception as exc:
            logger.warning(
                "outcome resolution failed for %s @ %s: %s",
                ticker,
                row.as_of,
                exc,
            )
            continue
    return updated


def _to_trade_signal(snapshot: Snapshot) -> TradeSignal | None:
    sig = snapshot.signal
    if sig is None:
        return None
    # v1 signals lacking max_holding_bars are unresolvable — skip.
    if sig.max_holding_bars is None:
        return None
    return TradeSignal(
        ticker=snapshot.ticker,
        as_of=snapshot.as_of,
        direction=sig.direction,
        entry_price=sig.entry_price,
        stop_price=sig.stop_price,
        target_price=sig.target_price,
        count_id=sig.count_id or "unknown",
        count_pattern=sig.count_pattern or "unknown",
        wave_label=sig.wave_label or "?",
        degree=sig.degree if sig.degree is not None else 0,
        confidence=sig.confidence if sig.confidence is not None else 0.0,
        expected_bars_to_target=(
            sig.expected_bars_to_target if sig.expected_bars_to_target is not None else 0
        ),
        max_holding_bars=sig.max_holding_bars,
    )


def _to_snapshot_outcome(o: Outcome) -> SnapshotOutcome:
    return SnapshotOutcome(
        status=o.status,
        exit_date=o.exit_date,
        exit_price=o.exit_price,
        bars_held=o.bars_held,
        realized_R=o.realized_R,
        last_observed_date=o.last_observed_date,
        bars_elapsed=o.bars_elapsed,
        unrealized_R=o.unrealized_R,
        mfe_R=o.mfe_R,
        mae_R=o.mae_R,
        resolved_at=datetime.now(tz=UTC),
    )

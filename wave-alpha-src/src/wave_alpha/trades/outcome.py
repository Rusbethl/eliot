"""Pure outcome resolver: given a published trade signal and forward daily
bars, determine target/stop/time_stop/pending using the engine's published
prices. No slippage, no commission, no borrow — that's a backtest concern.

Shared by `backtest.trade_layer` and `history.resolver` so simulation and
live-history outcomes cannot diverge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from wave_alpha.domain import Bar
from wave_alpha.trades.models import TradeSignal

Status = Literal["target", "stop", "time_stop", "pending"]


@dataclass(frozen=True)
class Outcome:
    status: Status
    exit_date: date | None
    exit_price: Decimal | None
    bars_held: int | None
    realized_R: float | None  # noqa: N815
    last_observed_date: date
    bars_elapsed: int
    unrealized_R: float  # noqa: N815
    mfe_R: float  # noqa: N815
    mae_R: float  # noqa: N815


def resolve_outcome(
    signal: TradeSignal,
    bars: list[Bar],
    *,
    as_of_close: date,
) -> Outcome | None:
    walked = [b for b in bars if signal.as_of < b.timestamp <= as_of_close]
    if not walked:
        return None
    entry = signal.entry_price
    risk = abs(entry - signal.stop_price)
    dir_sign = Decimal("1") if signal.direction == "long" else Decimal("-1")

    mfe_r = 0.0
    mae_r = 0.0
    for i, bar in enumerate(walked, start=1):
        # MFE/MAE on intraday extremes
        if signal.direction == "long":
            ex_high = float((bar.high - entry) / risk)
            ex_low = float((bar.low - entry) / risk)
        else:
            ex_high = float(-(bar.low - entry) / risk)
            ex_low = float(-(bar.high - entry) / risk)
        if ex_high > mfe_r:
            mfe_r = ex_high
        if ex_low < mae_r:
            mae_r = ex_low

        # Stop check (wins same-bar ties)
        stop_hit = (signal.direction == "long" and bar.low <= signal.stop_price) or (
            signal.direction == "short" and bar.high >= signal.stop_price
        )
        if stop_hit:
            return _finalize(
                walked,
                i,
                exit_price=signal.stop_price,
                status="stop",
                entry=entry,
                risk=risk,
                dir_sign=dir_sign,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )

        # Target check
        target_hit = (signal.direction == "long" and bar.high >= signal.target_price) or (
            signal.direction == "short" and bar.low <= signal.target_price
        )
        if target_hit:
            return _finalize(
                walked,
                i,
                exit_price=signal.target_price,
                status="target",
                entry=entry,
                risk=risk,
                dir_sign=dir_sign,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )

        # Time stop check
        if i >= signal.max_holding_bars:
            return _finalize(
                walked,
                i,
                exit_price=bar.close,
                status="time_stop",
                entry=entry,
                risk=risk,
                dir_sign=dir_sign,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )

    # Bars exhausted with no exit → pending
    last = walked[-1]
    unrealized = float((last.close - entry) * dir_sign / risk)
    return Outcome(
        status="pending",
        exit_date=None,
        exit_price=None,
        bars_held=None,
        realized_R=None,
        last_observed_date=last.timestamp,
        bars_elapsed=len(walked),
        unrealized_R=unrealized,
        mfe_R=mfe_r,
        mae_R=mae_r,
    )


def _finalize(
    walked: list[Bar],
    i: int,
    *,
    exit_price: Decimal,
    status: Status,
    entry: Decimal,
    risk: Decimal,
    dir_sign: Decimal,
    mfe_r: float,
    mae_r: float,
) -> Outcome:
    realized = float((exit_price - entry) * dir_sign / risk)
    exit_bar = walked[i - 1]
    return Outcome(
        status=status,
        exit_date=exit_bar.timestamp,
        exit_price=exit_price,
        bars_held=i,
        realized_R=realized,
        last_observed_date=exit_bar.timestamp,
        bars_elapsed=i,
        unrealized_R=realized,
        mfe_R=mfe_r,
        mae_R=mae_r,
    )

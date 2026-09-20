from __future__ import annotations

from datetime import date
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.trades.models import TradeSignal
from wave_alpha.trades.outcome import Outcome, resolve_outcome


def _bar(day: int, *, o: str, h: str, low: str, c: str) -> Bar:
    return Bar(
        timestamp=date(2026, 1, day),
        interval=Interval.DAILY,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=1_000_000,
    )


def _long_signal(*, as_of_day: int = 1) -> TradeSignal:
    return TradeSignal(
        ticker="TEST",
        as_of=date(2026, 1, as_of_day),
        direction="long",
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),  # risk = 5
        target_price=Decimal("110"),  # reward = 10 → rr=2.0
        count_id="C1",
        count_pattern="impulse",
        wave_label="3",
        degree=2,
        confidence=0.7,
        expected_bars_to_target=20,
        max_holding_bars=60,
    )


def test_long_target_hit_returns_target_outcome():
    sig = _long_signal()
    bars = [
        _bar(2, o="101", h="105", low="99", c="103"),
        _bar(3, o="103", h="112", low="102", c="111"),  # target hit
        _bar(4, o="111", h="115", low="109", c="112"),
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 4))
    assert isinstance(out, Outcome)
    assert out.status == "target"
    assert out.exit_date == date(2026, 1, 3)
    assert out.exit_price == Decimal("110")
    assert out.bars_held == 2
    assert out.realized_R == 2.0
    assert out.unrealized_R == 2.0  # mirrors realized when finalized
    assert (
        out.bars_elapsed == 2
    )  # filtered to (as_of, as_of_close]; only days 2 and 3 walked before exit
    assert out.last_observed_date == date(2026, 1, 3)


def test_long_stop_hit_returns_stop_outcome():
    sig = _long_signal()
    bars = [
        _bar(2, o="100", h="101", low="94", c="95"),  # stop hit (low <= 95)
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "stop"
    assert out.exit_price == Decimal("95")
    assert out.realized_R == -1.0
    assert out.bars_held == 1


def test_long_stop_and_target_same_bar_stop_wins():
    sig = _long_signal()
    bars = [
        _bar(2, o="100", h="111", low="94", c="100"),  # both hit; stop wins
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "stop"
    assert out.realized_R == -1.0


def test_long_time_stop_fires_at_max_holding_bars():
    sig = TradeSignal(
        ticker="TEST",
        as_of=date(2026, 1, 1),
        direction="long",
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        count_id="C1",
        count_pattern="impulse",
        wave_label="3",
        degree=2,
        confidence=0.7,
        expected_bars_to_target=2,
        max_holding_bars=3,
    )
    bars = [
        _bar(2, o="100", h="101", low="99", c="100"),
        _bar(3, o="100", h="102", low="99", c="101"),
        _bar(4, o="101", h="103", low="100", c="102"),  # time_stop fires here
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 4))
    assert out is not None
    assert out.status == "time_stop"
    assert out.exit_price == Decimal("102")
    assert out.bars_held == 3
    assert out.exit_date == date(2026, 1, 4)
    assert out.realized_R == 0.4  # (102 - 100) / 5


def test_long_pending_when_bars_exhausted_before_max_holding():
    sig = _long_signal()  # max_holding_bars = 60
    bars = [
        _bar(2, o="100", h="103", low="98", c="102"),
        _bar(3, o="102", h="105", low="100", c="104"),
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 3))
    assert out is not None
    assert out.status == "pending"
    assert out.exit_date is None
    assert out.exit_price is None
    assert out.bars_held is None
    assert out.realized_R is None
    assert out.unrealized_R == 0.8  # (104 - 100) / 5
    assert out.bars_elapsed == 2
    assert out.last_observed_date == date(2026, 1, 3)


def test_short_target_hit_mirrors_long():
    sig = TradeSignal(
        ticker="TEST",
        as_of=date(2026, 1, 1),
        direction="short",
        entry_price=Decimal("100"),
        stop_price=Decimal("105"),
        target_price=Decimal("90"),
        count_id="C1",
        count_pattern="zigzag",
        wave_label="C",
        degree=2,
        confidence=0.7,
        expected_bars_to_target=10,
        max_holding_bars=30,
    )
    bars = [
        _bar(2, o="100", h="102", low="89", c="90"),  # target hit (low <= 90)
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "target"
    assert out.exit_price == Decimal("90")
    assert out.realized_R == 2.0  # (100 - 90) / 5 = 2


def test_short_stop_hit_mirrors_long():
    sig = TradeSignal(
        ticker="TEST",
        as_of=date(2026, 1, 1),
        direction="short",
        entry_price=Decimal("100"),
        stop_price=Decimal("105"),
        target_price=Decimal("90"),
        count_id="C1",
        count_pattern="zigzag",
        wave_label="C",
        degree=2,
        confidence=0.7,
        expected_bars_to_target=10,
        max_holding_bars=30,
    )
    bars = [
        _bar(2, o="100", h="106", low="99", c="105"),  # stop hit (high >= 105)
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "stop"
    assert out.realized_R == -1.0


def test_stop_on_first_walked_bar():
    sig = _long_signal()
    bars = [
        _bar(2, o="98", h="98", low="90", c="92"),  # stop hit immediately
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "stop"
    assert out.bars_held == 1


def test_empty_bars_returns_none():
    sig = _long_signal()
    out = resolve_outcome(sig, [], as_of_close=date(2026, 1, 10))
    assert out is None


def test_bars_at_or_before_as_of_are_filtered():
    sig = _long_signal(as_of_day=5)
    bars = [
        _bar(3, o="100", h="111", low="99", c="110"),  # before as_of, skip
        _bar(5, o="100", h="111", low="99", c="110"),  # == as_of, skip
        _bar(6, o="100", h="103", low="98", c="102"),  # walked
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 6))
    assert out is not None
    assert out.status == "pending"
    assert out.bars_elapsed == 1
    assert out.last_observed_date == date(2026, 1, 6)


def test_bars_after_as_of_close_are_filtered():
    sig = _long_signal()
    bars = [
        _bar(2, o="100", h="103", low="99", c="102"),
        _bar(3, o="102", h="115", low="101", c="112"),  # would hit target, but past as_of_close
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "pending"
    assert out.bars_elapsed == 1


def test_mfe_mae_track_intraday_extremes_long():
    sig = _long_signal()  # entry=100, risk=5
    bars = [
        _bar(2, o="100", h="108", low="99", c="105"),  # high=108: MFE=1.6, low=99: MAE=-0.2
        _bar(3, o="105", h="106", low="96", c="100"),  # low=96: MAE=-0.8 (worsens)
        _bar(4, o="100", h="109", low="97", c="103"),  # high=109: MFE=1.8 (improves)
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 4))
    assert out is not None
    assert out.status == "pending"
    assert out.mfe_R == 1.8
    assert out.mae_R == -0.8


def test_mfe_mae_track_intraday_extremes_short():
    sig = TradeSignal(
        ticker="TEST",
        as_of=date(2026, 1, 1),
        direction="short",
        entry_price=Decimal("100"),
        stop_price=Decimal("105"),
        target_price=Decimal("90"),
        count_id="C1",
        count_pattern="zigzag",
        wave_label="C",
        degree=2,
        confidence=0.7,
        expected_bars_to_target=10,
        max_holding_bars=30,
    )
    bars = [
        # short: low=92 favorable → MFE=1.6; high=101 adverse → MAE=-0.2
        _bar(2, o="100", h="101", low="92", c="93"),
        # high=104 adverse → MAE=-0.8 (worsens)
        _bar(3, o="93", h="104", low="93", c="103"),
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 3))
    assert out is not None
    assert out.status == "pending"
    assert out.mfe_R == 1.6
    assert out.mae_R == -0.8


def test_mfe_mae_persist_to_finalized_outcome():
    sig = _long_signal()
    bars = [
        _bar(2, o="100", h="108", low="97", c="105"),  # mfe peaked here
        _bar(3, o="105", h="112", low="104", c="111"),  # target hit
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 3))
    assert out is not None
    assert out.status == "target"
    assert out.mfe_R == 2.4
    assert out.mae_R == -0.6

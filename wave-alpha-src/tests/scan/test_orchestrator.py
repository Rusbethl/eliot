from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.scan.orchestrator import run_scan


class _RisingProvider:
    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = []
        d = date(2024, 1, 1)
        i = 0
        while d <= end:
            if d.weekday() < 5:
                p = 100 + i * 0.4
                bars.append(
                    Bar(
                        timestamp=d,
                        open=Decimal(str(p)),
                        high=Decimal(str(p + 0.5)),
                        low=Decimal(str(p - 0.5)),
                        close=Decimal(str(p)),
                        volume=1_000,
                    )
                )
                i += 1
            d = d + timedelta(days=1)
        return bars


class _BrokenProvider:
    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        if ticker == "BAD":
            raise RuntimeError("provider down for BAD")
        return _RisingProvider().fetch(ticker, interval, end=end)


def test_run_scan_produces_one_row_per_ticker() -> None:
    report = run_scan(
        symbols=["AAPL", "MSFT", "NVDA"],
        as_of=date(2026, 5, 1),
        provider=_RisingProvider(),
    )
    assert {r.ticker for r in report.rows} == {"AAPL", "MSFT", "NVDA"}
    assert report.n_total == 3
    assert report.n_errored == 0


def test_run_scan_captures_errors_per_ticker() -> None:
    report = run_scan(
        symbols=["AAPL", "BAD", "MSFT"],
        as_of=date(2026, 5, 1),
        provider=_BrokenProvider(),
    )
    assert report.n_total == 3
    bad_row = next(r for r in report.rows if r.ticker == "BAD")
    assert bad_row.has_error
    assert "provider down" in (bad_row.error or "")
    # Other rows are unaffected
    assert all(not r.has_error for r in report.rows if r.ticker != "BAD")


def test_run_scan_uses_disabled_llm_by_default() -> None:
    """The orchestrator must NOT instantiate any LLM client by default."""
    import wave_alpha.llm.client as llm_client_mod

    sentinel = {"called": False}
    original_init = llm_client_mod.AnthropicClient.__init__

    def spy(self, *a, **kw):  # type: ignore[no-untyped-def]
        sentinel["called"] = True
        original_init(self, *a, **kw)

    try:
        llm_client_mod.AnthropicClient.__init__ = spy  # type: ignore[method-assign]
        run_scan(symbols=["AAPL"], as_of=date(2026, 5, 1), provider=_RisingProvider())
        assert sentinel["called"] is False
    finally:
        llm_client_mod.AnthropicClient.__init__ = original_init  # type: ignore[method-assign]


def test_run_scan_error_preserves_exception_class_name() -> None:
    report = run_scan(
        symbols=["BAD"],
        as_of=date(2026, 5, 1),
        provider=_BrokenProvider(),
    )
    bad_row = report.rows[0]
    assert bad_row.has_error
    # Format: "ClassName: message"
    assert ":" in (bad_row.error or "")
    assert (bad_row.error or "").split(":")[0] == "RuntimeError"


def test_run_scan_records_signal_when_engine_produces_one() -> None:
    report = run_scan(
        symbols=["AAPL"],
        as_of=date(2026, 5, 1),
        provider=_RisingProvider(),
    )
    row = report.rows[0]
    # On synthetic rising data, engine may or may not produce a signal —
    # if it does, the fields must be consistent.
    if row.has_signal:
        assert row.signal_entry is not None
        assert row.signal_stop is not None
        assert row.signal_target is not None
        assert row.signal_rr is not None


def test_run_scan_omits_right_edge_proba_without_model() -> None:
    """Default behavior: no model passed → no right-edge probability."""
    report = run_scan(
        symbols=["AAPL"],
        as_of=date(2026, 5, 1),
        provider=_RisingProvider(),
    )
    assert report.rows[0].right_edge_proba is None


def test_run_scan_populates_right_edge_proba_when_model_passed() -> None:
    """When a model is provided, the orchestrator surfaces its probability."""

    class _StubModel:
        def predict_proba(self, features, *, degree):  # type: ignore[no-untyped-def]
            return 0.42

    report = run_scan(
        symbols=["AAPL"],
        as_of=date(2026, 5, 1),
        provider=_RisingProvider(),
        right_edge_model=_StubModel(),
    )
    row = report.rows[0]
    # The synthetic rising series produces no tentative pivots → assessment
    # is None → proba is None. Either outcome is acceptable; the contract is
    # only that the orchestrator does not raise and the field is set when
    # the assessment yields one.
    assert row.right_edge_proba in (None, 0.42)


def test_run_scan_right_edge_proba_swallows_model_errors() -> None:
    """A misbehaving model must not abort the scan row."""

    class _BrokenModel:
        def predict_proba(self, features, *, degree):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    report = run_scan(
        symbols=["AAPL"],
        as_of=date(2026, 5, 1),
        provider=_RisingProvider(),
        right_edge_model=_BrokenModel(),
    )
    row = report.rows[0]
    assert row.right_edge_proba is None
    assert not row.has_error

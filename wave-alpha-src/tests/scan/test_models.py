from datetime import date, datetime
from decimal import Decimal

from wave_alpha.scan.models import ScanReport, ScanRow


def test_scan_row_construction() -> None:
    row = ScanRow(
        ticker="AAPL",
        as_of=date(2026, 5, 1),
        coherence=0.78,
        top_pattern="impulse",
        top_score=0.65,
        direction="up",
        signal_rr=Decimal("2.4"),
        signal_entry=Decimal("180"),
        signal_stop=Decimal("172"),
        signal_target=Decimal("199"),
        notes=None,
        error=None,
    )
    assert row.has_signal is True


def test_scan_row_no_signal_when_rr_missing() -> None:
    row = ScanRow(
        ticker="AAPL",
        as_of=date(2026, 5, 1),
        coherence=0.5,
        top_pattern="impulse",
        top_score=0.4,
        direction="up",
    )
    assert row.has_signal is False


def test_scan_row_error_state() -> None:
    row = ScanRow(
        ticker="ZZZ",
        as_of=date(2026, 5, 1),
        coherence=None,
        top_pattern=None,
        top_score=None,
        direction=None,
        error="no data from provider",
    )
    assert row.has_error is True


def test_scan_report_started_completed_invariant() -> None:
    started = datetime(2026, 5, 1, 9, 0, 0)
    completed = datetime(2026, 5, 1, 9, 0, 30)
    report = ScanReport(
        scan_id="2026-05-01T09-00-00",
        started_at=started,
        completed_at=completed,
        as_of=date(2026, 5, 1),
        rows=[],
    )
    assert report.duration_seconds == 30


def test_scan_report_summary_counts() -> None:
    report = ScanReport(
        scan_id="x",
        started_at=datetime(2026, 5, 1, 9, 0, 0),
        completed_at=datetime(2026, 5, 1, 9, 0, 30),
        as_of=date(2026, 5, 1),
        rows=[
            ScanRow(
                ticker="AAPL",
                as_of=date(2026, 5, 1),
                coherence=0.8,
                top_pattern="impulse",
                top_score=0.7,
                direction="up",
                signal_rr=Decimal("2.0"),
                signal_entry=Decimal("180"),
                signal_stop=Decimal("172"),
                signal_target=Decimal("196"),
            ),
            ScanRow(
                ticker="MSFT",
                as_of=date(2026, 5, 1),
                coherence=0.4,
                top_pattern="zigzag",
                top_score=0.5,
                direction="down",
            ),
            ScanRow(
                ticker="ZZZ",
                as_of=date(2026, 5, 1),
                coherence=None,
                top_pattern=None,
                top_score=None,
                direction=None,
                error="no data",
            ),
        ],
    )
    assert report.n_total == 3
    assert report.n_with_signal == 1
    assert report.n_errored == 1

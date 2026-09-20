from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from wave_alpha.scan.archive import (
    DEFAULT_SCAN_DIR,
    list_reports,
    load_report,
    save_report,
)
from wave_alpha.scan.models import ScanReport, ScanRow


def _sample_report(scan_id: str = "2026-05-01T09-00-00") -> ScanReport:
    return ScanReport(
        scan_id=scan_id,
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
        ],
    )


def test_default_scan_dir_under_wave_alpha_home() -> None:
    assert ".wave_alpha" in str(DEFAULT_SCAN_DIR)
    assert DEFAULT_SCAN_DIR.name == "scans"


def test_save_then_load_report_roundtrip(tmp_path: Path) -> None:
    rpt = _sample_report()
    path = save_report(rpt, dir=tmp_path)
    assert path.exists()
    loaded = load_report(rpt.scan_id, dir=tmp_path)
    assert loaded == rpt


def test_list_reports_sorted_newest_first(tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-01T09-00-00"), dir=tmp_path)
    save_report(_sample_report("2026-05-02T09-00-00"), dir=tmp_path)
    save_report(_sample_report("2026-05-01T10-00-00"), dir=tmp_path)
    ids = list_reports(dir=tmp_path)
    assert ids == [
        "2026-05-02T09-00-00",
        "2026-05-01T10-00-00",
        "2026-05-01T09-00-00",
    ]


def test_list_reports_empty_when_no_files(tmp_path: Path) -> None:
    assert list_reports(dir=tmp_path) == []


def test_load_report_missing_returns_none(tmp_path: Path) -> None:
    assert load_report("does-not-exist", dir=tmp_path) is None

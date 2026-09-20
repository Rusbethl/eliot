# tests/web/test_scan_routes.py
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wave_alpha.scan.archive import save_report
from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from wave_alpha.web.routes import scan as scan_routes

    monkeypatch.setattr(scan_routes, "_scan_dir", lambda: tmp_path)
    return TestClient(create_app())


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
            ScanRow(
                ticker="MSFT",
                as_of=date(2026, 5, 1),
                coherence=0.5,
                top_pattern="zigzag",
                top_score=0.4,
                direction="down",
            ),
        ],
    )


def test_get_scan_dashboard_with_no_reports_renders_empty_state(client: TestClient) -> None:
    res = client.get("/scan")
    assert res.status_code == 200
    assert "no scan" in res.text.lower() or "empty" in res.text.lower()


def test_get_scan_dashboard_lists_recent_reports(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-01T09-00-00"), dir=tmp_path)
    save_report(_sample_report("2026-05-02T09-00-00"), dir=tmp_path)
    res = client.get("/scan")
    assert res.status_code == 200
    # Newest first
    assert res.text.index("2026-05-02") < res.text.index("2026-05-01")


def test_get_scan_id_renders_table(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report(), dir=tmp_path)
    res = client.get("/scan/2026-05-01T09-00-00")
    assert res.status_code == 200
    # The redesigned page lazy-loads the row table via HTMX. Verify the page
    # mounts the table target pointed at the right /table endpoint, and that
    # the active scan_id is shown in the page header / pill row.
    assert "2026-05-01T09-00-00" in res.text
    assert 'hx-get="/scan/2026-05-01T09-00-00/table"' in res.text
    assert 'id="scan-table"' in res.text
    # The /table endpoint itself is verified by other tests in this module.


def test_get_scan_table_supports_sort_param(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report(), dir=tmp_path)
    res = client.get("/scan/2026-05-01T09-00-00/table?sort=ticker")
    assert res.status_code == 200
    # AAPL before MSFT alphabetically
    assert res.text.index("AAPL") < res.text.index("MSFT")


def test_get_scan_table_filter_signals(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report(), dir=tmp_path)
    res = client.get("/scan/2026-05-01T09-00-00/table?signals_only=true")
    # Only AAPL has a signal
    assert "AAPL" in res.text
    assert "MSFT" not in res.text


def test_get_unknown_scan_id_returns_404(client: TestClient) -> None:
    res = client.get("/scan/does-not-exist")
    assert res.status_code == 404


def test_scan_table_renders_right_edge_column_and_value(client: TestClient, tmp_path: Path) -> None:
    report = ScanReport(
        scan_id="2026-05-01T10-00-00",
        started_at=datetime(2026, 5, 1, 10, 0, 0),
        completed_at=datetime(2026, 5, 1, 10, 0, 30),
        as_of=date(2026, 5, 1),
        rows=[
            ScanRow(
                ticker="AAPL",
                as_of=date(2026, 5, 1),
                coherence=0.8,
                top_pattern="impulse",
                top_score=0.7,
                direction="up",
                right_edge_proba=0.71,
            ),
        ],
    )
    save_report(report, dir=tmp_path)
    res = client.get("/scan/2026-05-01T10-00-00/table")
    assert res.status_code == 200
    assert "Right-edge" in res.text  # column header (case-sensitive)
    assert "0.71" in res.text


def test_scan_table_supports_right_edge_sort(client: TestClient, tmp_path: Path) -> None:
    report = ScanReport(
        scan_id="2026-05-01T11-00-00",
        started_at=datetime(2026, 5, 1, 11, 0, 0),
        completed_at=datetime(2026, 5, 1, 11, 0, 30),
        as_of=date(2026, 5, 1),
        rows=[
            ScanRow(ticker="AAPL", as_of=date(2026, 5, 1), right_edge_proba=0.30),
            ScanRow(ticker="MSFT", as_of=date(2026, 5, 1), right_edge_proba=0.85),
        ],
    )
    save_report(report, dir=tmp_path)
    res = client.get("/scan/2026-05-01T11-00-00/table?sort=right_edge")
    assert res.status_code == 200
    # MSFT (0.85) ranks above AAPL (0.30) when sorting by right_edge
    assert res.text.index("MSFT") < res.text.index("AAPL")


def test_scan_table_filter_by_bias_long(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-03T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-03T09-00-00/table?bias=long")
    assert res.status_code == 200
    # AAPL is direction=up → matches long; MSFT is direction=down → excluded.
    assert "AAPL" in res.text
    assert "MSFT" not in res.text


def test_scan_table_filter_by_bias_short(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-04T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-04T09-00-00/table?bias=short")
    assert res.status_code == 200
    assert "MSFT" in res.text
    assert "AAPL" not in res.text


def test_scan_table_filter_by_bias_any_returns_all(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-05T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-05T09-00-00/table?bias=any")
    assert res.status_code == 200
    assert "AAPL" in res.text
    assert "MSFT" in res.text


def test_scan_table_filter_min_rr_drops_rows_below_threshold(
    client: TestClient, tmp_path: Path
) -> None:
    save_report(_sample_report("2026-05-06T09-00-00"), dir=tmp_path)
    # AAPL has rr=2.0; threshold 3.0 should drop it.
    res = client.get("/scan/2026-05-06T09-00-00/table?min_rr=3.0")
    assert res.status_code == 200
    assert "AAPL" not in res.text


def test_scan_table_filter_min_rr_zero_is_noop_for_signalled_rows(
    client: TestClient, tmp_path: Path
) -> None:
    save_report(_sample_report("2026-05-07T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-07T09-00-00/table?min_rr=0")
    assert res.status_code == 200
    # AAPL has a signal at rr=2.0 — kept.
    assert "AAPL" in res.text


def test_scan_table_invalid_bias_returns_422(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-08T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-08T09-00-00/table?bias=foo")
    assert res.status_code == 422


def test_scan_table_negative_min_rr_returns_422(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-09T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-09T09-00-00/table?min_rr=-1.0")
    assert res.status_code == 422


def test_scan_table_renders_column_header_popovers(client: TestClient, tmp_path: Path) -> None:
    """The /scan/{id}/table fragment renders 4 column-header popovers."""
    save_report(_sample_report("2026-05-10T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-10T09-00-00/table")
    assert res.status_code == 200
    body = res.text
    # Four popover triggers: Score, Coherence, R:R, Right-edge
    assert body.count("popover-trigger") >= 4
    # Each links to its /about anchor
    assert 'href="/about#enumeration"' in body
    assert 'href="/about#coherence"' in body
    assert 'href="/about#trade-plan"' in body
    assert 'href="/about#right-edge"' in body

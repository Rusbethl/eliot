from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wave_alpha.scan.archive import save_report
from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.watchlist.store import save_watchlist
from wave_alpha.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from wave_alpha.web import deps as web_deps
    from wave_alpha.web.routes import scan as scan_routes
    from wave_alpha.web.routes import watchlist as wl_routes

    monkeypatch.setattr(scan_routes, "_scan_dir", lambda: tmp_path)
    monkeypatch.setattr(wl_routes, "_watchlist_path", lambda: tmp_path / "wl.yaml")
    monkeypatch.setattr(web_deps, "_data_dir", lambda: tmp_path)
    return TestClient(create_app())


def test_home_renders_empty_state_with_no_data(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "wave-alpha" in res.text.lower()


def test_home_renders_top_signal_when_scan_present(client: TestClient, tmp_path: Path) -> None:
    save_report(
        ScanReport(
            scan_id="2026-05-09T14-30-00",
            started_at=datetime(2026, 5, 9, 14, 30, 0),
            completed_at=datetime(2026, 5, 9, 14, 30, 30),
            as_of=date(2026, 5, 9),
            rows=[
                ScanRow(
                    ticker="AAPL",
                    as_of=date(2026, 5, 9),
                    top_pattern="impulse",
                    top_score=0.82,
                    direction="up",
                    signal_rr=Decimal("3.21"),
                    signal_entry=Decimal("180"),
                    signal_stop=Decimal("172"),
                    signal_target=Decimal("196"),
                ),
            ],
        ),
        dir=tmp_path,
    )
    res = client.get("/")
    assert res.status_code == 200
    assert "AAPL" in res.text
    assert "impulse" in res.text


def test_home_renders_watchlist_section_when_entries_exist(
    client: TestClient, tmp_path: Path
) -> None:
    save_watchlist(
        [WatchlistEntry(symbol="TSLA", added_on=date(2026, 5, 1))],
        tmp_path / "wl.yaml",
    )
    res = client.get("/")
    assert res.status_code == 200
    assert "TSLA" in res.text


def test_home_kpi_strip_renders_popovers(client: TestClient) -> None:
    """The home page renders 4 KPI trust popovers in the KPI strip."""
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    assert body.count("popover-trigger") >= 4
    # Jinja HTML-escapes single quotes in the rendered headlines (' -> &#39;).
    assert "What &#39;Signals&#39; counts" in body
    assert "What &#39;Top R:R&#39; shows" in body
    assert "What &#39;Watchlist hits&#39; counts" in body
    assert "What &#39;Stable picks&#39; means" in body

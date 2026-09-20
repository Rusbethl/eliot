# tests/web/test_watchlist_routes.py
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.watchlist.store import save_watchlist
from wave_alpha.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from wave_alpha.web.routes import scan as scan_routes
    from wave_alpha.web.routes import watchlist as wl_routes

    monkeypatch.setattr(wl_routes, "_watchlist_path", lambda: tmp_path / "wl.yaml")
    monkeypatch.setattr(scan_routes, "_scan_dir", lambda: tmp_path)
    return TestClient(create_app())


def test_get_watchlist_renders_empty_state(client: TestClient) -> None:
    res = client.get("/watchlist")
    assert res.status_code == 200
    assert "watchlist" in res.text.lower()


def test_post_watchlist_add_persists_and_redirects(client: TestClient, tmp_path: Path) -> None:
    res = client.post("/watchlist/add", data={"symbol": "AAPL"}, follow_redirects=False)
    assert res.status_code == 303  # POST-redirect-GET
    assert res.headers["location"] == "/watchlist"
    from wave_alpha.watchlist.store import load_watchlist
    from wave_alpha.web.routes import watchlist as wl_routes

    entries = load_watchlist(wl_routes._watchlist_path())
    assert [e.symbol for e in entries] == ["AAPL"]


def test_post_watchlist_remove(client: TestClient, tmp_path: Path) -> None:
    from wave_alpha.web.routes import watchlist as wl_routes

    save_watchlist(
        [WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1))],
        wl_routes._watchlist_path(),
    )
    res = client.post("/watchlist/rm", data={"symbol": "AAPL"}, follow_redirects=False)
    assert res.status_code == 303
    from wave_alpha.watchlist.store import load_watchlist

    assert load_watchlist(wl_routes._watchlist_path()) == []


def test_post_watchlist_add_rejects_invalid_symbol(client: TestClient) -> None:
    res = client.post("/watchlist/add", data={"symbol": "bad sym"}, follow_redirects=False)
    # 400 with error in response body
    assert res.status_code == 400
    assert "invalid" in res.text.lower()


def test_get_watchlist_passes_enriched_rows_to_template(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """When a recent scan exists, the watchlist page surfaces last-known
    signal information from it (verified by checking the rendered text
    contains the pattern from the scan)."""
    from datetime import date as _date
    from datetime import datetime

    from wave_alpha.scan.archive import save_report
    from wave_alpha.scan.models import ScanReport, ScanRow
    from wave_alpha.web.routes import scan as scan_routes
    from wave_alpha.web.routes import watchlist as wl_routes

    monkeypatch.setattr(scan_routes, "_scan_dir", lambda: tmp_path)

    save_watchlist(
        [WatchlistEntry(symbol="AAPL", added_on=_date(2026, 5, 1))],
        wl_routes._watchlist_path(),
    )
    save_report(
        ScanReport(
            scan_id="2026-05-09T14-30-00",
            started_at=datetime(2026, 5, 9, 14, 30, 0),
            completed_at=datetime(2026, 5, 9, 14, 30, 30),
            as_of=_date(2026, 5, 9),
            rows=[
                ScanRow(
                    ticker="AAPL",
                    as_of=_date(2026, 5, 9),
                    top_pattern="impulse",
                    top_score=0.82,
                    direction="up",
                ),
            ],
        ),
        dir=tmp_path,
    )

    res = client.get("/watchlist")
    assert res.status_code == 200
    assert "AAPL" in res.text
    # The enrichment should surface the pattern from the scan in the page.
    assert "impulse" in res.text


def test_get_watchlist_renders_when_no_scan_exists(client: TestClient, tmp_path: Path) -> None:
    """Empty scan dir must not break the page."""
    from datetime import date as _date

    from wave_alpha.web.routes import watchlist as wl_routes

    save_watchlist(
        [WatchlistEntry(symbol="AAPL", added_on=_date(2026, 5, 1))],
        wl_routes._watchlist_path(),
    )
    res = client.get("/watchlist")
    assert res.status_code == 200
    assert "AAPL" in res.text

"""Static-asset content guards — cheap regressions for vendored bundle drift."""

from __future__ import annotations

from importlib.resources import files


def _read(rel_path: str) -> str:
    return (files("wave_alpha.web") / rel_path).read_text()


def test_deepdive_js_uses_lightweight_charts_v5_api() -> None:
    """v5 dropped addCandlestickSeries / series.setMarkers. Don't regress."""
    js = _read("static/deepdive.js")
    # v5 API present
    assert "addSeries(" in js
    assert "CandlestickSeries" in js
    assert "createSeriesMarkers(" in js
    # v3/v4 API absent
    assert "addCandlestickSeries" not in js
    assert ".setMarkers(" not in js


def test_lightweight_charts_bundle_is_v5() -> None:
    """The vendored bundle must expose v5 entry points so deepdive.js runs."""
    bundle = _read("static/vendor/lightweight-charts/lightweight-charts.standalone.production.js")
    assert "v5." in bundle  # license header carries the version
    assert "CandlestickSeries" in bundle
    assert "createSeriesMarkers" in bundle


def test_vendored_assets_serve_via_fastapi() -> None:
    from fastapi.testclient import TestClient

    from wave_alpha.web.app import create_app

    client = TestClient(create_app())
    paths = [
        "/static/app.css",
        "/static/deepdive.js",
        "/static/vendor/htmx/htmx.min.js",
        "/static/vendor/alpine/alpine.min.js",
        "/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js",
    ]
    for path in paths:
        res = client.get(path)
        assert res.status_code == 200, f"{path} → {res.status_code}"
        assert len(res.content) > 1000, f"{path} suspiciously small ({len(res.content)} bytes)"


def test_app_css_declares_brand_tokens() -> None:
    from importlib.resources import files

    css = (files("wave_alpha.web") / "static" / "app.css").read_text()
    # Tokens used everywhere in the redesign — if any go missing, lots of pages break.
    for token in [
        "--bg-base",
        "--bg-surface",
        "--bg-elevated",
        "--fg-primary",
        "--fg-secondary",
        "--fg-muted",
        "--cyan",
        "--magenta",
        "--purple",
        "--yellow",
        "--bull",
        "--bear",
        "--candle-up",
        "--candle-down",
        "--font-sans",
        "--font-mono",
    ]:
        assert token in css, f"missing design token: {token}"

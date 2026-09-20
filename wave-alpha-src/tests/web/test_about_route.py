"""GET /about: renders page, includes all 11 anchor IDs, includes disclaimer."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wave_alpha.web.app import create_app

ANCHORS = [
    "pipeline",
    "pivots",
    "templates",
    "enumeration",
    "coherence",
    "right-edge",
    "alpha-blend",
    "trade-plan",
    "lookahead",
    "privacy",
    "what-this-is-not",
]


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from wave_alpha.web import deps as web_deps
    from wave_alpha.web.routes import scan as scan_routes
    from wave_alpha.web.routes import watchlist as wl_routes

    monkeypatch.setattr(scan_routes, "_scan_dir", lambda: tmp_path)
    monkeypatch.setattr(wl_routes, "_watchlist_path", lambda: tmp_path / "wl.yaml")
    monkeypatch.setattr(web_deps, "_data_dir", lambda: tmp_path)
    return TestClient(create_app())


def test_about_returns_200(client: TestClient) -> None:
    res = client.get("/about")
    assert res.status_code == 200


def test_about_contains_all_anchor_ids(client: TestClient) -> None:
    res = client.get("/about")
    body = res.text
    missing = [a for a in ANCHORS if f'id="{a}"' not in body]
    assert not missing, f"missing anchor sections: {missing}"


def test_about_contains_disclaimer(client: TestClient) -> None:
    res = client.get("/about")
    assert "Informational only" in res.text


def test_about_contains_h1(client: TestClient) -> None:
    res = client.get("/about")
    assert "How wave-alpha works" in res.text


def test_nav_includes_about_link_on_every_page(client: TestClient) -> None:
    for path in ["/", "/scan", "/watchlist", "/settings", "/about"]:
        res = client.get(path)
        assert res.status_code == 200, f"{path} returned {res.status_code}"
        assert 'href="/about"' in res.text, f"About link missing on {path}"


def test_about_sections_have_substantive_content(client: TestClient) -> None:
    """Every section should have prose beyond the placeholder stub."""
    res = client.get("/about")
    body = res.text
    # The placeholder text from Task 2 must not survive past Task 11.
    assert "Filled in Task 11" not in body
    # Each section title appears as an <h2>.
    for title in (
        "Pipeline overview",
        "Pivots &amp; degrees",
        "Wave templates",
        "Enumeration &amp; scoring",
        "Multi-timeframe coherence",
        "Right-edge confirmation",
        "LLM rerank &amp; α blend",  # noqa: RUF001
        "Trade plan derivation",
        "Lookahead-safety guarantee",
        "Privacy contract",
        "What this is not",
    ):
        assert f"<h2>{title}</h2>" in body, f"missing section heading: {title}"


def test_about_pipeline_section_includes_diagram_and_citation(client: TestClient) -> None:
    res = client.get("/about")
    body = res.text
    pipeline = body.split('id="pipeline"', 1)[1].split('id="pivots"', 1)[0]
    assert "<svg" in pipeline and "diagram" in pipeline
    assert "PointInTimeView" in pipeline or "pipeline.py" in pipeline


def test_about_what_this_is_not_section_lists_caveats(client: TestClient) -> None:
    res = client.get("/about")
    body = res.text
    section = body.split('id="what-this-is-not"', 1)[1]
    assert "not financial advice" in section.lower()
    assert "miss patterns" in section.lower() or "can and does miss" in section.lower()

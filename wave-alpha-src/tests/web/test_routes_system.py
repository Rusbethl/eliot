from fastapi.testclient import TestClient

from wave_alpha.web.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_root_renders_home_page() -> None:
    res = _client().get("/", follow_redirects=False)
    assert res.status_code == 200
    # Sanity: the brand mark / wordmark appears.
    assert "wave-alpha" in res.text.lower()


def test_healthz_returns_ok() -> None:
    res = _client().get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

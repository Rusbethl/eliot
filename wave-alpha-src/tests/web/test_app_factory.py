from fastapi.testclient import TestClient

from wave_alpha.web.app import create_app


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app()
    assert app.title == "wave-alpha"


def test_static_files_mounted() -> None:
    app = create_app()
    client = TestClient(app)
    res = client.get("/static/app.css")
    assert res.status_code == 200
    assert "container" in res.text


def test_jinja_disclaimer_global_available() -> None:
    app = create_app()
    # Jinja env is reachable via app.state.templates
    env = app.state.templates.env
    assert "disclaimer" in env.globals
    assert "Informational only" in env.globals["disclaimer"]

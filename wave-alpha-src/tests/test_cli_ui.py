import re
import socket

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import _pick_port, app

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def test_ui_help_documents_port_and_no_browser_flags() -> None:
    res = runner.invoke(app, ["ui", "--help"])
    assert res.exit_code == 0
    stdout = _ANSI.sub("", res.stdout)
    assert "--port" in stdout
    assert "--no-browser" in stdout


def test_pick_port_returns_first_free(monkeypatch) -> None:
    # Bind 8765 to simulate it being taken; pick_port should return 8766
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 8765))
    try:
        port = _pick_port(start=8765, end=8775)
        assert port == 8766
    finally:
        s.close()


def test_pick_port_raises_when_no_ports_free(monkeypatch) -> None:
    sockets = []
    for p in range(9000, 9003):
        sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sk.bind(("127.0.0.1", p))
        sockets.append(sk)
    try:
        with pytest.raises(RuntimeError, match="no free port"):
            _pick_port(start=9000, end=9002)
    finally:
        for sk in sockets:
            sk.close()

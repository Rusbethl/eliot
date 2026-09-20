import subprocess
from unittest.mock import MagicMock

import pytest

from wave_alpha.llm.claude_code_client import ClaudeCodeClient
from wave_alpha.llm.client import LLMRequest


def test_claude_code_client_returns_stdout(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        result = MagicMock()
        result.stdout = "canned response"
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = ClaudeCodeClient(model="claude-haiku-4-5-20251001")
    req = LLMRequest(model="claude-haiku-4-5-20251001", system="SYS", user="USR")
    text = client.run(req)

    assert text == "canned response"
    argv = captured["argv"]
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "USR" in argv
    assert "--system-prompt" in argv
    assert "SYS" in argv
    assert "--model" in argv
    assert "claude-haiku-4-5-20251001" in argv
    assert captured["kwargs"]["check"] is True
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["timeout"] == 60


def test_claude_code_client_passes_json_schema_when_set(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        result = MagicMock()
        result.stdout = '{"ok": true}'
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    schema = '{"type":"object","properties":{"ok":{"type":"boolean"}}}'
    client = ClaudeCodeClient(model="claude-haiku-4-5-20251001", json_schema=schema)
    client.run(LLMRequest(model="x", system="s", user="u"))

    argv = captured["argv"]
    assert "--json-schema" in argv
    assert schema in argv


def test_claude_code_client_omits_json_schema_when_unset(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        result = MagicMock()
        result.stdout = "x"
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    ClaudeCodeClient(model="claude-haiku-4-5-20251001").run(
        LLMRequest(model="x", system="s", user="u")
    )

    assert "--json-schema" not in captured["argv"]


def test_claude_code_client_propagates_subprocess_error(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = ClaudeCodeClient(model="claude-haiku-4-5-20251001")
    req = LLMRequest(model="claude-haiku-4-5-20251001", system="s", user="u")
    with pytest.raises(subprocess.CalledProcessError):
        client.run(req)

from datetime import datetime
from pathlib import Path

import pytest

from wave_alpha.llm.cache import LLMResponseCache
from wave_alpha.llm.client import LLMRequest
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.runner import CachedResponseMissError, LLMRunner


class _StubClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def run(self, req: LLMRequest) -> str:
        self.calls += 1
        return self.response


def _runner(
    tmp_path: Path, *, mode: LLMMode, response: str = '{"ok": true}'
) -> tuple[LLMRunner, _StubClient]:
    cache = LLMResponseCache(db_path=tmp_path / "llm.sqlite")
    client = _StubClient(response)
    now = lambda: datetime(2024, 1, 1)  # noqa: E731
    runner = LLMRunner(client=client, cache=cache, mode=mode, now=now)
    return runner, client


def test_disabled_mode_returns_none_without_calling(tmp_path: Path) -> None:
    runner, client = _runner(tmp_path, mode=LLMMode.DISABLED)
    req = LLMRequest(model="haiku", system="S", user="U")
    assert runner.run(req) is None
    assert client.calls == 0


def test_live_mode_calls_client_and_writes_cache(tmp_path: Path) -> None:
    runner, client = _runner(tmp_path, mode=LLMMode.LIVE, response="R1")
    req = LLMRequest(model="haiku", system="S", user="U")
    assert runner.run(req) == "R1"
    assert client.calls == 1
    # second call hits cache
    assert runner.run(req) == "R1"
    assert client.calls == 1


def test_cached_mode_returns_cached_response(tmp_path: Path) -> None:
    runner_live, _ = _runner(tmp_path, mode=LLMMode.LIVE, response="R2")
    req = LLMRequest(model="haiku", system="S", user="U")
    runner_live.run(req)
    runner_cached, client = _runner(tmp_path, mode=LLMMode.CACHED)
    assert runner_cached.run(req) == "R2"
    assert client.calls == 0


def test_cached_mode_misses_raise(tmp_path: Path) -> None:
    runner, _ = _runner(tmp_path, mode=LLMMode.CACHED)
    req = LLMRequest(model="haiku", system="S", user="U")
    with pytest.raises(CachedResponseMissError):
        runner.run(req)

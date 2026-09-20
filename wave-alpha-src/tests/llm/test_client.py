from unittest.mock import MagicMock

import pytest

from wave_alpha.llm.client import AnthropicClient, LLMRequest, MissingAPIKeyError


class _StubMessages:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        block = MagicMock()
        block.text = self.response_text
        msg = MagicMock()
        msg.content = [block]
        return msg


class _StubAnthropic:
    def __init__(self, response_text: str) -> None:
        self.messages = _StubMessages(response_text)


def test_client_missing_api_key_raises() -> None:
    with pytest.raises(MissingAPIKeyError):
        AnthropicClient(api_key=None)


def test_client_sends_system_block_with_cache_control() -> None:
    stub = _StubAnthropic('{"ok": true}')
    client = AnthropicClient(api_key="sk-test", _sdk_factory=lambda **_: stub)
    req = LLMRequest(model="claude-haiku-4-5-20251001", system="SYS", user="USR")
    text = client.run(req)
    assert text == '{"ok": true}'
    sent = stub.messages.last_kwargs
    assert sent["model"] == "claude-haiku-4-5-20251001"
    assert sent["temperature"] == 0
    sys_block = sent["system"]
    assert isinstance(sys_block, list) and len(sys_block) == 1
    assert sys_block[0]["text"] == "SYS"
    assert sys_block[0]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][0] == {"role": "user", "content": "USR"}


def test_client_uses_max_tokens_default() -> None:
    stub = _StubAnthropic('{"ok": true}')
    client = AnthropicClient(api_key="sk-test", _sdk_factory=lambda **_: stub)
    client.run(LLMRequest(model="claude-haiku-4-5-20251001", system="S", user="U"))
    assert stub.messages.last_kwargs["max_tokens"] == 2048


def test_cache_only_client_holds_model_id() -> None:
    from wave_alpha.llm.client import CacheOnlyClient

    c = CacheOnlyClient(model="claude-haiku-4-5-20251001")
    assert c.model == "claude-haiku-4-5-20251001"


def test_cache_only_client_run_raises() -> None:
    from wave_alpha.llm.client import CacheOnlyClient, LLMRequest

    c = CacheOnlyClient(model="claude-haiku-4-5-20251001")
    with pytest.raises(RuntimeError, match="should never happen"):
        c.run(LLMRequest(model="x", system="s", user="u"))

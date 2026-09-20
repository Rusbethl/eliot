from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class MissingAPIKeyError(RuntimeError):
    """Raised when an LLM call is attempted without a configured API key."""


class LLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    system: str
    user: str
    max_tokens: int = 2048


class LLMClient(Protocol):
    """Minimum interface a runner needs from a client.

    AnthropicClient implements this. Tests use stubs; CACHED mode without
    an API key uses CacheOnlyClient (which never .run()s).
    """

    model: str

    def run(self, req: LLMRequest) -> str: ...


@dataclass(frozen=True)
class CacheOnlyClient:
    """A no-op client used when the runner is in CACHED mode without an API key.

    Holds the model id so cache lookups have the right key. Raises if .run()
    is invoked, which only happens on cache miss in LIVE — by construction
    that path is unreachable in CACHED mode.
    """

    model: str

    def run(self, req: LLMRequest) -> str:
        raise RuntimeError(
            "CacheOnlyClient.run() invoked — this should never happen in CACHED mode"
        )


@dataclass
class AnthropicClient:
    """Thin wrapper over the official Anthropic SDK.

    All calls run with temperature=0 and a prompt-cache breakpoint after the
    static system block (per spec §6 — "Prompt caching"). Pass _sdk_factory to
    inject a stub in tests; default lazily imports the real SDK.
    """

    api_key: str | None
    model: str = "claude-haiku-4-5-20251001"
    _sdk_factory: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        if not self.api_key:
            raise MissingAPIKeyError(
                "no Anthropic API key configured; set ANTHROPIC_API_KEY or run 'wave-alpha config set api_key'"
            )

    def _client(self) -> Any:
        if self._sdk_factory is not None:
            return self._sdk_factory(api_key=self.api_key)
        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "LLM reranking requires the 'llm' extra; install with "
                "`pip install wave-alpha[llm]` (or `uv pip install wave-alpha[llm]`)"
            ) from e
        return anthropic.Anthropic(api_key=self.api_key)

    def run(self, req: LLMRequest) -> str:
        sdk = self._client()
        message = sdk.messages.create(
            model=req.model,
            max_tokens=req.max_tokens,
            temperature=0,
            system=[{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": req.user}],
        )
        # Extract first text block
        for block in message.content:
            text = getattr(block, "text", None)
            if text:
                return text
        raise RuntimeError("Anthropic response contained no text block")

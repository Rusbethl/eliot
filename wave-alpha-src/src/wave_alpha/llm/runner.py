from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from wave_alpha.llm.cache import LLMResponseCache, prompt_hash
from wave_alpha.llm.client import LLMClient, LLMRequest
from wave_alpha.llm.modes import LLMMode


class CachedResponseMissError(RuntimeError):
    """Raised by LLMMode.CACHED when no cached response exists."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class LLMRunner:
    """Orchestrates cache-lookup → API call → cache-write per the configured mode.

    DISABLED → return None (caller skips LLM blending).
    CACHED   → cache-only; raise CachedResponseMissError on miss.
    LIVE     → cache-first, fall through to client, write back.
    """

    client: LLMClient
    cache: LLMResponseCache
    mode: LLMMode
    now: Callable[[], datetime] = _utcnow

    def run(self, req: LLMRequest) -> str | None:
        if self.mode is LLMMode.DISABLED:
            return None
        h = prompt_hash(req.system, req.user)
        cached = self.cache.get(req.model, h)
        if cached is not None:
            return cached
        if self.mode is LLMMode.CACHED:
            raise CachedResponseMissError(f"no cached response for model={req.model} hash={h[:12]}")
        response = self.client.run(req)
        self.cache.put(req.model, h, response=response, created_at=self.now())
        return response

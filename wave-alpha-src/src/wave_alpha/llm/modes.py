from __future__ import annotations

from enum import StrEnum


class LLMMode(StrEnum):
    """Run mode for the LLM stage.

    LIVE     — call the Anthropic API and write responses to the cache.
    CACHED   — cache-only; raise on miss. Used for paper-reproducible runs.
    DISABLED — skip the LLM stage entirely; engine score is the final score
               (alpha treated as 1.0). The mandatory backtest baseline.
    """

    LIVE = "live"
    CACHED = "cached"
    DISABLED = "disabled"

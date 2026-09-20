from __future__ import annotations

import subprocess
from dataclasses import dataclass

from wave_alpha.llm.client import LLMRequest


@dataclass(frozen=True)
class ClaudeCodeClient:
    """LLMClient implementation that shells out to the `claude` CLI.

    Spike-only — uses the user's Claude Code Max-plan auth on disk
    (no API key argument). Bypasses prompt caching breakpoints and
    SDK-level features; transport is the simplest thing that works.
    See docs/superpowers/specs/2026-05-08-claude-code-llm-spike-design.md.
    """

    model: str
    json_schema: str | None = None

    def run(self, req: LLMRequest) -> str:
        argv = [
            "claude",
            "-p",
            req.user,
            "--system-prompt",
            req.system,
            "--model",
            self.model,
            "--output-format",
            "text",
        ]
        if self.json_schema is not None:
            argv.extend(["--json-schema", self.json_schema])
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return result.stdout

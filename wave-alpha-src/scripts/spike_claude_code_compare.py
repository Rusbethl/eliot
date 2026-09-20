"""Spike: side-by-side compare AnthropicClient vs ClaudeCodeClient.

One-shot throwaway. Reads ANTHROPIC_API_KEY from env (required for the
baseline). Assumes the user is logged into the `claude` CLI for the
Claude Code path. Writes reports/claude_code_spike.json.

See docs/superpowers/specs/2026-05-08-claude-code-llm-spike-design.md.
Run: `uv run python scripts/spike_claude_code_compare.py`
"""

from __future__ import annotations

import json
import os
import time
import traceback
from datetime import date
from pathlib import Path

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.llm.candidates import serialize_candidate_set
from wave_alpha.llm.claude_code_client import ClaudeCodeClient
from wave_alpha.llm.client import AnthropicClient, LLMRequest
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.prompts import SCAN_SYSTEM_PROMPT, build_scan_user_prompt
from wave_alpha.llm.responses import ScanResponse, parse_scan_response
from wave_alpha.pipeline import AnalyzeRequest, run_analysis

TICKER = "AAPL"
AS_OF = date(2024, 12, 31)
MODEL = "claude-haiku-4-5-20251001"
OUT_PATH = Path("reports/claude_code_spike.json")


def _build_request() -> tuple[LLMRequest, set[str]]:
    """Run the engine pipeline once (LLM disabled) to get real candidates,
    then construct the same LLMRequest that `_maybe_run_scan` would build."""
    provider = CachedProvider(
        upstream=YahooProvider(),
        db_path=Path.home() / ".wave_alpha" / "cache.sqlite",
    )
    result = run_analysis(
        AnalyzeRequest(ticker=TICKER, as_of=AS_OF),
        provider=provider,
        llm_mode=LLMMode.DISABLED,
    )
    candidates = result.counts_daily
    if not candidates:
        raise RuntimeError(
            f"engine produced zero daily candidates for {TICKER} as-of {AS_OF}; "
            "pick a different ticker/date for the spike."
        )
    coh_score = result.coherence.score if result.coherence else 0.0
    block = serialize_candidate_set(candidates)
    user = build_scan_user_prompt(
        ticker=TICKER, as_of=AS_OF, candidates_block=block, coherence_score=coh_score
    )
    req = LLMRequest(model=MODEL, system=SCAN_SYSTEM_PROMPT, user=user)
    allowed = {f"C{i + 1}" for i in range(len(candidates))}
    return req, allowed


def _run_one(name: str, client, req: LLMRequest, allowed: set[str]) -> dict:
    record: dict = {"client": name}
    t0 = time.perf_counter()
    try:
        raw = client.run(req)
        record["raw"] = raw
        record["latency_s"] = time.perf_counter() - t0
        try:
            parsed = parse_scan_response(raw, allowed_candidate_ids=allowed)
            record["parsed"] = parsed.model_dump(mode="json")
            record["parse_error"] = None
        except Exception as e:
            record["parsed"] = None
            record["parse_error"] = f"{type(e).__name__}: {e}"
    except Exception as e:
        record["raw"] = None
        record["latency_s"] = time.perf_counter() - t0
        record["parsed"] = None
        record["parse_error"] = None
        record["client_error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    return record


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    print(f"Building LLMRequest for {TICKER} as-of {AS_OF}...")
    req, allowed = _build_request()
    print(
        f"  prompt: system={len(req.system)} chars, user={len(req.user)} chars, "
        f"candidates={len(allowed)}"
    )

    if api_key:
        print("Running AnthropicClient...")
        anthropic_record = _run_one(
            "anthropic", AnthropicClient(api_key=api_key, model=MODEL), req, allowed
        )
    else:
        print("Skipping AnthropicClient (no ANTHROPIC_API_KEY set).")
        anthropic_record = {
            "client": "anthropic",
            "skipped": True,
            "skip_reason": "ANTHROPIC_API_KEY not set",
        }

    schema_str = json.dumps(ScanResponse.model_json_schema())
    print(f"Running ClaudeCodeClient with --json-schema ({len(schema_str)} chars)...")
    claude_code_record = _run_one(
        "claude_code",
        ClaudeCodeClient(model=MODEL, json_schema=schema_str),
        req,
        allowed,
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "ticker": TICKER,
        "as_of": AS_OF.isoformat(),
        "model": MODEL,
        "request": {
            "system_chars": len(req.system),
            "user_chars": len(req.user),
            "allowed_candidate_ids": sorted(allowed),
        },
        "anthropic": anthropic_record,
        "claude_code": claude_code_record,
    }
    OUT_PATH.write_text(json.dumps(artifact, indent=2, default=str))
    print(f"\nWrote {OUT_PATH}")
    print(f"  anthropic:   {_summary_line(anthropic_record)}")
    print(f"  claude_code: {_summary_line(claude_code_record)}")


def _summary_line(record: dict) -> str:
    if record.get("skipped"):
        return f"SKIPPED ({record.get('skip_reason', 'no reason given')})"
    parts = [f"{record['latency_s']:.2f}s"]
    if "client_error" in record:
        parts.append("client_error=True")
    parts.append(f"parse_error={record.get('parse_error')!r}")
    return ", ".join(parts)


if __name__ == "__main__":
    main()

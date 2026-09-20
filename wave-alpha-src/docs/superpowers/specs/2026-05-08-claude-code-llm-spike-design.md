---
related:
  - src/wave_alpha/llm/client.py  # LLMClient Protocol — the seam this spike targets
  - src/wave_alpha/llm/runner.py  # LLMMode wiring (DISABLED / CACHED / LIVE)
---

# Claude Code Max as `--llm-mode live` backend — spike design

## 1. Motivation

The LLM reranker today only authenticates via an Anthropic API key
(`src/wave_alpha/llm/client.py:53` `AnthropicClient`). Users with a
Claude Code Max subscription have to pay for API credits separately
to use `--llm-mode live`. The deferred 2026-05-05 discussion
proposed adding a `ClaudeCodeClient` that uses the user's existing
`claude` CLI auth so the Max plan covers `live` mode.

Before designing the full feature (third config option, fallback
chain, scan-vs-analyze cost model, model-id mapping), this spike
answers the empirical question: **does the simplest possible
transport — shelling out to `claude -p` — actually work end-to-end
with our existing prompts, and at acceptable latency?**

If the spike says yes, we invest in the full feature. If it says no
(too slow, output drift breaks the parser, auth is fragile), we
either drop the idea or pivot to the `claude-agent-sdk` Python
package as the transport.

## 2. Goals and non-goals

**Goals**

- Implement a minimal `ClaudeCodeClient` satisfying the existing
  `LLMClient` Protocol, transport = `subprocess.run(["claude", "-p", ...])`.
- Run one `analyze` invocation on a hardcoded ticker through both
  `AnthropicClient` and `ClaudeCodeClient` against an identical
  `LLMRequest`, capturing raw response, parsed result, and wall-clock
  latency for each.
- Dump a comparison artifact to `reports/claude_code_spike.json`
  with enough detail to make a go/no-go call on the full feature.
- Keep the spike fully throwaway — zero changes to `cli/app.py`,
  `LLMMode`, `RuntimeConfig`, or any user-facing CLI flag.

**Non-goals**

- Designing the full feature's UX (config flags, fallback chain,
  cache key model-id policy, scan-mode latency budget).
- Prompt-cache analysis. The CLI / Agent SDK manages caching
  internally; quantifying that is a question for the full feature.
- Multi-ticker batch comparison. Single ticker is enough to settle
  the spike's three questions; variance across tickers is a full-
  feature concern.
- Privacy / argv-exposure review of subprocess prompt passing. Noted
  as a follow-up question for the full feature; the spike runs only
  on the user's own machine with non-sensitive payloads (the wave_alpha
  privacy contract — ticker, pivots, candidate counts, coherence
  metrics — contains nothing the user should hide from their own
  process listing).

## 3. Success criteria

The spike is a **success** iff `reports/claude_code_spike.json`
shows all three of the following:

1. **Functional equivalence** — the `ClaudeCodeClient` raw response
   parses without error through the existing JSON-response parser
   used by `AnthropicClient`'s downstream consumer (i.e., the same
   `llm/responses.py` path), and the parsed LLM probabilities are
   in a sensible range relative to the API call (within ~0.2 abs
   difference is OK; the point is not bit-equivalence — temperatures,
   model routing, and Max-plan model selection will differ).
2. **Latency tolerable** — wall-clock per-call < 30s, ideally < 10s.
   Above 30s makes `analyze` painful and rules out the simple
   subprocess transport without further engineering.
3. **Auth UX zero-friction** — `ClaudeCodeClient.run()` succeeds
   without reading `ANTHROPIC_API_KEY` and without an api_key
   constructor argument; only the user being logged into the
   `claude` CLI is required. (The baseline `AnthropicClient` run
   still needs the API key, of course — the spike requires both
   credentials present so the comparison is possible. The point is
   that the *Claude Code path itself* takes no key.)

The spike is a **failure** if any of the above breaks. Failure
modes inform the follow-up: parse failure → switch to
`claude-agent-sdk`; latency > 30s → same; auth issues → escalate.

## 4. Components

### 4.1 `src/wave_alpha/llm/claude_code_client.py` (new)

A `ClaudeCodeClient` dataclass implementing the `LLMClient` Protocol
(`model: str` attribute + `run(LLMRequest) -> str` method). It lives
under `llm/` rather than `scripts/` so the spike script can import
it as a normal module, and so that — if the spike succeeds — the
file is already in the natural place to evolve into production code.

`run()` invokes:

```
subprocess.run(
    ["claude", "-p", req.user, "--system-prompt", req.system,
     "--model", req.model, "--output-format", "text"],
    capture_output=True, text=True, timeout=60, check=True,
)
```

and returns `result.stdout`. Exact CLI flags are best-effort and
will be adjusted during the spike if the actual `claude` CLI uses
different names — verifying the flag surface is part of the spike.

The class does NOT inherit from anything; it just satisfies the
Protocol structurally, identical to how `CacheOnlyClient` does
today.

### 4.2 `scripts/spike_claude_code_compare.py` (new)

Orchestration script. Not registered in the CLI (`cli/app.py`
untouched), not under `src/`. Hardcoded constants at the top of the
file:

- `TICKER = "AAPL"`
- `AS_OF = date(2024, 12, 31)`
- `MODEL = "claude-haiku-4-5-20251001"` (matches `AnthropicClient` default)
- `OUT_PATH = Path("reports/claude_code_spike.json")`

Procedure:

1. Build the `LLMRequest` payload **once** by running the engine
   pipeline up to the candidate-set serialization step (reusing
   existing `llm/prompts.py` + `llm/candidates.py`).
2. Instantiate `AnthropicClient(api_key=os.environ["ANTHROPIC_API_KEY"], model=MODEL)`,
   call `.run(req)`, time it, capture stdout text.
3. Instantiate `ClaudeCodeClient(model=MODEL)`, call `.run(req)`,
   time it, capture stdout text. (No API key passed — the whole
   point is to prove this path is keyless.)
4. Try parsing both responses through the existing JSON parser;
   capture parsed result or parse error for each.
5. Write both runs to `OUT_PATH` as a single JSON object:
   `{"request": ..., "anthropic": {"raw": ..., "parsed": ..., "latency_s": ..., "error": ...},
    "claude_code": {...}}`.

Run with `uv run python scripts/spike_claude_code_compare.py`. The
script prints a one-line success/failure summary and the path to
the JSON artifact.

## 5. Data flow

```
spike script
  ├─ build LLMRequest once via prompts.py + candidates.py
  ├─ run #1: AnthropicClient.run(req)
  │   → raw_anthropic, t_anthropic, parsed_anthropic | parse_error
  ├─ run #2: ClaudeCodeClient.run(req)
  │   → raw_claude_code, t_claude_code, parsed_claude_code | parse_error
  └─ write reports/claude_code_spike.json (both runs side by side)
```

Cache is bypassed — both clients are instantiated directly, not
through `LLMRunner`'s cached path. We measure cold call latency for
both transports.

## 6. Error handling

Spike fails loud. Subprocess errors, timeouts, missing `claude`
binary, parse failures all surface as Python exceptions with the
captured stderr (if any) printed to console. No fallback. No retry.
No graceful degradation. Failure modes are the spike's primary
output.

## 7. Testing

One unit test, `tests/llm/test_claude_code_client.py`:

- Monkeypatch `subprocess.run` to return a fixed `CompletedProcess`
  with `stdout="canned response"`, `returncode=0`.
- Construct `ClaudeCodeClient(model="claude-haiku-4-5-20251001")`,
  call `.run(LLMRequest(model=..., system="sys", user="usr"))`.
- Assert the returned string is `"canned response"`.
- Assert the captured argv list passed to `subprocess.run` contains
  `"claude"`, `"-p"`, the user prompt, and the model id.

That's the entire test surface. The real validation of the spike is
running it.

## 8. Out of scope

Explicitly deferred to the post-spike full feature design:

- New `LLMProvider` enum / config key.
- CLI flag (`--llm-provider claude-code`) and `wave-alpha config`
  UX for selecting transport.
- Fallback chain semantics (Claude Code → API → cache → disabled).
- Cache key handling when model id differs between transports.
- Scan-mode behavior (multi-ticker latency, rate limiting against
  Max plan limits).
- Documentation in `README.md` / `CLAUDE.md`.
- Argv-exposure / privacy review of subprocess transport.

## 9. Decision after the spike

Read `reports/claude_code_spike.json`. Then one of three paths:

- **Pass** (all three success criteria met) → write a plan for the
  full feature using `claude_code_client.py` as the production
  client.
- **Soft fail** (functional + auth OK, latency 30–60s) → consider
  the full feature with `claude-agent-sdk` as the transport instead
  of subprocess; re-spike with that.
- **Hard fail** (parse drift, auth broken, or latency > 60s) →
  document findings, defer the feature, close the loop in the
  deferred memory note.

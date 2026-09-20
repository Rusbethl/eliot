from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import date
from pathlib import Path

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.stooq import StooqProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.llm.cache import LLMResponseCache
from wave_alpha.llm.client import AnthropicClient, CacheOnlyClient, LLMClient
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.runner import LLMRunner
from wave_alpha.pipeline import AnalyzeRequest, AnalyzeResult, run_analysis
from wave_alpha.prefs.config import load_config, resolve_api_key, resolve_stooq_api_key


def _data_dir() -> Path:
    """Indirection so tests can monkeypatch the cache location."""
    return Path.home() / ".wave_alpha"


def analyze(ticker: str, as_of: date, *, llm_mode: LLMMode = LLMMode.DISABLED) -> AnalyzeResult:
    """Memoized wrapper. Key = (ticker, as_of, llm_mode). maxsize=8 covers a
    handful of active deep-dive pages; tests must `cache_clear()` between runs.
    """
    return _memoized_analyze(ticker.upper(), as_of, llm_mode)


class _SingleflightAnalyzeCache:
    """Thread-safe single-flight LRU memoizer for `_run_analysis_uncached`.

    Why: the deepdive page fans out 6+ HTMX fragments in parallel, each
    calling `analyze` for the same key. `functools.lru_cache` only locks
    around the cache dict, not the underlying function — so concurrent
    misses each executed the full ~10s `run_analysis` independently,
    contending for the GIL and `cache.sqlite`. With single-flight, the
    first caller runs once; followers wait on a per-key Event and share
    the result.
    """

    def __init__(self, maxsize: int = 8) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._results: OrderedDict[tuple, AnalyzeResult] = OrderedDict()
        self._inflight: dict[tuple, tuple[threading.Event, dict]] = {}

    def __call__(self, ticker: str, as_of: date, llm_mode: LLMMode) -> AnalyzeResult:
        key = (ticker, as_of, llm_mode)
        with self._lock:
            if key in self._results:
                self._results.move_to_end(key)
                return self._results[key]
            inflight = self._inflight.get(key)
            if inflight is None:
                event = threading.Event()
                holder: dict = {}
                self._inflight[key] = (event, holder)
                leader = True
            else:
                event, holder = inflight
                leader = False

        if leader:
            try:
                holder["result"] = _run_analysis_uncached(ticker, as_of, llm_mode)
            except BaseException as exc:
                holder["error"] = exc
            finally:
                with self._lock:
                    if "result" in holder:
                        self._results[key] = holder["result"]
                        self._results.move_to_end(key)
                        while len(self._results) > self._maxsize:
                            self._results.popitem(last=False)
                    self._inflight.pop(key, None)
                event.set()
        else:
            event.wait()

        if "error" in holder:
            raise holder["error"]
        return holder["result"]

    def cache_clear(self) -> None:
        """Clear cached results. In-flight computations keep running; new
        callers for the same key will join the existing in-flight entry."""
        with self._lock:
            self._results.clear()


_memoized_analyze = _SingleflightAnalyzeCache(maxsize=8)


def _run_analysis_uncached(ticker: str, as_of: date, llm_mode: LLMMode) -> AnalyzeResult:
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    stooq_key = resolve_stooq_api_key(cfg)
    provider = CachedProvider(
        upstream=FallbackProvider([YahooProvider(), StooqProvider(api_key=stooq_key)]),
        db_path=data_dir / "cache.sqlite",
    )
    runner: LLMRunner | None = None
    if llm_mode is not LLMMode.DISABLED:
        api_key = resolve_api_key(cfg)
        client: LLMClient
        if api_key:
            client = AnthropicClient(api_key=api_key, model=cfg.llm.scan_model)
        else:
            # CACHED mode without a key: use CacheOnlyClient (paper-reproducible
            # runs). LIVE mode without a key would fail at the AnthropicClient
            # constructor — same as CLI behavior. We mirror that by silently
            # falling back to a CacheOnlyClient here, which surfaces a
            # CachedResponseMissError on first cache miss.
            client = CacheOnlyClient(model=cfg.llm.scan_model)
        cache = LLMResponseCache(db_path=data_dir / "llm_cache.sqlite")
        runner = LLMRunner(client=client, cache=cache, mode=llm_mode)
    req = AnalyzeRequest(ticker=ticker, as_of=as_of)
    return run_analysis(
        req,
        provider=provider,
        llm_mode=llm_mode,
        llm_runner=runner,
        alpha=cfg.llm.alpha,
        enabled_opt_in_templates=cfg.templates.enabled_opt_in_names(),
    )

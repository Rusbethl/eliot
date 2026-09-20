import threading
import time
from datetime import date
from unittest.mock import MagicMock, patch

from wave_alpha.llm.modes import LLMMode
from wave_alpha.web import deps


def test_analyze_memoized_dedupes_repeat_calls() -> None:
    deps._memoized_analyze.cache_clear()
    with patch.object(deps, "_run_analysis_uncached") as mock:
        mock.return_value = MagicMock()
        deps.analyze("AAPL", date(2024, 1, 5), llm_mode=LLMMode.DISABLED)
        deps.analyze("AAPL", date(2024, 1, 5), llm_mode=LLMMode.DISABLED)
        deps.analyze("AAPL", date(2024, 1, 5), llm_mode=LLMMode.DISABLED)
        assert mock.call_count == 1


def test_analyze_memoized_keys_on_ticker_as_of_mode() -> None:
    deps._memoized_analyze.cache_clear()
    with patch.object(deps, "_run_analysis_uncached") as mock:
        mock.return_value = MagicMock()
        deps.analyze("AAPL", date(2024, 1, 5), llm_mode=LLMMode.DISABLED)
        deps.analyze("MSFT", date(2024, 1, 5), llm_mode=LLMMode.DISABLED)
        deps.analyze("AAPL", date(2024, 1, 6), llm_mode=LLMMode.DISABLED)
        deps.analyze("AAPL", date(2024, 1, 5), llm_mode=LLMMode.LIVE)
        assert mock.call_count == 4


def test_analyze_cache_clear_works() -> None:
    deps._memoized_analyze.cache_clear()
    with patch.object(deps, "_run_analysis_uncached") as mock:
        mock.return_value = MagicMock()
        deps.analyze("AAPL", date(2024, 1, 5), llm_mode=LLMMode.DISABLED)
        deps._memoized_analyze.cache_clear()
        deps.analyze("AAPL", date(2024, 1, 5), llm_mode=LLMMode.DISABLED)
        assert mock.call_count == 2


def test_analyze_singleflights_concurrent_calls() -> None:
    # Regression: the deepdive page fans out 6+ HTMX fragments in parallel.
    # functools.lru_cache only locks around dict lookup, so concurrent calls
    # for the same key each executed run_analysis. The memoizer must
    # single-flight: one underlying call, followers wait and share the result.
    deps._memoized_analyze.cache_clear()
    started = threading.Event()
    proceed = threading.Event()

    def slow(*args, **kwargs):
        started.set()
        proceed.wait(timeout=2.0)
        return MagicMock()

    with patch.object(deps, "_run_analysis_uncached", side_effect=slow) as mock:
        threads = [
            threading.Thread(
                target=deps.analyze,
                args=("AAPL", date(2024, 1, 5)),
                kwargs={"llm_mode": LLMMode.DISABLED},
            )
            for _ in range(6)
        ]
        for t in threads:
            t.start()
        assert started.wait(timeout=2.0), "leader never entered _run_analysis_uncached"
        time.sleep(0.1)  # let the other 5 threads queue up on the in-flight entry
        proceed.set()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "follower deadlocked waiting on leader"

    assert mock.call_count == 1, (
        f"expected single-flight (1 call); got {mock.call_count} parallel executions"
    )

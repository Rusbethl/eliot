"""Pins the privacy contract documented in CLAUDE.md and the README:

The LLM payload may contain ticker symbol, pivot timestamps/prices, candidate
count structures, and coherence metrics. It MUST NOT contain raw OHLCV bars
or any form of the API key.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.llm.candidates import serialize_candidate_set
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _pivot(d: str, price: str, kind: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date.fromisoformat(d),
        price=Decimal(price),
        type=kind,
        state=PivotState.CONFIRMED,
    )


def _wave_count() -> WaveCount:
    return WaveCount(
        pattern="impulse",
        degree=2,
        score=0.82,
        pivots=[
            _pivot("2026-01-02", "100.00", PivotType.LOW),
            _pivot("2026-01-15", "115.00", PivotType.HIGH),
            _pivot("2026-02-01", "108.00", PivotType.LOW),
            _pivot("2026-02-20", "130.00", PivotType.HIGH),
            _pivot("2026-03-01", "122.00", PivotType.LOW),
            _pivot("2026-03-20", "150.00", PivotType.HIGH),
        ],
        soft_violations=[],
    )


# OHLCV field-name patterns — match as "key=" to avoid false-positives on
# legitimate pivot-type values ("type=high", "type=low") that appear in the
# serialized count structure.
_FORBIDDEN_OHLCV_FIELDS = (
    "open=",
    "high=",
    "low=",
    "close=",
    "volume=",
    "adj_close=",
    "adjclose=",
)

# Credential tokens — bare substrings are sufficient since these should never
# appear anywhere in the payload.
_FORBIDDEN_CREDENTIAL_TOKENS = (
    "anthropic",
    "api_key",
    "apikey",
    "sk-ant",
    "bearer",
    "authorization",
)


def test_serialize_candidate_set_has_no_forbidden_tokens():
    payload = serialize_candidate_set([_wave_count()])
    lower = payload.lower()
    for token in _FORBIDDEN_OHLCV_FIELDS:
        assert token not in lower, (
            f"forbidden OHLCV field {token!r} found in LLM candidate payload:\n{payload}"
        )
    for token in _FORBIDDEN_CREDENTIAL_TOKENS:
        assert token not in lower, (
            f"forbidden credential token {token!r} found in LLM candidate payload:\n{payload}"
        )


def test_serialize_candidate_set_contains_only_count_structure():
    payload = serialize_candidate_set([_wave_count()])
    # Sanity check positive content (so a future bug that empties the payload
    # doesn't pass the negative test trivially)
    assert "pattern=impulse" in payload
    assert "degree=2" in payload
    assert "100.00" in payload  # pivot price


@pytest.mark.parametrize("attr", ["api_key", "ANTHROPIC_API_KEY"])
def test_secrets_never_appear(monkeypatch, attr):
    monkeypatch.setenv(attr, "sk-ant-leak-canary-12345")
    payload = serialize_candidate_set([_wave_count()])
    assert "leak-canary" not in payload
    assert "sk-ant" not in payload.lower()


def test_serialize_candidate_set_lines_match_expected_shape():
    """Every non-empty line in the payload must match one of two known formats.

    This catches a class of leaks that the substring tests miss: any new
    line emitting unexpected keys (including OHLCV bars written without
    the `=` separator) will fail this regex check.
    """
    import re

    payload = serialize_candidate_set([_wave_count()])
    header_re = re.compile(r"^\[C\d+\] pattern=\w+ degree=\d+ score=-?\d+\.\d+$")
    pivot_re = re.compile(
        r"^  P\d+ ts=\d{4}-\d{2}-\d{2} price=-?\d+(?:\.\d+)? type=(?:high|low) state=(?:confirmed|tentative)$"
    )
    soft_re = re.compile(r"^  soft_violations=.*$")

    for line in payload.splitlines():
        if not line.strip():
            continue
        assert header_re.match(line) or pivot_re.match(line) or soft_re.match(line), (
            f"unexpected payload line shape: {line!r}"
        )

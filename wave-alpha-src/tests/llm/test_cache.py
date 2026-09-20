from datetime import datetime
from pathlib import Path

from wave_alpha.llm.cache import LLMResponseCache, prompt_hash


def test_prompt_hash_deterministic() -> None:
    h1 = prompt_hash("system", "user")
    h2 = prompt_hash("system", "user")
    assert h1 == h2
    assert h1 != prompt_hash("system", "user2")


def test_cache_roundtrip(tmp_path: Path) -> None:
    cache = LLMResponseCache(db_path=tmp_path / "llm.sqlite")
    key = ("claude-haiku-4-5-20251001", prompt_hash("S", "U"))
    assert cache.get(*key) is None
    cache.put(*key, response='{"ok": true}', created_at=datetime(2024, 1, 1))
    assert cache.get(*key) == '{"ok": true}'


def test_cache_distinguishes_models(tmp_path: Path) -> None:
    cache = LLMResponseCache(db_path=tmp_path / "llm.sqlite")
    h = prompt_hash("S", "U")
    cache.put("haiku", h, response="A", created_at=datetime(2024, 1, 1))
    cache.put("sonnet", h, response="B", created_at=datetime(2024, 1, 1))
    assert cache.get("haiku", h) == "A"
    assert cache.get("sonnet", h) == "B"


def test_cache_overwrite_replaces_existing(tmp_path: Path) -> None:
    cache = LLMResponseCache(db_path=tmp_path / "llm.sqlite")
    key = ("haiku", prompt_hash("S", "U"))
    cache.put(*key, response="A", created_at=datetime(2024, 1, 1))
    cache.put(*key, response="B", created_at=datetime(2024, 1, 2))
    assert cache.get(*key) == "B"

from __future__ import annotations

import ast
from collections.abc import Iterable
from functools import cached_property
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

# Templates that ship in the wheel but are NOT loaded by default at
# enumeration time. Users enable them via config (see prefs.models.
# TemplatesConfig). Adding to this set defers an opt-in template's
# default behaviour to "off" without removing it from the bundle.
OPT_IN_TEMPLATE_NAMES: frozenset[str] = frozenset({"leading_diagonal"})


class Constraint(BaseModel):
    # frozen=True makes Pydantic immutable for declared fields. Pydantic v2
    # permits @cached_property because the cache writes to __dict__, which
    # is separate from the validated field set. If a Pydantic upgrade ever
    # breaks this, the fallback is a module-level WeakValueDictionary keyed
    # by id(constraint) — semantically equivalent.
    model_config = ConfigDict(frozen=True)

    name: str
    severity: Literal["hard", "soft"]
    expr: str
    rationale: str = ""

    @cached_property
    def parsed(self) -> ast.Expression:
        """Parse the constraint expression once per Constraint instance.

        Used by ``validator.validate`` via ``dsl.evaluate_parsed`` to skip
        the per-call ``ast.parse`` cost. See spec §3.1.
        """
        return ast.parse(self.expr, mode="eval")


class PatternTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    direction_alternates: bool = True
    waves: list[str]
    constraints: list[Constraint]
    allowed_sub_patterns: dict[str, list[str]] = {}

    @classmethod
    def load_bundled(cls, name: str) -> PatternTemplate:
        path = _bundled_dir() / f"{name}.yaml"
        return cls.from_yaml_file(path)

    @classmethod
    def from_yaml_file(cls, path: Path) -> PatternTemplate:
        return cls.model_validate(yaml.safe_load(path.read_text()))


def _bundled_dir() -> Path:
    return Path(__file__).resolve().parent / "templates_data"


def load_bundled_templates() -> list[PatternTemplate]:
    """Return every template that ships in the wheel, including opt-in ones.

    Use this when callers want the complete bundled set (e.g., tests that
    need a specific opt-in template by name). For production enumeration
    use ``load_default_templates`` so opt-in templates are filtered by
    config.
    """
    return [PatternTemplate.from_yaml_file(p) for p in sorted(_bundled_dir().glob("*.yaml"))]


def load_default_templates(*, enabled_opt_in: Iterable[str] = ()) -> list[PatternTemplate]:
    """Return templates active by default for enumeration.

    Drops any template whose name is in ``OPT_IN_TEMPLATE_NAMES`` unless
    it appears in ``enabled_opt_in``. This is the function ``enumerate_counts``
    defaults to; production callers pass the names from
    ``AppConfig.templates.enabled_opt_in_names()``.
    """
    enabled = set(enabled_opt_in)
    return [
        t
        for t in load_bundled_templates()
        if t.name not in OPT_IN_TEMPLATE_NAMES or t.name in enabled
    ]

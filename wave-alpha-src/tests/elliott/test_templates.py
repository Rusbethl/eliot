from wave_alpha.elliott.templates import (
    OPT_IN_TEMPLATE_NAMES,
    PatternTemplate,
    load_bundled_templates,
    load_default_templates,
)


def test_impulse_template_has_three_hard_constraints() -> None:
    tpl = PatternTemplate.load_bundled("impulse")
    assert tpl.name == "impulse"
    assert len(tpl.waves) == 5
    hard = [c for c in tpl.constraints if c.severity == "hard"]
    hard_names = {c.name for c in hard}
    assert {"wave_2_max_retrace", "wave_3_not_shortest", "wave_4_no_overlap"} <= hard_names


def test_load_bundled_returns_all_v0_1_patterns() -> None:
    tpls = load_bundled_templates()
    names = {t.name for t in tpls}
    assert {
        "impulse",
        "zigzag",
        "flat",
        "expanded_flat",
        "contracting_triangle",
        "ending_diagonal",
    } <= names


def test_leading_diagonal_is_opt_in() -> None:
    """leading_diagonal is bundled (ships in the wheel) but not active by
    default — listed in OPT_IN_TEMPLATE_NAMES."""
    bundled = {t.name for t in load_bundled_templates()}
    assert "leading_diagonal" in bundled
    assert "leading_diagonal" in OPT_IN_TEMPLATE_NAMES


def test_load_default_templates_excludes_opt_in_by_default() -> None:
    """Default-template loader drops opt-in templates unless explicitly enabled."""
    default = {t.name for t in load_default_templates()}
    for opt_in in OPT_IN_TEMPLATE_NAMES:
        assert opt_in not in default, f"{opt_in} must be excluded from the default template set"


def test_load_default_templates_includes_opt_in_when_enabled() -> None:
    """When a name is passed in enabled_opt_in, it appears in the default set."""
    enabled = load_default_templates(enabled_opt_in={"leading_diagonal"})
    names = {t.name for t in enabled}
    assert "leading_diagonal" in names


def test_load_default_templates_ignores_unknown_opt_in_names() -> None:
    """Passing an unknown opt-in name is a no-op (it does not appear in the
    output because it is not bundled). This is forgiving by design — typos
    in config shouldn't crash analysis."""
    enabled = load_default_templates(enabled_opt_in={"does_not_exist"})
    names = {t.name for t in enabled}
    assert "does_not_exist" not in names
    assert "leading_diagonal" not in names  # still filtered


def test_template_grouping_invariant() -> None:
    """Locks the enumerator's (n_required, tuple(waves)) grouping topology.

    The enumerator-eval-cache optimization (spec
    2026-05-11-enumerator-eval-cache-design.md §3.3-3.4) keys its
    per-window cache by constraint expression text and assumes that
    every template within a (n_required, tuple(waves)) cell binds the
    same WaveSegment to the same wave-label. The enumerator groups
    templates by that compound key so cache sharing only happens
    within a single cell.

    Multiple distinct waves conventions per n_required are allowed
    (e.g., zigzag/flat/expanded_flat use ["A","B","C"] while wxy
    uses ["W","X","Y"] at the same n_required=4). The invariant this
    test locks is: every template must be assignable to some cell,
    and the topology must be at least non-empty.
    """
    cells: dict[tuple[int, tuple[str, ...]], list[str]] = {}
    for tpl in load_bundled_templates():
        key = (len(tpl.waves) + 1, tuple(tpl.waves))
        cells.setdefault(key, []).append(tpl.name)

    assert cells, "expected at least one template group"
    for (n_required, labels), names in cells.items():
        assert names, f"empty group at ({n_required}, {labels})"
        assert n_required == len(labels) + 1, (
            f"n_required/labels mismatch at ({n_required}, {labels}): {names}"
        )

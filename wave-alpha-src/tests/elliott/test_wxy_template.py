from wave_alpha.elliott.templates import PatternTemplate, load_bundled_templates


def test_wxy_template_loads_from_bundled_dir():
    tpl = PatternTemplate.load_bundled("wxy")
    assert tpl.name == "wxy"
    assert tpl.waves == ["W", "X", "Y"]
    assert tpl.direction_alternates is True


def test_wxy_template_appears_in_load_bundled_templates():
    names = {t.name for t in load_bundled_templates()}
    assert "wxy" in names


def test_wxy_template_has_expected_constraints():
    tpl = PatternTemplate.load_bundled("wxy")
    names = {c.name for c in tpl.constraints}
    assert names == {
        "x_shorter_than_w",
        "x_shorter_than_y",
        "y_at_least_0618_w",
        "y_at_most_1618_w",
        "x_max_retrace_of_w",
    }
    severities = {c.name: c.severity for c in tpl.constraints}
    assert severities["x_shorter_than_w"] == "hard"
    assert severities["x_shorter_than_y"] == "hard"
    assert severities["y_at_least_0618_w"] == "soft"
    assert severities["y_at_most_1618_w"] == "soft"
    assert severities["x_max_retrace_of_w"] == "soft"


def test_wxy_template_has_corrective_sub_pattern_allowlist():
    tpl = PatternTemplate.load_bundled("wxy")
    expected = {"zigzag", "flat", "expanded_flat", "contracting_triangle"}
    assert set(tpl.allowed_sub_patterns["W"]) == expected
    assert set(tpl.allowed_sub_patterns["X"]) == expected
    assert set(tpl.allowed_sub_patterns["Y"]) == expected

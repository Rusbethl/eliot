"""Structural tests for the popover() Jinja macro.

These verify the rendered HTML scaffolding without booting the full app —
the macro can be exercised against the Jinja environment directly.
"""

from __future__ import annotations

from importlib.resources import files

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _env() -> Environment:
    templates_dir = files("wave_alpha.web") / "templates"
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )


_RENDER_HOST = """\
{% from "_macros.html" import popover %}
{% call popover("How we got 1.18×", anchor="coherence") %}
  <p class="popover-narrative">Body content here.</p>
{% endcall %}
"""  # noqa: RUF001 — U+00D7 multiplication sign is the intended display character.


def test_popover_renders_trigger_button() -> None:
    out = _env().from_string(_RENDER_HOST).render()
    assert 'class="popover-trigger"' in out
    assert 'aria-label="Show details for How we got 1.18×"' in out  # noqa: RUF001
    assert "aria-expanded" in out
    assert 'type="button"' in out


def test_popover_renders_dialog_card() -> None:
    out = _env().from_string(_RENDER_HOST).render()
    assert 'class="popover-card"' in out
    assert 'role="dialog"' in out
    assert "Body content here." in out


def test_popover_renders_close_button() -> None:
    out = _env().from_string(_RENDER_HOST).render()
    assert 'class="popover-close"' in out
    assert 'aria-label="Close"' in out
    # The macro emits the HTML entity `&times;` rather than the literal U+00D7
    # character. With Jinja autoescape on, entities pass through verbatim to the
    # rendered output (they would be decoded by the browser, not the renderer).
    assert ">&times;<" in out


def test_popover_renders_learn_more_link_when_anchor_supplied() -> None:
    out = _env().from_string(_RENDER_HOST).render()
    assert 'href="/about#coherence"' in out
    assert "Learn more" in out


def test_popover_omits_learn_more_when_no_anchor() -> None:
    out = (
        _env()
        .from_string(
            '{% from "_macros.html" import popover %}'
            '{% call popover("Plain headline") %}body{% endcall %}'
        )
        .render()
    )
    assert "Learn more" not in out
    assert "/about#" not in out


def test_popover_alpine_directives_present() -> None:
    """Verify the click-toggle + click-outside + escape directives are wired."""
    out = _env().from_string(_RENDER_HOST).render()
    assert 'x-data="popover()"' in out
    assert 'x-on:click="open = !open"' in out
    assert 'x-on:click.outside="open = false"' in out
    assert 'x-on:keydown.escape.window="open = false"' in out
    assert 'x-show="open"' in out
    assert "x-cloak" in out


def test_popover_dialog_is_labelled_for_screen_readers() -> None:
    out = _env().from_string(_RENDER_HOST).render()
    assert 'aria-modal="true"' in out
    assert 'aria-labelledby="popover-head-' in out
    assert 'id="popover-head-' in out

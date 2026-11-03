"""Theme engine tests: palettes, contrast, CSS generation, and that switching
palette / mode in the real app never breaks the page. Presentation only --
no research, evidence or opportunity logic is exercised here."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import theme_engine as te

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"

REQUIRED_PALETTES = {
    "Executive Blue", "Otech Red", "Enterprise Emerald", "Technology Indigo", "Neutral Slate",
}
REQUIRED_TOKENS = {
    "bg", "surface", "surface_2", "border", "text", "text_secondary", "muted",
    "primary", "primary_hover", "on_primary", "primary_text", "primary_soft",
    "danger", "success", "warning", "info", "shadow",
}
TEXT_ROLES = ("text", "text_secondary", "muted", "primary_text", "danger", "success", "warning", "info")
SURFACES = ("bg", "surface", "surface_2")
AA = 4.5


def test_required_palettes_and_modes_exist():
    assert REQUIRED_PALETTES <= set(te.THEMES)
    assert te.DEFAULT_PALETTE in te.THEMES
    for name, modes in te.THEMES.items():
        assert set(modes) == {"light", "dark"}, name
        for mode, tokens in modes.items():
            assert REQUIRED_TOKENS <= set(tokens), (name, mode, REQUIRED_TOKENS - set(tokens))


@pytest.mark.parametrize("palette", sorted(te.THEMES))
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_every_text_role_meets_wcag_aa_on_every_surface(palette, mode):
    t = te.THEMES[palette][mode]
    for fg in TEXT_ROLES:
        for bg in SURFACES:
            ratio = te.contrast_ratio(t[fg], t[bg])
            assert ratio >= AA, f"{palette}/{mode}: {fg} on {bg} = {ratio:.2f}"
    for fill in ("primary", "primary_hover"):
        assert te.contrast_ratio(t["on_primary"], t[fill]) >= AA, (palette, mode, fill)
    for fg in ("text", "text_secondary", "muted", "primary_text"):
        assert te.contrast_ratio(t[fg], t["primary_soft"]) >= AA, (palette, mode, fg)


def test_light_mode_is_light_and_dark_mode_is_dark():
    for name, modes in te.THEMES.items():
        light, dark = modes["light"], modes["dark"]
        assert te._relative_luminance(light["bg"]) > 0.8, name
        assert te._relative_luminance(dark["bg"]) < 0.02, name
        assert te._relative_luminance(light["text"]) < 0.05, name
        assert te._relative_luminance(dark["text"]) > 0.8, name


def test_css_changes_with_palette_and_mode_and_has_no_unformatted_braces():
    seen = set()
    for palette in te.THEMES:
        for mode in te.MODES:
            css = te.build_theme_css(palette, mode)
            tokens = te.get_theme_tokens(palette, mode)
            assert f"--ar-bg: {tokens['bg']};" in css
            assert f"--ar-primary: {tokens['primary']};" in css
            assert "{{" not in css and "}}" not in css
            assert css.count("{") == css.count("}")
            seen.add(css)
    assert len(seen) == len(te.THEMES) * len(te.MODES)
    assert "color-scheme: dark" in te.build_theme_css("Executive Blue", "Dark")
    assert "color-scheme: light" in te.build_theme_css("Executive Blue", "Light")


def test_components_use_variables_not_hardcoded_colours():
    css = te._BASE_CSS
    for cls in (".ar-product-title", ".ar-signal-card", ".ar-evidence-card", ".ar-decision-hypothesis",
                ".ar-people-card", '[data-baseweb="tab"]', '[data-testid="stMetric"]'):
        assert cls in css, cls
    # No literal hex colours in the shared rules: every colour is a token.
    import re
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", css)


def test_stale_or_unknown_selection_falls_back_safely():
    class _St:
        session_state = {"ar_palette": "Otech", "ar_mode": "Sepia"}

    te.init_theme_state(_St)
    assert _St.session_state["ar_palette"] == te.DEFAULT_PALETTE
    assert _St.session_state["ar_mode"] == te.DEFAULT_MODE
    assert te.get_theme_tokens("No such palette", "dark") == te.THEMES[te.DEFAULT_PALETTE]["dark"]


def test_app_wires_theme_before_any_early_return_and_has_no_inline_style_blocks():
    text = APP.read_text(encoding="utf-8")
    ast.parse(text)  # app.py must be valid Python
    assert "<style>" not in text, "styling belongs in theme_engine.py"
    main_src = text[text.index("def main() -> None:"):]
    first_return = main_src.index("return")
    for call in ("init_theme_state(st)", "inject_theme(st)", "render_theme_controls(st)"):
        assert call in main_src[:first_return], call
    assert text.count("def main(") == 1
    assert text.count("from __future__ import annotations") == 1


def test_streamlit_config_is_valid_toml_without_bom_and_without_fixed_colours():
    cfg = ROOT / ".streamlit" / "config.toml"
    if not cfg.exists():
        pytest.skip("no project config.toml")
    raw = cfg.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    import tomllib
    data = tomllib.loads(raw.decode("utf-8"))
    theme = data.get("theme", {})
    for fixed in ("primaryColor", "backgroundColor", "secondaryBackgroundColor", "textColor"):
        assert fixed not in theme, fixed


# Captured at collection time, while the real package is loaded.
# tests/test_phase2_ui.py temporarily swaps a fake `streamlit` into
# sys.modules and later pops it; re-importing the package after that raises
# "DeltaGeneratorSingleton instance already exists". Restoring the original
# module object keeps these AppTest runs independent of test ordering.
import streamlit as _REAL_STREAMLIT  # noqa: E402


def _apptest():
    import sys

    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(APP), default_timeout=30)


def test_appearance_controls_render_in_sidebar():
    at = _apptest().run()
    assert not at.exception
    palette = [s for s in at.sidebar.selectbox if s.label == "Colour palette"]
    mode = [r for r in at.sidebar.radio if r.label == "Mode"]
    assert palette and mode
    assert set(palette[0].options) == set(te.THEMES)
    assert list(mode[0].options) == list(te.MODES)
    assert any("Appearance" in h.value for h in at.sidebar.subheader)


@pytest.mark.parametrize("palette", sorted(REQUIRED_PALETTES))
def test_switching_palette_and_mode_never_breaks_the_page(palette):
    at = _apptest().run()
    for mode in ("Dark", "Light", "Dark"):
        [s for s in at.sidebar.selectbox if s.label == "Colour palette"][0].set_value(palette)
        [r for r in at.sidebar.radio if r.label == "Mode"][0].set_value(mode)
        at.run()
        assert not at.exception, (palette, mode, at.exception)
        assert at.session_state["ar_palette"] == palette
        assert at.session_state["ar_mode"] == mode
        css = "\n".join(m.value for m in at.markdown if "<style>" in m.value)
        tokens = te.get_theme_tokens(palette, mode)
        assert f"--ar-bg: {tokens['bg']};" in css
        assert f"--ar-text: {tokens['text']};" in css

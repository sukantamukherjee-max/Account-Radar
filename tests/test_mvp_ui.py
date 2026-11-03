"""MVP UI regression tests.

1. The local / uploaded report loading UI is intentionally NOT part of the
   MVP. The only workflow is Organization -> Country -> Analyze account.
   These tests stop those controls from quietly reappearing.
2. The executive dashboard only shows values taken from the loaded report
   (never hardcoded example numbers), and every tab still renders.

Presentation-only: no research, evidence or opportunity logic is exercised.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"

# Captured at collection time while the real package is loaded (see the
# matching note in tests/test_theme_engine.py about test_phase2_ui.py
# swapping a fake streamlit into sys.modules).
import streamlit as _REAL_STREAMLIT  # noqa: E402

REMOVED_REPORT_UI = (
    "file_uploader",
    "Load local",
    "phase1d_report.json",
    "Load uploaded report",
    "Load existing report",
    "Existing report",
    "JSON report",
    "REPORT_PATH",
    "_load_report_file",
)


def _apptest():
    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(APP), default_timeout=30)


# --- 1. Local-report loading is not exposed in the MVP ----------------------

@pytest.mark.parametrize("fragment", REMOVED_REPORT_UI)
def test_local_report_loading_ui_is_not_in_app_source(fragment):
    assert fragment not in APP.read_text(encoding="utf-8"), fragment


def test_sidebar_contains_only_account_analysis_and_appearance_controls():
    at = _apptest().run()
    assert not at.exception
    assert [b.label for b in at.sidebar.button] == ["Analyze account"]
    assert [t.label for t in at.sidebar.text_input] == ["Organization", "Country (optional)"]
    assert [s.label for s in at.sidebar.selectbox] == ["Colour palette"]
    assert [r.label for r in at.sidebar.radio] == ["Navigation", "Mode"]
    assert len(at.get("file_uploader")) == 0


def test_app_starts_clean_without_any_account_loaded():
    at = _apptest().run()
    assert not at.exception
    assert "entry" not in at.session_state
    rendered = " ".join(m.value for m in at.markdown)
    assert "No account loaded" in rendered
    assert "phase1d" not in rendered and "JSON report" not in rendered
    # Navigation is unavailable until an account has been analysed.
    nav = [r for r in at.sidebar.radio if r.label == "Navigation"][0]
    assert nav.disabled


def test_sidebar_is_the_single_place_to_start_analysis():
    text = APP.read_text(encoding="utf-8")
    # One helper calls the unchanged live-research function exactly once,
    # and only the sidebar calls that helper.
    helper = text[text.index("def _analyze_account("):text.index("def _render_sidebar_nav(")]
    assert helper.count("_run_live(") == 1
    assert text.count("_analyze_account(") == 2  # def + sidebar
    assert text.count("_run_live(") == 2  # def + the helper
    # The main-area duplicate search form is gone.
    landing = text[text.index("def _render_landing("):text.index("def _render_evidence_legend(")]
    for widget in ("text_input", "button", "st.form", "_analyze_account", "_run_live"):
        assert widget not in landing, widget
    for key in ("landing_org", "landing_country", "ar_landing_search", "Research an account"):
        assert key not in text, key


def test_empty_state_has_start_card_and_no_empty_signal_bars():
    at = _apptest().run()
    assert not at.exception
    # Main area: no account-entry controls at all.
    assert len(at.main.text_input) == 0
    assert len(at.main.button) == 0
    html = " ".join(m.value for m in at.markdown)
    assert "Start with an account" in html
    assert "Enter an organization in the sidebar to begin research." in html
    assert "No signals yet" in html
    flow = [m for m in ("Public evidence", "Signals", "Capabilities", "Opportunities", "Actions")]
    assert all(f'<span class="ar-start-step">{stage}</span>' in html for stage in flow)
    assert ("Analyze an account to discover expansion, investment, partnership, "
            "technology and procurement signals.") in html
    assert 'class="ar-dist-row' not in html  # no placeholder progress bars
    assert "&ndash;" in html  # KPI cards still render, empty


# --- 2. Dashboard shows only real report values ------------------------------

def _entry(**report_overrides):
    report = {
        "organization_name": "Meridian Ports Authority",
        "country": "Nordholm",
        "website": "https://www.meridian-ports.example",
        "status": "qualified",
        "qualification": {"qualified_count": 41, "disqualified_count": 7, "corpus_reason": ""},
        "signals": [
            {"signal_type": "technology_initiative", "text": "Deployed a new terminal operating system.",
             "source_url": "https://news.example/a", "source_title": "Port news", "matched_terms": ["deploy"]},
            {"signal_type": "expansion", "text": "Opened a new container berth.",
             "source_url": "https://news.example/b", "source_title": "Trade daily", "matched_terms": ["new"]},
        ],
        "capability_summary": {},
        "chain": [],
        "opportunity_hypotheses": [],
        "incumbent_evidence": [],
        "acquisition_diagnostics": {"total_queries": 23, "elapsed_seconds": 31.2},
        "people": [],
    }
    report.update(report_overrides)
    return {"report": report, "ai_synthesis": {"status": "ok"}, "opportunity_hypotheses": []}


def test_kpi_cards_use_values_from_the_loaded_report():
    at = _apptest()
    at.session_state["entry"] = _entry()
    at.session_state["loaded_from"] = "live research"
    at.run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    for label in ("Qualified sources", "Signals", "Capability mappings", "Opportunities",
                  "Research queries", "Disqualified sources"):
        assert label in html, label
    assert '<span class="ar-num">41</span>' in html   # qualified_count
    assert '<span class="ar-num">2</span>' in html    # len(signals)
    assert '<span class="ar-num">23</span>' in html   # total_queries (previously always shown as 0)
    assert '<span class="ar-num">7</span>' in html    # disqualified_count
    assert "Meridian Ports Authority" in html
    # Loaded state restores the real signal landscape bars, and the empty
    # states are gone.
    assert html.count('class="ar-dist-row') == 2  # technology_initiative + expansion
    assert "Start with an account" not in html and "No signals yet" not in html
    assert [t.label for t in at.tabs] == ["WATCH", "UNDERSTAND", "DECIDE", "ACT", "RESEARCH", "PEOPLE"]


def test_report_text_is_html_escaped_in_custom_markup():
    at = _apptest()
    at.session_state["entry"] = _entry(organization_name="<script>alert(1)</script> Corp")
    at.session_state["loaded_from"] = "live research"
    at.run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_no_style_blocks_or_duplicate_entry_points_in_app():
    text = APP.read_text(encoding="utf-8")
    assert "<style>" not in text
    assert text.count("def main(") == 1
    assert text.count("def _render_sidebar(") == 1
    assert text.count('if __name__ == "__main__":') == 1
    assert text.rstrip().endswith("main()")


def test_executive_blue_light_is_the_default_and_all_palettes_remain():
    import theme_engine as te
    assert te.DEFAULT_PALETTE == "Executive Blue"
    assert te.DEFAULT_MODE == "Light"
    assert {"Executive Blue", "Otech Red", "Enterprise Emerald", "Technology Indigo",
            "Neutral Slate"} <= set(te.THEMES)
    at = _apptest().run()
    assert not at.exception
    assert at.session_state["ar_palette"] == "Executive Blue"
    assert at.session_state["ar_mode"] == "Light"

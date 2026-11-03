"""Interactive dashboard: PEOPLE filter controls, card selection and top KPI
navigation. UX only -- People Intelligence data is used as-is.

Every interactive element tested here must change the displayed state or
open an existing section.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import streamlit as _REAL_STREAMLIT  # noqa: E402  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
ORG = "Meridian Ports Authority"


def _person(pid, name, *, title="", seniority=None, functions=(), tech=(), influence=None, assoc="confirmed"):
    chips = [{"category": "function", "label": f.replace("_", " ").title(), "evidence": "e"} for f in functions]
    chips += [{"category": "technology", "label": t.replace("_", " ").title(), "evidence": "e"} for t in tech]
    if influence:
        chips.append({"category": "influence", "label": influence.replace("_", " ").title(), "evidence": "e"})
    return {
        "person_id": pid, "name": name, "current_title": title, "organization_name": ORG,
        "organization_association": assoc, "linkedin_url": f"https://www.linkedin.com/in/{pid}",
        "source_urls": [f"https://www.linkedin.com/in/{pid}"], "source_types": ["linkedin_profile"],
        "location": None, "seniority": seniority, "functions": list(functions), "technology_areas": list(tech),
        "influence_type": influence, "relevance_score": 50, "confidence": 70.0, "evidence": [],
        "recent_signals": [], "chips": chips, "classification_text": f"{name} - {title}",
    }


PEOPLE = [
    _person("cio", "Ana Chief", title="Chief Information Officer", seniority="c_level", functions=["it"],
            tech=["cloud"], influence="decision_maker"),
    _person("ciso", "Ben Secure", title="Head of Cybersecurity", seniority="head", functions=["cybersecurity"],
            tech=["cybersecurity"], influence="technical_stakeholder"),
    _person("infra", "Cara Infra", title="Infrastructure Manager", seniority="manager", functions=["infrastructure"],
            tech=["data_center", "network"], influence="technical_stakeholder"),
    _person("data", "Dan Data", title="Data Lead", seniority="manager", functions=["data"], tech=["ai"],
            influence="technical_stakeholder"),
    _person("buyer", "Eve Buyer", title="Procurement Manager", seniority="manager", functions=["procurement"],
            influence="procurement_stakeholder"),
    _person("buyer2", "Fay Contracts", title="Procurement Director", seniority="director", functions=["procurement"],
            influence="procurement_stakeholder"),
    _person("hr", "Gus People", title="HR Officer", seniority="manager", functions=[], influence=None),
]


def _entry():
    report = {
        "organization_name": ORG, "country": "Testland", "status": "qualified", "chain": [], "signals": [],
        "capability_summary": {}, "incumbent_evidence": [], "acquisition_diagnostics": {"total_queries": 9},
        "qualification": {
            "qualified_count": 12, "disqualified_count": 2, "corpus_reason": "",
            "disqualified_reasons": [
                {"url": "https://thin.example/a", "reason": "Too little usable text (42 chars)"},
                {"url": "https://blocked.example/b", "reason": "Page could not be fetched"},
            ],
        },
        "people": PEOPLE,
    }
    return {"report": report, "ai_synthesis": {}, "opportunity_hypotheses": []}


def _apptest():
    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state["entry"] = _entry()
    at.session_state["loaded_from"] = "test"
    return at


def _app_module():
    sys.modules["streamlit"] = _REAL_STREAMLIT
    import importlib

    import app
    return importlib.reload(app)


def _showing(at) -> tuple[int, int]:
    for c in at.caption:
        m = re.search(r"Showing (\d+) of (\d+) relevant stakeholders", c.value)
        if m:
            return int(m.group(1)), int(m.group(2))
    raise AssertionError("no 'Showing X of Y' caption")


def _names_shown(at) -> set[str]:
    names = set()
    for m in at.markdown:
        names.update(re.findall(r'<div class="ar-person-name">([^<]+)</div>', m.value))
    return names


def _slug(text):
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_")


# ---------------------------------------------------------------------------
# 1. Each summary card is a real filter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", ["Technology Leadership", "Cybersecurity", "Infrastructure / Cloud",
                                   "Digital / Data", "Procurement", "Other"])
def test_each_people_summary_card_filters_the_list(label):
    app = _app_module()
    expected = app._people_bucket_members(PEOPLE)[label]
    assert app._people_summary_buckets(PEOPLE)[label] == len(expected) > 0

    at = _apptest()
    at.run()
    assert _showing(at) == (len(PEOPLE), len(PEOPLE))
    at.button(key=f"people_bucket_{_slug(label)}").click().run()
    assert not at.exception
    assert at.session_state["people_bucket"] == label
    assert _showing(at) == (len(expected), len(PEOPLE))
    shown = _names_shown(at)
    assert shown == {p["name"] for p in PEOPLE if p["person_id"] in expected}
    # active state is visible on the card and in the active-filter bar
    html = " ".join(m.value for m in at.markdown)
    assert 'ar-cat ' in html and " is-active" in html and f"Group: {label}" in html
    # clicking the active card again removes the filter
    at.button(key=f"people_bucket_{_slug(label)}").click().run()
    assert at.session_state["people_bucket"] is None and _showing(at) == (len(PEOPLE), len(PEOPLE))


def test_bucket_counts_are_unchanged_from_people_intelligence_fields():
    app = _app_module()
    assert app._people_summary_buckets(PEOPLE) == {
        "Technology Leadership": 2, "Cybersecurity": 1, "Infrastructure / Cloud": 2,
        "Digital / Data": 1, "Procurement": 2, "Other": 1,
    }
    # People Intelligence v2: Technology Leadership requires leadership
    # seniority AND technology relevance -- the Procurement Director is in
    # Procurement, not Technology Leadership.
    members = app._people_bucket_members(PEOPLE)
    assert members["Technology Leadership"] == {"cio", "ciso"}
    assert "buyer2" in members["Procurement"] and "buyer2" not in members["Technology Leadership"]


# ---------------------------------------------------------------------------
# 2. Cumulative filters
# ---------------------------------------------------------------------------

def test_filters_are_cumulative():
    at = _apptest()
    at.run()
    at.button(key="people_bucket_technology_leadership").click().run()
    assert _showing(at) == (2, 7)
    at.selectbox(key="people_influence_filter").set_value("technical_stakeholder").run()
    assert _showing(at) == (1, 7) and _names_shown(at) == {"Ben Secure"}
    at.selectbox(key="people_seniority_filter").set_value("c_level").run()
    assert _showing(at) == (0, 7)
    assert any("No stakeholders match the current filters." in i.value for i in at.info)


def test_filter_function_matches_pure_filter_logic():
    app = _app_module()
    result = app._filter_people(PEOPLE, bucket="Procurement", function="procurement", seniority="director")
    assert [p["person_id"] for p in result] == ["buyer2"]
    assert app._filter_people(PEOPLE) == PEOPLE


# ---------------------------------------------------------------------------
# 3. Clearing filters
# ---------------------------------------------------------------------------

def test_clear_filters_resets_every_filter():
    at = _apptest()
    at.run()
    assert not any(b.key == "people_clear_filters" for b in at.button), "no Clear control without active filters"
    at.button(key="people_bucket_procurement").click().run()
    at.text_input(key="people_search").input("eve").run()
    at.selectbox(key="people_seniority_filter").set_value("manager").run()
    assert _showing(at) == (1, 7)
    at.button(key="people_clear_filters").click().run()
    assert not at.exception
    assert _showing(at) == (7, 7)
    assert at.session_state["people_bucket"] is None and at.session_state["people_search"] == ""
    assert at.session_state["people_seniority_filter"] == "All"
    assert not any(b.key == "people_clear_filters" for b in at.button)


# ---------------------------------------------------------------------------
# 4. Search + filter combination
# ---------------------------------------------------------------------------

def test_search_combines_with_card_and_dropdown_filters():
    at = _apptest()
    at.run()
    at.text_input(key="people_search").input("procurement").run()
    assert _showing(at) == (2, 7)
    at.button(key="people_bucket_procurement").click().run()
    assert _showing(at) == (2, 7)
    at.selectbox(key="people_seniority_filter").set_value("director").run()
    assert _showing(at) == (1, 7) and _names_shown(at) == {"Fay Contracts"}
    at.selectbox(key="people_function_filter").set_value("procurement").run()
    assert _showing(at) == (1, 7)
    html = " ".join(m.value for m in at.markdown)
    assert "Search: procurement" in html and "Group: Procurement" in html and "Function: procurement" in html


def test_card_chips_are_filters_for_their_dimension():
    at = _apptest()
    at.run()
    at.button(key="people_chip_infra_technology_data_center").click().run()
    assert at.session_state["people_tech_filter"] == "data_center"
    assert _showing(at) == (1, 7) and _names_shown(at) == {"Cara Infra"}
    at.button(key="people_chip_infra_technology_data_center").click().run()   # toggle off
    assert at.session_state["people_tech_filter"] == "All" and _showing(at) == (7, 7)
    at.button(key="people_chip_buyer_influence_procurement_stakeholder").click().run()
    assert at.session_state["people_influence_filter"] == "procurement_stakeholder" and _showing(at) == (2, 7)
    # chips on the remaining (visible) cards keep narrowing cumulatively
    assert "people_chip_ciso_function_cybersecurity" not in [b.key for b in at.button]   # filtered-out card
    at.button(key="people_chip_buyer_function_procurement").click().run()
    assert at.session_state["people_function_filter"] == "procurement" and _showing(at) == (2, 7)
    at.selectbox(key="people_seniority_filter").set_value("director").run()
    assert _showing(at) == (1, 7) and _names_shown(at) == {"Fay Contracts"}


def test_only_real_filter_dimensions_are_clickable_chips():
    at = _apptest()
    at.run()
    chip_keys = [b.key for b in at.button if b.key and b.key.startswith("people_chip_")]
    assert chip_keys and all(re.match(r"people_chip_[a-z0-9_]+_(function|technology|influence)_", k) for k in chip_keys)


# ---------------------------------------------------------------------------
# 5. Stakeholder selection
# ---------------------------------------------------------------------------

def test_selecting_a_stakeholder_expands_intelligence_and_keeps_filters():
    at = _apptest()
    at.run()
    assert not any("Account Radar thinks" in m.value for m in at.markdown), "cards start collapsed"
    at.button(key="people_bucket_procurement").click().run()
    at.button(key="people_select_buyer2").click().run()
    assert not at.exception
    assert at.session_state["people_selected"] == "buyer2"
    assert at.session_state["people_bucket"] == "Procurement" and _showing(at) == (2, 7)
    html = " ".join(m.value for m in at.markdown)
    assert "Account Radar thinks" in html and "Fay Contracts - Procurement Director" in html
    assert any(b.key == "people_select_buyer2" and b.label == "Hide intelligence" for b in at.button)
    at.button(key="people_select_buyer2").click().run()
    assert at.session_state["people_selected"] is None
    assert not any("Account Radar thinks" in m.value for m in at.markdown)


def test_selection_survives_filter_changes():
    at = _apptest()
    at.run()
    at.button(key="people_select_cio").click().run()
    at.selectbox(key="people_tech_filter").set_value("cloud").run()
    assert at.session_state["people_selected"] == "cio" and _showing(at) == (1, 7)
    assert any("Account Radar thinks" in m.value for m in at.markdown)


# ---------------------------------------------------------------------------
# 6. Top KPI navigation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("card,nav_item,research_view", [
    ("qualified_sources", "Research queries", "Qualified intelligence"),
    ("signals", "Signals", None),
    ("capability_mappings", "Capability mapping", None),
    ("opportunities", "Opportunities", None),
    ("research_queries", "Research queries", None),
    ("disqualified_sources", "Research queries", "Disqualified sources"),
])
def test_top_kpi_cards_open_existing_destinations(card, nav_item, research_view):
    app = _app_module()
    at = _apptest()
    at.run()
    seq = at.session_state["ar_nav_seq"] if "ar_nav_seq" in at.session_state else 0
    at.button(key=f"kpi_nav_{card}").click().run()
    assert not at.exception
    assert at.session_state["ar_nav"] == nav_item
    assert nav_item in app.NAV_ITEMS                       # an existing destination, not a new one
    assert at.session_state["ar_nav_seq"] == seq + 1       # forces the target tab open even if already selected
    if research_view:
        assert at.session_state["research_source_view"] == research_view


def test_disqualified_kpi_shows_the_disqualified_sources():
    at = _apptest()
    at.run()
    at.button(key="kpi_nav_disqualified_sources").click().run()
    panels = [m.value for m in at.markdown if 'class="ar-panel ar-disq"' in m.value]
    assert len(panels) == 2
    assert "Too little usable text (42 chars)" in panels[0] and "Page could not be fetched" in panels[1]
    assert any("2 source(s) were excluded" in c.value for c in at.caption)
    at.button(key="kpi_nav_qualified_sources").click().run()
    assert not any('class="ar-panel ar-disq"' in m.value for m in at.markdown)


def test_kpi_cards_keep_their_values_and_are_static_on_the_empty_state():
    at = _apptest()
    at.run()
    html = " ".join(m.value for m in at.markdown)
    for value in ("12", "9", "2"):
        assert f'<span class="ar-num">{value}</span>' in html
    empty = sys.modules["streamlit"]
    from streamlit.testing.v1 import AppTest
    blank = AppTest.from_file(str(APP), default_timeout=30)
    blank.run()
    assert not any((b.key or "").startswith("kpi_nav_") for b in blank.button), "no fake controls without an account"
    assert empty is _REAL_STREAMLIT


def test_interactive_cards_are_styled_in_the_theme_engine_only():
    theme = (ROOT / "theme_engine.py").read_text(encoding="utf-8")
    for selector in ("st-key-ar_kpi_grid", "st-key-kpi_nav_", "st-key-people_bucket_", ".ar-cat.is-active",
                     "st-key-ar_person_selected_"):
        assert selector in theme, selector
    assert "<style>" not in APP.read_text(encoding="utf-8")

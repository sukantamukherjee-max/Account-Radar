"""Phase C tests: People tab UI logic in app.py, tested against the REAL
app.py functions (not parallel copies), plus a small number of genuine
Streamlit-rendering checks via streamlit.testing.v1.AppTest where that's
practical without a live account/session.

Import note: app.py's module-level imports include five modules not
present in this environment (ui.data, ai.client, ai.synthesis,
research.providers.openrouter_search, research.providers.serper). No
stand-ins are created for them here or anywhere else -- this file will
collect and run correctly wherever those five modules genuinely exist
(i.e. the real repository), and is not expected to collect without them.
"""

from __future__ import annotations

from pathlib import Path
import app


# ---------------------------------------------------------------------------
# Fixture data -- plain dicts matching commercial.people.Person.to_dict()'s
# exact shape, since that is what report.get("people", []) actually
# contains once serialized through AccountRadarReport.to_dict().
# ---------------------------------------------------------------------------

def _person(**overrides) -> dict:
    base = {
        "person_id": "jane-doe-123",
        "name": "Jane Doe",
        "current_title": "Jane Doe - Chief Information Officer - Meridian Ports Authority | LinkedIn",
        "organization_name": "Meridian Ports Authority",
        "organization_association": "confirmed",
        "linkedin_url": "https://www.linkedin.com/in/jane-doe-123",
        "source_urls": ["https://www.linkedin.com/in/jane-doe-123"],
        "source_types": ["linkedin_profile"],
        "location": None,
        "seniority": "c_level",
        "functions": ["it", "cybersecurity"],
        "technology_areas": ["cloud"],
        "influence_type": "decision_maker",
        "relevance_score": 82.0,
        "confidence": 90.0,
        "evidence": ["Chief Information Officer"],
        "recent_signals": [],
        "chips": [
            {"category": "role", "label": "CIO", "evidence": "matched role term in title/snippet"},
            {"category": "function", "label": "IT", "evidence": "matched function term in title/snippet"},
            {"category": "technology", "label": "Cloud", "evidence": "matched technology term in title/snippet"},
            {"category": "influence", "label": "Decision Maker", "evidence": "derived from seniority + function/technology evidence"},
        ],
        "score_factors": {"seniority": 25, "function_relevance": 10, "technology_relevance": 5},
        "discovery_dimensions": ["technology_digital_infrastructure"],
        "classification_text": "Jane Doe - Chief Information Officer - Meridian Ports Authority Chief Information Officer",
        "classification_matched_terms": ["chief information officer"],
        "organization_association_evidence": ["organization name 'Meridian Ports Authority' appears in profile title/snippet"],
        "discovered_at": None,
        "last_verified_at": None,
    }
    base.update(overrides)
    return base


def _procurement_person() -> dict:
    return _person(
        person_id="john-smith-99",
        name="John Smith",
        current_title="John Smith - Procurement Director - Meridian Ports Authority | LinkedIn",
        seniority="director",
        functions=["procurement"],
        technology_areas=[],
        influence_type="procurement_stakeholder",
        chips=[
            {"category": "role", "label": "Procurement Director", "evidence": "matched role term in title/snippet"},
            {"category": "function", "label": "Procurement", "evidence": "matched function term in title/snippet"},
            {"category": "influence", "label": "Procurement Stakeholder", "evidence": "derived from seniority + function/technology evidence"},
        ],
    )


# ---------------------------------------------------------------------------
# 1. Summary buckets -- derived from actual people, never hardcoded
# ---------------------------------------------------------------------------

def test_summary_buckets_reflect_actual_people_not_hardcoded():
    people = [_person(), _procurement_person()]
    buckets = app._people_summary_buckets(people)
    # People Intelligence v2: only the technology C-level counts; the
    # Procurement Director is Procurement, not Technology Leadership.
    assert buckets["Technology Leadership"] == 1
    assert buckets["Cybersecurity"] == 1
    assert buckets["Infrastructure / Cloud"] == 1
    assert buckets["Procurement"] == 1
    assert buckets["Other"] == 0


def test_summary_buckets_other_for_unclassified_person():
    other_person = _person(
        person_id="alex-roe",
        name="Alex Roe",
        seniority=None,
        functions=[],
        technology_areas=[],
        influence_type="business_stakeholder",
        chips=[{"category": "influence", "label": "Business Stakeholder", "evidence": "x"}],
    )
    buckets = app._people_summary_buckets([other_person])
    assert buckets["Other"] == 1
    assert buckets["Technology Leadership"] == 0


# ---------------------------------------------------------------------------
# 2. Filters -- pure local filtering, no research triggered
# ---------------------------------------------------------------------------

def test_filter_by_search_matches_name_or_title():
    people = [_person(), _procurement_person()]
    assert [p["person_id"] for p in app._filter_people(people, search="jane")] == ["jane-doe-123"]
    assert [p["person_id"] for p in app._filter_people(people, search="procurement director")] == ["john-smith-99"]


def test_filter_by_function_seniority_influence_technology():
    people = [_person(), _procurement_person()]
    assert [p["person_id"] for p in app._filter_people(people, function="procurement")] == ["john-smith-99"]
    assert [p["person_id"] for p in app._filter_people(people, seniority="c_level")] == ["jane-doe-123"]
    assert [p["person_id"] for p in app._filter_people(people, influence="decision_maker")] == ["jane-doe-123"]
    assert [p["person_id"] for p in app._filter_people(people, technology="cloud")] == ["jane-doe-123"]


def test_filter_with_no_criteria_returns_everyone():
    people = [_person(), _procurement_person()]
    assert app._filter_people(people) == people


# ---------------------------------------------------------------------------
# 3. Chips displayed must match Person.chips -- i.e. the UI must not
# generate its own chip content, only read what's already there.
# ---------------------------------------------------------------------------

def test_why_this_person_matters_never_invents_a_role_not_in_chips():
    person = _person(chips=[{"category": "influence", "label": "Potential Decision Maker", "evidence": "x"}])
    explanation = app._why_this_person_matters(person)
    assert "CIO" not in explanation  # no role chip present -- must not fabricate one
    assert "potential decision maker" in explanation.lower()


def test_why_this_person_matters_reflects_actual_role_chip():
    explanation = app._why_this_person_matters(_person())
    assert "CIO" in explanation
    assert "cybersecurity" in explanation.lower() or "it" in explanation.lower()


def test_why_this_person_matters_has_a_safe_fallback_for_thin_evidence():
    thin_person = _person(seniority=None, functions=[], technology_areas=[], chips=[], influence_type=None)
    explanation = app._why_this_person_matters(thin_person)
    assert explanation  # never empty
    assert "limited additional classification evidence" in explanation.lower()


# ---------------------------------------------------------------------------
# 4/6. Genuine Streamlit rendering via AppTest -- People tab renders with
# populated people, with an empty state, and existing tabs are unaffected.
# ---------------------------------------------------------------------------

def _make_report(people: list[dict]) -> dict:
    return {
        "organization_name": "Meridian Ports Authority",
        "country": "Nordholm",
        "status": "qualified",
        "qualification": {"qualified_count": 3, "disqualified_count": 0, "corpus_reason": ""},
        "signals": [],
        "capability_summary": {},
        "chain": [],
        "opportunity_hypotheses": [],
        "incumbent_evidence": [],
        "acquisition_diagnostics": {},
        "people": people,
    }


def test_app_test_people_tab_renders_with_populated_people():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30)
    at.session_state["entry"] = {
        "report": _make_report([_person(), _procurement_person()]),
        "ai_synthesis": {},
        "opportunity_hypotheses": [],
    }
    at.session_state["loaded_from"] = "test"
    at.run()
    assert not at.exception
    full_text = " ".join(md.value for md in at.markdown) + " ".join(t.value for t in at.tabs[0].markdown) if at.tabs else ""
    # The PEOPLE tab itself exists among the rendered tabs.
    tab_labels = [t.label for t in at.tabs]
    assert "PEOPLE" in tab_labels
    people_tab = at.tabs[tab_labels.index("PEOPLE")]
    rendered = " ".join(md.value for md in people_tab.markdown) + " ".join(w.value for w in people_tab.write) if hasattr(people_tab, "write") else " ".join(md.value for md in people_tab.markdown)
    assert "Jane Doe" in rendered
    assert "John Smith" in rendered


def test_app_test_people_tab_empty_state():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30)
    at.session_state["entry"] = {"report": _make_report([]), "ai_synthesis": {}, "opportunity_hypotheses": []}
    at.session_state["loaded_from"] = "test"
    at.run()
    assert not at.exception
    tab_labels = [t.label for t in at.tabs]
    people_tab = at.tabs[tab_labels.index("PEOPLE")]
    info_texts = [i.value for i in people_tab.info]
    assert any("No stakeholder profiles were discovered for this account" in t for t in info_texts)


def test_app_test_existing_tabs_still_present_and_unaffected():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30)
    at.session_state["entry"] = {"report": _make_report([_person()]), "ai_synthesis": {}, "opportunity_hypotheses": []}
    at.session_state["loaded_from"] = "test"
    at.run()
    assert not at.exception
    tab_labels = [t.label for t in at.tabs]
    assert tab_labels == ["WATCH", "UNDERSTAND", "DECIDE", "ACT", "RESEARCH", "PEOPLE"]

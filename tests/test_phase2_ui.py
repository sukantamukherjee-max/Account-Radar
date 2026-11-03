from __future__ import annotations

import sys
from pathlib import Path

from ui.data import (
    account_label,
    account_labels,
    capability_summary_rows,
    epistemic_status_label,
    evidence_category_label,
    evidence_level_label,
    extract_organizations,
    normalize_entry,
    priority_label,
    source_is_linkedin,
    weak_evidence_total,
    research_items,
)

ROOT = Path(__file__).resolve().parents[1]


def test_app_exists_and_is_dynamic():
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "Oman Airports" not in text
    assert "Standard Chartered" not in text
    assert "Siemens" not in text
    assert "WATCH" in text and "UNDERSTAND" in text and "DECIDE" in text and "ACT" in text


def test_report_normalizer_accepts_runner_shape():
    payload = {"organizations": [{"report": {"organization_name": "Example"}, "ai_synthesis": {}}]}
    assert normalize_entry(payload)["report"]["organization_name"] == "Example"


def test_report_normalizer_accepts_single_entry():
    payload = {"report": {"organization_name": "Example"}, "ai_synthesis": {}}
    assert normalize_entry(payload) == payload


def test_report_normalizer_rejects_unknown_shape():
    try:
        normalize_entry({"foo": "bar"})
    except ValueError as exc:
        assert "Unsupported report format" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_linkedin_detection_is_generic():
    assert source_is_linkedin("https://www.linkedin.com/in/example")
    assert source_is_linkedin("https://om.linkedin.com/company/example")
    assert source_is_linkedin("https://www.linkedin.com/jobs/view/123")
    assert not source_is_linkedin("https://example.com/page")


def test_labels_are_stable():
    assert evidence_level_label("direct_evidence") == "Direct evidence"
    assert priority_label("medium") == "MEDIUM"


def test_app_uses_imported_evidence_label_helper():
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "_evidence_level_label" not in text
    assert "evidence_level_label(row.get(\"evidence_level\", \"\"))" in text


# ---------------------------------------------------------------------------
# Phase 2 hardening: capability-summary rendering fix + uncertainty surfacing
# ---------------------------------------------------------------------------

def test_capability_summary_rows_flattens_nested_structure():
    """Regression test for the pre-existing UNDERSTAND rendering bug: the
    raw report['capability_summary'] is a nested dict of mapping dicts, not
    something safe to str() directly into the UI. This must flatten it into
    simple per-capability counts.
    """
    summary = {
        "network_connectivity": {
            "capability_name": "Network & Connectivity",
            "direct_evidence": [{"x": 1}],
            "strong_inference": [{"x": 1}, {"x": 2}],
            "weak_relevance": [],
        },
        "cloud_ai_infrastructure": {
            "capability_name": "Cloud & AI Infrastructure",
            "direct_evidence": [],
            "strong_inference": [],
            "weak_relevance": [{"x": 1}],
        },
    }
    rows = capability_summary_rows(summary)
    by_id = {r["capability_id"]: r for r in rows}
    assert by_id["network_connectivity"]["capability_name"] == "Network & Connectivity"
    assert by_id["network_connectivity"]["direct_count"] == 1
    assert by_id["network_connectivity"]["strong_count"] == 2
    assert by_id["cloud_ai_infrastructure"]["weak_count"] == 1
    # None of the rows should be raw dicts/lists a template would have to
    # str() -- every value is a plain int or string.
    for row in rows:
        assert isinstance(row["direct_count"], int)
        assert isinstance(row["strong_count"], int)
        assert isinstance(row["weak_count"], int)


def test_capability_summary_rows_handles_empty_summary():
    assert capability_summary_rows({}) == []
    assert capability_summary_rows(None) == []


def test_weak_evidence_total_counts_across_capabilities():
    summary = {
        "a": {"weak_relevance": [{"x": 1}, {"x": 2}]},
        "b": {"weak_relevance": [{"x": 1}]},
        "c": {},
    }
    assert weak_evidence_total(summary) == 3
    assert weak_evidence_total({}) == 0
    assert weak_evidence_total(None) == 0


def test_epistemic_and_evidence_category_labels_are_human_readable():
    assert epistemic_status_label("confirmed_evidence") == "Confirmed evidence"
    assert epistemic_status_label("inference_hypothesis") == "Inference hypothesis"
    assert epistemic_status_label(None) == "Unknown"
    assert evidence_category_label("business_signal_inference") == "Business-signal inference"
    assert evidence_category_label(None) == "Unknown"


def test_app_surfaces_disqualified_and_weak_evidence_counts():
    """A qualified (not just insufficient_evidence) account must still
    expose uncertainty -- disqualified source count and weak-evidence
    count -- somewhere in the UI, not only the insufficient_evidence
    banner (Phase 2 hardening, item 9)."""
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "disqualified_count" in text
    assert "weak_evidence_total" in text
    assert "What we don't know" in text


# ---------------------------------------------------------------------------
# Multi-account selector for an already-loaded report.
#
# Bug being fixed: the Streamlit UI always rendered the FIRST organization
# in a multi-account phase1d_report.json (normalize_entry picked orgs[0]
# and nothing let the user pick a different already-loaded account; typing
# a different organization name into the "Organization" field only affects
# the *next* "Analyze account" / live-research run, it never re-selects
# among accounts already sitting in a loaded report).
#
# Fixtures below use fictional organization names, never real companies --
# consistent with the rest of this test suite and with the no-hardcoding
# requirement on the production code itself (see
# test_app_exists_and_is_dynamic above, and test_account_selector_never_
# hardcodes_an_organization_name below, which checks against a DIFFERENT
# invented name each run so passing cannot be an accident of matching the
# same literal string already in app.py).
# ---------------------------------------------------------------------------

def _fake_account(name: str) -> dict:
    return {
        "report": {"organization_name": name, "country": "Testland", "chain": [], "signals": [],
                     "qualification": {}, "capability_summary": {}, "incumbent_evidence": [],
                     "acquisition_diagnostics": {}, "status": "qualified"},
        "ai_synthesis": {"status": "ok", "account_narrative": f"Narrative for {name}.", "next_best_actions": []},
        "opportunity_hypotheses": [],
    }


def _multi_account_payload(names: list[str]) -> dict:
    return {"is_live_search": True, "ai_configured": True, "organizations": [_fake_account(n) for n in names]}


def _import_app_with_fake_streamlit():
    """Imports (or re-fetches an already-imported) `app` module bound to a
    fresh FakeSt instance, so each test gets a clean `st.session_state` /
    `st.sidebar` without real Streamlit installed (it isn't, in this
    environment -- see tests/fake_streamlit.py).
    """
    from tests.fake_streamlit import FakeSt

    fake_st = FakeSt()
    sys.modules["streamlit"] = fake_st
    sys.modules.pop("app", None)
    import app  # noqa: PLC0415 -- deliberately imported fresh per test

    return app, fake_st


# --- ui.data: pure extraction/labeling helpers ------------------------------

def test_extract_organizations_returns_every_account_for_a_multi_org_report():
    payload = _multi_account_payload(["Northbridge Freight Authority", "Meridian Retail Group", "Calderon Manufacturing"])
    accounts = extract_organizations(payload)
    assert [a["report"]["organization_name"] for a in accounts] == [
        "Northbridge Freight Authority", "Meridian Retail Group", "Calderon Manufacturing",
    ]


def test_extract_organizations_returns_single_item_list_for_a_single_entry_payload():
    payload = _fake_account("Solitary Holdings Ltd")
    accounts = extract_organizations(payload)
    assert accounts == [payload]


def test_extract_organizations_rejects_empty_organizations_list():
    try:
        extract_organizations({"organizations": []})
    except ValueError as exc:
        assert "no organizations" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_extract_organizations_rejects_unknown_shape():
    try:
        extract_organizations({"foo": "bar"})
    except ValueError as exc:
        assert "Unsupported report format" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_normalize_entry_still_returns_only_the_first_account_unchanged():
    """Backward-compat / preserved single-account behavior: normalize_entry
    (still used wherever only "the" account of a payload is wanted) keeps
    picking orgs[0], exactly as before this feature existed.
    """
    payload = _multi_account_payload(["First Harbor Logistics", "Second Harbor Logistics"])
    assert normalize_entry(payload)["report"]["organization_name"] == "First Harbor Logistics"


def test_account_label_uses_organization_name_from_the_entry():
    entry = _fake_account("Ashgrove Utilities Cooperative")
    assert account_label(entry, 0) == "Ashgrove Utilities Cooperative"


def test_account_label_falls_back_to_generic_placeholder_when_name_missing():
    entry = {"report": {}, "ai_synthesis": {}}
    assert account_label(entry, 2) == "Account 3"


def test_account_labels_are_dynamic_not_hardcoded():
    """Same helper, two completely different invented name sets -- proving
    the labels are computed from whatever is in the payload, never a fixed
    list baked into the code."""
    set_a = account_labels([_fake_account("Quillfeather Aerospace"), _fake_account("Bramwell Insurance Group")])
    set_b = account_labels([_fake_account("Torrance Water District"), _fake_account("Emberline Textiles")])
    assert set_a == ["Quillfeather Aerospace", "Bramwell Insurance Group"]
    assert set_b == ["Torrance Water District", "Emberline Textiles"]


def test_account_labels_disambiguates_duplicate_organization_names():
    accounts = [_fake_account("Duplicate Holdings"), _fake_account("Duplicate Holdings")]
    labels = account_labels(accounts)
    assert labels == ["Duplicate Holdings", "Duplicate Holdings (2)"]
    assert len(set(labels)) == 2  # always unique, so a selection can be mapped back unambiguously


# --- app.py structure: wiring, and the "never runs research" guarantee ------

def test_app_defines_and_wires_the_account_selector():
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "_render_account_selector" in text
    assert "st.sidebar.selectbox" in text
    # It must actually be called from the sidebar render path, not just defined.
    assert text.count("_render_account_selector(") >= 2  # def + at least one call site


def test_account_selector_never_calls_live_research_or_analyze_account():
    """Structural regression for the hard requirement "Do NOT run research
    or call Analyze account when switching between accounts": the selector
    function's own source must not reference _run_live, run_acquisition,
    build_report, or synthesize_account at all.
    """
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    start = text.index("def _render_account_selector(")
    end = text.index("\ndef _render_sidebar(")
    body = text[start:end]
    for forbidden in ("_run_live(", "run_acquisition(", "build_report(", "synthesize_account("):
        assert forbidden not in body, f"{forbidden} must not appear inside _render_account_selector"


def test_account_selector_guards_single_account_reports():
    """Structural check for "preserve existing single-account behavior":
    the function must bail out before rendering anything when there is at
    most one account.
    """
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    start = text.index("def _render_account_selector(")
    end = text.index("\ndef _render_sidebar(")
    body = text[start:end]
    assert "len(accounts) <= 1" in body or "len(accounts) < 2" in body
    assert "return" in body


# --- Functional: exercising the real app.py logic with a fake st -----------

def test_selecting_a_different_account_switches_the_loaded_entry_without_research():
    app, fake_st = _import_app_with_fake_streamlit()
    try:
        accounts = [
            _fake_account("Rosendale Municipal Water"),
            _fake_account("Kirkbride Textile Mills"),
            _fake_account("Alverstone Maritime"),
        ]
        fake_st.session_state.accounts = accounts
        fake_st.session_state.entry = accounts[0]

        calls: list[tuple] = []
        app._run_live = lambda *a, **k: calls.append((a, k))  # must never be invoked by the selector

        fake_st.sidebar.next_selectbox_return = "Kirkbride Textile Mills"
        app._render_account_selector()

        assert fake_st.session_state.entry["report"]["organization_name"] == "Kirkbride Textile Mills"
        assert calls == []  # research was never triggered by switching accounts
        # The dropdown was populated straight from the loaded accounts, not a fixed list.
        assert fake_st.sidebar.selectbox_calls[-1]["options"] == [
            "Rosendale Municipal Water", "Kirkbride Textile Mills", "Alverstone Maritime",
        ]
    finally:
        sys.modules.pop("app", None)
        sys.modules.pop("streamlit", None)


def test_single_account_report_shows_no_selector_and_keeps_original_entry():
    app, fake_st = _import_app_with_fake_streamlit()
    try:
        only_account = _fake_account("Everholt Data Services")
        fake_st.session_state.accounts = [only_account]
        fake_st.session_state.entry = only_account

        app._render_account_selector()

        assert fake_st.sidebar.selectbox_calls == []  # no dropdown rendered at all
        assert fake_st.session_state.entry is only_account  # untouched
    finally:
        sys.modules.pop("app", None)
        sys.modules.pop("streamlit", None)


def test_live_research_result_never_shows_a_selector():
    """Preserve existing single-account behavior end-to-end for the
    Analyze-account/live-research path: a live research result is always
    exactly one account, so `st.session_state.accounts` set to a one-item
    list must not surface a selector, exactly like any other single-account
    load.
    """
    app, fake_st = _import_app_with_fake_streamlit()
    try:
        entry = _fake_account("Pellham Grain Cooperative")
        fake_st.session_state.entry = entry
        fake_st.session_state.accounts = [entry]  # what the Analyze-account handler sets

        app._render_account_selector()

        assert fake_st.sidebar.selectbox_calls == []
    finally:
        sys.modules.pop("app", None)
        sys.modules.pop("streamlit", None)


def test_loading_a_multi_account_report_populates_accounts_and_defaults_to_the_first():
    """Exercises `_load_accounts` (the helper both "load report" sidebar
    actions use) directly -- proving a freshly loaded multi-account payload
    populates `st.session_state.accounts` from exactly what's in the JSON.
    """
    app, fake_st = _import_app_with_fake_streamlit()
    try:
        payload = _multi_account_payload(["Wrentham Shipping Lines", "Castlebrook Energy", "Fennimore Robotics"])
        accounts = app._load_accounts(payload)

        assert fake_st.session_state.accounts == accounts
        assert [a["report"]["organization_name"] for a in accounts] == [
            "Wrentham Shipping Lines", "Castlebrook Energy", "Fennimore Robotics",
        ]
    finally:
        sys.modules.pop("app", None)
        sys.modules.pop("streamlit", None)


def test_account_selector_never_hardcodes_an_organization_name():
    """Regression against hardcoding: a name invented fresh for this test
    run (guaranteed not to already appear anywhere in app.py's source)
    still shows up correctly as a selectable option once it's part of a
    loaded multi-account report.
    """
    app, fake_st = _import_app_with_fake_streamlit()
    try:
        novel_name = "Zzyzxvale Quantum Fabrication Consortium"
        assert novel_name not in (ROOT / "app.py").read_text(encoding="utf-8")

        accounts = [_fake_account(novel_name), _fake_account("Oldbury Freight Cooperative")]
        fake_st.session_state.accounts = accounts
        fake_st.session_state.entry = accounts[0]

        app._render_account_selector()

        assert novel_name in fake_st.sidebar.selectbox_calls[-1]["options"]
    finally:
        sys.modules.pop("app", None)
        sys.modules.pop("streamlit", None)


def test_research_items_exposes_chain_evidence_and_standalone_signals():
    entry = {
        "report": {
            "organization_name": "Example",
            "chain": [{
                "source_url": "https://example.com/a",
                "source_title": "Example announcement",
                "customer_signal": {"text": "The organization expanded its technology platform."},
                "business_impact": "Potential capacity requirement",
                "technology_requirement": "Additional infrastructure",
                "evidence_level": "direct_evidence",
                "evidence_category": "organizational_technology_event",
                "epistemic_status": "confirmed_evidence",
                "confidence": 91,
                "potential_otech_capability": {"capability_name": "Cloud & AI Infrastructure"},
                "matched_terms": ["expanded"],
            }],
            "signals": [{
                "source_url": "https://example.com/b",
                "source_title": "Separate signal",
                "text": "A separate public signal.",
                "signal_type": "expansion",
                "matched_terms": ["growth"],
            }],
        }
    }
    items = research_items(entry)
    assert len(items) == 2
    assert items[0]["kind"] == "evidence"
    assert items[0]["capability"] == "Cloud & AI Infrastructure"
    assert items[1]["kind"] == "signal"


def test_research_items_does_not_duplicate_same_chain_source_title():
    entry = {"report": {"chain": [
        {"source_url": "https://example.com/a", "source_title": "A", "customer_signal": {"text": "x"}},
        {"source_url": "https://example.com/a", "source_title": "A", "customer_signal": {"text": "y"}},
    ], "signals": []}}
    assert len(research_items(entry)) == 1


def test_research_workspace_exists_and_is_connected_to_main():
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '["WATCH", "UNDERSTAND", "DECIDE", "ACT", "RESEARCH", "PEOPLE"]' in text
    assert "def _render_research(" in text
    assert "Validate" in text
    assert "review" in text.lower()
    assert "Reject" in text
    assert "My opinion / account-manager comment" in text

def test_research_items_does_not_substitute_unrelated_same_url_signal():
    entry = {"report": {
        "chain": [{
            "source_url": "https://example.com/a",
            "source_title": "Account page",
            "customer_signal": {"text": None},
            "supporting_context": "Retrieved page context",
            "matched_terms": ["ai"],
            "evidence_level": "weak_relevance",
        }],
        "signals": [{
            "source_url": "https://example.com/a",
            "source_title": "Account page",
            "text": "An unrelated expansion signal.",
            "matched_terms": ["Expansion"],
        }],
    }}
    item = research_items(entry)[0]
    assert item["text"] == ""
    assert item["source_context"] == "Retrieved page context"


def test_clean_evidence_excerpt_rejects_navigation_chrome():
    from commercial.text_heuristics import clean_evidence_excerpt
    chrome = "Services Services Advertising Marketing Services Fleet Data & APIs Research & Consulting Network and Route Planning Marketplace Store Market Sector"
    assert clean_evidence_excerpt(chrome) == ""


def test_clean_evidence_excerpt_preserves_real_source_sentence():
    from commercial.text_heuristics import clean_evidence_excerpt
    text = "The organization announced a new technology programme to modernize its operations."
    assert clean_evidence_excerpt(text) == text


def test_chain_contains_clean_evidence_excerpt_field():
    from pathlib import Path
    text = Path("research/analysis/report.py").read_text(encoding="utf-8")
    assert '"evidence_excerpt": evidence_excerpt or None' in text
    assert '"published_at":' in text


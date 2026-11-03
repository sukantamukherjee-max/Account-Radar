"""DECIDE v2 -- the sales-judgment layer.

Four states (PURSUE / VALIDATE / MONITOR / NO ACTION) decided only from
existing outputs: the normalized account-signal layer, Opportunity
Intelligence (qualified by the existing chain confirmation), UNDERSTAND's
posture/buying view and People Intelligence. Nothing is invented.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from ui.decide_view import (
    DECISION_STATES,
    MONITOR,
    NO_ACTION,
    PURSUE,
    VALIDATE,
    build_decision,
    decision_header_html,
    handoff_brief,
    known_html,
    reasons_html,
)

import streamlit as _REAL_STREAMLIT  # noqa: E402  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
ORG = "Meridian Ports Authority"
OWN = "https://www.meridianports.example"
NEWS = "https://news.example/meridian-ports-soc"
TREND = "https://ifactoryapp.com/industries/aviation-management/airport-technology-trends-2026"
TREND_TITLE = "10 Airport Technology Trends Transforming Aviation Operations in 2026"


def _signal(kind, text, url, title=""):
    return {"signal_type": kind, "text": text, "source_url": url, "source_title": title, "matched_terms": []}


def _row(cap, level, category, url, *, signal_type=None, title="t", excerpt=None, confidence=60.0, status=None):
    return {
        "customer_signal": {"signal_type": signal_type, "text": None}, "evidence_excerpt": excerpt,
        "business_impact": f"{cap} impact.", "technology_requirement": "req",
        "potential_otech_capability": {"capability_id": cap, "capability_name": cap.replace("_", " ").title()},
        "evidence_level": level, "confidence": confidence, "source_url": url, "source_title": title,
        "supporting_context": excerpt or "", "matched_terms": [], "incumbent_context": [], "evidence_category": category,
        "inference_cluster_id": url,
        "epistemic_status": status or ("confirmed_evidence" if level == "direct_evidence" else "inference_hypothesis"),
    }


def _opp(cap, level, status, confidence, urls):
    return {
        "capability_id": cap, "capability_name": cap.replace("_", " ").title(), "priority": "high",
        "score": confidence * 0.7, "evidence_level": level, "confidence": confidence, "evidence_count": len(urls),
        "independent_source_count": len(urls), "hypothesis": "h", "rationale": "r", "who": "w", "conversation": "c",
        "next_step": "n", "source_urls": urls, "incumbent_context": [], "independent_domain_count": len(urls),
        "inference_cluster_count": 1, "epistemic_status": status,
    }


def _person(pid, name, *, assoc="confirmed", seniority="director", functions=(), tech=(), influence=None,
            title="", score=40):
    return {
        "person_id": pid, "name": name, "current_title": title, "organization_name": ORG,
        "organization_association": assoc, "linkedin_url": f"https://www.linkedin.com/in/{pid}",
        "source_urls": [f"https://www.linkedin.com/in/{pid}"], "source_types": ["linkedin_profile"],
        "location": None, "seniority": seniority, "functions": list(functions), "technology_areas": list(tech),
        "influence_type": influence, "relevance_score": score, "confidence": 70.0, "evidence": [],
        "recent_signals": [], "chips": [],
    }


INFRA_LEAD = _person("infra-lead", "Rana Infra", seniority="director", functions=["infrastructure"],
                     tech=["cloud", "network"], influence="potential_decision_maker",
                     title="Vice President - Infrastructure & Security")
CFO = _person("cfo", "Sami Finance", seniority="c_level", functions=["procurement"], influence="potential_decision_maker",
              title="Chief Financial Officer", score=55)
CYBER = _person("cyber", "Lina Cyber", seniority="head", functions=["cybersecurity"], tech=["cybersecurity"],
                influence="technical_stakeholder", title="Head of Cybersecurity")
BUSINESS = _person("biz", "Omar Business", seniority="manager", influence="business_stakeholder", title="Office Manager")

LEADERSHIP = [
    _signal("leadership_change", "Meridian Ports Authority appoints Jane Example as Acting CEO.",
            "https://aviation.example/acting-ceo", "Meridian Ports Authority appoints Acting CEO"),
    _signal("leadership_change", "Jane Example appointed Acting CEO of Meridian Ports Authority.",
            "https://www.linkedin.com/posts/aviation_meridian-ceo", "Meridian Ports Authority appoints CEO"),
]


def _report(**over):
    report = {
        "organization_name": ORG, "country": "Testland", "website": OWN, "status": "qualified",
        "qualification": {"qualified_count": 30, "disqualified_count": 0, "corpus_status": "qualified"},
        "signals": [], "chain": [], "people": [], "incumbent_evidence": [], "opportunity_hypotheses": [],
    }
    report.update(over)
    return report


def _decide(report, opportunities=None):
    return build_decision({"report": report, "opportunity_hypotheses": opportunities or []})


def _all_urls(report) -> set[str]:
    urls = {s["source_url"] for s in report.get("signals") or []}
    urls |= {r["source_url"] for r in report.get("chain") or []}
    urls |= {p["linkedin_url"] for p in report.get("people") or []}
    return urls


# ---------------------------------------------------------------------------
# PURSUE
# ---------------------------------------------------------------------------

def _pursue_report():
    return _report(
        signals=[_signal("technology_initiative",
                         "Meridian Ports Authority is deploying a security operations center and zero trust network.",
                         NEWS, "Meridian Ports Authority deploys security operations center")],
        chain=[_row("cybersecurity_resilience", "direct_evidence", "organizational_technology_event", NEWS,
                    signal_type="technology_initiative", title="Meridian Ports Authority deploys SOC",
                    excerpt="Meridian Ports Authority is deploying a security operations center.", confidence=90.0)],
        people=[CFO, CYBER, INFRA_LEAD],
    )


def test_pursue_with_a_real_qualifying_opportunity():
    report = _pursue_report()
    d = _decide(report, [_opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 90.0,
                              [NEWS, "https://second.example/soc"])])
    assert d.state == PURSUE and d.confirmed_opportunity_count == 1
    assert d.confidence == "High"
    assert "Cybersecurity Resilience opportunity" in d.headline
    assert d.reasons[0].anchor == "ar-opp-cybersecurity_resilience" and d.reasons[0].source_url == NEWS
    assert d.stakeholder.person_id == "cyber"          # aligned with the opportunity's capability
    assert "qualify the Cybersecurity Resilience opportunity" in d.next_action
    assert "Existing opportunity evidence" in d.known and "Confirmed account evidence" in d.known


def test_single_source_pursue_is_medium_confidence():
    d = _decide(_pursue_report(), [_opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 90.0, [NEWS])])
    assert d.state == PURSUE and d.confidence == "Medium"


# ---------------------------------------------------------------------------
# Weak / context / leadership can never produce PURSUE
# ---------------------------------------------------------------------------

def test_opportunity_claiming_confirmation_on_industry_context_is_not_pursue():
    report = _report(
        signals=[_signal("technology_initiative", "Ports such as Meridian Ports Authority and others adopt zero trust.",
                         TREND, TREND_TITLE)],
        chain=[_row("cybersecurity_resilience", "direct_evidence", "industry_context", TREND, title=TREND_TITLE,
                    status="confirmed_evidence")],
        people=[CYBER],
    )
    d = _decide(report, [_opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 95.0, [TREND])])
    assert d.state != PURSUE and d.confirmed_opportunity_count == 0


def test_weak_relevance_opportunity_is_ignored_and_never_pursue():
    d = _decide(_report(people=[CYBER]), [_opp("network_connectivity", "weak_relevance", "inference_hypothesis", 30.0,
                                               ["https://x.example/a"])])
    assert d.state == NO_ACTION and d.opportunity_count == 0


def test_industry_context_alone_is_no_action():
    report = _report(
        signals=[_signal("technology_initiative", "Ports such as Meridian Ports Authority and others adopt zero trust.",
                         TREND, TREND_TITLE)],
        chain=[_row("cybersecurity_resilience", "weak_relevance", "industry_context", TREND, title=TREND_TITLE)],
        people=[CYBER, INFRA_LEAD],
    )
    d = _decide(report)
    assert d.state == NO_ACTION
    assert any("background only" in r.text for r in d.reasons)
    assert d.questions == [] and d.stakeholder is None


def test_leadership_change_alone_is_never_pursue():
    d = _decide(_report(signals=LEADERSHIP, people=[INFRA_LEAD, CFO]))
    assert d.state == VALIDATE and d.state != PURSUE and d.opportunity_count == 0


def test_generic_stakeholder_technology_mention_cannot_create_a_decision():
    d = _decide(_report(people=[INFRA_LEAD, CYBER]))
    assert d.state == NO_ACTION


# ---------------------------------------------------------------------------
# VALIDATE / MONITOR / NO ACTION
# ---------------------------------------------------------------------------

def test_validate_with_strategic_activity_and_zero_opportunities():
    report = _report(signals=LEADERSHIP, people=[CFO, INFRA_LEAD, BUSINESS],
                     chain=[_row("network_connectivity", "weak_relevance", "incidental_mention", TREND, title=TREND_TITLE)])
    d = _decide(report)
    assert d.state == VALIDATE
    assert d.headline == ("Recent leadership change creates a reasonable discovery point, but available evidence does "
                          "not establish an Otech-specific technology or procurement requirement.")
    assert d.confidence == "Medium" and "2 independent sources" in d.confidence_reason
    assert d.stakeholder.person_id == "infra-lead"     # technology-relevant beats a more senior non-tech CFO
    texts = [r.text for r in d.reasons]
    assert 2 <= len(texts) <= 4
    assert texts[0].startswith("Account-specific leadership change")
    assert any("Relevant stakeholder identified: Rana Infra" in t for t in texts)
    assert any("No direct technology or procurement event" in t for t in texts)
    assert any("contextual rather than account-specific" in t for t in texts)
    assert "Schedule a discovery conversation with Rana Infra" in d.next_action


def test_validate_from_an_account_specific_technology_event_without_opportunity():
    report = _report(signals=[_signal("procurement_activity",
                                      "Meridian Ports Authority has issued a tender for SD-WAN and data center network upgrades.",
                                      f"{OWN}/en/tenders/1", "Tenders")], people=[INFRA_LEAD])
    d = _decide(report)
    assert d.state == VALIDATE and d.opportunity_count == 0
    assert "account-specific procurement activity" in d.headline
    assert d.questions[0].startswith("What is the scope and timeline of the procurement activity")


def test_validate_from_an_opportunity_hypothesis():
    url = "https://news.example/meridian-network"
    report = _report(
        signals=[_signal("project", "Meridian Ports Authority plans a new terminal network.", url, "Meridian network")],
        chain=[_row("network_connectivity", "strong_inference", "business_signal_inference", url, signal_type="project",
                    title="Meridian network", excerpt="plans a new terminal network")],
        people=[INFRA_LEAD])
    d = _decide(report, [_opp("network_connectivity", "strong_inference", "inference_hypothesis", 70.0, [url])])
    assert d.state == VALIDATE and d.confidence == "Medium"
    assert any(r.anchor == "ar-opp-network_connectivity" for r in d.reasons + [f for g in d.known.values() for f in g])


def test_monitor_when_no_technology_relevant_stakeholder():
    d = _decide(_report(signals=LEADERSHIP, people=[CFO, BUSINESS]))
    assert d.state == MONITOR
    assert any("No technology-relevant stakeholder" in r.text for r in d.reasons)
    assert d.questions and d.questions[0].startswith("Does the leadership change lead to")
    assert "Monitor the account" in d.next_action


def test_weakly_associated_technology_stakeholder_does_not_create_validate():
    weak_tech = dict(INFRA_LEAD, person_id="weak-tech", organization_association="weak")
    assert _decide(_report(signals=LEADERSHIP, people=[weak_tech])).state == MONITOR


def test_monitor_for_general_activity_and_for_reported_news_only():
    general = _report(signals=[_signal("technology_initiative", "To improve operations, Meridian Ports Authority has implemented innovative strategies.",
                                       "https://docs.example/strategy", "Strategic Management of Meridian Ports Authority")],
                      people=[INFRA_LEAD])
    assert _decide(general).state == MONITOR
    news = _report(signals=[_signal("technology_initiative",
                                    "News Meridian Ports Authority Signs Strategic Partnership with Bank Example to "
                                    "Implement Advanced Direct Payment System 01 Dec", "https://group.example", "Group")],
                   people=[INFRA_LEAD])
    d = _decide(news)
    assert d.state == MONITOR and d.reasons[0].text.startswith("Reported partnership")


def test_no_action_with_no_account_developments():
    d = _decide(_report())
    assert d.state == NO_ACTION and d.confidence == "High"
    assert d.headline.startswith("The public evidence shows no account-specific development")
    assert d.unknown == [] and d.questions == []


def test_no_action_on_a_thin_corpus_is_low_confidence():
    d = _decide(_report(qualification={"qualified_count": 2}, status="insufficient_evidence"))
    assert d.state == NO_ACTION and d.confidence == "Low"


# ---------------------------------------------------------------------------
# Stakeholders: selection and never invented
# ---------------------------------------------------------------------------

def test_stakeholder_is_always_a_real_person_from_people_intelligence():
    report = _report(signals=LEADERSHIP, people=[INFRA_LEAD, CFO, CYBER])
    d = _decide(report)
    assert d.stakeholder.person_id in {p["person_id"] for p in report["people"]}
    assert d.stakeholder.name == "Rana Infra" and d.stakeholder.title == "Vice President - Infrastructure & Security"
    assert "confirmed link to the account" in d.stakeholder.why


def test_no_people_means_no_stakeholder_and_a_role_not_a_name():
    d = _decide(_report(signals=[_signal("procurement_activity",
                                         "Meridian Ports Authority has issued a tender for SD-WAN upgrades.",
                                         f"{OWN}/tenders/2", "Tenders")]))
    assert d.stakeholder is None and "the account's technology leadership" in d.next_action


def test_unconfirmed_people_are_never_selected():
    unconfirmed = dict(INFRA_LEAD, person_id="u", organization_association="unconfirmed")
    d = _decide(_report(signals=LEADERSHIP, people=[unconfirmed]))
    assert d.stakeholder is None


# ---------------------------------------------------------------------------
# Missing information and validation questions
# ---------------------------------------------------------------------------

def test_missing_information_is_decision_relevant_and_never_contradicts_the_report():
    d = _decide(_report(signals=LEADERSHIP, people=[INFRA_LEAD]))
    joined = " ".join(d.unknown)
    for gap in ("Technology priorities following the leadership change", "Active projects", "Procurement timing",
                "Budget", "Decision process"):
        assert gap in joined, gap
    assert "roadmap" in joined
    tender = _report(signals=[_signal("procurement_activity",
                                      "Meridian Ports Authority has issued a tender for SD-WAN valued at OMR 4.5 million; "
                                      "the closing date is 30 November.", f"{OWN}/tenders/3", "Tenders")],
                     people=[INFRA_LEAD])
    joined = " ".join(_decide(tender).unknown)
    assert "Budget" not in joined and "Procurement timing" not in joined and "Technology priorities" not in joined


def test_validation_questions_come_from_the_evidence():
    d = _decide(_report(signals=LEADERSHIP, people=[INFRA_LEAD]))
    assert 1 <= len(d.questions) <= 3
    assert d.questions[0] == ("Have the recent leadership changes altered your infrastructure, cloud or network "
                              "priorities for the next 12–18 months?")
    assert d.questions[1].startswith("Under the new leadership, who owns infrastructure, cloud or network decisions")
    partnership = _report(signals=[_signal("partnership",
                                           "Meridian Ports Authority signs a partnership with Acme Networks for terminal services.",
                                           f"{OWN}/news/acme", "News")], people=[INFRA_LEAD])
    q = _decide(partnership).questions[0]
    assert "Acme Networks partnership" in q


# ---------------------------------------------------------------------------
# Traceability, no invented opportunities, no duplicates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("builder", ["pursue", "validate", "monitor"])
def test_every_linked_reason_and_fact_traces_to_report_data(builder):
    if builder == "pursue":
        report = _pursue_report()
        opps = [_opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 90.0, [NEWS])]
    elif builder == "validate":
        report, opps = _report(signals=LEADERSHIP, people=[INFRA_LEAD, CYBER]), []
    else:
        report, opps = _report(signals=LEADERSHIP, people=[CFO]), []
    d = _decide(report, opps)
    urls = _all_urls(report)
    for f in d.reasons + [f for g in d.known.values() for f in g]:
        if f.source_url:
            assert f.source_url in urls, f.source_url


def test_no_invented_opportunities():
    d = _decide(_report(signals=LEADERSHIP, people=[INFRA_LEAD]))
    assert d.opportunity_count == 0 and "Existing opportunity evidence" not in d.known
    html = known_html(d) + reasons_html(d)
    assert "ar-opp-" not in html


def test_no_duplicate_signals_in_the_decision():
    text = "News Meridian Ports Authority Signs Strategic Partnership with Bank Example to Implement Advanced Payment System"
    report = _report(signals=[_signal("partnership", "10 Mar " + text, f"{OWN}/news", "News"),
                              _signal("technology_initiative", text + " 01 Dec", f"{OWN}/news", "News")] + LEADERSHIP,
                     people=[INFRA_LEAD])
    d = _decide(report)
    facts = [f.text for g in d.known.values() for f in g]
    assert len(facts) == len(set(facts))
    assert sum("Bank Example" in f for f in facts) == 1


def test_decision_state_is_one_of_four_and_rendered_as_a_label_not_a_control():
    d = _decide(_report(signals=LEADERSHIP, people=[INFRA_LEAD]))
    assert d.state in DECISION_STATES
    header = decision_header_html(d)
    assert '<span class="ar-decision-state is-validate">VALIDATE</span>' in header
    assert "<button" not in header and "<a " not in header


# ---------------------------------------------------------------------------
# App: DECIDE page, People link, ACT handoff
# ---------------------------------------------------------------------------

def _apptest(report, opportunities=None):
    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(APP), default_timeout=30)
    base = {"chain": [], "signals": [], "capability_summary": {}, "incumbent_evidence": [],
            "acquisition_diagnostics": {}, "status": "qualified"}
    base.update(report)
    at.session_state["entry"] = {"report": base, "ai_synthesis": {}, "opportunity_hypotheses": opportunities or []}
    at.session_state["loaded_from"] = "test"
    return at


def test_decide_page_renders_all_sections():
    at = _apptest(_report(signals=LEADERSHIP, people=[INFRA_LEAD, CFO]))
    at.run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    for part in ("Current recommendation", "Confidence · Medium", "Why this decision", "What we know",
                 "What we don&#x27;t know", "What to validate", "Who to engage", "Recommended next action"):
        assert part in html, part
    assert "aviation.example/acting-ceo" in html                        # evidence opens its source
    assert any(b.key == "decide_take_to_act" for b in at.button)
    assert not any(b.label in DECISION_STATES for b in at.button)


def test_open_in_people_selects_the_stakeholder_and_navigates():
    at = _apptest(_report(signals=LEADERSHIP, people=[INFRA_LEAD, CFO]))
    at.run()
    at.session_state["people_search"] = "zzz"
    at.button(key="decide_open_person").click().run()
    assert not at.exception
    assert at.session_state["people_selected"] == "infra-lead"
    assert at.session_state["people_search"] == "" and at.session_state["ar_nav"] == "People"


def test_take_to_act_carries_the_decision_context_into_act():
    at = _apptest(_report(signals=LEADERSHIP, people=[INFRA_LEAD, CFO]))
    at.run()
    at.button(key="decide_take_to_act").click().run()
    assert not at.exception
    ctx = at.session_state["act_context"]
    assert ctx["decision"] == VALIDATE and ctx["stakeholder"]["person_id"] == "infra-lead"
    assert ctx["validation_questions"] and ctx["account_key"] == ORG
    assert {e["source_url"] for e in ctx["evidence"]} <= _all_urls(_report(signals=LEADERSHIP, people=[INFRA_LEAD, CFO]))
    assert at.session_state["ar_nav"] == "Next actions"
    # ACT v2 (ui/act_view.py) works from the handed-over context.
    html = " ".join(m.value for m in at.markdown)
    assert "Handed over from DECIDE" in html and '<span class="ar-decision-state is-validate">VALIDATE</span>' in html
    assert "Discovery Recommended" in html
    email = next(t.value for t in at.text_area if (t.key or "").startswith("act_outreach_email_"))
    assert "Dear Rana" in email and ctx["validation_questions"][0] in email
    assert at.text_area(key="act_brief").value == handoff_brief(ctx)


def test_act_ignores_a_context_from_another_account():
    at = _apptest(_report(signals=LEADERSHIP, people=[INFRA_LEAD]))
    at.session_state["act_context"] = {"account_key": "Some Other Org", "decision": VALIDATE, "headline": "x"}
    at.run()
    assert not any("Handed over from DECIDE" in m.value for m in at.markdown)


def test_opportunity_facts_link_to_the_opportunity_detail_on_the_board():
    report = _pursue_report()
    opp = _opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 90.0, [NEWS])
    at = _apptest(report, [opp])
    at.run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert 'href="#ar-opp-cybersecurity_resilience"' in html
    assert 'id="ar-opp-cybersecurity_resilience"' in html
    assert '<span class="ar-decision-state is-pursue">PURSUE</span>' in html


def test_no_action_has_no_take_to_act_control():
    at = _apptest(_report())
    at.run()
    assert not any(b.key == "decide_take_to_act" for b in at.button)


# ---------------------------------------------------------------------------
# Real saved corpus (skipped when not present)
# ---------------------------------------------------------------------------

def _saved_corpus():
    found = sorted(ROOT.glob("evidence_corpus_*.json"))
    return found[0] if found else None


@pytest.mark.skipif(_saved_corpus() is None, reason="no saved evidence corpus in the project folder")
def test_saved_corpus_decision_is_validate_with_zero_opportunities():
    from commercial.evidence_mapping import map_all_sources
    from commercial.incumbents import detect_all_incumbent_evidence
    from commercial.opportunities import build_opportunity_hypotheses
    from commercial.people import build_people
    from commercial.signals import extract_all_signals
    from compare_evidence_fix import _source_from_dict
    from research.analysis.qualification import QualificationResult, qualify_corpus
    from research.analysis.report import AccountRadarReport

    saved = json.loads(_saved_corpus().read_text(encoding="utf-8"))
    sources = [_source_from_dict(x) for x in saved["sources"]]
    qualified = qualify_corpus(sources).qualified_sources
    org, country = saved["organization_name"], saved["country"]
    mappings = map_all_sources(qualified, organization_name=org, country=country)
    opportunities = [o.to_dict() for o in build_opportunity_hypotheses(mappings, max_items=5)]
    report = AccountRadarReport(
        organization_name=org, country=country, website="https://www.omanairports.co.om", linkedin_company_url=None,
        qualification=QualificationResult(qualified_sources=qualified), signals=extract_all_signals(qualified),
        capability_mappings=mappings, incumbent_evidence=detect_all_incumbent_evidence(qualified),
        opportunity_hypotheses=build_opportunity_hypotheses(mappings),
        people=build_people([s for s in sources if s.source_type == "linkedin_profile"], organization_name=org),
        acquisition_diagnostics={},
    ).to_dict()
    d = build_decision({"report": report, "opportunity_hypotheses": opportunities})
    assert opportunities == [] and d.state == VALIDATE and d.state != PURSUE
    assert d.headline.startswith("Recent leadership change creates a reasonable discovery point")
    assert d.stakeholder and d.stakeholder.person_id in {p["person_id"] for p in report["people"]}
    # Confirmed People-list member with technology evidence; no title/name is pinned.
    assert d.stakeholder.association == "confirmed"
    assert {p["person_id"]: p for p in report["people"]}[d.stakeholder.person_id]["organization_association"] == "confirmed"
    assert d.stakeholder.areas

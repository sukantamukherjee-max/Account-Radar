"""UNDERSTAND v2 -- builder, renderer and regression tests.

The builder (`ui.understand_view.build_understand_view`) is deterministic
presentation logic over an existing analysis entry. These tests check that it
answers the seven UNDERSTAND questions from the normalized account-signal
layer without ever upgrading evidence, creating opportunities or rendering
raw/unstructured data, and that the Evidence Intelligence regressions still
hold when a report is produced by the real (offline) pipeline.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from commercial.capabilities import get_catalog
from commercial.evidence_mapping import _mentions_organization, _org_core_tokens, compound_aliases, map_all_sources
from commercial.incumbents import detect_all_incumbent_evidence
from commercial.opportunities import build_opportunity_hypotheses
from commercial.people import build_people
from commercial.signals import extract_all_signals
from research.analysis.qualification import QualificationResult
from research.analysis.report import AccountRadarReport
from research.sources import AccessStatus, Source
from ui import account_signals as sig
from ui.executive_summary import rank_key_signals
from ui.understand_view import (
    OBSERVED,
    STATUS_CONTEXT,
    STATUS_EVIDENCE,
    STATUS_MONITOR,
    STATUS_NONE,
    UNKNOWN,
    build_understand_view,
    clean_person_title,
    clean_text,
    understand_sections_html,
)

import streamlit as _REAL_STREAMLIT  # noqa: E402  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
ORG = "Oman Airports Management Company"
COUNTRY = "Oman"
OWN = "https://www.omanairports.co.om"

TREND_URL = "https://ifactoryapp.com/industries/aviation-management/airport-technology-trends-2026"
TREND_TITLE = "10 Airport Technology Trends Transforming Aviation Operations in 2026"
PARENT_URL = "https://www.aviation-group-holding.example"
LISTING_URL = "https://www.globaltenders.com/oman/om-airports-tenders"
DIRECTORY_URL = "https://www.cbinsights.com/company/oman-airport-management"
PROFILE_URL = "https://om.linkedin.com/in/jane-example"


# ---------------------------------------------------------------------------
# Hand-built report fixtures (shapes mirror AccountRadarReport.to_dict())
# ---------------------------------------------------------------------------

def _signal(kind, text, url, title=""):
    return {"signal_type": kind, "text": text, "source_url": url, "source_title": title, "matched_terms": []}


def _row(cap, level, category, url, *, signal_type=None, title="t", excerpt=None, confidence=60.0, status=None):
    return {
        "customer_signal": {"signal_type": signal_type, "text": None},
        "evidence_excerpt": excerpt,
        "business_impact": "impact",
        "technology_requirement": "req",
        "potential_otech_capability": {"capability_id": cap, "capability_name": cap.replace("_", " ").title()},
        "evidence_level": level,
        "confidence": confidence,
        "source_url": url,
        "source_title": title,
        "supporting_context": excerpt or "",
        "matched_terms": [],
        "incumbent_context": [],
        "evidence_category": category,
        "inference_cluster_id": url,
        "epistemic_status": status or ("confirmed_evidence" if level == "direct_evidence" else "inference_hypothesis"),
    }


def _person(pid, name, assoc, influence, *, title="Director of IT", seniority="director", functions=("it",),
            tech=("network",), confidence=70.0):
    return {
        "person_id": pid, "name": name, "current_title": title, "organization_name": ORG,
        "organization_association": assoc, "linkedin_url": f"https://om.linkedin.com/in/{pid}",
        "source_urls": [f"https://om.linkedin.com/in/{pid}"], "source_types": ["linkedin_profile"],
        "location": None, "seniority": seniority, "functions": list(functions), "technology_areas": list(tech),
        "influence_type": influence, "relevance_score": 50, "confidence": confidence,
        "evidence": ["## Experience\n- RAW PROFILE MARKDOWN that must never be rendered"],
        "recent_signals": [], "chips": [],
    }


def _incumbent(term, category, url, title, snippet):
    return {"term": term, "category": category, "source_url": url, "source_title": title, "snippet": snippet}


def _report(**overrides):
    report = {
        "organization_name": ORG,
        "country": COUNTRY,
        "website": OWN,
        "qualification": {"qualified_count": 8, "disqualified_count": 0, "corpus_reason": ""},
        "signals": [
            _signal("leadership_change", "Oman Airports appoints Nasser Example as Acting CEO of Oman Airports Management Company.",
                    "https://news.example/oman-airports-acting-ceo", "Oman Airports appoints Acting CEO"),
            _signal("technology_initiative", "Oman Airports is deploying a security operations center and zero trust network across its airports.",
                    "https://news.example/oman-airports-soc", "Oman Airports deploys security operations center"),
            _signal("technology_initiative", "OAMC has implemented innovative strategies in its strategic management.",
                    "https://docs.example/oamc-strategy", "Oman Airports strategy overview"),
            _signal("technology_initiative", "Airports such as Oman Airports and others adopt OT cybersecurity and zero trust.",
                    TREND_URL, TREND_TITLE),
            _signal("investment", "Oman Airports is a government company affiliated with the Investment Authority, established in 2002.",
                    f"{OWN}/en/content/about", "About Oman Airports"),
            _signal("technology_initiative",
                    "10 Mar News Oman Airports Signs Strategic Partnership with Bank Muscat to Implement Advanced "
                    "Direct Payment System 01 Dec At a Glance Our impact in numbers",
                    PARENT_URL, "Aviation Group Holding"),
            _signal("partnership", "Proud to partner with #OmanAirports on a new intelligent campus.", PROFILE_URL,
                    "Jane Example - Head of IT at Oman Airports Management Company"),
        ],
        "chain": [
            _row("cybersecurity_resilience", "direct_evidence", "organizational_technology_event",
                 "https://news.example/oman-airports-soc", signal_type="technology_initiative",
                 title="Oman Airports deploys security operations center",
                 excerpt="Oman Airports is deploying a security operations center across its airports.", confidence=88.0),
            _row("cybersecurity_resilience", "weak_relevance", "industry_context", TREND_URL, title=TREND_TITLE),
            _row("cloud_ai_infrastructure", "weak_relevance", "industry_context", TREND_URL, title=TREND_TITLE),
            _row("network_connectivity", "weak_relevance", "stakeholder_context", PROFILE_URL,
                 title="Jane Example - Head of IT at Oman Airports Management Company"),
            _row("data_centre_infrastructure", "weak_relevance", "current_state_technology", DIRECTORY_URL,
                 title="Oman Airports Management Company - Products, Competitors, Financials"),
            _row("enterprise_managed_services", "weak_relevance", "incidental_mention", PARENT_URL,
                 title="Aviation Group Holding"),
        ],
        "incumbent_evidence": [
            _incumbent("Cisco", "named_vendor", "https://news.example/oman-airports-soc",
                       "Oman Airports deploys security operations center",
                       "Oman Airports selected Cisco to deploy the zero trust network across its terminals."),
            _incumbent("Huawei", "named_vendor", PROFILE_URL, "Jane Example",
                       "Reposted: #Huawei is proud to partner with #OmanAirports to create an intelligent campus."),
            _incumbent("partner with", "partnership_language", PROFILE_URL, "Jane Example",
                       "Reposted: #Huawei is proud to partner with #OmanAirports to create an intelligent campus."),
            _incumbent("Oracle", "named_vendor", PROFILE_URL, "Jane Example",
                       "Oracle PL/SQL Developer Certified Professional; ICT at Oman Airports Management Company"),
            _incumbent("collaborate with", "partnership_language", "https://uk.linkedin.com/in/other-person", "Other",
                       "I had the opportunity to collaborate with outstanding colleagues and stakeholders."),
            _incumbent("AWS", "named_vendor", "https://market.example/oman-digital-transformation-market",
                       "Oman Digital Transformation Market Size & Growth to 2031",
                       "Strategic partnerships with AWS, Google Cloud and Microsoft diversify technology stacks."),
            _incumbent("AWS", "named_vendor", TREND_URL, TREND_TITLE, "Airports use AWS and Azure for analytics."),
        ],
        "people": [
            _person("jane-example", "‏Jane Example‏", "confirmed", "technical_stakeholder",
                    title="‏Jane Example‏ - ‏Head of IT at ...", seniority="head"),
            _person("ceo-example", "Nasser Example", "confirmed", "decision_maker", title="Acting CEO", seniority="c_level",
                    functions=(), tech=()),
            _person("buyer-example", "Proc Example", "weak", "procurement_stakeholder",
                    title="Procurement Manager at Oman Airports Management Company", seniority="manager",
                    functions=("procurement",), tech=()),
            _person("stranger", "Unrelated Person", "unconfirmed", None),
            _person("biz", "Business Person", "confirmed", "business_stakeholder"),
        ],
        "opportunity_hypotheses": [],
    }
    report.update(overrides)
    return report


def _entry(report=None, ai=None):
    return {"report": report if report is not None else _report(), "ai_synthesis": ai or {}, "opportunity_hypotheses": []}


def _html(view) -> str:
    return "\n".join(understand_sections_html(view))


# ---------------------------------------------------------------------------
# 1. Developments: ranked account signals, grouped by class
# ---------------------------------------------------------------------------

def test_developments_are_grouped_and_exclude_context_directory_and_profiles():
    view = build_understand_view(_entry())
    urls = {d.source_url for group in view.developments.values() for d in group}
    assert "https://news.example/oman-airports-soc" in {d.source_url for d in view.developments["technology_procurement"]}
    assert "https://news.example/oman-airports-acting-ceo" in {d.source_url for d in view.developments["strategic"]}
    assert "https://docs.example/oamc-strategy" in {d.source_url for d in view.developments["general"]}
    for excluded in (TREND_URL, PROFILE_URL, PARENT_URL, f"{OWN}/en/content/about"):
        assert excluded not in urls, excluded
    assert view.background_count == 1  # the static company-profile statement


def test_developments_use_the_same_ranking_as_the_executive_summary():
    report = _report()
    es_top, es_count = rank_key_signals(report)
    signal_set = sig.normalize_account_signals(report)
    assert [k.source_url for k in signal_set.ranked[: len(es_top)]] == [k.source_url for k in es_top]
    assert signal_set.meaningful_count == es_count
    view = build_understand_view(_entry(report))
    assert sum(view.development_counts.values()) == len(signal_set.ranked)


def test_understand_does_not_depend_on_the_executive_summary_module():
    src = (ROOT / "ui" / "understand_view.py").read_text(encoding="utf-8")
    assert "executive_summary" not in src
    for forbidden in ("from research", "from ai", "run_acquisition", "synthesize_account", "requests", "urllib.request",
                      "map_source_to_capabilities", "build_opportunity_hypotheses"):
        assert forbidden not in src, forbidden
    assert re.search(r"^import streamlit", src, re.M) is None, "streamlit is imported only inside the renderer"


# ---------------------------------------------------------------------------
# Parent / group-site reporting
# ---------------------------------------------------------------------------

def test_parent_site_news_is_a_separate_reported_news_tier():
    view = build_understand_view(_entry())
    assert [d.source_url for d in view.reported_news] == [PARENT_URL]
    item = view.reported_news[0]
    assert item.provenance == sig.REPORTED_NEWS and item.strength == "reported news"
    assert item.excerpt.startswith("Oman Airports Signs Strategic Partnership with Bank Muscat")
    assert "01 Dec" not in item.excerpt and "At a Glance" not in item.excerpt
    # never ranked with account signals, never in the executive key signals
    assert PARENT_URL not in {k.source_url for k in rank_key_signals(_report())[0]}
    assert any("not direct account evidence" in s for s in view.meaning)


def test_parent_site_news_never_creates_account_need_or_opportunity():
    view = build_understand_view(_entry())
    ems = next(p for p in view.posture if p.capability_id == "enterprise_managed_services")
    assert ems.counts["account_need"] == 0 and ems.status == STATUS_CONTEXT
    assert "opportunit" not in _html(view).lower().replace("not an opportunity", "").replace("never an opportunity", "")


def test_reported_news_requires_the_account_as_headline_subject_and_no_listing():
    org, url = ORG, "https://other.example/news"
    assert sig.is_reported_news("partnership", "Oman Airports signs an agreement with a bank.", "Group news", url, org, COUNTRY, OWN)
    assert not sig.is_reported_news("partnership", "A bank signs an agreement covering airports such as Oman Airports.",
                                    "Group news", url, org, COUNTRY, OWN)
    assert not sig.is_reported_news("partnership", "Oman Airports signs an agreement.", "Latest Oman Tenders",
                                    url, org, COUNTRY, OWN)
    assert not sig.is_reported_news("partnership", "Oman Airports signs X 03 Oct 2026 18 Oct 2026 View Detail",
                                    "Group", url, org, COUNTRY, OWN)
    assert not sig.is_reported_news("partnership", "Oman Airports signs an agreement.", "x", f"{OWN}/news", org, COUNTRY, OWN)
    assert not sig.is_reported_news("partnership", "Oman Airports signs an agreement.", "x", PROFILE_URL, org, COUNTRY, OWN)


def test_no_payment_vocabulary_was_added_and_payment_news_maps_to_no_capability():
    keywords = " ".join(k.lower() for c in get_catalog() for k in c["keywords"])
    for term in ("payment", "fintech", "direct debit", "pos terminal"):
        assert term not in keywords, term
    assert sig.specific_technology_terms("Implement Advanced Direct Payment System with Bank Muscat") == []


# ---------------------------------------------------------------------------
# 3. Capability posture
# ---------------------------------------------------------------------------

def test_capability_posture_buckets_and_single_status_per_capability():
    view = build_understand_view(_entry())
    by_id = {p.capability_id: p for p in view.posture}
    assert set(by_id) >= {c["id"] for c in get_catalog()}
    cyber = by_id["cybersecurity_resilience"]
    assert cyber.status == STATUS_EVIDENCE and cyber.counts["account_need"] == 1 and cyber.counts["market"] == 1
    assert by_id["cloud_ai_infrastructure"].status == STATUS_CONTEXT          # industry context only
    assert by_id["network_connectivity"].status == STATUS_MONITOR             # stakeholder context only
    dc = by_id["data_centre_infrastructure"]                                  # directory page => not current state
    assert dc.counts["current_state"] == 0 and dc.status == STATUS_CONTEXT
    for p in view.posture:
        assert p.status in (STATUS_EVIDENCE, STATUS_MONITOR, STATUS_CONTEXT, STATUS_NONE)
        assert set(p.counts) == {"account_need", "current_state", "stakeholder", "market"}


def test_weak_or_context_rows_never_reach_evidence_identified():
    chain = [
        _row("network_connectivity", "weak_relevance", "organizational_technology_event", "https://news.example/a",
             title="Oman Airports network"),
        _row("cybersecurity_resilience", "direct_evidence", "industry_context", TREND_URL, title=TREND_TITLE),
        _row("cloud_ai_infrastructure", "strong_inference", "procurement_signal", TREND_URL, title=TREND_TITLE),
    ]
    view = build_understand_view(_entry(_report(chain=chain)))
    assert all(p.status != STATUS_EVIDENCE for p in view.posture)
    assert any("No confirmed Otech need" in u for u in view.unknowns)


def test_strong_inference_is_evidence_but_reported_as_inference_only():
    chain = [_row("network_connectivity", "strong_inference", "procurement_signal", "https://news.example/tender",
                  signal_type="procurement_activity", title="Oman Airports issues network tender")]
    view = build_understand_view(_entry(_report(chain=chain)))
    net = next(p for p in view.posture if p.capability_id == "network_connectivity")
    assert net.status == STATUS_EVIDENCE and net.best_level == "strong_inference"
    assert any("No confirmed Otech need" in u for u in view.unknowns)
    assert any("inference only" in u and "Network & Connectivity" in u for u in view.unknowns)


def test_posture_never_implies_an_opportunity():
    html = _html(build_understand_view(_entry()))
    posture = html[html.index("Otech capability posture"):html.index("Technology &amp; incumbent context")]
    assert "opportunity" not in posture.lower().replace("not an opportunity", "").replace("never an opportunity", "")
    assert "WEAK / CONTEXT" not in html and "CONFIRMED ?" not in html


def test_no_current_signal_when_capability_has_no_rows():
    view = build_understand_view(_entry(_report(chain=[])))
    assert {p.status for p in view.posture} == {STATUS_NONE}


# ---------------------------------------------------------------------------
# 4. Current state / incumbents
# ---------------------------------------------------------------------------

def test_named_vendors_need_account_specific_provenance():
    view = build_understand_view(_entry())
    items = {(c.entity, c.provenance) for c in view.current_state}
    assert ("Cisco", "reported") in items              # third-party news naming the account with the vendor
    assert ("Huawei", "stakeholder_reported") in items  # via the #OmanAirports compound alias
    assert not any(c.entity == "Oracle" for c in view.current_state), "a personal certification is not a relationship"
    assert not any(c.entity.lower() in ("partner with", "collaborate with") for c in view.current_state)
    for c in view.current_state:
        assert c.entity and c.relationship and c.provenance_label and c.source_url and c.excerpt
    cisco = next(c for c in view.current_state if c.entity == "Cisco")
    assert cisco.relationship == "Selected supplier"
    huawei = next(c for c in view.current_state if c.entity == "Huawei")
    assert huawei.relationship == "Partnership"


def test_market_only_vendors_are_collapsed_into_market_landscape():
    view = build_understand_view(_entry())
    assert [v.entity for v in view.market_landscape] == ["AWS"]
    assert view.market_landscape[0].source_count == 2
    html = _html(view)
    assert "Market landscape" in html and "not account evidence" in html


def test_account_site_vendor_is_account_stated():
    report = _report(incumbent_evidence=[_incumbent("Fortinet", "named_vendor", f"{OWN}/en/news/firewall", "Firewall upgrade",
                                                    "We deployed Fortinet firewalls across all terminals.")])
    view = build_understand_view(_entry(report))
    assert [(c.entity, c.provenance_label, c.relationship) for c in view.current_state] == [
        ("Fortinet", "Account-stated", "Deployment")]


def test_unlinked_linkedin_profile_vendor_mentions_are_omitted_not_market():
    report = _report(people=[], incumbent_evidence=[
        _incumbent("Huawei", "named_vendor", "https://om.linkedin.com/in/someone",
                   "Someone - Other Company", "We are delighted to have Huawei joining us at Airports Innovate.")])
    view = build_understand_view(_entry(report))
    assert view.current_state == [] and view.market_landscape == [] and view.omitted_incumbent_mentions == 1


# ---------------------------------------------------------------------------
# 5. People
# ---------------------------------------------------------------------------

def test_people_grouped_with_clean_structured_fields_only():
    view = build_understand_view(_entry())
    assert view.people_available
    assert [p.name for p in view.people_groups["technical"]] == ["Jane Example"]
    assert [p.name for p in view.people_groups["decision_makers"]] == ["Nasser Example"]
    assert [p.name for p in view.people_groups["procurement"]] == ["Proc Example"]
    jane = view.people_groups["technical"][0]
    assert "‏" not in jane.name and jane.title == "Head of IT"
    assert jane.seniority == "Head" and jane.association_label == "Confirmed association" and jane.confidence == 70.0
    assert "IT" in jane.areas and "Network" in jane.areas
    proc = view.people_groups["procurement"][0]
    assert proc.title == "Procurement Manager" and proc.association_label == "Likely association"
    assert view.people_unconfirmed_count == 1 and view.people_other_count == 1 and view.people_weak_count == 1
    html = _html(view)
    assert "Unrelated Person" not in html
    assert "RAW PROFILE MARKDOWN" not in html and "## Experience" not in html
    assert "‏" not in html


def test_people_section_when_people_intelligence_was_not_run():
    report = _report()
    del report["people"]
    view = build_understand_view(_entry(report))
    assert not view.people_available
    html = _html(view)
    assert "People Intelligence was not run for this report." in html
    buying = {b.key: b for b in view.buying}
    assert buying["decision_makers"].status == UNKNOWN and buying["procurement_stakeholders"].status == UNKNOWN
    assert any("People Intelligence was not run" in u for u in view.unknowns)


def test_people_section_when_no_people_were_found():
    view = build_understand_view(_entry(_report(people=[])))
    assert view.people_available and all(g == [] for g in view.people_groups.values())
    assert "None identified." in _html(view)


@pytest.mark.parametrize("title,expected", [
    ("‏Basim Example‏ - ‏Vice President - Infrastructure & Security at ...", "Vice President - Infrastructure & Security"),
    ("Basim Example - CIO | LinkedIn", "CIO"),
    ("ICT Systems Manager at Oman Airports Management Company", "ICT Systems Manager"),
    ("Oman Airports Management Company", ""),
    ("Head of Cloud", "Head of Cloud"),
])
def test_person_title_cleanup(title, expected):
    assert clean_person_title({"name": "‏Basim Example‏", "current_title": title}, ORG, COUNTRY) == expected


def test_clean_text_removes_invisible_characters_and_styled_letters():
    assert clean_text("‏‫Name‬⁩ ﻿") == "Name"
    assert clean_text("\U0001d5d7\U0001d5f6\U0001d600\U0001d5f0\U0001d5fc\U0001d603\U0001d5f2\U0001d5ff") == "Discover"


# ---------------------------------------------------------------------------
# 6. Buying context
# ---------------------------------------------------------------------------

def test_buying_context_items_are_labelled_and_never_invent_budget_or_timeline():
    view = build_understand_view(_entry())
    buying = {b.key: b for b in view.buying}
    assert list(buying) == ["procurement_channels", "procurement_stakeholders", "decision_makers", "governance",
                            "incumbent_relationships", "active_buying", "budget", "timeline"]
    assert all(b.status in ("observed", "inferred", "unknown") for b in view.buying)
    assert buying["budget"].status == UNKNOWN and buying["timeline"].status == UNKNOWN
    assert buying["active_buying"].status == UNKNOWN and buying["procurement_channels"].status == UNKNOWN
    assert buying["decision_makers"].status == OBSERVED
    assert buying["procurement_stakeholders"].status == "inferred"       # weak association only
    assert buying["governance"].status == OBSERVED and "affiliated with" in buying["governance"].detail
    assert buying["incumbent_relationships"].status == OBSERVED            # Cisco, reported with the account
    unknowns = " ".join(view.unknowns)
    for gap in ("No budget", "No buying timeline", "No active RFP", "Supported only by market / industry context",
                "Stakeholder relationships are unverified"):
        assert gap in unknowns, gap


def test_budget_timeline_and_active_buying_observed_only_from_account_procurement_evidence():
    tender_url = "https://tenders.example/notice/1"
    report = _report(
        signals=[_signal("procurement_activity",
                         "Oman Airports has issued a tender for a data center network upgrade valued at OMR 4.5 million; "
                         "the closing date is 30 November.", tender_url, "Oman Airports issues data center tender")],
        chain=[_row("data_centre_infrastructure", "strong_inference", "procurement_signal", tender_url,
                    signal_type="procurement_activity", title="Oman Airports issues data center tender")],
    )
    buying = {b.key: b for b in build_understand_view(_entry(report)).buying}
    assert buying["active_buying"].status == OBSERVED
    assert buying["budget"].status == OBSERVED and "OMR 4.5 million" in buying["budget"].detail
    assert buying["timeline"].status == OBSERVED and "closing date" in buying["timeline"].detail.lower()
    assert buying["procurement_channels"].status == OBSERVED


def test_amounts_in_non_procurement_or_context_text_are_never_a_budget():
    report = _report(signals=[
        _signal("expansion", "Oman Airports announced the expansion of the terminal, a USD 900 million project by 2028.",
                "https://news.example/expansion", "Oman Airports expansion"),
        _signal("procurement_activity", "Airports such as Oman Airports and others will spend USD 2 billion by 2030.",
                TREND_URL, TREND_TITLE),
    ], chain=[])
    buying = {b.key: b for b in build_understand_view(_entry(report)).buying}
    assert buying["budget"].status == UNKNOWN and buying["timeline"].status == UNKNOWN
    assert buying["active_buying"].status == UNKNOWN


# ---------------------------------------------------------------------------
# 2 + AI narrative
# ---------------------------------------------------------------------------

def test_ai_narrative_is_labelled_separately_from_deterministic_facts():
    view = build_understand_view(_entry(ai={"status": "ok", "account_narrative": "The account is modernising security."}))
    html = _html(view)
    assert "AI interpretation" in html and "Deterministic summary" in html
    assert html.index("Deterministic summary") < html.index("The account is modernising security.")


@pytest.mark.parametrize("ai", [{}, {"status": "ai_unconfigured", "account_narrative": ""},
                                {"status": "ai_failed", "account_narrative": "stale text"}])
def test_deterministic_narrative_renders_without_ai(ai):
    view = build_understand_view(_entry(ai=ai))
    assert view.ai_narrative == ""
    assert view.meaning and view.meaning[0].startswith("Oman Airports is showing")
    html = _html(view)
    assert "stale text" not in html and "Not available for this run" in html


def test_empty_entry_renders_honestly():
    view = build_understand_view({})
    html = _html(view)
    assert "does not show account-specific developments" in " ".join(view.meaning)
    assert html.count('class="ar-panel ar-u-section"') == 7


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def test_renderer_answers_the_seven_questions_in_order():
    html = _html(build_understand_view(_entry()))
    titles = ["What is happening?", "What does it mean?", "Otech capability posture", "Technology &amp; incumbent context",
              "Who matters?", "Buying &amp; engagement context", "What we don&#x27;t know"]
    positions = [html.index(t) for t in titles]
    assert positions == sorted(positions)


def test_renderer_escapes_text_and_has_no_mojibake_separators():
    report = _report()
    report["signals"][1]["text"] = "Oman Airports is deploying <script>alert(1)</script> a security operations center."
    html = _html(build_understand_view(_entry(report)))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert " ? " not in html and "�" not in html


def test_renderer_never_dumps_raw_dicts_or_lists():
    html = _html(build_understand_view(_entry()))
    assert "{'" not in html and "['" not in html and "None" not in re.sub(r"<[^>]+>", "", html).split()


def test_app_understand_tab_uses_the_new_view():
    src = APP.read_text(encoding="utf-8")
    body = src[src.index("def _render_understand"):src.index("def _render_decide")]
    assert "build_understand_view" in body and "render_understand" in body
    assert "WEAK / CONTEXT" not in body and " ? " not in body
    assert 'get("evidence")' not in body and "buying_context" not in body


def _apptest():
    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(APP), default_timeout=30)


def test_understand_tab_renders_in_the_app():
    at = _apptest()
    at.session_state["entry"] = _entry(ai={"status": "ok", "account_narrative": "AI says something."})
    at.session_state["loaded_from"] = "live research"
    at.run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    for text in ("Otech capability posture", "Reported news", "Market landscape", "Buying &amp; engagement context",
                 "AI interpretation", "Jane Example"):
        assert text in html, text
    assert "RAW PROFILE MARKDOWN" not in html
    assert "Executive Account Summary" in html or "ar-exec" in html  # executive layout still present


def test_understand_tab_renders_without_people_or_ai():
    report = _report()
    del report["people"]
    at = _apptest()
    at.session_state["entry"] = _entry(report)
    at.session_state["loaded_from"] = "live research"
    at.run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "People Intelligence was not run for this report." in html
    assert "Deterministic summary" in html


# ---------------------------------------------------------------------------
# Aliases: deterministic hashtag / compound forms
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("#Huawei is proud to partner with #OmanAirports to create", True),
    ("OmanAirports announced a new terminal", True),
    ("Great day at #OmanAirports.", True),
    ("#OmanAirportsManagementCompany team", True),
    ("visit omanairports.co.om for details", False),     # domain, not a mention
    ("#omanairports", False),                              # case differs from the name: not matched
    ("XOmanAirports", False),                              # inside a longer token
    ("@OmanAirports", False),                              # handle, not a name form
    ("such as #OmanAirports and others", False),          # exemplar
    ("Oman Airport is mentioned", False),                  # no fuzzy matching
])
def test_hashtag_and_compound_aliases(text, expected):
    tokens = _org_core_tokens(ORG, country=COUNTRY)
    assert _mentions_organization(text, ORG, tokens, country=COUNTRY) is expected


def test_compound_aliases_are_derived_from_the_name_for_any_account():
    assert compound_aliases(ORG, country=COUNTRY) == ["OmanAirportsManagementCompany", "OmanAirports"]
    name = "Meridian Ports Authority"
    compounds = compound_aliases(name, country="Testland")
    assert compounds and compounds[0] == "MeridianPortsAuthority"
    assert _mentions_organization("Thanks #MeridianPortsAuthority!", name, _org_core_tokens(name, country="Testland"),
                                  country="Testland")
    assert compound_aliases("Acme", country=None) == []  # single word: no compound form


# ---------------------------------------------------------------------------
# AI prompt: ranked account-specific signals, not crawl order
# ---------------------------------------------------------------------------

def test_ai_prompt_uses_ranked_account_signals_not_crawl_order():
    from ai.prompt import _signals_block

    report = _report()
    fake = SimpleNamespace(signals=[SimpleNamespace(**s) for s in report["signals"]], to_dict=lambda: report)
    block = _signals_block(fake)
    lines = block.splitlines()
    assert "news.example/oman-airports-soc" in lines[0]            # strongest first, not the first crawled
    assert TREND_URL not in block and PROFILE_URL not in block and f"{OWN}/en/content/about" not in block
    parent = [line for line in lines if PARENT_URL in line]
    assert parent and "NOT direct account evidence" in parent[0]


def test_ai_prompt_when_no_account_specific_signal_exists():
    from ai.prompt import _signals_block

    report = _report(signals=[_signal("technology_initiative", "Airports such as Oman Airports adopt zero trust.",
                                      TREND_URL, TREND_TITLE)], chain=[])
    fake = SimpleNamespace(signals=[SimpleNamespace(**s) for s in report["signals"]], to_dict=lambda: report)
    assert _signals_block(fake).startswith("(none account-specific")


# ---------------------------------------------------------------------------
# Regressions through the real (offline) evidence pipeline
# ---------------------------------------------------------------------------

def _src(url, title, content, source_type="news"):
    return Source(
        url=url, domain=urlparse(url).netloc, title=title, source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic",
        content=content, access_status=AccessStatus.FETCHED,
    )


PIPELINE_SOURCES = [
    _src(TREND_URL, TREND_TITLE,
         "Airport technology trends for 2026. Airports such as Oman Airports and others are deploying OT cybersecurity, "
         "zero trust network segmentation, cloud data platforms and AI video analytics across terminals. "
         "Operators invest in data center capacity and managed security services.", source_type="technology"),
    _src(LISTING_URL, "Latest Oman Airports Tenders",
         "Reporting Non-Communicative Services Prices Vietnam 03 Oct 2026 18 Oct 2026 View Detail Lidar -Based "
         "Sensor-Based Boarding Supervision (Sbbs) For Boarding Gates In Terminal 1 Delivery, Implementation, Operation "
         "And Maintenance At Frankfurt/Main Airport, Terminal 1. Event And Kpi Data Must Be Integrated Via Api And Mqtt "
         "network and data center interfaces. The Rollout Is Planned For A Total Of Up To 30 Gates.",
         source_type="procurement"),
    _src(DIRECTORY_URL, "Oman Airports Management Company - Products, Competitors, Financials, Employees",
         "Oman Airports Management Company operates airports in Oman. What is Oman Airports Management Company's latest "
         "funding round? Oman Airports Management Company uses cloud, network and cybersecurity technology. "
         "Headcount and investors of Oman Airports Management Company.", source_type="directory"),
    _src(PROFILE_URL, "Jane Example - Head of IT Infrastructure at Oman Airports Management Company",
         "Head of IT Infrastructure at Oman Airports Management Company. Recent activity: Reposted: #Huawei is proud to "
         "partner with #OmanAirports to create an intelligent campus with 5G network, cloud and data center services.",
         source_type="linkedin_profile"),
    _src(PARENT_URL, "Aviation Group Holding",
         "Aviation Group Holding elevating aviation excellence. 10 Mar News Oman Airports Signs Strategic Partnership "
         "with Bank Muscat to Implement Advanced Direct Payment System 01 Dec At a Glance our impact in numbers, "
         "investment in technology and digital platforms across the group.", source_type="news"),
]


@pytest.fixture(scope="module")
def pipeline_entry():
    sources = PIPELINE_SOURCES
    mappings = map_all_sources(sources, organization_name=ORG, country=COUNTRY)
    report = AccountRadarReport(
        organization_name=ORG, country=COUNTRY, website=OWN, linkedin_company_url=None,
        qualification=QualificationResult(qualified_sources=list(sources)),
        signals=extract_all_signals(sources),
        capability_mappings=mappings,
        incumbent_evidence=detect_all_incumbent_evidence(sources),
        opportunity_hypotheses=build_opportunity_hypotheses(mappings),
        people=build_people([s for s in sources if s.source_type == "linkedin_profile"], organization_name=ORG),
        acquisition_diagnostics={},
    )
    opportunities = build_opportunity_hypotheses(mappings, max_items=5)
    return {"report": report.to_dict(), "ai_synthesis": {},
            "opportunity_hypotheses": [o.to_dict() for o in opportunities]}, mappings


def test_pipeline_regressions_hold_for_understand(pipeline_entry):
    entry, mappings = pipeline_entry
    report = entry["report"]
    # weak relevance never becomes an opportunity; nothing here is account evidence
    assert entry["opportunity_hypotheses"] == [] and report["opportunity_hypotheses"] == []
    by_url: dict[str, list[dict]] = {}
    for row in report["chain"]:
        by_url.setdefault(row["source_url"], []).append(row)
    # iFactory-style trend article stays industry context
    assert by_url[TREND_URL] and all(r["evidence_category"] == "industry_context" for r in by_url[TREND_URL])
    # Frankfurt listing is never attributed to the account
    assert all(r["evidence_level"] == "weak_relevance" for r in by_url.get(LISTING_URL, []))
    # LinkedIn personal profile stays stakeholder context
    assert all(r["evidence_category"] == "stakeholder_context" for r in by_url.get(PROFILE_URL, []))
    # parent-site news is not direct account evidence
    assert all(r["evidence_level"] != "direct_evidence" for r in by_url.get(PARENT_URL, []))

    view = build_understand_view(entry)
    dev_urls = {d.source_url for g in view.developments.values() for d in g}
    assert not dev_urls & {TREND_URL, LISTING_URL, DIRECTORY_URL, PROFILE_URL, PARENT_URL}
    assert PARENT_URL in {d.source_url for d in view.reported_news}
    assert LISTING_URL not in {d.source_url for d in view.reported_news}
    assert all(p.status != STATUS_EVIDENCE for p in view.posture)
    assert {b.key: b for b in view.buying}["active_buying"].status == UNKNOWN
    huawei = [c for c in view.current_state if c.entity == "Huawei"]
    assert huawei and huawei[0].provenance == "stakeholder_reported"


def test_directory_boilerplate_stays_background(pipeline_entry):
    entry, _ = pipeline_entry
    signal_set = sig.normalize_account_signals(entry["report"])
    assert DIRECTORY_URL not in {k.source_url for k in signal_set.ranked}
    assert DIRECTORY_URL not in {k.source_url for k in signal_set.reported_news}
    view = build_understand_view(entry)
    assert all(p.counts["current_state"] == 0 for p in view.posture)


# ---------------------------------------------------------------------------
# Real saved corpus (skipped when the corpus file is not present)
# ---------------------------------------------------------------------------

def _saved_corpus() -> Path | None:
    found = sorted(ROOT.glob("evidence_corpus_*.json"))
    return found[0] if found else None


@pytest.mark.skipif(_saved_corpus() is None, reason="no saved evidence corpus in the project folder")
def test_real_saved_corpus_invariants():
    from compare_evidence_fix import _source_from_dict

    saved = json.loads(_saved_corpus().read_text(encoding="utf-8"))
    sources = [_source_from_dict(d) for d in saved["sources"]]
    from research.analysis.qualification import qualify_corpus

    qualified = qualify_corpus(sources).qualified_sources
    org, country = saved["organization_name"], saved["country"]
    mappings = map_all_sources(qualified, organization_name=org, country=country)
    report = AccountRadarReport(
        organization_name=org, country=country, website=None, linkedin_company_url=None,
        qualification=QualificationResult(qualified_sources=qualified), signals=extract_all_signals(qualified),
        capability_mappings=mappings, incumbent_evidence=detect_all_incumbent_evidence(qualified),
        opportunity_hypotheses=build_opportunity_hypotheses(mappings),
        people=build_people([s for s in sources if s.source_type == "linkedin_profile"], organization_name=org),
        acquisition_diagnostics={},
    ).to_dict()
    view = build_understand_view({"report": report})
    confirmed = [r for r in report["chain"] if r["evidence_level"] == "direct_evidence"]
    if not confirmed:
        assert any("No confirmed Otech need" in u for u in view.unknowns)
    for d in view.reported_news:
        assert d.provenance == sig.REPORTED_NEWS
    html = _html(view)
    assert " ? " not in html and "## Experience" not in html and "‏" not in html

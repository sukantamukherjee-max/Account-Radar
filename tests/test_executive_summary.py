"""Executive Account Summary tests (ui/executive_summary.py + its renderer).

The executive view is presentation logic over the frozen Evidence
Intelligence layer. These tests pin the evidence rules it must never break:

  * industry context is never presented as account evidence;
  * industry_context (or any context category) never yields a CONFIRMED
    opportunity, even if an opportunity record claims confirmation;
  * confirmed_evidence vs inference_hypothesis is preserved;
  * every opportunity shown keeps its evidence level, confidence, epistemic
    status and source;
  * weak relevance never becomes an opportunity;
  * nothing is invented -- people come only from People Intelligence,
    sentences only from counts/labels/verbatim excerpts.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from ui.executive_summary import (
    HIGH_CONFIDENCE_THRESHOLD,
    MAX_KEY_SIGNALS,
    MAX_STAKEHOLDERS,
    build_executive_summary,
    clean_excerpt,
    display_title,
    rank_key_signals,
    select_stakeholders,
    split_opportunities,
)

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
ORG = "Oman Airports Management Company"
OWN = "https://www.omanairports.co.om"

import streamlit as _REAL_STREAMLIT  # noqa: E402  (see tests/test_theme_engine.py)


# ---------------------------------------------------------------------------
# Fixture builders (shapes mirror AccountRadarReport.to_dict())
# ---------------------------------------------------------------------------

def _signal(kind, text, url, title=""):
    return {"signal_type": kind, "text": text, "source_url": url, "source_title": title, "matched_terms": []}


def _row(cap, level, category, url, *, signal_type=None, confidence=60.0, title="t", excerpt=None):
    status = "confirmed_evidence" if level == "direct_evidence" else "inference_hypothesis"
    return {
        "customer_signal": {"signal_type": signal_type, "text": None},
        "evidence_excerpt": excerpt,
        "business_impact": f"{cap} impact.",
        "technology_requirement": "req",
        "potential_otech_capability": {"capability_id": cap, "capability_name": cap.replace("_", " ").title()},
        "evidence_level": level,
        "confidence": confidence,
        "source_url": url,
        "source_title": title,
        "supporting_context": "context",
        "matched_terms": [],
        "incumbent_context": [],
        "evidence_category": category,
        "inference_cluster_id": url,
        "epistemic_status": status,
    }


def _opp(cap, level, status, confidence, urls):
    return {
        "capability_id": cap, "capability_name": cap.replace("_", " ").title(), "priority": "medium",
        "score": confidence * 0.7, "evidence_level": level, "confidence": confidence,
        "evidence_count": len(urls), "independent_source_count": len(urls),
        "hypothesis": "h", "rationale": "r", "who": "w", "conversation": "c", "next_step": "n",
        "source_urls": urls, "incumbent_context": [], "independent_domain_count": 1,
        "inference_cluster_count": 1, "epistemic_status": status,
    }


def _person(pid, name, assoc, *, functions=(), tech=(), score=50, seniority="director", title="Director of IT"):
    return {
        "person_id": pid, "name": name, "current_title": title, "organization_name": ORG,
        "organization_association": assoc, "linkedin_url": f"https://om.linkedin.com/in/{pid}",
        "source_urls": [], "source_types": [], "location": None, "seniority": seniority,
        "functions": list(functions), "technology_areas": list(tech), "influence_type": None,
        "relevance_score": score, "confidence": 60, "evidence": [], "recent_signals": [], "chips": [],
    }


TREND_URL = "https://ifactoryapp.com/industries/aviation-management/airport-technology-trends-2026"
TREND_TITLE = "10 Airport Technology Trends Transforming Aviation Operations in 2026"
NEWS_URL = "https://news.example/oman-airports-soc"
NEWS_TITLE = "Oman Airports deploys new security operations center"


def _report(**overrides):
    report = {
        "organization_name": ORG,
        "country": "Oman",
        "website": OWN,
        "signals": [
            _signal("technology_initiative", "Oman Airports is deploying a security operations center across its airports.",
                    NEWS_URL, NEWS_TITLE),
            _signal("technology_initiative", "Oman Airports is deploying OT cybersecurity and zero trust.",
                    TREND_URL, TREND_TITLE),
            _signal("expansion", "Oman Airports announced the expansion of Muscat International Airport terminal.",
                    f"{OWN}/en/media/news/expansion", "Terminal expansion"),
        ],
        "chain": [
            _row("cybersecurity_resilience", "direct_evidence", "organizational_technology_event", NEWS_URL,
                 signal_type="technology_initiative", confidence=90.0, title=NEWS_TITLE,
                 excerpt="Oman Airports is deploying a security operations center across its airports."),
            _row("cybersecurity_resilience", "weak_relevance", "industry_context", TREND_URL,
                 confidence=25.0, title=TREND_TITLE),
        ],
        "opportunity_hypotheses": [],
        "people": [],
    }
    report.update(overrides)
    return report


# ---------------------------------------------------------------------------
# Evidence rule 1: industry context is never account evidence
# ---------------------------------------------------------------------------

def test_industry_context_source_never_becomes_a_key_signal():
    signals, _ = rank_key_signals(_report())
    assert signals, "the genuine account signals must still be shown"
    assert all(s.source_url != TREND_URL for s in signals)


def test_generic_industry_title_is_excluded_even_without_any_chain_row():
    report = _report(chain=[])
    signals, count = rank_key_signals(report)
    assert all(s.source_url != TREND_URL for s in signals)
    assert count == 2  # news + first-party expansion, not the trend article


def test_context_sources_are_counted_as_background_not_evidence():
    es = build_executive_summary(_report(), [])
    assert es.context_source_count == 1
    assert any("background only" in s for s in es.summary_sentences)


def test_third_party_listing_naming_account_in_passing_is_not_an_account_signal():
    listing = _signal("procurement_activity", "Payment to: Oman Airports Management Company account for tender fees.",
                      "https://tenders.example/office-furniture", "Supply of office furniture")
    signals, count = rank_key_signals(_report(signals=[listing], chain=[]))
    assert signals == [] and count == 0


def test_account_named_only_as_an_example_is_not_an_account_signal():
    exemplar = _signal("technology_initiative", "Airports such as Oman Airports are deploying new systems.",
                       "https://vendor.example/blog/oman-airports-case", "Oman Airports and peers")
    assert rank_key_signals(_report(signals=[exemplar], chain=[]))[0] == []


def test_linkedin_personal_profile_signals_are_not_account_signals():
    profile = _signal("leadership_change", "Oman Airports appointed me as head of IT.",
                      "https://om.linkedin.com/in/someone", "Someone - Oman Airports")
    assert rank_key_signals(_report(signals=[profile], chain=[]))[0] == []


# ---------------------------------------------------------------------------
# Key signal ranking
# ---------------------------------------------------------------------------

def test_key_signals_are_ranked_by_evidence_strength_and_capped():
    many = [
        _signal("partnership", f"Oman Airports signed partnership number {i}.", f"{OWN}/news/p{i}", "News")
        for i in range(8)
    ]
    report = _report(signals=many + _report()["signals"])
    signals, count = rank_key_signals(report)
    assert len(signals) == MAX_KEY_SIGNALS
    assert signals[0].source_url == NEWS_URL and signals[0].strength == "confirmed evidence"
    ranks = [s.strength_rank for s in signals]
    assert ranks == sorted(ranks, reverse=True)
    assert count == 10  # 8 partnerships + news + first-party expansion (trend excluded)


def test_meaningful_count_deduplicates_same_source_and_type():
    dup = [_signal("expansion", f"Oman Airports expansion detail {i}.", f"{OWN}/news/x", "x") for i in range(4)]
    _, count = rank_key_signals(_report(signals=dup, chain=[]))
    assert count == 1


def test_clean_excerpt_trims_partial_words_without_rewriting():
    assert clean_excerpt("stered distributor of mutual funds") == "…distributor of mutual funds"
    long = "Oman Airports " + "word " * 80
    out = clean_excerpt(long, max_chars=60)
    assert out.endswith("…") and len(out) <= 62 and out.startswith("Oman Airports")


def test_clean_excerpt_drops_bare_links():
    assert "http" not in clean_excerpt("Oman Airports update https://lnkd.in/abc expansion plan")


def test_display_title_drops_repeated_name_and_linkedin_suffix_only():
    p = {"name": "Jane Doe", "current_title": "Jane Doe - Chief Information Officer - Meridian Ports | LinkedIn"}
    assert display_title(p) == "Chief Information Officer - Meridian Ports"
    assert display_title({"name": "A B", "current_title": "Head of IT"}) == "Head of IT"
    assert display_title({"name": "A B", "current_title": None}) == ""


# ---------------------------------------------------------------------------
# Evidence rules 2-3: confirmed vs hypothesis, weak never an opportunity
# ---------------------------------------------------------------------------

def test_confirmed_opportunity_keeps_its_evidence_and_confidence():
    opp = _opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 90.0, [NEWS_URL])
    confirmed, hypotheses = split_opportunities([opp], _report())
    assert len(confirmed) == 1 and hypotheses == []
    o = confirmed[0]
    assert o.confirmed and o.epistemic_status == "confirmed_evidence" and o.evidence_level == "direct_evidence"
    assert o.confidence == 90.0 and o.source_url == NEWS_URL and o.source_title == NEWS_TITLE
    assert o.evidence_category == "organizational_technology_event"
    assert "security operations center" in o.evidence_excerpt


def test_industry_context_can_never_be_a_confirmed_opportunity():
    """An opportunity record that claims confirmation but whose only
    supporting chain evidence is industry context is shown as a hypothesis,
    flagged as downgraded -- never as confirmed."""
    report = _report(chain=[
        _row("cloud_ai_infrastructure", "strong_inference", "business_signal_inference", "https://x.example/a"),
        _row("cloud_ai_infrastructure", "weak_relevance", "industry_context", TREND_URL, title=TREND_TITLE),
    ])
    opp = _opp("cloud_ai_infrastructure", "direct_evidence", "confirmed_evidence", 80.0, [TREND_URL])
    confirmed, hypotheses = split_opportunities([opp], report)
    assert confirmed == []
    h = hypotheses[0]
    assert h.downgraded and not h.confirmed
    assert h.epistemic_status == "inference_hypothesis" and h.evidence_level == "strong_inference"
    assert h.source_url != TREND_URL, "industry context is never the evidence shown for an opportunity"


def test_direct_row_with_context_category_does_not_confirm():
    report = _report(chain=[
        _row("network_connectivity", "direct_evidence", "industry_context", TREND_URL),
        _row("network_connectivity", "strong_inference", "business_signal_inference", "https://x.example/n"),
    ])
    opp = _opp("network_connectivity", "direct_evidence", "confirmed_evidence", 85.0, [TREND_URL])
    confirmed, hypotheses = split_opportunities([opp], report)
    assert confirmed == [] and hypotheses[0].downgraded


def test_inference_hypothesis_stays_a_hypothesis():
    report = _report(chain=[_row("enterprise_managed_services", "strong_inference", "business_signal_inference",
                                 "https://x.example/m", signal_type="project")])
    opp = _opp("enterprise_managed_services", "strong_inference", "inference_hypothesis", 60.0, ["https://x.example/m"])
    confirmed, hypotheses = split_opportunities([opp], report)
    assert confirmed == [] and not hypotheses[0].downgraded
    assert hypotheses[0].epistemic_status == "inference_hypothesis" and hypotheses[0].confidence == 60.0
    assert "project signal" in hypotheses[0].why


def test_weak_relevance_is_never_an_opportunity():
    weak = _opp("data_centre_infrastructure", "weak_relevance", "inference_hypothesis", 30.0, ["https://x.example/w"])
    confirmed, hypotheses = split_opportunities([weak], _report())
    assert confirmed == [] and hypotheses == []
    es = build_executive_summary(_report(), [weak])
    assert es.opportunity_count == 0


def test_every_shown_opportunity_carries_evidence_and_confidence():
    opps = [
        _opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 90.0, [NEWS_URL]),
        _opp("cloud_ai_infrastructure", "strong_inference", "inference_hypothesis", 55.0, ["https://x.example/c"]),
    ]
    report = _report()
    report["chain"].append(_row("cloud_ai_infrastructure", "strong_inference", "business_signal_inference",
                                "https://x.example/c", title="Cloud news"))
    es = build_executive_summary(report, opps)
    shown = es.confirmed_opportunities + es.hypothesis_opportunities
    assert len(shown) == 2
    for o in shown:
        assert o.confidence > 0 and o.evidence_level in ("direct_evidence", "strong_inference")
        assert o.epistemic_status in ("confirmed_evidence", "inference_hypothesis") and o.source_url


def test_high_confidence_counts_only_confirmed_opportunities_above_threshold():
    report = _report()
    report["chain"].append(_row("network_connectivity", "direct_evidence", "procurement_signal", "https://t.example/rfp"))
    opps = [
        _opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 90.0, [NEWS_URL]),
        _opp("network_connectivity", "direct_evidence", "confirmed_evidence", HIGH_CONFIDENCE_THRESHOLD - 1, ["https://t.example/rfp"]),
        _opp("cloud_ai_infrastructure", "strong_inference", "inference_hypothesis", 95.0, ["https://x.example/c"]),
    ]
    es = build_executive_summary(report, opps)
    assert es.high_confidence_count == 1
    assert es.opportunity_count == 3


# ---------------------------------------------------------------------------
# Who to engage -- existing People Intelligence only
# ---------------------------------------------------------------------------

def test_stakeholders_come_only_from_people_intelligence_and_exclude_unconfirmed():
    people = [
        _person("a", "Unlinked Person", "unconfirmed", functions=["cybersecurity"], score=99),
        _person("b", "Likely Person", "weak", functions=["it"], score=90),
        _person("c", "Confirmed Cyber Lead", "confirmed", functions=["cybersecurity"], score=70),
        _person("d", "Confirmed Finance", "confirmed", functions=[], score=80, title="CFO"),
    ]
    picked, count = select_stakeholders({"people": people}, ["cybersecurity_resilience"])
    names = [s.person["name"] for s in picked]
    assert "Unlinked Person" not in names and count == 3
    assert names[0] == "Confirmed Cyber Lead" and picked[0].relevant_capability == "cybersecurity_resilience"
    assert len(picked) <= MAX_STAKEHOLDERS


def test_people_not_available_is_reported_as_unknown_not_zero():
    report = _report()
    del report["people"]
    es = build_executive_summary(report, [])
    assert es.stakeholder_count is None and es.stakeholders == []


# ---------------------------------------------------------------------------
# Narrative and conversation -- templated, nothing invented
# ---------------------------------------------------------------------------

def test_summary_has_three_to_five_sentences_built_from_counts():
    opps = [_opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 90.0, [NEWS_URL])]
    report = _report(people=[_person("c", "Confirmed Cyber Lead", "confirmed", functions=["cybersecurity"])])
    es = build_executive_summary(report, opps)
    assert 3 <= len(es.summary_sentences) <= 5
    text = " ".join(es.summary_sentences)
    assert f"{es.meaningful_signal_count} account-specific signal" in text
    assert "Cybersecurity Resilience is backed by confirmed account evidence" in text
    assert "Confirmed Cyber Lead" in text


def test_empty_account_summary_is_honest():
    es = build_executive_summary(_report(signals=[], chain=[], people=[]), [])
    text = " ".join(es.summary_sentences)
    assert "does not yet show account-specific signals" in text
    assert "No Otech opportunity clears the evidence bar yet" in text
    assert es.conversation == "" and "No account-specific signal" in es.conversation_basis
    assert 3 <= len(es.summary_sentences) <= 5


def test_conversation_is_anchored_on_the_strongest_signal_and_respects_epistemics():
    report = _report(chain=_report()["chain"] + [
        _row("cloud_ai_infrastructure", "strong_inference", "business_signal_inference", "https://x.example/c")
    ])
    hyp = [_opp("cloud_ai_infrastructure", "strong_inference", "inference_hypothesis", 60.0, ["https://x.example/c"])]
    es = build_executive_summary(report, hyp)
    assert "technology initiative" in es.conversation
    assert "Cloud Ai Infrastructure" in es.conversation
    assert "not a confirmed requirement" in es.conversation_basis


def test_real_stored_reports_respect_all_invariants():
    """Smoke test over the three real accounts in phase1d_report.json."""
    from ui.data import extract_organizations

    accounts = extract_organizations(json.loads((ROOT / "phase1d_report.json").read_text(encoding="utf-8")))
    assert accounts
    for entry in accounts:
        report = entry["report"]
        es = build_executive_summary(report, entry.get("opportunity_hypotheses", report.get("opportunity_hypotheses")))
        assert len(es.key_signals) <= MAX_KEY_SIGNALS and 1 <= len(es.summary_sentences) <= 5
        context_urls = {
            r["source_url"] for r in report.get("chain", []) if r.get("evidence_category") == "industry_context"
        }
        assert not any(k.source_url in context_urls for k in es.key_signals)
        for o in es.confirmed_opportunities:
            assert o.evidence_level == "direct_evidence" and o.epistemic_status == "confirmed_evidence"
            assert o.evidence_category in ("organizational_technology_event", "procurement_signal")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _apptest():
    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(APP), default_timeout=30)


def test_executive_section_renders_below_the_account_header_with_all_parts():
    report = _report(
        people=[_person("c", "Confirmed Cyber Lead", "confirmed", functions=["cybersecurity"])],
        status="qualified",
        qualification={"qualified_count": 3, "disqualified_count": 0, "corpus_reason": ""},
        acquisition_diagnostics={"total_queries": 5},
        capability_summary={},
    )
    opps = [_opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 90.0, [NEWS_URL])]
    at = _apptest()
    at.session_state["entry"] = {"report": report, "ai_synthesis": {"status": "ok"}, "opportunity_hypotheses": opps}
    at.session_state["loaded_from"] = "live research"
    at.run()
    assert not at.exception
    blocks = [m.value for m in at.markdown]
    exec_html = next(b for b in blocks if 'class="ar-exec"' in b)
    header_idx = next(i for i, b in enumerate(blocks) if 'class="ar-acct"' in b)
    exec_idx = blocks.index(exec_html)
    # KPI cards render one per clickable container (class "ar-kpi ...").
    kpi_idx = next(i for i, b in enumerate(blocks) if 'class="ar-kpis"' in b or 'class="ar-kpi ' in b)
    assert header_idx < exec_idx < kpi_idx
    for part in ("Account pulse", "Executive summary", "Key signals", "Otech opportunities",
                 "Who to engage", "Suggested conversation", "Confirmed evidence", "Sales hypotheses"):
        assert part in exec_html, part
    assert "90%" in exec_html and NEWS_URL in exec_html and "Confirmed Cyber Lead" in exec_html
    assert TREND_TITLE not in exec_html, "industry context must not appear as account evidence"


def test_executive_section_escapes_report_text():
    report = _report(organization_name="<script>alert(1)</script> Airports", status="qualified",
                     qualification={"qualified_count": 1, "disqualified_count": 0}, capability_summary={})
    at = _apptest()
    at.session_state["entry"] = {"report": report, "ai_synthesis": {}, "opportunity_hypotheses": []}
    at.run()
    assert not at.exception
    exec_html = next(m.value for m in at.markdown if 'class="ar-exec"' in m.value)
    assert "<script>" not in exec_html


def test_executive_layer_never_imports_research_ai_or_streamlit():
    src = (ROOT / "ui" / "executive_summary.py").read_text(encoding="utf-8")
    for forbidden in ("import streamlit", "from research", "from ai", "run_acquisition", "synthesize_account", "_run_live"):
        assert forbidden not in src, forbidden
    assert not re.search(r"^\s*(def|class) .*\bmap_source_to_capabilities\b", src, re.M)


# ---------------------------------------------------------------------------
# Evidence Intelligence interface: the generic-industry check must be called
# with exactly the arguments its installed signature accepts.
# ---------------------------------------------------------------------------

def test_generic_industry_check_matches_the_installed_signature():
    import inspect

    import commercial.evidence_mapping as em
    import ui.executive_summary as es_mod

    params = inspect.signature(em._is_generic_industry_content).parameters
    calls = []

    def spy(*args, **kwargs):
        inspect.signature(em._is_generic_industry_content).bind(*args, **kwargs)  # raises TypeError on a bad call
        calls.append(kwargs)
        return em._is_generic_industry_content(*args, **kwargs)

    original = es_mod._is_generic_industry_content
    es_mod._is_generic_industry_content = spy
    try:
        signals, _ = rank_key_signals(_report())
    finally:
        es_mod._is_generic_industry_content = original
    assert calls, "the evidence layer's generic-industry rule must be consulted"
    if "organization_name" in params:
        assert all(c.get("organization_name") == ORG for c in calls)
    assert all(s.source_url != TREND_URL for s in signals)


def test_works_with_the_earlier_single_argument_generic_industry_api(monkeypatch):
    """The pre-fix layer exposed `_is_generic_industry_content(source)` with
    no keyword arguments. The executive view must not pass keywords it does
    not accept, and generic industry content must still never become a key
    account signal or confirm an opportunity."""
    import ui.executive_summary as es_mod
    from commercial.evidence_mapping import _GENERIC_INDUSTRY_CONTENT_RE, _URL_TITLE_SEPARATORS_RE

    def single_argument_api(source):  # the earlier installed signature
        haystack = " ".join(part for part in (source.url or "", source.title or "") if part)
        return bool(_GENERIC_INDUSTRY_CONTENT_RE.search(_URL_TITLE_SEPARATORS_RE.sub(" ", haystack)))

    monkeypatch.setattr(es_mod, "_is_generic_industry_content", single_argument_api)

    # Trend article with NO chain rows: only the title/URL rule can exclude it.
    signals, count = rank_key_signals(_report(chain=[]))
    assert all(s.source_url != TREND_URL for s in signals)
    assert count == 2

    opp_claiming_confirmation = _opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 80.0, [TREND_URL])
    report = _report(chain=[_row("cybersecurity_resilience", "weak_relevance", "industry_context", TREND_URL)])
    es = build_executive_summary(report, [opp_claiming_confirmation])
    assert es.confirmed_opportunities == []
    assert all(o.source_url != TREND_URL for o in es.hypothesis_opportunities)
    assert all(k.source_url != TREND_URL for k in es.key_signals)
    assert es.context_source_count == 1

    weak = _opp("data_centre_infrastructure", "weak_relevance", "inference_hypothesis", 30.0, ["https://x.example/w"])
    assert build_executive_summary(_report(), [weak]).opportunity_count == 0

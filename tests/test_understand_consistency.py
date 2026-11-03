"""UNDERSTAND v2 consistency fix -- one event, one count, one classification,
two separate dimensions.

  1. The same underlying account event appears once (signal types attached
     as tags), never once per detected signal type.
  2. The Executive Summary and UNDERSTAND use the same normalized,
     deduplicated development count from ui.account_signals.
  3. A technology-related strategic partnership keeps a technology TAG but
     never becomes Technology / Procurement without the existing evidence
     rules (procurement, or specific technology named).
  4. Account relationship and evidence strength are separate dimensions.
  5. The deterministic narrative interprets, it does not just count.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ui import account_signals as sig
from ui.executive_summary import build_executive_summary, rank_key_signals
from ui.understand_view import build_understand_view, understand_sections_html

import streamlit as _REAL_STREAMLIT  # noqa: E402,F401  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
ORG = "Oman Airports Management Company"
COUNTRY = "Oman"
OWN = "https://www.omanairports.co.om"
NEWS = f"{OWN}/en/media/news"
HEADLINE = ("News Oman Airports Signs Strategic Partnership with Bank Muscat to Implement Advanced Direct Payment "
            "System 01 Dec At a Glance")


def _signal(kind, text, url, title=""):
    return {"signal_type": kind, "text": text, "source_url": url, "source_title": title, "matched_terms": []}


def _row(cap, level, category, url, *, signal_type=None, title="t"):
    return {
        "customer_signal": {"signal_type": signal_type, "text": None}, "evidence_excerpt": None,
        "business_impact": "impact", "technology_requirement": "req",
        "potential_otech_capability": {"capability_id": cap, "capability_name": cap},
        "evidence_level": level, "confidence": 60.0, "source_url": url, "source_title": title,
        "supporting_context": "", "matched_terms": [], "incumbent_context": [], "evidence_category": category,
        "inference_cluster_id": url,
        "epistemic_status": "confirmed_evidence" if level == "direct_evidence" else "inference_hypothesis",
    }


def _live_like_report(**overrides):
    """The shape seen in the live run: the Bank Muscat headline on an
    account page detected twice (partnership + technology initiative from
    overlapping windows), two leadership-change reports and one vague
    general-activity line."""
    report = {
        "organization_name": ORG, "country": COUNTRY, "website": OWN, "people": [],
        "signals": [
            _signal("partnership", "10 Mar " + HEADLINE, NEWS, "News"),
            _signal("technology_initiative", HEADLINE + " Our impact in numbers", NEWS, "News"),
            _signal("leadership_change", "Oman Airports Appoints Nasser Example As Acting CEO",
                    "https://aviation.example/acting-ceo", "Oman Airports Appoints Acting CEO"),
            _signal("leadership_change", "Nasser Example appointed Acting CEO of Oman Airports Management Company",
                    "https://www.linkedin.com/posts/aviation_oman-airports-ceo", "Oman Airports appoints CEO"),
            _signal("technology_initiative", "To improve operations, OAMC has implemented innovative strategies.",
                    "https://docs.example/strategy", "Strategic Management of Oman Airports"),
        ],
        "chain": [
            _row("enterprise_managed_services", "weak_relevance", "incidental_mention", NEWS, title="News"),
        ],
    }
    report.update(overrides)
    return report


def _all_devs(view):
    return [d for group in view.developments.values() for d in group]


# ---------------------------------------------------------------------------
# 1 + 3. One event, one primary class, tags attached
# ---------------------------------------------------------------------------

def test_same_event_detected_as_two_signal_types_appears_once():
    view = build_understand_view({"report": _live_like_report()})
    bank = [d for d in _all_devs(view) if "Bank Muscat" in d.excerpt]
    assert len(bank) == 1, "the Bank Muscat partnership must be rendered exactly once"
    event = bank[0]
    assert event.class_key == "strategic"
    assert event.tags == ["Partnership", "Technology-related"]
    assert all("Bank Muscat" not in d.excerpt for d in view.developments["general"])
    assert view.developments["technology_procurement"] == [] and view.development_counts["technology_procurement"] == 0
    html = "\n".join(understand_sections_html(view))
    assert html.count("Bank Muscat to Implement") == 1


def test_event_without_a_detected_partnership_type_gains_it_only_from_an_explicit_named_partnership():
    report = _live_like_report(signals=[_signal("technology_initiative", HEADLINE, NEWS, "News")])
    [event] = sig.normalize_account_signals(report).ranked
    assert event.signal_class == "strategic" and event.tags == ["Partnership", "Technology-related"]
    vague = _live_like_report(signals=[_signal("technology_initiative",
                                               "Oman Airports builds strong partnerships with global aviation leaders.",
                                               NEWS, "News")])
    [event] = sig.normalize_account_signals(vague).ranked
    assert event.signal_class == "general" and "Partnership" not in event.tags


def test_technology_tag_never_creates_technology_procurement_but_real_technology_events_still_do():
    tech = _live_like_report(signals=[
        _signal("partnership", "Oman Airports signs a partnership with Acme Networks for SD-WAN and a new data center.",
                NEWS, "News"),
        _signal("technology_initiative", "Oman Airports signs a partnership with Acme Networks for SD-WAN and a new data center.",
                NEWS, "News"),
    ])
    [event] = sig.normalize_account_signals(tech).ranked
    assert event.signal_class == "technology_procurement"   # specific technology named: existing rule
    assert "Partnership" in event.tags and "Technology-related" not in event.tags
    tender = _live_like_report(signals=[_signal("procurement_activity", "Oman Airports has issued a tender for airport services.",
                                                NEWS, "News")])
    assert sig.normalize_account_signals(tender).ranked[0].signal_class == "technology_procurement"


def test_different_events_on_the_same_page_are_not_merged():
    report = _live_like_report(signals=[
        _signal("leadership_change", "Oman Airports appoints a new Chief Operating Officer for its regional airports.",
                NEWS, "News"),
        _signal("expansion", "Separately, Oman Airports announced the expansion of the Duqm terminal building next year.",
                NEWS, "News"),
    ])
    assert len(sig.normalize_account_signals(report).ranked) == 2


def test_identical_text_from_two_sources_is_one_development():
    text = "Oman Airports announced the expansion of the Salalah terminal."
    report = _live_like_report(signals=[_signal("expansion", text, f"{OWN}/a", "A"), _signal("expansion", text, f"{OWN}/b", "B")])
    assert len(sig.normalize_account_signals(report).ranked) == 1


def test_merging_never_upgrades_evidence_strength():
    report = _live_like_report()
    for k in sig.normalize_account_signals(report).ranked:
        assert k.evidence_level in (sig.WEAK, sig.NO_CAPABILITY_LINK)
        assert k.strength_rank == 1


# ---------------------------------------------------------------------------
# 2. One shared count
# ---------------------------------------------------------------------------

def _count_fixtures():
    from tests.test_understand_view import _report as understand_fixture

    return [_live_like_report(), understand_fixture(), _live_like_report(signals=[]),
            _live_like_report(signals=_live_like_report()["signals"] * 3)]


@pytest.mark.parametrize("index", range(4))
def test_executive_summary_and_understand_use_the_same_development_count(index):
    report = _count_fixtures()[index]
    signal_set = sig.normalize_account_signals(report)
    es = build_executive_summary(report, [])
    view = build_understand_view({"report": report})
    _, ranked_count = rank_key_signals(report)
    assert es.meaningful_signal_count == signal_set.development_count == len(signal_set.ranked)
    assert sum(view.development_counts.values()) == signal_set.development_count == ranked_count
    # the executive key signals are the top of the same ranked list
    assert [k.excerpt for k in es.key_signals] == [k.excerpt for k in signal_set.ranked[: len(es.key_signals)]]


def test_live_like_counts_are_events_not_signal_type_pairs():
    report = _live_like_report()
    pairs = {(s["source_url"], s["signal_type"]) for s in report["signals"]}
    es = build_executive_summary(report, [])
    assert len(pairs) == 5 and es.meaningful_signal_count == 4
    assert any("4 account-specific signal" in s for s in es.summary_sentences)


def test_neither_ui_counts_raw_signals_or_mappings_itself():
    es_src = (ROOT / "ui" / "executive_summary.py").read_text(encoding="utf-8")
    u_src = (ROOT / "ui" / "understand_view.py").read_text(encoding="utf-8")
    assert "normalize_account_signals" in es_src and "normalize_account_signals" in u_src
    for src in (es_src, u_src):
        assert 'report.get("signals")' not in src
    assert "meaningful_count" in es_src


# ---------------------------------------------------------------------------
# 4. Account relationship vs evidence strength
# ---------------------------------------------------------------------------

def test_account_specific_and_evidence_strength_are_separate_dimensions():
    view = build_understand_view({"report": _live_like_report()})
    bank = next(d for d in _all_devs(view) if "Bank Muscat" in d.excerpt)
    assert bank.relationship_label == "Account-specific" and bank.evidence_label == "Weak relevance"
    assert bank.dimension_note == "About the account, but does not establish an Otech requirement."
    ceo = next(d for d in _all_devs(view) if d.source_url.startswith("https://aviation.example"))
    assert ceo.relationship_label == "Account-specific" and ceo.evidence_label == "No capability link"
    html = "\n".join(understand_sections_html(view))
    assert 'title="Account relationship">Account-specific<' in html
    assert 'title="Evidence strength">Weak relevance<' in html


def test_direct_and_strong_levels_come_from_the_chain_unchanged():
    url = "https://news.example/soc"
    report = _live_like_report(
        signals=[_signal("technology_initiative", "Oman Airports is deploying a security operations center.", url, "SOC")],
        chain=[_row("cybersecurity_resilience", "direct_evidence", "organizational_technology_event", url,
                    signal_type="technology_initiative")],
    )
    [d] = _all_devs(build_understand_view({"report": report}))
    assert (d.relationship_label, d.evidence_label) == ("Account-specific", "Direct evidence")
    report["chain"][0]["evidence_level"] = "strong_inference"
    [d] = _all_devs(build_understand_view({"report": report}))
    assert (d.relationship_label, d.evidence_label) == ("Account-specific", "Strong inference")


def test_reported_news_keeps_its_own_relationship():
    report = _live_like_report(signals=[_signal("technology_initiative", HEADLINE, "https://group.example", "Group Holding")])
    view = build_understand_view({"report": report})
    assert _all_devs(view) == []
    [news] = view.reported_news
    assert news.relationship_label == "Reported news" and news.class_key == "strategic"
    assert news.tags == ["Partnership", "Technology-related"]
    assert "not direct account evidence" in news.dimension_note


def test_executive_summary_line_shows_both_dimensions():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "_esc(k.relationship_label)} &middot; {_esc(k.evidence_label)}" in src
    assert "_esc(k.strength)" not in src


# ---------------------------------------------------------------------------
# 5. Narrative
# ---------------------------------------------------------------------------

def test_deterministic_narrative_interprets_without_inventing_facts():
    view = build_understand_view({"report": _live_like_report()})
    assert view.meaning[0] == ("Oman Airports is showing strategic and general business activity, including a Bank "
                               "Muscat partnership and recent leadership changes.")
    assert view.meaning[1] == ("However, available evidence does not currently establish an Otech-specific technology "
                               "or procurement requirement.")
    assert not view.meaning[0][0].isdigit() and "Public evidence shows" not in " ".join(view.meaning)
    assert view.posture_summary.endswith("no current signal.")
    html = "\n".join(understand_sections_html(view))
    assert html.index("However, available evidence") < html.index(">Capability posture<")


def test_counterparty_is_taken_verbatim_or_omitted():
    no_name = _live_like_report(signals=[_signal("partnership", "Oman Airports signed a partnership agreement this week.",
                                                 NEWS, "News")])
    view = build_understand_view({"report": no_name})
    assert "including a partnership" in view.meaning[0]
    assert sig.explicit_counterparty("Strategic Partnership With Bank Muscat To Implement Payments") == "Bank Muscat"
    assert sig.explicit_counterparty("partnership with Oman Airports", ORG, COUNTRY) == ""


def test_narrative_with_confirmed_need_and_with_no_developments():
    url = "https://news.example/soc"
    report = _live_like_report(
        signals=[_signal("technology_initiative", "Oman Airports is deploying a security operations center.", url, "SOC")],
        chain=[_row("cybersecurity_resilience", "direct_evidence", "organizational_technology_event", url,
                    signal_type="technology_initiative")],
    )
    meaning = build_understand_view({"report": report}).meaning
    assert meaning[1].startswith("Account evidence of need is identified for cybersecurity_resilience")
    empty = build_understand_view({"report": _live_like_report(signals=[])}).meaning
    assert empty[0].startswith("The public evidence collected does not show account-specific developments")
    assert empty[1].startswith("Available evidence does not currently establish")


# ---------------------------------------------------------------------------
# Real saved corpus: zero opportunities, shared counts (skipped without it)
# ---------------------------------------------------------------------------

def _saved_corpus():
    found = sorted(ROOT.glob("evidence_corpus_*.json"))
    return found[0] if found else None


@pytest.mark.skipif(_saved_corpus() is None, reason="no saved evidence corpus in the project folder")
def test_saved_corpus_counts_match_and_no_opportunity():
    from commercial.evidence_mapping import map_all_sources
    from commercial.incumbents import detect_all_incumbent_evidence
    from commercial.opportunities import build_opportunity_hypotheses
    from commercial.signals import extract_all_signals
    from compare_evidence_fix import _source_from_dict
    from research.analysis.qualification import QualificationResult, qualify_corpus
    from research.analysis.report import AccountRadarReport

    saved = json.loads(_saved_corpus().read_text(encoding="utf-8"))
    sources = [_source_from_dict(d) for d in saved["sources"]]
    qualified = qualify_corpus(sources).qualified_sources
    org, country = saved["organization_name"], saved["country"]
    mappings = map_all_sources(qualified, organization_name=org, country=country)
    opportunities = build_opportunity_hypotheses(mappings, max_items=5)
    assert opportunities == []
    report = AccountRadarReport(
        organization_name=org, country=country, website=OWN, linkedin_company_url=None,
        qualification=QualificationResult(qualified_sources=qualified), signals=extract_all_signals(qualified),
        capability_mappings=mappings, incumbent_evidence=detect_all_incumbent_evidence(qualified),
        opportunity_hypotheses=build_opportunity_hypotheses(mappings), people=[], acquisition_diagnostics={},
    ).to_dict()
    es = build_executive_summary(report, [])
    view = build_understand_view({"report": report})
    assert es.meaningful_signal_count == sum(view.development_counts.values())
    excerpts = [d.excerpt for d in _all_devs(view)] + [d.excerpt for d in view.reported_news]
    assert len(excerpts) == len(set(excerpts))
    assert sum("Bank Muscat" in e for e in excerpts) <= 1
    assert view.development_counts["technology_procurement"] == 0

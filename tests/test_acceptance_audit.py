"""Product acceptance audit -- regression tests for the defects it found.

1. The Executive Summary must not prescribe a next step ("monitor") that can
   contradict DECIDE; DECIDE owns the recommendation.
2. WATCH's "Strongest evidence" panel must never headline a weak-relevance or
   industry/market-context mapping as account evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.test_act_view import _apptest, _cases, _html
from tests.test_decide_view import TREND, TREND_TITLE, _report, _row, _signal
from ui.decide_view import VALIDATE, build_decision
from ui.executive_summary import build_executive_summary

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_summary_never_prescribes_monitor_when_decide_says_validate():
    report, opps = _cases()[VALIDATE]
    decision = build_decision({"report": report, "opportunity_hypotheses": opps, "ai_synthesis": {}})
    assert decision.state == VALIDATE
    text = " ".join(build_executive_summary(report, opps).summary_sentences).lower()
    assert "account to monitor" not in text
    assert "treat this as" not in text


def test_empty_summary_defers_next_step_to_decide():
    es = build_executive_summary(_report(signals=[], chain=[], people=[]), [])
    text = " ".join(es.summary_sentences)
    assert "No Otech opportunity clears the evidence bar yet" in text
    assert "see DECIDE" in text and "monitor" not in text.lower()


def test_strongest_evidence_ignores_weak_and_context_rows():
    import app
    weak = _row("cybersecurity_resilience", "weak_relevance", "industry_context", TREND, title=TREND_TITLE)
    ctx = _row("cloud_modernization", "strong_inference", "industry_context", TREND, title=TREND_TITLE)
    assert app._strongest_evidence({"chain": [weak, ctx]}) is None
    real = _row("cybersecurity_resilience", "strong_inference", "organizational_technology_event", TREND)
    assert app._strongest_evidence({"chain": [weak, ctx, real]}) is real


def test_watch_does_not_headline_market_context_as_strongest_evidence():
    sig = _signal("technology_initiative", "Ports in the region adopt zero trust.", TREND, TREND_TITLE)
    report = _report(signals=[sig], chain=[_row("cybersecurity_resilience", "weak_relevance",
                                             "industry_context", TREND, title=TREND_TITLE)], people=[])
    at = _apptest(report)
    at.run()
    assert not at.exception
    html = _html(at)
    assert "No account evidence of need yet" in html
    assert "background context, not account evidence" in html
    assert "No qualifying opportunity yet" not in html


# ---------------------------------------------------------------------------
# Demo polish (frozen build): identity order, WATCH counts, stakeholder text
# ---------------------------------------------------------------------------

def test_identity_line_leads_with_verified_domain_then_country():
    from tests.test_organization_identity import _identity
    from ui.identity_view import identity_block_html, identity_for_report
    block = identity_block_html(identity_for_report({"identity": _identity()}))
    assert block.index("omanairports.co.om") < block.index(">Oman<")
    # an injected research domain still never appears as the official domain
    bad = identity_block_html(identity_for_report({"identity": _identity(primary_official_domain="protenders.com")}))
    assert "protenders" not in bad and ">Oman<" in bad


def test_watch_header_separates_account_developments_from_background():
    sig = _signal("technology_initiative", "Ports in the region adopt zero trust.", TREND, TREND_TITLE)
    report = _report(signals=[sig, dict(sig), dict(sig)], chain=[], people=[])
    at = _apptest(report)
    at.run()
    assert not at.exception
    html = _html(at)
    assert "0 account-specific developments" in html
    assert "3 research signals incl. background across 1 type" in html
    assert "they are not account developments" in html
    assert "signals across" not in html.replace("research signals incl. background across", "")


def test_summary_stakeholder_text_is_clean_and_matches_decide():
    from tests.test_decide_view import INFRA_LEAD
    person = dict(INFRA_LEAD, name="\u200fBasim Allawati\u200f",
                  current_title="\u200fBasim Allawati\u200f - \u200fVice President - Infrastructure & Security at ...")
    report = _report(signals=[], chain=[], people=[person])
    from ui.executive_summary import stakeholder_name, stakeholder_title
    from ui.understand_view import clean_person_title
    assert stakeholder_name(person) == "Basim Allawati"
    title = stakeholder_title(person, report)
    assert title == "Vice President - Infrastructure & Security"
    assert title == clean_person_title(person, report.get("organization_name") or "", report.get("country"))
    text = " ".join(build_executive_summary(report, []).summary_sentences)
    assert "\u200f" not in text and "at ...." not in text
    assert "start with Basim Allawati, Vice President - Infrastructure & Security." in text

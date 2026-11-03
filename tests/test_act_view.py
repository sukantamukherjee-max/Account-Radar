"""ACT v2 -- the execution layer after DECIDE.

ACT consumes DECIDE's handoff (or DECIDE's current result for the loaded
account), never re-runs research, never invents people, opportunities,
projects, budgets or timelines, and never manufactures urgency. AI is an
optional enrichment only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from tests.test_decide_view import (
    BUSINESS,
    CFO,
    CYBER,
    INFRA_LEAD,
    LEADERSHIP,
    NEWS,
    ORG,
    OWN,
    TREND,
    TREND_TITLE,
    _opp,
    _pursue_report,
    _report,
    _row,
    _signal,
)
from ui.act_view import STATUS, build_action_plan, context_for, plan_for_entry
from ui.decide_view import MONITOR, NO_ACTION, PURSUE, VALIDATE, build_decision

import streamlit as _REAL_STREAMLIT  # noqa: E402  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
URGENCY = re.compile(r"\b(urgent|urgently|immediately|asap|act now|right away|critical|time-sensitive|"
                     r"before it is too late|major)\b", re.I)


def _entry(report, opportunities=None, ai=None):
    return {"report": report, "opportunity_hypotheses": opportunities or [], "ai_synthesis": ai or {}}


def _cases():
    pursue = (_pursue_report(), [_opp("cybersecurity_resilience", "direct_evidence", "confirmed_evidence", 90.0,
                                      [NEWS, "https://second.example/soc"])])
    validate = (_report(signals=LEADERSHIP, people=[INFRA_LEAD, CFO]), [])
    monitor = (_report(signals=LEADERSHIP, people=[CFO, BUSINESS]), [])
    no_action = (_report(signals=[_signal("technology_initiative", "Ports such as Meridian Ports Authority and others adopt zero trust.",
                                          TREND, TREND_TITLE)],
                         chain=[_row("cybersecurity_resilience", "weak_relevance", "industry_context", TREND, title=TREND_TITLE)],
                         people=[CYBER]), [])
    return {PURSUE: pursue, VALIDATE: validate, MONITOR: monitor, NO_ACTION: no_action}


def _plan(state):
    report, opps = _cases()[state]
    decision = build_decision({"report": report, "opportunity_hypotheses": opps})
    return decision, build_action_plan(decision.handoff(), handed_over=True), report


def _plan_text(plan) -> str:
    return " ".join([plan.recommended_action, plan.why_now, plan.objective, plan.outreach_email, plan.outreach_linkedin])


# ---------------------------------------------------------------------------
# All four DECIDE states flow into ACT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state,status,outreach", [
    (PURSUE, "Ready for Outreach", True),
    (VALIDATE, "Discovery Recommended", True),
    (MONITOR, "Monitor / No Immediate Outreach", False),
    (NO_ACTION, "No Action", False),
])
def test_each_decide_state_flows_into_act(state, status, outreach):
    decision, plan, _ = _plan(state)
    assert decision.state == state and plan.state == state
    assert plan.status == status == STATUS[state]
    assert plan.outreach_recommended is outreach
    assert plan.confidence == decision.confidence
    if state in (PURSUE, VALIDATE, MONITOR):
        assert plan.recommended_action == decision.next_action      # DECIDE's action, verbatim
    else:
        assert plan.recommended_action.startswith("No sales action now")


def test_pursue_prepares_an_opportunity_oriented_action():
    _, plan, _ = _plan(PURSUE)
    assert plan.objective.startswith("Advance Cybersecurity Resilience")
    assert plan.stakeholder["person_id"] == "cyber"
    assert "next steps" in plan.outreach_subject
    assert "There is no specific proposal" not in plan.outreach_email


def test_validate_prepares_a_discovery_action_without_positioning_a_solution():
    _, plan, _ = _plan(VALIDATE)
    assert plan.objective.startswith("Establish current infrastructure, cloud or network priorities")
    assert plan.objective.endswith("before positioning any Otech solution.")
    assert "There is no specific proposal behind this" in plan.outreach_email
    assert "opportunit" not in _plan_text(plan).lower()


def test_monitor_and_no_action_never_force_outreach():
    for state in (MONITOR, NO_ACTION):
        _, plan, _ = _plan(state)
        assert plan.stakeholder is None and not plan.outreach_email and not plan.outreach_linkedin
    _, monitor, _ = _plan(MONITOR)
    assert monitor.why_now.startswith("No immediate reason for outreach")
    _, none, _ = _plan(NO_ACTION)
    assert none.questions == [] and none.objective == ""


# ---------------------------------------------------------------------------
# Handoff preservation
# ---------------------------------------------------------------------------

def test_stakeholder_is_handed_over_unchanged():
    decision, plan, report = _plan(VALIDATE)
    assert plan.stakeholder == decision.handoff()["stakeholder"]
    assert plan.stakeholder["person_id"] in {p["person_id"] for p in report["people"]}
    assert "Dear Rana" in plan.outreach_email and "Hello Rana" in plan.outreach_linkedin


def test_validation_questions_are_preserved_verbatim_and_not_contradicted():
    decision, plan, _ = _plan(VALIDATE)
    assert plan.questions == decision.questions
    for q in decision.questions:
        assert q in plan.outreach_email
    assert len(re.findall(r"\?", plan.outreach_email)) == len(decision.questions) + 1   # + the meeting ask


def test_evidence_is_preserved_with_sources():
    decision, plan, report = _plan(VALIDATE)
    report_urls = {s["source_url"] for s in report["signals"]}
    handoff_urls = {e["source_url"] for e in decision.handoff()["evidence"]}
    assert plan.evidence and {e.source_url for e in plan.evidence} <= report_urls
    assert {e.source_url for e in plan.evidence} <= handoff_urls | {decision.trigger.source_url}
    assert len({e.source_url for e in plan.evidence}) == len(plan.evidence)            # one item per source
    assert not any("linkedin.com/in/" in e.source_url for e in plan.evidence)          # profiles are stakeholder context


# ---------------------------------------------------------------------------
# Guardrails: no urgency, no inference as fact, nothing invented
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", [PURSUE, VALIDATE, MONITOR, NO_ACTION])
def test_no_manufactured_urgency(state):
    _, plan, _ = _plan(state)
    assert not URGENCY.search(_plan_text(plan)), _plan_text(plan)
    if state in (PURSUE, VALIDATE):
        assert "Nothing in the evidence sets a deadline." in plan.why_now


def test_no_invented_projects_budgets_timelines_or_deployments():
    _, plan, _ = _plan(VALIDATE)
    text = _plan_text(plan).lower()
    for claim in ("is planning", "plans to", "budget of", "has budget", "is deploying", "deployment of",
                  "tender", "rfp", "transformation programme", "by 20"):
        assert claim not in text, claim


def test_no_invented_people():
    for state in (PURSUE, VALIDATE):
        _, plan, report = _plan(state)
        names = {p["name"] for p in report["people"]}
        other = names - {plan.stakeholder["name"]}
        assert not any(n in _plan_text(plan) for n in other)


def test_no_invented_opportunity_for_validate_or_monitor():
    for state in (VALIDATE, MONITOR):
        decision, plan, _ = _plan(state)
        assert decision.opportunity_count == 0
        assert "opportunit" not in _plan_text(plan).lower()


# ---------------------------------------------------------------------------
# Context source: handoff vs. derived from DECIDE
# ---------------------------------------------------------------------------

def test_act_without_a_handoff_uses_the_current_decide_result():
    report, opps = _cases()[VALIDATE]
    handoff = build_decision({"report": report, "opportunity_hypotheses": opps}).handoff()
    derived = plan_for_entry(_entry(report, opps))
    handed = plan_for_entry(_entry(report, opps), handoff)
    assert not derived.handed_over and handed.handed_over
    assert derived.recommended_action == handed.recommended_action and derived.questions == handed.questions


def test_a_handoff_for_another_account_is_ignored():
    report, _ = _cases()[VALIDATE]
    context, handed = context_for(_entry(report), {"account_key": "Some Other Org", "decision": PURSUE})
    assert not handed and context["decision"] == VALIDATE


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def _apptest(report, opportunities=None, ai=None):
    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(APP), default_timeout=30)
    base = {"chain": [], "signals": [], "capability_summary": {}, "incumbent_evidence": [],
            "acquisition_diagnostics": {}, "status": "qualified"}
    base.update(report)
    at.session_state["entry"] = {"report": base, "ai_synthesis": ai or {}, "opportunity_hypotheses": opportunities or []}
    at.session_state["loaded_from"] = "test"
    return at


def _html(at) -> str:
    return " ".join(m.value for m in at.markdown)


def test_act_page_renders_every_section_for_validate():
    report, _ = _cases()[VALIDATE]
    at = _apptest(report)
    at.run()
    assert not at.exception
    html = _html(at)
    for part in ("From the current DECIDE result", "Discovery Recommended", "Recommended action", "Why now",
                 "Conversation objective", "Discovery / validation questions", "Stakeholder", "Evidence",
                 "Suggested outreach"):
        assert part in html, part
    assert 'href="https://aviation.example/acting-ceo" target="_blank"' in html     # source links
    email = next(t for t in at.text_area if (t.key or "").startswith("act_outreach_email_"))
    assert email.value.startswith("Subject: ")


def test_take_to_act_journey_and_no_research_rerun(monkeypatch):
    import ai.synthesis
    import research.acquisition
    import research.analysis.report

    def forbidden(*a, **k):
        raise AssertionError("research / synthesis must not run when moving from DECIDE to ACT")

    monkeypatch.setattr(research.acquisition, "run_acquisition", forbidden)
    monkeypatch.setattr(research.analysis.report, "build_report", forbidden)
    monkeypatch.setattr(ai.synthesis, "synthesize_account", forbidden)
    report, _ = _cases()[VALIDATE]
    at = _apptest(report)
    at.run()
    at.button(key="decide_take_to_act").click().run()
    assert not at.exception
    assert at.session_state["ar_nav"] == "Next actions"
    html = _html(at)
    assert "Handed over from DECIDE" in html
    ctx = at.session_state["act_context"]
    assert ctx["validation_questions"][0] in html


def test_open_in_people_and_back_to_decide_from_act():
    report, _ = _cases()[VALIDATE]
    at = _apptest(report)
    at.run()
    at.button(key="act_open_person").click().run()
    assert at.session_state["people_selected"] == "infra-lead" and at.session_state["ar_nav"] == "People"
    at.button(key="act_back_to_decide").click().run()
    assert at.session_state["ar_nav"] == "Opportunities"            # the sidebar item that opens DECIDE


def test_outreach_channel_switch_shows_the_linkedin_message():
    report, _ = _cases()[VALIDATE]
    at = _apptest(report)
    at.run()
    at.radio(key="act_outreach_channel").set_value("LinkedIn").run()
    msg = next(t for t in at.text_area if (t.key or "").startswith("act_outreach_linkedin_"))
    assert msg.value.startswith("Hello Rana,") and "Subject:" not in msg.value


@pytest.mark.parametrize("ai", [{}, {"status": "ai_failed", "next_best_actions": []},
                                {"status": "ai_unconfigured"}, {"status": "ok", "next_best_actions": []}])
def test_act_is_complete_without_ai(ai):
    report, _ = _cases()[VALIDATE]
    at = _apptest(report, ai=ai)
    at.run()
    assert not at.exception
    html = _html(at)
    assert "Discovery Recommended" in html and "Rana Infra" in html and "Why now" in html
    assert any("AI suggestions are not available" in c.value for c in at.caption)


def test_ai_suggestions_are_optional_enrichment_and_do_not_change_the_plan():
    report, _ = _cases()[VALIDATE]
    ai = {"status": "ok", "next_best_actions": [{"capability_name": "Network Connectivity", "rationale": "AI rationale text.",
                                                 "epistemic_status": "inference_hypothesis"}]}
    with_ai = _apptest(report, ai=ai)
    with_ai.run()
    without = _apptest(report)
    without.run()
    plan_blocks = lambda at: [m.value for m in at.markdown if "ar-act" in m.value or "Why now" in m.value]  # noqa: E731
    assert plan_blocks(with_ai) == plan_blocks(without)
    assert any("AI suggestions (1)" in e.label for e in with_ai.expander)


def test_no_actionable_evidence_shows_no_outreach_and_no_stakeholder_controls():
    report, _ = _cases()[NO_ACTION]
    at = _apptest(report)
    at.run()
    assert not at.exception
    assert any("No outreach is recommended" in i.value for i in at.info)
    assert not any((t.key or "").startswith("act_outreach_") for t in at.text_area)
    assert not any(b.key == "act_open_person" for b in at.button)


def test_monitor_shows_no_outreach_in_the_app():
    report, _ = _cases()[MONITOR]
    at = _apptest(report)
    at.run()
    assert any("Monitor the account" in i.value for i in at.info)
    assert "Monitor / No Immediate Outreach" in _html(at)


# ---------------------------------------------------------------------------
# Real saved corpus (skipped when not present)
# ---------------------------------------------------------------------------

def _saved_corpus():
    found = sorted(ROOT.glob("evidence_corpus_*.json"))
    return found[0] if found else None


@pytest.mark.skipif(_saved_corpus() is None, reason="no saved evidence corpus in the project folder")
def test_saved_corpus_act_plan():
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
        organization_name=org, country=country, website=OWN.replace("meridianports.example", "omanairports.co.om"),
        linkedin_company_url=None, qualification=QualificationResult(qualified_sources=qualified),
        signals=extract_all_signals(qualified), capability_mappings=mappings,
        incumbent_evidence=detect_all_incumbent_evidence(qualified),
        opportunity_hypotheses=build_opportunity_hypotheses(mappings),
        people=build_people([s for s in sources if s.source_type == "linkedin_profile"], organization_name=org),
        acquisition_diagnostics={},
    ).to_dict()
    entry = _entry(report, opportunities)
    decision = build_decision(entry)
    plan = plan_for_entry(entry, decision.handoff())
    assert plan.state == VALIDATE and plan.status == "Discovery Recommended" and plan.handed_over
    assert plan.confidence == "Medium" and opportunities == []
    assert plan.questions == decision.questions and len(plan.questions) == 2
    # The selected stakeholder is a confirmed People-list member with technology evidence.
    # (No title/name is pinned: the pick follows current-role evidence only.)
    by_id = {p["person_id"]: p for p in report["people"]}
    assert plan.stakeholder["person_id"] in by_id
    assert plan.stakeholder["association"] == "confirmed"
    assert by_id[plan.stakeholder["person_id"]]["organization_association"] == "confirmed"
    assert plan.stakeholder["areas"]
    assert plan.why_now.startswith("A recent account-specific leadership change (reported by 2 independent sources)")
    assert "opportunit" not in _plan_text(plan).lower() and not URGENCY.search(_plan_text(plan))
    assert ORG not in _plan_text(plan)

from ai.client import AIResponse
from ai.models import SynthesisStatus
from ai.synthesis import synthesize_account
from commercial.opportunities import build_opportunity_hypotheses
from tests.test_ai_synthesis import FakeAIClient, _qualified_report


def test_empty_ai_actions_get_deterministic_guardrail():
    report = _qualified_report()
    assert build_opportunity_hypotheses(report.capability_mappings)
    payload = {
        "account_narrative": "Evidence-backed summary.",
        "capability_narratives": [],
        "next_best_actions": [],
    }
    result = synthesize_account(report, FakeAIClient(responses=[AIResponse(parsed_json=payload, model="test-model")]))
    assert result.status == SynthesisStatus.DEGRADED
    assert result.next_best_actions
    assert all(a.origin == "deterministic_guardrail" for a in result.next_best_actions)
    assert all(a.evidence_level in ("direct_evidence", "strong_inference") for a in result.next_best_actions)


def test_one_valid_ai_action_is_not_silently_replaced_or_multiplied():
    report = _qualified_report()
    candidate = build_opportunity_hypotheses(report.capability_mappings)[0]
    payload = {
        "account_narrative": "Summary.",
        "capability_narratives": [],
        "next_best_actions": [
            {"capability_id": candidate.capability_id, "rationale": "Validate the evidence.", "rank": 1},
        ],
    }
    result = synthesize_account(report, FakeAIClient(responses=[AIResponse(parsed_json=payload)]))
    assert len(result.next_best_actions) == 1
    assert result.next_best_actions[0].origin == "ai"


def test_opportunity_hypotheses_exclude_weak_only_mappings():
    report = _qualified_report()
    weak_only_ids = {
        m.capability_id for m in report.capability_mappings if m.evidence_level.value == "weak_relevance"
    } - {
        m.capability_id for m in report.capability_mappings
        if m.evidence_level.value in ("direct_evidence", "strong_inference")
    }
    opportunities = build_opportunity_hypotheses(report.capability_mappings)
    assert all(o.capability_id not in weak_only_ids for o in opportunities)

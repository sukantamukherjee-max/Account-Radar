import json

from commercial.opportunities import OpportunityHypothesis


def test_opportunity_hypothesis_is_json_serializable():
    item = OpportunityHypothesis(
        capability_id="network_connectivity",
        capability_name="Network & Connectivity",
        priority="high",
        score=80.0,
        evidence_level="direct_evidence",
        confidence=90.0,
        evidence_count=2,
        independent_source_count=2,
        hypothesis="Potential network opportunity to validate.",
        rationale="Supported by independent evidence.",
        who="Technology leadership",
        conversation="Validate roadmap.",
        next_step="Confirm requirements.",
        source_urls=["https://example.com/evidence"],
    )
    payload = item.to_dict()
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    assert decoded["capability_id"] == "network_connectivity"
    assert decoded["score"] == 80.0

"""Phase 2 hardening tests: over-inference/capability fan-out reduction
while preserving legitimate multi-capability inference, plus the new
evidence_category / inference_cluster_id / epistemic_status fields and
domain/cluster-aware opportunity scoring.

Product principle under test throughout this file (per the approved
hardening direction): FEWER FALSELY INDEPENDENT HYPOTHESES, while
PRESERVING LEGITIMATE TECHNOLOGY INFERENCES. A fix that merely reduces the
number of mappings without checking locality/narrowing would fail the
"genuine local expansion" tests below just as badly as the old code failed
the fan-out tests -- both directions are asserted.

Generic placeholder organization only; no real organization/sector/country
is referenced anywhere in this file.
"""

from __future__ import annotations

from commercial.evidence_mapping import map_all_sources, map_source_to_capabilities
from commercial.models import EvidenceLevel
from commercial.opportunities import build_opportunity_hypotheses
from research.sources import AccessStatus, Source

ORG_NAME = "Meridian Ports Authority"


def _source(*, url: str = "https://example.com/a", content: str, source_type: str = "official") -> Source:
    return Source(
        url=url, domain=url.split("/")[2], title="", source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic",
        content=content, access_status=AccessStatus.FETCHED,
    )


def _levels(mappings, capability_id=None):
    if capability_id:
        mappings = [m for m in mappings if m.capability_id == capability_id]
    return {m.evidence_level for m in mappings}


# ---------------------------------------------------------------------------
# The fan-out regression itself (items 1 + 2 combined)
# ---------------------------------------------------------------------------

def test_unrelated_signal_elsewhere_in_source_does_not_promote_generic_trigger():
    """The exact reported failure mode: a generic trigger ("acquisition",
    shared by every capability in the catalogue) appears near the target
    organization's name, while the only qualifying-looking business signal
    in the source ("strategic partnership") is in a completely unrelated,
    disconnected sentence. Before Phase 2 hardening this promoted all five
    capabilities to strong_inference. After hardening, none of them should
    reach strong_inference or direct_evidence -- the signal is not locally
    associated with the trigger, and even if it were, "partnership" is
    excluded from the generic-trigger-qualifying signal set entirely.
    """
    content = (
        f"{ORG_NAME} today confirmed a strategic partnership with a regional logistics operator "
        "to expand joint marketing efforts across the region. "
        "In entirely separate news, industry observers noted that " + ORG_NAME + "'s planned "
        "acquisition of a smaller regional operator is expected to close next quarter, pending "
        "regulatory approval. No further details were shared about the transaction structure."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    promoted = [m for m in mappings if m.evidence_level != EvidenceLevel.WEAK_RELEVANCE]
    assert not promoted, f"expected zero promoted mappings, got: {[(m.capability_id, m.evidence_level.value) for m in promoted]}"
    # Every capability that matched "acquisition" is still retained as
    # weak/context evidence -- never silently dropped.
    assert len(mappings) >= 1
    assert all(m.evidence_category == "incidental_mention" for m in mappings)


def test_partnership_never_independently_promotes_generic_trigger_even_when_local():
    """Even when "partnership" sits in the SAME sentence as the generic
    trigger (i.e. locality would not save it), it must still never promote
    a generic trigger to strong_inference -- item 2 excludes it from the
    generic-trigger-qualifying signal set entirely, regardless of position.

    Deliberately uses "acquisition" as the trigger (not "new facility" or
    similar, which would itself independently match the "expansion" signal
    pattern in commercial/signals.py and mask what this test is checking).
    """
    content = (
        f"{ORG_NAME}'s planned acquisition of a regional operator is proceeding as part of a "
        "broader strategic partnership agreement."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    assert not any(m.evidence_level == EvidenceLevel.STRONG_INFERENCE for m in mappings)


def test_investment_never_independently_promotes_generic_trigger_even_when_local():
    content = (
        f"{ORG_NAME}'s planned acquisition of a regional operator is being funded through a "
        "new investment round."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    assert not any(m.evidence_level == EvidenceLevel.STRONG_INFERENCE for m in mappings)


def test_contract_award_never_independently_promotes_generic_trigger_even_when_local():
    content = f"{ORG_NAME} was awarded the contract covering its planned acquisition of a regional operator."
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    assert not any(m.evidence_level == EvidenceLevel.STRONG_INFERENCE for m in mappings)


# ---------------------------------------------------------------------------
# Legitimate inference must be PRESERVED, not collateral damage (product
# philosophy: "do NOT optimize for fewer mappings")
# ---------------------------------------------------------------------------

def test_genuine_local_expansion_event_still_produces_multiple_related_capability_hypotheses():
    """The "GOOD" example from the approved spec: one genuine, locally
    described expansion event legitimately implies several capabilities at
    once. This must still work after the fan-out fix -- the fix targets
    UNRELATED signals, not genuine multi-capability inference from one
    real event.
    """
    content = (
        f"{ORG_NAME} announced today that it is opening a new facility in the northern district. "
        "This expansion will require additional network capacity, data storage infrastructure, "
        "and improved cybersecurity operations to support the new site."
    )
    s = _source(content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    strong_or_direct = [m for m in mappings if m.evidence_level in (EvidenceLevel.STRONG_INFERENCE, EvidenceLevel.DIRECT_EVIDENCE)]
    capabilities_hit = {m.capability_id for m in strong_or_direct}
    assert len(capabilities_hit) >= 2, f"expected legitimate multi-capability inference, got: {capabilities_hit}"
    assert all(m.evidence_category == "business_signal_inference" for m in strong_or_direct)
    assert all(m.epistemic_status == "inference_hypothesis" for m in strong_or_direct)


def test_organization_specific_new_site_can_still_infer_network_need():
    """Regression guard for the pre-existing Phase 1E test of the same
    name: a single-capability legitimate inference must still work.

    Current/Change/Requirement Signal Gate update: "new site" is a
    generic_trigger SHARED by several catalogue capabilities, so it is now
    ambiguous under the approved catalogue-derived shared-trigger rule and
    requires local, capability-specific corroboration. The fixture is
    updated to add genuine network-specific vocabulary next to the trigger
    -- the test's original intent (a real business signal can still ground
    a network-need inference) is unchanged.
    """
    s = _source(
        url="https://example.com/press", source_type="business",
        content=(
            f"{ORG_NAME} is expanding into a new site to support future operations, "
            "requiring additional network connectivity and bandwidth at the new location."
        ),
    )
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    network = [m for m in mappings if m.capability_id == "network_connectivity"]
    assert network
    assert any(m.evidence_level == EvidenceLevel.STRONG_INFERENCE for m in network)


def test_specific_official_technology_event_remains_direct():
    """Regression guard: Rule 2/3 direct_evidence is untouched by the Rule
    4 fan-out fix."""
    s = _source(
        url="https://example.com/announcement", source_type="official",
        content=f"{ORG_NAME} announced a cloud migration initiative to a new cloud platform for its core workloads.",
    )
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    cloud = [m for m in mappings if m.capability_id == "cloud_ai_infrastructure"]
    assert cloud
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cloud)


# ---------------------------------------------------------------------------
# inference_cluster_id
# ---------------------------------------------------------------------------

def test_capability_mappings_from_same_event_share_inference_cluster_id():
    content = (
        f"{ORG_NAME} announced today that it is opening a new facility in the northern district. "
        "This expansion will require additional network capacity, data storage infrastructure, "
        "and improved cybersecurity operations to support the new site."
    )
    s = _source(content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    strong = [m for m in mappings if m.evidence_level == EvidenceLevel.STRONG_INFERENCE]
    assert len(strong) >= 2
    cluster_ids = {m.inference_cluster_id for m in strong}
    assert len(cluster_ids) == 1, f"expected one shared cluster id for one event, got {cluster_ids}"


def test_capability_mappings_from_different_events_have_different_cluster_ids():
    s1 = _source(
        url="https://example.com/press-1", source_type="official",
        content=f"{ORG_NAME} is opening a new facility. This expansion increases network capacity needs.",
    )
    s2 = _source(
        url="https://example.com/tender-1", source_type="procurement",
        content=f"{ORG_NAME} issued a tender for disaster recovery and backup and recovery services.",
    )
    mappings = map_all_sources([s1, s2], organization_name=ORG_NAME)
    eligible = [m for m in mappings if m.evidence_level in (EvidenceLevel.STRONG_INFERENCE, EvidenceLevel.DIRECT_EVIDENCE)]
    assert eligible
    cluster_ids_by_source = {m.source_url: m.inference_cluster_id for m in eligible}
    assert len(set(cluster_ids_by_source.values())) >= 2, "mappings from two distinct events must not share a cluster id"


# ---------------------------------------------------------------------------
# evidence_category
# ---------------------------------------------------------------------------

def test_evidence_category_assigned_per_classification_branch():
    technographics = _source(
        url="https://technographics-example.com/stack", source_type="technology",
        content=f"{ORG_NAME} technology stack — technographics database entry: cloud platform, storage infrastructure.",
    )
    linkedin_profile = Source(
        url="https://www.linkedin.com/in/synthetic-person", domain="linkedin.com", title="IT Manager",
        source_type="linkedin_profile", snippet="IT Manager with a background in cloud and security.",
        discovered_via_query="q", discovery_provider="test", family="linkedin_people",
        content=None, access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
    )
    linkedin_job = Source(
        url="https://www.linkedin.com/jobs/view/999", domain="linkedin.com",
        title=f"{ORG_NAME} hiring Cloud Engineer", source_type="business", snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic_business",
        content=f"{ORG_NAME} is hiring a Cloud Engineer to support cloud platform and storage infrastructure.",
        access_status=AccessStatus.FETCHED,
    )
    procurement = _source(
        url="https://example.com/rfp", source_type="procurement",
        content=f"{ORG_NAME} issued a tender for disaster recovery and backup and recovery services.",
    )
    inference = _source(
        url="https://example.com/expansion", source_type="official",
        content=f"{ORG_NAME} is opening a new facility. This expansion will require additional network capacity.",
    )
    incidental = _source(
        url="https://example.com/trend", source_type="news",
        content="Commentators discussed general cloud, storage and network trends across the sector.",
    )

    tech_mappings = map_source_to_capabilities(technographics, organization_name=ORG_NAME)
    assert tech_mappings and all(m.evidence_category == "current_state_technology" for m in tech_mappings)

    profile_mappings = map_source_to_capabilities(linkedin_profile, organization_name=ORG_NAME)
    assert profile_mappings and all(m.evidence_category == "stakeholder_context" for m in profile_mappings)

    job_mappings = map_source_to_capabilities(linkedin_job, organization_name=ORG_NAME)
    assert job_mappings and all(m.evidence_category == "hiring_intent_context" for m in job_mappings)

    procurement_mappings = map_source_to_capabilities(procurement, organization_name=ORG_NAME)
    direct_procurement = [m for m in procurement_mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    assert direct_procurement
    assert any(m.evidence_category == "procurement_signal" for m in direct_procurement)

    inference_mappings = map_source_to_capabilities(inference, organization_name=ORG_NAME)
    strong = [m for m in inference_mappings if m.evidence_level == EvidenceLevel.STRONG_INFERENCE]
    assert strong and all(m.evidence_category == "business_signal_inference" for m in strong)

    incidental_mappings = map_source_to_capabilities(incidental, organization_name=ORG_NAME)
    assert incidental_mappings and all(m.evidence_category == "incidental_mention" for m in incidental_mappings)


# ---------------------------------------------------------------------------
# epistemic_status
# ---------------------------------------------------------------------------

def test_epistemic_status_direct_evidence_is_confirmed():
    s = _source(
        url="https://example.com/announcement", source_type="official",
        content=f"{ORG_NAME} announced a cloud migration initiative to a new cloud platform for its core workloads.",
    )
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    direct = [m for m in mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    assert direct
    assert all(m.epistemic_status == "confirmed_evidence" for m in direct)


def test_epistemic_status_inference_and_weak_levels_are_inference_hypothesis():
    s = _source(
        content=f"{ORG_NAME} is opening a new facility. This expansion will require additional network capacity.",
        source_type="official",
    )
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    non_direct = [m for m in mappings if m.evidence_level != EvidenceLevel.DIRECT_EVIDENCE]
    assert non_direct
    assert all(m.epistemic_status == "inference_hypothesis" for m in non_direct)


# ---------------------------------------------------------------------------
# Opportunity scoring hardening (item 5): domain + cluster diversity, not
# raw mapping/URL counts
# ---------------------------------------------------------------------------

def test_opportunity_score_does_not_inflate_from_repeated_same_domain_urls():
    content = (
        f"{ORG_NAME} is opening a new facility in the northern district. This expansion will "
        "require additional network capacity."
    )
    s1 = _source(url="https://news-example.com/article-a", content=content, source_type="official")
    s2 = _source(url="https://news-example.com/article-b", content=content, source_type="official")
    mappings_one = map_all_sources([s1], organization_name=ORG_NAME)
    mappings_two = map_all_sources([s1, s2], organization_name=ORG_NAME)
    opp_one = {o.capability_id: o for o in build_opportunity_hypotheses(mappings_one)}
    opp_two = {o.capability_id: o for o in build_opportunity_hypotheses(mappings_two)}
    assert "network_connectivity" in opp_one and "network_connectivity" in opp_two
    one, two = opp_one["network_connectivity"], opp_two["network_connectivity"]
    # A second article from the SAME domain must not raise the domain-
    # diversity count -- this is the primary guard against "repeated URLs
    # from the same source artificially inflate confidence." A second
    # distinctly-reported instance of an event can still nudge the small
    # inference-cluster bonus (capped well below the domain-diversity
    # bonus a genuinely independent domain would earn -- see
    # test_opportunity_score_rewards_genuine_domain_diversity), but must
    # never receive the same-sized bump as real corroborating diversity.
    assert two.independent_domain_count == one.independent_domain_count == 1
    assert two.score - one.score <= 2.0


def test_opportunity_score_rewards_genuine_domain_diversity():
    """Domain diversity must still reward a score -- but only when the
    diversity comes from bonus-eligible evidence (organizational_technology_
    event / procurement_signal per evidence-relevance hardening
    recommendation #1). Uses direct, organizationally-associated
    technology-event evidence (two distinct capability keywords, a genuine
    technology_initiative signal, in the same sentence as the organization)
    rather than a generic-trigger inference, since business_signal_inference
    diversity is deliberately EXCLUDED from the bonus as of recommendation
    #1 -- see test_business_signal_inference_diversity_does_not_inflate_score
    below for that half of the behavior.
    """
    content = (
        f"{ORG_NAME} today announced a network modernization initiative, deploying a new SD-WAN "
        "and completing a bandwidth upgrade across its facilities."
    )
    s1 = _source(url="https://news-example.com/article-a", content=content, source_type="official")
    s2 = _source(url="https://another-outlet-example.com/article-b", content=content, source_type="official")
    mappings_same_domain = map_all_sources([s1, s1], organization_name=ORG_NAME)
    mappings_diff_domain = map_all_sources([s1, s2], organization_name=ORG_NAME)
    opp_same = {o.capability_id: o for o in build_opportunity_hypotheses(mappings_same_domain)}["network_connectivity"]
    opp_diff = {o.capability_id: o for o in build_opportunity_hypotheses(mappings_diff_domain)}["network_connectivity"]
    assert opp_diff.independent_domain_count == 2
    assert opp_same.independent_domain_count == 1
    assert opp_diff.score > opp_same.score


def test_business_signal_inference_diversity_does_not_inflate_score():
    """Evidence-relevance hardening (recommendation #1): the SAME
    generic-trigger/strong_inference fixture that used to reward domain
    diversity must no longer do so -- business_signal_inference evidence
    stays in the base evidence_count (nothing is hidden or dropped) but
    must not inflate the score's domain/cluster diversity bonus."""
    content = (
        f"{ORG_NAME} is opening a new facility in the northern district. This expansion will "
        "require additional network capacity."
    )
    s1 = _source(url="https://news-example.com/article-a", content=content, source_type="official")
    s2 = _source(url="https://another-outlet-example.com/article-b", content=content, source_type="official")
    mappings_same_domain = map_all_sources([s1, s1], organization_name=ORG_NAME)
    mappings_diff_domain = map_all_sources([s1, s2], organization_name=ORG_NAME)
    opp_same = {o.capability_id: o for o in build_opportunity_hypotheses(mappings_same_domain)}["network_connectivity"]
    opp_diff = {o.capability_id: o for o in build_opportunity_hypotheses(mappings_diff_domain)}["network_connectivity"]
    assert opp_diff.evidence_level == "strong_inference"
    # The underlying evidence is still visibly more diverse (transparency
    # fields are unaffected)...
    assert opp_diff.independent_domain_count == 2
    assert opp_same.independent_domain_count == 1
    # ...but the SCORE no longer rewards it, because business_signal_
    # inference is not a bonus-eligible evidence category.
    assert opp_diff.score == opp_same.score


def test_opportunity_score_counts_inference_clusters_not_raw_mappings():
    """A single event that legitimately produces several STRONG_INFERENCE
    mappings for ONE capability from ONE source must count as one cluster,
    not be treated as though several independent events corroborate it."""
    content = (
        f"{ORG_NAME} is opening a new facility. This expansion will require additional network "
        "capacity, cybersecurity operations, and data storage infrastructure."
    )
    s = _source(content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    network_mappings = [m for m in mappings if m.capability_id == "network_connectivity"]
    assert len(network_mappings) == 1  # one mapping per (source, capability) pair
    opp = build_opportunity_hypotheses(mappings)
    net_opp = next(o for o in opp if o.capability_id == "network_connectivity")
    assert net_opp.inference_cluster_count == 1


# ---------------------------------------------------------------------------
# AI cannot invent evidence_category / inference_cluster_id / epistemic_status
# ---------------------------------------------------------------------------

def test_ai_response_with_invented_evidence_category_field_fails_schema_validation():
    """The AI response schema is a closed shape (additionalProperties:
    False) that never included evidence_level, evidence_category,
    inference_cluster_id, or epistemic_status as AI-settable fields. A
    response that tries to add one must fail schema validation outright
    (ai_failed) rather than being silently accepted with the extra field
    ignored -- proving there is no path for the AI to inject these values.
    """
    from ai.schema import validate_against_schema

    payload = {
        "account_narrative": "x",
        "capability_narratives": [
            {
                "capability_id": "network_connectivity",
                "narrative": "y",
                "cited_source_urls": [],
                "evidence_category": "organizational_technology_event",  # AI attempting to invent this
            }
        ],
        "next_best_actions": [],
    }
    ok, reason = validate_against_schema(payload)
    assert ok is False
    assert "evidence_category" in reason or "unexpected field" in reason


def test_ai_response_with_invented_epistemic_status_field_fails_schema_validation():
    from ai.schema import validate_against_schema

    payload = {
        "account_narrative": "x",
        "capability_narratives": [],
        "next_best_actions": [
            {
                "capability_id": "network_connectivity",
                "rationale": "y",
                "rank": 1,
                "epistemic_status": "confirmed_evidence",  # AI attempting to invent this
            }
        ],
    }
    ok, reason = validate_against_schema(payload)
    assert ok is False


def test_grounded_capability_narrative_carries_only_app_computed_category_and_status():
    """End-to-end: even a perfectly well-formed, schema-valid AI response
    never influences best_evidence_category/best_epistemic_status on the
    resulting CapabilityNarrative -- those are always copied from the real
    deterministic CapabilityMapping.
    """
    from ai.client import AIResponse
    from ai.synthesis import synthesize_account
    from research.acquisition import AcquisitionDiagnostics, AcquisitionRun
    from research.analysis.report import build_report
    from research.organization import ResolvedOrganization

    class _FakeClient:
        def __init__(self, response):
            self._response = response

        def is_configured(self):
            return True

        def complete_json(self, prompt, *, schema_name):
            return self._response

    org = ResolvedOrganization(
        name=ORG_NAME, country="Nordholm", website="https://www.example-meridian.test",
        linkedin_company_url=None, website_was_supplied=False, linkedin_was_supplied=False,
    )
    diagnostics = AcquisitionDiagnostics(
        organization_name=ORG_NAME, resolved_website=org.website, resolved_linkedin_company_url=None,
        started_at=0.0, finished_at=1.0,
    )
    s = _source(
        url="https://example.com/announcement", source_type="official",
        content=f"{ORG_NAME} announced a cloud migration initiative to a new cloud platform for its core workloads.",
    )
    # qualify_corpus requires >= 3 qualified sources for the corpus to be
    # analyzed at all -- two filler sources keep this an end-to-end
    # "qualified" report rather than an insufficient_evidence short-circuit.
    filler_1 = _source(
        url="https://example.com/press-2", source_type="business",
        content=f"{ORG_NAME} published its quarterly community engagement update covering several ongoing programmes.",
    )
    filler_2 = _source(
        url="https://example.com/press-3", source_type="business",
        content=f"{ORG_NAME} participated in an industry roundtable discussing sector-wide trends and best practices.",
    )
    run = AcquisitionRun(organization=org, sources=[s, filler_1, filler_2], diagnostics=diagnostics)
    report = build_report(run)
    direct = next(m for m in report.capability_mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE)

    response = AIResponse(
        parsed_json={
            "account_narrative": "Evidence shows a confirmed cloud initiative.",
            "capability_narratives": [
                {"capability_id": direct.capability_id, "narrative": "Confirmed.", "cited_source_urls": [direct.source_url]}
            ],
            "next_best_actions": [],
        },
        model="fake-model",
    )
    result = synthesize_account(report, _FakeClient(response))
    assert result.capability_narratives
    narrative = result.capability_narratives[0]
    assert narrative.best_evidence_category == direct.evidence_category
    assert narrative.best_epistemic_status == direct.epistemic_status

"""Current / Change / Requirement Signal Gate (approved design, this pass).

Closes the one gap the adversarial brief found: Rule 2/3 Branch 1 in
commercial/evidence_mapping.py ("two or more distinct specific catalogue
terms co-occur, org-associated") previously promoted straight to
DIRECT_EVIDENCE with no requirement that any change/requirement signal be
present nearby -- so a purely descriptive sentence like "Oman Airports is
equipped with modern systems including a security operations center and
zero trust architecture" cleared it on keyword-count alone. This is fixed
by requiring a locally-grounded change/requirement signal (reusing the same
tech_event_signal/qualifying_signal machinery Branches 2/3 already use) for
DIRECT_EVIDENCE; without one, the mapping is downgraded to STRONG_INFERENCE
/ EXISTING_CAPABILITY_CONTEXT rather than dropped.

Guardrail (critical, tested explicitly below): an EXISTING_CAPABILITY_CONTEXT
mapping must never, by itself, create or inflate a sales opportunity
(commercial/opportunities.py) -- it may remain as visible supporting context
when a genuinely qualifying mapping ALSO exists for the same capability.

This file also covers the generalized shared-generic-trigger ambiguity rule
(the "digital transformation" fan-out across Cloud & AI / Cybersecurity /
Enterprise Managed Services) and the narrow, pre-existing "investment"
qualifying-signal path for the direct-keyword locality gate.

Fixtures are deliberately fictional, mirroring the rest of this suite.
"""

from __future__ import annotations

from commercial.evidence_mapping import map_all_sources, map_source_to_capabilities
from commercial.models import EvidenceCategory, EvidenceLevel
from commercial.opportunities import build_opportunity_hypotheses
from research.sources import AccessStatus, Source

ORG_NAME = "Verimark National Airports Authority"
COUNTRY = "Verimark"
BANK_ORG_NAME = "Castellane Bank"


def _source(*, url: str = "https://example.com/a", content: str, source_type: str = "official") -> Source:
    return Source(
        url=url, domain=url.split("/")[2], title="", source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic",
        content=content, access_status=AccessStatus.FETCHED,
    )


def _mapping_for(mappings, capability_id):
    return next((m for m in mappings if m.capability_id == capability_id), None)


# ---------------------------------------------------------------------------
# 1. The core gap: existing-capability-only mentions are downgraded, not
#    promoted to DIRECT_EVIDENCE, and never independently generate an
#    opportunity. (Guardrail #1's two named examples, verbatim.)
# ---------------------------------------------------------------------------

def test_existing_capability_only_is_downgraded_not_direct_evidence():
    content = (
        f"{ORG_NAME} is equipped with modern systems including a security operations center "
        "and zero trust architecture across its facilities."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    m = _mapping_for(mappings, "cybersecurity_resilience")
    assert m is not None
    assert m.evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert m.evidence_category == EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value


def test_existing_capability_only_produces_no_opportunity_alone_airport_example():
    content = (
        f"{ORG_NAME} is equipped with modern systems including a security operations center "
        "and zero trust architecture across its facilities."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    opps = {o.capability_id: o for o in build_opportunity_hypotheses(mappings)}
    assert "cybersecurity_resilience" not in opps


def test_existing_capability_only_produces_no_opportunity_alone_bank_infra_example():
    content = (
        f"{BANK_ORG_NAME}'s infrastructure includes a cloud platform, ai-ready infrastructure, "
        "and a modern data platform, among the most advanced in the region."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME, country="Castellane")
    m = _mapping_for(mappings, "cloud_ai_infrastructure")
    assert m is not None
    assert m.evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert m.evidence_category == EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value
    opps = {o.capability_id: o for o in build_opportunity_hypotheses(mappings)}
    assert "cloud_ai_infrastructure" not in opps


def test_existing_capability_context_never_becomes_direct_evidence_even_with_more_terms():
    """Adding a THIRD state-only term must not somehow tip this back to
    DIRECT_EVIDENCE -- the gate is about the presence of a change signal,
    never about how many static terms happen to co-occur."""
    content = (
        f"{ORG_NAME} operates a security operations center, maintains a disaster recovery plan, "
        "and follows strict regulatory compliance across its facilities."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    m = _mapping_for(mappings, "cybersecurity_resilience")
    assert m is not None
    assert m.evidence_level != EvidenceLevel.DIRECT_EVIDENCE
    assert m.evidence_category == EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value


# ---------------------------------------------------------------------------
# 2. Coexistence: an existing-capability mapping alongside a genuinely
#    qualifying one for the SAME capability still produces an opportunity,
#    but the opportunity's evidence_level/confidence come from the
#    qualifying mapping -- never inflated or set by the existing-capability
#    one, and the existing-capability mapping is still visible (evidence_count).
# ---------------------------------------------------------------------------

def test_existing_capability_mapping_remains_as_supporting_context_when_qualifying_evidence_exists():
    content_existing = (
        f"{ORG_NAME} is equipped with modern systems including a security operations center "
        "and zero trust architecture across its facilities."
    )
    content_qualifying = (
        f"{ORG_NAME} announced it is upgrading its ransomware protection following a recent "
        "cyber security incident."
    )
    s1 = _source(url="https://example.com/airport-a", content=content_existing)
    s2 = _source(url="https://example2.com/airport-b", content=content_qualifying)
    mappings = map_all_sources([s1, s2], organization_name=ORG_NAME, country=COUNTRY)

    cap_mappings = [m for m in mappings if m.capability_id == "cybersecurity_resilience"]
    categories = {m.evidence_category for m in cap_mappings}
    assert EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value in categories
    assert EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value != EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT.value

    opps = {o.capability_id: o for o in build_opportunity_hypotheses(mappings)}
    opp = opps.get("cybersecurity_resilience")
    assert opp is not None
    qualifying_mapping = next(m for m in cap_mappings if m.evidence_category != EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value)
    existing_mapping = next(m for m in cap_mappings if m.evidence_category == EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value)
    # The opportunity's evidence_level/confidence trace to the QUALIFYING
    # mapping, not the existing-capability one.
    assert opp.evidence_level == qualifying_mapping.evidence_level.value
    assert opp.confidence == qualifying_mapping.confidence
    assert opp.confidence != existing_mapping.confidence
    # But the existing-capability mapping is still counted as visible
    # supporting evidence, not silently dropped.
    assert opp.evidence_count == 2


# ---------------------------------------------------------------------------
# 3. Genuine current/change/requirement signals still qualify -- including
#    the widened deploy/implement verb forms.
# ---------------------------------------------------------------------------

def test_upgrading_language_qualifies_as_direct_or_strong():
    content = f"{ORG_NAME} is upgrading its security operations center and zero trust architecture."
    s = _source(content=content)
    m = _mapping_for(map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY), "cybersecurity_resilience")
    assert m.evidence_level in (EvidenceLevel.DIRECT_EVIDENCE, EvidenceLevel.STRONG_INFERENCE)
    assert m.evidence_category != EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value


def test_security_infrastructure_tender_qualifies():
    content = f"{ORG_NAME} issued a tender for a new security operations center and disaster recovery plan."
    s = _source(content=content)
    m = _mapping_for(map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY), "cybersecurity_resilience")
    assert m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE
    assert m.evidence_category == EvidenceCategory.PROCUREMENT_SIGNAL.value


def test_deployed_past_tense_now_recognized_as_technology_initiative_signal():
    """Regression for the widened `deploy(s|ed|ing)?` regex (previously only
    `deploys?` -- missed the past tense entirely)."""
    from commercial.signals import extract_signals
    s = _source(content=f"{ORG_NAME} deployed a new private network across its terminals.")
    signals = extract_signals(s)
    assert any(sig.signal_type == "technology_initiative" for sig in signals)


def test_implemented_past_tense_now_recognized_as_technology_initiative_signal():
    """Regression for the added `implement(s|ed|ing)?` alternation
    (previously only the noun form "implementation of" was covered)."""
    from commercial.signals import extract_signals
    s = _source(content=f"{ORG_NAME} implemented a new security platform across its facilities.")
    signals = extract_signals(s)
    assert any(sig.signal_type == "technology_initiative" for sig in signals)


def test_implemented_security_platform_qualifies_end_to_end():
    content = (
        f"{ORG_NAME} implemented a new security operations center and zero trust architecture "
        "across its facilities."
    )
    s = _source(content=content)
    m = _mapping_for(map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY), "cybersecurity_resilience")
    assert m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE
    assert m.evidence_category != EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value


# ---------------------------------------------------------------------------
# 4. Lexical-density negative control -- proves the gate is not solvable by
#    keyword/term counting alone.
# ---------------------------------------------------------------------------

def test_many_weak_generic_terms_with_zero_change_signal_stays_unpromotable():
    content = (
        f"{ORG_NAME} says cybersecurity, cloud, AI, network, and managed services are among the "
        "technology areas its business operates in."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    for m in mappings:
        assert m.evidence_level == EvidenceLevel.WEAK_RELEVANCE
    opps = build_opportunity_hypotheses(mappings)
    assert opps == []


# ---------------------------------------------------------------------------
# 5. Generalized shared-generic-trigger ambiguity (the SC digital-
#    transformation fan-out). Computed from the catalogue, no trigger name
#    hardcoded in production code.
# ---------------------------------------------------------------------------

def test_shared_generic_trigger_is_detected_purely_from_the_catalogue():
    from commercial.evidence_mapping import _shared_generic_triggers
    shared = _shared_generic_triggers()
    assert "digital transformation" in shared  # shared by 3 catalogue capabilities today
    assert "acquisition" in shared              # shared by several catalogue capabilities today


def test_digital_transformation_alone_does_not_fan_out_to_cybersecurity():
    content = (
        f"{BANK_ORG_NAME} is pursuing a cloud-first digital transformation strategy. The bank "
        "recently completed a technology programme as part of this broader effort, migrating "
        "its core banking platform to the cloud."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME, country="Castellane")
    cloud = _mapping_for(mappings, "cloud_ai_infrastructure")
    cyber = _mapping_for(mappings, "cybersecurity_resilience")
    assert cloud is not None and cloud.evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert cyber is None or cyber.evidence_level == EvidenceLevel.WEAK_RELEVANCE


def test_digital_transformation_alone_does_not_fan_out_to_enterprise_managed_services():
    """The harder case: EMS's own weak_signal_terms list includes the bare
    word "transformation", which is trivially contained inside the trigger
    phrase "digital transformation" itself. That must not count as
    independent corroboration (the self-overlap fix in
    _locally_supported_by_context_terms)."""
    content = (
        f"{BANK_ORG_NAME} is pursuing a cloud-first digital transformation strategy. The bank "
        "recently completed a technology programme as part of this broader effort, migrating "
        "its core banking platform to the cloud."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME, country="Castellane")
    ems = _mapping_for(mappings, "enterprise_managed_services")
    assert ems is None or ems.evidence_level == EvidenceLevel.WEAK_RELEVANCE
    opps = {o.capability_id: o for o in build_opportunity_hypotheses(mappings)}
    assert "enterprise_managed_services" not in opps
    assert "cybersecurity_resilience" not in opps
    assert "cloud_ai_infrastructure" in opps


def test_digital_transformation_can_still_support_cybersecurity_with_genuine_local_corroboration():
    """The trigger is not banned outright -- when the surrounding text
    actually uses that capability's own vocabulary, promotion is preserved."""
    content = (
        f"{BANK_ORG_NAME} is pursuing a cloud-first digital transformation strategy, strengthening "
        "its cybersecurity posture as part of a technology programme."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME, country="Castellane")
    cyber = _mapping_for(mappings, "cybersecurity_resilience")
    assert cyber is not None
    assert cyber.evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert cyber.evidence_category == EvidenceCategory.BUSINESS_SIGNAL_INFERENCE.value


# ---------------------------------------------------------------------------
# 6. "investment" qualifying-signal path (pre-existing in
#    _QUALIFYING_SIGNAL_TYPES for the direct-keyword locality gate --
#    unchanged by this pass). Positive: locally associated with a specific
#    keyword. Negative: investment genuinely unrelated/out of locality range.
# ---------------------------------------------------------------------------

def test_investment_locally_associated_with_specific_keyword_qualifies():
    content = f"{ORG_NAME} today announced a major investment in additional compute capacity to support future growth."
    s = _source(content=content)
    m = _mapping_for(map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY), "data_centre_infrastructure")
    assert m is not None
    assert m.evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert m.signal_type == "investment"


def test_investment_unrelated_to_the_capability_does_not_qualify():
    """Negative control: the investment is about an unrelated training
    academy, several sentences away from the only capability-keyword
    mention, so it must not be treated as locally grounding that mention."""
    content = (
        f"{ORG_NAME} today announced a major investment in a new regional training academy for "
        "staff development. The academy will open next year and focus on aviation careers. "
        "It is part of a broader community initiative unrelated to core operations. "
        "In an unrelated part of its estate, the airport also runs its own compute capacity "
        "for internal operations."
    )
    s = _source(content=content)
    m = _mapping_for(map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY), "data_centre_infrastructure")
    assert m is not None
    assert m.evidence_level == EvidenceLevel.WEAK_RELEVANCE


# ---------------------------------------------------------------------------
# 7. Full 200-baseline + new-test regression marker (sanity: this module's
#    own tests must not accidentally rely on any catalogue/organization
#    hardcoding).
# ---------------------------------------------------------------------------

def test_no_organization_or_sector_name_hardcoded_in_the_gate_logic():
    import inspect
    from commercial import evidence_mapping
    src = inspect.getsource(evidence_mapping._shared_generic_triggers) + inspect.getsource(evidence_mapping._generic_trigger_is_ambiguous)
    for forbidden in ("Oman", "Siemens", "Standard Chartered", "airport", "bank", "Castellane", "Verimark"):
        assert forbidden not in src
def test_existing_capability_with_two_grounded_terms_does_not_become_direct_without_change_signal():
    content = (
        f"{ORG_NAME} is equipped with modern systems including a security operations center "
        "and zero trust architecture across its facilities."
    )
    s = _source(content=content)

    mappings = map_source_to_capabilities(
        s,
        organization_name=ORG_NAME,
        country=COUNTRY,
    )

    m = _mapping_for(mappings, "cybersecurity_resilience")
    assert m is not None
    assert m.evidence_category == EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value
    assert m.evidence_level != EvidenceLevel.DIRECT_EVIDENCE

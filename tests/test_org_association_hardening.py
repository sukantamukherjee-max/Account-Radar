"""Phase 1C-H3: organization-association / evidence-boundary hardening.

Regression coverage for the false-positive pattern found on a real Oman
Airports live run: an Enterprise Managed Services direct_evidence mapping
whose matched term ("digital transformation programme") and backing
technology_initiative signal came from a passage about a NATIONAL digital
transformation programme with 66 participating companies -- not from
anything said about the target organization's own technology requirement.
The target organization was mentioned elsewhere in the same
(Oman-Algeria cooperation) article, in a different context entirely.

None of the fixtures below reference Oman Airports, aviation, Oman,
Algeria, or any other real organization/sector -- they use a generic
placeholder organization name and a generic government/MOU article shape
that could apply to any account.
"""

from __future__ import annotations

from commercial.evidence_mapping import map_all_sources, map_source_to_capabilities
from commercial.models import EvidenceLevel
from research.sources import AccessStatus, Source

ORG_NAME = "Meridian Ports Authority"


def _source(*, source_type: str, content: str, url: str = "https://example.com/a") -> Source:
    return Source(
        url=url, domain="example.com", title="", source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic",
        content=content, access_status=AccessStatus.FETCHED,
    )


# ---------------------------------------------------------------------------
# The reported false-positive pattern (item 7)
# ---------------------------------------------------------------------------

def test_org_mentioned_in_broader_national_programme_article_is_not_direct_evidence():
    """Reproduces the exact reported shape: the target organization is
    named in one part of a government cooperation article (signing an MOU
    with a partner country), and a national digital-transformation
    programme -- involving many OTHER companies -- is described elsewhere
    in the same article. The organization's own technology requirement is
    never established, so this must not become direct_evidence for any
    capability even though a specific capability phrase
    ("digital transformation programme") and a technology_initiative
    signal are both genuinely present in the source.
    """
    content = (
        f"{ORG_NAME} and its counterpart signed a Memorandum of Understanding this week to strengthen "
        "cooperation on transport and logistics. "
        "The signing ceremony was attended by senior officials from both administrations, who praised the "
        "long-standing relationship between the two organizations. "
        "\n\n"
        "In a separate development, the Ministry announced that the National Digital Transformation "
        "Programme, launched two years ago, now counts participation from 66 companies across the country's "
        "transport, logistics, finance and manufacturing sectors, as part of the broader national strategy "
        "to modernize public and private sector technology infrastructure."
    )
    s = _source(source_type="official", content=content)  # high trust
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)

    ems_mappings = [m for m in mappings if m.capability_id == "enterprise_managed_services"]
    assert ems_mappings, "expected the phrase to be retained as evidence, just not as direct_evidence"
    for m in ems_mappings:
        assert m.evidence_level != EvidenceLevel.DIRECT_EVIDENCE, (
            f"a national-programme mention unconnected to {ORG_NAME} must not become direct_evidence"
        )


def test_map_all_sources_applies_the_same_gate_via_organization_name_kwarg():
    """The same pattern, exercised through map_all_sources (the entry
    point research.analysis.report actually calls), confirming the
    organization_name kwarg threads through correctly.
    """
    content = (
        f"{ORG_NAME} representatives attended a regional summit on infrastructure cooperation. "
        "The summit brought together delegates from a dozen countries to discuss shared development priorities. "
        "Elsewhere, delegates also discussed the region's broader digital transformation programme and cloud "
        "migration initiatives across dozens of unrelated public agencies."
    )
    s = _source(source_type="technology", content=content)
    mappings = map_all_sources([s], organization_name=ORG_NAME)
    direct = [m for m in mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    assert direct == []


def test_without_organization_context_the_association_gate_is_a_no_op():
    """When no organization_name is supplied (e.g. a caller exercising
    evidence_mapping.py directly, or older code that hasn't been updated
    to pass it), behavior must match pre-H3 semantics -- this is what lets
    all of the Phase 1C-H2 tests keep passing unchanged.
    """
    content = (
        f"{ORG_NAME} was mentioned in passing at the event. "
        "Analysts noted the announcement drew broad regional media attention. "
        "The region's national digital transformation programme now includes 66 participating companies."
    )
    s = _source(source_type="official", content=content)
    with_org = map_source_to_capabilities(s, organization_name=ORG_NAME)
    without_org = map_source_to_capabilities(s)  # no organization_name -- gate opts out

    ems_with_org = [m for m in with_org if m.capability_id == "enterprise_managed_services"]
    ems_without_org = [m for m in without_org if m.capability_id == "enterprise_managed_services"]
    assert all(m.evidence_level != EvidenceLevel.DIRECT_EVIDENCE for m in ems_with_org)
    # Without organization context, the pre-H3 rules apply: a single
    # specific term backed by a technology_initiative signal from a
    # high-trust source is direct_evidence.
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in ems_without_org)


# ---------------------------------------------------------------------------
# Positive case: organization-specific announcement in an official source
# still becomes direct_evidence (item 8)
# ---------------------------------------------------------------------------

def test_organization_specific_technology_announcement_in_official_source_is_still_direct_evidence():
    """An official source that explicitly ties the technology event to the
    target organization itself -- not a national programme, not a partner
    country, not another company -- must still reach direct_evidence. This
    proves the H3 gate narrows false positives without breaking the
    legitimate case.
    """
    content = (
        f"{ORG_NAME} today announced the completion of its own digital transformation programme, covering a "
        "new cloud platform and modernized IT operations across its facilities. "
        f"The {ORG_NAME} programme replaces legacy systems that had been in place for over a decade."
    )
    s = _source(source_type="official", content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    ems_mappings = [m for m in mappings if m.capability_id == "enterprise_managed_services"]
    assert ems_mappings
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in ems_mappings)


def test_organization_specific_procurement_still_direct_evidence_with_org_context():
    """Regression: the Phase 1C-H procurement-RFP case, now also passing
    organization_name, must remain direct_evidence -- the RFP text itself
    names the organization near the technology terms.
    """
    content = (
        f"{ORG_NAME} has issued a request for proposal (RFP) for storage infrastructure to support expanded "
        f"compute capacity at {ORG_NAME}'s primary facility."
    )
    s = _source(source_type="procurement", content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    dc_mappings = [m for m in mappings if m.capability_id == "data_centre_infrastructure"]
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in dc_mappings)


def test_two_distinct_terms_near_organization_mention_still_direct_evidence():
    content = (
        f"{ORG_NAME} deployed a new data platform using GPU compute to power its AI-driven operations "
        f"planning at {ORG_NAME} headquarters."
    )
    s = _source(source_type="news", content=content)  # medium trust -- association, not trust, is being tested
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    cloud_mappings = [m for m in mappings if m.capability_id == "cloud_ai_infrastructure"]
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cloud_mappings)


# ---------------------------------------------------------------------------
# Preservation as context/inference rather than silent deletion (item 4)
# ---------------------------------------------------------------------------

def test_unassociated_national_programme_mention_is_preserved_as_lower_tier_evidence():
    """The org/technology mismatch case must not vanish from the report --
    it should still surface, just correctly leveled down."""
    content = (
        f"{ORG_NAME} signed a cooperation MOU with a neighboring authority. "
        "The agreement covers general knowledge-sharing and joint working groups over the coming year. "
        "Separately, the ministry highlighted the national digital transformation programme's growth to 66 "
        "participating companies nationwide."
    )
    s = _source(source_type="official", content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    ems_mappings = [m for m in mappings if m.capability_id == "enterprise_managed_services"]
    assert ems_mappings, "evidence must be retained, not dropped, even though it's not direct"
    assert all(m.evidence_level in (EvidenceLevel.STRONG_INFERENCE, EvidenceLevel.WEAK_RELEVANCE) for m in ems_mappings)

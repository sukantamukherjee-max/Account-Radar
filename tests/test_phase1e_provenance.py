"""Phase 1E provenance and sales-signal hardening tests."""
from __future__ import annotations

from commercial.evidence_mapping import map_source_to_capabilities
from commercial.models import EvidenceLevel
from research.sources import AccessStatus, Source


def _source(*, url: str, title: str, content: str, source_type: str = "technology") -> Source:
    return Source(
        url=url, domain="example.com", title=title, source_type=source_type,
        snippet="", discovered_via_query="q", discovery_provider="test",
        family="generic", content=content, access_status=AccessStatus.FETCHED,
    )


def test_technographics_never_becomes_direct_or_strong():
    s = _source(
        url="https://example.com/technology-stack",
        title="Example Company Technology Stack - Technographics",
        content="Example Company uses DDoS protection, cloud platform, backup and recovery.",
    )
    mappings = map_source_to_capabilities(s, organization_name="Example Company")
    assert mappings
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in mappings)
    assert all(m.confidence <= 35 for m in mappings)


def test_linkedin_job_is_not_capability_proof():
    s = _source(
        url="https://www.linkedin.com/jobs/view/12345",
        title="Example Company hiring VP Digital Transformation",
        content="Example Company is hiring a VP Digital Transformation to lead cloud, cybersecurity and digital transformation.",
    )
    mappings = map_source_to_capabilities(s, organization_name="Example Company")
    assert mappings
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in mappings)


def test_generic_integration_without_local_org_context_does_not_become_strong():
    s = _source(
        url="https://example.com/article",
        title="Regional infrastructure update",
        source_type="business",
        content=(
            "Example Company attended a regional event. "
            "The article separately discusses integration and digital transformation programmes across other organizations."
        ),
    )
    mappings = map_source_to_capabilities(s, organization_name="Example Company")
    assert all(m.evidence_level != EvidenceLevel.STRONG_INFERENCE for m in mappings)


def test_organization_specific_new_site_can_still_infer_network_need():
    """Current/Change/Requirement Signal Gate update: "new site"/"facility
    expansion" are generic_triggers SHARED by several catalogue capabilities
    (network_connectivity, data_centre_infrastructure, cybersecurity_
    resilience, enterprise_managed_services all list them), so under the
    approved catalogue-derived shared-trigger rule they now require local,
    capability-specific corroboration -- exactly the same mechanism that
    already applied to "capacity" triggers before this pass, generalized.
    The fixture is updated to add genuine network-specific vocabulary
    ("network connectivity", "bandwidth") next to the trigger, preserving
    this test's original intent (a real business signal can still ground a
    network-need inference) without relying on bare generic-trigger language
    that could equally have meant any of several other capabilities.
    """
    s = _source(
        url="https://example.com/press",
        title="Example Company expands to a new site",
        source_type="business",
        content=(
            "Example Company is expanding into a new site to support future operations, "
            "requiring additional network connectivity and bandwidth at the new location."
        ),
    )
    mappings = map_source_to_capabilities(s, organization_name="Example Company")
    network = [m for m in mappings if m.capability_id == "network_connectivity"]
    assert network
    assert any(m.evidence_level == EvidenceLevel.STRONG_INFERENCE for m in network)


def test_specific_official_technology_event_remains_direct():
    s = _source(
        url="https://example.com/announcement",
        title="Example Company announces cloud migration",
        source_type="official",
        content=(
            "Example Company announced a cloud migration initiative to a new cloud platform "
            "for its core workloads."
        ),
    )
    mappings = map_source_to_capabilities(s, organization_name="Example Company")
    cloud = [m for m in mappings if m.capability_id == "cloud_ai_infrastructure"]
    assert cloud
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cloud)

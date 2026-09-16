"""Phase 1C-H3.1: LinkedIn personal-profile cap regression tests.

Reproduces the reported regression from a real Oman Airports live run: a
personal LinkedIn `/in/...` profile (on a country-localized subdomain,
`om.linkedin.com`) reached `direct_evidence` (confidence 90) for
Cybersecurity & Resilience, backed by three specific terms and a
technology_initiative signal -- something the H2 LinkedIn cap was
explicitly supposed to prevent regardless of match count.

Root cause (see the long comment above `_is_linkedin_personal_profile_url`
in commercial/evidence_mapping.py): the H2 cap keyed off
`source.source_type == "linkedin_profile"`, a value set once upstream at
acquisition time based on which discovery family found the URL. A
personal LinkedIn URL that instead surfaces via a different family (or a
localized host the upstream classifier didn't anticipate, like
`om.linkedin.com`) can carry a different source_type and silently skip
the cap. These tests use a generic placeholder organization and a
synthetic (non-real) person/profile -- no real organization, sector, or
person is referenced.
"""

from __future__ import annotations

from commercial.evidence_mapping import map_source_to_capabilities
from commercial.models import EvidenceLevel
from research.sources import AccessStatus, Source

ORG_NAME = "Meridian Ports Authority"


def _source(*, url: str, title: str, source_type: str, content: str) -> Source:
    return Source(
        url=url, domain="linkedin.com" if "linkedin.com" in url else "example.com",
        title=title, source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic",
        content=content, access_status=AccessStatus.FETCHED,
    )


# ---------------------------------------------------------------------------
# The exact reported regression pattern
# ---------------------------------------------------------------------------

def test_personal_linkedin_profile_with_multiple_specific_terms_cannot_be_direct_evidence():
    """The precise reported shape: a personal profile on a localized
    LinkedIn subdomain (om.linkedin.com/in/...), MISCLASSIFIED with a
    non-LinkedIn source_type (as if a generic discovery family had found
    it), containing multiple highly specific cybersecurity terms and a
    real technology-event signal. Must never reach direct_evidence.
    """
    s = _source(
        url="https://om.linkedin.com/in/synthetic-security-person",
        title=f"Synthetic Person — {ORG_NAME}",
        source_type="technology",  # deliberately NOT "linkedin_profile" -- reproduces the misclassification
        content=(
            f"Synthetic Person — Head of Information Security at {ORG_NAME}. Leading the SOC build-out, "
            "regulatory compliance programme and enterprise cyber risk management. Rolled out a new zero "
            "trust architecture and security operations center as part of a technology modernisation "
            "programme."
        ),
    )
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    cyber_mappings = [m for m in mappings if m.capability_id == "cybersecurity_resilience"]
    assert cyber_mappings, "evidence should still be retained, just capped"
    assert all(m.evidence_level != EvidenceLevel.DIRECT_EVIDENCE for m in cyber_mappings)


def test_personal_linkedin_profile_cannot_be_strong_inference_either():
    """Same fixture: must not land at strong_inference either -- the H2/H3
    rule is a hard weak_relevance cap, not merely "not direct"."""
    s = _source(
        url="https://om.linkedin.com/in/synthetic-security-person",
        title=f"Synthetic Person — {ORG_NAME}",
        source_type="business",  # also deliberately misclassified
        content=(
            f"Synthetic Person — Head of Information Security at {ORG_NAME}. Leading the SOC build-out, "
            "regulatory compliance programme and enterprise cyber risk management."
        ),
    )
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    cyber_mappings = [m for m in mappings if m.capability_id == "cybersecurity_resilience"]
    assert cyber_mappings
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in cyber_mappings)
    assert all(m.confidence <= 35 for m in cyber_mappings)
    # Evidence is preserved, not discarded (item 4).
    for m in cyber_mappings:
        assert m.matched_terms


def test_correctly_classified_linkedin_profile_still_capped_too():
    """Regression guard for the ORIGINAL H2 mechanism: a profile correctly
    tagged source_type="linkedin_profile" must still be capped (this is
    what test_capability_mapping.py already covers, repeated here to keep
    all LinkedIn-cap coverage co-located and to prove the two detection
    paths -- source_type and URL -- agree).
    """
    s = _source(
        url="https://www.linkedin.com/in/synthetic-security-person-2",
        title=f"Synthetic Person 2 — {ORG_NAME}",
        source_type="linkedin_profile",
        content=(
            f"Synthetic Person 2 — CISO at {ORG_NAME}. SOC, regulatory compliance and cyber risk programmes."
        ),
    )
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    cyber_mappings = [m for m in mappings if m.capability_id == "cybersecurity_resilience"]
    assert cyber_mappings
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in cyber_mappings)


# ---------------------------------------------------------------------------
# LinkedIn company pages must remain distinct (item 3)
# ---------------------------------------------------------------------------

def test_linkedin_company_page_is_not_treated_as_personal_profile():
    """A /company/ page on linkedin.com (even on a localized subdomain)
    must NOT be swept up by the personal-profile cap -- it should be
    classified by its actual source_type/trust tier as usual.
    """
    s = _source(
        url="https://om.linkedin.com/company/meridian-ports-authority",
        title=f"{ORG_NAME} | LinkedIn",
        source_type="linkedin_company",
        content=(
            f"{ORG_NAME} announced completion of its own digital transformation programme, including a new "
            f"security operations center and zero trust architecture rollout across {ORG_NAME} facilities."
        ),
    )
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    cyber_mappings = [m for m in mappings if m.capability_id == "cybersecurity_resilience"]
    assert cyber_mappings
    # A company page is medium trust and eligible for direct_evidence when
    # the underlying rules (2+ terms, or signal-backed) are satisfied --
    # unlike a personal profile, it is not hard-capped.
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cyber_mappings)


def test_linkedin_company_url_helper_does_not_match_company_paths():
    from commercial.evidence_mapping import _is_linkedin_personal_profile_url

    assert _is_linkedin_personal_profile_url("https://www.linkedin.com/in/someone") is True
    assert _is_linkedin_personal_profile_url("https://om.linkedin.com/in/someone") is True
    assert _is_linkedin_personal_profile_url("https://www.linkedin.com/company/some-org") is False
    assert _is_linkedin_personal_profile_url("https://om.linkedin.com/company/some-org") is False
    assert _is_linkedin_personal_profile_url("https://example.com/in/not-linkedin") is False
    assert _is_linkedin_personal_profile_url("") is False


# ---------------------------------------------------------------------------
# Positive case: genuine organization-specific technology evidence from a
# non-LinkedIn official/technology/procurement source is unaffected (item 8)
# ---------------------------------------------------------------------------

def test_official_source_technology_evidence_still_direct_evidence_after_h3_1():
    s = _source(
        url="https://www.example-official.test/news/security-programme",
        title=f"{ORG_NAME} technology announcement",
        source_type="official",
        content=(
            f"{ORG_NAME} today announced the completion of its cybersecurity programme, including a new "
            f"security operations center and zero trust architecture across {ORG_NAME}'s facilities."
        ),
    )
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    cyber_mappings = [m for m in mappings if m.capability_id == "cybersecurity_resilience"]
    assert cyber_mappings
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cyber_mappings)


def test_procurement_source_technology_evidence_still_direct_evidence_after_h3_1():
    s = _source(
        url="https://www.example-tenders.test/notices/rfp-soc",
        title="RFP: Security operations",
        source_type="procurement",
        content=(
            f"{ORG_NAME} has issued a request for proposal for a security operations center and regulatory "
            f"compliance monitoring capability to support {ORG_NAME}'s cyber risk programme."
        ),
    )
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME)
    cyber_mappings = [m for m in mappings if m.capability_id == "cybersecurity_resilience"]
    assert cyber_mappings
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cyber_mappings)

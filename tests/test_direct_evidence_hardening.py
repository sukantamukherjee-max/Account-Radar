"""Phase 1C-H2: direct_evidence hardening tests.

Regression coverage for the specific false-positive pattern reported from a
real Oman Airports run: a partnership/MOU/cooperation/regional-infrastructure
article that mentions a generic technology word in passing was being
promoted to `direct_evidence` purely because the source's trust tier was
high -- without the article actually describing any explicit technology
requirement, initiative, procurement action, deployment, or renewal at the
organization itself.

None of these tests reference Oman Airports, aviation, or any other real
organization/sector -- the fixtures below are generic, reusable patterns
(an MOU/cooperation announcement, a regional-infrastructure roundup) that
could apply to any account.
"""

from __future__ import annotations

from commercial.capabilities import get_catalog
from commercial.evidence_mapping import map_source_to_capabilities
from commercial.models import EvidenceLevel
from research.sources import AccessStatus, Source


def _source(*, source_type: str, content: str, url: str = "https://example.com/a") -> Source:
    return Source(
        url=url, domain="example.com", title="", source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic",
        content=content, access_status=AccessStatus.FETCHED,
    )


# ---------------------------------------------------------------------------
# The reported false-positive pattern
# ---------------------------------------------------------------------------

def test_mou_partnership_article_with_generic_tech_word_is_not_direct_evidence():
    """A cooperation/MOU announcement between two organizations, mentioning
    a generic technology word ('network') only in passing, must not become
    direct_evidence -- there is no explicit technology requirement,
    initiative, upgrade, or procurement action described.
    """
    s = _source(
        source_type="official",  # high trust tier
        content=(
            "The two organizations signed a Memorandum of Understanding to strengthen cooperation in "
            "transport and airport management, covering knowledge sharing, joint working groups and "
            "future collaboration across their transport network of operations."
        ),
    )
    mappings = map_source_to_capabilities(s)
    network_mappings = [m for m in mappings if m.capability_id == "network_connectivity"]
    assert network_mappings, "expected the generic 'network' mention to be retained as weak evidence, not dropped"
    for m in network_mappings:
        assert m.evidence_level != EvidenceLevel.DIRECT_EVIDENCE


def test_regional_infrastructure_roundup_article_does_not_manufacture_direct_evidence():
    """A regional infrastructure/PPP roundup article that name-checks an
    organization alongside general infrastructure commentary must not be
    promoted to direct_evidence for any capability just because it comes
    from a high-trust source and contains sector-generic words.
    """
    s = _source(
        source_type="technology",  # high trust tier
        content=(
            "Infrastructure and public-private partnerships across the region continue to attract investment, "
            "with governments prioritising transport, energy and network infrastructure as part of broader "
            "economic diversification plans."
        ),
    )
    mappings = map_source_to_capabilities(s)
    direct = [m for m in mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    assert direct == []


def test_high_trust_single_generic_keyword_without_tech_event_signal_is_not_direct_evidence():
    """Isolate the mechanism directly: a high-trust source with exactly one
    specific-but-unaccompanied capability keyword and no technology-event
    signal must not reach direct_evidence, regardless of which capability.
    """
    s = _source(
        source_type="official",
        content="The organization discussed its long-term strategy, briefly noting that fiber remains important.",
    )
    mappings = map_source_to_capabilities(s)
    for m in mappings:
        assert m.evidence_level != EvidenceLevel.DIRECT_EVIDENCE


# ---------------------------------------------------------------------------
# Proving the fix doesn't over-correct: genuine single-term + real
# technology-event evidence must still reach direct_evidence.
# ---------------------------------------------------------------------------

def test_single_specific_keyword_with_technology_event_signal_is_still_direct_evidence():
    """A high-trust source describing an actual technology upgrade/renewal
    event, naming one specific capability term, should still reach
    direct_evidence -- the fix must not make direct_evidence unreachable
    via a single term, only require it to be backed by a real event.
    """
    s = _source(
        source_type="official",
        content=(
            "The organization announced it has upgraded its transport network as part of a technology "
            "modernisation programme completed this quarter."
        ),
    )
    mappings = map_source_to_capabilities(s)
    network_mappings = [m for m in mappings if m.capability_id == "network_connectivity"]
    assert network_mappings
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in network_mappings)
    direct = [m for m in network_mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE][0]
    assert direct.signal_type == "technology_initiative"


def test_two_distinct_specific_terms_still_reach_direct_evidence_without_a_signal():
    """The pre-existing "two or more distinct specific terms co-occurring"
    rule must be preserved unchanged -- this is not gated on a
    technology-event signal, per the original (still-valid) rationale that
    such co-occurrence is unlikely to be coincidental.
    """
    s = _source(
        source_type="news",  # medium trust -- proves this path doesn't depend on trust tier either
        content="The organization deployed a new data platform using GPU compute for its AI workloads.",
    )
    mappings = map_source_to_capabilities(s)
    cloud_mappings = [m for m in mappings if m.capability_id == "cloud_ai_infrastructure"]
    direct = [m for m in cloud_mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    assert direct
    assert len(direct[0].matched_terms) >= 2


def test_procurement_rfp_still_reaches_direct_evidence():
    """Regression: the existing procurement-RFP case (single-or-more
    specific terms, high trust, backed by a procurement_activity signal)
    must remain direct_evidence after this change.
    """
    s = _source(
        source_type="procurement",
        content=(
            "The organization has issued a request for proposal (RFP) for storage infrastructure to support "
            "expanded compute capacity."
        ),
    )
    mappings = map_source_to_capabilities(s)
    dc_mappings = [m for m in mappings if m.capability_id == "data_centre_infrastructure"]
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in dc_mappings)


# ---------------------------------------------------------------------------
# Signal vs technology-evidence vs capability-hypothesis separation (item 4)
# ---------------------------------------------------------------------------

def test_partnership_signal_is_retained_as_context_without_becoming_direct_technology_evidence():
    """A partnership/MOU article should still surface as a customer
    signal (via commercial.signals, independent of capability mapping) and
    may still support a strong_inference-level capability hypothesis when a
    generic trigger co-occurs -- but must never be reported as direct
    technology evidence on the strength of the partnership alone.
    """
    from commercial.signals import extract_signals

    s = _source(
        source_type="business",
        content=(
            "The organization signed a strategic partnership agreement and announced plans for geographic "
            "expansion into a new facility that will require additional network connectivity capacity, alongside a new digital platform."
        ),
    )
    signals = extract_signals(s)
    assert any(sig.signal_type == "partnership" for sig in signals)

    mappings = map_source_to_capabilities(s)
    direct = [m for m in mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    # No specific capability keyword was used verbatim (only generic
    # trigger phrases like "new facility"/"new digital platform"), so
    # nothing here should ever reach direct_evidence purely off the
    # partnership + expansion language.
    assert direct == []
    assert any(m.evidence_level == EvidenceLevel.STRONG_INFERENCE for m in mappings)


# ---------------------------------------------------------------------------
# Catalogue schema regression guard (defense in depth for the root cause)
# ---------------------------------------------------------------------------

def test_no_capability_has_a_term_in_both_keywords_and_weak_signal_terms():
    """Direct regression test for the root cause: a term must never live in
    both the 'specific/direct' bucket and the 'generic/weak' bucket for the
    same capability -- that duplication is exactly what let a bare word
    like 'network' be treated as strong evidence. capabilities.py's loader
    already enforces this at load time (raises ValueError); this test
    additionally asserts it holds for the catalogue actually shipped.
    """
    for capability in get_catalog():
        keyword_set = {k.strip().lower() for k in capability["keywords"]}
        weak_set = {w.strip().lower() for w in capability.get("weak_signal_terms", [])}
        overlap = keyword_set & weak_set
        assert not overlap, f"{capability['id']!r} has overlapping keyword/weak terms: {overlap}"

"""Phase 1C-H items 2-6: evidence-first capability mapping tests."""

from __future__ import annotations

from commercial.evidence_mapping import map_all_sources, map_source_to_capabilities
from commercial.incumbents import detect_incumbent_evidence
from commercial.models import EvidenceLevel
from commercial.signals import extract_signals
from research.sources import AccessStatus, Source


def _source(
    *, url="https://example.com/a", title="", snippet="", content="", source_type="news", family="generic_news"
) -> Source:
    return Source(
        url=url, domain="example.com", title=title, source_type=source_type, snippet=snippet,
        discovered_via_query="q", discovery_provider="test", family=family, content=content or None,
        access_status=AccessStatus.FETCHED if content else AccessStatus.INDEXED_SNIPPET_ONLY,
    )


# ---------------------------------------------------------------------------
# False-positive reduction (item 3)
# ---------------------------------------------------------------------------

def test_generic_single_word_cloud_mention_is_weak_not_direct():
    """A single incidental 'cloud' word must never become a confident Cloud
    opportunity -- it should surface at most as weak_relevance.
    """
    s = _source(
        source_type="news",
        content="The company held its annual staff picnic under a clear blue sky with barely a cloud in sight.",
    )
    mappings = map_source_to_capabilities(s)
    cloud_mappings = [m for m in mappings if m.capability_id == "cloud_ai_infrastructure"]
    assert cloud_mappings, "expected at least a weak-relevance candidate to be retained as evidence"
    for m in cloud_mappings:
        assert m.evidence_level == EvidenceLevel.WEAK_RELEVANCE
        assert m.confidence <= 35


def test_official_source_with_specific_multi_term_cloud_language_is_direct_evidence():
    s = _source(
        source_type="technology",
        content=(
            "As part of its digital transformation programme, the organization announced a major cloud migration "
            "initiative moving core workloads to a new cloud platform with GPU compute for its AI infrastructure."
        ),
    )
    mappings = map_source_to_capabilities(s)
    cloud_mappings = [m for m in mappings if m.capability_id == "cloud_ai_infrastructure"]
    assert cloud_mappings
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cloud_mappings)
    direct = [m for m in cloud_mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE][0]
    assert len(direct.matched_terms) >= 2
    assert direct.confidence >= 60


def test_news_article_with_many_tech_terms_does_not_map_to_every_capability_as_direct():
    """A news article that name-drops many technology terms in passing
    should not produce a DIRECT_EVIDENCE mapping for every single Otech
    capability -- only where the evidence is actually specific.
    """
    s = _source(
        source_type="news",
        content=(
            "In broader industry news this week, commentators discussed trends in network equipment, storage "
            "hardware, security software and outsourcing deals across the sector, without naming any particular "
            "company's plans."
        ),
    )
    mappings = map_source_to_capabilities(s)
    direct = [m for m in mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    # Generic, unspecific trend-piece language should not earn direct
    # evidence status for capabilities it only brushes against with single
    # weak/generic terms.
    assert direct == []


# ---------------------------------------------------------------------------
# LinkedIn profile false positives (item 3 / 7)
# ---------------------------------------------------------------------------

def test_linkedin_profile_mentioning_storage_is_capped_at_weak_relevance():
    s = _source(
        url="https://www.linkedin.com/in/jane-doe",
        source_type="linkedin_profile",
        family="linkedin_people",
        snippet="Jane Doe — Storage and Backup Systems Engineer at a technology company. 10+ years in storage infrastructure and backup and recovery design.",
    )
    mappings = map_source_to_capabilities(s)
    dc_mappings = [m for m in mappings if m.capability_id == "data_centre_infrastructure"]
    assert dc_mappings
    for m in dc_mappings:
        assert m.evidence_level == EvidenceLevel.WEAK_RELEVANCE
        assert m.confidence <= 35


def test_linkedin_profile_never_produces_direct_evidence_regardless_of_term_count():
    s = _source(
        url="https://www.linkedin.com/in/john-smith",
        source_type="linkedin_profile",
        family="linkedin_people",
        snippet=(
            "John Smith — leads cloud migration, cloud platform strategy, GPU compute and AI infrastructure "
            "programmes. Cybersecurity programme owner. Zero trust architecture advocate."
        ),
    )
    mappings = map_source_to_capabilities(s)
    assert mappings, "expected some weak-relevance evidence to be retained"
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in mappings)


# ---------------------------------------------------------------------------
# Direct evidence vs strong inference vs weak relevance (item 2)
# ---------------------------------------------------------------------------

def test_business_signal_without_direct_capability_language_is_strong_inference():
    """Current/Change/Requirement Signal Gate update: "facility expansion"/
    "new site"/"new programme" are generic_triggers SHARED across several
    catalogue capabilities, so under the approved catalogue-derived
    shared-trigger rule they now require local, capability-specific
    corroboration before promoting ANY capability -- otherwise the bare
    business-expansion language is exactly as ambiguous about which
    capability it concerns as "digital transformation" is. The fixture adds
    genuine network-specific vocabulary so this test still exercises a
    real, capability-corroborated inference rather than bare generic-trigger
    language alone.
    """
    s = _source(
        source_type="business",
        content=(
            "The organization announced a major facility expansion, adding a new site to support future growth "
            "as part of a new programme, including new fiber connectivity to serve the site."
        ),
    )
    mappings = map_source_to_capabilities(s)
    # generic_triggers like "facility expansion"/"new site"/"new programme" apply
    # to multiple capabilities via inference (backed by an extracted "expansion"/"project" signal).
    assert mappings
    assert all(m.evidence_level in (EvidenceLevel.STRONG_INFERENCE, EvidenceLevel.WEAK_RELEVANCE) for m in mappings)
    assert any(m.evidence_level == EvidenceLevel.STRONG_INFERENCE for m in mappings)
    inferred = [m for m in mappings if m.evidence_level == EvidenceLevel.STRONG_INFERENCE][0]
    assert inferred.signal_type in ("expansion", "project")
    assert 35 <= inferred.confidence <= 70


def test_evidence_levels_are_ordered_direct_gt_inference_gt_weak_in_confidence():
    """Current/Change/Requirement Signal Gate update: the `inference` fixture
    below relied on "new facility"/"new programme" alone -- generic_triggers
    now SHARED across several capabilities under the approved catalogue-
    derived rule, requiring local capability-specific corroboration. Network-
    specific vocabulary is added so this remains a genuine strong_inference
    case rather than bare, capability-ambiguous language.
    """
    direct = _source(source_type="official", content="The organization deployed a new data centre with expanded storage infrastructure and compute capacity.")
    inference = _source(
        source_type="business",
        content="The organization is expanding into a new facility as part of a new programme, upgrading its network connectivity to support the site.",
    )
    weak = _source(source_type="news", content="Commentary mentioned storage in passing as an industry trend.")

    d = [m for m in map_source_to_capabilities(direct) if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    i = [m for m in map_source_to_capabilities(inference) if m.evidence_level == EvidenceLevel.STRONG_INFERENCE]
    w = [m for m in map_source_to_capabilities(weak) if m.evidence_level == EvidenceLevel.WEAK_RELEVANCE]
    assert d and i and w
    assert d[0].confidence > i[0].confidence > w[0].confidence


# ---------------------------------------------------------------------------
# Competitor / incumbent separation (item 5)
# ---------------------------------------------------------------------------

def test_incumbent_vendor_mention_is_retained_but_not_a_capability_mapping():
    s = _source(
        source_type="news",
        content="The organization renewed its long-standing partnership with Cisco for its network infrastructure.",
    )
    incumbents = detect_incumbent_evidence(s)
    assert any(i.vendor_or_partner_term.lower() == "cisco" for i in incumbents)
    assert any(i.category == "incumbent_language" for i in incumbents)

    mappings = map_source_to_capabilities(s)
    network_mappings = [m for m in mappings if m.capability_id == "network_connectivity"]
    # A capability mapping may still exist (network language is present),
    # but it must never be DIRECT_EVIDENCE purely because a vendor name
    # was mentioned, and it must carry the incumbent context rather than
    # silently ignoring it.
    for m in network_mappings:
        assert m.evidence_level != EvidenceLevel.DIRECT_EVIDENCE or "network" in " ".join(m.matched_terms).lower()
        assert "cisco" in [t.lower() for t in m.incumbent_context]


def test_incumbent_presence_does_not_boost_confidence():
    """Current/Change/Requirement Signal Gate update: "new facility" is a
    generic_trigger SHARED across several capabilities, now requiring local
    capability-specific corroboration under the approved catalogue-derived
    rule. Network-specific vocabulary is added to BOTH fixtures identically
    so the comparison this test makes (incumbent mention must not inflate
    confidence) remains apples-to-apples.
    """
    with_incumbent = _source(
        source_type="business",
        content=(
            "The organization is expanding into a new facility, requiring additional network "
            "connectivity. Cisco is the incumbent network vendor."
        ),
    )
    without_incumbent = _source(
        source_type="business",
        content="The organization is expanding into a new facility, requiring additional network connectivity.",
    )
    a = [m for m in map_source_to_capabilities(with_incumbent) if m.capability_id == "network_connectivity"]
    b = [m for m in map_source_to_capabilities(without_incumbent) if m.capability_id == "network_connectivity"]
    assert a and b
    # Confidence should not be higher merely because a vendor name appeared.
    assert a[0].confidence <= b[0].confidence + 5  # allow small noise from extra matched term text length only


# ---------------------------------------------------------------------------
# Source-level traceability (item 6)
# ---------------------------------------------------------------------------

def test_every_mapping_has_a_source_url_and_title():
    sources = [
        _source(url="https://example.com/news1", source_type="news", content="The organization announced a major cloud migration initiative and new cloud platform."),
        _source(url="https://www.linkedin.com/in/someone", source_type="linkedin_profile", family="linkedin_people", snippet="Storage engineer."),
    ]
    mappings = map_all_sources(sources)
    assert mappings
    for m in mappings:
        assert m.source_url.startswith("http")
        assert m.source_url in {s.url for s in sources}


def test_no_mapping_created_for_source_without_url():
    s = _source(url="", content="cloud migration cloud platform GPU compute")
    assert map_source_to_capabilities(s) == []


def test_signal_extraction_is_source_attributed():
    s = _source(url="https://example.com/press", content="The company announced a major facility expansion this quarter.")
    signals = extract_signals(s)
    assert signals
    for sig in signals:
        assert sig.source_url == "https://example.com/press"

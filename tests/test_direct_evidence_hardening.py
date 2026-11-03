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
from commercial.evidence_mapping import _mentions_organization, _org_core_tokens, map_source_to_capabilities
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


# ---------------------------------------------------------------------------
# Fix B: single-remaining-core-token organization association must require
# the literal full organization name, not a raw or word-boundary token
# match. Confirmed live false-positive class (Oman Air investigation): an
# organization name that reduces to one short/generic token after country
# stripping in _org_core_tokens (e.g. "Oman Air" -> ["air"]) is too weak to
# trust alone -- both as a substring ("chairman", "fairness") and as a
# genuine whole word ("air traffic").
# ---------------------------------------------------------------------------

def test_org_token_does_not_match_as_substring_of_unrelated_word_fairness():
    tokens = _org_core_tokens("Oman Air", country="Oman")
    assert tokens == ["air"]
    assert not _mentions_organization(
        "Manually tracking documentation and ensuring fairness in vendor selection is difficult.",
        "Oman Air", tokens,
    )


def test_org_token_does_not_match_as_substring_of_unrelated_word_chairman():
    tokens = _org_core_tokens("Oman Air", country="Oman")
    assert not _mentions_organization(
        "The chairman of the company announced a new partnership.",
        "Oman Air", tokens,
    )


def test_org_token_does_not_match_as_substring_of_unrelated_word_repair():
    tokens = _org_core_tokens("Oman Air", country="Oman")
    assert not _mentions_organization(
        "This approach helps repair gaps in the existing process.",
        "Oman Air", tokens,
    )


def test_org_token_does_not_match_as_a_genuine_but_unrelated_whole_word():
    """The harder case word-boundary matching alone does not solve: 'air
    traffic' is a genuine whole-word match of the token 'air', but is not
    evidence of Oman Air. The single-token early return must reject this
    too, not just the substring-inside-another-word cases above.
    """
    tokens = _org_core_tokens("Oman Air", country="Oman")
    assert not _mentions_organization(
        "The regional hub handles significant air traffic each year.",
        "Oman Air", tokens,
    )


def test_org_token_still_matches_via_the_literal_full_organization_name():
    tokens = _org_core_tokens("Oman Air", country="Oman")
    assert _mentions_organization(
        "Oman Air announced a new partnership this quarter.",
        "Oman Air", tokens,
    )


def test_multi_token_organization_name_word_boundary_matching_unaffected():
    """Positive control: an organization with more than one core token is
    unaffected by the single-token early return, and still benefits from
    word-boundary (not substring) matching.
    """
    tokens = _org_core_tokens("Meridian Ports Authority", country="Nordholm")
    assert len(tokens) > 1
    # Whole-word occurrence of enough tokens still matches.
    assert _mentions_organization(
        "Meridian Ports handled record cargo volumes this year.",
        "Meridian Ports Authority", tokens,
    )
    # A token embedded inside an unrelated longer word must not count.
    assert not _mentions_organization(
        "The portable meridianite alloy was used in the new equipment.",
        "Meridian Ports Authority", tokens,
    )


# ---------------------------------------------------------------------------
# End-to-end live false-positive reproductions
# ---------------------------------------------------------------------------

def test_aufait_shaped_generic_marketing_page_does_not_reach_direct_evidence():
    """Reproduces the confirmed live mechanism: no genuine Oman Air mention
    anywhere on the page; organization association was only ever reachable
    via the 'fairness' substring match on the single remaining token 'air'.
    """
    content = (
        "Manually tracking documentation and ensuring fairness in vendor selection is difficult. "
        "How RFP Software Helps: Logs every action with an audit trail, making the process "
        "traceable. Implements role-based access controls to protect sensitive information. "
        "Regulatory compliance tracking (ISO, SOC 2, GDPR, etc.) is included for teams that need it."
    )
    s = _source(source_type="technology", content=content)
    mappings = map_source_to_capabilities(s, organization_name="Oman Air", country="Oman")
    assert not any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in mappings)
    cyber = [m for m in mappings if m.capability_id == "cybersecurity_resilience"]
    assert not any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cyber)


def test_zawya_shaped_unrelated_press_release_does_not_associate_with_org():
    """Reproduces the confirmed live mechanism on the second source: no
    genuine Oman Air mention; organization association was only reachable
    via the 'chairman' substring match on the single remaining token 'air'.
    """
    content = (
        "The chairman of Otech announced a new collaboration with Kaspersky to expand locally "
        "hosted cybersecurity services in Oman."
    )
    s = _source(source_type="news", content=content)
    mappings = map_source_to_capabilities(s, organization_name="Oman Air", country="Oman")
    assert not any(
        m.evidence_level in (EvidenceLevel.DIRECT_EVIDENCE, EvidenceLevel.STRONG_INFERENCE)
        for m in mappings
    )


# ---------------------------------------------------------------------------
# Organization-identity + industry-context hardening (Oman Airports
# Management Company review). These deliberately use the real account name
# that exposed the defect: a multi-word legal name whose non-country words
# include generic corporate words ("management", "company").
# ---------------------------------------------------------------------------

from commercial.evidence_mapping import _org_short_name  # noqa: E402
from commercial.models import EpistemicStatus  # noqa: E402
from commercial.opportunities import build_opportunity_hypotheses  # noqa: E402

_OAMC = "Oman Airports Management Company"
_OA_SHORT = "Oman Airports"


def _titled_source(*, source_type: str, content: str, url: str, title: str) -> Source:
    from urllib.parse import urlparse
    return Source(
        url=url, domain=urlparse(url).netloc, title=title, source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic",
        content=content, access_status=AccessStatus.FETCHED,
    )


def _no_direct_and_no_confirmed(mappings) -> None:
    assert not any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in mappings)
    assert not any(m.epistemic_status == EpistemicStatus.CONFIRMED_EVIDENCE.value for m in mappings)
    assert not any(
        o.epistemic_status == EpistemicStatus.CONFIRMED_EVIDENCE.value
        for o in build_opportunity_hypotheses(mappings)
    )


def test_vendor_article_never_naming_org_is_not_direct_and_creates_no_confirmed_opportunity():
    """Generic corporate words ('company', 'management') plus 'airports' in
    ordinary prose must not stand in for the organization's identity. The
    URL/title carry no trend marker, so this exercises the organization-
    association fix on its own (not the industry-context guard)."""
    s = _titled_source(
        source_type="technology",
        url="https://vendor.example/blog/securing-ot-networks",
        title="Securing OT networks",
        content=(
            "Airports and airport management companies are deploying a security operations center "
            "and zero trust architecture to protect OT networks. Every company's security management "
            "team is implementing ransomware defences and a disaster recovery plan."
        ),
    )
    for org in (_OAMC, _OA_SHORT):
        mappings = map_source_to_capabilities(s, organization_name=org, country="Oman")
        assert mappings, "expected the topical matches to be retained as context"
        _no_direct_and_no_confirmed(mappings)


def test_trend_article_naming_org_as_example_is_not_direct_evidence():
    """A third-party trend page that names the organization only as an
    example must not become confirmed/direct evidence merely because the
    organization name appears next to capability terms."""
    s = _titled_source(
        source_type="technology",
        url="https://vendor.example/blog/airport-technology-trends-2026",
        title="Airport Technology Trends 2026",
        content=(
            "Airports like Oman Airports are deploying a security operations center and zero trust "
            "architecture to protect OT networks. Ransomware remains a key cyber risk."
        ),
    )
    for org in (_OAMC, _OA_SHORT):
        mappings = map_source_to_capabilities(s, organization_name=org, country="Oman")
        cyber = [m for m in mappings if m.capability_id == "cybersecurity_resilience"]
        assert cyber, "industry context must be retained for traceability"
        assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in cyber)
        _no_direct_and_no_confirmed(mappings)


def test_first_party_short_name_reaches_direct_evidence_under_both_account_names():
    s = _titled_source(
        source_type="official",
        url="https://www.omanairports.example/en/media/news/soc-launch",
        title="Oman Airports launches cyber security operations centre",
        content=(
            "Oman Airports has deployed a new security operations center and zero trust architecture "
            "at Muscat International Airport. The implementation strengthens cyber risk management."
        ),
    )
    for org in (_OAMC, _OA_SHORT):
        cyber = [
            m for m in map_source_to_capabilities(s, organization_name=org, country="Oman")
            if m.capability_id == "cybersecurity_resilience"
        ]
        assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cyber), org
        assert all(
            m.epistemic_status == EpistemicStatus.CONFIRMED_EVIDENCE.value
            for m in cyber if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE
        )


def test_full_legal_name_still_associates_and_reaches_direct_evidence():
    s = _titled_source(
        source_type="news",
        url="https://news.example/business/oman-airports-zero-trust",
        title="Oman Airports to implement zero trust architecture",
        content=(
            "Oman Airports Management Company is implementing a zero trust architecture and a new "
            "security operations center, the company said on Monday."
        ),
    )
    cyber = [
        m for m in map_source_to_capabilities(s, organization_name=_OAMC, country="Oman")
        if m.capability_id == "cybersecurity_resilience"
    ]
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cyber)


def test_body_trend_language_does_not_demote_genuine_news():
    """The industry-context guard reads URL/title only: a genuine account
    news item that mentions 'industry trends' in its body is unaffected."""
    s = _titled_source(
        source_type="news",
        url="https://news.example/business/oman-airports-soc",
        title="Oman Airports deploys new security operations center",
        content=(
            "In line with industry trends and market trends, Oman Airports has deployed a new security "
            "operations center and zero trust architecture at Muscat International Airport."
        ),
    )
    cyber = [
        m for m in map_source_to_capabilities(s, organization_name=_OAMC, country="Oman")
        if m.capability_id == "cybersecurity_resilience"
    ]
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cyber)


def test_generic_corporate_words_do_not_establish_organization_identity():
    tokens = _org_core_tokens(_OAMC, country="Oman")
    assert tokens == ["airports"]
    for sentence in (
        "The company's management team is deploying a security operations center.",
        "Airports and airport management companies are deploying new systems.",
        "Airports, management and company structures were discussed.",
    ):
        assert not _mentions_organization(sentence, _OAMC, tokens, country="Oman"), sentence
    # The multi-word short form and the full legal name both still associate.
    assert _mentions_organization("Oman Airports has deployed a new SOC.", _OAMC, tokens, country="Oman")
    assert _mentions_organization("Oman Airports Management Company said today.", _OAMC, tokens, country="Oman")


def test_short_name_alias_is_never_a_single_word_or_country_only():
    assert _org_short_name(_OAMC, country="Oman") == "Oman Airports"
    assert _org_short_name("Airports Management Company", country="Oman") is None  # would be one word
    assert _org_short_name("Oman National Company", country="Oman") == "Oman National"
    assert _org_short_name("Oman Group Holdings LLC", country="Oman") is None  # one word left
    assert _org_short_name("Gulf Services International Company", country="Oman") is None  # one word left
    assert _org_short_name("Oman Air", country="Oman") is None  # nothing to strip
    assert _org_short_name("Meridian Ports Authority", country="Nordholm") is None


def test_linkedin_and_executive_bio_downgrades_keep_level_and_epistemic_status_consistent():
    profile = _titled_source(
        source_type="business",  # deliberately mis-tagged upstream
        url="https://om.linkedin.com/in/example-person",
        title="Example Person - Head of IT - Oman Airports",
        content=(
            "Oman Airports has deployed a new security operations center and zero trust architecture. "
            "Leading the implementation of the disaster recovery plan."
        ),
    )
    bio = _titled_source(
        source_type="official",
        url="https://www.omanairports.example/en/leadership/cio",
        title="Leadership",
        content=(
            "He is responsible for digital transformation. Before joining the company he served "
            "in several technology roles, and he holds a degree in engineering. "
            "Oman Airports has deployed a new security operations center and zero trust architecture."
        ),
    )
    for s in (profile, bio):
        mappings = map_source_to_capabilities(s, organization_name=_OAMC, country="Oman")
        assert mappings
        for m in mappings:
            assert m.evidence_level == EvidenceLevel.WEAK_RELEVANCE
            assert m.epistemic_status == EpistemicStatus.INFERENCE_HYPOTHESIS.value

"""Evidence-intelligence fix: generic industry / vendor / contextual content
must never be classified as account-specific DIRECT evidence or produce a
CONFIRMED opportunity by itself.

Root cause reproduced here (see the long comment above
commercial.evidence_mapping._source_is_account_centred): a third-party
article that mentioned the account only in passing -- or only as an example
("airports such as X") -- could reach direct_evidence / confirmed_evidence
at 80-95% because (1) the open-web "technology" discovery family is treated
as high trust, (2) a generic sentence adjacent to a passing account sentence
borrowed that account mention, and (3) exemplar mentions counted as account
mentions. Nothing asked whether the SOURCE was about the account.

Test map (task spec):
  A  -- generic industry article                      -> no direct / no confirmed
  B  -- account-specific announcement                 -> still direct
  C  -- vendor-published article with real account news -> not disqualified by domain
  D  -- several generic contextual articles           -> no confirmed opportunity
  E  -- existing procurement / project / contract / technology-event /
        customer-requirement tests                    -> unchanged (rest of suite)
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from commercial.evidence_mapping import (
    _is_generic_industry_content,
    _mentions_organization,
    _org_core_tokens,
    _source_is_account_centred,
    map_all_sources,
    map_source_to_capabilities,
)
from commercial.models import CapabilityMapping, EpistemicStatus, EvidenceCategory, EvidenceLevel
from commercial.opportunities import build_opportunity_hypotheses
from research.sources import AccessStatus, Source

OAMC = "Oman Airports Management Company"
COUNTRY = "Oman"
CYBER = "cybersecurity_resilience"
CONFIRMED = EpistemicStatus.CONFIRMED_EVIDENCE.value


def _src(*, url: str, title: str, content: str, source_type: str = "technology") -> Source:
    return Source(
        url=url, domain=urlparse(url).netloc, title=title, source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic",
        content=content, access_status=AccessStatus.FETCHED,
    )


def _map(s: Source, org: str = OAMC, country: str = COUNTRY):
    return map_source_to_capabilities(s, organization_name=org, country=country)


def _cyber(mappings):
    return [m for m in mappings if m.capability_id == CYBER]


def _assert_not_confirmed(mappings) -> None:
    assert not any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in mappings)
    assert not any(m.epistemic_status == CONFIRMED for m in mappings)
    assert not any(o.epistemic_status == CONFIRMED for o in build_opportunity_hypotheses(mappings))


# ---------------------------------------------------------------------------
# Test A -- generic industry article
# ---------------------------------------------------------------------------

def test_a_airport_technology_trends_article_is_industry_context_not_account_evidence():
    s = _src(
        url="https://vendor.example/blog/10-airport-technology-trends-2026",
        title="10 Airport Technology Trends Transforming Aviation Operations in 2026",
        content=(
            "Oman Airports is deploying a security operations center and zero trust architecture "
            "to protect OT networks. Airports everywhere face ransomware and need a disaster recovery plan."
        ),
    )
    mappings = _map(s)
    cyber = _cyber(mappings)
    assert cyber, "industry context must be retained for traceability, not dropped"
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in cyber)
    assert all(m.evidence_category == EvidenceCategory.INDUSTRY_CONTEXT.value for m in cyber)
    _assert_not_confirmed(mappings)


def test_a_incident_shape_vendor_ot_explainer_with_passing_account_mention():
    """The reported failure: a vendor article about airport OT cybersecurity,
    found by the 'technology' discovery family (high trust), naming the
    account once in passing. Before the fix: direct_evidence /
    confirmed_evidence at 95%."""
    s = _src(
        url="https://vendor.example/insights/why-ot-cyber-resilience-matters",
        title="Why OT cyber resilience matters for aviation",
        content=(
            "Ransomware attacks on aviation operational technology are rising every year. Regulators "
            "expect operators to test a disaster recovery plan. Many operators now run a security "
            "operations center around the clock. A zero trust architecture limits lateral movement. "
            "In the region, Oman Airports is upgrading its airport systems. Our platform delivers OT "
            "visibility for critical infrastructure."
        ),
    )
    mappings = _map(s)
    assert _cyber(mappings)
    assert all(m.evidence_category == EvidenceCategory.INDUSTRY_CONTEXT.value for m in mappings)
    _assert_not_confirmed(mappings)


def test_a_account_named_only_as_an_example_is_not_an_account_mention():
    s = _src(
        url="https://vendor.example/blog/securing-ot-at-airports",
        title="Securing operational technology at airports",  # no trend/explainer marker
        content=(
            "Airports face a growing wave of ransomware attacks on operational technology. Airports such "
            "as Oman Airports are deploying a security operations center to monitor OT networks. A zero "
            "trust architecture limits lateral movement."
        ),
    )
    mappings = _map(s)
    assert _cyber(mappings)
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in _cyber(mappings))
    _assert_not_confirmed(mappings)


def test_a_generic_sentence_cannot_borrow_an_adjacent_passing_account_mention():
    """Non-account-centred source: the cyber terms sit in generic sentences
    NEXT TO a passing account sentence that itself contains no cyber term."""
    s = _src(
        url="https://vendor.example/blog/aviation-security-operations",
        title="Inside aviation security operations",
        content=(
            "Ransomware groups increasingly target aviation operators across the world. Many operators now "
            "run a security operations center around the clock. Oman Airports is upgrading its terminal "
            "systems this year. A zero trust architecture limits lateral movement between networks."
        ),
    )
    mappings = _map(s)
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in _cyber(mappings))
    _assert_not_confirmed(mappings)


def test_a_passing_but_explicit_account_claim_in_context_source_is_inference_not_confirmed():
    """A context source that does make an explicit, same-sentence claim about
    the account is reclassified as an inference to validate -- not dropped,
    and not confirmed."""
    s = _src(
        url="https://vendor.example/blog/aviation-security-operations",
        title="Inside aviation security operations",
        content=(
            "Ransomware groups increasingly target aviation operators across the world. Many operators "
            "review their incident response every year. Regulators publish new guidance often. "
            "Oman Airports is deploying a security operations center and zero trust architecture this year."
        ),
    )
    mappings = _map(s)
    cyber = _cyber(mappings)
    assert any(m.evidence_level == EvidenceLevel.STRONG_INFERENCE for m in cyber)
    _assert_not_confirmed(mappings)
    opps = [o for o in build_opportunity_hypotheses(mappings) if o.capability_id == CYBER]
    assert opps and opps[0].epistemic_status == EpistemicStatus.INFERENCE_HYPOTHESIS.value


# ---------------------------------------------------------------------------
# Test B -- account-specific announcement stays eligible for direct evidence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source",
    [
        _src(
            url="https://www.omanairports.co.om/en/media/news/cyber-programme",
            title="Cybersecurity programme launched",
            source_type="official",
            content=(
                "Oman Airports has launched a cybersecurity programme and deployed a new security "
                "operations center and zero trust architecture across its airports."
            ),
        ),
        _src(
            url="https://news.example/business/2026/05/airport-operator-cyber",
            title="Airport operator modernises cyber defences",
            source_type="news",
            content=(
                "Oman Airports Management Company is implementing a new security operations center and "
                "zero trust architecture, the company said on Monday. The programme starts this year."
            ),
        ),
    ],
    ids=["official_announcement", "news_lead_sentence"],
)
def test_b_account_specific_announcement_reaches_direct_and_confirmed(source):
    cyber = _cyber(_map(source))
    direct = [m for m in cyber if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    assert direct
    assert all(m.epistemic_status == CONFIRMED for m in direct)
    assert direct[0].evidence_category == EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT.value


def test_b_account_procurement_tender_reaches_direct_procurement_signal():
    s = _src(
        url="https://tenders.example/oman-airports-soc-rfp",
        title="Oman Airports issues RFP for security operations center",
        source_type="procurement",
        content=(
            "Oman Airports has issued a request for proposal (RFP) for a managed security operations "
            "center and zero trust architecture covering its airports."
        ),
    )
    direct = [m for m in _cyber(_map(s)) if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    assert direct and direct[0].evidence_category == EvidenceCategory.PROCUREMENT_SIGNAL.value


# ---------------------------------------------------------------------------
# Test C -- vendor-published article containing genuine account news
# ---------------------------------------------------------------------------

def test_c_vendor_domain_does_not_disqualify_account_specific_selection_news():
    s = _src(
        url="https://xyz-security.example/news/oman-airports-selects-xyz",
        title="Oman Airports selects XYZ for cybersecurity modernisation programme",
        content=(
            "Oman Airports has selected XYZ to deploy a new security operations center and zero trust "
            "architecture as part of its airport cybersecurity modernisation programme. The "
            "implementation will begin this year."
        ),
    )
    direct = [m for m in _cyber(_map(s)) if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    assert direct and direct[0].epistemic_status == CONFIRMED


def test_c_vendor_page_with_account_in_lead_sentence_is_account_centred():
    s = _src(
        url="https://xyz-security.example/press/2026-programme",
        title="New programme announcement",
        content=(
            "Oman Airports has selected XYZ to deploy a new security operations center and zero trust "
            "architecture. XYZ will support the airport cybersecurity modernisation programme."
        ),
    )
    assert _source_is_account_centred(s, s.content, OAMC, country=COUNTRY)
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in _cyber(_map(s)))


def test_c_explainer_title_is_exempt_when_it_names_the_account_as_subject():
    s = _src(
        url="https://xyz-security.example/stories/how-oman-airports-cut-cyber-risk",
        title="How Oman Airports cut cyber risk with a new security operations center",
        content="Oman Airports deployed a new security operations center and zero trust architecture.",
    )
    assert not _is_generic_industry_content(s, organization_name=OAMC, country=COUNTRY)
    # ...but the same explainer shape without the account in the title is context.
    generic = _src(url="https://xyz-security.example/blog/how-to-build-a-soc", title="How to build a SOC", content="x")
    assert _is_generic_industry_content(generic, organization_name=OAMC, country=COUNTRY)


# ---------------------------------------------------------------------------
# Test D -- many contextual sources do not add up to confirmation
# ---------------------------------------------------------------------------

def test_d_multiple_generic_airport_cyber_articles_never_become_confirmed():
    sources = [
        _src(
            url="https://vendor-a.example/blog/airport-cybersecurity-trends-2026",
            title="Airport cybersecurity trends 2026",
            content=(
                "Airports such as Oman Airports are deploying a security operations center. A zero trust "
                "architecture and a disaster recovery plan are now standard."
            ),
        ),
        _src(
            url="https://vendor-b.example/insights/why-airport-ot-security-matters",
            title="Why airport OT security matters",
            content=(
                "Ransomware threatens airport operations. Operators are deploying a security operations "
                "center and zero trust architecture. Oman Airports and other airports face similar risk."
            ),
        ),
        _src(
            url="https://vendor-c.example/blog/securing-airport-networks",
            title="Securing airport networks",
            content=(
                "Airport operators are upgrading to a zero trust architecture. A security operations center "
                "provides continuous monitoring. Many airports, including Oman Airports, publish incident "
                "response plans."
            ),
        ),
    ]
    mappings = map_all_sources(sources, organization_name=OAMC, country=COUNTRY)
    assert _cyber(mappings), "contextual matches are retained"
    _assert_not_confirmed(mappings)


# ---------------------------------------------------------------------------
# Opportunity protection (defense in depth)
# ---------------------------------------------------------------------------

def _mapping(*, level: EvidenceLevel, category: str | None, url: str) -> CapabilityMapping:
    from commercial.models import epistemic_status_for
    return CapabilityMapping(
        capability_id=CYBER, capability_name="Cybersecurity & Resilience", evidence_level=level,
        matched_terms=["security operations center"], source_url=url, source_title="t", confidence=80.0,
        evidence_category=category, inference_cluster_id=url, epistemic_status=epistemic_status_for(level).value,
    )


def test_opportunity_is_not_confirmed_by_a_direct_mapping_with_a_context_category():
    m = _mapping(level=EvidenceLevel.DIRECT_EVIDENCE, category=EvidenceCategory.INDUSTRY_CONTEXT.value,
                 url="https://vendor.example/a")
    opps = build_opportunity_hypotheses([m])
    assert len(opps) == 1, "the opportunity is kept, not deleted"
    assert opps[0].epistemic_status == EpistemicStatus.INFERENCE_HYPOTHESIS.value
    assert opps[0].evidence_level == EvidenceLevel.STRONG_INFERENCE.value


def test_opportunity_is_confirmed_by_a_direct_account_event_mapping():
    m = _mapping(level=EvidenceLevel.DIRECT_EVIDENCE,
                 category=EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT.value, url="https://omanairports.example/a")
    opps = build_opportunity_hypotheses([m])
    assert opps[0].epistemic_status == CONFIRMED
    assert opps[0].evidence_level == EvidenceLevel.DIRECT_EVIDENCE.value


# ---------------------------------------------------------------------------
# Unit-level guarantees
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sentence",
    [
        "Airports such as Oman Airports are deploying new systems.",
        "Operators like Oman Airports are upgrading networks.",
        "Several operators, including Oman Airports, are investing in security.",
        "Oman Airports and other airports in the region face similar threats.",
        "Regional hubs, for example Oman Airports, run security operations.",
    ],
)
def test_exemplar_mentions_do_not_count_as_account_mentions(sentence):
    assert not _mentions_organization(sentence, OAMC, _org_core_tokens(OAMC, country=COUNTRY), country=COUNTRY)


@pytest.mark.parametrize(
    "sentence",
    [
        "Oman Airports has deployed a new security operations center.",
        "Like other operators, Oman Airports is upgrading its networks.",
        "Oman Airports Management Company said it is implementing zero trust.",
        "The programme, launched by Oman Airports, covers all terminals.",
    ],
)
def test_subject_mentions_still_count_as_account_mentions(sentence):
    assert _mentions_organization(sentence, OAMC, _org_core_tokens(OAMC, country=COUNTRY), country=COUNTRY)


def test_fix_is_organization_agnostic():
    """Same behaviour for an unrelated, fictional account -- nothing is
    keyed to Oman Airports."""
    org, country = "Meridian Ports Authority", "Nordholm"
    context = _src(
        url="https://vendor.example/insights/why-port-cyber-resilience-matters",
        title="Why port cyber resilience matters",
        content=(
            "Ports such as Meridian Ports Authority are deploying a security operations center. A zero "
            "trust architecture limits lateral movement."
        ),
    )
    account = _src(
        url="https://news.example/ports/meridian-soc",
        title="Meridian Ports Authority deploys security operations center",
        source_type="news",
        content=(
            "Meridian Ports Authority has deployed a new security operations center and zero trust "
            "architecture across its terminals."
        ),
    )
    _assert_not_confirmed(_map(context, org, country))
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in _cyber(_map(account, org, country)))

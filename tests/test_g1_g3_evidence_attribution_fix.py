"""Regression tests for the G1/G3 evidence-attribution fix (F1 + F2 + F2b
+ F3), traced and designed against the Golden Case Validation Set
(tests/test_golden_cases.py). These tests are new and do not modify any
existing test file or the golden fixtures themselves; the golden G1/G3
tests are expected to pass, unmodified, once this fix is applied.

F1 -- commercial/text_heuristics.py: looks_like_vendor_case_study_span now
      also recognizes customer-listing/name-dropping frames ("trusted by
      X", "customers such as X"), not just first-person delivery language.
F2  -- commercial/evidence_mapping.py: _correlating_signal_has_tech_context
      now requires local technology corroboration for every signal type
      EXCEPT a genuine technology-event signal (fail-closed), rather than
      only for the narrow {expansion, project} set (fail-open elsewhere).
F2b -- commercial/evidence_mapping.py: _locally_supported_by_context_terms
      gained `also_exclude_terms`, so a qualifying keyword cannot
      corroborate itself via a context word contained inside its own text.
F3  -- commercial/signals.py: extract_signals gained `extra_span_filter`,
      so a signal type's first-in-document match is not taken from a span
      the caller's own evidence-relevance disqualifiers would reject,
      permanently shadowing a later, genuine occurrence.
"""

from __future__ import annotations

import pytest

from commercial.evidence_mapping import (
    _locally_supported_by_context_terms,
    map_source_to_capabilities,
)
from commercial.models import EvidenceLevel
from commercial.opportunities import build_opportunity_hypotheses
from commercial.signals import extract_signals
from commercial.text_heuristics import looks_like_vendor_case_study_span
from research.sources import AccessStatus, Source

ORG_NAME = "Verimark National Airports Authority"
COUNTRY = "Verimark"
TARGET_ORG = "Oman Air"
TARGET_COUNTRY = "Oman"

_COMPLIANCE_TAIL = (
    "Regulatory compliance tracking (ISO, SOC 2, GDPR, etc.) Implements role-based "
    "access controls and audit trails for every approval."
)


def _source(*, content: str, source_type: str = "official", url: str = "https://example.test/a") -> Source:
    return Source(
        url=url, domain="example.test", title="", source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="t", family="g", content=content,
        access_status=AccessStatus.FETCHED,
    )


def _evaluate(content: str, *, org: str, country: str | None, source_type: str = "official"):
    mappings = map_source_to_capabilities(_source(content=content, source_type=source_type), organization_name=org, country=country)
    opps = build_opportunity_hypotheses(mappings)
    return mappings, opps


def _promoted(mappings, capability_id: str):
    return [m for m in mappings if m.capability_id == capability_id and m.evidence_level != EvidenceLevel.WEAK_RELEVANCE]


# ---------------------------------------------------------------------------
# F1 unit tests -- looks_like_vendor_case_study_span
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("segment", [
    "Trusted by Oman Air.",
    "Trusted by customers such as Oman Air.",
    "Our platform is used by clients like Oman Air.",
    "Customers including Oman Air rely on our platform.",
])
def test_f1_customer_listing_and_name_dropping_frames_are_detected(segment):
    assert looks_like_vendor_case_study_span(segment)


@pytest.mark.parametrize("segment", [
    "Oman Air today confirmed a major cloud migration initiative.",
    "Oman Air announced it is expanding its cybersecurity capabilities.",
])
def test_f1_organization_originated_statements_are_not_flagged(segment):
    assert not looks_like_vendor_case_study_span(segment)


# ---------------------------------------------------------------------------
# G1 class -- vendor page naming the organization via a customer-listing
# frame, followed by generic product/marketing keyword content. Zero
# opportunities, for both a single-token (post-country-strip) and a
# multi-token organization name.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("org", "country"),
    [(TARGET_ORG, TARGET_COUNTRY), (ORG_NAME, COUNTRY)],
    ids=["single-token-org-after-country-strip", "multi-token-org"],
)
@pytest.mark.parametrize("frame", [
    "Trusted by customers such as {org}.",
    "Our clients include {org}.",
    "Customers like {org} use our platform daily.",
])
def test_g1_customer_listing_frame_on_vendor_page_yields_zero_opportunities(org, country, frame):
    content = f"{frame.format(org=org)} {_COMPLIANCE_TAIL}"
    mappings, opps = _evaluate(content, org=org, country=country, source_type="technology")
    assert opps == [], f"expected zero opportunities, got {[(o.capability_id, o.score) for o in opps]}"
    assert not any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in mappings)


# ---------------------------------------------------------------------------
# G1 recall guards -- genuine organization-attributed procurement/technology
# claims must still promote to DIRECT_EVIDENCE. F1 must not become a
# blanket "organization name + capability keywords" blocker.
# ---------------------------------------------------------------------------

def test_g1_recall_guard_tender_with_adjacent_keywords_still_direct_evidence():
    content = f"{ORG_NAME} has launched a tender. The tender covers storage infrastructure and compute capacity for its new data centre."
    mappings, opps = _evaluate(content, org=ORG_NAME, country=COUNTRY, source_type="procurement")
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE and m.capability_id == "data_centre_infrastructure" for m in mappings)
    assert opps and opps[0].score == 64.5


def test_g1_recall_guard_pronoun_sentence_still_direct_evidence():
    content = f"{ORG_NAME} confirmed its plans on Tuesday. It is deploying new storage infrastructure and compute capacity across its facilities."
    mappings, opps = _evaluate(content, org=ORG_NAME, country=COUNTRY)
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE and m.capability_id == "data_centre_infrastructure" for m in mappings)
    assert opps and opps[0].score == 64.5


# ---------------------------------------------------------------------------
# G3 class -- a genuinely organization-attributed partnership/investment/
# contract-award mention, whose only capability-keyword hit is a homonym
# with no independently-present technology vocabulary, must yield zero
# opportunities. The keyword is retained as weak_relevance, not dropped.
# ---------------------------------------------------------------------------

_G3_MOU = (
    "Oman Air and a regional operator signed a Memorandum of Understanding to strengthen "
    "cooperation in transport and airport management, covering knowledge sharing, joint "
    "working groups and future collaboration across their transport network of operations."
)


def test_g3_partnership_mou_with_homonym_keyword_yields_zero_opportunities():
    mappings, opps = _evaluate(_G3_MOU, org=TARGET_ORG, country=TARGET_COUNTRY)
    assert opps == []
    network = [m for m in mappings if m.capability_id == "network_connectivity"]
    assert network and all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in network), (
        f"homonym keyword evidence must be retained at weak_relevance, not dropped or promoted: "
        f"{[(m.evidence_level.value) for m in network]}"
    )


@pytest.mark.parametrize("sentence_template", [
    "{org} confirmed a major investment to expand its transport network across the region this year.",
    "{org} was awarded a contract to develop its transport network infrastructure.",
    "{org} announced an expansion of its transport network to several new cities this year.",
    "{org} announced a major project to extend its transport network across the region.",
])
def test_g3_generic_qualifying_signals_with_homonym_keyword_and_no_independent_context_yield_zero(sentence_template):
    """investment, contract_award, expansion and project all previously
    escaped corroboration for this exact homonym shape -- investment/
    contract_award because they were never in _CORRELATING_ONLY_SIGNAL_
    TYPES at all (F2), expansion/project because the keyword's own text
    ('network' inside 'transport network') satisfied the corroboration
    check against itself (F2b).
    """
    content = sentence_template.format(org=TARGET_ORG)
    mappings, opps = _evaluate(content, org=TARGET_ORG, country=TARGET_COUNTRY, source_type="news")
    assert opps == [], f"expected zero opportunities, got {[(o.capability_id, o.score) for o in opps]}"


# ---------------------------------------------------------------------------
# G3 controls -- a qualifying signal WITH genuine, independent technology
# context must still promote. This is the behavior F2/F2b must not break.
# ---------------------------------------------------------------------------

def test_g3_control_partnership_with_independent_technology_context_still_promotes():
    """The existing NVIDIA/Oakridge partnership fixture: 'AI' is genuine,
    independent context for cloud_ai_infrastructure, not contained inside
    the qualifying term itself.
    """
    content = (
        "CES 2026: NVIDIA and Oakridge Industrial AG today expanded their strategic partnership "
        "to build the Industrial AI Operating System, accelerating industrial AI innovation together."
    )
    mappings, opps = _evaluate(content, org="Oakridge Industrial AG", country=None)
    assert _promoted(mappings, "cloud_ai_infrastructure")
    assert opps and opps[0].capability_id == "cloud_ai_infrastructure" and opps[0].score == 42.0


def test_g3_control_canonical_expansion_with_independent_network_capacity_still_promotes():
    content = f"{ORG_NAME} is opening a new facility. This expansion will require additional network capacity."
    mappings, opps = _evaluate(content, org=ORG_NAME, country=COUNTRY)
    assert _promoted(mappings, "network_connectivity")
    assert opps and opps[0].score == 42.0


def test_g3_control_own_site_requirement_with_fiber_and_bandwidth_still_promotes():
    content = (
        "Oakridge Industrial AG is building a new facility that must be close to gas, water and "
        "fiber connections to support its own operations, as part of a major expansion project "
        "that will also increase the facility's network bandwidth capacity."
    )
    mappings, opps = _evaluate(content, org="Oakridge Industrial AG", country=None)
    assert _promoted(mappings, "network_connectivity")
    assert opps and opps[0].score == 42.0


# ---------------------------------------------------------------------------
# F2b helper unit test -- also_exclude_terms
# ---------------------------------------------------------------------------

def test_f2b_also_exclude_terms_prevents_keyword_self_corroboration():
    text = "Oman Air signed a partnership covering their transport network of operations."
    # "network" is contained inside "transport network" itself -- must NOT
    # count as independent corroboration when excluded.
    assert not _locally_supported_by_context_terms(
        text, ["partnership"], ["network"], also_exclude_terms=("transport network",)
    )
    # The same context term, from a genuinely separate occurrence, still counts.
    text2 = "Oman Air signed a partnership. The deal also covers network upgrades at its main hub."
    assert _locally_supported_by_context_terms(
        text2, ["partnership"], ["network"], also_exclude_terms=("transport network",)
    )


# ---------------------------------------------------------------------------
# F3 unit test -- extract_signals(extra_span_filter=...)
# ---------------------------------------------------------------------------

def test_f3_extra_span_filter_skips_a_disqualified_first_match_and_finds_a_later_genuine_one():
    from commercial.evidence_mapping import _span_disqualifies_evidence

    content = (
        "Government agencies across the whole of government are pursuing a national digital "
        f"transformation programme together. Separately, {ORG_NAME} said it is expanding its own "
        "cybersecurity capabilities as part of a digital transformation initiative, deploying a "
        "new cloud platform to support its own terminal operations this quarter."
    )
    source = _source(content=content)

    without_filter = extract_signals(source)
    tech_without = next((s for s in without_filter if s.signal_type == "technology_initiative"), None)
    assert tech_without is not None
    assert "government" in tech_without.text.lower()  # shadowed by the collective-scope span

    with_filter = extract_signals(source, extra_span_filter=_span_disqualifies_evidence)
    tech_with = next((s for s in with_filter if s.signal_type == "technology_initiative"), None)
    assert tech_with is not None
    # Signal.text is a fixed-width excerpt around match_start, so it may not
    # contain the organization's full name verbatim -- assert on the
    # content that distinguishes the two candidate sentences instead.
    assert "government" not in tech_with.text.lower()
    assert "expanding its own cybersecurity" in tech_with.text.lower()


def test_f3_default_behavior_is_unchanged_with_no_filter():
    content = f"{ORG_NAME} today confirmed a major cloud migration initiative."
    source = _source(content=content)
    assert extract_signals(source) == extract_signals(source, extra_span_filter=None)


def test_f3_wired_into_evidence_mapping_government_wide_fixture_now_reaches_direct_evidence():
    """End-to-end: with F3 wired into map_source_to_capabilities' own call
    to extract_signals, the organization's own, separate claim in this
    fixture is no longer shadowed by the earlier collective-scope mention,
    and correctly reaches DIRECT_EVIDENCE.
    """
    content = (
        "Government agencies across the whole of government are pursuing a national digital "
        f"transformation programme together. Separately, {ORG_NAME} said it is expanding its own "
        "cybersecurity capabilities as part of a digital transformation initiative, deploying a "
        "new cloud platform to support its own terminal operations this quarter."
    )
    mappings, opps = _evaluate(content, org=ORG_NAME, country=COUNTRY)
    cloud = [m for m in mappings if m.capability_id == "cloud_ai_infrastructure"]
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in cloud)
    # The unrelated, still-collective-scope-disqualified government mapping
    # for the SAME capability must not itself be treated as customer evidence.
    assert _promoted(mappings, "cybersecurity_resilience")  # organization's own separate claim


# ---------------------------------------------------------------------------
# Preservation -- Fix B (organization-token association) is untouched by
# this patch.
# ---------------------------------------------------------------------------

def test_fix_b_single_token_organization_association_still_rejects_substring_traps():
    from commercial.evidence_mapping import _mentions_organization, _org_core_tokens

    tokens = _org_core_tokens(TARGET_ORG, country=TARGET_COUNTRY)
    assert tokens == ["air"]
    assert not _mentions_organization("The chairman announced a new partnership.", TARGET_ORG, tokens)
    assert not _mentions_organization("Ensuring fairness in vendor selection is difficult.", TARGET_ORG, tokens)
    assert _mentions_organization(f"{TARGET_ORG} announced a new partnership.", TARGET_ORG, tokens)

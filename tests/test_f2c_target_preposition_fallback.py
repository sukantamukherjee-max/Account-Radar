"""Regression tests for F2c: the scoped target-preposition fallback in
commercial.evidence_mapping._correlating_signal_has_tech_context.

Background: most multi-word catalogue keywords are built from [a modifier]
+ [the capability's own weak_signal_term] (e.g. "compute capacity" contains
"compute", "transport network" contains "network"). F2b's overlap-exclusion
correctly refuses to let that internal substring corroborate itself, but
this means a genuinely organization-specific investment ("a major
investment in additional compute capacity") and an unrelated homonym
("collaboration across their transport network") are lexically
indistinguishable -- both are single catalogue keywords, at the same
word-distance from an equally generic qualifying signal.

F2c distinguishes them using closed-class English grammar: whether the
keyword's own occurrence is the object of a TARGET preposition ("in",
"for", "on"...) versus a SCOPE/coverage preposition ("across", "covering",
"spanning"...). This file does not modify any existing test file or the
golden fixtures; it only adds new, focused coverage.
"""

from __future__ import annotations

from commercial.evidence_mapping import (
    _keyword_is_object_of_target_preposition,
    map_source_to_capabilities,
)
from commercial.models import EvidenceCategory, EvidenceLevel
from commercial.opportunities import build_opportunity_hypotheses
from research.sources import AccessStatus, Source

ORG_NAME = "Verimark National Airports Authority"
COUNTRY = "Verimark"
TARGET_ORG = "Oman Air"
TARGET_COUNTRY = "Oman"


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
# Core regression: investment in compute capacity must promote.
# ---------------------------------------------------------------------------

def test_investment_in_compute_capacity_reaches_strong_inference():
    content = f"{ORG_NAME} today announced a major investment in additional compute capacity to support future growth."
    mappings, opps = _evaluate(content, org=ORG_NAME, country=COUNTRY)
    m = next(x for x in mappings if x.capability_id == "data_centre_infrastructure")
    assert m.evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert m.signal_type == "investment"
    assert m.matched_terms == ["compute capacity"]


def test_investment_in_compute_capacity_opportunity_unchanged_by_scoring():
    """Not a scoring change -- confirms the resulting opportunity score
    matches the existing WATCH-tier formula for a single STRONG_INFERENCE
    mapping with no bonus-eligible diversity.
    """
    content = f"{ORG_NAME} today announced a major investment in additional compute capacity to support future growth."
    _, opps = _evaluate(content, org=ORG_NAME, country=COUNTRY)
    assert opps and opps[0].capability_id == "data_centre_infrastructure"
    assert opps[0].score == 42.0


# ---------------------------------------------------------------------------
# G3 must remain exactly as before: scope-preposition homonyms across
# multiple generic signal types must not promote, and the keyword is
# retained at weak_relevance, not dropped.
# ---------------------------------------------------------------------------

_G3_MOU = (
    "Oman Air and a regional operator signed a Memorandum of Understanding to strengthen "
    "cooperation in transport and airport management, covering knowledge sharing, joint "
    "working groups and future collaboration across their transport network of operations."
)


def test_g3_mou_still_yields_zero_opportunities_and_weak_relevance_context():
    mappings, opps = _evaluate(_G3_MOU, org=TARGET_ORG, country=TARGET_COUNTRY)
    assert opps == []
    network = [m for m in mappings if m.capability_id == "network_connectivity"]
    assert network and all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in network)


def test_scope_preposition_homonyms_across_signal_types_do_not_promote():
    for sentence_template, source_type in [
        ("{org} confirmed a major investment across its transport network this year.", "news"),
        ("{org} was awarded a contract covering its transport network infrastructure.", "news"),
        ("{org} announced an expansion spanning its transport network to several new cities this year.", "news"),
        ("{org} announced a major project throughout its transport network across the region.", "news"),
    ]:
        content = sentence_template.format(org=TARGET_ORG)
        mappings, opps = _evaluate(content, org=TARGET_ORG, country=TARGET_COUNTRY, source_type=source_type)
        assert opps == [], f"{content!r} unexpectedly produced an opportunity: {[(o.capability_id, o.score) for o in opps]}"


# ---------------------------------------------------------------------------
# Target-preposition fallback -- unit level.
# ---------------------------------------------------------------------------

def test_target_prepositions_qualify_the_keyword_as_a_direct_object():
    for prefix in ["investment in ", "a contract for ", "funding toward ", "spending on "]:
        text = f"The organization announced {prefix}additional compute capacity this year."
        idx = text.index("additional compute capacity")
        assert _keyword_is_object_of_target_preposition(text, idx), prefix


def test_scope_prepositions_do_not_qualify():
    for prefix in ["collaboration across ", "an agreement covering ", "a partnership spanning ", "operations throughout "]:
        text = f"The organization announced {prefix}its transport network this year."
        idx = text.index("its transport network") + len("its ")
        assert not _keyword_is_object_of_target_preposition(text, idx), prefix


def test_unrecognized_preposition_fails_closed():
    """An ungoverned or unrecognized word must never be treated as an
    implicit target -- silence is not evidence.
    """
    text = "The organization discussed compute capacity during the meeting."
    idx = text.index("compute capacity")
    assert not _keyword_is_object_of_target_preposition(text, idx)


def test_modifiers_are_skipped_but_the_walk_is_bounded():
    text = "The organization announced investment in its own additional planned compute capacity."
    idx = text.index("compute capacity")
    # Four skippable modifiers ("its", "own", "additional", "planned") sit
    # between "in" and the keyword -- beyond the walk-back limit, so the
    # preposition is not found and the fallback correctly does not fire.
    assert not _keyword_is_object_of_target_preposition(text, idx)


# ---------------------------------------------------------------------------
# The fallback must never let a generic_triggers term qualify -- only
# genuine catalogue `keywords`.
# ---------------------------------------------------------------------------

def test_generic_trigger_does_not_qualify_through_the_fallback_even_with_a_target_preposition():
    """'new facility' is network_connectivity's own generic_triggers entry,
    not a keywords entry. Even preceded by a target preposition ("into a
    new facility"), it must not bypass Rule 4's independent-corroboration
    requirement -- this is the exact overreach identified and rejected
    during the forensic trace.
    """
    content = f"{ORG_NAME} announced an expansion into a new facility, with no other technology detail mentioned."
    mappings, opps = _evaluate(content, org=ORG_NAME, country=COUNTRY)
    network = [m for m in mappings if m.capability_id == "network_connectivity"]
    assert not any(m.evidence_level != EvidenceLevel.WEAK_RELEVANCE for m in network)
    assert not any(o.capability_id == "network_connectivity" for o in opps)


# ---------------------------------------------------------------------------
# Existing independent-context corroboration (F2b) is unaffected -- checked
# BEFORE the new fallback, and still succeeds on its own where it already did.
# ---------------------------------------------------------------------------

def test_independent_context_corroboration_still_works_without_needing_the_fallback():
    """The NVIDIA/Oakridge partnership control: 'AI' is genuine, separate
    corroboration for cloud_ai_infrastructure, found by F2b alone -- the
    target-preposition fallback is never reached (nor needed) here.
    """
    content = (
        "CES 2026: NVIDIA and Oakridge Industrial AG today expanded their strategic partnership "
        "to build the Industrial AI Operating System, accelerating industrial AI innovation together."
    )
    mappings, opps = _evaluate(content, org="Oakridge Industrial AG", country=None)
    assert _promoted(mappings, "cloud_ai_infrastructure")
    assert opps and opps[0].capability_id == "cloud_ai_infrastructure" and opps[0].score == 42.0


def test_canonical_expansion_with_independent_network_capacity_still_promotes():
    content = f"{ORG_NAME} is opening a new facility. This expansion will require additional network capacity."
    mappings, opps = _evaluate(content, org=ORG_NAME, country=COUNTRY)
    assert _promoted(mappings, "network_connectivity")
    assert opps and opps[0].score == 42.0


def test_own_site_requirement_with_fiber_and_bandwidth_still_promotes():
    content = (
        "Oakridge Industrial AG is building a new facility that must be close to gas, water and "
        "fiber connections to support its own operations, as part of a major expansion project "
        "that will also increase the facility's network bandwidth capacity."
    )
    mappings, opps = _evaluate(content, org="Oakridge Industrial AG", country=None)
    assert _promoted(mappings, "network_connectivity")
    assert opps and opps[0].score == 42.0


# ---------------------------------------------------------------------------
# Known, unsupported constructions -- must remain non-promoting, and this
# test simply documents that fact rather than special-casing it.
# ---------------------------------------------------------------------------

def test_gerund_mediated_investment_phrasing_is_a_known_recall_gap_not_promoted():
    """'investment on reducing cyber risk' -- a gerund ('reducing') sits
    between the preposition and the keyword. Not detected by this
    heuristic; documented as a known limitation, not specially handled.
    """
    content = f"{ORG_NAME} announced a major investment on reducing cyber risk across its operations."
    mappings, opps = _evaluate(content, org=ORG_NAME, country=COUNTRY)
    cyber = [m for m in mappings if m.capability_id == "cybersecurity_resilience"]
    assert not any(m.evidence_level != EvidenceLevel.WEAK_RELEVANCE for m in cyber)
    assert not any(o.capability_id == "cybersecurity_resilience" for o in opps)


def test_infinitive_mediated_partnership_phrasing_is_a_known_recall_gap_not_promoted():
    """'partnership to grow its cloud platform usage' -- an infinitive verb
    ('to grow') sits between the signal and the keyword, with no
    preposition governing the keyword directly. Not detected; documented,
    not specially handled. ('to deploy'/'to launch'/'to implement' are
    avoided here since those verbs independently trigger a genuine
    technology_initiative signal, which legitimately bypasses corroboration
    entirely -- that is correct existing behavior, not this fallback.)
    """
    content = f"{ORG_NAME} announced a new strategic partnership to grow its cloud platform usage across its facilities."
    mappings, opps = _evaluate(content, org=ORG_NAME, country=COUNTRY)
    cloud = [m for m in mappings if m.capability_id == "cloud_ai_infrastructure"]
    assert not any(m.evidence_level != EvidenceLevel.WEAK_RELEVANCE for m in cloud)
    assert not any(o.capability_id == "cloud_ai_infrastructure" for o in opps)


# ---------------------------------------------------------------------------
# Preservation -- Fix B untouched.
# ---------------------------------------------------------------------------

def test_fix_b_single_token_organization_association_unaffected():
    from commercial.evidence_mapping import _mentions_organization, _org_core_tokens

    tokens = _org_core_tokens(TARGET_ORG, country=TARGET_COUNTRY)
    assert tokens == ["air"]
    assert not _mentions_organization("The chairman announced a new partnership.", TARGET_ORG, tokens)
    assert not _mentions_organization("Ensuring fairness in vendor selection is difficult.", TARGET_ORG, tokens)
    assert _mentions_organization(f"{TARGET_ORG} announced a new partnership.", TARGET_ORG, tokens)

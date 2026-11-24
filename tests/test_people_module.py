"""Phase A tests for commercial.people -- Person model, classification,
chips, scoring, deduplication. Purely deterministic, no network calls, no
AI call. Uses a generic placeholder organization -- no real
organization/sector/country is referenced, matching the existing
LinkedIn-hardening test file's convention.
"""

from __future__ import annotations

from commercial.people import (
    Person,
    annotate_identity,
    _name_from_title,
    assess_organization_association,
    build_people,
    classify_person_text,
    deduplicate_profile_sources,
    derive_influence_type,
    generate_chips,
    score_person,
    summarize_by_function,
)
from commercial.people_taxonomy import get_taxonomy
from research.sources import AccessStatus, Source

ORG_NAME = "Meridian Ports Authority"


def _source(*, url, title, snippet="", source_type="linkedin_profile", discovery_dimension=None, quality=50.0):
    s = Source(
        url=url,
        domain="linkedin.com",
        title=title,
        source_type=source_type,
        snippet=snippet,
        discovered_via_query="q",
        discovery_provider="test",
        family="linkedin_people",
        access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
        source_quality=quality,
    )
    if discovery_dimension:
        s.extra["discovery_dimension"] = discovery_dimension
    return s


# ---------------------------------------------------------------------------
# 1. Person model creation
# ---------------------------------------------------------------------------

def test_person_model_creation_has_expected_fields():
    p = Person(
        person_id="jane-doe-123",
        name="Jane Doe",
        current_title="Chief Information Officer at Meridian Ports Authority",
        organization_name=ORG_NAME,
        organization_association="confirmed",
        linkedin_url="https://www.linkedin.com/in/jane-doe-123",
    )
    assert p.person_id == "jane-doe-123"
    assert p.chips == []
    assert p.functions == []
    d = p.to_dict()
    assert d["person_id"] == "jane-doe-123"
    assert "score_factors" in d
    assert "organization_association_evidence" in d


# ---------------------------------------------------------------------------
# 2. LinkedIn candidate deduplication (by stable /in/ slug, across
# subdomains and tracking params -- item beyond plain URL-string dedup)
# ---------------------------------------------------------------------------

def test_deduplicate_profile_sources_merges_same_slug_across_subdomains_and_params():
    sources = [
        _source(url="https://www.linkedin.com/in/jane-doe-123", title="Jane Doe - CIO - Meridian Ports Authority | LinkedIn"),
        _source(url="https://om.linkedin.com/in/jane-doe-123?trk=public_profile", title="Jane Doe - CIO - Meridian Ports Authority | LinkedIn"),
        _source(url="https://www.linkedin.com/in/john-smith-99", title="John Smith - Procurement Manager - Meridian Ports Authority | LinkedIn"),
    ]
    groups = deduplicate_profile_sources(sources)
    assert len(groups) == 2
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2]


def test_deduplicate_profile_sources_keeps_unrecognizable_urls_as_singletons():
    sources = [_source(url="https://www.linkedin.com/company/meridian-ports-authority", title="Meridian Ports Authority | LinkedIn", source_type="linkedin_company")]
    groups = deduplicate_profile_sources(sources)
    assert len(groups) == 1
    assert len(groups[0]) == 1


# ---------------------------------------------------------------------------
# 3. Organization association
# ---------------------------------------------------------------------------

def test_organization_association_confirmed_when_name_literally_present():
    level, evidence = assess_organization_association(
        "Jane Doe - CIO - Meridian Ports Authority", ORG_NAME
    )
    assert level == "confirmed"
    assert evidence


def test_organization_association_needs_verification_when_only_part_of_the_name_is_present():
    # A partial / brand-only employer cannot identify the operating entity:
    # it is "needs_verification", never confirmed and never in the main list.
    level, evidence = assess_organization_association("Jane Doe - CIO - Meridian Ports", ORG_NAME)
    assert level == "needs_verification"
    assert evidence


def test_organization_association_unconfirmed_when_unrelated():
    level, evidence = assess_organization_association("Jane Doe - CIO - Some Other Company", ORG_NAME)
    assert level == "unconfirmed"
    assert evidence == []


def test_build_people_excludes_unconfirmed_association_candidates():
    """Guardrail #3: candidates with no evidence of organization
    association are not presented as stakeholders.
    """
    sources = [
        _source(url="https://www.linkedin.com/in/unrelated-person", title="Someone - CIO - A Totally Different Company | LinkedIn", snippet="Chief Information Officer"),
    ]
    people = build_people(sources, organization_name=ORG_NAME)
    assert people == []


# ---------------------------------------------------------------------------
# 4. Role classification
# ---------------------------------------------------------------------------

def test_role_classification_matches_specific_catalogue_role():
    result = classify_person_text("Jane Doe - Chief Information Officer - Meridian Ports Authority")
    assert result["role_id"] == "cio"
    assert result["role_label"] == "CIO"
    assert result["seniority"] == "c_level"


def test_role_classification_none_when_no_catalogue_role_present():
    result = classify_person_text("Jane Doe - Marketing Coordinator - Meridian Ports Authority")
    assert result["role_id"] is None
    # Fallback seniority is also None here -- "Coordinator" matches no
    # generic seniority marker either.
    assert result["seniority"] is None


# ---------------------------------------------------------------------------
# 5. Function classification
# ---------------------------------------------------------------------------

def test_function_classification_matches_cybersecurity_and_it():
    result = classify_person_text(
        "John Smith - Head of Cybersecurity - Meridian Ports Authority", 
    )
    assert "cybersecurity" in result["functions"]


def test_function_classification_empty_when_no_function_terms_present():
    result = classify_person_text("Jane Doe - Front Desk Coordinator - Meridian Ports Authority")
    assert result["functions"] == []


# ---------------------------------------------------------------------------
# 6. Chip generation -- evidence-backed only, never from discovery_dimension
# ---------------------------------------------------------------------------

def test_chips_are_generated_only_from_actual_matches():
    chips = generate_chips(
        role_label="CIO",
        functions=["it", "digital_transformation"],
        technology_areas=["cloud"],
        influence_type="decision_maker",
        organization_association="confirmed",
        source_types=["linkedin_profile"],
    )
    labels = {c["label"] for c in chips}
    assert "CIO" in labels
    assert "Cloud" in labels
    assert "Decision Maker" in labels
    assert "LinkedIn" in labels
    assert "Needs Verification" not in labels  # confirmed association


def test_no_chips_manufactured_from_discovery_dimension_alone():
    """Core guardrail #2/#4: a person surfaced by the
    technology_digital_infrastructure dimension, whose actual title/snippet
    supports NOTHING, must receive no role/function/technology chips.
    """
    sources = [
        _source(
            url="https://www.linkedin.com/in/some-person",
            title="Alex Roe - Executive Assistant - Meridian Ports Authority | LinkedIn",
            snippet="Executive Assistant",
            discovery_dimension="technology_digital_infrastructure",
        )
    ]
    people = build_people(sources, organization_name=ORG_NAME)
    assert len(people) == 1
    p = people[0]
    chip_categories = {c["category"] for c in p.chips}
    assert "role" not in chip_categories
    assert "function" not in chip_categories
    assert "technology" not in chip_categories


def test_needs_verification_chip_present_for_weak_association():
    chips = generate_chips(
        role_label=None,
        functions=["cybersecurity"],
        technology_areas=[],
        influence_type=None,
        organization_association="needs_verification",
        source_types=["linkedin_profile"],
    )
    assert any(c["label"] == "Needs Verification" for c in chips)


# ---------------------------------------------------------------------------
# 7. Relevance scoring -- deterministic, explainable factors
# ---------------------------------------------------------------------------

def test_relevance_scoring_returns_explainable_factors():
    score, factors = score_person(
        seniority="c_level",
        functions=["it", "cybersecurity"],
        technology_areas=["cloud"],
        organization_association="confirmed",
        source_url_count=2,
        discovery_dimensions=["technology_digital_infrastructure"],
    )
    assert score > 0
    assert set(factors.keys()) == {
        "seniority",
        "function_relevance",
        "technology_relevance",
        "organization_association",
        "multiple_evidence_sources",
        "discovery_dimension_corroboration",
    }  # the sales-priority tier is deliberately NOT a relevance_score factor
    assert sum(factors.values()) == score


def test_relevance_scoring_is_higher_for_stronger_evidence():
    weak_score, _ = score_person(
        seniority=None, functions=[], technology_areas=[], organization_association="weak",
        source_url_count=1, discovery_dimensions=[],
    )
    strong_score, _ = score_person(
        seniority="c_level", functions=["it"], technology_areas=["cloud"], organization_association="confirmed",
        source_url_count=2, discovery_dimensions=["technology_digital_infrastructure"],
    )
    assert strong_score > weak_score


# ---------------------------------------------------------------------------
# 8. People Intelligence v2: NO maximum -- every qualified person is kept
#    (the former 30-person display cap was removed).
# ---------------------------------------------------------------------------

def test_build_people_keeps_every_credible_candidate_no_thirty_cap():
    sources = [
        _source(
            url=f"https://www.linkedin.com/in/person-{i}",
            title=f"Person {i} - IT Manager - {ORG_NAME} | LinkedIn",
            snippet="IT Manager",
        )
        for i in range(50)
    ]
    people = build_people(sources, organization_name=ORG_NAME)
    assert len(people) == 50


# ---------------------------------------------------------------------------
# 9. Fewer-than-20 credible people is allowed (no padding)
# ---------------------------------------------------------------------------

def test_build_people_does_not_pad_below_natural_count():
    sources = [
        _source(
            url=f"https://www.linkedin.com/in/person-{i}",
            title=f"Person {i} - IT Manager - {ORG_NAME} | LinkedIn",
            snippet="IT Manager",
        )
        for i in range(5)
    ]
    people = build_people(sources, organization_name=ORG_NAME)
    assert len(people) == 5


# ---------------------------------------------------------------------------
# 10. No invented person data
# ---------------------------------------------------------------------------

def test_name_extraction_never_fabricates_beyond_the_title_text():
    sources = [
        _source(url="https://www.linkedin.com/in/jane-doe-123", title="Jane Doe - CIO - Meridian Ports Authority | LinkedIn", snippet="Chief Information Officer")
    ]
    people = build_people(sources, organization_name=ORG_NAME)
    assert people[0].name == "Jane Doe"
    assert people[0].current_title == "Jane Doe - CIO - Meridian Ports Authority | LinkedIn"


def test_no_location_fabricated_when_not_explicitly_available():
    sources = [
        _source(url="https://www.linkedin.com/in/jane-doe-123", title="Jane Doe - CIO - Meridian Ports Authority | LinkedIn", snippet="Chief Information Officer")
    ]
    people = build_people(sources, organization_name=ORG_NAME)
    assert people[0].location is None


# ---------------------------------------------------------------------------
# 11. Evidence is retained
# ---------------------------------------------------------------------------

def test_evidence_and_provenance_are_retained_on_the_person():
    sources = [
        _source(
            url="https://www.linkedin.com/in/jane-doe-123",
            title="Jane Doe - Chief Information Officer - Meridian Ports Authority | LinkedIn",
            snippet="Chief Information Officer, cloud and digital transformation",
            discovery_dimension="technology_digital_infrastructure",
        )
    ]
    people = build_people(sources, organization_name=ORG_NAME)
    p = people[0]
    assert p.evidence
    assert p.classification_text
    assert "chief information officer" in p.classification_matched_terms or "cio" in p.classification_matched_terms
    assert p.organization_association_evidence
    assert p.discovery_dimensions == ["technology_digital_infrastructure"]
    assert p.score_factors


# ---------------------------------------------------------------------------
# 12. Person detail view data -- derived summary counts, influence
# hierarchy correctness
# ---------------------------------------------------------------------------

def test_summarize_by_function_reflects_actual_classified_people():
    sources = [
        _source(url="https://www.linkedin.com/in/p1", title=f"A - Chief Information Security Officer - {ORG_NAME} | LinkedIn", snippet="CISO"),
        _source(url="https://www.linkedin.com/in/p2", title=f"B - Head of Cybersecurity - {ORG_NAME} | LinkedIn", snippet="Cybersecurity"),
        _source(url="https://www.linkedin.com/in/p3", title=f"C - Procurement Director - {ORG_NAME} | LinkedIn", snippet="Procurement"),
    ]
    people = build_people(sources, organization_name=ORG_NAME)
    counts = summarize_by_function(people)
    assert counts.get("cybersecurity") == 2
    assert counts.get("procurement") == 1


def test_influence_hierarchy_does_not_overclaim_decision_maker():
    """A senior title with no relevant function/technology evidence gets
    the qualified 'Potential Decision Maker', not the strong 'Decision
    Maker' claim.
    """
    influence = derive_influence_type(
        seniority="c_level", functions=[], technology_areas=[], organization_association="confirmed"
    )
    assert influence == "potential_decision_maker"


def test_influence_hierarchy_decision_maker_requires_seniority_and_relevant_function():
    influence = derive_influence_type(
        seniority="c_level", functions=["cybersecurity"], technology_areas=[], organization_association="confirmed"
    )
    assert influence == "decision_maker"


def test_influence_none_when_association_unconfirmed():
    influence = derive_influence_type(
        seniority="c_level", functions=["cybersecurity"], technology_areas=[], organization_association="unconfirmed"
    )
    assert influence is None


# ---------------------------------------------------------------------------
# Taxonomy sanity (schema loads and validates)
# ---------------------------------------------------------------------------

def test_taxonomy_loads_and_has_expected_top_level_categories():
    taxonomy = get_taxonomy()
    assert {"roles", "functions", "technology_areas"} <= set(taxonomy.keys())
    assert set(taxonomy.keys()) <= {"roles", "functions", "technology_areas", "people_discovery"}
    assert len(taxonomy["roles"]) > 0
    assert len(taxonomy["functions"]) > 0
    assert len(taxonomy["technology_areas"]) > 0
def test_name_from_title_rejects_role_description():
    result = _name_from_title("Head of IT at Acme Telecom Oman")
    assert result == ""


def test_name_from_title_preserves_real_person_name():
    result = _name_from_title("John Smith - CTO - Ooredoo Oman")
    assert result == "John Smith"
def test_build_people_rejects_role_description_as_candidate_name():
    sources = [
        _source(
            url="https://www.linkedin.com/in/role-only-profile",
            title="Head of IT at Acme Telecom Oman",
            snippet="Chief Information Officer",
        )
    ]

    people = build_people(sources, organization_name=ORG_NAME)

    assert people == []



def _identity_person(person_id, name, title, url, location=None, organization=ORG_NAME):
    return Person(
        person_id=person_id,
        name=name,
        current_title=title,
        organization_name=organization,
        organization_association="confirmed",
        linkedin_url=url,
        location=location,
    )


def test_identity_annotation_flags_related_slug_alternate_profile_without_merging():
    a = _identity_person("jane-doe", "Jane Doe", "Jane Doe - Head of IT - Meridian Ports Authority",
                         "https://www.linkedin.com/in/jane-doe")
    b = _identity_person("jane-doe-4a1b2c3", "Jane Doe", "Jane Doe - Head of IT - Meridian Ports Authority",
                         "https://www.linkedin.com/in/jane-doe-4a1b2c3")
    annotate_identity([a, b])
    assert a.identity_flags == ["likely_same_profile_alternate_url"]
    assert a.possible_duplicate_of == ["jane-doe-4a1b2c3"]
    assert b.possible_duplicate_of == ["jane-doe"]


def test_identity_annotation_flags_same_name_collision_as_weaker_verification():
    a = _identity_person("jane-doe-1", "Jane Doe", "Jane Doe - Head of IT - Meridian Ports Authority",
                         "https://www.linkedin.com/in/jane-doe-1")
    b = _identity_person("jane-doe-2", "Jane Doe", "Jane Doe - Head of IT - Meridian Ports Authority",
                         "https://www.linkedin.com/in/jane-doe-2")
    annotate_identity([a, b])
    assert "possible_identity_collision" in a.identity_flags
    assert a.possible_duplicate_of == ["jane-doe-2"]


def test_identity_annotation_normalises_honorific_credentials_and_al_joining():
    a = _identity_person("sara-1", "Dr. Sara Khan", "Dr. Sara Khan - CIO",
                         "https://www.linkedin.com/in/sara-1")
    b = _identity_person("sara-2", "Sara Khan, PMP", "Sara Khan, PMP - CIO",
                         "https://www.linkedin.com/in/sara-2")
    annotate_identity([a, b])
    assert "possible_identity_collision" in a.identity_flags


def test_identity_annotation_does_not_match_synthetic_numbered_names():
    a = _identity_person("person-1", "Person 1", "Person 1 - Manager",
                         "https://www.linkedin.com/in/person-1")
    b = _identity_person("person-2", "Person 2", "Person 2 - Manager",
                         "https://www.linkedin.com/in/person-2")
    annotate_identity([a, b])
    assert not a.identity_flags
    assert not b.identity_flags


def test_identity_fields_are_serialized_with_empty_defaults():
    p = _identity_person("john-smith", "John Smith", "John Smith - Manager",
                         "https://www.linkedin.com/in/john-smith")
    data = p.to_dict()
    assert data["identity_flags"] == []
    assert data["possible_duplicate_of"] == []
    assert data["identity_notes"] == []


def test_identity_annotation_does_not_match_same_name_when_org_and_title_differ():
    a = _identity_person("alex-1", "Alex Morgan", "Alex Morgan - Head of IT - Meridian Ports",
                         "https://www.linkedin.com/in/alex-morgan-1", organization="Meridian Ports")
    b = _identity_person("alex-2", "Alex Morgan", "Alex Morgan - Finance Director - Gulf Logistics",
                         "https://www.linkedin.com/in/alex-morgan-2", organization="Gulf Logistics")
    annotate_identity([a, b])
    assert not a.identity_flags
    assert not b.identity_flags


def test_identity_annotation_does_not_treat_missing_organisations_as_equal():
    a = _identity_person("sam-1", "Sam Taylor", "Sam Taylor - Head of IT",
                         "https://www.linkedin.com/in/sam-taylor-1", organization="")
    b = _identity_person("sam-2", "Sam Taylor", "Sam Taylor - Finance Director",
                         "https://www.linkedin.com/in/sam-taylor-2", organization="")
    annotate_identity([a, b])
    assert not a.identity_flags
    assert not b.identity_flags


def test_identity_annotation_normalises_diacritics_and_phd_credentials():
    a = _identity_person("jose-1", "José García, Ph.D.", "José García - CIO",
                         "https://www.linkedin.com/in/jose-garcia-1")
    b = _identity_person("jose-2", "Jose Garcia", "Jose Garcia - CIO",
                         "https://www.linkedin.com/in/jose-garcia-2")
    annotate_identity([a, b])
    assert "possible_identity_collision" in a.identity_flags


def test_identity_annotation_does_not_flag_same_name_same_target_org_with_different_titles():
    # organization_name is the target account label assigned to every Person
    # by assess_people(); it is not independent evidence of shared identity.
    a = _identity_person("alex-1", "Alex Morgan", "Alex Morgan - Head of IT",
                         "https://www.linkedin.com/in/alex-morgan-1")
    b = _identity_person("alex-2", "Alex Morgan", "Alex Morgan - Finance Director",
                         "https://www.linkedin.com/in/alex-morgan-2")
    annotate_identity([a, b])
    assert not a.identity_flags
    assert not b.identity_flags
    assert not a.possible_duplicate_of
    assert not b.possible_duplicate_of

"""People Identity & Association Hardening -- TESTS ONLY (written before the fix).

Origin: a live run for "Ooredoo Oman" listed stakeholders who do not currently
work for that entity -- e.g. Atif Bukhari (displayed location Dubai, UAE) and
Ahmad Al-Neama (associated with Ooredoo Group). An offline audit traced this to:

  R1  association is decided on ONE concatenated blob (title + snippet +
      supplemental snippets), so an organization mention anywhere counts;
  R2  "weak" matches (>= half of the org's name tokens present, which a
      country word in a *location* satisfies) are kept in the list;
  R3  Brave `extra_snippets` are merged into the snippet and treated as
      profile evidence;
  R4  historical roles (dated/ended ranges, "worked at", "until") are not
      reliably detected -- only a short marker list is masked;
  R5  nothing distinguishes the target entity (Ooredoo Oman) from the group
      brand (Ooredoo Group) or sister operating companies (Ooredoo Kuwait ...);
  R6  title/seniority/functions are classified from page-wide text, so another
      person's title ("People also viewed ...") can be attributed to the
      candidate.

These tests encode the REQUIRED behaviour. Against the current implementation
the ones tagged [EXPECTED-FAIL] fail; the ones tagged [CONTROL] pass today and
must keep passing after the fix. They are fully offline: in-memory Source
objects / fake providers only, no network, no API keys.

NOTE ON FIXTURES: the live snippets were not available to this audit, so the
fixtures below are structural reconstructions of the public LinkedIn result
layout ("Name - Headline - Company | LinkedIn" + "Location · Experience:"
fields) that reproduce each failure mechanism. Real names are used only for
the two reported cases; no production code references any organization.
"""

from __future__ import annotations

import pytest

from commercial.people import (
    assess_organization_association,
    build_people,
    classify_person_text,
)
from research.providers.base import RawResult
from research.providers.brave import BraveSearchProvider
from research.sources import AccessStatus, Source

ORG = "Ooredoo Oman"


def _profile(slug, title, snippet="", *, extras=None, provider="brave"):
    s = Source(
        url=f"https://www.linkedin.com/in/{slug}",
        domain="linkedin.com",
        title=title,
        source_type="linkedin_profile",
        snippet=snippet,
        discovered_via_query="q",
        discovery_provider=provider,
        family="linkedin_people",
        access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
        source_quality=50.0,
    )
    s.extra.update({"linkedin_kind": "in", "supplemental_snippets": list(extras or [])})
    return s


def _names(sources, org=ORG):
    return [p.name for p in build_people(sources, organization_name=org)]


# ---------------------------------------------------------------------------
# 1. Reported case: Atif Bukhari -- location Dubai, UAE; organization only in
#    page-wide / extra text. [R1, R3, R5]
# ---------------------------------------------------------------------------

def test_atif_bukhari_dubai_located_profile_with_org_only_in_extra_snippets_is_not_listed():  # [EXPECTED-FAIL]
    src = _profile(
        "atif-bukhari",
        "Atif Bukhari - Head of IT Security | LinkedIn",
        "Dubai, United Arab Emirates. Head of IT Security. 500+ connections on LinkedIn.",
        extras=["Ooredoo Oman announced a managed security partnership. Cybersecurity leadership across Oman."],
    )
    assert _names([src]) == []


def test_atif_bukhari_org_in_extra_text_must_not_yield_confirmed_association():  # [EXPECTED-FAIL]
    text = ("Atif Bukhari - Head of IT Security | LinkedIn Dubai, United Arab Emirates. "
            "Ooredoo Oman announced a managed security partnership.")
    level, _ = assess_organization_association(text, ORG)
    assert level != "confirmed"


def test_atif_bukhari_displayed_location_conflicting_with_org_country_blocks_weak_association():  # [EXPECTED-FAIL]
    # Headline names only the brand; "Oman" appears in a sentence about the
    # company, not as the person's location. Dubai is the person's location.
    src = _profile(
        "atif-bukhari-2",
        "Atif Bukhari - Senior Manager Information Security - Ooredoo | LinkedIn",
        "Dubai, United Arab Emirates. Telecom operator active in Oman and Qatar.",
    )
    assert _names([src]) == []


# ---------------------------------------------------------------------------
# 2. Reported case: Ahmad Al-Neama -- employer is Ooredoo Group. [R2, R5]
# ---------------------------------------------------------------------------

def test_ahmad_al_neama_group_employer_with_oman_in_location_is_not_listed_for_ooredoo_oman():  # [EXPECTED-FAIL]
    # "oman" (from the LOCATION) + "ooredoo" (from the GROUP employer) satisfies
    # the 2-of-2 token rule -> "weak" -> kept today.
    src = _profile(
        "ahmad-al-neama",
        "Ahmad Al-Neama - Director IT Infrastructure - Ooredoo Group | LinkedIn",
        "Muscat, Oman. Director IT Infrastructure at Ooredoo Group.",
    )
    assert _names([src]) == []


def test_ahmad_al_neama_group_employer_association_level_is_not_confirmed():  # [EXPECTED-FAIL]
    level, _ = assess_organization_association(
        "Ahmad Al-Neama - Director IT Infrastructure - Ooredoo Group | LinkedIn Muscat, Oman", ORG)
    assert level not in ("confirmed", "weak")


def test_group_employer_is_a_valid_match_when_the_target_organization_is_the_group():  # [CONTROL]
    src = _profile(
        "ahmad-al-neama-grp",
        "Ahmad Al-Neama - Director IT Infrastructure - Ooredoo Group | LinkedIn",
        "Doha, Qatar. Director IT Infrastructure at Ooredoo Group.",
    )
    assert _names([src], org="Ooredoo Group") == ["Ahmad Al-Neama"]


# ---------------------------------------------------------------------------
# 3. Other operating companies of the same brand. [R5]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sister", ["Ooredoo Kuwait", "Ooredoo Qatar", "Ooredoo Tunisia", "Ooredoo Maldives",
                                    "Ooredoo Group"])
def test_sister_operating_company_with_oman_mentioned_elsewhere_is_not_listed(sister):  # [EXPECTED-FAIL]
    src = _profile(
        "sister-person",
        f"Sam Example - Chief Information Officer - {sister} | LinkedIn",
        f"Chief Information Officer at {sister}. Regional coverage includes Oman.",
    )
    assert _names([src]) == []


def test_target_entity_in_headline_company_segment_is_confirmed_and_listed():  # [CONTROL]
    src = _profile(
        "jane-roe",
        "Jane Roe - Chief Information Officer - Ooredoo Oman | LinkedIn",
        "Muscat, Oman. Chief Information Officer at Ooredoo Oman.",
    )
    people = build_people([src], organization_name=ORG)
    assert [p.name for p in people] == ["Jane Roe"]
    assert people[0].organization_association == "confirmed"


# ---------------------------------------------------------------------------
# 4. Brand-only employer names. [R5]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("headline", [
    "Lee Example - IT Director - Ooredoo | LinkedIn",
    "Lee Example - IT Director at Ooredoo | LinkedIn",
])
def test_brand_only_employer_is_not_treated_as_the_target_entity(headline):  # [EXPECTED-FAIL]
    # Location Muscat, Oman supplies the second token. A bare brand cannot
    # identify WHICH operating company employs the person.
    src = _profile("lee-example", headline, "Muscat, Oman. IT Director.")
    assert _names([src]) == []


def test_brand_only_employer_is_never_confirmed():  # [CONTROL]
    level, _ = assess_organization_association("Lee Example - IT Director - Ooredoo | LinkedIn", ORG)
    assert level != "confirmed"


# ---------------------------------------------------------------------------
# 5. Former / historical employment. [R4]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snippet", [
    "Experience: Ooredoo Oman · Jan 2014 - Mar 2019",                 # ended date range
    "Experience: Ooredoo Oman · 2012 - 2018",                         # ended year range
    "Worked at Ooredoo Oman from 2015 to 2020. Now at Another Telco.",
    "Spent eight years at Ooredoo Oman until 2021.",
    "Left Ooredoo Oman in 2022 to join Another Telco.",
    "Past roles: IT Manager at Ooredoo Oman.",
    "Was IT Director at Ooredoo Oman.",
])
def test_historical_role_at_the_target_org_is_not_current_employment(snippet):  # [EXPECTED-FAIL]
    level, _ = assess_organization_association(f"Pat Example - IT Consultant | LinkedIn {snippet}", ORG)
    assert level == "unconfirmed"


@pytest.mark.parametrize("snippet", [
    "Experience: Ooredoo Oman · Jan 2019 - Present",
    "Experience: Ooredoo Oman · 2019 - present",
    "Experience: Ooredoo Oman · Jan 2019 - Present · 5 yrs",
])
def test_open_ended_role_at_the_target_org_is_current_employment(snippet):  # [CONTROL]
    level, _ = assess_organization_association(f"Pat Example - IT Manager | LinkedIn {snippet}", ORG)
    assert level == "confirmed"


def test_former_role_does_not_leak_seniority_into_current_classification():  # [EXPECTED-FAIL]
    src = _profile(
        "former-cio",
        "Pat Example - IT Manager - Ooredoo Oman | LinkedIn",
        "Muscat, Oman. IT Manager at Ooredoo Oman. Past: Chief Information Officer at Another Telco 2010 - 2016.",
    )
    people = build_people([src], organization_name=ORG)
    assert len(people) == 1
    assert people[0].seniority != "c_level"
    assert people[0].role_id != "cio"


# ---------------------------------------------------------------------------
# 6. Page-wide snippets / Brave extra snippets. [R1, R3]
# ---------------------------------------------------------------------------

def test_supplemental_snippet_cannot_establish_current_employment():  # [EXPECTED-FAIL]
    src = _profile(
        "extras-only",
        "Robin Example - Network Engineer | LinkedIn",
        "Network Engineer. Telecom and cloud.",
        extras=["Experience: Ooredoo Oman · 2020 - Present"],
    )
    assert _names([src]) == []


def test_role_term_only_in_supplemental_snippet_does_not_set_seniority_or_role():  # [EXPECTED-FAIL]
    src = _profile(
        "extras-role",
        "Robin Example - Network Engineer - Ooredoo Oman | LinkedIn",
        "Muscat, Oman. Network Engineer at Ooredoo Oman.",
        extras=["Our Chief Information Officer spoke at the conference about cloud strategy."],
    )
    people = build_people([src], organization_name=ORG)
    assert len(people) == 1
    assert people[0].role_id != "cio"
    assert people[0].seniority != "c_level"


def test_supplemental_snippets_remain_available_as_context_on_a_valid_person():  # [CONTROL]
    ctx = "Speaker at a regional cloud security summit."
    src = _profile(
        "ctx-kept",
        "Robin Example - Network Engineer - Ooredoo Oman | LinkedIn",
        "Muscat, Oman. Network Engineer at Ooredoo Oman.",
        extras=[ctx],
    )
    people = build_people([src], organization_name=ORG)
    assert len(people) == 1 and ctx in people[0].classification_text


def test_brave_parse_keeps_extra_snippets_separate_from_the_profile_description():  # [EXPECTED-FAIL]
    """Data-model contract: Brave's description and extra_snippets are merged
    into one `snippet` string today, so nothing downstream can tell them
    apart. The provider must expose them separately (proposed attribute name:
    RawResult.extra_snippets). Description-only text must stay in `snippet`."""
    body = {"web": {"results": [{
        "url": "https://www.linkedin.com/in/split-me",
        "title": "Split Me - Analyst | LinkedIn",
        "description": "Analyst. Dubai, United Arab Emirates.",
        "extra_snippets": ["Ooredoo Oman reported record 5G subscriber growth."],
    }]}}
    raw = BraveSearchProvider._parse(body, "q")[0]
    assert "Ooredoo Oman" not in raw.snippet
    assert getattr(raw, "extra_snippets", None) == ["Ooredoo Oman reported record 5G subscriber growth."]


def test_raw_result_without_extra_snippets_still_constructs_for_other_providers():  # [CONTROL]
    r = RawResult(url="https://x.test", title="t", snippet="s", query="q", provider="serper", rank=0)
    assert r.snippet == "s"


# ---------------------------------------------------------------------------
# 7. Titles belonging to someone else (related-profile / page-wide text). [R6]
# ---------------------------------------------------------------------------

def test_other_persons_title_in_page_text_is_not_attributed_to_the_candidate():  # [EXPECTED-FAIL]
    src = _profile(
        "candidate-a",
        "Candidate Alpha - Systems Engineer - Ooredoo Oman | LinkedIn",
        "Muscat, Oman. Systems Engineer at Ooredoo Oman. "
        "People also viewed: Jane Roe - Chief Information Officer - Ooredoo Oman.",
    )
    people = build_people([src], organization_name=ORG)
    assert len(people) == 1
    assert people[0].seniority != "c_level"
    assert people[0].role_id != "cio"


def test_candidate_with_no_own_org_evidence_is_not_listed_because_another_person_has_it():  # [EXPECTED-FAIL]
    src = _profile(
        "candidate-b",
        "Candidate Beta - Engineer | LinkedIn",
        "Engineer. People also viewed: Jane Roe - Chief Information Officer at Ooredoo Oman.",
    )
    assert _names([src]) == []


def test_name_mismatch_between_profile_title_and_snippet_subject_is_not_confirmed():  # [EXPECTED-FAIL]
    src = _profile(
        "candidate-c",
        "Candidate Gamma - Analyst | LinkedIn",
        "Ahmad Al-Neama is the Chief Information Officer at Ooredoo Oman.",
    )
    assert _names([src]) == []


# ---------------------------------------------------------------------------
# 8. Weak (partial-name) associations must not enter the list. [R2]
# ---------------------------------------------------------------------------

def test_weak_association_is_not_included_in_the_people_list():  # [EXPECTED-FAIL]
    src = _profile(
        "weak-one",
        "Weak One - Cloud Architect | LinkedIn",
        "Cloud Architect. Muscat, Oman. Worked on Ooredoo projects.",
    )
    level, _ = assess_organization_association(f"{src.title} {src.snippet}", ORG)
    assert level != "confirmed"            # (was "weak" before the hardening)
    assert _names([src]) == []             # required behaviour


def test_partial_name_match_for_a_generic_multi_word_org_is_not_listed():  # [EXPECTED-FAIL]
    src = _profile(
        "weak-two",
        "Weak Two - IT Manager | LinkedIn",
        "IT Manager. Meridian Ports consultancy.",
    )
    assert _names([src], org="Meridian Ports Authority") == []


def test_country_word_in_location_alone_never_counts_as_an_org_token():  # [EXPECTED-FAIL]
    src = _profile(
        "country-only",
        "Country Only - IT Director - Unrelated Telecom | LinkedIn",
        "Muscat, Oman. IT Director. Ooredoo is a competitor.",
    )
    assert _names([src]) == []


# ---------------------------------------------------------------------------
# 9. Controls: legitimate current employees must still be listed.
# ---------------------------------------------------------------------------

def test_experience_field_open_ended_at_target_org_is_listed():  # [CONTROL]
    src = _profile(
        "ctrl-exp",
        "Casey Example - Cybersecurity Manager | LinkedIn",
        "Muscat, Oman. Experience: Ooredoo Oman · Mar 2020 - Present · Location: Muscat",
    )
    assert _names([src]) == ["Casey Example"]


def test_legitimate_profile_keeps_role_and_function_classification():  # [CONTROL]
    src = _profile(
        "ctrl-cls",
        "Dana Example - Head of Cybersecurity - Ooredoo Oman | LinkedIn",
        "Muscat, Oman. Head of Cybersecurity at Ooredoo Oman.",
    )
    people = build_people([src], organization_name=ORG)
    assert len(people) == 1
    c = classify_person_text(f"{src.title} {src.snippet}")
    assert people[0].role_id == c["role_id"] and "cybersecurity" in people[0].functions

"""People classification must come from the candidate's OWN current evidence.

A candidate who is already confirmed must not have their title, seniority,
function or technology areas shaped by unrelated third-person text, another
person's role, a role held at some other employer, or pronoun-led text that
refers to someone else. Supplemental / Brave extra snippets stay out of both
association and classification.

Offline only: in-memory Source objects; no network, no API keys.
"""

from __future__ import annotations

import pytest

from commercial.people import assess_people, build_people
from commercial.people_association import assess_results
from research.sources import AccessStatus, Source

ORG = "Oman Airports Management Company"
COUNTRY = "Oman"

# A confirmed candidate whose own current role carries only a network signal.
TITLE = f"Jane Doe - Network Engineer - {ORG} | LinkedIn"
CLEAN = "Muscat, Oman."


def _source(title, snippet, *, supplemental=None, extra_snippets=None, slug="jane-doe"):
    s = Source(
        url=f"https://om.linkedin.com/in/{slug}", domain="linkedin.com", title=title,
        source_type="linkedin_profile", snippet=snippet, discovered_via_query="q", discovery_provider="brave",
        family="linkedin_people", access_status=AccessStatus.INDEXED_SNIPPET_ONLY, source_quality=50.0,
    )
    s.extra.update({"linkedin_kind": "in", "supplemental_snippets": list(supplemental or []),
                    "extra_snippets": list(extra_snippets or [])})
    return s


def _person(title, snippet, **kw):
    people = build_people([_source(title, snippet, **kw)], organization_name=ORG, organization_country=COUNTRY)
    assert len(people) == 1, "candidate must be confirmed"
    return people[0]


def _signature(p):
    return (p.seniority, tuple(p.functions), tuple(p.technology_areas), p.role_id, tuple(p.classification_matched_terms))


def _noisy(noise, title=TITLE):
    return _person(title, f"{CLEAN} {noise}")


# ---------------------------------------------------------------------------
# 1. Text that is NOT the candidate's own current evidence must not classify them
# ---------------------------------------------------------------------------

CONTAMINATION = [
    ("role_at_another_employer", "After two years at Acme Telecom as Chief Information Officer I moved on"),
    ("reports_to_someone", "I report to the Chief Technology Officer"),
    ("colleague_role", "Our Chief Information Officer John Smith leads data center strategy"),
    ("leadership_of_another_person", "Working under the leadership of Chief Executive Officer Ahmed Al Amry"),
    ("she_is_unattributed", "She is the Director of Cyber Security"),
    ("he_after_welcome_of_other", "Welcome Sam Lee to the team. He is the new Director of Data Centre Operations"),
    ("pronoun_after_other_persons_clause", "Sam Lee joined last week as Chief Information Security Officer. He leads cybersecurity"),
    ("repost_of_third_party_content", "Reposted: Our Chief Executive Officer opened the new data center"),
]


@pytest.mark.parametrize("noise", [n for _, n in CONTAMINATION], ids=[i for i, _ in CONTAMINATION])
def test_unrelated_text_does_not_change_a_confirmed_candidates_classification(noise):
    assert _signature(_noisy(noise)) == _signature(_person(TITLE, CLEAN))


@pytest.mark.parametrize("noise", [n for _, n in CONTAMINATION], ids=[i for i, _ in CONTAMINATION])
def test_unrelated_text_stays_out_of_the_classification_basis(noise):
    a = assess_results([(TITLE, f"{CLEAN} {noise}")], ORG, organization_country=COUNTRY, candidate_name="Jane Doe")
    key = noise.split(".")[-1].strip().split()[-1].lower()      # a distinctive trailing word of the clause
    assert key not in a.basis_text.lower(), a.basis_text


# Already handled today -- kept as regression controls.
ALREADY_EXCLUDED = [
    ("recommendation_by_other", "- Bob Lee: “Jane worked closely with our Head of Cloud Infrastructure and the Chief Technology Officer”"),
    ("other_name_led_is", "John Smith is Chief Information Officer"),
    ("other_name_led_dash", "John Smith - Director of Cyber Security"),
    ("related_profiles_tail", "People also viewed\nJohn Smith - Chief Information Officer"),
    ("welcome_post", "Welcome Sara Khan, who has joined as Chief Financial Officer"),
    ("hiring_ad", "We are hiring a Director of Cyber Security"),
    ("ended_role_with_marker", "Previously worked as Chief Information Officer"),
]


@pytest.mark.parametrize("noise", [n for _, n in ALREADY_EXCLUDED], ids=[i for i, _ in ALREADY_EXCLUDED])
def test_already_excluded_text_stays_excluded(noise):
    assert _signature(_noisy(noise)) == _signature(_person(TITLE, CLEAN))


# ---------------------------------------------------------------------------
# 2. Supplemental / Brave extras: no association, no classification
# ---------------------------------------------------------------------------

def test_supplemental_and_extra_snippets_do_not_classify_a_confirmed_candidate():
    extras = ["Chief Information Officer, cybersecurity, data center", "Director of Cloud Infrastructure"]
    p = _person(TITLE, CLEAN, supplemental=extras, extra_snippets=extras)
    assert _signature(p) == _signature(_person(TITLE, CLEAN))
    assert p.context_snippets, "extras are kept as context"


def test_extras_cannot_associate_an_otherwise_unassociated_candidate():
    extras = [f"Jane Doe - Chief Information Officer - {ORG} | LinkedIn", f"Chief Information Officer at {ORG}",
              f"Headline: Chief Information Officer at {ORG}"]
    r = assess_people([_source("Jane Doe", CLEAN, supplemental=extras, extra_snippets=extras)],
                      organization_name=ORG, organization_country=COUNTRY)
    assert r.confirmed == [] and r.unverified == [] and len(r.rejected) == 1


# ---------------------------------------------------------------------------
# 3. Legitimate classification from the candidate's own current evidence is preserved
# ---------------------------------------------------------------------------

def test_headline_current_role_and_seniority_fields_still_classify():
    p = _person(f"Jane Doe - {ORG}",
                "Headline: Director of Data Center Operations at Oman Airports\n"
                "Current role: Director of Data Center Operations\nSeniority: Director")
    assert p.seniority == "director" and "data_center" in p.technology_areas and "infrastructure" in p.functions


def test_title_role_still_classifies():
    p = _person(f"Jane Doe - Director of Cyber Security - {ORG} | LinkedIn", CLEAN)
    assert p.seniority == "director" and p.functions == ["cybersecurity"] and p.technology_areas == ["cybersecurity"]


def test_current_dated_experience_role_at_the_target_still_classifies():
    p = _person(f"Jane Doe - {ORG}", f"## Experience\n- Chief Information Officer at {ORG} (Jan 2022 to present)")
    assert p.role_id == "cio" and p.seniority == "c_level"


def test_ended_experience_roles_still_do_not_classify():
    p = _person(f"Jane Doe - {ORG} | LinkedIn",
                f"## Experience\n- Chief Information Officer at Acme (Jan 2015 to Dec 2020)\n"
                f"- Analyst at {ORG} (Jan 2021 to present)")
    assert p.role_id is None and p.seniority is None


def test_first_person_current_role_statement_still_classifies():
    p = _person(f"Jane Doe - {ORG}", f"I work at {ORG} as Head of Cloud Infrastructure")
    assert p.role_id == "head_of_infrastructure" and "cloud" in p.technology_areas


def test_candidate_name_led_current_role_statement_still_classifies():
    p = _person(f"Jane Doe - {ORG}", f"Jane Doe is the Director of Cyber Security at {ORG}")
    assert p.seniority == "director" and p.functions == ["cybersecurity"]


# ---------------------------------------------------------------------------
# 4. Anita Gackowska evidence pattern (offline): a card + posts that mention
#    other employers, other people and a connectivity programme
# ---------------------------------------------------------------------------

ANITA = (
    "# Anita Gackowska\nOman Airports Management Company\nMuscat, Masqaţ, Oman, OM  \n500 connections, 6780 followers\n\n"
    "## About\nA dedicated and dynamic professional, combining strong leadership skills and business…\n\n"
    "## Experience\nN/A\n\n## Education\nN/A [...] - Delighted to be back in the Middle East to continue my aviation "
    "journey! After an fantastic year and a half at IAG/BA as Head of Airport Affairs—where I had the pleasure of "
    "working alongside Gavin Molloy and Matt Davies—I’ve now relocated to Oman to take on a new challenge. I’ve joined "
    "Oman Airports Management Company as VP Strategic Incentives, under the leadership of CEO Ahmed Al Amry. With over "
    "five years of recent experience in the region, including my time at the Saudi Air Connectivity Programme"
)


def test_anita_pattern_is_not_classified_from_another_employers_role_or_another_persons_title():
    p = _person("Anita Gackowska", ANITA, slug="anita-gackowska")
    assert p.organization_association == "confirmed"
    assert p.seniority != "head", "taken from her former 'Head of Airport Affairs' at IAG/BA"
    assert p.seniority != "c_level", "taken from 'CEO Ahmed Al Amry'"
    assert "network" not in p.technology_areas, "taken from the 'Saudi Air Connectivity' programme"
    assert "connectivity" not in [t.lower() for t in p.classification_matched_terms]


def test_anita_pattern_keeps_her_own_stated_role_at_the_target():
    p = _person("Anita Gackowska", ANITA, slug="anita-gackowska")
    a = assess_results([("Anita Gackowska", ANITA)], ORG, organization_country=COUNTRY, candidate_name="Anita Gackowska")
    assert "VP Strategic Incentives" in a.basis_text
    assert p.organization_association == "confirmed"


# ---------------------------------------------------------------------------
# 5. The structured Headline field is kept WHOLE, pipe-separated segments included
# ---------------------------------------------------------------------------

def test_pipe_separated_headline_segments_are_all_preserved():
    p = _person(f"Jane Doe - {ORG}",
                f"Headline: Chief Information Officer | Cloud | Cybersecurity at Oman Airports\n"
                f"Location: Muscat, Oman")
    assert p.role_id == "cio" and p.seniority == "c_level"
    assert {"cloud", "cybersecurity"} <= set(p.technology_areas)


def test_pipe_segments_in_headline_do_not_need_a_current_role_line():
    p = _person(f"Jane Doe - {ORG}", "Headline: Network Engineer | Data Center | Cloud at Oman Airports")
    assert {"network", "data_center", "cloud"} <= set(p.technology_areas)


def test_pipe_separated_text_outside_the_headline_field_is_not_classified():
    p = _person(f"Jane Doe - Network Engineer - {ORG} | LinkedIn", "Muscat, Oman. Interests: Cloud | Cybersecurity | Data Center")
    assert set(p.technology_areas) == {"network"}


def test_headline_of_another_person_is_not_classified():
    p = _person(f"Jane Doe - Network Engineer - {ORG} | LinkedIn",
                "Muscat, Oman.\nSam Lee - Headline: Chief Information Officer | Cloud | Cybersecurity")
    assert set(p.technology_areas) == {"network"} and p.role_id is None


def test_related_profile_headline_is_not_classified():
    p = _person(f"Jane Doe - Network Engineer - {ORG} | LinkedIn",
                "Muscat, Oman.\nPeople also viewed\nHeadline: Chief Information Officer | Cloud")
    assert set(p.technology_areas) == {"network"} and p.role_id is None


def test_historical_headline_is_not_classified():
    p = _person(f"Jane Doe - Network Engineer - {ORG} | LinkedIn",
                "Muscat, Oman.\nHeadline: Former Chief Information Officer | Cloud")
    assert set(p.technology_areas) == {"network"} and p.role_id is None

"""People current-title extraction fallback. When the result title carries no
role of its own, the person's explicit `Current role:` field, then a current
`Headline:` field, supply the title. Never from supplemental/extra snippets,
related-person text, historical roles or ended employment. Offline only."""

from __future__ import annotations

from commercial.people import build_people
from research.sources import AccessStatus, Source

ORG = "Oman Airports Management Company"


def _profile(title, snippet, *, extras=None, slug="jane-doe"):
    s = Source(
        url=f"https://om.linkedin.com/in/{slug}", domain="linkedin.com", title=title,
        source_type="linkedin_profile", snippet=snippet, discovered_via_query="q", discovery_provider="brave",
        family="linkedin_people", access_status=AccessStatus.INDEXED_SNIPPET_ONLY, source_quality=50.0,
    )
    s.extra.update({"linkedin_kind": "in", "supplemental_snippets": list(extras or [])})
    return s


def _one(title, snippet, **kw):
    people = build_people([_profile(title, snippet, **kw)], organization_name=ORG, organization_country="Oman")
    return people[0] if people else None


NAME_ORG = "Jane Doe - Oman Airports Management Company"


def test_current_role_field_supplies_title_when_result_title_has_none():
    p = _one(NAME_ORG, "Headline: Whatever at Oman Airports\nCurrent role: Head Of Cyber Security\nSeniority: Head")
    assert p is not None and p.organization_association == "confirmed"
    assert p.current_title == "Jane Doe - Head Of Cyber Security"


def test_current_role_field_wins_over_headline():
    p = _one(NAME_ORG, "Headline: Chief Dreamer at Oman Airports\nCurrent role: Head Of Cyber Security\nLocation: Muscat, Oman")
    assert p.current_title == "Jane Doe - Head Of Cyber Security"


def test_headline_fallback_strips_target_employer_and_is_used_without_current_role():
    p = _one(NAME_ORG, "Headline: Head of Cyber Security at Oman Airports\nLocation: Muscat, Oman")
    assert p is not None and p.organization_association == "confirmed"
    assert p.current_title == "Jane Doe - Head of Cyber Security"


def test_result_title_with_a_role_is_never_replaced():
    t = "Jane Doe - Chief Information Officer - Oman Airports Management Company | LinkedIn"
    p = _one(t, "Headline: Head of Cyber Security at Oman Airports\nCurrent role: Head Of Cyber Security")
    assert p.current_title == t


def test_truncated_result_title_role_still_counts_as_a_role():
    t = "Jane Doe - Vice President - Infrastructure & Security at ..."
    p = _one(t, "Headline: Vice President - Infrastructure & Security at Oman Airports\n"
                "## Experience\n- Vice President at Oman Airports Management Company (Dec 2022 to present)")
    assert p is not None and p.current_title == t


def test_headline_at_a_different_employer_gives_no_fallback_title():
    from commercial.people_association import fallback_current_title
    assert fallback_current_title(["Headline: Chief Financial Officer at Acme Holdings"], "Jane Doe", ORG) is None
    # a sibling entity of the same brand is not the target either
    assert fallback_current_title(["Headline: Analyst at Oman Air"], "Jane Doe", ORG) is None
    assert fallback_current_title(["Headline: Analyst at Oman Airports"], "Jane Doe", ORG) == ("Analyst", "Headline field")


def test_historical_headline_is_not_a_title():
    p = _one(NAME_ORG, "Headline: Former Head of Cyber Security at Oman Airports\n"
                       "Experience: Oman Airports Management Company")
    assert p is not None and p.current_title == NAME_ORG


def test_ended_experience_roles_never_supply_the_title():
    p = _one(NAME_ORG, "## Experience\n- Chief Information Officer at Oman Airports Management Company (Mar 2015 to Jan 2023)\n"
                       "- Analyst at Oman Airports Management Company (Dec 2023 to present)")
    assert p is not None and p.current_title == NAME_ORG


def test_related_profile_current_role_is_not_the_title():
    p = _one(NAME_ORG, "Headline: Analyst at Oman Airports\nPeople also viewed\nCurrent role: Chief Executive Officer")
    assert p is not None and "Chief Executive" not in p.current_title
    assert p.current_title == "Jane Doe - Analyst"


def test_other_persons_current_role_line_is_not_the_title():
    p = _one(NAME_ORG, "John Smith - Current role: Chief Executive Officer [...] Headline: Analyst at Oman Airports")
    assert p is not None and "Chief Executive" not in p.current_title
    assert p.current_title == "Jane Doe - Analyst"


def test_conflicting_current_role_values_give_no_guess():
    p = _one(NAME_ORG, "Current role: Chief Executive Officer\n[...]\nCurrent role: Head of Audit\nHeadline: Analyst at Oman Airports")
    assert p is not None and p.current_title == NAME_ORG


def test_supplemental_and_extra_snippets_never_supply_the_title():
    p = _one(NAME_ORG, "Headline: Analyst at Oman Airports",
             extras=["Current role: Chief Executive Officer", "Headline: Chair at Oman Airports"])
    assert p is not None
    assert "Chief Executive" not in p.current_title and "Chair" not in p.current_title
    assert p.current_title == "Jane Doe - Analyst"


def test_no_title_evidence_leaves_the_result_title_unchanged():
    p = _one(NAME_ORG, "Experience: Oman Airports Management Company")
    assert p is not None and p.current_title == NAME_ORG


def test_unverified_candidates_do_not_get_a_fallback_title():
    from commercial.people import assess_people
    r = assess_people([_profile("Jane Doe - Oman Airports", "Headline: Head of IT at Oman Airports\nCurrent role: Head of IT")],
                      organization_name=ORG, organization_country="Oman")
    for p in r.unverified:
        assert p.current_title == "Jane Doe - Oman Airports"

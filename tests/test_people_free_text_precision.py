"""People association precision for free-text and card evidence.

Offline only: in-memory Source objects and, where present, the project's saved
evidence corpus. No network, no API keys.

Policy under test
-----------------
* A person's own profile card naming the target confirms employment when
  nothing contradicts it (card-only stays confirmed).
* A card naming the target does NOT silently override a dated Experience entry
  showing that employment ended: the result is needs_verification, with an
  inspectable reason.
* Free text confirms only when it is attributed to the candidate AND describes
  present employment. Congratulations, hiring ads, welcomes, reposts,
  aspirations, third-person clauses and clauses led by another person's name
  (or a bare third-person pronoun) establish nothing.
* An undated first-person "I've joined <target>" post is a claimed employment
  event, not reliable current employment: on its own it is needs_verification.
  Combined with independent structured current evidence it stays confirmed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from commercial.people import assess_people
from commercial.people_association import (
    CONFIRMED_CURRENT,
    NEEDS_VERIFICATION,
    assess_results,
)
from research.sources import AccessStatus, Source

ORG = "Oman Airports Management Company"
COUNTRY = "Oman"
ROOT = Path(__file__).resolve().parent.parent


def _source(title, snippet, slug="jane-doe", extras=None):
    s = Source(
        url=f"https://om.linkedin.com/in/{slug}", domain="linkedin.com", title=title,
        source_type="linkedin_profile", snippet=snippet, discovered_via_query="q", discovery_provider="brave",
        family="linkedin_people", access_status=AccessStatus.INDEXED_SNIPPET_ONLY, source_quality=50.0,
    )
    s.extra.update({"linkedin_kind": "in", "supplemental_snippets": list(extras or []), "extra_snippets": []})
    return s


def _verdict(title, snippet, **kw):
    """-> (status, reasons) through the real People path (assess_people)."""
    r = assess_people([_source(title, snippet, **kw)], organization_name=ORG, organization_country=COUNTRY)
    if r.confirmed:
        return CONFIRMED_CURRENT, list(r.confirmed[0].verification_reasons)
    if r.unverified:
        return NEEDS_VERIFICATION, list(r.unverified[0].verification_reasons)
    return "rejected", list(r.rejected[0]["reasons"]) if r.rejected else []


def _card(company=ORG, experience="N/A"):
    return (f"# Jane Doe\n{company}\nMuscat, Masqaţ, Oman, OM  \n500 connections, 6000 followers\n\n"
            f"## About\nA dedicated professional.\n\n## Experience\n{experience}\n\n## Education\nN/A")


BARE = "Jane Doe"          # a result title that carries no employer at all


# ---------------------------------------------------------------------------
# 1. Free text that must NOT establish the candidate's current employment
# ---------------------------------------------------------------------------

NOT_EVIDENCE = [
    ("congratulations", f"Congratulations Sara on your new role at {ORG}!"),
    ("congratulations_joined", f"Congratulations to everyone who joined {ORG} this year."),
    ("hiring_ad", f"We are hiring a Network Engineer at {ORG}"),
    ("hiring_join_us", f"Join us at {ORG} as a Senior Analyst - apply now"),
    ("welcome_post", f"Welcome Sara Khan, who has joined {ORG} as CFO"),
    ("welcome_colleague", f"Please welcome our new colleague to {ORG} as Head of Audit"),
    ("repost_from_other", f"Reposted from John Smith: John is CIO at {ORG}"),
    ("repost_colon_led", f"Reposted: John Smith: CIO at {ORG}"),
    ("aspiration_work_for", f"Looking to work for {ORG} one day"),
    ("aspiration_dream", f"My dream is to work for {ORG}"),
    ("other_name_colon", f"John Smith: CIO at {ORG}"),
    ("other_name_bullet", f"John Smith • CIO at {ORG}"),
    ("other_name_dash", f"John Smith - CIO at {ORG}"),
    ("other_name_is", f"John Smith is CIO at {ORG}"),
    ("other_name_recommendation", f"- Bob Lee: “I work at {ORG}. Jane is great.”"),
    ("pronoun_he_joined", f"He joined {ORG} as CFO"),
    ("pronoun_she_is", f"She is now CIO at {ORG}"),
    ("pronoun_they_work", f"They work at {ORG}"),
]


@pytest.mark.parametrize("snippet", [s for _, s in NOT_EVIDENCE], ids=[i for i, _ in NOT_EVIDENCE])
def test_unattributed_or_non_employment_clauses_never_confirm(snippet):
    status, _ = _verdict(BARE, snippet)
    assert status != CONFIRMED_CURRENT, f"confirmed from: {snippet!r}"


@pytest.mark.parametrize("snippet", [s for _, s in NOT_EVIDENCE], ids=[i for i, _ in NOT_EVIDENCE])
def test_such_clauses_do_not_add_a_target_claim_for_the_candidate(snippet):
    a = assess_results([(BARE, snippet)], ORG, organization_country=COUNTRY, candidate_name="Jane Doe")
    assert a.status != CONFIRMED_CURRENT
    assert not [c for c in a.claims if c.entity == "target" and c.temporal == "current"], \
        [c.describe() for c in a.claims]


def test_such_clauses_do_not_disturb_a_genuinely_confirmed_person():
    """Noise clauses neither confirm nor conflict: a person with a real current
    employer statement stays confirmed."""
    noise = "\n".join(s for _, s in NOT_EVIDENCE)
    status, _ = _verdict(f"Jane Doe - Analyst - {ORG} | LinkedIn", noise)
    assert status == CONFIRMED_CURRENT


# ---------------------------------------------------------------------------
# 2. B1: undated first-person post alone = claimed event, needs verification
# ---------------------------------------------------------------------------

FIRST_PERSON_EVENTS = [
    f"Thrilled to share that I’ve joined {ORG} as VP Strategy.",
    f"I have joined {ORG} as Head of Infrastructure.",
    f"I joined {ORG} as a Senior Manager.",
    f"Delighted to announce that I’m joining {ORG} as CIO.",
]


@pytest.mark.parametrize("post", FIRST_PERSON_EVENTS)
def test_undated_first_person_join_post_alone_needs_verification(post):
    status, reasons = _verdict(BARE, post)
    assert status == NEEDS_VERIFICATION
    assert reasons, "the reason must be inspectable"


@pytest.mark.parametrize("post", FIRST_PERSON_EVENTS)
def test_first_person_join_post_never_counts_as_confirmed_stakeholder(post):
    r = assess_people([_source(BARE, post)], organization_name=ORG, organization_country=COUNTRY)
    assert r.confirmed == []
    assert len(r.unverified) == 1


def test_first_person_post_plus_independent_structured_evidence_stays_confirmed():
    status, _ = _verdict(f"Jane Doe - Analyst - {ORG} | LinkedIn", FIRST_PERSON_EVENTS[0])
    assert status == CONFIRMED_CURRENT


def test_first_person_post_does_not_override_a_card_naming_another_company():
    status, reasons = _verdict(BARE, _card(company="Acme Holdings") + f" [...] - I’ve joined {ORG} as VP Strategy.")
    assert status == NEEDS_VERIFICATION
    assert any("Acme" in r for r in reasons)


# ---------------------------------------------------------------------------
# 3. Card + dated Experience that shows the employment ended
# ---------------------------------------------------------------------------

def test_card_naming_target_but_dated_experience_ended_needs_verification_with_reason():
    status, reasons = _verdict(BARE, _card(experience=f"- Analyst at {ORG} (Mar 2015 to Jan 2023)"))
    assert status == NEEDS_VERIFICATION
    text = " ".join(reasons).lower()
    assert "ended" in text and "card" in text, reasons


def test_card_ended_conflict_is_reported_by_assess_results_with_both_claims():
    a = assess_results([(BARE, _card(experience=f"- Analyst at {ORG} (Mar 2015 to Jan 2023)"))], ORG,
                       organization_country=COUNTRY, candidate_name="Jane Doe")
    assert a.status == NEEDS_VERIFICATION
    descs = [c.describe() for c in a.claims]
    assert any("profile card" in d and "current" in d for d in descs)
    assert any("Experience section" in d and "historical" in d for d in descs)


def test_card_naming_target_with_a_current_dated_entry_stays_confirmed():
    status, _ = _verdict(BARE, _card(experience=f"- Analyst at {ORG} (Mar 2015 to present)"))
    assert status == CONFIRMED_CURRENT


def test_card_with_earlier_ended_entry_and_a_newer_current_entry_stays_confirmed():
    exp = (f"- Manager at {ORG} (Jan 2023 to present)\n- Analyst at {ORG} (Mar 2015 to Dec 2022)")
    status, _ = _verdict(BARE, _card(experience=exp))
    assert status == CONFIRMED_CURRENT


def test_card_naming_target_with_current_job_elsewhere_needs_verification():
    status, reasons = _verdict(BARE, _card(experience="- CFO at Acme Holdings (Feb 2023 to present)"))
    assert status == NEEDS_VERIFICATION and reasons


# ---------------------------------------------------------------------------
# 4. Positive controls: legitimate evidence is not rejected for being free text
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snippet", [
    f"Jane Doe is the Chief Information Officer at {ORG}.",
    f"Jane Doe works at {ORG} as a Network Architect.",
    f"Chief Information Officer at {ORG}. Based in Muscat, Oman.",
    f"Headline: Head of Cyber Security at {ORG}\nLocation: Muscat, Oman",
    f"Experience: {ORG}",
    f"## Experience\n- Vice President at {ORG} (Dec 2022 to present)",
], ids=["name_led_is", "name_led_works", "profile_description", "headline_field", "experience_field", "dated_present"])
def test_candidate_attributed_current_evidence_confirms(snippet):
    status, _ = _verdict(BARE, snippet)
    assert status == CONFIRMED_CURRENT


def test_title_company_segment_confirms():
    status, _ = _verdict(f"Jane Doe - {ORG}", "")
    assert status == CONFIRMED_CURRENT


def test_card_only_evidence_confirms_when_nothing_contradicts():
    status, _ = _verdict(BARE, _card())
    assert status == CONFIRMED_CURRENT


def test_card_naming_a_sibling_company_is_still_rejected():
    status, _ = _verdict(BARE, _card(company="Oman Air"))
    assert status != CONFIRMED_CURRENT


# ---------------------------------------------------------------------------
# 5. Anita Gackowska evidence pattern (offline)
# ---------------------------------------------------------------------------

ANITA_PATTERN = (
    "# Anita Gackowska\nOman Airports Management Company\nMuscat, Masqaţ, Oman, OM  \n"
    "500 connections, 6780 followers\n\n## About\nA dedicated and dynamic professional, combining strong "
    "leadership skills and business…\n\n## Experience\nN/A\n\n## Education\n### The Corporate Governance Institute  \n"
    "N/A  \n2024-01 - 2024-02\nNone\n\n## Volunteering\nN/A"
)
ANITA_POST = (
    " [...] - Delighted to be back in the Middle East to continue my aviation journey! After an fantastic year and "
    "a half at IAG/BA as Head of Airport Affairs I’ve now relocated to Oman to take on a new challenge. I’ve joined "
    "Oman Airports Management Company as VP Strategic Incentives, under the leadership of CEO Ahmed Al Amry."
)


def test_anita_pattern_card_plus_first_person_post_remains_confirmed():
    status, _ = _verdict("Anita Gackowska", ANITA_PATTERN + ANITA_POST, slug="anita-gackowska")
    assert status == CONFIRMED_CURRENT


def test_anita_pattern_card_only_remains_confirmed():
    status, _ = _verdict("Anita Gackowska", ANITA_PATTERN, slug="anita-gackowska")
    assert status == CONFIRMED_CURRENT


def test_anita_pattern_confirmation_rests_on_the_card_not_the_post():
    a = assess_results([("Anita Gackowska", ANITA_PATTERN + ANITA_POST)], ORG, organization_country=COUNTRY,
                       candidate_name="Anita Gackowska")
    assert any(c.provenance == "profile card" and c.entity == "target" and c.temporal == "current" for c in a.claims)
    assert any("profile card" in e for e in a.evidence)


def test_anita_pattern_post_without_the_card_is_not_confirmation():
    status, _ = _verdict("Anita Gackowska", ANITA_POST.strip(" [.]-"), slug="anita-gackowska")
    assert status == NEEDS_VERIFICATION


def _saved_corpus():
    found = sorted(ROOT.glob("evidence_corpus_*.json"))
    return found[0] if found else None


@pytest.mark.skipif(_saved_corpus() is None, reason="no saved evidence corpus in the project folder")
def test_saved_fixture_anita_is_confirmed_from_the_card_only_as_far_as_the_fixture_shows():
    """Pins what the SAVED fixture supports: one result, a profile card naming
    the target, Experience 'N/A', a first-person post. It does not verify the
    live profile."""
    from compare_evidence_fix import _source_from_dict

    saved = json.loads(_saved_corpus().read_text(encoding="utf-8"))
    sources = [_source_from_dict(x) for x in saved["sources"]]
    anita = [s for s in sources if s.source_type == "linkedin_profile" and "anita-gackowska" in s.url.lower()]
    if not anita:
        pytest.skip("Anita Gackowska is not in this saved corpus")
    assert len(anita) == 1 and not anita[0].extra.get("supplemental_snippets")
    r = assess_people(anita, organization_name=saved["organization_name"],
                      organization_country=saved.get("country"))
    assert len(r.confirmed) == 1 and not r.unverified
    assert r.confirmed[0].organization_association == "confirmed"

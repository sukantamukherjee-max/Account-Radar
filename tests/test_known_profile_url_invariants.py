"""A known LinkedIn profile URL identifies a profile; it never verifies employment.

Characterisation tests for the CURRENT behaviour that any future "known profile
URL as a discovery lead" feature must keep (no production change is made here):

  * a URL with no profile text cannot create a person -- it is not even listed
    as confirmed / unverified / rejected;
  * a name-only title, or a slug that spells the organisation, is not employment
    evidence;
  * when profile text for the known URL exists it is judged by the unchanged
    association rules (confirmed / unverified / rejected), whoever supplied the URL;
  * a URL written inside someone else's snippet creates nothing for that URL;
  * slug normalisation is host-, case-, query- and trailing-slash-insensitive, and
    only /in/<slug> pages are profiles.

Synthetic data only; no network.
"""

from __future__ import annotations

import pytest

from commercial.people import _profile_slug, assess_people, deduplicate_profile_sources
from research.providers.base import RawResult
from research.sources import SOURCE_TYPE_LINKEDIN_PROFILE, Source

import streamlit as _REAL_STREAMLIT  # noqa: E402,F401  (see tests/test_theme_engine.py)

ORG = "Ooredoo Oman"
COUNTRY = "Oman"
SLUG = "layla-al-example-1a2b3c4d"                       # fictional
KNOWN_URL = f"https://www.linkedin.com/in/{SLUG}/"


def _source(url, title="", snippet="", provider="brave"):
    return Source.from_raw(RawResult(url=url, title=title, snippet=snippet, query="q", provider=provider, rank=0),
                           source_type=SOURCE_TYPE_LINKEDIN_PROFILE, family="linkedin_people")


def _assess(*sources):
    return assess_people(list(sources), organization_name=ORG, organization_country=COUNTRY)


def _everyone(result):
    return [*result.confirmed, *result.unverified, *result.rejected]


# --- URL identification is not employment verification -------------------------

def test_a_known_url_with_no_profile_text_creates_no_person_at_all():
    result = _assess(_source(KNOWN_URL, "", ""))
    assert _everyone(result) == []                        # not confirmed, not unverified, not even rejected


@pytest.mark.parametrize("title,snippet", [
    ("Layla Al-Example | LinkedIn", ""),
    ("Layla Al-Example | LinkedIn", "Muscat, Oman. 500+ connections"),
    ("Dr. Layla Al-Example - Chief Technology and Information Officer | LinkedIn", "Muscat, Oman"),
])
def test_a_known_url_with_a_name_or_a_title_but_no_employer_is_never_confirmed(title, snippet):
    result = _assess(_source(KNOWN_URL, title, snippet))
    assert result.confirmed == [] and result.unverified == []
    assert [r["url"] for r in result.rejected] == [f"https://www.linkedin.com/in/{SLUG}"]
    assert any("no current-employer statement" in reason for reason in result.rejected[0]["reasons"])


def test_a_slug_that_spells_the_organisation_is_not_evidence():
    url = "https://www.linkedin.com/in/someone-ooredoo-oman-5a5a5a"
    result = _assess(_source(url, "Someone | LinkedIn", "Muscat, Oman"))
    assert result.confirmed == [] and result.unverified == []


# --- Profile text for a known URL is judged by the unchanged association rules ----

@pytest.mark.parametrize("title,snippet,bucket", [
    (f"Layla Al-Example - Chief Technology and Information Officer - {ORG} | LinkedIn",
     f"Chief Technology and Information Officer at {ORG}. Oman", "confirmed"),
    ("Layla Al-Example - Ooredoo", "Chief Technology and Information Officer at Ooredoo. Oman", "unverified"),
    ("Layla Al-Example - Ooredoo Group", "Chief Technology and Information Officer at Ooredoo Group. Qatar", "rejected"),
    ("Layla Al-Example - Chief Technology Officer - Omantel | LinkedIn", "Chief Technology Officer at Omantel", "rejected"),
])
def test_known_url_with_profile_text_gets_the_normal_status(title, snippet, bucket):
    result = _assess(_source(KNOWN_URL, title, snippet))
    got = ("confirmed" if result.confirmed else "unverified" if result.unverified else
           "rejected" if result.rejected else "none")
    assert got == bucket


def test_an_old_appointment_announcement_for_the_same_name_is_not_employment():
    title, snippet = "Layla Al-Example | LinkedIn", ("Layla Al-Example on her appointment as Chief Technology and "
                                                      "Information Officer, and we are proud of the Omani leaders.")
    result = _assess(_source(KNOWN_URL, title, snippet))
    assert result.confirmed == [] and result.unverified == []


# --- A URL inside another person's snippet creates nothing ---------------------------

def test_a_url_quoted_in_another_persons_snippet_creates_no_person_for_that_url():
    host = _source("https://www.linkedin.com/in/host-person-77", f"Host Person - {ORG} | LinkedIn",
                   f"Marketing at {ORG}. See linkedin.com/in/{SLUG} for our new CTIO.")
    result = _assess(host)
    assert all(SLUG not in (p.linkedin_url if hasattr(p, "linkedin_url") else p["url"]) for p in _everyone(result))
    assert [p.name for p in result.confirmed] == ["Host Person"]


# --- Slug normalisation: the identity key a future lead would rely on -------------------

@pytest.mark.parametrize("url", [
    f"https://www.linkedin.com/in/{SLUG}/",
    f"https://om.linkedin.com/in/{SLUG}",
    f"https://www.linkedin.com/in/{SLUG.upper()}?trackingId=abc&trk=public",
    f"http://linkedin.com/in/{SLUG}#experience",
])
def test_profile_slug_ignores_host_case_query_fragment_and_trailing_slash(url):
    assert _profile_slug(url) == SLUG


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/company/ooredoo-oman/",
    "https://www.linkedin.com/posts/someone_activity-123",
    "https://www.linkedin.com/pub/someone/1/2/3",
    "https://www.linkedin.com/in/",
    "https://example.com/in/" + SLUG,                        # right path shape, wrong site: slug helper is path-only,
])                                                           # so the HOST must be checked by whoever accepts a URL
def test_only_in_slash_slug_pages_have_a_slug(url):
    if url.startswith("https://example.com"):
        assert _profile_slug(url) == SLUG                    # documents that host validation is the caller's job
    else:
        assert _profile_slug(url) is None


def test_the_same_slug_on_different_hosts_deduplicates_to_one_profile():
    a = _source(f"https://www.linkedin.com/in/{SLUG}/", f"L - {ORG}", f"Chief Technology Officer at {ORG}")
    b = _source(f"https://om.linkedin.com/in/{SLUG}", f"L - {ORG}", "x")
    assert len(deduplicate_profile_sources([a, b])) == 1
    assert len(_assess(a, b).confirmed) == 1

"""Known LinkedIn profile URL -> IDENTITY lead (pure helpers; no network, no keys).

A known URL / slug is identity information only. It is never employment
evidence; employment is judged exclusively by the existing association gate.
Synthetic, fictional data only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from research import known_profiles as kp
from research.known_profiles import (
    ALREADY_DISCOVERED,
    INVALID_URL,
    RESOLVED_VIA_SEARCH,
    RESOLVED_VIA_SUPPLIED_TEXT,
    STATES,
    UNRESOLVED_NO_CONTENT,
    build_probe_query,
    canonicalize_profile_url,
    invalid_url_reason,
    match_results,
    sanitize_excerpt,
    source_from_supplied_text,
)
from research.providers.base import RawResult

import streamlit as _REAL_STREAMLIT  # noqa: E402,F401  (see tests/test_theme_engine.py)

SLUG = "layla-al-example-1a2b3c4d"
CANON = f"https://www.linkedin.com/in/{SLUG}"
ROOT = Path(__file__).resolve().parent.parent


def test_the_five_identity_states_are_exactly_these():
    assert STATES == (INVALID_URL, ALREADY_DISCOVERED, RESOLVED_VIA_SEARCH, RESOLVED_VIA_SUPPLIED_TEXT,
                      UNRESOLVED_NO_CONTENT)
    assert STATES == ("invalid_url", "already_discovered", "resolved_via_search", "resolved_via_supplied_text",
                      "unresolved_no_content")


# ---------------------------------------------------------------------------
# URL canonicalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    f"https://www.linkedin.com/in/{SLUG}",
    f"https://www.linkedin.com/in/{SLUG}/",
    f"http://linkedin.com/in/{SLUG}",
    f"https://om.linkedin.com/in/{SLUG}",
    f"https://m.linkedin.com/in/{SLUG}/",
    f"HTTPS://WWW.LINKEDIN.COM/in/{SLUG.upper()}/",
    f"https://www.linkedin.com/in/{SLUG}?trk=public_profile&utm_source=x",
    f"https://www.linkedin.com/in/{SLUG}/?originalSubdomain=om#section",
    f"  https://www.linkedin.com/in/{SLUG}  ",
    f"linkedin.com/in/{SLUG}",
    f"www.linkedin.com/in/{SLUG}/",
])
def test_valid_profile_urls_canonicalize_to_one_form(url):
    c = canonicalize_profile_url(url)
    assert c is not None and c.slug == SLUG and c.url == CANON
    assert invalid_url_reason(url) is None


def test_percent_encoded_and_mixed_case_slugs_are_normalised():
    a = canonicalize_profile_url("https://www.linkedin.com/in/Layla-Al-Example-1A2B3C4D")
    b = canonicalize_profile_url("https://www.linkedin.com/in/layla%2Dal%2Dexample%2D1a2b3c4d")
    assert a.slug == b.slug == SLUG


@pytest.mark.parametrize("url,reason", [
    ("", "empty"),
    ("   ", "empty"),
    (None, "empty"),
    ("not a url", "not_a_profile_path"),
    ("javascript:alert(1)", "scheme"),
    ("ftp://www.linkedin.com/in/" + SLUG, "scheme"),
    ("https://example.com/in/" + SLUG, "host"),
    ("https://linkedin.com.evil.net/in/" + SLUG, "host"),
    ("https://notlinkedin.com/in/" + SLUG, "host"),
    ("https://evil.net/https://www.linkedin.com/in/" + SLUG, "host"),
    ("https://www.linkedin.com/company/ooredoo-oman", "not_a_profile_path"),
    ("https://www.linkedin.com/posts/someone_activity-123", "not_a_profile_path"),
    ("https://www.linkedin.com/pulse/some-article", "not_a_profile_path"),
    ("https://www.linkedin.com/feed/", "not_a_profile_path"),
    ("https://www.linkedin.com/pub/some-name/1/2/3", "not_a_profile_path"),
    ("https://www.linkedin.com/in/", "not_a_profile_path"),
    ("https://www.linkedin.com/in", "not_a_profile_path"),
    ("https://www.linkedin.com/", "not_a_profile_path"),
    (f"https://www.linkedin.com/in/{SLUG}/details/experience", "extra_subpath"),
    (f"https://www.linkedin.com/in/{SLUG}/recent-activity/all/", "extra_subpath"),
    (f"https://www.linkedin.com/in/{SLUG}/en", "extra_subpath"),
    ("https://www.linkedin.com/in/ab", "bad_slug"),
    ("https://www.linkedin.com/in/" + "a" * 101, "bad_slug"),
    ("https://www.linkedin.com/in/bad slug!", "bad_slug"),
    ("https://www.linkedin.com/in/%3Cscript%3E", "bad_slug"),
])
def test_invalid_urls_are_rejected_with_a_reason_code(url, reason):
    assert canonicalize_profile_url(url) is None
    assert invalid_url_reason(url) == reason


def test_canonical_object_carries_only_identity_never_employment():
    c = canonicalize_profile_url(CANON)
    assert set(vars(c)) == {"slug", "url"} or set(c.__dataclass_fields__) == {"slug", "url"}
    assert not hasattr(c, "organization_association") and not hasattr(c, "status")


# ---------------------------------------------------------------------------
# Exact slug versus near matches
# ---------------------------------------------------------------------------

def _r(url, title="x", snippet="y"):
    return RawResult(url=url, title=title, snippet=snippet, query="q", provider="brave", rank=0)


def test_only_an_exact_canonical_slug_matches():
    results = [
        _r(f"https://om.linkedin.com/in/{SLUG.upper()}/"),                    # same slug, other host/case/slash
        _r(f"https://www.linkedin.com/in/{SLUG}-2"),                           # longer
        _r(f"https://www.linkedin.com/in/{SLUG[:-1]}c"),                       # different id
        _r("https://www.linkedin.com/in/layla-al-example"),                    # same name, no id
        _r(f"https://www.linkedin.com/company/{SLUG}"),                        # company page
        _r(f"https://evil.net/in/{SLUG}"),                                     # other host
        _r(f"https://www.linkedin.com/posts/{SLUG}"),                          # post
    ]
    hit = match_results(SLUG, results)
    assert [r.url for r in hit] == [results[0].url]


def test_a_title_naming_the_person_never_matches_without_the_slug():
    namesake = _r("https://www.linkedin.com/in/someone-else-99", title="Layla Al-Example - Chief Technology Officer - Ooredoo Oman")
    assert match_results(SLUG, [namesake]) == []


def test_match_results_tolerates_empty_input():
    assert match_results(SLUG, []) == [] and match_results(SLUG, None) == []


# ---------------------------------------------------------------------------
# Probe query
# ---------------------------------------------------------------------------

def test_probe_query_is_one_site_restricted_slug_path_no_or_no_name():
    q = build_probe_query(True, SLUG)
    assert q == f"site:linkedin.com/in/{SLUG}" and " OR " not in q and '"' not in q
    plain = build_probe_query(False, SLUG)
    assert plain == f"linkedin.com/in/{SLUG}" and "site:" not in plain


# ---------------------------------------------------------------------------
# Supplied visible profile text
# ---------------------------------------------------------------------------

def test_supplied_text_becomes_a_labelled_source_with_the_exact_slug_url():
    text = "Layla Al-Example\nChief Technology and Information Officer at Ooredoo Oman\nMuscat, Oman"
    src = source_from_supplied_text(canonicalize_profile_url(CANON), text)
    assert src.url == CANON and src.title == "Layla Al-Example"
    assert "Chief Technology and Information Officer at Ooredoo Oman" in src.snippet
    assert src.extra["origin"] == "user_supplied_text" and src.extra["user_supplied_unverified"] is True
    assert src.discovery_provider == "user_supplied_text"
    assert src.extra["discovery_providers"] == ["user_supplied_text"]
    assert src.extra["linkedin_kind"] == "in"


@pytest.mark.parametrize("text", [None, "", "   \n\t ", "ok", "..."])
def test_unusable_text_produces_no_source(text):
    assert source_from_supplied_text(canonicalize_profile_url(CANON), text) is None


def test_supplied_text_is_cleaned_of_control_characters_and_bounded():
    text = "Layla Al-Example\x00\x07\nChief Technology Officer at Ooredoo Oman\n" + ("lorem ipsum " * 5000)
    src = source_from_supplied_text(canonicalize_profile_url(CANON), text)
    assert "\x00" not in src.title + src.snippet and "\x07" not in src.title + src.snippet
    assert len(src.title) <= 300 and len(src.snippet) <= 8000


# ---------------------------------------------------------------------------
# Sanitised excerpt for diagnostics (<= 400 chars, no secrets / query strings / tracking)
# ---------------------------------------------------------------------------

def test_excerpt_is_bounded_to_400_characters():
    assert len(sanitize_excerpt("word " * 1000)) <= 400
    assert sanitize_excerpt("short text") == "short text"
    assert sanitize_excerpt(None) == "" and sanitize_excerpt("") == ""


@pytest.mark.parametrize("text,gone", [
    ("see https://www.linkedin.com/in/x-1a2b3c4d?trk=public_profile&utm_source=share#top", ["trk=", "utm_source", "#top", "?"]),
    ("api_key=SUPERSECRETVALUE123 more", ["SUPERSECRETVALUE123"]),
    ("Authorization: Bearer abcDEF123456 end", ["abcDEF123456"]),
    ("token: AbCdEf0123456789AbCdEf0123456789 end", ["AbCdEf0123456789AbCdEf0123456789"]),
    ("X-Subscription-Token: BRAVE-SENTINEL-KEY-0001", ["BRAVE-SENTINEL-KEY-0001"]),
    ("password=hunter2 and secret=abc", ["hunter2"]),
    ("mail me at someone@example.com", ["someone@example.com"]),
])
def test_excerpt_removes_secrets_query_strings_and_tracking_tokens(text, gone):
    out = sanitize_excerpt(text)
    for g in gone:
        assert g not in out, (g, out)


def test_excerpt_keeps_the_url_path_slug_but_drops_the_query():
    out = sanitize_excerpt(f"profile https://www.linkedin.com/in/{SLUG}/?trk=abc#x ok")
    assert f"/in/{SLUG}" in out and "trk" not in out


# ---------------------------------------------------------------------------
# No personal names in production code
# ---------------------------------------------------------------------------

def test_no_personal_name_is_hard_coded_in_the_known_profile_module():
    text = (ROOT / "research" / "known_profiles.py").read_text(encoding="utf-8").lower()
    for name in ("jihad", "ansari", "hadhrami", "layla", "al-example", "60b7a23b"):
        assert name not in text

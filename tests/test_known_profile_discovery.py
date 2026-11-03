"""People discovery -- a KNOWN LinkedIn profile URL as an IDENTITY lead.

  * the URL / slug is identity only; it is never employment evidence;
  * only an exact canonical slug returned by a provider resolves it through search;
  * URL-only input with no usable content creates no person of any status;
  * user-supplied visible text goes through the unchanged association gate
    and is labelled as user supplied (never treated as independently verified);
  * at most one probe per URL, counted in the existing People budget (soft 30 /
    hard 45), run BEFORE the name-variant follow-up; Tavily is never used in brave_only.

Synthetic, fictional data only; no network, no keys.
"""

from __future__ import annotations

import json

import pytest

from commercial.people import assess_people
from research import linkedin as li
from research.linkedin import PEOPLE_MODE_ENV, PeopleDiscoveryConfig, discover_linkedin
from research.organization import ResolvedOrganization
from research.providers.base import QueryOutcome, RawResult

import streamlit as _REAL_STREAMLIT  # noqa: E402,F401  (see tests/test_theme_engine.py)

ORG = "Ooredoo Oman"
COUNTRY = "Oman"
NAME = "Layla Al-Example"
SPACED = "Layla Al Example"
SLUG = "layla-al-example-1a2b3c4d"
URL = f"https://om.linkedin.com/in/{SLUG}/?trk=public_profile#top"
CANON = f"https://www.linkedin.com/in/{SLUG}"
ROW = (f"https://om.linkedin.com/in/{SLUG}", f"{NAME} - Chief Technology and Information Officer - {ORG} | LinkedIn",
       f"Chief Technology and Information Officer at {ORG}. Muscat, Oman.")
MENTION = (f"{NAME} on her appointment as Chief Technology and Information Officer, and we are proud of the "
           "Omani leaders.")
PROBE_Q = f"site:linkedin.com/in/{SLUG}"
Q_HYPHEN = f'site:linkedin.com/in "{NAME}" "{ORG}"'
Q_SPACED = f'site:linkedin.com/in "{SPACED}" "{ORG}"'

TEXT_CONFIRMED = f"{NAME}\nChief Technology and Information Officer at {ORG}\nMuscat, Oman"
TEXT_UNVERIFIED = f"{NAME}\nChief Technology and Information Officer at Ooredoo\nOman"
TEXT_REJECTED = f"{NAME}\nChief Technology Officer at Omantel\nMuscat, Oman"


def _org():
    return ResolvedOrganization(name=ORG, country=COUNTRY, website=None, linkedin_company_url=None,
                                website_was_supplied=False, linkedin_was_supplied=False)


def host_row(slug="host-person-77", name="Host Person", snippet=MENTION):
    return (f"https://www.linkedin.com/in/{slug}", f"{name} - {ORG} | LinkedIn", snippet)


def filler(prefix, n=3):
    return [(f"https://www.linkedin.com/in/{prefix}-{k}", f"{prefix.title()} {k} - Network Engineer - {ORG} | LinkedIn",
             f"Network Engineer at {ORG}") for k in range(n)]


class FakeBrave:
    name = "brave"
    supports_pagination = True
    supports_rich_snippets = True

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, int]] = []

    def is_configured(self):
        return True

    def search(self, query, *, num_results=10, rich_snippets=False, page=0):
        self.calls.append((query, page))
        out = self.handler(query, page, len(self.calls))
        if isinstance(out, QueryOutcome):
            return out
        rows, more = out if isinstance(out, tuple) else (out, False)
        return QueryOutcome(query=query, provider=self.name, page=page, more_results_available=more,
                            results=[RawResult(url=u, title=t, snippet=s, query=query, provider=self.name, rank=i)
                                     for i, (u, t, s) in enumerate(rows)])


class SpyTavily:
    name = "tavily"

    def __init__(self):
        self.used = []

    def is_configured(self):
        self.used.append("is_configured")
        return True

    def search(self, query, *, num_results=10, **kw):
        self.used.append(query)
        return QueryOutcome(query=query, provider=self.name)


@pytest.fixture(autouse=True)
def _brave_only(monkeypatch):
    monkeypatch.delenv(PEOPLE_MODE_ENV, raising=False)


def handler_for(probe_rows=(), *, mention=True, name_rows=()):
    """Broad queries never return her; the slug probe returns `probe_rows`; name queries `name_rows`."""
    def handler(q, page, n):
        if q.startswith("site:linkedin.com/in/"):
            return list(probe_rows)
        if f'"{NAME}"' in q or f'"{SPACED}"' in q:
            return list(name_rows)
        if page == 0 and n <= 14 and mention:
            return [host_row(), *filler("generic", 2)]
        return filler("generic", 2)
    return handler


def run(handler, *, urls=(URL,), text=None, config=None, extra_providers=(), brave=None):
    brave = brave or FakeBrave(handler)
    result = discover_linkedin(_org(), [*extra_providers, brave], config=config,
                               known_profile_urls=list(urls), known_profile_text=text)
    return brave, result


def known(result):
    return result.family_outcome.coverage.metrics["known_profile_leads"]


def probes(brave):
    return [q for q, _ in brave.calls if q.startswith("site:linkedin.com/in/")]


def name_calls(brave):
    return [q for q, _ in brave.calls if f'"{NAME}"' in q or f'"{SPACED}"' in q]


def people(result):
    return assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)


def status_of(res, url):
    return ("confirmed" if any(p.linkedin_url == url for p in res.confirmed) else
            "unverified" if any(p.linkedin_url == url for p in res.unverified) else
            "rejected" if any(r["url"] == url for r in res.rejected) else "absent")


def nobody_named_layla(res):
    for p in [*res.confirmed, *res.unverified]:
        assert "layla" not in p.name.lower()
    assert all("layla" not in (r.get("name") or "").lower() for r in res.rejected)


# ---------------------------------------------------------------------------
# Defaults & no behaviour change without a known URL
# ---------------------------------------------------------------------------

def test_defaults_keep_the_budgets_and_cap_known_probes():
    cfg = PeopleDiscoveryConfig()
    assert (cfg.soft_budget, cfg.hard_ceiling) == (30, 45)
    assert cfg.known_profile_probe_max == 1 and cfg.known_profile_max == 3


def test_without_known_urls_the_request_sequence_is_unchanged():
    a = FakeBrave(handler_for())
    discover_linkedin(_org(), [a])
    b = FakeBrave(handler_for())
    discover_linkedin(_org(), [b], known_profile_urls=[], known_profile_text=None)
    assert a.calls == b.calls and not any(q.startswith("site:linkedin.com/in/") for q, _ in a.calls)
    assert discover_linkedin(_org(), [FakeBrave(handler_for())]).family_outcome.coverage.metrics["known_profile_leads"] == []


# ---------------------------------------------------------------------------
# URL-only: identity lead, never a person
# ---------------------------------------------------------------------------

def test_url_only_with_no_content_is_unresolved_and_creates_no_person_of_any_status():
    brave, result = run(handler_for())                       # probe returns nothing
    assert probes(brave) == [PROBE_Q]                        # exactly one probe, slug path, no query/fragment
    [lead] = known(result)
    assert lead["state"] == "unresolved_no_content" and lead["slug"] == SLUG and lead["requests"] == 1
    assert lead["canonical_url"] == CANON and lead["exact_match_returned"] is False
    assert all(SLUG not in s.url for s in result.profile_candidates)
    nobody_named_layla(people(result))


def test_the_url_itself_is_not_employment_evidence_when_the_probe_returns_unrelated_pages():
    other = ("https://www.linkedin.com/in/unrelated-person", f"Unrelated Person - Marketing Manager - {ORG} | LinkedIn",
             f"Marketing Manager at {ORG}")
    brave, result = run(handler_for([other]))
    [lead] = known(result)
    assert lead["state"] == "unresolved_no_content" and lead["exact_match_returned"] is False
    nobody_named_layla(people(result))


# ---------------------------------------------------------------------------
# Search resolution: exact slug only, then the UNCHANGED gate
# ---------------------------------------------------------------------------

def test_exact_slug_from_the_probe_resolves_via_search_and_is_assessed_normally():
    brave, result = run(handler_for([ROW]))
    [lead] = known(result)
    assert lead["state"] == "resolved_via_search" and lead["exact_match_returned"] is True and lead["requests"] == 1
    assert lead["resolved_by"] == "probe"
    res = people(result)
    assert [p.linkedin_url for p in res.confirmed if SLUG in p.linkedin_url] == [ROW[0]]
    hits = next(s for s in result.profile_candidates if SLUG in s.url).extra["discovery_hits"]
    assert {h["dimension"] for h in hits} == {"known_profile_probe"}


@pytest.mark.parametrize("title,snippet,expected", [
    (f"{NAME} - Chief Technology and Information Officer - {ORG} | LinkedIn",
     f"Chief Technology and Information Officer at {ORG}. Oman", "confirmed"),
    (f"{NAME} - Ooredoo", "Chief Technology and Information Officer at Ooredoo. Oman", "unverified"),
    (f"{NAME} - Ooredoo Group", "Chief Technology and Information Officer at Ooredoo Group. Qatar", "rejected"),
    (f"{NAME} - Chief Technology Officer - Omantel | LinkedIn", "Chief Technology Officer at Omantel", "rejected"),
])
def test_a_probe_resolved_profile_keeps_the_unchanged_employment_outcome(title, snippet, expected):
    row = (f"https://www.linkedin.com/in/{SLUG}", title, snippet)
    brave, result = run(handler_for([row]))
    assert known(result)[0]["state"] == "resolved_via_search"       # identity resolved regardless of employment
    assert status_of(people(result), row[0]) == expected


@pytest.mark.parametrize("wrong", [
    f"https://www.linkedin.com/in/{SLUG}-2",
    "https://www.linkedin.com/in/layla-al-example-1a2b3c4e",
    "https://www.linkedin.com/in/layla-al-example",
    f"https://www.linkedin.com/company/{SLUG}",
    f"https://example.com/in/{SLUG}",
])
def test_results_with_the_wrong_slug_never_resolve_the_lead(wrong):
    row = (wrong, f"{NAME} - Chief Technology and Information Officer - {ORG} | LinkedIn", f"Chief Technology and Information Officer at {ORG}")
    brave, result = run(handler_for([row]))
    [lead] = known(result)
    assert lead["state"] == "unresolved_no_content" and lead["exact_match_returned"] is False


def test_a_probe_error_is_an_unresolved_lead_without_retry_and_without_raw_error_text():
    def handler(q, page, n):
        if q.startswith("site:linkedin.com/in/"):
            return QueryOutcome(query=q, provider="brave", error="HTTP 401: SECRET-SENTINEL-TEXT")
        return [host_row(), *filler(f"g{n}", 2)]
    brave, result = run(handler)
    assert probes(brave) == [PROBE_Q]
    [lead] = known(result)
    assert lead["state"] == "unresolved_no_content" and lead["probe_failed"] is True
    assert "SECRET-SENTINEL-TEXT" not in json.dumps(known(result))


# ---------------------------------------------------------------------------
# Reuse of existing results at zero cost
# ---------------------------------------------------------------------------

def test_an_already_discovered_profile_is_reused_with_zero_extra_requests():
    def handler(q, page, n):
        return [ROW, host_row(), *filler(f"g{n}", 2)]
    base = FakeBrave(handler)
    discover_linkedin(_org(), [base])
    brave, result = run(handler)
    assert brave.calls == base.calls                                  # not one extra request
    [lead] = known(result)
    assert lead["state"] == "already_discovered" and lead["requests"] == 0
    assert sum(1 for p in people(result).confirmed if SLUG in p.linkedin_url) == 1


def test_already_discovered_beats_supplied_text_the_text_is_not_added():
    def handler(q, page, n):
        return [ROW, host_row(), *filler(f"g{n}", 2)]
    brave, result = run(handler, text={URL: TEXT_REJECTED})
    assert known(result)[0]["state"] == "already_discovered"
    assert all(s.extra.get("origin") != "user_supplied_text" for s in result.profile_candidates)
    assert sum(1 for p in people(result).confirmed if SLUG in p.linkedin_url) == 1


# ---------------------------------------------------------------------------
# Invalid, duplicate and over-cap URLs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "https://www.linkedin.com/company/ooredoo-oman",
    f"https://www.linkedin.com/in/{SLUG}/details/experience",
    "https://example.com/in/" + SLUG,
    "not a url",
    "",
])
def test_invalid_urls_make_no_request_and_no_person(bad):
    brave, result = run(handler_for(), urls=[bad])
    [lead] = known(result)
    assert lead["state"] == "invalid_url" and lead["requests"] == 0 and lead["slug"] is None
    assert probes(brave) == []
    nobody_named_layla(people(result))


def test_invalid_url_records_a_reason_code_but_not_the_raw_input_query_or_fragment():
    brave, result = run(handler_for(), urls=["https://example.com/in/x?token=SECRET-QUERY-VALUE#frag-secret"])
    dumped = json.dumps(known(result))
    assert "SECRET-QUERY-VALUE" not in dumped and "frag-secret" not in dumped and "token=" not in dumped
    assert known(result)[0]["reason"] == "host"


def test_the_same_profile_given_in_several_forms_costs_one_probe():
    brave, result = run(handler_for(), urls=[URL, CANON, CANON + "/", f"https://www.linkedin.com/in/{SLUG.upper()}?x=1"])
    assert probes(brave) == [PROBE_Q] and len(known(result)) == 1


def test_probes_are_capped_per_run_and_extra_urls_stay_unresolved_without_a_request():
    urls = [f"https://www.linkedin.com/in/person-{i}-0a1b2c3d" for i in range(5)]
    brave, result = run(handler_for(), urls=urls)
    assert len(probes(brave)) == PeopleDiscoveryConfig().known_profile_max == 3
    states = [k["state"] for k in known(result)]
    assert states == ["unresolved_no_content"] * 5
    assert [k["requests"] for k in known(result)] == [1, 1, 1, 0, 0]
    assert known(result)[3]["reason"] == "probe_cap"


# ---------------------------------------------------------------------------
# Supplied visible text -> existing gate; provenance labelled
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    (TEXT_CONFIRMED, "confirmed"),
    (TEXT_UNVERIFIED, "unverified"),
    (TEXT_REJECTED, "rejected"),
])
def test_supplied_text_passes_through_the_unchanged_association_gate(text, expected):
    brave, result = run(handler_for(), text={URL: text})
    [lead] = known(result)
    assert lead["state"] == "resolved_via_supplied_text" and lead["resolved_by"] == "supplied_text"
    assert status_of(people(result), CANON) == expected


def test_supplied_text_is_labelled_and_is_not_treated_as_independently_verified():
    brave, result = run(handler_for(), text={URL: TEXT_CONFIRMED})
    [src] = [s for s in result.profile_candidates if SLUG in s.url]
    assert src.extra["origin"] == "user_supplied_text" and src.extra["user_supplied_unverified"] is True
    assert src.url == CANON
    assert known(result)[0]["provenance"] == "user_supplied_text"
    [person] = [p for p in people(result).confirmed if SLUG in p.linkedin_url]
    assert person.discovery_providers == ["user_supplied_text"]


def test_probe_resolution_wins_over_supplied_text_and_the_text_is_not_used():
    brave, result = run(handler_for([ROW]), text={URL: TEXT_REJECTED})
    assert known(result)[0]["state"] == "resolved_via_search"
    assert all(s.extra.get("origin") != "user_supplied_text" for s in result.profile_candidates)
    assert status_of(people(result), ROW[0]) == "confirmed"


@pytest.mark.parametrize("text", ["", "   \n  ", None, "ok"])
def test_empty_or_unusable_supplied_text_is_url_only_and_creates_no_person(text):
    brave, result = run(handler_for(), text={URL: text})
    assert known(result)[0]["state"] == "unresolved_no_content"
    nobody_named_layla(people(result))


def test_text_supplied_for_a_different_or_invalid_url_is_ignored():
    other = "https://www.linkedin.com/in/someone-else-0a1b2c3d"
    brave, result = run(handler_for(), text={other: TEXT_CONFIRMED, "https://example.com/in/x": TEXT_CONFIRMED})
    assert known(result)[0]["state"] == "unresolved_no_content"
    nobody_named_layla(people(result))


def test_a_single_string_of_text_applies_only_when_exactly_one_url_is_known():
    brave, result = run(handler_for(), text=TEXT_CONFIRMED)
    assert known(result)[0]["state"] == "resolved_via_supplied_text"
    two = [URL, "https://www.linkedin.com/in/someone-else-0a1b2c3d"]
    brave2, result2 = run(handler_for(), urls=two, text=TEXT_CONFIRMED)
    assert all(k["state"] == "unresolved_no_content" for k in known(result2))


def test_full_supplied_text_never_appears_in_the_run_metrics_only_a_short_sanitised_excerpt():
    marker = "ZZ-UNIQUE-TAIL-MARKER-9917"
    text = TEXT_CONFIRMED + "\n" + ("filler sentence about nothing. " * 80) + marker + " api_key=SUPERSECRET123 https://x.example/p?trk=abc"
    brave, result = run(handler_for(), text={URL: text})
    dumped = json.dumps(result.family_outcome.coverage.metrics, default=str)
    assert marker not in dumped and "SUPERSECRET123" not in dumped and "trk=abc" not in dumped
    [lead] = known(result)
    assert 0 < len(lead["excerpt"]) <= 400 and lead["excerpt"].startswith(NAME)
    assert "text" not in lead and "supplied_text" not in lead


# ---------------------------------------------------------------------------
# Third-party mentions never establish identity or employment
# ---------------------------------------------------------------------------

def test_a_name_in_another_persons_snippet_never_resolves_the_lead_or_creates_a_person():
    brave, result = run(handler_for(mention=True))
    assert known(result)[0]["state"] == "unresolved_no_content"
    res = people(result)
    nobody_named_layla(res)
    host = [p for p in [*res.confirmed, *res.unverified] if p.linkedin_url.endswith("/host-person-77")]
    assert len(host) == 1 and host[0].seniority is None and host[0].role_label is None


# ---------------------------------------------------------------------------
# Probe BEFORE the name-variant follow-up; budgets; Tavily
# ---------------------------------------------------------------------------

def test_the_probe_runs_before_the_name_variant_follow_up():
    brave, result = run(handler_for())
    qs = [q for q, _ in brave.calls]
    assert probes(brave) == [PROBE_Q] and name_calls(brave) == [Q_HYPHEN, Q_SPACED]
    assert qs.index(PROBE_Q) < qs.index(Q_HYPHEN)


def test_an_exact_slug_resolution_skips_the_name_variant_queries_for_that_lead():
    brave, result = run(handler_for([ROW]))
    assert probes(brave) == [PROBE_Q] and name_calls(brave) == []
    assert EXISTING_LEAD_SKIPPED(result)


def EXISTING_LEAD_SKIPPED(result):
    leads = result.family_outcome.coverage.metrics["lead_followups"]
    return leads and all(l["status"] == "skipped_already_discovered" and l["requests"] == 0 for l in leads)


def test_supplied_text_resolution_also_skips_the_name_variant_queries():
    brave, result = run(handler_for(), text={URL: TEXT_CONFIRMED})
    assert probes(brave) == [PROBE_Q] and name_calls(brave) == []


def test_unresolved_url_still_allows_the_two_name_variants_when_budget_permits():
    brave, result = run(handler_for())
    assert len(name_calls(brave)) == 2 and len(probes(brave)) == 1


def test_probe_and_name_variants_are_counted_in_the_same_request_log_and_provider_total():
    brave, result = run(handler_for())
    metrics = result.family_outcome.coverage.metrics
    log = metrics["request_log"]
    assert [e["query"] for e in log if e["dimension"] == "known_profile_probe"] == [PROBE_Q]
    assert len(log) == len(brave.calls) == metrics["providers"]["brave"]["requests"]


@pytest.mark.parametrize("budget", list(range(6, 31)))
def test_known_probes_and_name_follow_ups_never_exceed_the_budget(budget):
    urls = [URL, "https://www.linkedin.com/in/person-b-0a1b2c3d", "https://www.linkedin.com/in/person-c-0a1b2c3d"]
    brave, result = run(handler_for(), urls=urls, config=PeopleDiscoveryConfig(soft_budget=budget, hard_ceiling=budget))
    assert len(brave.calls) <= budget
    assert result.family_outcome.coverage.metrics["providers"]["brave"]["requests"] == len(brave.calls)
    for k in known(result):
        assert k["requests"] <= 1
    assert all(l.get("requests", 0) <= 2 for l in result.family_outcome.coverage.metrics["lead_followups"])
    assert all(k["state"] in ("unresolved_no_content", "resolved_via_search", "already_discovered") for k in known(result))


def test_worst_case_stays_inside_the_unchanged_30_and_45_budgets():
    names = ["Sami El-Rashid", "Noor Al-Habib", "Omar Al-Qasim"]
    rows = [host_row(f"w{i}", f"Host {i}", f"{n} on his appointment as Chief Information Officer") for i, n in enumerate(names)]
    urls = [f"https://www.linkedin.com/in/person-{i}-0a1b2c3d" for i in range(6)]

    def handler(q, page, n):
        if q.startswith("site:linkedin.com/in/") or ('"' in q and "Chief" not in q and "Network" not in q and q.count('"') == 4):
            return []
        return ([*rows, *filler(f"g{n}", 1)], True)

    brave, result = run(handler, urls=urls)
    assert len(brave.calls) <= 45 and len(brave.calls) == result.family_outcome.coverage.metrics["providers"]["brave"]["requests"]
    assert len(probes(brave)) <= 3


def test_brave_only_never_calls_tavily_even_with_a_key_known_urls_and_follow_ups(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "TAVILY-SENTINEL-KEY")
    spy = SpyTavily()
    brave, result = run(handler_for([ROW]), extra_providers=[spy])
    assert spy.used == []
    metrics = result.family_outcome.coverage.metrics
    assert metrics["people_mode"] == "brave_only" and metrics["tavily_called"] is False
    assert known(result)[0]["state"] == "resolved_via_search"


def test_no_probe_when_the_provider_run_has_no_budget_left_for_it():
    brave, result = run(handler_for(), config=PeopleDiscoveryConfig(soft_budget=3, hard_ceiling=3))
    assert len(brave.calls) <= 3
    for k in known(result):
        assert k["state"] in ("unresolved_no_content", "already_discovered", "resolved_via_search")


def test_metrics_never_contain_the_raw_input_url_query_or_fragment():
    brave, result = run(handler_for())
    dumped = json.dumps(known(result))
    assert "trk=" not in dumped and "#top" not in dumped and "om.linkedin.com" not in dumped

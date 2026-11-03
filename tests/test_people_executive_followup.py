"""People discovery -- exact-phrase executive queries and capped LEAD follow-up.

Situation covered: the broad executive queries do not return an executive's
own profile, but another person's profile carries an appointment snippet
that names the executive. That name is a discovery LEAD:

  * a lead only triggers one profile-restricted search for that person at the
    resolved organisation (`site:linkedin.com/in "<Name>" "<Org>"`);
  * a lead never creates a person record and never establishes employment;
  * whatever profile comes back goes through the unchanged association gate;
  * leads are de-duplicated, capped (`lead_followup_max`, default 2) and
    counted in the SAME request budget (soft 30 / hard ceiling 45).

Synthetic data only; a fictional executive; no network, no keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from commercial.people import assess_people
from research import linkedin as li
from research.linkedin import (
    PEOPLE_MODE_ENV,
    PEOPLE_QUERY_BUDGET,
    PeopleDiscoveryConfig,
    build_lead_query,
    discover_linkedin,
    query_families,
)
from research.organization import ResolvedOrganization
from research.people_leads import extract_leads
from research.providers.base import QueryOutcome, RawResult

import streamlit as _REAL_STREAMLIT  # noqa: E402,F401  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
ORG = "Ooredoo Oman"
COUNTRY = "Oman"
NAME = "Layla Al-Example"
SPACED = "Layla Al Example"                  # the other spelling of the same name
EXEC_SLUG = "layla-al-example-1a2b3c4d"
EXEC_URL = f"https://om.linkedin.com/in/{EXEC_SLUG}"
EXEC_ROW = (EXEC_URL, "Layla Al-Example - Chief Technology and Information Officer - Ooredoo Oman | LinkedIn",
            "Chief Technology and Information Officer at Ooredoo Oman. Muscat, Oman.")
MENTION = (f"{NAME} on her appointment as Chief Technology and Information Officer, and we are proud of the "
           "Omani leaders. Ooredoo Oman launched SD-WAN with a partner.")


def host_row(slug="host-person-77", name="Host Person", snippet=MENTION):
    return (f"https://www.linkedin.com/in/{slug}", f"{name} - Ooredoo Oman | LinkedIn", snippet)


def _org():
    return ResolvedOrganization(name=ORG, country=COUNTRY, website=None, linkedin_company_url=None,
                                website_was_supplied=False, linkedin_was_supplied=False)


def _filler(prefix, n=3):
    return [(f"https://www.linkedin.com/in/{prefix}-{k}", f"{prefix.title()} {k} - Network Engineer - Ooredoo Oman | LinkedIn",
             "Network Engineer at Ooredoo Oman") for k in range(n)]


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
        self.used: list[str] = []

    def is_configured(self):
        self.used.append("is_configured")
        return True

    def search(self, query, *, num_results=10, **kw):
        self.used.append(query)
        return QueryOutcome(query=query, provider=self.name)


@pytest.fixture(autouse=True)
def _brave_only(monkeypatch):
    monkeypatch.delenv(PEOPLE_MODE_ENV, raising=False)


def _is_lead_query(q: str) -> bool:
    return q.startswith('site:linkedin.com/in "') and '" "Ooredoo Oman"' in q and "(" not in q


def _broad_fails_handler(lead_rows):
    """Broad queries (org-wide, executive titles, families) never return the executive;
    they DO return a host profile whose snippet mentions her. Only an exact-name query
    returns `lead_rows`."""
    def handler(q, page, n):
        if f'"{NAME}"' in q or f'"{SPACED}"' in q:
            return list(lead_rows)
        if page == 0 and n <= 14:
            return [host_row(), *_filler("generic", 2)]
        return _filler("generic", 2)
    return handler


def _leads(result):
    return result.family_outcome.coverage.metrics["lead_followups"]


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

def test_lead_query_is_profile_restricted_exact_name_plus_resolved_organisation():
    assert build_lead_query(li.DIALECT_RESTRICTED, ORG, NAME) == f'site:linkedin.com/in "{NAME}" "{ORG}"'
    plain = build_lead_query(li.DIALECT_PLAIN, ORG, NAME)
    assert plain == f"{NAME} {ORG} linkedin" and '"' not in plain and "site:" not in plain


def test_executive_floor_includes_single_exact_title_phrases_not_only_or_groups():
    fam = next(f for f in query_families() if f["id"] == "technology_leadership")
    n = fam["executive_terms"]
    exact = [t for t in fam["terms"][:n] if " OR " not in t]
    assert n == 4 and len(exact) == 2
    assert all(t.startswith('"') and t.endswith('"') for t in exact)
    assert {'"Chief Information Officer"', '"Chief Technology Officer"'} == set(exact)


def test_exact_phrase_executive_queries_run_even_when_generic_queries_yield_nothing_new():
    static = _filler("same", 5)
    brave = FakeBrave(lambda q, page, n: list(static))
    result = discover_linkedin(_org(), [brave])
    queries = [q for q, _ in brave.calls]
    for phrase in ('"Chief Information Officer"', '"Chief Technology Officer"'):
        assert any(q == f'site:linkedin.com/in "{ORG}" {phrase}' for q in queries), phrase
    assert len(brave.calls) <= PEOPLE_QUERY_BUDGET


# ---------------------------------------------------------------------------
# 1. Found by an exact-name query after the broad executive queries failed
# ---------------------------------------------------------------------------

def test_executive_is_found_by_exact_name_query_after_broad_queries_fail():
    brave = FakeBrave(_broad_fails_handler([EXEC_ROW]))
    result = discover_linkedin(_org(), [brave])
    lead_calls = [q for q, _ in brave.calls if f'"{NAME}"' in q]
    assert lead_calls == [f'site:linkedin.com/in "{NAME}" "{ORG}"']
    assert EXEC_URL in {s.url for s in result.profile_candidates}
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    assert [p.linkedin_url for p in people.confirmed if EXEC_SLUG in p.linkedin_url] == [EXEC_URL]
    hits = next(s for s in result.profile_candidates if s.url == EXEC_URL).extra["discovery_hits"]
    assert {h["dimension"] for h in hits} == {"lead_followup"}
    [lead] = _leads(result)
    assert lead["name"] == NAME and lead["status"] == "queried" and lead["profiles_returned"] == 1
    assert lead["source_profile_url"].endswith("/host-person-77")


def test_control_without_lead_followup_the_executive_is_not_found():
    brave = FakeBrave(_broad_fails_handler([EXEC_ROW]))
    result = discover_linkedin(_org(), [brave], config=PeopleDiscoveryConfig(lead_followup_max=0))
    assert EXEC_URL not in {s.url for s in result.profile_candidates}
    assert not [q for q, _ in brave.calls if f'"{NAME}"' in q]


# ---------------------------------------------------------------------------
# 2. A mention triggers a search but never creates or confirms anyone
# ---------------------------------------------------------------------------

def test_mention_alone_creates_no_person_when_the_profile_is_not_returned():
    brave = FakeBrave(_broad_fails_handler([]))                       # exact-name search finds nothing
    result = discover_linkedin(_org(), [brave])
    assert [q for q, _ in brave.calls if f'"{NAME}"' in q]            # the lead WAS searched
    assert all(EXEC_SLUG not in s.url for s in result.profile_candidates)
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    everyone = [*people.confirmed, *people.unverified]
    assert all("layla" not in p.name.lower() for p in everyone)
    assert all("layla" not in (r.get("name") or "").lower() for r in people.rejected)
    [lead] = _leads(result)
    assert lead["status"] == "queried" and lead["profiles_returned"] == 0


def test_the_host_profile_is_not_turned_into_the_executive():
    brave = FakeBrave(_broad_fails_handler([]))
    result = discover_linkedin(_org(), [brave])
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    host = [p for p in [*people.confirmed, *people.unverified] if p.linkedin_url.endswith("/host-person-77")]
    assert len(host) == 1 and host[0].name.startswith("Host Person")
    assert host[0].seniority is None and host[0].role_label is None


def test_a_different_person_returned_by_the_lead_query_is_not_credited_as_the_lead():
    other = ("https://www.linkedin.com/in/unrelated-person", "Unrelated Person - Marketing Manager - Ooredoo Oman | LinkedIn",
             "Marketing Manager at Ooredoo Oman")
    result = discover_linkedin(_org(), [FakeBrave(_broad_fails_handler([other]))])
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    assert all("layla" not in p.name.lower() for p in [*people.confirmed, *people.unverified])


# ---------------------------------------------------------------------------
# 3 + 4. The discovered profile passes the UNCHANGED association gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,snippet,expected", [
    ("Layla Al-Example - Chief Technology and Information Officer - Ooredoo Oman | LinkedIn",
     "Chief Technology and Information Officer at Ooredoo Oman. Oman", "confirmed"),
    ("Layla Al-Example - Ooredoo", "Chief Technology and Information Officer at Ooredoo. Oman", "unverified"),
    ("Layla Al-Example - Ooredoo Group", "Chief Technology and Information Officer at Ooredoo Group. Qatar", "rejected"),
    ("Layla Al-Example - Chief Technology Officer - Omantel | LinkedIn", "Chief Technology Officer at Omantel", "rejected"),
])
def test_follow_up_profile_goes_through_the_existing_association_gate(title, snippet, expected):
    row = (f"https://www.linkedin.com/in/{EXEC_SLUG}", title, snippet)
    result = discover_linkedin(_org(), [FakeBrave(_broad_fails_handler([row]))])
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    url = row[0]
    status = ("confirmed" if any(p.linkedin_url == url for p in people.confirmed) else
              "unverified" if any(p.linkedin_url == url for p in people.unverified) else
              "rejected" if any(r["url"] == url for r in people.rejected) else "absent")
    assert status == expected


# ---------------------------------------------------------------------------
# 5. Duplicate leads / repeated URLs
# ---------------------------------------------------------------------------

def test_the_same_lead_mentioned_many_times_and_in_name_variants_costs_one_request():
    variants = [
        host_row("h1", "Host One", MENTION),
        host_row("h2", "Host Two", MENTION.replace(NAME, "Dr. Layla Al Example")),
        host_row("h3", "Host Three", "Congratulations to Layla Al-Example on her appointment as Chief Technology Officer."),
        host_row("h4", "Host Four", f"Warm congratulations {NAME} on your new role as Chief Information Officer"),
    ]

    def handler(q, page, n):
        if "Example" in q and "Chief" not in q:
            return [EXEC_ROW]
        return [*variants, *_filler(f"g{n}", 1)]

    brave = FakeBrave(handler)
    result = discover_linkedin(_org(), [brave])
    lead_calls = [q for q, _ in brave.calls if _is_lead_query(q)]
    assert len(lead_calls) == 1
    assert len(_leads(result)) == 1
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    assert sum(1 for p in people.confirmed if EXEC_SLUG in p.linkedin_url) == 1


def test_repeated_profile_urls_across_hosts_and_queries_stay_one_person():
    www = (f"https://www.linkedin.com/in/{EXEC_SLUG}", EXEC_ROW[1], EXEC_ROW[2])

    def handler(q, page, n):
        if f'"{NAME}"' in q:
            return [EXEC_ROW, www]
        return [host_row(), *_filler("generic", 2)]

    result = discover_linkedin(_org(), [FakeBrave(handler)])
    assert len([s for s in result.profile_candidates if EXEC_SLUG in s.url]) == 1
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    assert sum(1 for p in people.confirmed if EXEC_SLUG in p.linkedin_url) == 1


def test_no_lead_request_when_the_persons_own_profile_was_already_discovered():
    def handler(q, page, n):
        return [EXEC_ROW, host_row(), *_filler("generic", 2)]

    brave = FakeBrave(handler)
    result = discover_linkedin(_org(), [brave])
    assert [q for q, _ in brave.calls if _is_lead_query(q)] == []
    assert _leads(result)[0]["status"] == "skipped_already_discovered"


def test_a_lead_is_never_queried_twice_across_runs_of_the_same_provider_plan():
    brave = FakeBrave(_broad_fails_handler([EXEC_ROW]))
    discover_linkedin(_org(), [brave])
    assert len({q for q, _ in brave.calls if f'"{NAME}"' in q}) == 1


# ---------------------------------------------------------------------------
# 6. Budgets
# ---------------------------------------------------------------------------

def _many_leads_handler(k=6):
    rows = [host_row(f"h{i}", f"Host {i}", f"Sami Example{chr(97 + i)} on his appointment as Chief Information Officer")
            for i in range(k)]
    return lambda q, page, n: [*rows, *_filler(f"g{n}", 1)]


def test_lead_followups_are_capped():
    brave = FakeBrave(_many_leads_handler())
    result = discover_linkedin(_org(), [brave])
    # two LEADS are searched (cap), each with at most its two spellings => at most 4 requests
    assert PeopleDiscoveryConfig().lead_followup_max == 2
    assert len([q for q, _ in brave.calls if _is_lead_query(q)]) <= 2 * PeopleDiscoveryConfig().lead_variant_max
    assert len(_leads(result)) >= 3                                    # more leads existed than were searched
    assert sum(1 for lead in _leads(result) if lead["status"] == "queried") == 2


def test_cap_is_configurable_and_zero_disables_follow_up():
    brave = FakeBrave(_many_leads_handler())
    discover_linkedin(_org(), [brave], config=PeopleDiscoveryConfig(lead_followup_max=0))
    assert [q for q, _ in brave.calls if _is_lead_query(q)] == []


def test_all_variants_pagination_and_followups_stay_within_the_hard_ceiling():
    counter = {"n": 0}

    def handler(q, page, n):
        counter["n"] += 1
        host = [host_row(f"hh{counter['n']}-{i}", f"Host {counter['n']} {i}",
                         f"Sami Name{chr(97 + (counter['n'] + i) % 20)}{chr(97 + i)} on his appointment as Chief Technology Officer")
                for i in range(3)]
        return ([*host, *_filler(f"p{counter['n']}", 8)], True)

    brave = FakeBrave(handler)
    result = discover_linkedin(_org(), [brave])
    assert len(brave.calls) <= 45
    assert result.family_outcome.coverage.metrics["providers"]["brave"]["requests"] == len(brave.calls)
    assert len(_leads(result)) >= 1 and sum(1 for lead in _leads(result) if lead["status"] == "queried") <= 2


def test_followups_never_push_a_low_budget_run_past_its_budget():
    brave = FakeBrave(_many_leads_handler())
    discover_linkedin(_org(), [brave], config=PeopleDiscoveryConfig(soft_budget=13, hard_ceiling=13))
    assert len(brave.calls) <= 13


def test_followups_are_part_of_the_request_log_and_the_provider_total():
    brave = FakeBrave(_broad_fails_handler([EXEC_ROW]))
    result = discover_linkedin(_org(), [brave])
    log = result.family_outcome.coverage.metrics["request_log"]
    assert any(e["dimension"] == "lead_followup" and f'"{NAME}"' in e["query"] for e in log)
    assert len(log) == len(brave.calls) == result.family_outcome.coverage.metrics["providers"]["brave"]["requests"]


# ---------------------------------------------------------------------------
# 7. No personal name hard-coded
# ---------------------------------------------------------------------------

def test_no_personal_name_is_hard_coded_in_production_code_or_taxonomy():
    paths = [ROOT / "commercial" / "people_taxonomy.json", ROOT / "research" / "linkedin.py",
             ROOT / "research" / "people_leads.py", *sorted((ROOT / "commercial").glob("*.py"))]
    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for name in ("jihad", "ansari", "hadhrami", "layla", "al-example"):
            assert name not in text, f"{name!r} found in {path.name}"


# ---------------------------------------------------------------------------
# 8. Brave-only never calls Tavily, even with a Tavily key configured
# ---------------------------------------------------------------------------

def test_brave_only_never_calls_tavily_even_with_a_key_and_follow_ups(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "TAVILY-SENTINEL-KEY")
    spy = SpyTavily()
    brave = FakeBrave(_broad_fails_handler([EXEC_ROW]))
    result = discover_linkedin(_org(), [spy, brave])
    assert spy.used == []
    metrics = result.family_outcome.coverage.metrics
    assert metrics["people_mode"] == "brave_only" and metrics["tavily_called"] is False
    assert EXEC_URL in {s.url for s in result.profile_candidates}


# ---------------------------------------------------------------------------
# Lead extraction: credible appointment / leadership announcements only
# ---------------------------------------------------------------------------

def _names(text, **kw):
    return [lead.name for lead in extract_leads(text, organization=ORG, **kw)]


@pytest.mark.parametrize("text,name", [
    (MENTION, NAME),
    ("Congratulations to Sami Rashid on his appointment as Chief Information Officer.", "Sami Rashid"),
    ("We are pleased to announce the appointment of Dr. Noor Habib as Chief Technology Officer.", "Noor Habib"),
    ("Omar bin Saleed Qasim has been appointed as Head of IT.", "Omar bin Saleed Qasim"),
    ("Welcome Rania Farouk as our new Chief Information Security Officer", "Rania Farouk"),
    ("Hearty congratulations Tariq Nasser on your new role as Chief Digital Officer!", "Tariq Nasser"),
])
def test_credible_appointment_announcements_become_leads(text, name):
    assert _names(text) == [name]


@pytest.mark.parametrize("text", [
    "We are hiring a Chief Technology Officer. Apply now.",                                  # vacancy
    "Sami Rashid on his appointment as Chief Financial Officer",                              # not a technology leadership title
    "Sami Rashid on his appointment as Marketing Manager",                                    # not senior
    "Congratulations on the new role as Chief Information Officer!",                          # no name
    "Ooredoo Oman on its appointment as Chief Technology Officer partner",                    # organisation, not a person
    "sami rashid on his appointment as Chief Information Officer",                            # not a proper name
    "Looking for a Chief Information Officer? Sami Rashid is hiring one, join our team.",     # hiring ad
    "Thank you to everyone for the support",                                                  # nothing
    "",
])
def test_non_announcements_do_not_become_leads(text):
    assert _names(text) == []


def test_a_persons_own_snippet_is_not_a_lead_about_someone_else():
    assert _names(MENTION, own_name=NAME) == []


def test_leads_carry_a_title_phrase_and_never_an_employment_claim():
    [lead] = extract_leads(MENTION, organization=ORG)
    assert lead.title_phrase == "Chief Technology and Information Officer"
    assert not hasattr(lead, "organization_association") and not hasattr(lead, "status")


# ===========================================================================
# Name-variant follow-up: 'Al-Name' and 'Al Name' are the same person
# ===========================================================================
#
# Brave OR-groups proved unreliable in a saved run (results contained none of the
# alternatives), so the two spellings are two SEPARATE queries -- never one OR query.
# Both count against the existing People budget; no more than two requests per lead.

from research.people_leads import name_spellings  # noqa: E402

Q_HYPHEN = f'site:linkedin.com/in "{NAME}" "{ORG}"'
Q_SPACED = f'site:linkedin.com/in "{SPACED}" "{ORG}"'


def _lead_calls(brave):
    return [q for q, _ in brave.calls if _is_lead_query(q)]


def _by_spelling(hyphen_rows=(), spaced_rows=()):
    """Broad queries never find her; each spelling returns its own rows."""
    def handler(q, page, n):
        if f'"{NAME}"' in q:
            return list(hyphen_rows)
        if f'"{SPACED}"' in q:
            return list(spaced_rows)
        if page == 0 and n <= 14:
            return [host_row(), *_filler("generic", 2)]
        return _filler("generic", 2)
    return handler


@pytest.mark.parametrize("name,expected", [
    ("Layla Al-Example", ["Layla Al-Example", "Layla Al Example"]),
    ("Layla Al Example", ["Layla Al Example", "Layla Al-Example"]),
    ("Sami El-Rashid", ["Sami El-Rashid", "Sami El Rashid"]),
    ("Sami El Rashid", ["Sami El Rashid", "Sami El-Rashid"]),
    ("Omar Abu-Bakr", ["Omar Abu-Bakr", "Omar Abu Bakr"]),
    ("Omar bin Saleed Qasim", ["Omar bin Saleed Qasim"]),
    ("Sami Rashid", ["Sami Rashid"]),
    ("Mary-Anne Smith", ["Mary-Anne Smith"]),                       # ordinary hyphens are left alone
])
def test_name_spellings_cover_hyphen_and_space_forms_as_written_first(name, expected):
    assert name_spellings(name) == expected


@pytest.mark.parametrize("name", ["Layla Al-Example", "Layla Al Example", "Abu Al-Hassan Al-Example", "A B", "", "  "])
def test_name_spellings_never_exceed_two_and_never_repeat(name):
    out = name_spellings(name)
    assert len(out) <= 2 and len({v.lower() for v in out}) == len(out)
    if name.strip():
        assert out and out[0] == " ".join(name.split())


def test_name_spellings_introduce_no_name_the_text_did_not_contain():
    from research.people_leads import name_tokens
    for name in ("Layla Al-Example", "Sami El Rashid", "Omar Abu-Bakr"):
        assert {tuple(name_tokens(v)) for v in name_spellings(name)} == {tuple(name_tokens(name))}


def test_no_or_syntax_is_used_for_name_variants():
    for q in (Q_HYPHEN, Q_SPACED):
        assert " OR " not in q and "(" not in q
    plain = build_lead_query(li.DIALECT_PLAIN, ORG, SPACED)
    assert plain == f"{SPACED} {ORG} linkedin"


def test_defaults_keep_the_budgets_and_cap_variants_at_two():
    cfg = PeopleDiscoveryConfig()
    assert cfg.lead_variant_max == 2 and cfg.lead_followup_max == 2
    assert (cfg.soft_budget, cfg.hard_ceiling) == (30, 45) == (PEOPLE_QUERY_BUDGET, 45)


def test_first_spelling_that_returns_the_profile_stops_the_lead_after_one_request():
    brave = FakeBrave(_by_spelling(hyphen_rows=[EXEC_ROW]))
    result = discover_linkedin(_org(), [brave])
    assert _lead_calls(brave) == [Q_HYPHEN]
    [lead] = _leads(result)
    assert lead["requests"] == 1 and lead["stop_reason"] == "profile_found" and lead["status"] == "queried"
    assert lead["name_variants"] == [NAME, SPACED] and lead["queries"] == [Q_HYPHEN] and lead["query"] == Q_HYPHEN


def test_profile_only_returned_for_the_other_spelling_is_found_by_the_second_query():
    brave = FakeBrave(_by_spelling(hyphen_rows=[], spaced_rows=[EXEC_ROW]))
    result = discover_linkedin(_org(), [brave])
    assert _lead_calls(brave) == [Q_HYPHEN, Q_SPACED]
    assert EXEC_URL in {s.url for s in result.profile_candidates}
    [lead] = _leads(result)
    assert lead["requests"] == 2 and lead["stop_reason"] == "profile_found" and lead["queries"] == [Q_HYPHEN, Q_SPACED]
    assert lead["profiles_returned"] == 1


def test_spaced_form_in_the_snippet_tries_the_spaced_spelling_first():
    mention = MENTION.replace(NAME, SPACED)
    brave = FakeBrave(lambda q, page, n: _by_spelling(spaced_rows=[EXEC_ROW])(q, page, n)
                      if _is_lead_query(q) else [host_row(snippet=mention), *_filler(f"g{n}", 2)])
    result = discover_linkedin(_org(), [brave])
    assert _lead_calls(brave) == [Q_SPACED]
    assert EXEC_URL in {s.url for s in result.profile_candidates}


def test_zero_results_for_both_spellings_makes_exactly_two_requests_and_creates_nobody():
    brave = FakeBrave(_by_spelling())
    result = discover_linkedin(_org(), [brave])
    assert _lead_calls(brave) == [Q_HYPHEN, Q_SPACED]                  # never a third request for this lead
    [lead] = _leads(result)
    assert lead["requests"] == 2 and lead["profiles_returned"] == 0
    assert lead["stop_reason"] == "variants_exhausted" and lead["status"] == "queried"
    assert all(EXEC_SLUG not in s.url for s in result.profile_candidates)
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    for p in [*people.confirmed, *people.unverified]:
        assert "layla" not in p.name.lower()
    assert all("layla" not in (r.get("name") or "").lower() for r in people.rejected)


def test_other_peoples_profiles_from_a_variant_query_do_not_stop_the_lead_or_become_her():
    other = ("https://www.linkedin.com/in/unrelated-person", "Unrelated Person - Marketing Manager - Ooredoo Oman | LinkedIn",
             "Marketing Manager at Ooredoo Oman")
    brave = FakeBrave(_by_spelling(hyphen_rows=[other], spaced_rows=[EXEC_ROW]))
    result = discover_linkedin(_org(), [brave])
    assert _lead_calls(brave) == [Q_HYPHEN, Q_SPACED]                  # an unrelated profile is not 'found'
    assert EXEC_URL in {s.url for s in result.profile_candidates}
    [lead] = _leads(result)
    assert lead["profiles_returned"] == 2


def test_a_lead_never_uses_more_than_two_requests_even_with_many_possible_spellings():
    mention = MENTION.replace(NAME, "Abu Al-Hassan Al-Example")
    brave = FakeBrave(lambda q, page, n: [] if _is_lead_query(q) else [host_row(snippet=mention), *_filler(f"g{n}", 2)])
    result = discover_linkedin(_org(), [brave])
    assert 1 <= len(_lead_calls(brave)) <= 2
    assert all(lead["requests"] <= 2 for lead in _leads(result))


def test_variant_limit_is_configurable_and_one_means_the_as_written_spelling_only():
    brave = FakeBrave(_by_spelling(spaced_rows=[EXEC_ROW]))
    result = discover_linkedin(_org(), [brave], config=PeopleDiscoveryConfig(lead_variant_max=1))
    assert _lead_calls(brave) == [Q_HYPHEN]
    assert EXEC_URL not in {s.url for s in result.profile_candidates}


def test_both_variant_queries_are_in_the_request_log_and_the_provider_total():
    brave = FakeBrave(_by_spelling())
    result = discover_linkedin(_org(), [brave])
    metrics = result.family_outcome.coverage.metrics
    logged = [e["query"] for e in metrics["request_log"] if e["dimension"] == "lead_followup"]
    assert logged == [Q_HYPHEN, Q_SPACED]
    assert len(metrics["request_log"]) == len(brave.calls) == metrics["providers"]["brave"]["requests"]


# -- duplicate results ------------------------------------------------------

def test_same_profile_from_both_spellings_hosts_case_and_trailing_slash_is_one_person():
    upper = (f"https://om.linkedin.com/in/{EXEC_SLUG.upper()}/", EXEC_ROW[1], EXEC_ROW[2])
    www = (f"https://www.linkedin.com/in/{EXEC_SLUG}", EXEC_ROW[1], EXEC_ROW[2])
    other = ("https://www.linkedin.com/in/unrelated-person", "Unrelated Person - Marketing Manager - Ooredoo Oman | LinkedIn",
             "Marketing Manager at Ooredoo Oman")
    # first spelling: an unrelated profile + her under a different host/case; second: her again + the unrelated one again
    brave = FakeBrave(_by_spelling(hyphen_rows=[other, upper], spaced_rows=[www, EXEC_ROW, other]))
    result = discover_linkedin(_org(), [brave])
    assert len([s for s in result.profile_candidates if EXEC_SLUG in s.url.lower()]) == 1
    assert len([s for s in result.profile_candidates if "unrelated-person" in s.url]) == 1
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    assert sum(1 for p in people.confirmed if EXEC_SLUG in p.linkedin_url.lower()) == 1


def test_repeated_results_across_spellings_are_not_double_counted_as_new_people():
    same = [("https://www.linkedin.com/in/dup-person", "Dup Person - Network Engineer - Ooredoo Oman | LinkedIn",
             "Network Engineer at Ooredoo Oman")]
    brave = FakeBrave(_by_spelling(hyphen_rows=same, spaced_rows=same))
    result = discover_linkedin(_org(), [brave])
    assert len([s for s in result.profile_candidates if "dup-person" in s.url]) == 1


# -- budget ----------------------------------------------------------------

def test_worst_case_follow_up_is_four_requests_inside_the_unchanged_budgets():
    names = ["Sami El-Rashid", "Noor Al-Habib", "Omar Al-Qasim"]
    rows = [host_row(f"w{i}", f"Host {i}", f"{n} on his appointment as Chief Information Officer")
            for i, n in enumerate(names)]
    brave = FakeBrave(lambda q, page, n: [] if _is_lead_query(q) else [*rows, *_filler(f"g{n}", 1)])
    result = discover_linkedin(_org(), [brave])
    assert len(_lead_calls(brave)) == 4                                 # 2 leads x 2 spellings, third lead not searched
    assert len(brave.calls) <= 30
    assert sum(1 for lead in _leads(result) if lead["status"] == "queried") == 2
    assert [lead["status"] for lead in _leads(result)].count("pending") == 1


@pytest.mark.parametrize("budget", list(range(8, 22)))
def test_variant_requests_never_exceed_the_budget_or_two_per_lead(budget):
    names = ["Sami El-Rashid", "Noor Al-Habib"]
    rows = [host_row(f"b{i}", f"Host {i}", f"{n} on his appointment as Chief Information Officer")
            for i, n in enumerate(names)]
    brave = FakeBrave(lambda q, page, n: [] if _is_lead_query(q) else [*rows, *_filler(f"g{n}", 1)])
    result = discover_linkedin(_org(), [brave], config=PeopleDiscoveryConfig(soft_budget=budget, hard_ceiling=budget))
    assert len(brave.calls) <= budget
    assert result.family_outcome.coverage.metrics["providers"]["brave"]["requests"] == len(brave.calls)
    assert all(lead.get("requests", 0) <= 2 for lead in _leads(result))
    assert all(lead["status"] != "in_progress" for lead in _leads(result))


def test_budget_exhausted_between_the_two_spellings_stops_the_second_one():
    name = "Sami El-Rashid"
    rows = [host_row("one", "Host One", f"{name} on his appointment as Chief Information Officer")]

    def run(budget):
        brave = FakeBrave(lambda q, page, n: [] if _is_lead_query(q) else [*rows, *_filler(f"g{n}", 1)])
        result = discover_linkedin(_org(), [brave], config=PeopleDiscoveryConfig(soft_budget=budget, hard_ceiling=budget))
        return brave, result

    cut = None
    for budget in range(5, 31):
        brave, result = run(budget)
        leads = _leads(result)
        if leads and leads[0].get("requests") == 1 and len(brave.calls) == budget:
            cut = (budget, leads[0])
            break
    assert cut is not None, "no budget produced a lead cut off after its first spelling"
    budget, lead = cut
    assert lead["stop_reason"] == "budget" and lead["status"] == "queried"
    assert lead["queries"] == [f'site:linkedin.com/in "{name}" "{ORG}"']
    assert f'site:linkedin.com/in "Sami El Rashid" "{ORG}"' not in [q for q, _ in run(budget)[0].calls]


# -- unchanged association gate for a profile found by the second spelling ---

@pytest.mark.parametrize("title,snippet,expected", [
    ("Layla Al-Example - Chief Technology and Information Officer - Ooredoo Oman | LinkedIn",
     "Chief Technology and Information Officer at Ooredoo Oman. Oman", "confirmed"),
    ("Layla Al-Example - Ooredoo", "Chief Technology and Information Officer at Ooredoo. Oman", "unverified"),
    ("Layla Al-Example - Ooredoo Group", "Chief Technology and Information Officer at Ooredoo Group. Qatar", "rejected"),
    ("Layla Al-Example - Chief Technology Officer - Omantel | LinkedIn", "Chief Technology Officer at Omantel", "rejected"),
])
def test_profile_found_by_the_second_spelling_goes_through_the_unchanged_association_gate(title, snippet, expected):
    row = (f"https://www.linkedin.com/in/{EXEC_SLUG}", title, snippet)
    result = discover_linkedin(_org(), [FakeBrave(_by_spelling(spaced_rows=[row]))])
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    url = row[0]
    status = ("confirmed" if any(p.linkedin_url == url for p in people.confirmed) else
              "unverified" if any(p.linkedin_url == url for p in people.unverified) else
              "rejected" if any(r["url"] == url for r in people.rejected) else "absent")
    assert status == expected


def test_a_name_in_another_persons_snippet_never_creates_a_person_or_employment():
    result = discover_linkedin(_org(), [FakeBrave(_by_spelling())])
    assert all(EXEC_SLUG not in s.url for s in result.profile_candidates)
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    host = [p for p in [*people.confirmed, *people.unverified] if p.linkedin_url.endswith("/host-person-77")]
    assert len(host) == 1 and host[0].name.startswith("Host Person") and host[0].seniority is None


def test_an_error_on_the_first_spelling_stops_that_lead_without_a_second_request():
    def handler(q, page, n):
        if _is_lead_query(q):
            return QueryOutcome(query=q, provider="brave", error="HTTP 503: SECRET-SENTINEL-TEXT")
        return [host_row(), *_filler(f"g{n}", 2)]

    brave = FakeBrave(handler)
    result = discover_linkedin(_org(), [brave])
    assert _lead_calls(brave) == [Q_HYPHEN]
    [lead] = _leads(result)
    assert lead["requests"] == 1 and lead["stop_reason"] == "error" and lead["status"] == "queried"


def test_variant_follow_up_never_calls_tavily_in_brave_only_mode(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "TAVILY-SENTINEL-KEY")
    spy = SpyTavily()
    brave = FakeBrave(_by_spelling(spaced_rows=[EXEC_ROW]))
    result = discover_linkedin(_org(), [spy, brave])
    assert spy.used == [] and _lead_calls(brave) == [Q_HYPHEN, Q_SPACED]
    assert result.family_outcome.coverage.metrics["tavily_called"] is False

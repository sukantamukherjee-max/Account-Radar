"""People discovery -- executive title queries (Brave).

Root cause being covered (from the Ooredoo Oman funnel run): the executive
vocabulary (CIO/CTO/...) was never queried because the adaptive scheduler
starved the technology-leadership family after one zero-yield generic query
(`technology` added no NEW people, so the family scored 0 and was never
picked again before the budget ran out).

The fix under test:
  A. two exact-title executive query groups at the FRONT of the
     technology_leadership family, always run in the floor
     (`executive_terms: 2`), even when the generic queries yield nothing new;
  B. `site:linkedin.com/in` stays the default; a plain-wording fallback is
     used ONLY when an executive query returns zero LinkedIn profile
     results, at most `executive_fallback_max` (2) times per run, and every
     request counts against the same soft budget (30) / hard ceiling (45).

Everything is synthetic: in-memory fakes, a fictional executive, no network,
no keys. Association / attribution / classification rules are NOT changed
and are re-asserted here through the real pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from commercial.people import assess_people
from research import linkedin as li
from research.linkedin import (
    DIALECT_PLAIN,
    DIALECT_RESTRICTED,
    PEOPLE_MODE_ENV,
    PEOPLE_QUERY_BUDGET,
    PeopleDiscoveryConfig,
    build_people_query,
    discover_linkedin,
    query_families,
)
from research.organization import ResolvedOrganization
from research.providers.base import QueryOutcome, RawResult

import streamlit as _REAL_STREAMLIT  # noqa: E402,F401  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
ORG = "Ooredoo Oman"
COUNTRY = "Oman"
EXEC_SLUG = "layla-al-example-1a2b3c4d"        # fictional executive
EXEC_URL_OM = f"https://om.linkedin.com/in/{EXEC_SLUG}"
EXEC_URL_WWW = f"https://www.linkedin.com/in/{EXEC_SLUG}"
EXEC_ROW = (EXEC_URL_OM, "Layla Al-Example - Chief Technology and Information Officer - Ooredoo Oman | LinkedIn",
            "Chief Technology and Information Officer at Ooredoo Oman. Muscat, Oman.")
EXEC_ROW_WWW = (EXEC_URL_WWW, "Layla Al-Example - Chief Technology and Information Officer - Ooredoo Oman | LinkedIn",
                "Layla Al-Example. Chief Technology and Information Officer at Ooredoo Oman.")
HOST_PROFILE = ("https://www.linkedin.com/in/host-person-77", "Host Person - Ooredoo Oman | LinkedIn",
                "Layla Al-Example on her appointment as Chief Technology and Information Officer, "
                "and we are proud of the Omani leaders. Marketing at Ooredoo Oman.")


def _org():
    return ResolvedOrganization(name=ORG, country=COUNTRY, website=None, linkedin_company_url=None,
                                website_was_supplied=False, linkedin_was_supplied=False)


def _filler(prefix, n=5):
    return [(f"https://www.linkedin.com/in/{prefix}-{k}", f"{prefix.title()} {k} - Network Engineer - Ooredoo Oman | LinkedIn",
             "Network Engineer at Ooredoo Oman") for k in range(n)]


def _floor() -> int:
    """org-wide search + first term of every family + the family's remaining executive title queries"""
    families = query_families()
    return 1 + len(families) + sum(max(0, int(f.get("executive_terms") or 0) - 1) for f in families)


def _is_exec_query(q: str) -> bool:
    return "Chief Technology Officer" in q or "CITO" in q or "Chief Information" in q


class FakeBrave:
    """name == 'brave'; `handler(query, page, n)` -> rows or (rows, more)."""

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
        rows, more = out if isinstance(out, tuple) else (out, False)
        return QueryOutcome(query=query, provider=self.name, page=page, more_results_available=more,
                            results=[RawResult(url=u, title=t, snippet=s, query=query, provider=self.name, rank=i)
                                     for i, (u, t, s) in enumerate(rows)])


@pytest.fixture(autouse=True)
def _brave_only(monkeypatch):
    monkeypatch.delenv(PEOPLE_MODE_ENV, raising=False)


def _static_unless_exec(exec_rows):
    """Generic queries always return the SAME people (zero new yield after the
    first), exactly the situation that starved the family before."""
    static = _filler("generic", 6)

    def handler(q, page, n):
        if _is_exec_query(q):
            return list(exec_rows)
        return list(static)
    return handler


def _exec_queries(brave):
    return [(q, p) for q, p in brave.calls if _is_exec_query(q)]


def _old_strategy(monkeypatch):
    """The query plan as it was before the fix: no executive groups, floor of 1."""
    families = query_families()
    old = []
    for f in families:
        f = dict(f)
        n = int(f.pop("executive_terms", 0) or 0)
        f["terms"] = f["terms"][n:]
        old.append(f)
    monkeypatch.setattr(li, "query_families", lambda: old)


# ---------------------------------------------------------------------------
# Taxonomy / query shape (A)
# ---------------------------------------------------------------------------

def test_executive_groups_lead_the_technology_leadership_family():
    fam = next(f for f in query_families() if f["id"] == "technology_leadership")
    assert fam["executive_terms"] == 4
    first, second = fam["terms"][:2]
    assert first.startswith("(") and " OR " in first and '"Chief Technology Officer"' in first
    assert '"Chief Information Officer"' in first and "CTO" in first and "CIO" in first
    assert "CITO" in second and '"Chief Technology and Information Officer"' in second
    assert '"Chief Information & Technology Officer"' in second
    # the pre-existing generic terms are still there, after the executive groups
    assert {"technology", "information technology", "IT director", "head of IT"} <= set(fam["terms"][4:])


def test_no_person_name_is_hard_coded_anywhere_in_production_discovery():
    sources = [ROOT / "commercial" / "people_taxonomy.json", ROOT / "research" / "linkedin.py"]
    sources += sorted((ROOT / "commercial").glob("*.py"))
    for path in sources:
        text = path.read_text(encoding="utf-8").lower()
        for name in ("jihad", "ansari", "hadhrami"):
            assert name not in text, f"{name!r} must not appear in {path.name}"


def test_restricted_query_keeps_site_prefix_and_is_within_brave_limits():
    for term in query_families()[0]["terms"][:2]:
        q = build_people_query(DIALECT_RESTRICTED, ORG, term, COUNTRY)
        assert q.startswith("site:linkedin.com/in " + f'"{ORG}"')
        assert len(q) <= 400 and len(q.split()) <= 50


def test_plain_wording_of_an_executive_group_has_no_operators():
    for term in query_families()[0]["terms"][:2]:
        q = build_people_query(DIALECT_PLAIN, ORG, term, COUNTRY)
        assert q.startswith(ORG) and q.endswith("linkedin")
        assert '"' not in q and "(" not in q and " OR " not in q and "site:" not in q
        assert "Chief" in q


def test_budgets_are_unchanged():
    cfg = PeopleDiscoveryConfig()
    assert PEOPLE_QUERY_BUDGET == 30 and cfg.soft_budget == 30 and cfg.hard_ceiling == 45
    assert cfg.executive_fallback_max == 2


# ---------------------------------------------------------------------------
# 1. A candidate's own profile is discoverable through the strategy (+ control)
# ---------------------------------------------------------------------------

def test_executives_own_profile_is_discovered_and_confirmed():
    brave = FakeBrave(_static_unless_exec([EXEC_ROW]))
    result = discover_linkedin(_org(), [brave])
    urls = {s.url for s in result.profile_candidates}
    assert EXEC_URL_OM in urls
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    mine = [p for p in people.confirmed if p.linkedin_url == EXEC_URL_OM]
    assert len(mine) == 1 and mine[0].organization_association == "confirmed"
    found_by = next(s for s in result.profile_candidates if s.url == EXEC_URL_OM).extra["discovery_hits"]
    assert all(h["dimension"] == "family:technology_leadership" and _is_exec_query(h["query"]) for h in found_by)


def test_control_without_the_executive_queries_the_profile_is_never_found(monkeypatch):
    _old_strategy(monkeypatch)
    brave = FakeBrave(_static_unless_exec([EXEC_ROW]))
    result = discover_linkedin(_org(), [brave])
    assert EXEC_URL_OM not in {s.url for s in result.profile_candidates}
    assert _exec_queries(brave) == []


def test_executive_queries_run_in_the_floor_even_when_generic_queries_have_zero_yield():
    brave = FakeBrave(_static_unless_exec([]))               # exec queries return nothing too
    result = discover_linkedin(_org(), [brave])
    floor = _floor()
    log = result.family_outcome.coverage.metrics["request_log"]
    first_floor = [e["query"] for e in log[:floor + 4]]
    tl = [q for q in first_floor if q.startswith('site:linkedin.com/in "Ooredoo Oman" (')]
    assert len(tl) == 2
    assert any('"Chief Technology Officer"' in q for q in tl) and any("CITO" in q for q in tl)
    assert all(e["dimension"] == "family:technology_leadership" for e in log if e["query"] in tl)


def test_generic_terms_of_the_family_are_still_reachable_after_the_executive_groups():
    fam = next(f for f in query_families() if f["id"] == "technology_leadership")
    brave = FakeBrave(lambda q, page, n: _filler(f"p{n}", 5) if "technology" in q.lower() or "CIO" in q else _filler(f"q{n}", 1))
    discover_linkedin(_org(), [brave])
    queried = " ".join(q for q, _ in brave.calls)
    assert fam["terms"][4] in queried or fam["terms"][5] in queried


# ---------------------------------------------------------------------------
# 2. Third-party mentions never create or substitute for a profile
# ---------------------------------------------------------------------------

def test_a_third_party_mention_does_not_create_or_substitute_for_the_candidates_profile():
    def handler(q, page, n):
        if _is_exec_query(q):
            return [HOST_PROFILE]                              # only someone ELSE's profile mentions her
        return _filler("generic", 3)

    brave = FakeBrave(handler)
    result = discover_linkedin(_org(), [brave])
    assert all(EXEC_SLUG not in s.url for s in result.profile_candidates)
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    everyone = [*people.confirmed, *people.unverified]
    assert all("layla" not in p.name.lower() and EXEC_SLUG not in p.linkedin_url for p in everyone)
    assert all("layla" not in (r.get("name") or "").lower() and EXEC_SLUG not in r["url"] for r in people.rejected)
    # the mention stays attached to the HOST profile only
    host = [s for s in result.profile_candidates if s.url == HOST_PROFILE[0]]
    assert len(host) == 1 and "Layla" in host[0].snippet


def test_a_mention_alone_never_confirms_the_hosts_profile_as_the_executive():
    def handler(q, page, n):
        return [HOST_PROFILE] if _is_exec_query(q) else []
    result = discover_linkedin(_org(), [FakeBrave(handler)])
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    for p in [*people.confirmed, *people.unverified]:
        assert p.linkedin_url == HOST_PROFILE[0] and p.name.startswith("Host Person")


# ---------------------------------------------------------------------------
# 3. Deduplication and association rules stay intact
# ---------------------------------------------------------------------------

def test_same_profile_on_several_hosts_and_queries_is_one_candidate_with_all_hits():
    def handler(q, page, n):
        if "CITO" in q:
            return [EXEC_ROW_WWW]
        if "Chief Technology Officer" in q:
            return [EXEC_ROW]
        return _filler("generic", 2)

    result = discover_linkedin(_org(), [FakeBrave(handler)])
    mine = [s for s in result.profile_candidates if EXEC_SLUG in s.url]
    assert len(mine) == 1
    assert len({h["query"] for h in mine[0].extra["discovery_hits"]}) >= 2
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    assert sum(1 for p in people.confirmed if EXEC_SLUG in p.linkedin_url) == 1


def test_bare_brand_and_sibling_executives_keep_their_unverified_and_rejected_status():
    bare = ("https://www.linkedin.com/in/bare-brand-exec", "Sam Bare - Ooredoo",
            "Chief Information Officer at Ooredoo. Oman")
    sibling = ("https://www.linkedin.com/in/sibling-exec", "Sia Sibling - Ooredoo Group",
               "Chief Technology Officer at Ooredoo Group. Qatar")
    other = ("https://www.linkedin.com/in/other-exec", "Omar Other - Chief Technology Officer - Omantel | LinkedIn",
             "Chief Technology Officer at Omantel")

    def handler(q, page, n):
        return [EXEC_ROW, bare, sibling, other] if _is_exec_query(q) else _filler("generic", 2)

    result = discover_linkedin(_org(), [FakeBrave(handler)])
    people = assess_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    assert {p.linkedin_url for p in people.confirmed} >= {EXEC_URL_OM}
    assert "bare-brand-exec" in " ".join(p.linkedin_url for p in people.unverified)
    rejected_urls = {r["url"] for r in people.rejected}
    assert sibling[0] in rejected_urls and other[0] in rejected_urls
    assert not {bare[0], sibling[0], other[0]} & {p.linkedin_url for p in people.confirmed}


# ---------------------------------------------------------------------------
# B. Executive plain-wording fallback: zero LinkedIn profiles only, cap 2
# ---------------------------------------------------------------------------

def _plain_exec_calls(brave):
    return [q for q, _ in brave.calls if _is_exec_query(q) and "site:" not in q]


def test_zero_profile_executive_queries_fall_back_to_plain_wording_at_most_twice():
    brave = FakeBrave(lambda q, page, n: [] if _is_exec_query(q) else _filler(f"g{n}", 3))
    result = discover_linkedin(_org(), [brave])
    plain = _plain_exec_calls(brave)
    assert 1 <= len(plain) <= 2 == PeopleDiscoveryConfig().executive_fallback_max
    for q in plain:
        assert '"' not in q and "(" not in q and " OR " not in q
    log = result.family_outcome.coverage.metrics["request_log"]
    assert sum(1 for e in log if e["query"] in plain) == len(plain)       # recorded in the same log
    assert result.family_outcome.coverage.metrics["providers"]["brave"]["requests"] == len(brave.calls)


def test_fallback_can_find_the_profile_the_restricted_query_missed():
    def handler(q, page, n):
        if _is_exec_query(q):
            return [EXEC_ROW] if "site:" not in q else []
        return _filler(f"g{n}", 3)
    result = discover_linkedin(_org(), [FakeBrave(handler)])
    assert EXEC_URL_OM in {s.url for s in result.profile_candidates}


def test_executive_query_returning_only_non_profile_pages_counts_as_zero_profiles():
    news = [("https://example.com/news/appointment", "Appointment news", "Ooredoo Oman appoints a Chief Technology Officer")]
    brave = FakeBrave(lambda q, page, n: news if _is_exec_query(q) and "site:" in q else _filler(f"g{n}", 3))
    discover_linkedin(_org(), [brave])
    assert len(_plain_exec_calls(brave)) >= 1


def test_no_fallback_when_the_executive_query_returned_profiles():
    brave = FakeBrave(lambda q, page, n: [EXEC_ROW] if _is_exec_query(q) else _filler(f"g{n}", 3))
    discover_linkedin(_org(), [brave])
    assert _plain_exec_calls(brave) == []


def test_fallback_is_limited_to_executive_queries_once_restricted_wording_is_proven():
    # generic queries productive -> restricted wording proven; only exec queries are empty
    brave = FakeBrave(lambda q, page, n: [] if _is_exec_query(q) else _filler(f"g{n}", 4))
    discover_linkedin(_org(), [brave])
    plain = [q for q, _ in brave.calls if "site:" not in q]
    assert len(plain) <= 2 and all(_is_exec_query(q) for q in plain)


def test_fallback_cap_is_configurable_and_zero_disables_it():
    brave = FakeBrave(lambda q, page, n: [] if _is_exec_query(q) else _filler(f"g{n}", 3))
    discover_linkedin(_org(), [brave], config=PeopleDiscoveryConfig(executive_fallback_max=0))
    assert _plain_exec_calls(brave) == []


# ---------------------------------------------------------------------------
# Budget ceilings
# ---------------------------------------------------------------------------

def test_hard_ceiling_is_never_exceeded_when_everything_is_productive():
    brave = FakeBrave(lambda q, page, n: (_filler(f"p{n}", 8), True))
    discover_linkedin(_org(), [brave])
    assert len(brave.calls) <= 45


def test_hard_ceiling_is_never_exceeded_when_everything_is_empty():
    brave = FakeBrave(lambda q, page, n: [])
    result = discover_linkedin(_org(), [brave])
    assert len(brave.calls) <= 45
    assert result.family_outcome.coverage.metrics["providers"]["brave"]["requests"] == len(brave.calls)


def test_soft_budget_holds_when_yield_is_low():
    counter = {"n": 0}

    def handler(q, page, n):                                   # ~1 new person per request, then dries up
        counter["n"] += 1
        return (_filler(f"s{counter['n']}", 1), True)

    brave = FakeBrave(handler)
    cfg = PeopleDiscoveryConfig(extension_min_weighted_per_request=5.0, saturation_window=1000)
    discover_linkedin(_org(), [brave], config=cfg)
    assert len(brave.calls) <= PEOPLE_QUERY_BUDGET


def test_executive_fallbacks_count_against_the_soft_budget():
    brave = FakeBrave(lambda q, page, n: [])
    cfg = PeopleDiscoveryConfig(soft_budget=12, hard_ceiling=12)
    discover_linkedin(_org(), [brave], config=cfg)
    assert len(brave.calls) <= 12


def test_floor_is_the_old_floor_plus_the_extra_executive_title_queries_and_far_below_the_soft_budget():
    floor = _floor()
    brave = FakeBrave(lambda q, page, n: _filler("same", 1))
    discover_linkedin(_org(), [brave])
    assert len(brave.calls) == floor <= PEOPLE_QUERY_BUDGET

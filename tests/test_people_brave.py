"""People Intelligence -- Brave optimisation (Brave-only build phase).

Covers: the authoritative People provider mode (brave_only never touches
Tavily, even when TAVILY_API_KEY is configured; brave_tavily kept for the
future), Serper as a controlled fallback, profile-restricted Brave queries
with a plain-wording fallback, taxonomy query families, adaptive pagination,
discovery yield, the adaptive search budget (no cap on people), dedup and
provenance across pages/queries, stricter current-employer association,
telecom / Gulf-title classification, and the sales-priority tier (kept
separate from relevance_score).

No network and no API keys: providers are in-memory fakes and the
organization is a generic placeholder.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from commercial.people import (
    GROUP_CYBERSECURITY,
    GROUP_DIGITAL_DATA,
    GROUP_INFRA_CLOUD,
    GROUP_OTHER,
    GROUP_PROCUREMENT,
    GROUP_TECH_LEADERSHIP,
    assess_organization_association,
    build_people,
    classify_person_text,
    people_groups,
    sales_priority,
    score_person,
)
from commercial.people_taxonomy import get_taxonomy
from research import linkedin as li
from research.linkedin import (
    PEOPLE_MODE_ENV,
    PeopleDiscoveryConfig,
    discover_linkedin,
    people_mode,
    person_yield_weight,
    query_families,
)
from research.organization import ResolvedOrganization
from research.providers import brave as brave_module
from research.providers.base import QueryOutcome, RawResult
from research.providers.brave import BraveSearchProvider

import streamlit as _REAL_STREAMLIT  # noqa: E402  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
ORG = "Meridian Ports Authority"
COUNTRY = "Nordholm"
RESTRICTED_PREFIX = "site:linkedin.com/in"


# ---------------------------------------------------------------------------
# Fakes and helpers
# ---------------------------------------------------------------------------

def _org():
    return ResolvedOrganization(name=ORG, country=COUNTRY, website="https://www.example-meridian.test",
                                linkedin_company_url=None, website_was_supplied=False, linkedin_was_supplied=False)


def _p(slug, headline="Network Engineer", *, assoc=True, host="www", snippet=None):
    company = ORG if assoc else "Other Holding Company"
    title = f"{slug.replace('-', ' ').title()} - {headline} - {company} | LinkedIn"
    return (f"https://{host}.linkedin.com/in/{slug}", title, snippet if snippet is not None else headline)


def _term_of(query: str) -> str | None:
    """The family term of a People query in either wording (None = organisation-wide search)."""
    if query.startswith(RESTRICTED_PREFIX):
        rest = query[len(RESTRICTED_PREFIX):].strip()
        rest = rest.replace(f'"{ORG}"', "", 1).strip()
        return rest or None
    rest = query.replace(ORG, "", 1).strip()
    rest = re.sub(r"\blinkedin\b", "", rest).replace(COUNTRY, "").strip()
    return rest or None


class FakeBrave:
    """Brave-like fake: paginates, supports rich snippets, records every call.
    `results(term, page, query)` -> (rows, more_results_available)."""

    name = "brave"
    supports_pagination = True
    supports_rich_snippets = True

    def __init__(self, results=None, *, configured=True, error=None):
        self._results = results or (lambda term, page, query: ([], False))
        self._configured = configured
        self._error = error
        self.calls: list[tuple[str, int]] = []

    def is_configured(self):
        return self._configured

    def search(self, query, *, num_results=10, rich_snippets=False, page=0):
        self.calls.append((query, page))
        if self._error:
            return QueryOutcome(query=query, provider=self.name, error=self._error, page=page)
        rows, more = self._results(_term_of(query), page, query)
        return QueryOutcome(query=query, provider=self.name, page=page, more_results_available=more, results=[
            RawResult(url=u, title=t, snippet=s, query=query, provider=self.name, rank=i)
            for i, (u, t, s) in enumerate(rows)])


class FakePlain:
    """Provider WITHOUT pagination / rich-snippet support (search() would raise
    TypeError if either were passed)."""

    def __init__(self, name, results=None, *, configured=True, error=None):
        self.name = name
        self._results = results or (lambda term, n: [])
        self._configured = configured
        self._error = error
        self.calls: list[str] = []

    def is_configured(self):
        return self._configured

    def search(self, query, *, num_results=10):
        self.calls.append(query)
        if self._error:
            return QueryOutcome(query=query, provider=self.name, error=self._error)
        rows = self._results(_term_of(query), len(self.calls))
        return QueryOutcome(query=query, provider=self.name, results=[
            RawResult(url=u, title=t, snippet=s, query=query, provider=self.name, rank=i)
            for i, (u, t, s) in enumerate(rows)])


class SpyTavily:
    """Records ANY use. In brave_only mode nothing may be called on it."""

    name = "tavily"

    def __init__(self):
        self.used: list[str] = []

    def is_configured(self):
        self.used.append("is_configured")
        return True

    def search(self, query, *, num_results=10):
        self.used.append(query)
        return QueryOutcome(query=query, provider=self.name)


def _new_each_call(prefix, per_call=1, headline="Network Engineer"):
    counter = {"n": 0}

    def results(term, page, query=None):
        counter["n"] += 1
        n = counter["n"]
        return [_p(f"{prefix}-{n}-{k}", headline) for k in range(per_call)], True
    return results


def _metrics(result):
    return result.family_outcome.coverage.metrics


@pytest.fixture(autouse=True)
def _brave_only(monkeypatch):
    monkeypatch.delenv(PEOPLE_MODE_ENV, raising=False)


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(brave_module.time, "sleep", lambda s: slept.append(s))
    return slept


FAMILIES = query_families()
# Executive title groups (a family's first `executive_terms` terms) always run in the floor.
EXEC_FLOOR_EXTRA = sum(max(0, int(f.get("executive_terms") or 0) - 1) for f in FAMILIES)
EXEC_GROUPS = {t for f in FAMILIES for t in f["terms"][:int(f.get("executive_terms") or 0)]}
FLOOR = 1 + len(FAMILIES) + EXEC_FLOOR_EXTRA  # organisation-wide search + first term of every family + extra executive groups


# ---------------------------------------------------------------------------
# 1. Brave provider: pagination, more-results flag, extra snippets, rate limit
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.text, self.headers = status, body, "", headers or {}

    def json(self):
        return self._body


class _Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params))
        return self.responses.pop(0)


def _body(more=None, n=1):
    body = {"web": {"results": [{"url": f"https://www.linkedin.com/in/x{i}", "title": "X", "description": "Main",
                                 "extra_snippets": ["Extra"]} for i in range(n)]}}
    if more is not None:
        body["query"] = {"more_results_available": more}
    return body


def _brave(*responses):
    session = _Session(*responses)
    return BraveSearchProvider(api_key="brave-test-key-not-real", session=session, backoff_base=0.0), session


def test_brave_normal_search_parameters_are_unchanged(no_sleep):
    brave, session = _brave(_Resp(200, _body()))
    brave.search("q", num_results=5)
    assert session.calls[0] == {"q": "q", "count": 5}


def test_brave_page_sends_offset_only_when_paging(no_sleep):
    brave, session = _brave(_Resp(200, _body()), _Resp(200, _body()))
    assert brave.supports_pagination
    first = brave.search("q", num_results=20, page=0)
    second = brave.search("q", num_results=20, page=2)
    assert "offset" not in session.calls[0] and session.calls[1]["offset"] == 2
    assert first.page == 0 and second.page == 2


def test_brave_page_is_clamped_to_the_documented_maximum(no_sleep):
    brave, session = _brave(_Resp(200, _body()))
    brave.search("q", num_results=20, page=15)
    assert session.calls[0]["offset"] == 9


@pytest.mark.parametrize("flag", [True, False, None])
def test_brave_more_results_available_is_parsed(flag, no_sleep):
    brave, _ = _brave(_Resp(200, _body(more=flag)))
    assert brave.search("q").more_results_available is flag


def test_brave_extra_snippets_rejection_is_remembered(no_sleep):
    brave, session = _brave(_Resp(422, {}), _Resp(200, _body()), _Resp(200, _body()))
    assert brave.search("q1", rich_snippets=True).ok
    assert brave.search("q2", rich_snippets=True).ok
    assert len(session.calls) == 3  # one rejected request in total, never repeated
    assert "extra_snippets" in session.calls[0]
    assert "extra_snippets" not in session.calls[1] and "extra_snippets" not in session.calls[2]


def test_brave_rate_limit_headers_pause_the_next_request(no_sleep):
    limited = {"X-RateLimit-Remaining": "0, 900", "X-RateLimit-Reset": "1, 100000"}
    brave, session = _brave(_Resp(200, _body(), headers=limited), _Resp(200, _body()))
    brave.search("q1")
    brave.search("q2")
    assert len(session.calls) == 2
    assert no_sleep and 0 < max(no_sleep) <= 1.0


def test_people_classes_still_satisfy_the_provider_contract():
    from research.providers.base import SearchProvider
    assert isinstance(BraveSearchProvider(api_key="brave-test-key-not-real"), SearchProvider)


# ---------------------------------------------------------------------------
# 2. Provider mode: brave_only is authoritative; Serper controlled fallback
# ---------------------------------------------------------------------------

def test_default_people_mode_is_brave_only():
    assert people_mode() == "brave_only"
    assert PeopleDiscoveryConfig().mode == "brave_only"


def test_invalid_people_mode_falls_back_to_brave_only(monkeypatch):
    monkeypatch.setenv(PEOPLE_MODE_ENV, "everything")
    assert people_mode() == "brave_only"


def test_brave_only_never_calls_tavily_even_when_it_is_configured(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-test-key-not-real")
    tavily = SpyTavily()
    brave = FakeBrave(_new_each_call("b"))
    result = discover_linkedin(_org(), [tavily, brave])
    assert tavily.used == []
    assert brave.calls
    m = _metrics(result)
    assert m["people_mode"] == "brave_only" and m["tavily_called"] is False
    assert "tavily" not in m["providers"]


def test_brave_only_with_thin_brave_still_never_calls_tavily(monkeypatch):
    tavily, serper = SpyTavily(), FakePlain("serper")
    discover_linkedin(_org(), [tavily, FakeBrave(), serper])
    assert tavily.used == [] and serper.calls  # thin -> Serper, never Tavily


def test_brave_tavily_mode_keeps_tavily_first_for_the_future(monkeypatch):
    monkeypatch.setenv(PEOPLE_MODE_ENV, "brave_tavily")
    tavily = FakePlain("tavily", lambda term, n: [_p(f"t-{n}", "IT Manager")])
    brave = FakeBrave(_new_each_call("b"))
    result = discover_linkedin(_org(), [brave, tavily])
    assert tavily.calls and brave.calls
    m = _metrics(result)
    assert m["people_mode"] == "brave_tavily" and m["tavily_called"] is True
    assert list(m["providers"])[:2] == ["tavily", "brave"]


def test_serper_is_not_called_when_brave_is_healthy():
    serper = FakePlain("serper")
    discover_linkedin(_org(), [FakeBrave(_new_each_call("b", per_call=2)), serper])
    assert serper.calls == []


def test_serper_is_called_when_brave_fails():
    brave = FakeBrave(error="HTTP 500")
    serper = FakePlain("serper", lambda term, n: [_p(f"s-{n}", "CIO")])
    result = discover_linkedin(_org(), [brave, serper])
    assert len(brave.calls) == PeopleDiscoveryConfig().error_circuit  # circuit breaker
    assert serper.calls and len(result.profile_candidates) > 0
    assert "provider errors" in _metrics(result)["providers"]["brave"]["stop_reason"]
    assert result.family_outcome.fallback_triggered


def test_serper_is_called_when_brave_discovery_is_thin():
    brave = FakeBrave(lambda term, page, q: ([_p("only-one", "CIO")], False))
    serper = FakePlain("serper", lambda term, n: [_p(f"s-{n}", "IT Manager")])
    discover_linkedin(_org(), [brave, serper])
    assert serper.calls


def test_serper_is_called_when_brave_is_not_configured():
    serper = FakePlain("serper", lambda term, n: [_p(f"s-{n}", "IT Manager")])
    result = discover_linkedin(_org(), [FakeBrave(configured=False), serper])
    assert serper.calls
    assert _metrics(result)["providers"]["brave"]["stop_reason"] == "not configured"


def test_serper_fallback_has_its_own_small_budget():
    serper = FakePlain("serper", lambda term, n: [_p(f"s-{n}-{k}", "IT Manager") for k in range(5)])
    discover_linkedin(_org(), [FakeBrave(configured=False), serper])
    assert len(serper.calls) <= PeopleDiscoveryConfig().serper_max_requests


# ---------------------------------------------------------------------------
# 3. Query wording and families
# ---------------------------------------------------------------------------

def test_brave_queries_are_restricted_to_profile_pages():
    brave = FakeBrave(_new_each_call("b"))
    discover_linkedin(_org(), [brave])
    assert brave.calls[0] == (f'{RESTRICTED_PREFIX} "{ORG}"', 0)
    for query, _ in brave.calls:
        assert query.startswith(RESTRICTED_PREFIX) and f'"{ORG}"' in query
        # boolean OR only inside the executive title groups
        assert " OR " not in query or any(query.endswith(group) for group in EXEC_GROUPS)


def test_other_providers_use_plain_wording_without_operators():
    serper = FakePlain("serper", lambda term, n: [_p(f"s-{n}", "IT Manager")])
    discover_linkedin(_org(), [FakeBrave(configured=False), serper])
    for query in serper.calls:
        assert "site:" not in query and '"' not in query and " OR " not in query and ORG in query


def test_restricted_zero_results_retry_plain_and_switch_when_plain_works():
    def results(term, page, query):
        if query.startswith(RESTRICTED_PREFIX):
            return [], False
        return [_p(f"plain-{abs(hash(query)) % 10**8}-{page}", "IT Manager")], False
    brave = FakeBrave(results)
    result = discover_linkedin(_org(), [brave])
    queries = [q for q, _ in brave.calls]
    assert queries[0].startswith(RESTRICTED_PREFIX) and not queries[1].startswith(RESTRICTED_PREFIX)
    switch_at = PeopleDiscoveryConfig().restricted_zero_switch * 2
    assert all(not q.startswith(RESTRICTED_PREFIX) for q in queries[switch_at:])
    assert _metrics(result)["providers"]["brave"]["dialect"] == "plain"


def test_plain_retries_stop_when_the_account_has_no_profiles_at_all():
    brave = FakeBrave()
    result = discover_linkedin(_org(), [brave])
    # Plain retries: the organisation-wide search and the first executive group share the
    # generic retries (restricted_zero_switch); the second executive group has its own
    # capped executive fallback (+1). Nothing beyond that.
    assert len(brave.calls) == FLOOR + PeopleDiscoveryConfig().restricted_zero_switch + 1
    assert _metrics(result)["providers"]["brave"]["stop_reason"] == "saturated"


def test_query_families_come_from_the_taxonomy():
    tax_families = get_taxonomy()["people_discovery"]["query_families"]
    assert [f["id"] for f in FAMILIES] == [f["id"] for f in tax_families]
    ids = {f["id"] for f in FAMILIES}
    assert {"technology_leadership", "infrastructure_cloud", "telecom_technology", "cybersecurity",
            "digital_data", "applications_architecture", "procurement"} <= ids
    for f in FAMILIES:
        assert f["terms"] and 0 < f["weight"] <= 1.0
    telecom = next(f for f in FAMILIES if f["id"] == "telecom_technology")
    assert {"RAN", "OSS BSS", "core network"} <= set(telecom["terms"])


def test_no_generic_business_title_queries():
    banned = {"manager", "director", "finance manager", "finance director", "hr manager", "head"}
    for f in FAMILIES:
        for term in f["terms"]:
            assert term.lower() not in banned


def test_floor_covers_every_family_even_when_results_are_zero():
    brave = FakeBrave()
    discover_linkedin(_org(), [brave])
    terms = {_term_of(q) for q, _ in brave.calls}
    assert None in terms  # organisation-wide search
    for f in FAMILIES:
        assert f["terms"][0] in terms


# ---------------------------------------------------------------------------
# 4. Pagination
# ---------------------------------------------------------------------------

def _org_pages(per_page: dict, *, more=True, assoc=True, headline="Cloud Engineer"):
    """Organisation-wide search returns per_page[page] profiles; family queries return nothing."""
    def results(term, page, query):
        if term is None:
            n = per_page.get(page, 0)
            return [_p(f"org-{page}-{k}", headline, assoc=assoc) for k in range(n)], more
        return [], False
    return results


def _org_pages_requested(brave):
    return [page for q, page in brave.calls if _term_of(q) is None]


def test_org_page2_when_page1_has_three_new_associated_people():
    brave = FakeBrave(_org_pages({0: 3}))
    discover_linkedin(_org(), [brave])
    assert _org_pages_requested(brave)[:2] == [0, 1]


def test_org_page2_when_page1_has_five_profiles_even_if_not_associated():
    brave = FakeBrave(_org_pages({0: 5}, assoc=False))
    discover_linkedin(_org(), [brave])
    assert _org_pages_requested(brave)[:2] == [0, 1]


def test_no_org_page2_when_page1_is_weak():
    brave = FakeBrave(_org_pages({0: 2}))
    discover_linkedin(_org(), [brave])
    assert _org_pages_requested(brave) == [0]


def test_org_deeper_pages_continue_only_while_productive():
    brave = FakeBrave(_org_pages({0: 4, 1: 4, 2: 4, 3: 1, 4: 9}))
    discover_linkedin(_org(), [brave])
    assert _org_pages_requested(brave) == [0, 1, 2, 3]


def test_org_paging_stops_when_brave_reports_no_more_results():
    brave = FakeBrave(_org_pages({0: 9, 1: 9}, more=False))
    discover_linkedin(_org(), [brave])
    assert _org_pages_requested(brave) == [0]


def test_org_max_pages_is_configurable():
    brave = FakeBrave(_org_pages({p: 9 for p in range(10)}))
    discover_linkedin(_org(), [brave], config=PeopleDiscoveryConfig(org_max_pages=2))
    assert _org_pages_requested(brave) == [0, 1]


def test_org_page2_thresholds_are_configurable():
    brave = FakeBrave(_org_pages({0: 4}))
    cfg = PeopleDiscoveryConfig(org_page2_min_new_associated=10, org_page2_min_profiles=50)
    discover_linkedin(_org(), [brave], config=cfg)
    assert _org_pages_requested(brave) == [0]


def test_family_page2_and_page3_only_while_productive():
    first = FAMILIES[0]["terms"][0]
    yields = {0: 3, 1: 2, 2: 1, 3: 9}

    def results(term, page, query):
        if term == first:
            return [_p(f"fam-{page}-{k}", "IT Director") for k in range(yields[page])], True
        return [], False
    brave = FakeBrave(results)
    discover_linkedin(_org(), [brave], config=PeopleDiscoveryConfig(family_max_pages=9))
    assert [page for q, page in brave.calls if _term_of(q) == first] == [0, 1, 2]


def test_family_page2_not_requested_when_page1_is_below_threshold():
    first = FAMILIES[0]["terms"][0]

    def results(term, page, query):
        if term == first:
            return [_p(f"fam-{page}-{k}", "IT Director") for k in range(2)], True
        return [], False
    brave = FakeBrave(results)
    discover_linkedin(_org(), [brave])
    assert [page for q, page in brave.calls if _term_of(q) == first] == [0]


def test_provider_without_pagination_is_never_paged():
    static = FakePlain("static", lambda term, n: [_p(f"s-{n}-{k}", "Cloud Engineer") for k in range(6)])
    discover_linkedin(_org(), [static])  # FakePlain.search() has no `page` parameter
    assert static.calls


# ---------------------------------------------------------------------------
# 5. Discovery yield and adaptive budget (no cap on people)
# ---------------------------------------------------------------------------

def test_person_yield_weights():
    assert person_yield_weight([GROUP_TECH_LEADERSHIP, GROUP_INFRA_CLOUD]) == 1.0
    assert person_yield_weight([GROUP_CYBERSECURITY]) == 0.9
    assert person_yield_weight([GROUP_INFRA_CLOUD]) == 0.9
    assert person_yield_weight([GROUP_DIGITAL_DATA]) == 0.9
    assert person_yield_weight([GROUP_PROCUREMENT]) == 0.8
    assert person_yield_weight([GROUP_OTHER]) == 0.25


def test_family_stops_after_two_consecutive_zero_yield_requests():
    static = FakePlain("static")
    cfg = PeopleDiscoveryConfig(saturation_window=1000)
    result = discover_linkedin(_org(), [static], config=cfg)
    fams = _metrics(result)["families"]
    # Every family stops after two zero-yield requests -- except that the executive title
    # queries of a family (its first `executive_terms` terms) are a guaranteed floor and
    # all run, so the exact-phrase variants still run when the OR groups return nothing.
    expected = {f["id"]: max(2, int(f.get("executive_terms") or 0)) for f in FAMILIES}
    for f in FAMILIES:
        assert fams[f["id"]]["requests"] == expected[f["id"]]
        assert fams[f["id"]]["stop_reason"] == "zero yield"
    assert len(static.calls) == 1 + sum(expected.values())


def test_saturation_stops_the_run_after_the_floor():
    static = FakePlain("static", lambda term, n: [_p("same-person", "CIO")])
    result = discover_linkedin(_org(), [static])
    assert len(static.calls) == FLOOR
    assert _metrics(result)["providers"]["static"]["stop_reason"] == "saturated"


def test_soft_budget_with_moderate_yield():
    static = FakePlain("static", lambda term, n: [_p(f"m-{n}", "Network Engineer")])  # 0.9 per request
    result = discover_linkedin(_org(), [static])
    assert len(static.calls) == PeopleDiscoveryConfig().soft_budget == 30
    assert _metrics(result)["providers"]["static"]["stop_reason"] == "budget reached"


def test_budget_extends_to_the_hard_ceiling_only_while_yield_is_high():
    static = FakePlain("static", lambda term, n: [_p(f"h-{n}-{k}", "Cloud Engineer") for k in range(5)])
    result = discover_linkedin(_org(), [static])
    cfg = PeopleDiscoveryConfig()
    assert len(static.calls) == cfg.hard_ceiling == 45
    m = _metrics(result)
    assert m["providers"]["static"]["stop_reason"] == "hard ceiling"
    assert m["providers"]["static"]["budget"] == 45


def test_query_budget_argument_overrides_the_soft_budget():
    static = FakePlain("static", lambda term, n: [_p(f"m-{n}", "Network Engineer")])
    discover_linkedin(_org(), [static], query_budget=12)
    assert len(static.calls) == 12


def test_no_cap_on_people_with_a_high_yield_run():
    static = FakePlain("static", lambda term, n: [_p(f"h-{n}-{k}", "Cloud Engineer") for k in range(5)])
    result = discover_linkedin(_org(), [static])
    assert len(result.profile_candidates) == 5 * len(static.calls) > 200
    assert len(build_people(result.profile_candidates, organization_name=ORG)) == len(result.profile_candidates)


def test_telemetry_reports_yield_per_request():
    brave = FakeBrave(_new_each_call("b", per_call=2))
    result = discover_linkedin(_org(), [brave])
    m = _metrics(result)
    assert m["requests"] == len(brave.calls)
    assert m["associated_profiles"] == len(result.profile_candidates)
    assert m["people_per_request"] == round(m["associated_profiles"] / m["requests"], 2)
    entry = m["request_log"][0]
    assert {"provider", "query", "page", "dimension", "profiles", "new_associated", "weighted_yield"} <= set(entry)
    assert entry["new_associated"] == 2 and entry["weighted_yield"] == pytest.approx(1.8)


# ---------------------------------------------------------------------------
# 6. Dedup and provenance across pages / queries / subdomains
# ---------------------------------------------------------------------------

def test_same_person_across_pages_queries_and_subdomains_is_one_candidate():
    first = FAMILIES[0]["terms"][0]
    rich = f"IT Director at {ORG}. Leads infrastructure, cloud and network operations."

    def results(term, page, query):
        if term is None and page == 0:
            return [_p("ana-a", "IT Director")], False
        if term == first and page == 0:
            return [_p("ana-a", "IT Director", host="om", snippet=rich)], False
        return [], False
    result = discover_linkedin(_org(), [FakeBrave(results)])
    assert len(result.profile_candidates) == 1
    hits = result.profile_candidates[0].extra["discovery_hits"]
    assert {h["dimension"] for h in hits} == {"general_workforce", f"family:{FAMILIES[0]['id']}"}
    assert result.profile_candidates[0].snippet == rich
    person = build_people(result.profile_candidates, organization_name=ORG)[0].to_dict()
    assert person["discovery_hit_count"] == 2
    assert person["discovery_providers"] == ["brave"]


# ---------------------------------------------------------------------------
# 7. Association: current employer only (never loosened)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    f"Network Manager | Former IT Manager at {ORG}",
    f"Ex-{ORG} | Cloud Architect at Other Holding Company",
    f"Previously Network Manager at {ORG}. Now at Other Holding Company",
    f"Formerly with {ORG}",
    f"Retired from {ORG}",
])
def test_former_employer_mentions_do_not_count(text):
    assert assess_organization_association(text, ORG)[0] == "unconfirmed"


def test_current_employment_still_confirmed_when_a_former_employer_is_also_mentioned():
    level, _ = assess_organization_association(f"Network Manager at {ORG}. Previously at Other Holding Company", ORG)
    assert level == "confirmed"


def test_experience_field_is_current_employer_evidence():
    level, evidence = assess_organization_association(f"Jane Roe. Experience: {ORG} · Location: Nordholm", ORG)
    assert level == "confirmed" and any("current employer" in e for e in evidence)


def test_linkedin_title_company_segment_is_current_employer_evidence():
    level, evidence = assess_organization_association(f"Jane Roe - Cloud Architect - {ORG} | LinkedIn", ORG)
    assert level == "confirmed" and any("current employer" in e for e in evidence)


def test_short_snippet_without_the_organisation_is_not_qualified():
    brave = FakeBrave(lambda term, page, q: ([("https://www.linkedin.com/in/no-org", "No Org - CIO | LinkedIn", "CIO")], False))
    result = discover_linkedin(_org(), [brave])
    assert build_people(result.profile_candidates, organization_name=ORG) == []
    assert _metrics(result)["associated_profiles"] == 0


def test_former_employee_is_not_counted_in_discovery_yield():
    brave = FakeBrave(lambda term, page, q: ([(
        "https://www.linkedin.com/in/ex-person", "Ex Person - CIO - Other Holding Company | LinkedIn",
        f"Former IT Director at {ORG}")], False))
    result = discover_linkedin(_org(), [brave])
    assert _metrics(result)["associated_profiles"] == 0
    assert build_people(result.profile_candidates, organization_name=ORG) == []


# ---------------------------------------------------------------------------
# 8. Classification: telecom vocabulary, Gulf titles, ampersands
# ---------------------------------------------------------------------------

def _groups(title):
    r = classify_person_text(title)
    return people_groups({"seniority": r["seniority"], "functions": r["functions"],
                          "technology_areas": r["technology_areas"], "role_id": r["role_id"]}), r


@pytest.mark.parametrize("title", ["OSS/BSS Manager", "RAN Engineer", "Core Network Manager", "NOC Engineer",
                                   "Service Assurance Lead", "Billing Systems Manager", "Network Planning Engineer",
                                   "Telecom Engineer", "Service Delivery Manager", "Network Operations Specialist"])
def test_telecom_titles_map_to_infrastructure_cloud(title):
    groups, r = _groups(title)
    assert GROUP_INFRA_CLOUD in groups and GROUP_OTHER not in groups
    assert "telecom_technology" in r["functions"]


@pytest.mark.parametrize("title", ["Head of OSS/BSS", "Director RAN", "Head of Service Assurance",
                                   "Director Billing Systems", "Head of Network Operations",
                                   "Director Network Planning", "Head of Core Network", "Director Service Delivery"])
def test_telecom_vocabulary_never_makes_technology_leadership(title):
    groups, _ = _groups(title)
    assert GROUP_TECH_LEADERSHIP not in groups
    assert GROUP_INFRA_CLOUD in groups


@pytest.mark.parametrize("title,seniority", [("GM IT", "director"), ("DGM Information Technology", "director"),
                                             ("General Manager - IT", "director"),
                                             ("Deputy General Manager - Technology", "director"),
                                             ("Chief Technology & Information Officer", "c_level")])
def test_gulf_and_ampersand_technology_titles_are_technology_leadership(title, seniority):
    groups, r = _groups(title)
    assert r["seniority"] == seniority
    assert GROUP_TECH_LEADERSHIP in groups


@pytest.mark.parametrize("title", ["GM Finance", "General Manager - Human Resources", "DGM Legal"])
def test_gulf_business_titles_are_not_technology_leadership(title):
    groups, r = _groups(title)
    assert r["seniority"] == "director"
    assert GROUP_TECH_LEADERSHIP not in groups


def test_section_head_is_manager_level():
    groups, r = _groups("Section Head - IT Infrastructure")
    assert r["seniority"] == "manager" and GROUP_TECH_LEADERSHIP not in groups


# ---------------------------------------------------------------------------
# 9. Sales-priority tier: separate from relevance_score
# ---------------------------------------------------------------------------

def _tier(title):
    groups, r = _groups(title)
    return sales_priority({"seniority": r["seniority"], "functions": r["functions"],
                           "technology_areas": r["technology_areas"], "role_id": r["role_id"]})[0]


def test_sales_priority_tiers_follow_enterprise_technology_sales_usefulness():
    assert _tier("Chief Information Officer") == 1
    assert _tier("IT Director") == 2
    assert _tier("Network Manager") == 3
    assert _tier("Procurement Director") == 4
    assert _tier("SOC Analyst") == 5
    assert _tier("Procurement Manager") == 5
    assert _tier("Chief Financial Officer") == 6
    assert _tier("Office Administrator") == 7
    assert _tier("Head of OSS/BSS") > 2  # telecom vocabulary never reaches the leadership tiers


def test_relevance_score_has_no_sales_priority_or_otech_factor():
    _, factors = score_person(seniority="c_level", functions=["it"], technology_areas=["cloud"],
                              organization_association="confirmed", source_url_count=1, discovery_dimensions=[])
    assert set(factors) == {"seniority", "function_relevance", "technology_relevance", "organization_association",
                            "multiple_evidence_sources", "discovery_dimension_corroboration"}


def test_build_people_order_is_still_relevance_order_for_protected_sections():
    from research.sources import Source
    sources = [Source(url=f"https://www.linkedin.com/in/{slug}", domain="linkedin.com", title=title,
                      source_type="linkedin_profile", snippet="", discovered_via_query="q",
                      discovery_provider="brave", family="linkedin_people")
               for slug, title in [("cfo", f"Cfo Person - Chief Financial Officer - {ORG} | LinkedIn"),
                                   ("eng", f"Eng Person - Network Engineer - {ORG} | LinkedIn")]]
    people = [p.to_dict() for p in build_people(sources, organization_name=ORG)]
    key = [(-p["relevance_score"], -p["confidence"], p["name"]) for p in people]
    assert key == sorted(key)
    assert {p["name"]: p["sales_priority_tier"] for p in people} == {"Cfo Person": 6, "Eng Person": 5}


def _ui_person(pid, name, seniority, functions, relevance):
    return {
        "person_id": pid, "name": name, "current_title": name, "organization_name": ORG,
        "organization_association": "confirmed", "linkedin_url": f"https://www.linkedin.com/in/{pid}",
        "source_urls": [f"https://www.linkedin.com/in/{pid}"], "source_types": ["linkedin_profile"],
        "location": None, "seniority": seniority, "functions": list(functions), "technology_areas": [],
        "influence_type": None, "relevance_score": relevance, "confidence": 70.0, "evidence": [],
        "recent_signals": [], "chips": [], "classification_text": name, "discovery_providers": ["brave"],
    }


def test_people_page_is_sorted_by_sales_priority_tier():
    people = [_ui_person("cfo", "Zed Finance", "c_level", [], 90),
              _ui_person("eng", "Yan Engineer", None, ["infrastructure"], 40),
              _ui_person("cio", "Xia Chief", "c_level", ["it"], 60)]
    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    report = {"organization_name": ORG, "country": COUNTRY, "status": "qualified", "chain": [], "signals": [],
              "capability_summary": {}, "incumbent_evidence": [], "acquisition_diagnostics": {},
              "qualification": {"qualified_count": 1, "disqualified_count": 0, "corpus_reason": "",
                                "disqualified_reasons": []},
              "people": people}
    at.session_state["entry"] = {"report": report, "ai_synthesis": {}, "opportunity_hypotheses": []}
    at.session_state["loaded_from"] = "test"
    at.run()
    assert not at.exception
    order = []
    for m in at.markdown:
        order += re.findall(r'<div class="ar-person-name">([^<]+)</div>', m.value)
    assert order == ["Xia Chief", "Yan Engineer", "Zed Finance"]
    html = " ".join(m.value for m in at.markdown)
    assert "Technology decision maker" in html

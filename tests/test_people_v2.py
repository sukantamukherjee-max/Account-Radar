"""People Intelligence v2.

Covers: no people caps (30 people / 100 candidates), the People-specific
provider order (Tavily primary, Brave supplemental, Serper final fallback),
cross-provider merge / dedup / provenance, adaptive query budget,
taxonomy-driven queries, group mapping, Technology Leadership rules,
OTech-relevance ranking, Brave rich snippets and the PEOPLE page rendering
the complete set.

No network and no API keys: providers are small in-memory fakes and the
organization is a generic placeholder.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

import commercial.people as people_module
from commercial.people import (
    GROUP_CYBERSECURITY,
    GROUP_DIGITAL_DATA,
    GROUP_INFRA_CLOUD,
    GROUP_OTHER,
    GROUP_PROCUREMENT,
    GROUP_TECH_LEADERSHIP,
    PEOPLE_GROUP_ORDER,
    build_people,
    classify_person_text,
    group_members,
    people_groups,
)
from commercial.people_taxonomy import get_taxonomy
from research.linkedin import (
    PEOPLE_MODE_ENV,
    PEOPLE_QUERY_BUDGET,
    PeopleDiscoveryConfig,
    _person_queries,
    discover_linkedin,
    order_people_providers,
    query_families,
)
from research.organization import ResolvedOrganization
from research.providers.base import QueryOutcome, RawResult
from research.providers.brave import BraveSearchProvider

import streamlit as _REAL_STREAMLIT  # noqa: E402  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
ORG = "Meridian Ports Authority"
COUNTRY = "Nordholm"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakePeopleProvider:
    """In-memory provider. `results_for(query, call_no)` returns RawResults."""

    def __init__(self, name, results_for=None, *, configured=True, error=None):
        self.name = name
        self._results_for = results_for or (lambda q, n: [])
        self._configured = configured
        self._error = error
        self.queries: list[str] = []

    def is_configured(self):
        return self._configured

    def search(self, query, *, num_results=10):
        self.queries.append(query)
        if self._error:
            return QueryOutcome(query=query, provider=self.name, error=self._error)
        rows = self._results_for(query, len(self.queries))
        return QueryOutcome(query=query, provider=self.name, results=[
            RawResult(url=u, title=t, snippet=s, query=query, provider=self.name, rank=i)
            for i, (u, t, s) in enumerate(rows)
        ])


def _org():
    return ResolvedOrganization(name=ORG, country=COUNTRY, website="https://www.example-meridian.test",
                                linkedin_company_url=None, website_was_supplied=False, linkedin_was_supplied=False)


def _profile(slug, title, snippet="", host="www"):
    return (f"https://{host}.linkedin.com/in/{slug}", title, snippet)


def _fixed(rows):
    return lambda q, n: list(rows)


def _always_new(prefix):
    """Every query returns one NEW organization-associated profile."""
    return lambda q, n: [_profile(f"{prefix}-{n}", f"{prefix} {n} - IT Manager - {ORG} | LinkedIn", "IT Manager")]


def _people_from(result):
    return [p.to_dict() for p in build_people(result.profile_candidates, organization_name=ORG)]


# ---------------------------------------------------------------------------
# 1. No people caps
# ---------------------------------------------------------------------------

def test_no_thirty_person_cap_all_qualified_people_are_kept():
    from research.sources import Source
    sources = []
    for i in range(47):
        s = Source(url=f"https://www.linkedin.com/in/person-{i}", domain="linkedin.com",
                   title=f"Person {i} - Network Engineer - {ORG} | LinkedIn", source_type="linkedin_profile",
                   snippet="Network engineer", discovered_via_query="q", discovery_provider="tavily",
                   family="linkedin_people")
        sources.append(s)
    assert len(build_people(sources, organization_name=ORG)) == 47
    assert not hasattr(people_module, "_MAX_TARGET")


def test_no_hundred_candidate_cap():
    # 5 new profiles per request; the adaptive budget, not a people cap, ends the run.
    def five_new(q, n):
        return [_profile(f"p-{n}-{k}", f"P {n} {k} - Cloud Engineer - {ORG} | LinkedIn") for k in range(5)]
    brave = FakePeopleProvider("brave", five_new)
    result = discover_linkedin(_org(), [brave])
    assert len(result.profile_candidates) == 5 * len(brave.queries) > 100
    assert len(_people_from(result)) == len(result.profile_candidates)


# ---------------------------------------------------------------------------
# 2. Provider order, merge, dedup, provenance
# ---------------------------------------------------------------------------

def test_brave_tavily_mode_provider_order_is_tavily_brave_serper():
    brave, serper, tavily = (FakePeopleProvider(n) for n in ("brave", "serper", "tavily"))
    assert [p.name for p in order_people_providers([brave, serper, tavily])] == ["tavily", "brave", "serper"]


def test_general_provider_order_is_unchanged():
    from research.providers.registry import SEARCH_PROVIDER_ORDER
    assert tuple(SEARCH_PROVIDER_ORDER) == ("brave", "tavily", "serper")


def test_tavily_and_brave_results_are_merged_and_deduplicated_with_provenance(monkeypatch):
    # Maximum-coverage mode (kept for when the Tavily quota allows it).
    monkeypatch.setenv(PEOPLE_MODE_ENV, "brave_tavily")
    long_snippet = f"Head of Cybersecurity at {ORG}. Leads security operations, IAM and SOC for the group."
    tavily = FakePeopleProvider("tavily", _fixed([
        _profile("ana-a", f"Ana A - {ORG} | LinkedIn", long_snippet),
        _profile("ben-b", f"Ben B - Procurement Manager - {ORG} | LinkedIn", "Procurement"),
    ]))
    brave = FakePeopleProvider("brave", _fixed([
        _profile("ana-a", f"Ana A - {ORG} | LinkedIn", "Security", host="om"),  # same person, other subdomain
        _profile("cy-c", f"Cy C - Enterprise Architect - {ORG} | LinkedIn", "Architecture"),
    ]))
    result = discover_linkedin(_org(), [brave, tavily])  # given out of order on purpose
    assert tavily.queries and brave.queries
    slugs = sorted(s.url.rstrip("/").rsplit("/", 1)[-1] for s in result.profile_candidates)
    assert slugs == ["ana-a", "ben-b", "cy-c"]  # Ana counted once

    by_slug = {s.url.rstrip("/").rsplit("/", 1)[-1]: s for s in result.profile_candidates}
    assert by_slug["ana-a"].extra["discovery_providers"] == ["tavily", "brave"]
    assert by_slug["ana-a"].snippet == long_snippet  # richest snippet kept as the main one
    assert "Security" in by_slug["ana-a"].extra["supplemental_snippets"]

    people = {p["name"]: p for p in _people_from(result)}
    assert people["Ana A"]["discovery_providers"] == ["brave", "tavily"] or \
        sorted(people["Ana A"]["discovery_providers"]) == ["brave", "tavily"]
    assert people["Ben B"]["discovery_providers"] == ["tavily"]
    assert people["Cy C"]["discovery_providers"] == ["brave"]
    # classification used the rich (Tavily) snippet
    assert GROUP_CYBERSECURITY in people["Ana A"]["people_groups"]

    fo = result.family_outcome
    assert [r.provider for r in fo.runs if r.used] == ["tavily", "brave"]
    assert fo.fallback_triggered


def test_serper_is_skipped_when_results_are_not_thin():
    brave = FakePeopleProvider("brave", _always_new("b"))
    serper = FakePeopleProvider("serper", _always_new("s"))
    result = discover_linkedin(_org(), [serper, brave])
    assert serper.queries == []
    assert "not needed" in result.family_outcome.coverage.metrics["provider_notes"]["serper"]


def test_serper_supplements_when_results_are_thin():
    tavily = FakePeopleProvider("tavily", _fixed([]))
    brave = FakePeopleProvider("brave", _fixed([]))
    serper = FakePeopleProvider("serper", _fixed([_profile("x-1", f"X 1 - CIO - {ORG} | LinkedIn")]))
    result = discover_linkedin(_org(), [tavily, brave, serper])
    assert serper.queries and tavily.queries == []  # brave_only: Tavily is never called
    assert [s.extra["discovery_providers"] for s in result.profile_candidates] == [["serper"]]


def test_unconfigured_and_failing_providers_do_not_block_the_others():
    tavily = FakePeopleProvider("tavily", configured=False)
    brave = FakePeopleProvider("brave", error="HTTP 500")
    serper = FakePeopleProvider("serper", _fixed([_profile("x-1", f"X 1 - CTO - {ORG} | LinkedIn")]))
    result = discover_linkedin(_org(), [tavily, brave, serper])
    assert tavily.queries == []
    assert len(brave.queries) == PeopleDiscoveryConfig().error_circuit  # circuit breaker
    assert len(result.profile_candidates) == 1


# ---------------------------------------------------------------------------
# 3. Query budget and adaptive stop
# ---------------------------------------------------------------------------

def test_soft_query_budget_is_thirty_requests_for_moderate_yield():
    assert PEOPLE_QUERY_BUDGET == 30
    assert len(_person_queries(ORG, COUNTRY)) > PEOPLE_QUERY_BUDGET  # the plan is larger than the budget
    tavily = FakePeopleProvider("tavily", _always_new("t"))
    brave = FakePeopleProvider("brave", _always_new("b"))
    discover_linkedin(_org(), [tavily, brave])
    assert tavily.queries == []  # brave_only
    assert len(brave.queries) == PEOPLE_QUERY_BUDGET


def test_saturation_stops_a_provider_early():
    same = _fixed([_profile("only-one", f"Only One - CIO - {ORG} | LinkedIn")])
    brave = FakePeopleProvider("brave", same)
    result = discover_linkedin(_org(), [brave])
    exec_extra = sum(max(0, int(f.get("executive_terms") or 0) - 1) for f in query_families())
    assert len(brave.queries) == 1 + len(query_families()) + exec_extra < PEOPLE_QUERY_BUDGET
    assert result.family_outcome.coverage.metrics["provider_notes"]["brave"] == "saturated"


# ---------------------------------------------------------------------------
# 4. Association safeguards preserved
# ---------------------------------------------------------------------------

def test_association_safeguards_are_preserved():
    brave = FakePeopleProvider("brave", _fixed([
        _profile("in-org", f"In Org - IT Director - {ORG} | LinkedIn"),
        _profile("elsewhere", "Elsewhere - IT Director - Another Company | LinkedIn", "IT Director at Another Company"),
    ]))
    people = _people_from(discover_linkedin(_org(), [brave]))
    assert [p["name"] for p in people] == ["In Org"]
    assert people[0]["organization_association"] == "confirmed"


# ---------------------------------------------------------------------------
# 5. Taxonomy-driven queries
# ---------------------------------------------------------------------------

def test_queries_are_taxonomy_driven_and_prioritised():
    pairs = _person_queries(ORG, COUNTRY)
    assert pairs[0][0] == "general_workforce"
    for _, q in pairs:
        assert ORG in q and "site:" not in q and '"' not in q and " OR " not in q
    planned = [q.lower() for _, q in pairs]
    for term in ("cio", "cto", "ciso", "infrastructure", "cloud", "network", "cyber", "data", "procurement"):
        assert any(term in q for q in planned), term
    # technology leadership is queried before anything else
    assert pairs[1][0] == "family:technology_leadership"
    # no generic business-title queries
    for _, q in pairs:
        term = q[len(ORG):].lower().replace("linkedin", "").strip()
        assert term not in {"manager", "director", "finance manager", "finance director", "hr manager"}
    # the plain wording carries the executive groups WITHOUT their Brave operators
    from research.linkedin import plain_term
    family_terms = {plain_term(t).lower() for f in get_taxonomy()["people_discovery"]["query_families"] for t in f["terms"]}
    planned_terms = {q[len(ORG):].lower().replace(" linkedin", "").strip() for d, q in pairs if d != "general_workforce"}
    assert planned_terms == family_terms


# ---------------------------------------------------------------------------
# 6. Group mapping
# ---------------------------------------------------------------------------

def _groups(title):
    r = classify_person_text(title)
    return people_groups({"seniority": r["seniority"], "functions": r["functions"],
                          "technology_areas": r["technology_areas"], "role_id": r["role_id"]})


@pytest.mark.parametrize("title", ["Network Manager", "Senior Network Engineer", "IT Operations Manager",
                                   "Head of Infrastructure", "Cloud Architect", "Data Center Manager",
                                   "Platform Engineer", "Systems Engineer"])
def test_infrastructure_titles_map_to_infrastructure_cloud(title):
    assert GROUP_INFRA_CLOUD in _groups(title)


@pytest.mark.parametrize("title", ["Applications Manager", "Enterprise Architect", "ERP Lead",
                                   "Head of Digital Transformation", "Data Analytics Manager", "AI Lead"])
def test_digital_titles_map_to_digital_data(title):
    assert GROUP_DIGITAL_DATA in _groups(title)


@pytest.mark.parametrize("title", ["Cybersecurity Manager", "Information Security Officer", "SOC Analyst",
                                   "IAM Lead", "Security Architect", "CISO"])
def test_security_titles_map_to_cybersecurity(title):
    assert GROUP_CYBERSECURITY in _groups(title)


@pytest.mark.parametrize("title", ["Procurement Manager", "Sourcing Manager", "Vendor Management Lead",
                                   "Contracts Manager"])
def test_procurement_titles_map_to_procurement(title):
    assert GROUP_PROCUREMENT in _groups(title)


@pytest.mark.parametrize("title", ["Physical Security Manager", "Security Guard Supervisor",
                                   "Head of Safety and Security", "Airport Security Officer", "CCTV Operator"])
def test_physical_security_is_not_cybersecurity(title):
    assert GROUP_CYBERSECURITY not in _groups(title)


def test_professional_network_is_not_the_network_technology():
    r = classify_person_text("Member of the professional network of engineers")
    assert "network" not in r["technology_areas"]


@pytest.mark.parametrize("title", ["Senior Network Engineer", "SOC Analyst", "Cloud Architect", "Systems Engineer"])
def test_technical_stakeholder_without_seniority_is_not_other(title):
    groups = _groups(title)
    assert GROUP_OTHER not in groups and groups


@pytest.mark.parametrize("title", ["Chief Financial Officer", "Finance Director", "HR Director",
                                   "Procurement Director"])
def test_non_technical_leader_is_not_technology_leadership(title):
    assert GROUP_TECH_LEADERSHIP not in _groups(title)


def test_technology_leadership_requires_technology_relevance():
    assert people_groups({"seniority": "c_level", "functions": [], "technology_areas": []}) == [GROUP_OTHER]
    assert people_groups({"seniority": "director", "functions": ["procurement"]}) == [GROUP_PROCUREMENT]
    assert people_groups({"seniority": "director", "functions": ["it"]}) == [GROUP_TECH_LEADERSHIP, GROUP_INFRA_CLOUD]
    assert people_groups({"seniority": "manager", "functions": ["it"]}) == [GROUP_INFRA_CLOUD]
    for title in ("Chief Information Officer", "CTO", "CISO", "IT Director", "Head of Infrastructure"):
        assert GROUP_TECH_LEADERSHIP in _groups(title), title


def test_every_taxonomy_entry_has_a_valid_group():
    valid = {"infrastructure_cloud", "cybersecurity", "digital_data", "procurement"}
    tax = json.loads((ROOT / "commercial" / "people_taxonomy.json").read_text(encoding="utf-8"))
    for category in ("roles", "functions", "technology_areas"):
        for e in tax[category]:
            assert e.get("group") in valid, (category, e["id"])


def test_group_members_returns_the_six_groups():
    members = group_members([{"person_id": "a", "seniority": "c_level", "functions": ["it"]},
                             {"person_id": "b", "seniority": None, "functions": []}])
    assert list(members) == list(PEOPLE_GROUP_ORDER) and len(members) == 6
    assert members[GROUP_TECH_LEADERSHIP] == {"a"} and members[GROUP_OTHER] == {"b"}


# ---------------------------------------------------------------------------
# 7. OTech-relevance ranking
# ---------------------------------------------------------------------------

def test_technology_stakeholders_rank_above_business_stakeholders():
    # PEOPLE page order = sales-priority tier (relevance_score is unchanged).
    from commercial.people import sort_people_for_sales
    brave = FakePeopleProvider("brave", _fixed([
        _profile("fin", f"Fin Dir - Finance Director - {ORG} | LinkedIn"),
        _profile("net", f"Net Mgr - Network Manager - {ORG} | LinkedIn"),
        _profile("cio", f"Cio Person - Chief Information Officer - {ORG} | LinkedIn"),
    ]))
    names = [p["name"] for p in sort_people_for_sales(_people_from(discover_linkedin(_org(), [brave])))]
    assert names.index("Cio Person") < names.index("Fin Dir")
    assert names.index("Net Mgr") < names.index("Fin Dir")


# ---------------------------------------------------------------------------
# 8. Brave rich snippets (opt-in, People only)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text, self.headers = status, body, "", {}

    def json(self):
        return self._body


class _Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params))
        return self.responses.pop(0)


_BODY = {"web": {"results": [{"url": "https://www.linkedin.com/in/x", "title": "X", "description": "Main",
                              "extra_snippets": ["Extra one", "Extra two"]}]}}


def test_brave_rich_snippets_are_opt_in_and_returned_separately():
    session = _Session(_Resp(200, _BODY), _Resp(200, _BODY))
    brave = BraveSearchProvider(api_key="brave-test-key-not-real", session=session, backoff_base=0.0)
    assert brave.supports_rich_snippets
    plain = brave.search("q", num_results=5)
    rich = brave.search("q", num_results=5, rich_snippets=True)
    assert "extra_snippets" not in session.calls[0]
    assert session.calls[1]["extra_snippets"] == "true"
    assert plain.results[0].snippet.startswith("Main")
    # Identity hardening: extras are NOT merged into the result's own
    # snippet; they travel separately as context.
    assert rich.results[0].snippet == "Main"
    assert rich.results[0].extra_snippets == ["Extra one", "Extra two"]


def test_brave_rich_snippets_rejected_falls_back_once_without_them():
    session = _Session(_Resp(422, {}), _Resp(200, _BODY))
    brave = BraveSearchProvider(api_key="brave-test-key-not-real", session=session, backoff_base=0.0)
    outcome = brave.search("q", num_results=5, rich_snippets=True)
    assert outcome.ok and len(session.calls) == 2
    assert "extra_snippets" in session.calls[0] and "extra_snippets" not in session.calls[1]


# ---------------------------------------------------------------------------
# 9. PEOPLE page renders the complete set
# ---------------------------------------------------------------------------

def _ui_person(i, title, seniority, functions, tech=()):
    return {
        "person_id": f"p{i}", "name": f"Person {i:02d}", "current_title": title, "organization_name": ORG,
        "organization_association": "confirmed", "linkedin_url": f"https://www.linkedin.com/in/p{i}",
        "source_urls": [f"https://www.linkedin.com/in/p{i}"], "source_types": ["linkedin_profile"],
        "location": None, "seniority": seniority, "functions": list(functions), "technology_areas": list(tech),
        "influence_type": None, "relevance_score": 50, "confidence": 70.0, "evidence": [],
        "recent_signals": [], "chips": [], "classification_text": title,
        "discovery_providers": ["tavily", "brave"] if i % 2 else ["tavily"],
    }


_SHAPES = [("Network Engineer", None, [], ["network"]), ("CIO", "c_level", ["it"], []),
           ("Security Analyst", None, ["cybersecurity"], []), ("Data Lead", "manager", ["data"], []),
           ("Buyer", "manager", ["procurement"], []), ("Office Admin", None, [], [])]
UI_PEOPLE = [_ui_person(i, *_SHAPES[i % len(_SHAPES)]) for i in range(60)]


def _apptest():
    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    report = {"organization_name": ORG, "country": COUNTRY, "status": "qualified", "chain": [], "signals": [],
              "capability_summary": {}, "incumbent_evidence": [], "acquisition_diagnostics": {},
              "qualification": {"qualified_count": 1, "disqualified_count": 0, "corpus_reason": "",
                                "disqualified_reasons": []},
              "people": UI_PEOPLE}
    at.session_state["entry"] = {"report": report, "ai_synthesis": {}, "opportunity_hypotheses": []}
    at.session_state["loaded_from"] = "test"
    return at


def _showing(at):
    for c in at.caption:
        m = re.search(r"Showing (\d+) of (\d+) relevant stakeholders", c.value)
        if m:
            return int(m.group(1)), int(m.group(2))
    raise AssertionError("no 'Showing X of Y' caption")


def _names(at):
    out = set()
    for m in at.markdown:
        out.update(re.findall(r'<div class="ar-person-name">([^<]+)</div>', m.value))
    return out


def test_people_page_renders_all_sixty_people_without_truncation():
    at = _apptest()
    at.run()
    assert not at.exception
    assert _showing(at) == (60, 60)
    assert _names(at) == {p["name"] for p in UI_PEOPLE}


def test_group_filter_works_across_the_full_set():
    sys.modules["streamlit"] = _REAL_STREAMLIT
    import importlib

    import app
    app = importlib.reload(app)
    members = app._people_bucket_members(UI_PEOPLE)
    assert {k: len(v) for k, v in members.items()} == {
        "Technology Leadership": 10, "Cybersecurity": 10, "Infrastructure / Cloud": 20,
        "Digital / Data": 10, "Procurement": 10, "Other": 10,
    }
    at = _apptest()
    at.run()
    at.button(key="people_bucket_infrastructure___cloud").click().run()
    assert not at.exception
    assert _showing(at) == (20, 60)

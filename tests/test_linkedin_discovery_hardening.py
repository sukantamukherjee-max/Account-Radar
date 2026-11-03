"""Phase 1C-H4: LinkedIn personal-profile ACQUISITION hardening tests.

Reproduces and fixes the reported acquisition gap: a real Oman Airports
live run resolved a LinkedIn company page fine (au.linkedin.com/company/...)
but all seven LinkedIn people-discovery dimensions returned zero
candidates. Root cause: `_person_queries` built
`site:linkedin.com/in "<company>" (ROLE OR ROLE OR ...)`-style queries --
heavily operator-constrained searches against `/in/` pages, which are far
more sparsely indexed by Google than `/company/` pages, tend to return
nothing at all on Serper's free tier rather than degrading gracefully.

These tests use only synthetic, in-process provider stubs (no network
calls, no live API keys, no fabricated "real" results) and a generic
placeholder organization -- no real organization/sector/country is
referenced.
"""

from __future__ import annotations

from research.linkedin import _person_queries, discover_linkedin
from research.organization import ResolvedOrganization
from research.providers.base import QueryOutcome, RawResult

ORG_NAME = "Meridian Ports Authority"
ORG_COUNTRY = "Nordholm"


class _StaticLinkedInProvider:
    """Returns the SAME fixed synthetic result set for every query,
    regardless of query text -- deterministic and network-free. This
    simulates "the search engine returned some mixed organic results,
    some of which happen to be LinkedIn URLs" without depending on any
    specific query string matching, which is exactly the shape of result
    the post-filtering approach (item 5) is designed to handle.
    """

    name = "static"

    def __init__(self, results: list[RawResult]):
        self._results = results

    def is_configured(self) -> bool:
        return True

    def search(self, query: str, *, num_results: int = 10) -> QueryOutcome:
        return QueryOutcome(
            query=query,
            provider=self.name,
            results=[
                RawResult(url=r.url, title=r.title, snippet=r.snippet, query=query, provider=self.name, rank=i)
                for i, r in enumerate(self._results)
            ],
        )


def _org(*, linkedin_company_url: str | None = None) -> ResolvedOrganization:
    return ResolvedOrganization(
        name=ORG_NAME,
        country=ORG_COUNTRY,
        website="https://www.example-meridian.test",
        linkedin_company_url=linkedin_company_url,
        website_was_supplied=False,
        linkedin_was_supplied=False,
    )


_MIXED_RESULTS = [
    RawResult(url="https://www.linkedin.com/in/synthetic-person-a", title="Synthetic Person A", snippet="IT Manager", query="q", provider="static", rank=0),
    RawResult(url="https://om.linkedin.com/in/synthetic-person-b", title="Synthetic Person B", snippet="Procurement Manager", query="q", provider="static", rank=1),
    RawResult(url="https://au.linkedin.com/in/synthetic-person-c?trk=abc", title="Synthetic Person C", snippet="Finance Manager", query="q", provider="static", rank=2),
    RawResult(url="https://uk.linkedin.com/in/synthetic-person-d/", title="Synthetic Person D", snippet="Director", query="q", provider="static", rank=3),
    RawResult(url="https://au.linkedin.com/company/meridian-ports-authority", title="Meridian Ports Authority | LinkedIn", snippet="Company page", query="q", provider="static", rank=4),
    RawResult(url="https://www.example-news.test/article", title="Unrelated news article", snippet="not linkedin", query="q", provider="static", rank=5),
]


# ---------------------------------------------------------------------------
# Item 1/5/8: generic (non-operator-restricted) results containing valid
# /in/ URLs are retained as personal-profile candidates, across LinkedIn
# subdomains including localized ones (item 6).
# ---------------------------------------------------------------------------

def test_in_urls_from_generic_results_are_retained_across_linkedin_subdomains():
    provider = _StaticLinkedInProvider(_MIXED_RESULTS)
    result = discover_linkedin(_org(), [provider])

    candidate_urls = {s.url for s in result.profile_candidates}
    assert "https://www.linkedin.com/in/synthetic-person-a" in candidate_urls
    assert "https://om.linkedin.com/in/synthetic-person-b" in candidate_urls
    assert "https://au.linkedin.com/in/synthetic-person-c?trk=abc" in candidate_urls
    assert "https://uk.linkedin.com/in/synthetic-person-d" in candidate_urls

    for s in result.profile_candidates:
        assert s.source_type == "linkedin_profile"
        assert s.extra.get("linkedin_kind") == "in"

    # The unrelated non-LinkedIn article must never be treated as a candidate.
    assert "https://www.example-news.test/article" not in candidate_urls


def test_company_page_url_in_generic_results_is_not_treated_as_a_profile_candidate():
    """Item 7: /company/ and /in/ classification stay separate -- a
    /company/ URL turning up in the same result set must become (or
    confirm) the company page, never a profile candidate.
    """
    provider = _StaticLinkedInProvider(_MIXED_RESULTS)
    result = discover_linkedin(_org(), [provider])

    assert result.company_page is not None
    assert result.company_page.url == "https://au.linkedin.com/company/meridian-ports-authority"
    assert result.company_page.source_type == "linkedin_company"
    assert result.company_page.extra.get("linkedin_kind") == "company"

    profile_urls = {s.url for s in result.profile_candidates}
    assert result.company_page.url not in profile_urls


def test_already_resolved_company_page_is_not_overwritten_or_double_counted():
    """When the company page was already resolved upstream (as in the real
    live run), a /company/ URL appearing again in generic LinkedIn-people
    search results must not create a duplicate or overwrite it.
    """
    provider = _StaticLinkedInProvider(_MIXED_RESULTS)
    org = _org(linkedin_company_url="https://au.linkedin.com/company/meridian-ports-authority")
    result = discover_linkedin(org, [provider])

    assert result.company_page is not None
    assert result.company_page.url == "https://au.linkedin.com/company/meridian-ports-authority"
    assert result.company_page.discovery_provider == "resolved"  # unchanged -- not overwritten by the generic loop
    profile_urls = {s.url for s in result.profile_candidates}
    assert "https://au.linkedin.com/company/meridian-ports-authority" not in profile_urls


# ---------------------------------------------------------------------------
# Item 9 (updated for People Intelligence v2 / Brave optimisation): discovery
# dimensions are the taxonomy query families (people_discovery.query_families
# in commercial/people_taxonomy.json) instead of a fixed list of seven; every
# family generates queries and dimension-tagged processing works end to end.
# ---------------------------------------------------------------------------

_DIMENSION_PREFIXES = ("family:",)
_PLAIN_DIMENSIONS = {"general_workforce"}


def _planned_dimensions() -> set[str]:
    return {d for d, _ in _person_queries(ORG_NAME, ORG_COUNTRY)}


def test_taxonomy_dimensions_generate_queries():
    pairs = _person_queries(ORG_NAME, ORG_COUNTRY)
    dimension_names = {d for d, _ in pairs}
    assert "general_workforce" in dimension_names
    assert "family:technology_leadership" in dimension_names
    for d in dimension_names:
        assert d in _PLAIN_DIMENSIONS or d.startswith(_DIMENSION_PREFIXES), d
    assert len(pairs) >= 7
    # Every query string must actually mention the organization.
    for _, q in pairs:
        assert ORG_NAME in q


def test_planned_dimensions_process_returned_in_candidates():
    """With a provider that returns real-shaped /in/ URLs for every query,
    candidates are counted against the dimension that first surfaced them
    and every planned dimension is reported (zero or more)."""
    provider = _StaticLinkedInProvider(_MIXED_RESULTS)
    result = discover_linkedin(_org(), [provider])

    assert set(result.dimension_counts.keys()) == _planned_dimensions()
    assert sum(result.dimension_counts.values()) == len(result.profile_candidates)
    assert result.dimension_counts["general_workforce"] > 0
    assert len(result.profile_candidates) > 0


def test_zero_results_across_all_dimensions_is_still_reported_honestly():
    """A provider that genuinely returns nothing must still leave
    dimension_counts correctly shaped (all zero, not missing keys)."""
    provider = _StaticLinkedInProvider([])
    result = discover_linkedin(_org(), [provider])
    assert set(result.dimension_counts.keys()) == _planned_dimensions()
    assert all(count == 0 for count in result.dimension_counts.values())
    assert result.profile_candidates == []


# ---------------------------------------------------------------------------
# Item 4: no site: operators, no exact-phrase quoting, no boolean OR in the
# LinkedIn person-discovery queries; item 8 coverage for query shape.
# ---------------------------------------------------------------------------

def test_person_queries_avoid_site_operator_quoting_and_boolean_or():
    pairs = _person_queries(ORG_NAME, ORG_COUNTRY)
    for dimension, q in pairs:
        assert "site:" not in q, f"{dimension!r} query still uses a site: operator: {q!r}"
        assert '"' not in q, f"{dimension!r} query still uses exact-phrase quoting: {q!r}"
        assert " OR " not in q, f"{dimension!r} query still uses boolean OR: {q!r}"


def test_general_workforce_query_is_bare_and_includes_country_when_available():
    # general_workforce has only one entry (no role terms) -- find it
    # directly from the pairs list.
    gw_queries = [q for d, q in _person_queries(ORG_NAME, ORG_COUNTRY) if d == "general_workforce"]
    assert len(gw_queries) == 1
    assert ORG_NAME in gw_queries[0]
    assert ORG_COUNTRY in gw_queries[0]
    assert "linkedin" in gw_queries[0].lower()
    assert "site:" not in gw_queries[0]


def test_person_queries_work_without_a_country():
    pairs = _person_queries(ORG_NAME, "")
    assert pairs  # still produces queries
    for _, q in pairs:
        assert "site:" not in q

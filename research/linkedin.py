"""LinkedIn acquisition -- the single, consolidated pipeline.

This replaces FOUR overlapping implementations from app_v1_36_0.py
(linkedin_sources, _deep_linkedin_sources, _openrouter_linkedin_harvest,
_linkedin_search_results) with one pipeline, matching spec §4:

    Organization
        -> Company LinkedIn discovery
        -> Public profile discovery (generic discovery dimensions)
        -> Candidate URL pool
        -> Deduplication
        -> Profile/snippet evidence capture (access_status marked explicitly)
        -> (role/function classification is a later AI-layer stage, not here)

The discovery dimensions below are GENERIC (leadership, technology,
procurement, operations, finance/strategy, general workforce) -- identical
regardless of what sector the organization is in. This is not a "sector
playbook"; it is the same fixed list of business functions every
organization of any kind has some subset of. No organization name, sector,
country, or role list is ever special-cased in code.
"""

from __future__ import annotations

from urllib.parse import urlparse

from research.organization import ResolvedOrganization
from research.providers.base import SearchProvider
from research.providers.router import CoverageAssessment, FamilyOutcome, QueryOutcome, acquire_family
from research.sources import (
    SOURCE_TYPE_LINKEDIN_COMPANY,
    SOURCE_TYPE_LINKEDIN_PROFILE,
    Source,
    dedupe_sources,
    score_source,
)

# Generic discovery dimensions per spec §4/§9. Each dimension carries a
# SHORT list of representative role terms -- these are business functions,
# not sector knowledge, and every organization of any kind has some subset
# of them. Phase 1C-H4: terms are plain strings, not an operator-joined
# clause (see _person_queries below for why).
GENERIC_LINKEDIN_DIMENSIONS: list[tuple[str, list[str]]] = [
    ("general_workforce", []),  # broadest possible: just the org name
    ("executive_leadership", ["CEO", "Managing Director"]),
    ("technology_digital_infrastructure", ["Chief Technology Officer", "IT Manager"]),
    ("procurement_commercial", ["Procurement Manager", "Supply Chain"]),
    ("operations_projects_engineering", ["Operations Manager", "Project Manager"]),
    ("finance_strategy", ["Finance Manager", "Business Development"]),
    ("director_and_management_general", ["Director", "Manager"]),
]

# Bounds how many representative-term queries each non-general dimension
# issues. Combined with the single bare query for "general_workforce", this
# keeps the LinkedIn-person query count for one organization at
# 1 + 6*2 = 13 -- comparable to the pre-H4 design's 7 queries, and well
# inside the ~30-queries-per-run budget the Phase 1A provider validation
# tested Serper's free tier against for a full acquisition run (LinkedIn
# discovery is one part of that budget, alongside official/domain-restricted
# and open-web discovery).
_MAX_TERMS_PER_DIMENSION = 2


def _person_queries(company: str, country: str) -> list[tuple[str, str]]:
    """Returns [(dimension, query_string), ...] for the linkedin.com/in
    breadth pass. Country is included when available but is never required
    -- omitting it must not disqualify a query.

    Phase 1C-H4: these are deliberately PLAIN, UNRESTRICTED queries -- no
    `site:` operator, no exact-phrase quoting, no boolean OR. A real live
    run showed that `site:linkedin.com/in "<company>" (ROLE OR ROLE OR
    ...)`-style queries reliably return ZERO organic results on Serper's
    free tier for `/in/` (personal-profile) searches -- including the
    plainest form of that pattern with no OR clause at all
    (`site:linkedin.com/in "<company>"` for general_workforce) -- even
    though the structurally similar `site:linkedin.com/company "<company>"`
    query used for the company page still works. Individual LinkedIn
    profile pages are far more sparsely indexed by Google than company
    pages, and a heavily operator-constrained query against a thinly
    indexed target tends to come back empty rather than gracefully
    degrading, whereas Google's own ranking for a similar UNRESTRICTED
    query (no site: filter forcing an exact-index match) will often still
    surface a LinkedIn profile URL among its normal top results when one
    exists.

    This restores the originally intended design: broad, generic search
    queries, with candidate LinkedIn `/in/` URLs identified by POST-
    FILTERING whatever comes back (see the URL-path filter in
    discover_linkedin below) -- not by asking the search engine to filter
    for us via an operator it may not honor well for this kind of page.
    """
    out: list[tuple[str, str]] = []
    for dimension, role_terms in GENERIC_LINKEDIN_DIMENSIONS:
        if not role_terms:
            q = f"{company} linkedin"
            if country:
                q = f"{q} {country}"
            out.append((dimension, q))
            continue
        for term in role_terms[:_MAX_TERMS_PER_DIMENSION]:
            out.append((dimension, f"{company} {term} linkedin"))
    return out


def _linkedin_coverage_assessment(outcomes: list[QueryOutcome]) -> CoverageAssessment:
    """LinkedIn-specific coverage: sparse if the primary provider errored
    broadly, OR if essentially nothing that looks like a real /in/ or
    /company/ URL came back across the whole dimension set.
    """
    total = len(outcomes)
    errored = sum(1 for o in outcomes if not o.ok)
    profile_urls = set()
    for o in outcomes:
        for r in o.results:
            path = urlparse(r.url).path.lower()
            host = urlparse(r.url).netloc.lower()
            if "linkedin.com" in host and ("/in/" in path or "/company/" in path):
                profile_urls.add(r.url.rstrip("/"))
    metrics = {"queries_run": total, "queries_errored": errored, "linkedin_urls_found": len(profile_urls)}
    if total == 0:
        return CoverageAssessment(True, "no LinkedIn queries were run", metrics)
    if errored == total:
        return CoverageAssessment(True, "primary provider errored on every LinkedIn query", metrics)
    if errored / total >= 0.6:
        return CoverageAssessment(True, f"primary provider errored on {errored}/{total} LinkedIn queries", metrics)
    if len(profile_urls) == 0:
        return CoverageAssessment(True, "zero linkedin.com/in or /company URLs found across all dimensions", metrics)
    return CoverageAssessment(False, "LinkedIn discovery coverage looks healthy", metrics)


class LinkedInDiscoveryResult:
    def __init__(
        self,
        company_page: Source | None,
        profile_candidates: list[Source],
        family_outcome: FamilyOutcome,
        dimension_counts: dict,
    ):
        self.company_page = company_page
        self.profile_candidates = profile_candidates
        self.family_outcome = family_outcome
        self.dimension_counts = dimension_counts

    @property
    def all_sources(self) -> list[Source]:
        out = list(self.profile_candidates)
        if self.company_page:
            out = [self.company_page] + out
        return out


def discover_linkedin(
    org: ResolvedOrganization,
    providers: list[SearchProvider],
    *,
    max_candidates: int = 100,
) -> LinkedInDiscoveryResult:
    """The single LinkedIn acquisition pipeline.

    Step 1 (company page): already resolved in research.organization if
    possible (explicit URL, or found via official-site link-out / search)
    -- this function turns that resolved URL into a Source, and if it's
    still missing, makes one more attempt.

    Steps 2-4 (profile discovery, candidate pool, dedup): the generic
    discovery-dimension breadth pass below, run as ONE family so fallback
    is assessed once across the whole LinkedIn effort, not per-query and
    not per-dimension.
    """
    company = org.name
    country = org.country or ""

    company_source: Source | None = None
    if org.linkedin_company_url:
        company_source = Source(
            url=org.linkedin_company_url.rstrip("/"),
            domain="linkedin.com",
            title=f"{company} — LinkedIn Company Page",
            source_type=SOURCE_TYPE_LINKEDIN_COMPANY,
            snippet="LinkedIn company page." + (" (explicitly supplied)" if org.linkedin_was_supplied else " (discovered)"),
            discovered_via_query="(organization resolution)",
            discovery_provider="resolved",
            family="linkedin_company",
            extra={"linkedin_kind": "company"},
        )
        company_source.source_quality = score_source(company_source)

    dimension_pairs = _person_queries(company, country)
    dimension_names = [d for d, _ in dimension_pairs]
    queries = [q for _, q in dimension_pairs]

    family_outcome = acquire_family(
        family="linkedin_people",
        queries=queries,
        providers=providers,
        num_results=20,
        max_workers=6,
        coverage_fn=_linkedin_coverage_assessment,
    )

    candidates: list[Source] = []
    dimension_counts: dict[str, int] = {name: 0 for name in dimension_names}
    query_to_dimension = dict(zip(queries, dimension_names))

    for run in family_outcome.runs:
        if not run.used:
            continue
        for outcome in run.outcomes:
            dim = query_to_dimension.get(outcome.query, "unknown")
            for r in outcome.results:
                path = urlparse(r.url).path.lower()
                host = urlparse(r.url).netloc.lower()
                if "linkedin.com" not in host:
                    continue
                if "/company/" in path:
                    if company_source is None:
                        company_source = Source.from_raw(
                            r, source_type=SOURCE_TYPE_LINKEDIN_COMPANY, family="linkedin_company"
                        )
                        company_source.extra["linkedin_kind"] = "company"
                    continue
                if "/in/" not in path:
                    continue
                s = Source.from_raw(r, source_type=SOURCE_TYPE_LINKEDIN_PROFILE, family="linkedin_people")
                s.extra["linkedin_kind"] = "in"
                s.extra["discovery_dimension"] = dim
                candidates.append(s)
                dimension_counts[dim] = dimension_counts.get(dim, 0) + 1

    for s in candidates:
        s.source_quality = score_source(s)
    candidates = dedupe_sources(candidates)
    candidates.sort(key=lambda s: -s.source_quality)
    candidates = candidates[:max_candidates]

    return LinkedInDiscoveryResult(
        company_page=company_source,
        profile_candidates=candidates,
        family_outcome=family_outcome,
        dimension_counts=dimension_counts,
    )

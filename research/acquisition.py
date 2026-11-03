"""Acquisition orchestration: composes organization resolution, official-site
crawl, generic discovery, and LinkedIn discovery into one AcquisitionRun,
with full diagnostics.

This is the ONE place all acquisition families are wired together, replacing
`collect`, `collect_deep`, and `collect_fast` from app_v1_36_0.py. Nothing
below is organization/sector-specific: every function takes the resolved
organization as plain data.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from research.fetcher import fetch_page, published_at_for_url
from research.linkedin import LinkedInDiscoveryResult, discover_linkedin
from research.organization import OrganizationInput, ResolvedOrganization, resolve_organization
from research.providers.base import SearchProvider
from research.providers.router import FamilyOutcome
from research.sources import (
    AccessStatus,
    Source,
    dedupe_sources,
    score_source,
)
from research.web import crawl_official_site, discover_domain_restricted_sources, discover_generic_sources

# How many non-LinkedIn sources to attempt a real page fetch for. Matches
# the original codebase's DEEP_SOURCE_TARGET intent (broad durable corpus)
# without its unbounded fan-out.
MAX_FETCH_GENERAL = 40
# LinkedIn pages are login-walled in the overwhelming majority of
# unauthenticated cases (this is expected, not a bug -- see Phase 0/1B
# design notes). Fetching all discovered candidates would burn a fetch
# call per profile for near-zero additional evidence beyond the snippet
# already captured at discovery time. A small validation sample is
# fetched anyway so access_status reporting reflects real, current
# behavior rather than an assumption.
MAX_FETCH_LINKEDIN_SAMPLE = 8


@dataclass
class FamilyDiagnostic:
    family: str
    queries_run: int
    results_returned: int
    unique_urls: int
    providers_used: list[str]
    fallback_triggered: bool
    fallback_reason: str | None
    errors: list[str]
    provider_requests: dict = field(default_factory=dict)  # queries sent per provider

    @classmethod
    def from_family_outcome(cls, fo: FamilyOutcome) -> "FamilyDiagnostic":
        unique = {r.url.rstrip("/").lower() for r in fo.results}
        return cls(
            family=fo.family,
            queries_run=len(fo.queries),
            results_returned=len(fo.results),
            unique_urls=len(unique),
            providers_used=fo.providers_used,
            fallback_triggered=fo.fallback_triggered,
            fallback_reason=fo.fallback_reason,
            errors=fo.errors,
            provider_requests=fo.provider_requests,
        )


@dataclass
class AcquisitionDiagnostics:
    organization_name: str
    resolved_website: str | None
    resolved_linkedin_company_url: str | None
    started_at: float
    finished_at: float
    family_diagnostics: list[FamilyDiagnostic] = field(default_factory=list)
    linkedin_dimension_counts: dict = field(default_factory=dict)
    total_sources_pre_dedup: int = 0
    total_sources_post_dedup: int = 0
    duplicates_removed: int = 0
    access_status_counts: dict = field(default_factory=dict)
    source_type_counts: dict = field(default_factory=dict)

    @property
    def elapsed_seconds(self) -> float:
        return round(self.finished_at - self.started_at, 2)

    @property
    def total_queries(self) -> int:
        return sum(f.queries_run for f in self.family_diagnostics)

    @property
    def total_results_returned(self) -> int:
        return sum(f.results_returned for f in self.family_diagnostics)

    @property
    def results_per_query(self) -> float:
        q = self.total_queries
        return round(self.total_results_returned / q, 2) if q else 0.0

    @property
    def fallback_families(self) -> list[str]:
        return [f.family for f in self.family_diagnostics if f.fallback_triggered]

    @property
    def fallback_frequency(self) -> str:
        total = len(self.family_diagnostics)
        triggered = len(self.fallback_families)
        pct = round(100 * triggered / total, 1) if total else 0.0
        return f"{triggered}/{total} families ({pct}%)"

    @property
    def providers_used(self) -> list[str]:
        """Search providers actually invoked during this run, in first-use
        order (e.g. ["brave"] or ["brave", "tavily"])."""
        out: list[str] = []
        for f in self.family_diagnostics:
            for name in f.providers_used:
                if name not in out:
                    out.append(name)
        return out

    @property
    def provider_requests(self) -> dict[str, int]:
        """Approximate provider consumption: queries sent per provider."""
        out: dict[str, int] = {}
        for f in self.family_diagnostics:
            for name, count in (f.provider_requests or {}).items():
                out[name] = out.get(name, 0) + count
        return out

    @property
    def all_errors(self) -> list[str]:
        out = []
        for f in self.family_diagnostics:
            out.extend(f"[{f.family}] {e}" for e in f.errors)
        return out

    def to_dict(self) -> dict:
        return {
            "organization": self.organization_name,
            "resolved_website": self.resolved_website,
            "resolved_linkedin_company_url": self.resolved_linkedin_company_url,
            "elapsed_seconds": self.elapsed_seconds,
            "total_queries": self.total_queries,
            "total_results_returned": self.total_results_returned,
            "results_per_query": self.results_per_query,
            "total_sources_pre_dedup": self.total_sources_pre_dedup,
            "total_sources_post_dedup": self.total_sources_post_dedup,
            "duplicates_removed": self.duplicates_removed,
            "fallback_frequency": self.fallback_frequency,
            "fallback_families": self.fallback_families,
            "providers_used": self.providers_used,
            "provider_requests": self.provider_requests,
            "access_status_counts": self.access_status_counts,
            "source_type_counts": self.source_type_counts,
            "linkedin_dimension_counts": self.linkedin_dimension_counts,
            "errors": self.all_errors,
            "families": [
                {
                    "family": f.family,
                    "queries_run": f.queries_run,
                    "results_returned": f.results_returned,
                    "unique_urls": f.unique_urls,
                    "providers_used": f.providers_used,
                    "provider_requests": f.provider_requests,
                    "fallback_triggered": f.fallback_triggered,
                    "fallback_reason": f.fallback_reason,
                    "error_count": len(f.errors),
                }
                for f in self.family_diagnostics
            ],
        }


@dataclass
class AcquisitionRun:
    organization: ResolvedOrganization
    sources: list[Source]
    diagnostics: AcquisitionDiagnostics


def _fetch_sources_inplace(sources: list[Source], *, max_workers: int = 8) -> None:
    """Attempt a real page fetch for sources that don't already have
    content (the official homepage is fetched during crawl_official_site
    already). Fills in access_status + content, then rescored.
    """
    to_fetch = [s for s in sources if s.access_status == AccessStatus.NOT_FETCHED]
    if not to_fetch:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_page, s.url): s for s in to_fetch}
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                content, status = fut.result()
            except Exception:
                content, status = "", AccessStatus.UNREACHABLE
            if status == AccessStatus.FETCHED and content:
                s.content = content
                s.access_status = status
                published_at = published_at_for_url(s.url)
                if published_at:
                    s.extra["published_at"] = published_at
            elif not content and s.snippet:
                # Fetch failed/blocked but we already have discovery-time
                # evidence -- retain it, mark status honestly.
                s.access_status = (
                    AccessStatus.INDEXED_SNIPPET_ONLY if status == AccessStatus.INDEXED_SNIPPET_ONLY else status
                )
            else:
                s.access_status = status
            s.source_quality = score_source(s)


def run_acquisition(
    account: OrganizationInput,
    providers: list[SearchProvider],
    *,
    fetch_general: bool = True,
) -> AcquisitionRun:
    started = time.monotonic()
    family_diagnostics: list[FamilyDiagnostic] = []

    org = resolve_organization(account, providers)

    all_sources: list[Source] = []

    # Official site: direct crawl (no provider query -- kept because it was
    # the most reliable discovery step in the original codebase).
    if org.website:
        all_sources.extend(crawl_official_site(org.website, org.name))

    # Domain-restricted discovery (catches pages the homepage doesn't link to).
    domain_sources, domain_family = discover_domain_restricted_sources(org, providers)
    all_sources.extend(domain_sources)
    if domain_family is not None:
        family_diagnostics.append(FamilyDiagnostic.from_family_outcome(domain_family))

    # Generic open-web discovery: news / procurement / technology / business.
    generic_sources, generic_families = discover_generic_sources(org, providers)
    all_sources.extend(generic_sources)
    for fo in generic_families:
        family_diagnostics.append(FamilyDiagnostic.from_family_outcome(fo))

    # LinkedIn: the single consolidated pipeline.
    linkedin_result: LinkedInDiscoveryResult = discover_linkedin(org, providers)
    all_sources.extend(linkedin_result.all_sources)
    family_diagnostics.append(FamilyDiagnostic.from_family_outcome(linkedin_result.family_outcome))

    for s in all_sources:
        if s.source_quality == 0.0:
            s.source_quality = score_source(s)

    total_pre_dedup = len(all_sources)
    deduped = dedupe_sources(all_sources)
    duplicates_removed = total_pre_dedup - len(deduped)

    if fetch_general:
        non_linkedin = [s for s in deduped if s.source_type not in ("linkedin_profile",)]
        non_linkedin.sort(key=lambda s: -s.source_quality)
        fetch_targets = [s for s in non_linkedin if s.access_status == AccessStatus.NOT_FETCHED][:MAX_FETCH_GENERAL]

        linkedin_profiles = [s for s in deduped if s.source_type == "linkedin_profile"]
        linkedin_profiles.sort(key=lambda s: -s.source_quality)
        linkedin_sample = linkedin_profiles[:MAX_FETCH_LINKEDIN_SAMPLE]

        _fetch_sources_inplace(fetch_targets + linkedin_sample, max_workers=8)

        # Every source that was never attempted keeps evidence via its
        # discovery snippet; mark it explicitly rather than leaving an
        # ambiguous NOT_FETCHED with no snippet fallback applied.
        for s in deduped:
            if s.access_status == AccessStatus.NOT_FETCHED:
                s.access_status = (
                    AccessStatus.INDEXED_SNIPPET_ONLY if s.snippet else AccessStatus.NO_CONTENT
                )

    access_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for s in deduped:
        access_counts[s.access_status.value] = access_counts.get(s.access_status.value, 0) + 1
        type_counts[s.source_type] = type_counts.get(s.source_type, 0) + 1

    finished = time.monotonic()
    diagnostics = AcquisitionDiagnostics(
        organization_name=org.name,
        resolved_website=org.website,
        resolved_linkedin_company_url=org.linkedin_company_url,
        started_at=started,
        finished_at=finished,
        family_diagnostics=family_diagnostics,
        linkedin_dimension_counts=linkedin_result.dimension_counts,
        total_sources_pre_dedup=total_pre_dedup,
        total_sources_post_dedup=len(deduped),
        duplicates_removed=duplicates_removed,
        access_status_counts=access_counts,
        source_type_counts=type_counts,
    )

    return AcquisitionRun(organization=org, sources=deduped, diagnostics=diagnostics)

"""Multi-provider query execution with family-level (not per-query) fallback.

Per the Phase 1B architecture decision: a single sparse query must NOT
trigger a fallback call -- some queries legitimately return few or zero
results (that's real information, not provider failure). Fallback is only
justified when the *whole query family* (e.g. "all LinkedIn person-discovery
queries for this account") looks like the primary provider genuinely
struggled -- it errored out, returned nothing at all, or is unconfigured.

This module has no knowledge of LinkedIn, organizations, or any other
business concept. It only knows how to run a batch of query strings against
an ordered list of providers and report what happened, honestly.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from research.providers.base import QueryOutcome, RawResult, SearchProvider


@dataclass
class CoverageAssessment:
    sparse: bool
    reason: str
    metrics: dict = field(default_factory=dict)


def default_coverage_assessment(outcomes: list[QueryOutcome]) -> CoverageAssessment:
    """Family is sparse if the provider errored on most queries, or if the
    combined, deduplicated result set across the whole family is empty.
    A handful of individual zero-result queries inside an otherwise-healthy
    family is NOT sparse -- that's expected, normal search behavior.
    """
    total = len(outcomes)
    errored = sum(1 for o in outcomes if not o.ok)
    unique_urls = {r.url.rstrip("/") for o in outcomes for r in o.results}
    metrics = {
        "queries_run": total,
        "queries_errored": errored,
        "unique_urls": len(unique_urls),
    }
    if total == 0:
        return CoverageAssessment(True, "no queries were run", metrics)
    if errored == total:
        return CoverageAssessment(True, "primary provider errored on every query in this family", metrics)
    if errored / total >= 0.6:
        return CoverageAssessment(True, f"primary provider errored on {errored}/{total} queries", metrics)
    if len(unique_urls) == 0:
        return CoverageAssessment(True, "primary provider returned zero unique results across the whole family", metrics)
    return CoverageAssessment(False, "primary provider coverage looks healthy", metrics)


@dataclass
class ProviderFamilyRun:
    """What happened when one provider was pointed at a query family."""

    provider: str
    outcomes: list[QueryOutcome]
    used: bool  # False if this provider was never actually invoked


@dataclass
class FamilyOutcome:
    family: str
    queries: list[str]
    runs: list[ProviderFamilyRun]
    fallback_triggered: bool
    fallback_reason: str | None
    coverage: CoverageAssessment

    @property
    def results(self) -> list[RawResult]:
        """All raw results from whichever provider(s) actually ran."""
        out: list[RawResult] = []
        for run in self.runs:
            if run.used:
                for outcome in run.outcomes:
                    out.extend(outcome.results)
        return out

    @property
    def providers_used(self) -> list[str]:
        return [r.provider for r in self.runs if r.used]

    @property
    def provider_requests(self) -> dict[str, int]:
        """Queries actually sent to each provider in this family (approximate
        API consumption). Queries refused locally because the provider is
        not configured cost nothing and are not counted."""
        out: dict[str, int] = {}
        for run in self.runs:
            if run.used:
                sent = sum(1 for o in run.outcomes if not (o.error or "").endswith("not configured"))
                if sent:
                    out[run.provider] = out.get(run.provider, 0) + sent
        return out

    @property
    def errors(self) -> list[str]:
        out = []
        for run in self.runs:
            if run.used:
                for o in run.outcomes:
                    if not o.ok:
                        out.append(f"{run.provider}: {o.query!r} -> {o.error}")
        return out


def _run_queries(
    provider: SearchProvider, queries: list[str], *, num_results: int, max_workers: int
) -> list[QueryOutcome]:
    if not provider.is_configured():
        return [
            QueryOutcome(query=q, provider=provider.name, error=f"{provider.name} not configured")
            for q in queries
        ]
    outcomes: list[QueryOutcome] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(queries) or 1))) as pool:
        futures = {pool.submit(provider.search, q, num_results=num_results): q for q in queries}
        for fut in as_completed(futures):
            q = futures[fut]
            try:
                outcomes.append(fut.result())
            except Exception as exc:  # noqa: BLE001 - provider misbehaved beyond its own contract
                outcomes.append(QueryOutcome(query=q, provider=provider.name, error=f"unexpected error: {exc}"))
    # Keep output order aligned with input query order for readability/diagnostics.
    order = {q: i for i, q in enumerate(queries)}
    outcomes.sort(key=lambda o: order.get(o.query, 0))
    return outcomes


def acquire_family(
    family: str,
    queries: list[str],
    providers: list[SearchProvider],
    *,
    num_results: int = 10,
    max_workers: int = 6,
    coverage_fn: Callable[[list[QueryOutcome]], CoverageAssessment] | None = None,
) -> FamilyOutcome:
    """Run one named query family through the primary provider; escalate to
    the next configured provider only if the family-level coverage
    assessment says the primary was genuinely sparse or unavailable, and
    continue down the chain only while the family remains sparse.
    """
    coverage_fn = coverage_fn or default_coverage_assessment
    if not providers:
        return FamilyOutcome(
            family=family,
            queries=queries,
            runs=[],
            fallback_triggered=False,
            fallback_reason=None,
            coverage=CoverageAssessment(True, "no providers configured for this run", {}),
        )

    runs: list[ProviderFamilyRun] = []
    primary = providers[0]
    primary_outcomes = _run_queries(primary, queries, num_results=num_results, max_workers=max_workers)
    runs.append(ProviderFamilyRun(provider=primary.name, outcomes=primary_outcomes, used=True))

    assessment = coverage_fn(primary_outcomes)
    fallback_triggered = False
    fallback_reason = None

    if assessment.sparse and len(providers) > 1:
        # Ordered fallback chain (e.g. Brave -> Tavily -> Serper). Each
        # fallback is tried only while the family is STILL sparse by the same
        # family-level rule: a healthy fallback ends the chain, so a provider
        # is never called "just in case" and API usage is not inflated.
        for fallback in providers[1:]:
            if not fallback.is_configured():
                runs.append(ProviderFamilyRun(provider=fallback.name, outcomes=[], used=False))
                continue
            fallback_outcomes = _run_queries(fallback, queries, num_results=num_results, max_workers=max_workers)
            runs.append(ProviderFamilyRun(provider=fallback.name, outcomes=fallback_outcomes, used=True))
            fallback_triggered = True
            fallback_reason = fallback_reason or assessment.reason
            if not coverage_fn(fallback_outcomes).sparse:
                break  # this fallback covered the family; stop here

    return FamilyOutcome(
        family=family,
        queries=queries,
        runs=runs,
        fallback_triggered=fallback_triggered,
        fallback_reason=fallback_reason,
        coverage=assessment,
    )

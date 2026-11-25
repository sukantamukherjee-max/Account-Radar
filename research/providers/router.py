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

Run-level provider health (opt-in, see provider_health_scope): inside one
acquisition run, a provider whose WHOLE query family failed for a reason
that will not clear within the run -- rejected key or exhausted quota
(HTTP 401/402/403, Tavily 432/433), or rate limiting (HTTP 429) when a
configured fallback follows it -- is skipped for the rest of that run, so
every later family goes straight to the fallback instead of paying the same
failure (and its retries and backoff) again. Timeouts, network errors, 5xx,
malformed responses and partial failures never trip it. Outside a scope the
router keeps no memory between families, exactly as before.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Iterator

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


# ---------------------------------------------------------------------------
# Run-level provider health
# ---------------------------------------------------------------------------

_HTTP_STATUS_RE = re.compile(r"^HTTP (\d{3}):")
# Failures that will not clear within one run: rejected key / no access
# (401, 403), exhausted quota or plan (402; Tavily 432, 433).
_UNRECOVERABLE_STATUS = frozenset({"401", "402", "403", "432", "433"})
_RATE_LIMIT_STATUS = "429"


class _RunHealth:
    """Providers taken out of service for the rest of ONE acquisition run.
    Keyed by provider object identity; the object is held so its id cannot
    be reused while the run is alive. Lock-protected so a scope stays
    consistent even if families are ever run from several threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tripped: dict[int, tuple[object, str]] = {}

    def reason(self, provider: SearchProvider) -> str | None:
        with self._lock:
            entry = self._tripped.get(id(provider))
        return entry[1] if entry else None

    def trip(self, provider: SearchProvider, reason: str) -> None:
        with self._lock:
            self._tripped.setdefault(id(provider), (provider, reason))


_RUN_HEALTH: ContextVar[_RunHealth | None] = ContextVar("account_radar_provider_health", default=None)


@contextmanager
def provider_health_scope() -> Iterator[None]:
    """One acquisition run. Every provider starts healthy when the scope is
    entered, and everything learned is discarded when it exits -- normally
    or through an exception -- so nothing leaks into the next run, even when
    the same provider objects are reused (batch scripts). Context-local:
    concurrent runs (e.g. separate Streamlit sessions, each on its own
    thread) never see each other's state."""
    token = _RUN_HEALTH.set(_RunHealth())
    try:
        yield
    finally:
        _RUN_HEALTH.reset(token)


def _trip_reason(outcomes: list[QueryOutcome], *, has_fallback: bool) -> str | None:
    """Why this provider should be skipped for the rest of the run, or None.
    Only when EVERY query in the family failed, and every failure is an
    unrecoverable status -- or HTTP 429, if a configured fallback exists."""
    if not outcomes or any(o.ok for o in outcomes):
        return None
    statuses: list[str] = []
    for outcome in outcomes:
        match = _HTTP_STATUS_RE.match(outcome.error or "")
        status = match.group(1) if match else None
        if status in _UNRECOVERABLE_STATUS or (status == _RATE_LIMIT_STATUS and has_fallback):
            statuses.append(status)
        else:
            return None  # timeout, transport, 5xx, malformed, unexpected, not configured ...
    return f"HTTP {Counter(statuses).most_common(1)[0][0]}"


def _skipped_outcomes(provider: SearchProvider, queries: list[str], reason: str) -> list[QueryOutcome]:
    return [
        QueryOutcome(query=q, provider=provider.name, error=f"skipped: unavailable earlier in this run ({reason})")
        for q in queries
    ]


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

    health = _RUN_HEALTH.get()

    def has_fallback_after(index: int) -> bool:
        return any(p.is_configured() and not (health and health.reason(p)) for p in providers[index + 1:])

    def observe(index: int, outcomes: list[QueryOutcome]) -> None:
        if health is not None:
            reason = _trip_reason(outcomes, has_fallback=has_fallback_after(index))
            if reason:
                health.trip(providers[index], reason)

    runs: list[ProviderFamilyRun] = []
    primary = providers[0]
    skip_reason = health.reason(primary) if health else None
    if skip_reason:
        # Taken out of service earlier in this run: no request is sent, the
        # run is recorded as not used (so provider_requests, providers_used
        # and errors are unaffected) and the family goes straight to fallback.
        skipped = _skipped_outcomes(primary, queries, skip_reason)
        runs.append(ProviderFamilyRun(provider=primary.name, outcomes=skipped, used=False))
        assessment = CoverageAssessment(
            True, f"{primary.name} skipped: unavailable earlier in this run ({skip_reason})",
            coverage_fn(skipped).metrics,
        )
    else:
        primary_outcomes = _run_queries(primary, queries, num_results=num_results, max_workers=max_workers)
        runs.append(ProviderFamilyRun(provider=primary.name, outcomes=primary_outcomes, used=True))
        observe(0, primary_outcomes)
        assessment = coverage_fn(primary_outcomes)
    fallback_triggered = False
    fallback_reason = None

    if assessment.sparse and len(providers) > 1:
        # Ordered fallback chain (e.g. Brave -> Tavily). Each
        # fallback is tried only while the family is STILL sparse by the same
        # family-level rule: a healthy fallback ends the chain, so a provider
        # is never called "just in case" and API usage is not inflated.
        for index, fallback in enumerate(providers[1:], start=1):
            if not fallback.is_configured():
                runs.append(ProviderFamilyRun(provider=fallback.name, outcomes=[], used=False))
                continue
            fallback_skip = health.reason(fallback) if health else None
            if fallback_skip:
                runs.append(ProviderFamilyRun(
                    provider=fallback.name, outcomes=_skipped_outcomes(fallback, queries, fallback_skip), used=False))
                continue
            fallback_outcomes = _run_queries(fallback, queries, num_results=num_results, max_workers=max_workers)
            runs.append(ProviderFamilyRun(provider=fallback.name, outcomes=fallback_outcomes, used=True))
            observe(index, fallback_outcomes)
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

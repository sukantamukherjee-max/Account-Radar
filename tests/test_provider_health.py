"""Run-level provider health (research/providers/router.py).

Inside one acquisition run (provider_health_scope), a provider whose WHOLE
query family failed with a failure that will not clear within the run is
skipped for the rest of that run:
  * HTTP 401 / 402 / 403 and Tavily 432 / 433  -> always trip
  * HTTP 429                                   -> trip only when a configured,
                                                  still-healthy fallback follows
  * timeout, transport, 5xx, malformed, unexpected, partial failure -> never
Outside a scope the router keeps no memory between families (unchanged).
No network: providers are scripted in memory.
"""

from __future__ import annotations

import threading

import pytest

import research.acquisition as acquisition
from research.acquisition import FamilyDiagnostic, run_acquisition
from research.organization import OrganizationInput
from research.providers import router
from research.providers.base import QueryOutcome, RawResult
from research.providers.router import CoverageAssessment, acquire_family, provider_health_scope
from research.sources import AccessStatus
from tests.mock_provider import MockSearchProvider

QUERIES = [f"query {i}" for i in range(4)]


class ScriptedProvider:
    """Answers every query with `error` (or one healthy result when error is
    None). `error` may be a callable(query) -> str | None. Counts calls."""

    def __init__(self, name, error=None, configured=True):
        self.name = name
        self.error = error
        self.configured = configured
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def is_configured(self):
        return self.configured

    def search(self, query, *, num_results=10, **_kwargs):
        with self._lock:
            self.calls.append(query)
        error = self.error(query) if callable(self.error) else self.error
        if error:
            return QueryOutcome(query=query, provider=self.name, error=error)
        return QueryOutcome(query=query, provider=self.name, results=[
            RawResult(url=f"https://{self.name}.test/{abs(hash(query))}", title="t", snippet="s",
                      query=query, provider=self.name, rank=0)])


def _families(chain, n=3, queries=QUERIES):
    return [acquire_family(f"family-{i}", list(queries), chain) for i in range(n)]


def _tripped(provider) -> str | None:
    health = router._RUN_HEALTH.get()
    return health.reason(provider) if health else None


# ---------------------------------------------------------------------------
# Trip rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 402, 403, 432, 433])
def test_unrecoverable_status_trips_and_later_families_skip_the_provider(status):
    primary = ScriptedProvider("brave", error=f"HTTP {status}: denied")
    fallback = ScriptedProvider("tavily")
    with provider_health_scope():
        first, *later = _families([primary, fallback], n=7)
        assert _tripped(primary) == f"HTTP {status}"

    assert len(primary.calls) == len(QUERIES)                 # only the first family paid
    assert len(fallback.calls) == 7 * len(QUERIES)            # fallback served every family
    assert first.providers_used == ["brave", "tavily"]
    for outcome in later:
        assert outcome.providers_used == ["tavily"]
        assert outcome.fallback_triggered is True
        assert outcome.fallback_reason == f"brave skipped: unavailable earlier in this run (HTTP {status})"
        assert outcome.provider_requests == {"tavily": len(QUERIES)}   # skipped queries are not requests
        assert outcome.errors == []                                     # no repeated error noise
        assert len(outcome.results) == len(QUERIES)
        skipped = outcome.runs[0]
        assert skipped.provider == "brave" and skipped.used is False
        assert [o.error for o in skipped.outcomes] == [
            f"skipped: unavailable earlier in this run (HTTP {status})"] * len(QUERIES)


def test_unrecoverable_status_trips_even_without_a_fallback():
    only = ScriptedProvider("brave", error="HTTP 401: bad key")
    with provider_health_scope():
        first, second = _families([only], n=2)
    assert len(only.calls) == len(QUERIES)
    assert second.results == [] and second.providers_used == [] and second.provider_requests == {}
    assert second.coverage.sparse is True


def test_rate_limit_trips_when_a_configured_fallback_exists():
    primary = ScriptedProvider("brave", error="HTTP 429: slow down")
    fallback = ScriptedProvider("tavily")
    with provider_health_scope():
        _families([primary, fallback], n=3)
        assert _tripped(primary) == "HTTP 429"
    assert len(primary.calls) == len(QUERIES)
    assert len(fallback.calls) == 3 * len(QUERIES)


def test_rate_limit_does_not_trip_a_single_provider_chain():
    only = ScriptedProvider("brave", error="HTTP 429: slow down")
    with provider_health_scope():
        _families([only], n=3)
        assert _tripped(only) is None
    assert len(only.calls) == 3 * len(QUERIES)


def test_rate_limit_does_not_trip_when_the_only_follower_is_unconfigured():
    primary = ScriptedProvider("brave", error="HTTP 429: slow down")
    unconfigured = ScriptedProvider("tavily", configured=False)
    with provider_health_scope():
        _families([primary, unconfigured], n=3)
        assert _tripped(primary) is None
    assert len(primary.calls) == 3 * len(QUERIES) and unconfigured.calls == []


def test_rate_limit_does_not_trip_when_the_only_follower_is_already_out_of_service():
    primary = ScriptedProvider("brave", error=lambda q: "HTTP 500: oops")
    fallback = ScriptedProvider("tavily", error="HTTP 401: bad key")
    with provider_health_scope():
        acquire_family("f1", QUERIES, [primary, fallback])     # 500s: no trip; tavily 401: trips
        assert _tripped(fallback) == "HTTP 401" and _tripped(primary) is None
        primary.error = "HTTP 429: slow down"
        acquire_family("f2", QUERIES, [primary, fallback])     # no healthy fallback left
        assert _tripped(primary) is None
        acquire_family("f3", QUERIES, [primary, fallback])
    assert len(primary.calls) == 3 * len(QUERIES)
    assert len(fallback.calls) == len(QUERIES)


def test_mixed_unrecoverable_and_rate_limit_failures_trip_with_the_most_common_status():
    errors = {"query 0": "HTTP 401: x", "query 1": "HTTP 429: x", "query 2": "HTTP 429: x", "query 3": "HTTP 402: x"}
    primary = ScriptedProvider("brave", error=errors.get)
    with provider_health_scope():
        acquire_family("f", QUERIES, [primary, ScriptedProvider("tavily")])
        assert _tripped(primary) == "HTTP 429"


def test_a_tripped_fallback_is_skipped_and_the_chain_continues_to_the_next():
    primary = ScriptedProvider("brave", error="HTTP 503: busy")      # never trips
    middle = ScriptedProvider("tavily", error="HTTP 432: plan limit")
    last = ScriptedProvider("serper")
    with provider_health_scope():
        outcomes = _families([primary, middle, last], n=4)
    assert len(primary.calls) == 4 * len(QUERIES)
    assert len(middle.calls) == len(QUERIES)
    assert len(last.calls) == 4 * len(QUERIES)
    for outcome in outcomes[1:]:
        assert outcome.providers_used == ["brave", "serper"]
        assert outcome.provider_requests == {"brave": len(QUERIES), "serper": len(QUERIES)}
        assert [(r.provider, r.used) for r in outcome.runs] == [("brave", True), ("tavily", False), ("serper", True)]


def test_skipped_primary_falls_back_even_with_a_custom_coverage_rule():
    primary = ScriptedProvider("brave", error="HTTP 403: forbidden")
    fallback = ScriptedProvider("tavily")
    never_sparse = lambda outcomes: CoverageAssessment(False, "custom: healthy", {})  # noqa: E731
    with provider_health_scope():
        acquire_family("f1", QUERIES, [primary, fallback], coverage_fn=lambda o: CoverageAssessment(True, "x", {}))
        second = acquire_family("f2", QUERIES, [primary, fallback], coverage_fn=never_sparse)
    assert second.fallback_triggered is True and second.providers_used == ["tavily"]


# ---------------------------------------------------------------------------
# Non-trip rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("error", [
    "timeout",
    "transport error: connection reset",
    "HTTP 500: boom", "HTTP 502: bad gateway", "HTTP 503: busy", "HTTP 504: gateway timeout",
    "malformed response: body is not JSON",
    "malformed response: unexpected JSON shape",
    "unexpected error: boom",
    "HTTP 404: not found",
    "HTTP 400: bad request",
    "HTTP 429, retrying",          # intermediate wording, not a final status
])
def test_transient_or_unrelated_failures_never_trip(error):
    primary = ScriptedProvider("brave", error=error)
    fallback = ScriptedProvider("tavily")
    with provider_health_scope():
        _families([primary, fallback], n=3)
        assert _tripped(primary) is None
    assert len(primary.calls) == 3 * len(QUERIES)


@pytest.mark.parametrize("status", [401, 429])
def test_partial_family_failure_never_trips(status):
    primary = ScriptedProvider("brave", error=lambda q: f"HTTP {status}: x" if q != "query 3" else None)
    with provider_health_scope():
        _families([primary, ScriptedProvider("tavily")], n=3)
        assert _tripped(primary) is None
    assert len(primary.calls) == 3 * len(QUERIES)


def test_qualifying_failures_mixed_with_a_timeout_never_trip():
    primary = ScriptedProvider("brave", error=lambda q: "timeout" if q == "query 0" else "HTTP 401: x")
    with provider_health_scope():
        _families([primary, ScriptedProvider("tavily")], n=2)
        assert _tripped(primary) is None
    assert len(primary.calls) == 2 * len(QUERIES)


def test_unconfigured_primary_is_not_tripped_and_costs_nothing():
    primary = ScriptedProvider("brave", configured=False)
    fallback = ScriptedProvider("tavily")
    with provider_health_scope():
        outcomes = _families([primary, fallback], n=2)
        assert _tripped(primary) is None
    assert primary.calls == []
    assert all(o.provider_requests == {"tavily": len(QUERIES)} for o in outcomes)


def test_empty_query_family_never_trips():
    primary = ScriptedProvider("brave", error="HTTP 401: x")
    with provider_health_scope():
        acquire_family("empty", [], [primary, ScriptedProvider("tavily")])
        assert _tripped(primary) is None


# ---------------------------------------------------------------------------
# Scope isolation
# ---------------------------------------------------------------------------

def test_outside_a_scope_the_router_keeps_no_memory_between_families():
    primary = ScriptedProvider("brave", error="HTTP 401: bad key")
    fallback = ScriptedProvider("tavily")
    outcomes = _families([primary, fallback], n=3)
    assert router._RUN_HEALTH.get() is None
    assert len(primary.calls) == 3 * len(QUERIES)
    assert all(o.providers_used == ["brave", "tavily"] for o in outcomes)
    assert all(o.provider_requests == {"brave": len(QUERIES), "tavily": len(QUERIES)} for o in outcomes)


def test_consecutive_scopes_sharing_providers_each_start_healthy():
    primary = ScriptedProvider("brave", error="HTTP 401: bad key")
    fallback = ScriptedProvider("tavily")
    chain = [primary, fallback]
    with provider_health_scope():
        _families(chain, n=3)
    with provider_health_scope():
        assert _tripped(primary) is None
        _families(chain, n=3)
    assert len(primary.calls) == 2 * len(QUERIES)  # exactly one paid family per run


def test_scope_exited_by_an_exception_leaves_no_state_behind():
    primary = ScriptedProvider("brave", error="HTTP 402: quota")
    chain = [primary, ScriptedProvider("tavily")]
    with pytest.raises(RuntimeError):
        with provider_health_scope():
            acquire_family("f", QUERIES, chain)
            assert _tripped(primary) == "HTTP 402"
            raise RuntimeError("run failed")
    assert router._RUN_HEALTH.get() is None
    with provider_health_scope():
        assert _tripped(primary) is None


def test_nested_scope_is_independent_and_restores_the_outer_one():
    primary = ScriptedProvider("brave", error="HTTP 401: x")
    chain = [primary, ScriptedProvider("tavily")]
    with provider_health_scope():
        acquire_family("outer", QUERIES, chain)
        with provider_health_scope():
            assert _tripped(primary) is None
        assert _tripped(primary) == "HTTP 401"


def test_concurrent_runs_on_separate_threads_do_not_share_health():
    shared_primary = ScriptedProvider("brave", error="HTTP 401: x")
    seen: dict[str, str | None] = {}
    tripped_in_a = threading.Event()
    b_checked = threading.Event()

    def run_a():
        with provider_health_scope():
            acquire_family("a", QUERIES, [shared_primary, ScriptedProvider("tavily")])
            seen["a"] = _tripped(shared_primary)
            tripped_in_a.set()
            b_checked.wait(5)

    def run_b():
        tripped_in_a.wait(5)
        with provider_health_scope():
            seen["b"] = _tripped(shared_primary)
        seen["b_outside"] = _tripped(shared_primary)
        b_checked.set()

    threads = [threading.Thread(target=run_a), threading.Thread(target=run_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert seen == {"a": "HTTP 401", "b": None, "b_outside": None}


# ---------------------------------------------------------------------------
# run_acquisition integration (offline)
# ---------------------------------------------------------------------------

class SwitchableProvider(MockSearchProvider):
    """MockSearchProvider that can be made to reject every request."""

    def __init__(self, name):
        super().__init__(name=name)
        self.reject_with: str | None = None
        self.call_count = 0

    def search(self, query, *, num_results=10, **kwargs):
        self.call_count += 1
        if self.reject_with:
            return QueryOutcome(query=query, provider=self.name, error=self.reject_with)
        return super().search(query, num_results=num_results)


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(acquisition, "fetch_page", lambda url: ("", AccessStatus.UNREACHABLE))
    monkeypatch.setattr(acquisition, "crawl_official_site", lambda website, name: [])


def _router_families(run) -> list[FamilyDiagnostic]:
    return [f for f in run.diagnostics.family_diagnostics if "linkedin" not in f.family.lower()]


def test_two_consecutive_runs_sharing_one_provider_list_do_not_leak_health(offline):
    primary, fallback = SwitchableProvider("mock_primary"), SwitchableProvider("mock_fallback")
    providers = [primary, fallback]          # reused across accounts, as run_phase1c/1d do
    account = OrganizationInput(name="A Wholly Fictitious Test Organization Ltd", country="Testland")

    primary.reject_with = "HTTP 401: bad key"
    first = run_acquisition(account, providers)
    families = _router_families(first)
    assert families, "expected router-driven families in the run"
    for f in families:
        assert "mock_primary" not in f.providers_used          # tripped during organization resolution
        assert f.fallback_reason.startswith("mock_primary skipped: unavailable earlier in this run")
        assert "mock_fallback" in f.providers_used

    primary.reject_with = None               # key fixed between accounts
    second = run_acquisition(OrganizationInput(name="Another Unrelated Synthetic Company"), providers)
    assert router._RUN_HEALTH.get() is None
    for f in _router_families(second):
        assert "mock_primary" in f.providers_used               # healthy again in the new run
        assert not (f.fallback_reason or "").startswith("mock_primary skipped")


def test_a_run_that_raises_does_not_poison_the_next_run(offline, monkeypatch):
    primary, fallback = SwitchableProvider("mock_primary"), SwitchableProvider("mock_fallback")
    providers = [primary, fallback]
    account = OrganizationInput(name="A Wholly Fictitious Test Organization Ltd")

    def explode(*args, **kwargs):
        raise RuntimeError("generic discovery failed")

    primary.reject_with = "HTTP 403: forbidden"
    monkeypatch.setattr(acquisition, "discover_generic_sources", explode)
    with pytest.raises(RuntimeError):
        run_acquisition(account, providers)
    assert router._RUN_HEALTH.get() is None

    monkeypatch.undo()
    monkeypatch.setattr(acquisition, "fetch_page", lambda url: ("", AccessStatus.UNREACHABLE))
    monkeypatch.setattr(acquisition, "crawl_official_site", lambda website, name: [])
    primary.reject_with = None
    run = run_acquisition(account, providers)
    assert all("mock_primary" in f.providers_used for f in _router_families(run))


def test_healthy_run_is_unchanged_by_the_scope(offline):
    providers = [SwitchableProvider("mock_primary"), SwitchableProvider("mock_fallback")]
    run = run_acquisition(OrganizationInput(name="Another Unrelated Synthetic Company"), providers)
    assert run.diagnostics.total_queries > 0
    for f in _router_families(run):
        assert f.providers_used[0] == "mock_primary"
        assert not (f.fallback_reason or "").startswith("mock_primary skipped")

"""Demo Mode failure explanations (ui/demo_mode.py) and the run-level
`skipped_providers` diagnostic they read (research/acquisition.py).

Demo Mode names each failed provider in chain order, picks the hint by
severity (authentication -> quota/plan -> rate limit -> network/timeout ->
server -> malformed/unexpected), reads error lines, fallback_reason and
skipped_providers, and never shows API keys, raw error text, payloads or
search queries. No network: providers are scripted in memory.
"""

from __future__ import annotations

import pytest

import research.acquisition as acquisition
from research.acquisition import FamilyDiagnostic, run_acquisition
from research.analysis.report import build_report
from research.organization import OrganizationInput
from research.providers import registry
from research.providers.base import QueryOutcome, RawResult
from research.providers.router import FamilyOutcome, ProviderFamilyRun, CoverageAssessment
from research.sources import AccessStatus
from ui import demo_mode
from ui.demo_mode import FAILURE_TITLE, failure_from_exception, failure_from_report, failure_html

SECRET = "sk-live-NEVER-SHOW-4c1d9e"
QUERY = '"Acme Holdings" Oman digital transformation'
LABEL = {"brave": "Brave Search", "tavily": "Tavily", "serper": "Serper"}
KEY = {"brave": "BRAVE_SEARCH_API_KEY", "tavily": "TAVILY_API_KEY", "serper": "SERPER_API_KEY"}


def _line(provider: str, error: str, query: str = QUERY, family: str = "generic_news") -> str:
    """Exactly the report format: acquisition prefixes router.FamilyOutcome.errors."""
    return f"[{family}] {provider}: {query!r} -> {error}"


def _report(errors=(), families=None, returned=0) -> dict:
    diag = {"total_results_returned": returned, "errors": list(errors)}
    if families is not None:
        diag["families"] = families
    return {"acquisition_diagnostics": diag}


def _failure(*lines, families=None):
    failure = failure_from_report(_report(lines, families))
    assert failure is not None and failure.title == FAILURE_TITLE
    return failure


# ---------------------------------------------------------------------------
# Registry mirror
# ---------------------------------------------------------------------------

def test_demo_mode_provider_constants_match_the_registry():
    assert demo_mode.SEARCH_PROVIDER_ORDER == registry.SEARCH_PROVIDER_ORDER
    assert demo_mode.SEARCH_PROVIDER_KEYS == registry.SEARCH_PROVIDER_KEYS


def test_no_key_hint_names_every_registry_key_with_the_primary_first():
    failure = failure_from_exception(RuntimeError(
        "No search provider API key is configured: BRAVE_SEARCH_API_KEY, TAVILY_API_KEY and "
        "SERPER_API_KEY are not configured in the environment."))
    assert failure.cause == "The research provider API key is not configured."
    assert failure.hint == ("Set at least one of BRAVE_SEARCH_API_KEY (primary), TAVILY_API_KEY or "
                            "SERPER_API_KEY in the environment, then restart Demo Mode.")
    for key in registry.SEARCH_PROVIDER_KEYS:
        assert key in failure.hint
    assert failure.hint.index(registry.SEARCH_PROVIDER_KEYS[0]) < failure.hint.index(registry.SEARCH_PROVIDER_KEYS[1])


# ---------------------------------------------------------------------------
# 1. Every category, for every provider
# ---------------------------------------------------------------------------

CATEGORY_CASES = [
    # error string, sentence fragment, status shown, hint (with {key} / {label})
    ("HTTP 401: invalid token", "rejected the API key", 401, "Check {key}, then restart Demo Mode."),
    ("HTTP 403: forbidden", "rejected the API key", 403, "Check {key}, then restart Demo Mode."),
    ("HTTP 402: payment required", "reached its usage or plan limit", 402,
     "Check the {label} plan or remaining usage ({key}), then retry."),
    ("HTTP 432: plan limit", "reached its usage or plan limit", 432,
     "Check the {label} plan or remaining usage ({key}), then retry."),
    ("HTTP 433: pay-as-you-go limit", "reached its usage or plan limit", 433,
     "Check the {label} plan or remaining usage ({key}), then retry."),
    ("HTTP 429: too many requests", "hit its rate limit", 429, "Wait a moment, then retry."),
    ("timeout", "did not respond in time", None, "Check the connection and retry."),
    ("transport error: connection reset", "could not be reached (network problem)", None, "Check the connection and retry."),
    ("HTTP 500: boom", "was temporarily unavailable", 500, "Wait a moment, then retry."),
    ("HTTP 502: bad gateway", "was temporarily unavailable", 502, "Wait a moment, then retry."),
    ("HTTP 503: busy", "was temporarily unavailable", 503, "Wait a moment, then retry."),
    ("HTTP 504: gateway timeout", "was temporarily unavailable", 504, "Wait a moment, then retry."),
    ("malformed response: body is not JSON", "returned an unexpected response", None, "Retry the research."),
    ("malformed response: unexpected JSON shape", "returned an unexpected response", None, "Retry the research."),
    ("unexpected error: boom", "returned an unexpected response", None, "Retry the research."),
    ("HTTP 404: not found", "returned an error", 404, "Retry the research."),
]


@pytest.mark.parametrize("provider", ["brave", "tavily", "serper"])
@pytest.mark.parametrize("error,fragment,status,hint", CATEGORY_CASES)
def test_each_failure_category_names_the_provider(provider, error, fragment, status, hint):
    failure = _failure(_line(provider, error))
    suffix = f" (HTTP {status})." if status else "."
    assert failure.cause == f"{LABEL[provider]} {fragment}{suffix}"
    assert failure.hint == hint.format(key=KEY[provider], label=LABEL[provider])


def test_rate_limit_intermediate_wording_is_still_a_rate_limit():
    assert _failure(_line("brave", "HTTP 429, retrying")).cause == "Brave Search hit its rate limit (HTTP 429)."


# ---------------------------------------------------------------------------
# 2. Mixed failures: chain order and severity
# ---------------------------------------------------------------------------

def test_mixed_failures_are_listed_in_chain_order_whatever_the_line_order():
    failure = _failure(
        _line("serper", "timeout"),
        _line("tavily", "HTTP 429: limit"),
        _line("brave", "HTTP 401: bad key"),
    )
    assert failure.cause == ("Brave Search rejected the API key (HTTP 401). Tavily hit its rate limit (HTTP 429). "
                             "Serper did not respond in time.")
    assert failure.hint == "Check BRAVE_SEARCH_API_KEY, then restart Demo Mode."


@pytest.mark.parametrize("brave,tavily,hint", [
    ("HTTP 503: x", "HTTP 401: x", "Check TAVILY_API_KEY, then restart Demo Mode."),               # auth beats server
    ("HTTP 402: x", "HTTP 401: x", "Check TAVILY_API_KEY, then restart Demo Mode."),               # auth beats quota
    ("HTTP 429: x", "HTTP 432: x", "Check the Tavily plan or remaining usage (TAVILY_API_KEY), then retry."),
    ("timeout", "HTTP 429: x", "Wait a moment, then retry."),                                      # rate beats timeout
    ("HTTP 503: x", "transport error: x", "Check the connection and retry."),                      # network beats server
    ("malformed response: body is not JSON", "HTTP 502: x", "Wait a moment, then retry."),        # server beats malformed
])
def test_hint_follows_the_approved_severity_order(brave, tavily, hint):
    assert _failure(_line("brave", brave), _line("tavily", tavily)).hint == hint


def test_providers_sharing_the_most_serious_category_are_all_named_in_the_hint():
    failure = _failure(_line("serper", "HTTP 403: x"), _line("brave", "HTTP 401: x"))
    assert failure.hint == "Check BRAVE_SEARCH_API_KEY and SERPER_API_KEY, then restart Demo Mode."
    failure = _failure(_line("brave", "HTTP 402: x"), _line("tavily", "HTTP 433: x"))
    assert failure.hint == ("Check the Brave Search and Tavily plan or remaining usage "
                            "(BRAVE_SEARCH_API_KEY and TAVILY_API_KEY), then retry.")


def test_one_provider_with_several_failures_is_explained_by_its_most_serious_one():
    failure = _failure(_line("brave", "timeout"), _line("brave", "HTTP 401: x"), _line("brave", "HTTP 503: x"))
    assert failure.cause == "Brave Search rejected the API key (HTTP 401)."


# ---------------------------------------------------------------------------
# 3-4. B5.3 run-level health: skipped providers, real pipeline
# ---------------------------------------------------------------------------

class Scripted:
    supports_rich_snippets = False
    supports_pagination = False

    def __init__(self, name, error=None, results=0):
        self.name, self.error, self.results = name, error, results
        self.calls = 0

    def is_configured(self):
        return True

    def search(self, query, **kwargs):
        self.calls += 1
        if self.error:
            return QueryOutcome(query=query, provider=self.name, error=self.error)
        return QueryOutcome(query=query, provider=self.name, results=[
            RawResult(url=f"https://{self.name}.test/{i}-{abs(hash(query)) % 997}", title="Fictional Org Ltd",
                      snippet="Fictional Org Ltd", query=query, provider=self.name, rank=i)
            for i in range(self.results)])


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(acquisition, "fetch_page", lambda url: ("", AccessStatus.UNREACHABLE))
    monkeypatch.setattr(acquisition, "crawl_official_site", lambda website, name: [])


def _live(chain, org="Fictional Org Ltd"):
    report = build_report(run_acquisition(OrganizationInput(name=org, country="Oman"), chain)).to_dict()
    return report, failure_from_report(report)


@pytest.mark.parametrize("error,cause,hint", [
    ("HTTP 401: invalid key", "Tavily rejected the API key (HTTP 401).", "Check TAVILY_API_KEY, then restart Demo Mode."),
    ("HTTP 432: plan limit", "Tavily reached its usage or plan limit (HTTP 432).",
     "Check the Tavily plan or remaining usage (TAVILY_API_KEY), then retry."),
])
def test_tavily_only_unrecoverable_failure_is_explained(offline, error, cause, hint):
    report, failure = _live([Scripted("tavily", error)])
    diag = report["acquisition_diagnostics"]
    assert diag["errors"] == []                              # the B5.3 skip leaves no error lines ...
    assert any(f["skipped_providers"] == {"tavily": error.split(":")[0]} for f in diag["families"])  # ... but this
    assert failure.cause == cause and failure.hint == hint
    assert "organization name" not in failure.hint


def test_brave_rate_limit_then_tavily_rejected_key_shows_both_causes(offline):
    report, failure = _live([Scripted("brave", "HTTP 429: slow down"), Scripted("tavily", "HTTP 401: bad key")])
    assert failure.cause == "Brave Search hit its rate limit (HTTP 429). Tavily rejected the API key (HTTP 401)."
    assert failure.hint == "Check TAVILY_API_KEY, then restart Demo Mode."
    skipped = [f["skipped_providers"] for f in report["acquisition_diagnostics"]["families"] if f["skipped_providers"]]
    assert skipped and all(s == {"brave": "HTTP 429", "tavily": "HTTP 401"} for s in skipped)


def test_skipped_fallback_is_explained_even_when_a_later_provider_ran(offline):
    # Brave 503 never trips; Tavily 432 trips; Serper is healthy but finds nothing.
    report, failure = _live([Scripted("brave", "HTTP 503: busy"), Scripted("tavily", "HTTP 432: plan"),
                             Scripted("serper")])
    assert failure.cause == ("Brave Search was temporarily unavailable (HTTP 503). "
                             "Tavily reached its usage or plan limit (HTTP 432).")
    assert failure.hint == "Check the Tavily plan or remaining usage (TAVILY_API_KEY), then retry."


def test_skipped_primary_with_no_remaining_fallback(offline):
    report, failure = _live([Scripted("brave", "HTTP 402: quota")])
    assert failure.cause == "Brave Search reached its usage or plan limit (HTTP 402)."
    assert all(f["providers_used"] in ([], ["brave"]) for f in report["acquisition_diagnostics"]["families"])


def test_a_healthy_fallback_with_results_is_a_result_not_a_failure(offline):
    report, failure = _live([Scripted("brave", "HTTP 401: bad key"), Scripted("tavily", results=2)])
    assert report["acquisition_diagnostics"]["total_results_returned"] > 0
    assert failure is None


def test_fallback_reason_alone_is_understood_for_reports_without_skipped_providers():
    families = [{"family": "generic_news", "providers_used": ["tavily"],
                 "fallback_reason": "brave skipped: unavailable earlier in this run (HTTP 401)"}]
    failure = _failure(_line("tavily", "timeout"), families=families)
    assert failure.cause == "Brave Search rejected the API key (HTTP 401). Tavily did not respond in time."
    assert failure.hint == "Check BRAVE_SEARCH_API_KEY, then restart Demo Mode."


def test_ordinary_fallback_reasons_are_not_mistaken_for_failures():
    families = [{"family": "generic_news", "fallback_reason": "primary provider returned zero unique results across the whole family",
                 "skipped_providers": {}}]
    failure = failure_from_report(_report([], families))
    assert failure.cause == "The research providers returned no results for this organization."


# ---------------------------------------------------------------------------
# 5. Organization names and query text never drive the classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    '"Timeout Media" Oman news',
    '"Network International" UAE procurement',
    "\"O'Brien Transport Error Logistics\" timed out HTTP 401 not configured",
    "query with -> arrow and 'quotes'",
])
def test_query_text_is_never_classified(query):
    failure = _failure(_line("brave", "HTTP 503: busy", query=query))
    assert failure.cause == "Brave Search was temporarily unavailable (HTTP 503)."
    assert failure.hint == "Wait a moment, then retry."


@pytest.mark.parametrize("org", ["Timeout Media", "Network International", "Transport Error Holdings"])
def test_organization_names_do_not_change_live_classification(offline, org):
    _, failure = _live([Scripted("brave", "HTTP 503: unavailable")], org=org)
    assert failure.cause == "Brave Search was temporarily unavailable (HTTP 503)."


# ---------------------------------------------------------------------------
# 6. No keys, raw text, payloads or queries in user-facing output
# ---------------------------------------------------------------------------

def test_keys_payloads_and_queries_never_reach_the_user():
    payload = '{"error": {"detail": "token ' + SECRET + ' rejected", "trace": "Traceback (most recent call last)"}}'
    query = f'"Secret Project Falcon" {SECRET}'
    families = [{"skipped_providers": {"tavily": f"HTTP 401 {SECRET}"},
                 "fallback_reason": f"serper skipped: unavailable earlier in this run (HTTP 403 {SECRET})"}]
    failure = _failure(_line("brave", f"HTTP 401: {payload}", query=query), _line("unknownprov", f"transport error: {SECRET}"),
                       families=families)
    shown = failure.title + failure.cause + failure.hint + failure_html(failure, "Acme", "Oman")
    for forbidden in (SECRET, "Secret Project Falcon", "Traceback", '"error"', "rejected\"", "unknownprov", "detail"):
        assert forbidden not in shown, forbidden
    assert "Another research provider" in failure.cause


def test_exception_text_is_never_shown():
    for message in (f"HTTPSConnectionPool: token={SECRET}", f"Read timed out {SECRET}", f"boom {SECRET}"):
        failure = failure_from_exception(RuntimeError(message))
        assert SECRET not in failure.cause + failure.hint and message not in failure.cause + failure.hint


# ---------------------------------------------------------------------------
# 7. Backward compatibility with older reports
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("errors,cause,hint", [
    (["[official] HTTP 401: invalid key"], "The research provider rejected the API key.",
     "Check the configured key, then restart Demo Mode."),
    (["[a] HTTP 429: too many"], "The research provider's usage limit was reached.",
     "Wait a moment or check the provider plan, then retry."),
    (["[a] timeout"], "The research provider did not respond in time.", "Check the connection and retry."),
    (["[a] transport error: x"], "A network problem prevented live research.", "Check the connection and retry."),
    (["[a] HTTP 503: busy"], "The research provider returned errors.", "Retry the research."),
    (["[a] TAVILY_API_KEY not configured"], "The research provider API key is not configured.", None),
    (["no family prefix at all"], "The research provider returned errors.", "Retry the research."),
])
def test_older_lines_without_provider_names_keep_their_original_wording(errors, cause, hint):
    failure = failure_from_report(_report(errors))
    assert failure.cause == cause
    if hint:
        assert failure.hint == hint


def test_older_reports_without_families_or_skipped_providers():
    assert _failure(_line("brave", "HTTP 401: x")).cause == "Brave Search rejected the API key (HTTP 401)."
    families = [{"family": "generic_news", "providers_used": ["brave"], "fallback_reason": None, "error_count": 1}]
    assert _failure(_line("brave", "HTTP 401: x"), families=families).cause == "Brave Search rejected the API key (HTTP 401)."
    assert failure_from_report(_report([], ["not-a-dict", {"skipped_providers": "nonsense"}])).cause == \
        "The research providers returned no results for this organization."


def test_result_and_empty_rules_are_unchanged():
    assert failure_from_report({}) is None
    assert failure_from_report({"acquisition_diagnostics": {"errors": [_line("brave", "HTTP 401: x")]}}) is None
    assert failure_from_report(_report([_line("brave", "HTTP 401: x")], returned=3)) is None
    empty = failure_from_report(_report([]))
    assert (empty.cause, empty.hint) == ("The research providers returned no results for this organization.",
                                         "Check the organization name and country, then retry.")


# ---------------------------------------------------------------------------
# 8. Acquisition diagnostics are preserved; skipped_providers is additive
# ---------------------------------------------------------------------------

def _outcome(provider, query, error=None, results=0):
    return QueryOutcome(query=query, provider=provider, error=error, results=[
        RawResult(url=f"https://{provider}.test/{i}", title="t", snippet="s", query=query, provider=provider, rank=i)
        for i in range(results)])


def test_skipped_providers_is_filled_from_unused_skipped_runs_only():
    skipped = "skipped: unavailable earlier in this run (HTTP 432)"
    fo = FamilyOutcome(
        family="generic_news", queries=["q1", "q2"],
        runs=[
            ProviderFamilyRun("brave", [_outcome("brave", "q1", "HTTP 503: x"), _outcome("brave", "q2", "HTTP 503: x")], True),
            ProviderFamilyRun("tavily", [_outcome("tavily", "q1", skipped), _outcome("tavily", "q2", skipped)], False),
            ProviderFamilyRun("unconfigured", [], False),
            ProviderFamilyRun("serper", [_outcome("serper", "q1", results=1), _outcome("serper", "q2")], True),
        ],
        fallback_triggered=True, fallback_reason="primary provider errored on every query in this family",
        coverage=CoverageAssessment(True, "x", {}),
    )
    diag = FamilyDiagnostic.from_family_outcome(fo)
    assert diag.skipped_providers == {"tavily": "HTTP 432"}
    # every existing field keeps its meaning
    assert diag.providers_used == ["brave", "serper"]
    assert diag.provider_requests == {"brave": 2, "serper": 2}
    assert diag.fallback_reason == "primary provider errored on every query in this family"
    assert diag.errors == ["brave: 'q1' -> HTTP 503: x", "brave: 'q2' -> HTTP 503: x"]
    assert diag.results_returned == 1 and diag.queries_run == 2


EXISTING_FAMILY_KEYS = {"family", "queries_run", "results_returned", "unique_urls", "providers_used",
                        "provider_requests", "fallback_triggered", "fallback_reason", "error_count"}


def test_healthy_run_diagnostics_keep_every_field_and_gain_an_empty_skipped_providers(offline):
    run = run_acquisition(OrganizationInput(name="Fictional Org Ltd", country="Oman"),
                          [Scripted("brave", results=2), Scripted("tavily", results=2)])
    data = run.diagnostics.to_dict()
    for family in data["families"]:
        assert set(family) == EXISTING_FAMILY_KEYS | {"skipped_providers"}
        assert family["skipped_providers"] == {}
    assert data["errors"] == [] and data["providers_used"] == ["brave"]
    assert set(data["provider_requests"]) == {"brave"}


def test_skipped_run_diagnostics_keep_request_and_usage_semantics(offline):
    brave, tavily = Scripted("brave", "HTTP 401: bad key"), Scripted("tavily", results=1)
    data = run_acquisition(OrganizationInput(name="Fictional Org Ltd", country="Oman"), [brave, tavily]).diagnostics.to_dict()
    router_families = [f for f in data["families"] if f["skipped_providers"]]
    assert router_families
    for family in router_families:
        assert family["skipped_providers"] == {"brave": "HTTP 401"}
        assert family["providers_used"] == ["tavily"]
        assert "brave" not in family["provider_requests"]       # skipped queries are not requests
        assert family["fallback_reason"] == "brave skipped: unavailable earlier in this run (HTTP 401)"

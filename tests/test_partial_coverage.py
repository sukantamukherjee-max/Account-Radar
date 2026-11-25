"""Partial provider failure visibility (B5.6).

A run that retrieved results although a provider failed or was skipped by
run-level provider health shows ONE warning above the executive summary and
tabs, in normal and Demo Mode. Healthy runs, successful empty results and
total failures (handled by the existing paths) show no partial warning. The
message names providers, categories, status codes and counts only.

No network: reports are built in memory, live research is replaced by a fake
for the app-level tests, and the acquisition tests use scripted providers.
"""

from __future__ import annotations

import json

import pytest

import research.acquisition as acquisition
from research.acquisition import run_acquisition
from research.analysis.report import build_report
from research.organization import OrganizationInput
from research.providers.base import QueryOutcome, RawResult
from research.sources import AccessStatus
from tests.test_decide_view import INFRA_LEAD, LEADERSHIP, _report
from tests.test_demo_mode import APP, DEMO_APP, ROOT, _analyze, _app_module, _apptest, _html
from ui.demo_mode import (
    PARTIAL_COVERAGE_FOOTER,
    PARTIAL_COVERAGE_TITLE,
    failure_from_report,
    partial_coverage_from_report,
)

SECRET = "sk-live-PARTIAL-SECRET-7a7a"
QUERY = '"Acme Holdings" Oman digital transformation'
LABEL = {"brave": "Brave Search", "tavily": "Tavily"}


def _line(provider, error, query=QUERY, family="generic_news"):
    return f"[{family}] {provider}: {query!r} -> {error}"


def _diag(errors=(), *, returned=12, families=None, requests=None):
    diag = {"total_results_returned": returned, "errors": list(errors)}
    if families is not None:
        diag["families"] = families
    if requests is not None:
        diag["provider_requests"] = requests
    return {"acquisition_diagnostics": diag}


def _coverage(*lines, families=None, requests=None, returned=12):
    coverage = partial_coverage_from_report(_diag(lines, returned=returned, families=families, requests=requests))
    assert coverage is not None
    assert coverage.message.startswith(PARTIAL_COVERAGE_TITLE + " ")
    assert coverage.message.endswith(" " + PARTIAL_COVERAGE_FOOTER)
    return coverage


def _body(message):
    return message[len(PARTIAL_COVERAGE_TITLE) + 1:-len(PARTIAL_COVERAGE_FOOTER) - 1]


# ---------------------------------------------------------------------------
# Report-level: when a warning appears
# ---------------------------------------------------------------------------

def test_healthy_run_has_no_warning():
    assert partial_coverage_from_report(_diag([], requests={"brave": 40})) is None
    families = [{"family": "generic_news", "providers_used": ["brave"], "fallback_reason": None, "skipped_providers": {}}]
    assert partial_coverage_from_report(_diag([], families=families)) is None


def test_one_provider_fails_while_results_were_retrieved():
    coverage = _coverage(*[_line("brave", "HTTP 401: invalid token")] * 4, requests={"brave": 4, "tavily": 4})
    assert _body(coverage.message) == "Brave Search: 4 of 4 searches failed (API key rejected, HTTP 401)."
    assert coverage.failed_providers == ("brave",) and coverage.skipped_providers == ()


@pytest.mark.parametrize("provider", ["brave", "tavily"])
@pytest.mark.parametrize("error,detail", [
    ("HTTP 401: x", "API key rejected, HTTP 401"),
    ("HTTP 403: x", "API key rejected, HTTP 403"),
    ("HTTP 402: x", "usage or plan limit reached, HTTP 402"),
    ("HTTP 432: x", "usage or plan limit reached, HTTP 432"),
    ("HTTP 433: x", "usage or plan limit reached, HTTP 433"),
    ("HTTP 429: x", "rate limit, HTTP 429"),
    ("timeout", "timed out"),
    ("transport error: reset", "network problem"),
    ("HTTP 503: x", "service temporarily unavailable, HTTP 503"),
    ("malformed response: body is not JSON", "unexpected response"),
    ("unexpected error: boom", "unexpected response"),
    ("HTTP 404: x", "provider error, HTTP 404"),
])
def test_every_failure_category_for_every_provider(provider, error, detail):
    coverage = _coverage(_line(provider, error), requests={provider: 10})
    assert _body(coverage.message) == f"{LABEL[provider]}: 1 of 10 searches failed ({detail})."


def test_failed_then_skipped_is_one_combined_sentence():
    families = [{"family": "generic_news", "skipped_providers": {"brave": "HTTP 401"},
                 "fallback_reason": "brave skipped: unavailable earlier in this run (HTTP 401)"}]
    coverage = _coverage(_line("brave", "HTTP 401: x"), _line("brave", "HTTP 401: x"),
                         families=families, requests={"brave": 2})
    assert _body(coverage.message) == ("Brave Search: 2 of 2 searches failed (API key rejected, HTTP 401) "
                                       "and it was then skipped for the rest of the run.")
    assert coverage.failed_providers == ("brave",) and coverage.skipped_providers == ("brave",)
    assert coverage.message.count("Brave Search") == 1


def test_combined_sentence_names_a_different_skip_reason():
    families = [{"skipped_providers": {"brave": "HTTP 401"}}]
    coverage = _coverage(_line("brave", "timeout"), families=families, requests={"brave": 5})
    assert _body(coverage.message) == ("Brave Search: 1 of 5 searches failed (timed out) and it was then skipped "
                                       "for the rest of the run (API key rejected, HTTP 401).")


def test_skipped_only_provider_is_described_as_skipped_not_failed():
    coverage = _coverage(families=[{"skipped_providers": {"tavily": "HTTP 432"}}])
    assert _body(coverage.message) == "Tavily was skipped for the rest of the run (usage or plan limit reached, HTTP 432)."
    assert coverage.failed_providers == () and coverage.skipped_providers == ("tavily",)


def test_skip_known_only_from_fallback_reason_in_older_b53_reports():
    families = [{"family": "generic_news", "fallback_reason": "brave skipped: unavailable earlier in this run (HTTP 429)"}]
    coverage = _coverage(families=families)
    assert _body(coverage.message) == "Brave Search was skipped for the rest of the run (rate limit, HTTP 429)."


def test_multiple_affected_providers_are_reported_separately_in_chain_order():
    families = [{"skipped_providers": {"tavily": "HTTP 432"}}]
    coverage = _coverage(_line("serper", "timeout"), _line("brave", "HTTP 429: x"), _line("brave", "HTTP 429: x"),
                         families=families, requests={"brave": 50, "serper": 9})
    assert _body(coverage.message) == (
        "Brave Search: 2 of 50 searches failed (rate limit, HTTP 429). "
        "Tavily was skipped for the rest of the run (usage or plan limit reached, HTTP 432). "
        "Another research provider: 1 of 9 searches failed (timed out).")   # Serper: no longer in the chain
    assert coverage.failed_providers == ("brave", "serper") and coverage.skipped_providers == ("tavily",)


def test_mixed_categories_name_the_most_serious_and_count_all_failures():
    coverage = _coverage(_line("brave", "timeout"), _line("brave", "HTTP 429: x"), _line("brave", "HTTP 503: x"),
                         requests={"brave": 52})
    assert _body(coverage.message) == "Brave Search: 3 of 52 searches failed (most serious: rate limit, HTTP 429)."


@pytest.mark.parametrize("requests,expected", [
    (None, "Brave Search: 2 searches failed (timed out)."),
    ({}, "Brave Search: 2 searches failed (timed out)."),
    ({"brave": 1}, "Brave Search: 2 searches failed (timed out)."),       # inconsistent total: not shown
    ({"brave": "12"}, "Brave Search: 2 searches failed (timed out)."),
    ({"brave": 2}, "Brave Search: 2 of 2 searches failed (timed out)."),
])
def test_totals_are_shown_only_when_available_and_consistent(requests, expected):
    coverage = _coverage(_line("brave", "timeout"), _line("brave", "timeout"), requests=requests)
    assert _body(coverage.message) == expected


def test_a_single_failure_without_a_total_is_singular():
    assert _body(_coverage(_line("brave", "timeout")).message) == "Brave Search: 1 search failed (timed out)."


# ---------------------------------------------------------------------------
# Report-level: what is never a partial failure
# ---------------------------------------------------------------------------

def test_total_failure_is_left_to_the_existing_path_unchanged():
    report = _diag([_line("brave", "HTTP 401: x")], returned=0, families=[{"skipped_providers": {"tavily": "HTTP 432"}}])
    assert partial_coverage_from_report(report) is None
    failure = failure_from_report(report)                   # B5.4 behaviour, unchanged
    assert failure.cause == "Brave Search rejected the API key (HTTP 401). Tavily reached its usage or plan limit (HTTP 432)."


def test_successful_empty_results_are_never_failures():
    families = [{"family": "generic_news", "providers_used": ["brave", "tavily"],
                 "fallback_reason": "primary provider returned zero unique results across the whole family",
                 "skipped_providers": {}}]
    assert partial_coverage_from_report(_diag([], families=families, requests={"brave": 8, "tavily": 8})) is None


def test_not_configured_lines_are_not_coverage_failures():
    report = _diag([_line("tavily", "TAVILY_API_KEY not configured"), "[a] SERPER_API_KEY not configured"])
    assert partial_coverage_from_report(report) is None


def test_query_text_is_never_classified():
    for query in ('"Timeout Media" Oman', '"Network International" timed out HTTP 401 not configured'):
        coverage = _coverage(_line("brave", "HTTP 503: x", query=query))
        assert _body(coverage.message) == "Brave Search: 1 search failed (service temporarily unavailable, HTTP 503)."


# ---------------------------------------------------------------------------
# Backward compatibility and missing diagnostics
# ---------------------------------------------------------------------------

def test_older_lines_without_provider_names_give_a_general_sentence():
    coverage = _coverage("[official] HTTP 429: too many", "[news] timeout")
    assert _body(coverage.message) == "Some searches returned errors (2)."
    assert coverage.failed_providers == () and coverage.skipped_providers == ()


@pytest.mark.parametrize("report", [
    {},
    {"acquisition_diagnostics": None},
    {"acquisition_diagnostics": {}},
    {"acquisition_diagnostics": {"errors": ["[a] HTTP 401: x"]}},                       # no total
    {"acquisition_diagnostics": {"total_results_returned": None, "errors": ["[a] HTTP 401: x"]}},
    {"acquisition_diagnostics": {"total_results_returned": "12", "errors": ["[a] HTTP 401: x"]}},
    {"acquisition_diagnostics": {"total_results_returned": True, "errors": ["[a] HTTP 401: x"]}},
    {"acquisition_diagnostics": {"total_results_returned": 12}},
    {"acquisition_diagnostics": {"total_results_returned": 12, "errors": None, "families": None}},
    {"acquisition_diagnostics": {"total_results_returned": 12, "families": ["not-a-dict", {"skipped_providers": "x"}]}},
])
def test_reports_without_usable_diagnostics_show_no_warning(report):
    assert partial_coverage_from_report(report) is None


def test_stored_reports_in_the_repository_show_no_warning():
    payload = json.loads((ROOT / "phase1d_report.json").read_text(encoding="utf-8"))
    for entry in payload["organizations"]:
        assert partial_coverage_from_report(entry.get("report", entry)) is None


# ---------------------------------------------------------------------------
# Sensitive content never reaches the message
# ---------------------------------------------------------------------------

def test_keys_payloads_and_queries_never_reach_the_message():
    payload = '{"error": {"detail": "token ' + SECRET + ' rejected", "trace": "Traceback (most recent call last)"}}'
    families = [{"skipped_providers": {"tavily": f"HTTP 432 {SECRET}"},
                 "fallback_reason": f"serper skipped: unavailable earlier in this run (HTTP 403 {SECRET})"}]
    coverage = _coverage(_line("brave", f"HTTP 401: {payload}", query=f'"Secret Project Falcon" {SECRET}'),
                         _line("unknownprov", f"transport error: {SECRET}"), families=families)
    for forbidden in (SECRET, "Secret Project Falcon", "Traceback", "detail", "unknownprov", '"error"'):
        assert forbidden not in coverage.message, forbidden
    assert "Another research provider" in coverage.message


# ---------------------------------------------------------------------------
# Real pipeline (scripted providers, no network)
# ---------------------------------------------------------------------------

class Scripted:
    supports_rich_snippets = False
    supports_pagination = False

    def __init__(self, name, error=None, results=0):
        self.name, self.error, self.results = name, error, results

    def is_configured(self):
        return True

    def search(self, query, **kwargs):
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


def _live_report(chain):
    return build_report(run_acquisition(OrganizationInput(name="Fictional Org Ltd", country="Oman"), chain)).to_dict()


def test_pipeline_healthy_run_has_no_warning(offline):
    assert partial_coverage_from_report(_live_report([Scripted("brave", results=2), Scripted("tavily", results=2)])) is None


def test_pipeline_failed_then_skipped_primary_with_working_fallback(offline):
    coverage = partial_coverage_from_report(_live_report([Scripted("brave", "HTTP 401: bad"), Scripted("tavily", results=2)]))
    assert coverage is not None and coverage.failed_providers == ("brave",) and coverage.skipped_providers == ("brave",)
    assert "Brave Search:" in coverage.message and "and it was then skipped for the rest of the run" in coverage.message
    assert "Tavily" not in coverage.message                  # never claims which provider supplied the results


def test_pipeline_skipped_fallback_is_reported_as_skipped(offline):
    report = _live_report([Scripted("brave", "HTTP 429: slow"), Scripted("tavily", "HTTP 432: plan"), Scripted("serper", results=2)])
    coverage = partial_coverage_from_report(report)
    assert coverage.skipped_providers == ("brave", "tavily")
    assert "Tavily was skipped for the rest of the run (usage or plan limit reached, HTTP 432)." in coverage.message


def test_pipeline_empty_primary_without_errors_is_not_a_failure(offline):
    assert partial_coverage_from_report(_live_report([Scripted("brave", results=0), Scripted("tavily", results=2)])) is None


def test_pipeline_total_failure_shows_no_partial_warning(offline):
    report = _live_report([Scripted("brave", "HTTP 401: bad")])
    assert partial_coverage_from_report(report) is None and failure_from_report(report) is not None


# ---------------------------------------------------------------------------
# App level: one warning above the executive summary and tabs, both modes
# ---------------------------------------------------------------------------

def _entry(org="Partial Org", *, results=12, errors=(), families=None, requests=None):
    diag = {"total_queries": 6, "total_results_returned": results, "errors": list(errors)}
    if families is not None:
        diag["families"] = families
    if requests is not None:
        diag["provider_requests"] = requests
    report = _report(organization_name=org, country="Oman", signals=LEADERSHIP, people=[INFRA_LEAD],
                     acquisition_diagnostics=diag)
    from ui import demo_mode
    return {"report": report, "ai_synthesis": {}, "opportunity_hypotheses": [], "researched_at": demo_mode.researched_at_iso()}


class _Live:
    def __init__(self, entry):
        self.entry = entry

    def __call__(self, name, country, *, progress=None, reuse_verified_identity=True):
        if isinstance(self.entry, BaseException):
            raise self.entry
        return self.entry


@pytest.fixture
def live(monkeypatch):
    app = _app_module()

    def install(entry):
        monkeypatch.setattr(app, "_run_live", _Live(entry))
    return install


PARTIAL = dict(errors=[_line("brave", f"HTTP 401: token {SECRET}")] * 3,
               families=[{"family": "generic_news", "skipped_providers": {"brave": "HTTP 401"}}],
               requests={"brave": 3, "tavily": 6})
PARTIAL_TEXT = ("Research coverage may be incomplete. Brave Search: 3 of 3 searches failed (API key rejected, HTTP 401) "
                "and it was then skipped for the rest of the run. Results shown come from the providers that responded.")


def _warnings(at):
    return [w.value for w in at.warning]


def _normal(entry):
    """Normal mode: load the entry the way the app stores a live result."""
    at = _apptest(APP)
    at.session_state["entry"] = entry
    at.session_state["loaded_from"] = "live research"
    return at.run()


def _demo(live, entry, org="Partial Org"):
    """Demo Mode: Analyze through the real app path with a fake live run."""
    live(entry)
    return _analyze(_apptest(DEMO_APP).run(), org, "Oman")


def test_normal_mode_partial_failure_shows_one_warning():
    at = _normal(_entry(**PARTIAL))
    assert not at.exception
    assert _warnings(at) == [PARTIAL_TEXT]
    assert SECRET not in " ".join(_warnings(at)) + _html(at)


def test_demo_mode_partial_failure_shows_the_result_and_one_warning(live):
    at = _demo(live, _entry(**PARTIAL))
    assert not at.exception
    assert _warnings(at) == [PARTIAL_TEXT]
    assert "entry" in at.session_state                        # the result is shown, not a failure
    assert "Live research could not be completed" not in _html(at)
    assert "LIVE RESEARCH" in _html(at)                       # the Demo status strip is still shown
    assert SECRET not in " ".join(_warnings(at)) + _html(at)


def test_healthy_run_shows_no_warning_in_either_mode(live):
    at = _normal(_entry())
    assert not at.exception and _warnings(at) == []
    at = _demo(live, _entry(), org="Healthy Org")
    assert not at.exception and _warnings(at) == []


def test_warning_sits_above_the_executive_summary_and_tabs():
    at = _normal(_entry(**PARTIAL))
    elements = list(at.main)
    kinds = [type(el).__name__ for el in elements]
    first_warning = kinds.index("Warning")
    exec_index = next(i for i, el in enumerate(elements)
                      if kinds[i] == "Markdown" and 'class="ar-exec"' in str(el.value))
    assert first_warning < exec_index
    assert "Tabs" not in kinds[:first_warning]
    assert kinds.count("Warning") == 1


def test_partial_warning_coexists_with_the_insufficient_evidence_warning():
    entry = _entry(**PARTIAL)
    entry["report"]["status"] = "insufficient_evidence"
    entry["report"].setdefault("qualification", {})["corpus_reason"] = "Only two qualified sources."
    at = _normal(entry)
    assert _warnings(at) == [PARTIAL_TEXT, "Only two qualified sources."]


def test_demo_total_failure_is_unchanged(live):
    at = _demo(live, _entry("Empty Org", results=0, errors=[_line("brave", "HTTP 401: bad")]), org="Empty Org")
    assert "entry" not in at.session_state
    assert "Brave Search rejected the API key (HTTP 401)." in _html(at)
    assert _warnings(at) == [] and [b.label for b in at.main.button] == ["Retry research"]


def test_normal_mode_zero_result_run_shows_no_partial_warning():
    at = _normal(_entry("Empty Org", results=0, errors=[_line("brave", "HTTP 401: bad")]))
    assert not at.exception
    assert not any(w.startswith(PARTIAL_COVERAGE_TITLE) for w in _warnings(at))

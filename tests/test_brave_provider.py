"""Brave Search API provider and the Brave -> Tavily -> Serper chain.

No network: Brave HTTP responses come from a fake session, and the router
tests use small in-memory providers that count their calls.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest
import requests

from research.providers import brave as brave_module
from research.providers.base import QueryOutcome, RawResult, SearchProvider
from research.providers.brave import BRAVE_ENDPOINT, BraveSearchProvider
from research.providers.registry import SEARCH_PROVIDER_ORDER, build_search_providers
from research.providers.router import acquire_family, default_coverage_assessment

import streamlit as _REAL_STREAMLIT  # noqa: E402  (see tests/test_theme_engine.py)

ROOT = pathlib.Path(__file__).resolve().parents[1]
DUMMY_KEY = "brave-test-key-not-real"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, body=None, text="", headers=None, bad_json=False):
        self.status_code = status
        self._body = body
        self.text = text
        self.headers = headers or {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


class FakeSession:
    """Returns queued responses (or raises queued exceptions) per GET."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _brave(*responses, retries=2):
    session = FakeSession(*responses)
    provider = BraveSearchProvider(api_key=DUMMY_KEY, session=session, max_retries=retries, backoff_base=0.0)
    return provider, session


def _web(*items):
    return {"type": "search", "web": {"type": "search", "results": list(items)}}


def _item(n, **extra):
    base = {"title": f"Result <strong>{n}</strong> &amp; more", "url": f"https://example{n}.test/page",
            "description": f"Snippet <strong>{n}</strong> text", "page_age": f"2026-09-0{n}T00:00:00"}
    base.update(extra)
    return base


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(brave_module.time, "sleep", lambda s: None)


# ---------------------------------------------------------------------------
# 1-3. Recognition, configuration, normalization
# ---------------------------------------------------------------------------

def test_brave_provider_is_recognized_and_first_in_the_chain(monkeypatch):
    provider = BraveSearchProvider(api_key=DUMMY_KEY)
    assert isinstance(provider, SearchProvider)
    assert provider.name == "brave"
    assert SEARCH_PROVIDER_ORDER == ("brave", "tavily", "serper")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", DUMMY_KEY)
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-test")
    monkeypatch.setenv("SERPER_API_KEY", "serper-test")
    assert [p.name for p in build_search_providers()] == ["brave", "tavily", "serper"]


def test_missing_key_means_unconfigured_and_no_request(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    session = FakeSession()
    provider = BraveSearchProvider(session=session)
    assert provider.is_configured() is False
    outcome = provider.search("any query")
    assert outcome.error == "BRAVE_SEARCH_API_KEY not configured" and outcome.results == []
    assert session.calls == []


def test_key_is_read_only_from_brave_search_api_key(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", DUMMY_KEY)
    assert BraveSearchProvider().is_configured() is False
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", DUMMY_KEY)
    assert BraveSearchProvider().is_configured() is True


def test_valid_response_converts_to_raw_results():
    provider, session = _brave(FakeResponse(body=_web(_item(1))))
    outcome = provider.search("acme cloud", num_results=10)
    assert outcome.ok
    assert outcome.results == [RawResult(url="https://example1.test/page", title="Result 1 & more",
                                         snippet="Snippet 1 text", query="acme cloud", provider="brave",
                                         rank=0, published_at="2026-09-01T00:00:00")]
    call = session.calls[0]
    assert call["url"] == BRAVE_ENDPOINT == "https://api.search.brave.com/res/v1/web/search"
    assert call["params"] == {"q": "acme cloud", "count": 10}
    assert call["headers"]["X-Subscription-Token"] == DUMMY_KEY
    assert "answers" not in call["url"] and "summarizer" not in call["url"]


def test_count_is_clamped_to_brave_maximum():
    provider, session = _brave(FakeResponse(body=_web()))
    provider.search("q", num_results=50)
    assert session.calls[0]["params"]["count"] == 20


# ---------------------------------------------------------------------------
# 4-5. Rank, empty and malformed results
# ---------------------------------------------------------------------------

def test_multiple_results_preserve_rank_and_skip_malformed_items():
    provider, _ = _brave(FakeResponse(body=_web(_item(1), {"title": "no url"}, "garbage", _item(2),
                                                {"url": "javascript:alert(1)"}, _item(3))))
    results = provider.search("q").results
    assert [r.url for r in results] == [f"https://example{n}.test/page" for n in (1, 2, 3)]
    assert [r.rank for r in results] == [0, 1, 2]
    assert {r.provider for r in results} == {"brave"}


@pytest.mark.parametrize("body", [{"type": "search"}, _web(), {"web": {"results": None}}])
def test_genuine_zero_results_are_empty_not_an_error(body):
    provider, _ = _brave(FakeResponse(body=body))
    outcome = provider.search("q")
    assert outcome.ok and outcome.results == []


@pytest.mark.parametrize("response", [FakeResponse(bad_json=True), FakeResponse(body=["not", "a", "dict"])])
def test_malformed_body_is_an_error_not_empty_evidence(response):
    provider, _ = _brave(response)
    outcome = provider.search("q")
    assert not outcome.ok and outcome.error.startswith("malformed response")


# ---------------------------------------------------------------------------
# 6-9. HTTP errors, rate limits, timeouts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_fail_cleanly_without_retry_or_key_leak(status):
    provider, session = _brave(FakeResponse(status=status, text=f"invalid token {DUMMY_KEY}"))
    outcome = provider.search("q")
    assert outcome.error.startswith(f"HTTP {status}:")
    assert DUMMY_KEY not in outcome.error
    assert len(session.calls) == 1


def test_429_is_retried_then_reported():
    provider, session = _brave(FakeResponse(status=429, headers={"Retry-After": "1"}),
                               FakeResponse(status=429), FakeResponse(status=429, text="rate limited"))
    outcome = provider.search("q")
    assert outcome.error.startswith("HTTP 429:") and len(session.calls) == 3


def test_429_then_success_returns_results():
    provider, _ = _brave(FakeResponse(status=429), FakeResponse(body=_web(_item(1))))
    outcome = provider.search("q")
    assert outcome.ok and len(outcome.results) == 1


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_retried_then_reported(status):
    provider, session = _brave(*(FakeResponse(status=status, text="server") for _ in range(3)))
    outcome = provider.search("q")
    assert outcome.error.startswith(f"HTTP {status}:") and len(session.calls) == 3


def test_timeout_is_handled():
    provider, session = _brave(*(requests.exceptions.Timeout() for _ in range(3)))
    outcome = provider.search("q")
    assert outcome.error == "timeout" and outcome.results == [] and len(session.calls) == 3


def test_network_failure_is_handled_and_redacted():
    provider, _ = _brave(*(requests.exceptions.ConnectionError(f"boom {DUMMY_KEY}") for _ in range(3)))
    outcome = provider.search("q")
    assert outcome.error.startswith("transport error") and DUMMY_KEY not in outcome.error


# ---------------------------------------------------------------------------
# 10-15. Router: Brave -> Tavily -> Serper with family-level semantics
# ---------------------------------------------------------------------------

class ScriptedProvider:
    """In-memory provider: mode is 'healthy', 'empty', 'error' or 'unconfigured'."""

    def __init__(self, name, mode="healthy"):
        self.name = name
        self.mode = mode
        self.calls = []

    def is_configured(self):
        return self.mode != "unconfigured"

    def search(self, query, *, num_results=10):
        self.calls.append(query)
        if self.mode == "error":
            return QueryOutcome(query=query, provider=self.name, error="HTTP 503: unavailable")
        if self.mode == "empty":
            return QueryOutcome(query=query, provider=self.name)
        url = f"https://{self.name}.test/{abs(hash(query)) % 10_000}"
        return QueryOutcome(query=query, provider=self.name,
                            results=[RawResult(url=url, title="t", snippet="s", query=query, provider=self.name, rank=0)])


QUERIES = [f"family query {i}" for i in range(5)]


def _chain(brave="healthy", tavily="healthy", serper="healthy"):
    return [ScriptedProvider("brave", brave), ScriptedProvider("tavily", tavily), ScriptedProvider("serper", serper)]


def test_router_prefers_brave_and_healthy_brave_does_not_call_tavily_or_serper():
    providers = _chain()
    outcome = acquire_family("f", QUERIES, providers)
    assert outcome.providers_used == ["brave"] and not outcome.fallback_triggered
    assert len(providers[0].calls) == 5 and providers[1].calls == [] and providers[2].calls == []
    assert outcome.provider_requests == {"brave": 5}


@pytest.mark.parametrize("mode", ["unconfigured", "error", "empty"])
def test_router_falls_back_brave_to_tavily(mode):
    providers = _chain(brave=mode)
    outcome = acquire_family("f", QUERIES, providers)
    assert outcome.fallback_triggered
    assert "tavily" in outcome.providers_used and providers[2].calls == []    # healthy Tavily: no Serper
    assert outcome.results and {r.provider for r in outcome.results} == {"tavily"}


def test_router_falls_back_brave_tavily_serper_when_required():
    providers = _chain(brave="error", tavily="error")                         # e.g. Tavily out of credits
    outcome = acquire_family("f", QUERIES, providers)
    assert outcome.providers_used == ["brave", "tavily", "serper"]
    assert outcome.provider_requests == {"brave": 5, "tavily": 5, "serper": 5}
    assert {r.provider for r in outcome.results} == {"serper"}


def test_unconfigured_providers_cost_nothing_and_are_skipped():
    providers = _chain(brave="unconfigured", tavily="unconfigured")
    outcome = acquire_family("f", QUERIES, providers)
    assert providers[0].calls == [] and providers[1].calls == []
    assert outcome.provider_requests == {"serper": 5}


def test_a_single_sparse_query_does_not_trigger_any_fallback():
    class OneEmpty(ScriptedProvider):
        def search(self, query, *, num_results=10):
            if query == QUERIES[0]:
                self.calls.append(query)
                return QueryOutcome(query=query, provider=self.name)
            return super().search(query, num_results=num_results)

    providers = [OneEmpty("brave"), ScriptedProvider("tavily"), ScriptedProvider("serper")]
    outcome = acquire_family("f", QUERIES, providers)
    assert outcome.providers_used == ["brave"] and providers[1].calls == []


def test_family_level_coverage_rule_is_unchanged():
    ok = QueryOutcome(query="a", provider="p", results=[RawResult("https://x.test", "t", "s", "a", "p", 0)])
    err = QueryOutcome(query="b", provider="p", error="HTTP 500")
    assert default_coverage_assessment([ok, err]).sparse is False                  # 1/2 errors
    assert default_coverage_assessment([ok, err, err]).sparse is True              # >= 60% errored
    assert default_coverage_assessment([QueryOutcome(query="c", provider="p")]).sparse is True


def test_providers_used_and_requests_reach_acquisition_diagnostics():
    from research.acquisition import AcquisitionDiagnostics, FamilyDiagnostic
    families = [FamilyDiagnostic.from_family_outcome(acquire_family("a", QUERIES, _chain())),
                FamilyDiagnostic.from_family_outcome(acquire_family("b", QUERIES, _chain(brave="error")))]
    diag = AcquisitionDiagnostics(organization_name="Any Org", resolved_website=None,
                                  resolved_linkedin_company_url=None, started_at=0, finished_at=1,
                                  family_diagnostics=families)
    data = diag.to_dict()
    assert data["providers_used"] == ["brave", "tavily"]
    assert data["provider_requests"] == {"brave": 10, "tavily": 5}
    assert data["families"][1]["provider_requests"] == {"brave": 5, "tavily": 5}


# ---------------------------------------------------------------------------
# 16-17. No hard-coded keys or organizations; chain wiring in the app
# ---------------------------------------------------------------------------

def test_no_hardcoded_api_keys_in_provider_code():
    for path in (ROOT / "research" / "providers").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?i)(api[_-]?key|token)\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]", text), path.name
        assert not re.search(r"\bBSA[A-Za-z0-9_\-]{20,}", text), path.name          # Brave key shape
        assert not re.search(r"\btvly-[A-Za-z0-9]{10,}", text), path.name


def test_no_hardcoded_organization_names_in_new_provider_modules():
    for name in ("brave.py", "registry.py"):
        text = (ROOT / "research" / "providers" / name).read_text(encoding="utf-8")
        for org in ("Oman", "Ooredoo", "Omantel", "Vodafone", "Airports"):
            assert org not in text, (name, org)
        tree = ast.parse(text)
        literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert not [v for v in literals if v.endswith((" LLC", " SAOG", " Company"))]


def test_live_research_uses_the_registry_chain_without_openrouter_search(monkeypatch):
    sys.modules["streamlit"] = _REAL_STREAMLIT
    import app

    seen = {}

    class Stop(Exception):
        pass

    def spy(org, providers):
        seen["providers"] = [p.name for p in providers]
        raise Stop

    monkeypatch.setattr(app, "run_acquisition", spy)
    monkeypatch.setattr(app.st, "session_state", {})
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", DUMMY_KEY)
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-test")
    monkeypatch.setenv("SERPER_API_KEY", "serper-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test")
    with pytest.raises(Stop):
        app._run_live("Any Org", "Anywhere")
    assert seen["providers"] == ["brave", "tavily", "serper"]

    monkeypatch.delenv("TAVILY_API_KEY")
    with pytest.raises(Stop):
        app._run_live("Any Org", "Anywhere")
    assert seen["providers"] == ["brave", "serper"]

    for var in ("BRAVE_SEARCH_API_KEY", "SERPER_API_KEY"):
        monkeypatch.delenv(var)
    with pytest.raises(RuntimeError):
        app._run_live("Any Org", "Anywhere")


def test_demo_launcher_has_no_provider_hardcoding():
    text = (ROOT / "demo" / "demo_app.py").read_text(encoding="utf-8")
    for name in ("Brave", "Tavily", "Serper", "OpenRouter", "providers"):
        assert name not in text

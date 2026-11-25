"""Failure-contract tests for the fallback search providers (Tavily, Serper).

Brave (tests/test_brave_provider.py) defines the contract every provider in
the Brave -> Tavily -> Serper chain follows:
  * missing key          -> "<KEY> not configured", no request
  * 4xx (not 429)        -> "HTTP <code>: ...", exactly one request
  * 429 / 5xx            -> bounded retries, then "HTTP <code>: ..."
  * timeout / transport  -> bounded retries, then "timeout" / "transport error: ..."
  * malformed 200 body   -> "malformed response: ...", exactly one request
  * genuine zero results -> empty result list, not an error
  * search() never raises, and the API key never appears in an error.

No network: every provider gets a fake session; time.sleep is recorded,
never slept.
"""

from __future__ import annotations

import pytest
import requests

from research.providers import brave as brave_module
from research.providers import serper as serper_module
from research.providers import tavily as tavily_module
from research.providers.base import RawResult, SearchProvider
from research.providers.brave import BraveSearchProvider
from research.providers.serper import SERPER_ENDPOINT, SerperProvider
from research.providers.tavily import TAVILY_ENDPOINT, TavilyProvider

KEY = "test-key-NOT-REAL-7f3a9c"


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
            # What requests raises for a non-JSON body: a ValueError that is
            # ALSO a RequestException (the Serper misclassification case).
            raise requests.exceptions.JSONDecodeError("Expecting value", self.text, 0)
        return self._body


class FakeSession:
    """Returns queued responses (or raises queued exceptions) per request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def post(self, url, **kwargs):
        return self._next("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._next("GET", url, kwargs)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    recorded: list[float] = []
    for module in (tavily_module, serper_module, brave_module):
        monkeypatch.setattr(module.time, "sleep", recorded.append)
    return recorded


def _tavily(*responses, retries=2):
    session = FakeSession(*responses)
    return TavilyProvider(api_key=KEY, session=session, max_retries=retries), session


def _serper(*responses, retries=2):
    session = FakeSession(*responses)
    return SerperProvider(api_key=KEY, session=session, max_retries=retries), session


def _brave(*responses, retries=2):
    session = FakeSession(*responses)
    return BraveSearchProvider(api_key=KEY, session=session, max_retries=retries), session


# Healthy, empty 200 bodies per provider.
EMPTY_BODY = {"tavily": {"results": []}, "serper": {"organic": []}, "brave": {"web": {"results": []}}}
MAKERS = {"tavily": _tavily, "serper": _serper, "brave": _brave}
FALLBACKS = ("tavily", "serper")


# ---------------------------------------------------------------------------
# Configuration and defaults
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls,env", [(TavilyProvider, "TAVILY_API_KEY"), (SerperProvider, "SERPER_API_KEY")])
def test_missing_key_is_unconfigured_and_sends_no_request(cls, env):
    session = FakeSession()
    provider = cls(session=session)
    assert isinstance(provider, SearchProvider)
    assert provider.is_configured() is False
    outcome = provider.search("anything")
    assert outcome.error == f"{env} not configured" and outcome.results == []
    assert session.calls == []


@pytest.mark.parametrize("cls,env", [(TavilyProvider, "TAVILY_API_KEY"), (SerperProvider, "SERPER_API_KEY")])
def test_key_is_read_from_environment(monkeypatch, cls, env):
    monkeypatch.setenv(env, f"  {KEY}  ")
    assert cls().is_configured() is True


def test_tavily_defaults_ten_second_timeout_and_two_retries():
    provider, session = _tavily(FakeResponse(body={"results": []}))
    assert provider.timeout == 10.0 and provider._max_retries == 2
    provider.search("q")
    call = session.calls[0]
    assert call["url"] == TAVILY_ENDPOINT and call["timeout"] == 10.0
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["json"] == {"query": "q", "search_depth": "basic", "max_results": 10}


def test_serper_timeout_and_retry_defaults_are_unchanged():
    provider = SerperProvider(api_key=KEY)
    assert provider._timeout == 8.0 and provider._max_retries == 2 and provider._backoff_base == 0.75


# ---------------------------------------------------------------------------
# Successful parsing
# ---------------------------------------------------------------------------

def test_tavily_valid_response_converts_to_raw_results():
    body = {"results": [
        {"url": " https://a.test/1 ", "title": " A ", "content": " body A ", "published_date": "2026-09-01"},
        "not-a-dict",
        {"url": "", "title": "no url"},
        {"url": "https://b.test/2", "title": "B", "snippet": "snippet B"},
    ]}
    provider, session = _tavily(FakeResponse(body=body))
    outcome = provider.search("q", num_results=50)
    assert outcome.ok and len(session.calls) == 1
    assert session.calls[0]["json"]["max_results"] == 20  # clamped
    assert outcome.results == [
        RawResult(url="https://a.test/1", title="A", snippet="body A", query="q", provider="tavily",
                  rank=0, published_at="2026-09-01"),
        RawResult(url="https://b.test/2", title="B", snippet="snippet B", query="q", provider="tavily",
                  rank=3, published_at=None),
    ]


def test_serper_valid_response_converts_to_raw_results_and_knowledge_graph():
    body = {
        "organic": [
            {"link": "https://a.test/1", "title": "A", "snippet": "sa", "date": "Sep 1, 2026"},
            "not-a-dict",
            {"link": "https://b.test/2", "title": "B", "snippet": "sb"},
        ],
        "knowledgeGraph": {"website": "https://org.test", "title": "Org", "description": "Org desc"},
    }
    provider, session = _serper(FakeResponse(body=body))
    outcome = provider.search("q", num_results=500)
    assert outcome.ok and len(session.calls) == 1
    assert session.calls[0]["url"] == SERPER_ENDPOINT and session.calls[0]["json"]["num"] == 100
    assert [(r.url, r.provider, r.rank) for r in outcome.results] == [
        ("https://a.test/1", "serper", 0),
        ("https://b.test/2", "serper", 1),
        ("https://org.test", "serper_knowledge_graph", -1),
    ]
    assert outcome.results[0].published_at == "Sep 1, 2026"


@pytest.mark.parametrize("body", [
    {"organic": "not-a-list"},
    {"organic": None, "knowledgeGraph": "not-a-dict"},
    {"organic": [{"title": "no link"}], "knowledgeGraph": {"title": "no website"}},
])
def test_serper_invalid_shapes_inside_a_dict_body_are_skipped_not_raised(body):
    provider, _ = _serper(FakeResponse(body=body))
    outcome = provider.search("q")
    assert outcome.ok and outcome.results == []


@pytest.mark.parametrize("body", [{"results": "not-a-list"}, {"results": None}, {"answer": "x"}])
def test_tavily_invalid_shapes_inside_a_dict_body_are_skipped_not_raised(body):
    provider, _ = _tavily(FakeResponse(body=body))
    outcome = provider.search("q")
    assert outcome.ok and outcome.results == []


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", FALLBACKS)
@pytest.mark.parametrize("first", [
    FakeResponse(status=429, text="slow down"),
    FakeResponse(status=503, text="busy"),
    requests.exceptions.Timeout("read timed out"),
    requests.exceptions.ConnectionError("reset"),
])
def test_transient_failure_then_success_is_retried(name, first, sleeps):
    provider, session = MAKERS[name](first, FakeResponse(body=EMPTY_BODY[name]))
    outcome = provider.search("q")
    assert outcome.ok and outcome.results == []
    assert len(session.calls) == 2 and len(sleeps) == 1


@pytest.mark.parametrize("name", FALLBACKS)
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_persistent_retryable_status_stops_after_max_retries(name, status):
    provider, session = MAKERS[name](*[FakeResponse(status=status, text="nope")] * 3)
    outcome = provider.search("q")
    assert outcome.error == f"HTTP {status}: nope"
    assert len(session.calls) == 3


@pytest.mark.parametrize("name", FALLBACKS)
def test_persistent_timeout_and_transport_errors_stop_after_max_retries(name):
    provider, session = MAKERS[name](*[requests.exceptions.Timeout("t")] * 3)
    assert provider.search("q").error == "timeout" and len(session.calls) == 3
    provider, session = MAKERS[name](*[requests.exceptions.ConnectionError("dns failure")] * 3)
    assert provider.search("q").error == "transport error: dns failure" and len(session.calls) == 3


@pytest.mark.parametrize("name", FALLBACKS)
def test_max_retries_zero_means_exactly_one_request(name):
    provider, session = MAKERS[name](FakeResponse(status=503, text="busy"), retries=0)
    assert provider.search("q").error == "HTTP 503: busy" and len(session.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 432, 433])
def test_tavily_non_retryable_statuses_make_exactly_one_request(status):
    provider, session = _tavily(FakeResponse(status=status, text="denied"))
    assert provider.search("q").error == f"HTTP {status}: denied"
    assert len(session.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403])
def test_serper_non_retryable_statuses_make_exactly_one_request(status):
    provider, session = _serper(FakeResponse(status=status, text="denied"))
    assert provider.search("q").error == f"HTTP {status}: denied"
    assert len(session.calls) == 1


def test_tavily_backoff_is_exponential_and_retry_after_is_capped(sleeps):
    provider, _ = _tavily(
        FakeResponse(status=429, headers={"Retry-After": "120"}),
        FakeResponse(status=429, headers={"Retry-After": "not-a-number"}),
        FakeResponse(status=429, text="still limited"),
    )
    assert provider.search("q").error == "HTTP 429: still limited"
    assert sleeps == [5.0, 1.5]  # Retry-After capped at 5s; then 0.75 * 2**1


def test_tavily_retry_after_longer_than_backoff_is_honoured(sleeps):
    provider, _ = _tavily(FakeResponse(status=429, headers={"Retry-After": "2"}), FakeResponse(body={"results": []}))
    assert provider.search("q").ok
    assert sleeps == [2.0]


# ---------------------------------------------------------------------------
# Malformed 200 bodies: an error, never retried, never raised
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", FALLBACKS)
def test_non_json_200_is_malformed_and_not_retried(name, sleeps):
    provider, session = MAKERS[name](FakeResponse(text="<html>gateway</html>", bad_json=True))
    outcome = provider.search("q")
    assert outcome.error == "malformed response: body is not JSON"
    assert len(session.calls) == 1 and sleeps == []


@pytest.mark.parametrize("name", FALLBACKS)
@pytest.mark.parametrize("body", [[1, 2], "text", 42, None])
def test_non_object_json_200_is_malformed_and_not_retried(name, body):
    provider, session = MAKERS[name](FakeResponse(body=body))
    outcome = provider.search("q")
    assert outcome.error == "malformed response: unexpected JSON shape"
    assert len(session.calls) == 1


def test_tavily_unexpected_exception_from_session_is_reported_not_raised():
    provider, session = _tavily(RuntimeError(f"boom {KEY}"))
    outcome = provider.search("q")
    assert outcome.error == "unexpected error: boom ***" and len(session.calls) == 1


# ---------------------------------------------------------------------------
# API-key redaction and error-text cap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", FALLBACKS)
def test_key_is_redacted_from_http_error_bodies(name):
    provider, _ = MAKERS[name](FakeResponse(status=401, text=f"invalid key {KEY} supplied"))
    error = provider.search("q").error
    assert KEY not in error and error == "HTTP 401: invalid key *** supplied"


@pytest.mark.parametrize("name", FALLBACKS)
def test_key_is_redacted_from_transport_errors(name):
    provider, _ = MAKERS[name](*[requests.exceptions.ConnectionError(f"failed token={KEY}")] * 3)
    error = provider.search("q").error
    assert KEY not in error and error == "transport error: failed token=***"


@pytest.mark.parametrize("name,limit", [("tavily", 200), ("serper", 300)])
def test_key_cut_by_the_length_cap_is_still_never_exposed(name, limit):
    # The key straddles the cap: redact-then-truncate must leave no fragment.
    text = "x" * (limit - 10) + KEY + "y" * 50
    provider, _ = MAKERS[name](FakeResponse(status=403, text=text))
    error = provider.search("q").error
    assert KEY[:8] not in error
    assert len(error) <= len("HTTP 403: ") + limit


# ---------------------------------------------------------------------------
# One contract across Brave, Tavily and Serper
# ---------------------------------------------------------------------------

SCENARIOS = {
    "auth rejected": ([FakeResponse(status=401, text="bad key")], "HTTP 401: bad key", 1),
    "rate limit then success": ([FakeResponse(status=429), "OK"], None, 2),
    "server error then success": ([FakeResponse(status=502), "OK"], None, 2),
    "timeout then success": ([requests.exceptions.Timeout("t"), "OK"], None, 2),
    "persistent rate limit": ([FakeResponse(status=429, text="limit")] * 3, "HTTP 429: limit", 3),
    "persistent timeout": ([requests.exceptions.Timeout("t")] * 3, "timeout", 3),
    "persistent network failure": ([requests.exceptions.ConnectionError("down")] * 3, "transport error: down", 3),
    "non-JSON 200": ([FakeResponse(text="<html/>", bad_json=True)], "malformed response: body is not JSON", 1),
    "list JSON 200": ([FakeResponse(body=[])], "malformed response: unexpected JSON shape", 1),
    "zero results": (["OK"], None, 1),
}


@pytest.mark.parametrize("name", ["brave", "tavily", "serper"])
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_all_chain_providers_share_one_failure_contract(name, scenario):
    queued, expected_error, expected_calls = SCENARIOS[scenario]
    responses = [FakeResponse(body=EMPTY_BODY[name]) if r == "OK" else r for r in queued]
    provider, session = MAKERS[name](*responses)
    outcome = provider.search("q")
    assert outcome.error == expected_error, (name, scenario, outcome.error)
    assert len(session.calls) == expected_calls, (name, scenario)
    assert outcome.provider == name and outcome.query == "q"
    if expected_error is None:
        assert outcome.results == []

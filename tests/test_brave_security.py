"""Brave provider security and extra-snippets fallback (research/providers/brave.py).

  * The API key is redacted BEFORE error text is shortened: no fragment of
    it survives the 200-character cut, on any error path.
  * Exceptions other than requests' are caught inside the request loop and
    returned as a sanitised "unexpected error: ..." (at most 200 characters,
    no retry); search() never raises for a request failure.
  * extra_snippets is switched off only when the plain verification retry
    SUCCEEDS. HTTP 401 / 402 never trigger that retry. Unrelated failures
    never switch the option off.

No network: Brave HTTP responses come from a fake session; time.sleep is
recorded, never slept.
"""

from __future__ import annotations

import pytest
import requests

import research.acquisition as acquisition
from research.acquisition import run_acquisition
from research.linkedin import PEOPLE_MODE_ENV, discover_linkedin
from research.organization import OrganizationInput, ResolvedOrganization
from research.providers import brave as brave_module
from research.providers import router
from research.providers.brave import BRAVE_ENDPOINT, BraveSearchProvider
from research.providers.router import acquire_family, provider_health_scope
from research.sources import AccessStatus

KEY = "BSAkey0123456789abcdefABCDEF0123"     # 32 characters, alphanumeric
LIMIT = 200


class FakeResponse:
    def __init__(self, status=200, body=None, text="", headers=None, json_error=None):
        self.status_code = status
        self._body = body
        self.text = text
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._body


class FakeSession:
    """Queued responses / exceptions; the last item repeats when the queue runs out."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params or {}))
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, BaseException):
            raise item
        return item


def _body(n=1, url="https://www.linkedin.com/in/person"):
    return {"web": {"results": [{"url": f"{url}{i}", "title": "Person", "description": "Main",
                                 "extra_snippets": ["Extra"]} for i in range(n)]}}


OK = FakeResponse(200, _body())


def _brave(*items, key=KEY):
    session = FakeSession(*items)
    return BraveSearchProvider(api_key=key, session=session, backoff_base=0.0), session


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(brave_module.time, "sleep", recorded.append)
    return recorded


def _fragments(key: str, size: int = 4) -> set[str]:
    return {key[i:i + size] for i in range(len(key) - size + 1)}


def _assert_no_key_fragment(text: str, key: str = KEY) -> None:
    assert key not in text
    leaked = [f for f in _fragments(key) if f in text]
    assert not leaked, f"key fragments leaked: {leaked}"


# ---------------------------------------------------------------------------
# 1. Redaction before truncation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("offset", range(LIMIT - len(KEY) - 5, LIMIT + 2))
def test_http_error_body_never_leaks_a_key_fragment_at_the_cut(offset):
    body = "." * offset + KEY + "-" * 40
    brave, session = _brave(FakeResponse(403, text=body))
    error = brave.search("q").error
    _assert_no_key_fragment(error)
    assert error.startswith("HTTP 403: ")
    assert len(error) <= len("HTTP 403: ") + LIMIT
    assert len(session.calls) == 1


def test_http_error_body_with_the_key_repeated_and_inside_the_limit_is_fully_redacted():
    brave, _ = _brave(FakeResponse(401, text=f"token {KEY} rejected; token {KEY} again"))
    assert brave.search("q").error == "HTTP 401: token *** rejected; token *** again"


@pytest.mark.parametrize("offset", range(LIMIT - len(KEY) - 5, LIMIT + 2))
def test_transport_error_never_leaks_a_key_fragment_at_the_cut(offset):
    exc = requests.exceptions.ConnectionError("." * offset + KEY + "-" * 40)
    brave, session = _brave(exc)
    error = brave.search("q").error
    _assert_no_key_fragment(error)
    assert error.startswith("transport error: ") and len(error) <= len("transport error: ") + LIMIT
    assert len(session.calls) == 3          # transport errors are still retried (unchanged)


def test_a_key_longer_than_the_limit_is_still_fully_redacted():
    long_key = "K" + "0123456789abcdef" * 20     # 321 characters
    brave, _ = _brave(FakeResponse(401, text=f"bad {long_key} key"), key=long_key)
    error = brave.search("q").error
    assert error == "HTTP 401: bad *** key"
    _assert_no_key_fragment(error, long_key)


def test_timeout_and_malformed_errors_carry_no_payload():
    brave, _ = _brave(requests.exceptions.Timeout(f"read timed out {KEY}"))
    assert brave.search("q").error == "timeout"
    brave, _ = _brave(FakeResponse(200, text=KEY, json_error=ValueError(KEY)))
    assert brave.search("q").error == "malformed response: body is not JSON"


# ---------------------------------------------------------------------------
# 2. Unexpected exceptions
# ---------------------------------------------------------------------------

class _BrokenStr(Exception):
    def __str__(self):
        raise RuntimeError("str() failed")


@pytest.mark.parametrize("exc", [
    RuntimeError(f"boom {KEY}"),
    UnicodeError(f"bad bytes near {KEY}"),
    KeyError(KEY),
    AttributeError(f"'NoneType' has no attribute {KEY}"),
    ValueError(f"invalid literal {KEY}"),
    TypeError("unsupported operand"),
])
def test_unexpected_exceptions_are_sanitised_bounded_and_not_retried(exc, sleeps):
    brave, session = _brave(exc)
    outcome = brave.search("q")                       # never raises
    assert outcome.error.startswith("unexpected error: ")
    assert len(outcome.error) <= LIMIT
    _assert_no_key_fragment(outcome.error)
    assert outcome.results == [] and outcome.provider == "brave" and outcome.query == "q"
    assert len(session.calls) == 1 and sleeps == []   # no retry, no backoff


def test_a_huge_exception_with_the_key_at_the_cut_is_bounded_and_clean():
    for offset in range(150, LIMIT + 2):
        brave, _ = _brave(RuntimeError("." * offset + KEY + "-" * 5000))
        error = brave.search("q").error
        assert len(error) <= LIMIT
        _assert_no_key_fragment(error)


def test_an_exception_whose_str_fails_is_still_reported():
    brave, _ = _brave(_BrokenStr())
    assert brave.search("q").error == "unexpected error: _BrokenStr"


def test_an_unexpected_exception_while_reading_the_body_is_caught():
    brave, session = _brave(FakeResponse(200, json_error=RuntimeError(f"decoder crashed {KEY}")))
    outcome = brave.search("q")
    assert outcome.error == "unexpected error: decoder crashed ***" and len(session.calls) == 1


def test_router_records_the_sanitised_error_and_never_takes_brave_out_of_service():
    brave, session = _brave(RuntimeError(f"boom {KEY}"))
    with provider_health_scope():
        first = acquire_family("f1", ["q1", "q2"], [brave])
        second = acquire_family("f2", ["q3"], [brave])
        assert router._RUN_HEALTH.get().reason(brave) is None   # unexpected errors never trip (B5.3)
    for line in first.errors + second.errors:
        assert line.endswith("unexpected error: boom ***")
        _assert_no_key_fragment(line)
    assert len(session.calls) == 3


def _org():
    return ResolvedOrganization(name="Meridian Example Holdings", country="Oman", website="https://www.example-meridian.test",
                                linkedin_company_url=None, website_was_supplied=False, linkedin_was_supplied=False)


def test_people_discovery_records_the_sanitised_error(monkeypatch):
    monkeypatch.delenv(PEOPLE_MODE_ENV, raising=False)
    brave, session = _brave(RuntimeError(f"boom {KEY}"))
    result = discover_linkedin(_org(), [brave])
    errors = result.family_outcome.errors
    assert errors and all(e.endswith("unexpected error: boom ***") for e in errors)
    for entry in result.family_outcome.coverage.metrics["request_log"]:
        _assert_no_key_fragment(str(entry))
    assert len(session.calls) >= 1


# ---------------------------------------------------------------------------
# 3. Extra-snippets fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_option_is_switched_off_only_when_the_plain_retry_succeeds(status):
    brave, session = _brave(FakeResponse(status, text="option refused"), OK, OK)
    outcome = brave.search("q", rich_snippets=True)
    assert outcome.ok and len(session.calls) == 2
    assert session.calls[0]["extra_snippets"] == "true" and "extra_snippets" not in session.calls[1]
    assert brave._extra_snippets_rejected is True
    brave.search("next", rich_snippets=True)
    assert "extra_snippets" not in session.calls[2] and len(session.calls) == 3   # no longer paid for


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_option_stays_enabled_when_the_plain_retry_also_fails(status):
    brave, session = _brave(FakeResponse(status, text=f"first {KEY}"), FakeResponse(status, text=f"plain {KEY}"), OK)
    outcome = brave.search("q", rich_snippets=True)
    assert outcome.error == f"HTTP {status}: plain ***"           # the plain retry's error, as before
    assert len(session.calls) == 2 and brave._extra_snippets_rejected is False
    brave.search("next", rich_snippets=True)
    assert session.calls[2]["extra_snippets"] == "true"


@pytest.mark.parametrize("status", [401, 402])
def test_key_and_payment_failures_make_one_request_and_keep_the_option(status):
    brave, session = _brave(FakeResponse(status, text=f"denied {KEY}"), OK)
    outcome = brave.search("q", rich_snippets=True)
    assert outcome.error == f"HTTP {status}: denied ***"
    assert len(session.calls) == 1 and session.calls[0]["extra_snippets"] == "true"
    assert brave._extra_snippets_rejected is False
    brave.search("next", rich_snippets=True)
    assert session.calls[1]["extra_snippets"] == "true"


@pytest.mark.parametrize("first", [
    FakeResponse(429, text="slow down"),
    FakeResponse(500, text="boom"),
    FakeResponse(503, text="busy"),
    requests.exceptions.Timeout("t"),
    requests.exceptions.ConnectionError("reset"),
    RuntimeError("boom"),
    FakeResponse(200, text="<html>", json_error=ValueError("not json")),
])
def test_unrelated_failures_never_trigger_the_plain_retry_or_switch_the_option_off(first):
    brave, session = _brave(first)
    outcome = brave.search("q", rich_snippets=True)
    assert not outcome.ok
    assert all(call.get("extra_snippets") == "true" for call in session.calls)   # never a plain request
    assert brave._extra_snippets_rejected is False


def test_an_over_long_query_does_not_switch_the_option_off_for_later_queries():
    too_long = FakeResponse(422, text="query too long")
    brave, session = _brave(too_long, too_long, OK)
    assert not brave.search("word " * 300, rich_snippets=True).ok
    brave.search("normal people query", rich_snippets=True)
    assert session.calls[-1]["extra_snippets"] == "true"


def test_plain_retry_keeps_page_and_count():
    brave, session = _brave(FakeResponse(422, text="option refused"), OK)
    brave.search("q", num_results=7, rich_snippets=True, page=3)
    assert session.calls[0] == {"q": "q", "count": 7, "extra_snippets": "true", "offset": 3}
    assert session.calls[1] == {"q": "q", "count": 7, "offset": 3}


def test_plain_searches_are_unchanged():
    brave, session = _brave(OK)
    assert brave.search("q", num_results=5).ok
    assert session.calls == [{"q": "q", "count": 5}]


# ---------------------------------------------------------------------------
# 4. One provider reused across several accounts
# ---------------------------------------------------------------------------

def test_one_provider_reused_across_accounts_keeps_the_option_until_a_real_rejection():
    brave, session = _brave(
        FakeResponse(401, text="bad key"),                                   # account A: key problem
        FakeResponse(422, text="bad query"), FakeResponse(422, text="bad query"),  # account B: bad query
        OK,                                                                  # account C: healthy
        FakeResponse(422, text="option not in plan"), OK,                    # account D: real rejection
        OK,                                                                  # account E
    )
    for account_query in ("account A", "account B", "account C"):
        brave.search(account_query, rich_snippets=True)
        assert brave._extra_snippets_rejected is False
    assert session.calls[3]["extra_snippets"] == "true"                      # account C still asked for it
    assert brave.search("account D", rich_snippets=True).ok
    assert brave._extra_snippets_rejected is True
    brave.search("account E", rich_snippets=True)
    assert "extra_snippets" not in session.calls[-1]                         # a plan property: stays off


class _AccountSession:
    """Answers by mode: 'dead' -> HTTP 401 for everything; 'healthy' -> results."""

    def __init__(self):
        self.mode = "dead"
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        assert url == BRAVE_ENDPOINT
        self.calls.append(dict(params or {}))
        if self.mode == "dead":
            return FakeResponse(401, text=f"invalid token {KEY}")
        return FakeResponse(200, _body(2))


def test_run_acquisition_reusing_brave_across_accounts_keeps_extra_snippets(monkeypatch):
    monkeypatch.delenv(PEOPLE_MODE_ENV, raising=False)
    monkeypatch.setattr(acquisition, "fetch_page", lambda url: ("", AccessStatus.UNREACHABLE))
    monkeypatch.setattr(acquisition, "crawl_official_site", lambda website, name: [])
    session = _AccountSession()
    brave = BraveSearchProvider(api_key=KEY, session=session, backoff_base=0.0)
    providers = [brave]                                   # reused, as run_phase1c / run_phase1d do

    first = run_acquisition(OrganizationInput(name="Account One Holdings", country="Oman"), providers)
    for line in first.diagnostics.all_errors:
        _assert_no_key_fragment(line)
    rich_calls_first = [c for c in session.calls if c.get("extra_snippets") == "true"]
    assert rich_calls_first                                 # People Discovery asked for extra snippets
    assert brave._extra_snippets_rejected is False         # a dead key never disables the option

    session.mode, session.calls = "healthy", []
    run_acquisition(OrganizationInput(name="Account Two Trading", country="Oman"), providers)
    assert any(c.get("extra_snippets") == "true" for c in session.calls)   # People Discovery still asks for it
    assert brave._extra_snippets_rejected is False

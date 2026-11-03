"""Brave Search API provider -- PRIMARY acquisition provider.

Uses Brave's official Web Search endpoint (structured JSON results):

    GET https://api.search.brave.com/res/v1/web/search?q=...&count=...
    header  X-Subscription-Token: <BRAVE_SEARCH_API_KEY>

It never scrapes brave.com result pages and never calls Brave's Answers
(summarization) endpoint: Account Radar receives raw results and remains
solely responsible for evidence classification. Like every provider
(see base.py), this file does no relevance judgment and no LLM work.

Failure contract (identical in spirit to serper.py / tavily.py):
  * missing key            -> QueryOutcome.error "BRAVE_SEARCH_API_KEY not configured"
  * HTTP 401/403/402/422   -> QueryOutcome.error "HTTP <code>: ..." (no retry)
  * HTTP 429 / 5xx         -> bounded retries, then QueryOutcome.error
  * timeout / transport    -> bounded retries, then QueryOutcome.error
  * malformed 200 body     -> QueryOutcome.error "malformed response: ..."
  * genuine zero results   -> QueryOutcome with an empty result list (NOT an error)
A provider failure is therefore never turned into fake empty evidence; the
router sees an error and can fall back.

People discovery options (opt-in; plain `search(query, num_results=...)`
calls, i.e. all general research, send exactly the same request as before):
  * rich_snippets=True -> Brave's documented `extra_snippets` option (up to
    five more excerpts per result), returned SEPARATELY in
    RawResult.extra_snippets and never merged into the result's snippet. If Brave rejects
    the option (HTTP 4xx other than 429, e.g. not in the current plan), the
    query is retried once without it AND the rejection is remembered for the
    lifetime of this provider object, so it is never paid for twice.
  * page=N -> Brave's documented `offset` paging (zero-based page index of
    `count`-sized pages, maximum 9). The response's
    `query.more_results_available` flag is passed back on the QueryOutcome
    so callers can avoid requesting empty pages.

Rate limits: when Brave's X-RateLimit-Remaining header reports that the
per-second allowance is used up, the next request waits for
X-RateLimit-Reset (capped), instead of provoking HTTP 429 responses.

Cost safety: one HTTP request per query (two only in the one-off
extra_snippets rejection case), no automatic credit purchase or billing
integration. Brave's free monthly allowance and per-request pricing are set
by Brave's current plan terms, not by this code.
"""

from __future__ import annotations

import html
import os
import re
import threading
import time

import requests

from research.providers.base import QueryOutcome, RawResult

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
BRAVE_KEY_ENV = "BRAVE_SEARCH_API_KEY"
BRAVE_MAX_COUNT = 20  # Brave's documented maximum results per request
BRAVE_MAX_OFFSET = 9  # Brave's documented maximum page offset

_TAG_RE = re.compile(r"<[^>]+>")
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


def _clean_text(value: object, limit: int) -> str:
    """Brave highlights matches with <strong> markup and HTML entities.
    Remove the markup only; the wording itself is kept verbatim."""
    text = html.unescape(_TAG_RE.sub("", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _snippet(item: dict) -> str:
    """The result's own description only. Brave `extra_snippets` are returned
    separately by _extra_snippets (they are other passages from the page)."""
    return _clean_text(item.get("description"), 2000)


def _extra_snippets(item: dict, description: str) -> list[str]:
    """Brave `extra_snippets`, cleaned and de-duplicated (never repeating the
    description), in Brave's order."""
    out: list[str] = []
    extra = item.get("extra_snippets")
    for value in extra if isinstance(extra, list) else []:
        text = _clean_text(value, 2000)
        if text and text != description and text not in out:
            out.append(text)
    return out


def _first_number(value: object) -> float | None:
    """Brave rate-limit headers carry one value per window ("1, 15000"); the
    first is the per-second window."""
    try:
        return float(str(value).split(",")[0].strip())
    except (TypeError, ValueError):
        return None


class BraveSearchProvider:
    name = "brave"
    supports_rich_snippets = True
    supports_pagination = True

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 10.0,
        max_retries: int = 2,
        backoff_base: float = 0.75,
        max_retry_after: float = 5.0,
        session: requests.Session | None = None,
    ):
        self._api_key = (api_key or os.getenv(BRAVE_KEY_ENV) or "").strip()
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_retry_after = max_retry_after
        self._session = session or requests.Session()
        self._extra_snippets_rejected = False
        self._rate_lock = threading.Lock()
        self._not_before = 0.0

    def is_configured(self) -> bool:
        return bool(self._api_key)

    # -- helpers -------------------------------------------------------------

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def _wait(self, attempt: int, response: requests.Response | None = None) -> None:
        delay = self._backoff_base * (2**attempt)
        retry_after = (response.headers.get("Retry-After") if response is not None else None) or ""
        try:
            delay = max(delay, min(float(retry_after), self._max_retry_after))
        except ValueError:
            pass
        time.sleep(delay)

    def _pace(self) -> None:
        with self._rate_lock:
            delay = self._not_before - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def _note_rate_limit(self, response: requests.Response) -> None:
        headers = getattr(response, "headers", None) or {}
        remaining = _first_number(headers.get("X-RateLimit-Remaining"))
        if remaining is None or remaining > 0:
            return
        reset = _first_number(headers.get("X-RateLimit-Reset"))
        wait = min(max(reset if reset is not None else 1.0, 0.0), self._max_retry_after)
        with self._rate_lock:
            self._not_before = max(self._not_before, time.monotonic() + wait)

    # -- SearchProvider ------------------------------------------------------

    def search(self, query: str, *, num_results: int = 10, rich_snippets: bool = False, page: int = 0) -> QueryOutcome:
        page = max(0, min(BRAVE_MAX_OFFSET, int(page or 0)))
        if rich_snippets and not self._extra_snippets_rejected:
            outcome = self._search(query, num_results=num_results, rich_snippets=True, page=page)
            if outcome.error and outcome.error.startswith("HTTP 4") and not outcome.error.startswith("HTTP 429"):
                self._extra_snippets_rejected = True
                return self._search(query, num_results=num_results, rich_snippets=False, page=page)
            return outcome
        return self._search(query, num_results=num_results, rich_snippets=False, page=page)

    def _search(self, query: str, *, num_results: int, rich_snippets: bool, page: int = 0) -> QueryOutcome:
        started = time.monotonic()
        if not self.is_configured():
            return QueryOutcome(query=query, provider=self.name,
                                error=f"{BRAVE_KEY_ENV} not configured", elapsed_seconds=0.0)

        params = {"q": query, "count": max(1, min(BRAVE_MAX_COUNT, int(num_results)))}
        if rich_snippets:
            params["extra_snippets"] = "true"
        if page:
            params["offset"] = page
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._api_key,
        }

        last_error: str | None = None
        for attempt in range(self._max_retries + 1):
            try:
                self._pace()
                resp = self._session.get(BRAVE_ENDPOINT, params=params, headers=headers, timeout=self._timeout)
                self._note_rate_limit(resp)
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        last_error = "malformed response: body is not JSON"
                        break
                    if not isinstance(body, dict):
                        last_error = "malformed response: unexpected JSON shape"
                        break
                    meta = body.get("query") if isinstance(body.get("query"), dict) else {}
                    more = meta.get("more_results_available")
                    return QueryOutcome(query=query, provider=self.name, results=self._parse(body, query),
                                        elapsed_seconds=time.monotonic() - started, page=page,
                                        more_results_available=more if isinstance(more, bool) else None)
                if resp.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                    last_error = f"HTTP {resp.status_code}, retrying"
                    self._wait(attempt, resp)
                    continue
                last_error = f"HTTP {resp.status_code}: {self._redact((resp.text or '')[:200])}"
                break
            except requests.exceptions.Timeout:
                last_error = "timeout"
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                break
            except requests.exceptions.RequestException as exc:
                last_error = f"transport error: {self._redact(str(exc))[:200]}"
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                break

        return QueryOutcome(query=query, provider=self.name, error=last_error or "unknown error",
                            elapsed_seconds=time.monotonic() - started, page=page)

    @staticmethod
    def _parse(body: dict, query: str) -> list[RawResult]:
        """Brave web results -> RawResult, in Brave's order. Items without a
        usable URL are skipped (malformed item), never invented. A body with
        no "web" section is a genuine zero-result answer."""
        web = body.get("web")
        items = web.get("results") if isinstance(web, dict) else None
        out: list[RawResult] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            out.append(
                RawResult(
                    url=url,
                    title=_clean_text(item.get("title"), 600),
                    snippet=_snippet(item),
                    query=query,
                    provider="brave",
                    rank=len(out),
                    published_at=str(item.get("page_age") or "").strip() or None,
                    extra_snippets=_extra_snippets(item, _snippet(item)),
                )
            )
        return out

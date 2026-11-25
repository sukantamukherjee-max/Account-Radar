"""Tavily Search provider -- SECONDARY in the live chain (Brave -> Tavily -> Serper).

Plain JSON POST to api.tavily.com/search. No LLM involvement and no
relevance judgment in this file (see base.py).

Failure contract (same as brave.py / serper.py):
  * missing key              -> QueryOutcome.error "TAVILY_API_KEY not configured" (no request)
  * HTTP 401/403/432/433/4xx -> QueryOutcome.error "HTTP <code>: ..." (no retry;
                                432/433 are Tavily plan/usage limits, not transient)
  * HTTP 429 / 5xx           -> bounded retries (Retry-After honoured, capped), then QueryOutcome.error
  * timeout / transport      -> bounded retries, then QueryOutcome.error
  * malformed 200 body       -> QueryOutcome.error "malformed response: ..." (no retry)
  * genuine zero results     -> QueryOutcome with an empty result list (NOT an error)
The API key is redacted from every error message, and error text is capped.
A provider failure is never turned into fake empty evidence; the router sees
an error and can fall back.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from .base import QueryOutcome, RawResult


TAVILY_ENDPOINT = "https://api.tavily.com/search"
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)
_ERROR_TEXT_LIMIT = 200


class TavilyProvider:
    """Tavily Search provider for Account Radar."""

    name = "tavily"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 10.0,
        max_retries: int = 2,
        backoff_base: float = 0.75,
        max_retry_after: float = 5.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("TAVILY_API_KEY")
            or ""
        ).strip()
        self.timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_retry_after = max_retry_after
        self._session = session or requests.Session()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    # -- helpers -------------------------------------------------------------

    def _redact(self, text: str) -> str:
        return text.replace(self.api_key, "***") if self.api_key else text

    def _wait(self, attempt: int, response: Any = None) -> None:
        delay = self._backoff_base * (2**attempt)
        headers = getattr(response, "headers", None) or {}
        try:
            delay = max(delay, min(float(headers.get("Retry-After") or ""), self._max_retry_after))
        except ValueError:
            pass
        time.sleep(delay)

    # -- SearchProvider ------------------------------------------------------

    def search(
        self,
        query: str,
        num_results: int = 10,
    ) -> QueryOutcome:
        started = time.monotonic()

        if not self.is_configured():
            return QueryOutcome(
                query=query,
                provider=self.name,
                error="TAVILY_API_KEY not configured",
                elapsed_seconds=0.0,
            )

        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": max(1, min(20, int(num_results))),
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: str | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._session.post(
                    TAVILY_ENDPOINT,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.exceptions.Timeout:
                last_error = "timeout"
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                break
            except requests.exceptions.RequestException as exc:
                last_error = f"transport error: {self._redact(str(exc))[:_ERROR_TEXT_LIMIT]}"
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                break
            except Exception as exc:  # noqa: BLE001 - never let a provider failure abort the run
                last_error = f"unexpected error: {self._redact(str(exc))[:_ERROR_TEXT_LIMIT]}"
                break

            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError:
                    last_error = "malformed response: body is not JSON"
                    break
                if not isinstance(body, dict):
                    last_error = "malformed response: unexpected JSON shape"
                    break
                return QueryOutcome(
                    query=query,
                    provider=self.name,
                    results=self._parse(body, query),
                    elapsed_seconds=time.monotonic() - started,
                )

            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                last_error = f"HTTP {response.status_code}, retrying"
                self._wait(attempt, response)
                continue
            last_error = f"HTTP {response.status_code}: {self._redact(response.text or '')[:_ERROR_TEXT_LIMIT]}"
            break

        return QueryOutcome(
            query=query,
            provider=self.name,
            error=last_error or "unknown error",
            elapsed_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _parse(
        body: dict[str, Any],
        query: str,
    ) -> list[RawResult]:
        results: list[RawResult] = []
        items = body.get("results")

        for rank, item in enumerate(items if isinstance(items, list) else []):
            if not isinstance(item, dict):
                continue

            url = str(item.get("url") or "").strip()

            if not url:
                continue

            results.append(
                RawResult(
                    url=url,
                    title=str(item.get("title") or "").strip()[:600],
                    snippet=str(
                        item.get("content")
                        or item.get("snippet")
                        or ""
                    ).strip()[:2000],
                    query=query,
                    provider="tavily",
                    rank=rank,
                    published_at=(
                        str(
                            item.get("published_date")
                            or ""
                        ).strip()
                        or None
                    ),
                )
            )

        return results

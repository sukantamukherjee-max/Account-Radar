"""Serper.dev provider -- last fallback in the live chain (Brave -> Tavily -> Serper).

Plain JSON POST to google.serper.dev/search. No LLM involvement anywhere
in this file. Retries transient failures (timeouts, 429, 5xx) with bounded
exponential backoff; a persistent failure becomes a QueryOutcome.error, not
a raised exception that aborts the caller. A malformed 200 body (not JSON,
or not the expected shape) is reported as "malformed response: ..." without
a retry, and the API key is redacted from every error message.

Serper's own documentation does not publish a hard rate-limit number, so
this provider defaults to a conservative per-instance request pace rather
than assuming an unlimited ceiling (see Phase 1A validation notes).
"""

from __future__ import annotations

import os
import time

import requests

from research.providers.base import QueryOutcome, RawResult

SERPER_ENDPOINT = "https://google.serper.dev/search"
_ERROR_TEXT_LIMIT = 300


class SerperProvider:
    name = "serper"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 8.0,
        max_retries: int = 2,
        backoff_base: float = 0.75,
        session: requests.Session | None = None,
    ):
        self._api_key = (api_key or os.getenv("SERPER_API_KEY") or "").strip()
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._session = session or requests.Session()

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def search(self, query: str, *, num_results: int = 10) -> QueryOutcome:
        started = time.monotonic()
        if not self.is_configured():
            return QueryOutcome(
                query=query,
                provider=self.name,
                error="SERPER_API_KEY not configured",
                elapsed_seconds=0.0,
            )

        payload = {"q": query, "num": max(1, min(100, int(num_results)))}
        headers = {"X-API-KEY": self._api_key, "Content-Type": "application/json"}

        last_error: str | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.post(
                    SERPER_ENDPOINT, json=payload, headers=headers, timeout=self._timeout
                )
                if resp.status_code == 200:
                    # Parsed here, inside the 200 branch: requests' JSON
                    # error is also a RequestException, and must not be
                    # mistaken for a transport failure and retried.
                    try:
                        body = resp.json()
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
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                    last_error = f"HTTP {resp.status_code}, retrying"
                    time.sleep(self._backoff_base * (2**attempt))
                    continue
                last_error = f"HTTP {resp.status_code}: {self._redact(resp.text or '')[:_ERROR_TEXT_LIMIT]}"
                break
            except requests.exceptions.Timeout:
                last_error = "timeout"
                if attempt < self._max_retries:
                    time.sleep(self._backoff_base * (2**attempt))
                    continue
                break
            except requests.exceptions.RequestException as exc:
                last_error = f"transport error: {self._redact(str(exc))[:_ERROR_TEXT_LIMIT]}"
                if attempt < self._max_retries:
                    time.sleep(self._backoff_base * (2**attempt))
                    continue
                break

        return QueryOutcome(
            query=query,
            provider=self.name,
            error=last_error or "unknown error",
            elapsed_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _parse(body: dict, query: str) -> list[RawResult]:
        """Convert Serper's native JSON shape into RawResult, verbatim.

        No filtering, no relevance judgment. Organic results, plus the
        "knowledge graph" / "answerBox" website field when present (useful
        for official-site discovery), are surfaced as-is.
        """
        out: list[RawResult] = []
        rank = 0
        organic = body.get("organic")
        for item in organic if isinstance(organic, list) else []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("link") or "").strip()
            if not url:
                continue
            out.append(
                RawResult(
                    url=url,
                    title=str(item.get("title") or "").strip()[:600],
                    snippet=str(item.get("snippet") or "").strip()[:2000],
                    query=query,
                    provider="serper",
                    rank=rank,
                    published_at=str(item.get("date") or item.get("publishedDate") or "").strip() or None,
                )
            )
            rank += 1
        kg = body.get("knowledgeGraph")
        kg = kg if isinstance(kg, dict) else {}
        kg_url = str(kg.get("website") or "").strip()
        if kg_url:
            out.append(
                RawResult(
                    url=kg_url,
                    title=str(kg.get("title") or "").strip()[:600],
                    snippet=str(kg.get("description") or "").strip()[:2000],
                    query=query,
                    provider="serper_knowledge_graph",
                    rank=-1,
                )
            )
        return out

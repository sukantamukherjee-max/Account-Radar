"""Serper.dev provider -- PRIMARY acquisition provider.

Plain JSON POST to google.serper.dev/search. No LLM involvement anywhere
in this file. Retries transient failures (timeouts, 429, 5xx) with bounded
exponential backoff; a persistent failure becomes a QueryOutcome.error, not
a raised exception that aborts the caller.

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


class SerperProvider:
    name = "serper"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 8.0,
        max_retries: int = 2,
        backoff_base: float = 0.75,
    ):
        self._api_key = (api_key or os.getenv("SERPER_API_KEY") or "").strip()
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._session = requests.Session()

    def is_configured(self) -> bool:
        return bool(self._api_key)

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
                    return QueryOutcome(
                        query=query,
                        provider=self.name,
                        results=self._parse(resp.json(), query),
                        elapsed_seconds=time.monotonic() - started,
                    )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                    last_error = f"HTTP {resp.status_code}, retrying"
                    time.sleep(self._backoff_base * (2**attempt))
                    continue
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                break
            except requests.exceptions.Timeout:
                last_error = "timeout"
                if attempt < self._max_retries:
                    time.sleep(self._backoff_base * (2**attempt))
                    continue
                break
            except requests.exceptions.RequestException as exc:
                last_error = f"transport error: {exc}"
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
        for item in (body.get("organic") or []):
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
        kg = body.get("knowledgeGraph") or {}
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

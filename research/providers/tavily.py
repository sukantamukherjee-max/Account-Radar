from __future__ import annotations

import os
import time
from typing import Any

import requests

from .base import QueryOutcome, RawResult, SearchProvider


TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilyProvider:
    """Tavily Search provider for Account Radar."""

    name = "tavily"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("TAVILY_API_KEY")
            or ""
        ).strip()
        self.timeout = timeout
        self._session = requests.Session()

    def is_configured(self) -> bool:
        return bool(self.api_key)

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

        try:
            response = self._session.post(
                TAVILY_ENDPOINT,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )

            if response.status_code == 200:
                body = response.json()
                return QueryOutcome(
                    query=query,
                    provider=self.name,
                    results=self._parse(body, query),
                    elapsed_seconds=time.monotonic() - started,
                )

            return QueryOutcome(
                query=query,
                provider=self.name,
                error=f"HTTP {response.status_code}: {response.text[:300]}",
                elapsed_seconds=time.monotonic() - started,
            )

        except requests.exceptions.Timeout:
            return QueryOutcome(
                query=query,
                provider=self.name,
                error="timeout",
                elapsed_seconds=time.monotonic() - started,
            )

        except requests.exceptions.RequestException as exc:
            return QueryOutcome(
                query=query,
                provider=self.name,
                error=f"transport error: {exc}",
                elapsed_seconds=time.monotonic() - started,
            )

        except Exception as exc:
            return QueryOutcome(
                query=query,
                provider=self.name,
                error=f"unexpected error: {exc}",
                elapsed_seconds=time.monotonic() - started,
            )

    @staticmethod
    def _parse(
        body: dict[str, Any],
        query: str,
    ) -> list[RawResult]:
        results: list[RawResult] = []

        for rank, item in enumerate(body.get("results") or []):
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
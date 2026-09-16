"""OpenRouter web-search (Parallel engine) provider -- SECONDARY/FALLBACK.

This is the component the Phase 0 diagnosis specifically flagged: earlier
versions of Account Radar asked the model to act as a "bulk result echoer"
and transcribe search results as JSON prose. That is an LLM-mediated
enumeration step and is exactly what this rebuild eliminates.

This implementation uses ONLY the `url_citation` annotations OpenRouter
attaches to the completion response -- these are populated directly by the
underlying search engine (Parallel), independent of whatever the model
writes as its visible reply. The model's visible/generated text is fetched
by the SDK but is never parsed, regexed, or otherwise used as a results
source. If annotations are empty, the result set is empty -- there is no
fallback to reading the model's prose.

Because Phase 1A's validation could not fully reconcile OpenRouter's
published per-request pricing with Parallel's own per-result pricing, this
provider records `estimated_results_returned` on every call so real cost
can be reconciled against your OpenRouter usage dashboard empirically.
"""

from __future__ import annotations

import os
import time

from research.providers.base import QueryOutcome, RawResult

PRIMARY_AI_MODEL = "inception/mercury-2.5-preview"


class OpenRouterSearchProvider:
    name = "openrouter_parallel"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        engine: str = "parallel",
        timeout: float = 25.0,
        model: str = PRIMARY_AI_MODEL,
    ):
        self._api_key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
        self._engine = engine
        self._timeout = timeout
        self._model = model
        self._client = None  # lazy: don't import/construct the SDK if unused

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self._api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={"HTTP-Referer": "http://localhost:8501", "X-Title": "Account Radar"},
                timeout=self._timeout,
                max_retries=0,
            )
        return self._client

    def search(self, query: str, *, num_results: int = 10) -> QueryOutcome:
        started = time.monotonic()
        if not self.is_configured():
            return QueryOutcome(
                query=query,
                provider=self.name,
                error="OPENROUTER_API_KEY not configured",
                elapsed_seconds=0.0,
            )

        plugin = {
            "id": "web",
            "engine": self._engine,
            "max_results": max(1, min(25, int(num_results))),
        }

        try:
            client = self._get_client()
            resp = client.chat.completions.create(
                model=self._model,
                # Minimal prompt: we are not asking the model to produce or
                # interpret results. This message exists only to trigger the
                # web-search tool call; its content is otherwise irrelevant,
                # and the model's reply is discarded below regardless of
                # what it says.
                messages=[{"role": "user", "content": query}],
                plugins=[plugin],
                max_tokens=16,
                temperature=0,
            )
        except Exception as exc:  # noqa: BLE001 - provider/transport failure, not a result
            return QueryOutcome(
                query=query,
                provider=self.name,
                error=f"OpenRouter request failed: {exc}",
                elapsed_seconds=time.monotonic() - started,
            )

        if not getattr(resp, "choices", None):
            return QueryOutcome(
                query=query,
                provider=self.name,
                error="OpenRouter returned no choices",
                elapsed_seconds=time.monotonic() - started,
            )

        message = resp.choices[0].message
        results = self._extract_annotations(message, query)

        usage = getattr(resp, "usage", None)
        cost = getattr(usage, "cost", None) if usage is not None else None
        elapsed = time.monotonic() - started
        outcome = QueryOutcome(
            query=query,
            provider=self.name,
            results=results,
            elapsed_seconds=elapsed,
        )
        # Attach empirical cost/result-count data as instance-level log
        # entries rather than inventing new QueryOutcome fields, so the
        # orchestration layer can print/record it for the cost
        # reconciliation Phase 1A called for.
        outcome.__dict__["_openrouter_result_count"] = len(results)
        outcome.__dict__["_openrouter_reported_cost"] = cost
        return outcome

    @staticmethod
    def _extract_annotations(message, query: str) -> list[RawResult]:
        """Pull url_citation annotations ONLY. Never touches message.content."""
        annotations = getattr(message, "annotations", None) or []
        # Some SDK/provider combinations only populate annotations in the
        # raw dict form; fall back to model_dump() for that shape, but this
        # is still reading structured provider-populated fields, never the
        # model's free-text reply.
        if not annotations and hasattr(message, "model_dump"):
            try:
                raw = message.model_dump()
                annotations = raw.get("annotations") or []
            except Exception:
                annotations = []

        out: list[RawResult] = []
        for rank, ann in enumerate(annotations):
            if hasattr(ann, "model_dump"):
                try:
                    ann = ann.model_dump()
                except Exception:
                    continue
            if not isinstance(ann, dict):
                continue
            citation = ann.get("url_citation") or ann.get("urlCitation") or {}
            if hasattr(citation, "model_dump"):
                try:
                    citation = citation.model_dump()
                except Exception:
                    citation = {}
            if not isinstance(citation, dict):
                continue
            url = str(citation.get("url") or "").strip()
            if not url:
                continue
            out.append(
                RawResult(
                    url=url,
                    title=str(citation.get("title") or "").strip()[:600],
                    snippet=str(citation.get("content") or "").strip()[:2000],
                    query=query,
                    provider="openrouter_parallel",
                    rank=rank,
                    published_at=str(citation.get("date") or citation.get("published_date") or "").strip() or None,
                )
            )
        return out

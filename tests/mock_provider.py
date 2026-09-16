"""Deterministic, synthetic SearchProvider for structural testing without
live API keys/network access.

IMPORTANT: this produces FAKE, clearly-synthetic data. It exists only to
prove the acquisition pipeline's mechanics (dedup, scoring, family-level
fallback assessment, diagnostics reporting) are correct in isolation from
any real search index. It is NOT a substitute for a live run against
Serper/OpenRouter and must never be presented as real acquisition results.

Results are generated purely from the query string at call time (via a
hash), so this works identically for ANY organization name passed in --
there is no table of known companies here, consistent with the
no-hardcoding constraint that also applies to production code.
"""

from __future__ import annotations

import hashlib
import re
import time

from research.providers.base import QueryOutcome, RawResult


def _stable_int(text: str, mod: int) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest(), 16) % mod


def _extract_quoted_company(query: str) -> str:
    m = re.search(r'"([^"]+)"', query)
    if m:
        return m.group(1)
    # Phase 1C-H4: research/linkedin.py's person-discovery queries are
    # deliberately plain/unquoted (see _person_queries), e.g.
    # "<company> CEO linkedin" or "<company> linkedin <country>". Fall
    # back to everything before the literal word "linkedin" as a
    # best-effort synthetic company label -- purely cosmetic (it only
    # feeds fake title/snippet text below), never used for real matching.
    m = re.split(r"\blinkedin\b", query, maxsplit=1, flags=re.I)
    prefix = m[0].strip()
    return prefix or "the organization"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "org"


class MockSearchProvider:
    """Synthetic provider. `sparse_for` lets a test deliberately simulate a
    provider that performs poorly for one named organization, to validate
    that family-level fallback logic actually triggers when it should
    (and does NOT trigger for organizations where coverage is healthy).
    """

    def __init__(self, name: str = "mock_primary", *, sparse_for: str | None = None, error_rate: float = 0.0):
        self.name = name
        self._sparse_for = (sparse_for or "").lower()
        self._error_rate = error_rate

    def is_configured(self) -> bool:
        return True

    def search(self, query: str, *, num_results: int = 10) -> QueryOutcome:
        started = time.monotonic()
        company = _extract_quoted_company(query)
        slug = _slug(company)

        if self._sparse_for and self._sparse_for in company.lower():
            # Deterministically simulate a provider that is genuinely
            # struggling for this organization: every query either errors
            # or returns nothing at all. This intentionally never returns
            # real results, so family-level coverage assessment sees
            # zero unique URLs / high error rate regardless of which
            # individual queries happen to run -- a reliable fixture for
            # testing the family-wide (not per-query) fallback trigger.
            if _stable_int(query, 3) == 0:
                return QueryOutcome(
                    query=query, provider=self.name, error="mock transport error (simulated sparse provider)"
                )
            return QueryOutcome(query=query, provider=self.name, results=[], elapsed_seconds=time.monotonic() - started)

        if self._error_rate and (_stable_int(query, 100) / 100.0) < self._error_rate:
            return QueryOutcome(query=query, provider=self.name, error="mock simulated transient error")

        n = min(num_results, 3 + _stable_int(query, 8))
        results = []
        low = query.lower()

        if "site:linkedin.com/in" in low or ("linkedin" in low and "site:" not in low):
            # Second condition: Phase 1C-H4's plain, unrestricted
            # LinkedIn person-discovery queries (no site: operator --
            # see research/linkedin.py._person_queries) are detected by
            # the literal "linkedin" keyword instead of an operator.
            for i in range(n):
                seed = f"{query}:{i}"
                pid = _stable_int(seed, 999999)
                results.append(
                    RawResult(
                        url=f"https://www.linkedin.com/in/{slug}-person-{pid}",
                        title=f"Synthetic Person {pid} — {company}",
                        snippet=f"Synthetic LinkedIn snippet for {company}, generated for testing (query: {query[:80]}).",
                        query=query,
                        provider=self.name,
                        rank=i,
                    )
                )
        elif "site:linkedin.com/company" in low:
            results.append(
                RawResult(
                    url=f"https://www.linkedin.com/company/{slug}",
                    title=f"{company} — LinkedIn",
                    snippet=f"Synthetic LinkedIn company page snippet for {company}.",
                    query=query,
                    provider=self.name,
                    rank=0,
                )
            )
        elif "site:" in low:
            domain = low.split("site:", 1)[1].split(" ", 1)[0]
            for i in range(min(n, 4)):
                results.append(
                    RawResult(
                        url=f"https://{domain}/synthetic-page-{i}",
                        title=f"{company} — Synthetic Corporate Page {i}",
                        snippet="Synthetic domain-restricted result for testing.",
                        query=query,
                        provider=self.name,
                        rank=i,
                    )
                )
        elif "official website" in low:
            results.append(
                RawResult(
                    url=f"https://www.{slug}.example",
                    title=f"{company} — Official Website",
                    snippet=f"Synthetic official-site result for {company}.",
                    query=query,
                    provider=self.name,
                    rank=0,
                )
            )
        else:
            for i in range(min(n, 5)):
                results.append(
                    RawResult(
                        url=f"https://news.example/{slug}/article-{i}",
                        title=f"{company} synthetic news item {i}",
                        snippet=f"Synthetic open-web snippet for {company} (query: {query[:60]}).",
                        query=query,
                        provider=self.name,
                        rank=i,
                    )
                )

        return QueryOutcome(query=query, provider=self.name, results=results, elapsed_seconds=time.monotonic() - started)

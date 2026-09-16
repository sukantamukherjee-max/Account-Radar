"""Provider-agnostic contract for search-result acquisition.

Every acquisition provider (Serper, OpenRouter/Parallel, or any future
provider) implements SearchProvider. A provider's job is narrow and
mechanical: take one query string, return whatever raw results the
underlying index has for it. A provider must NEVER:

  - call a language model to decide which results matter,
  - summarize, rewrite, or paraphrase a result's title/snippet,
  - silently drop results because "the model thought they weren't relevant".

Relevance/classification is a downstream AI-reasoning-layer concern (not
built in this phase). This layer's only job is honest, complete, structured
enumeration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RawResult:
    """One raw, unprocessed search result exactly as the provider returned it."""

    url: str
    title: str
    snippet: str
    query: str          # the exact query string that produced this result
    provider: str        # "serper" | "openrouter_parallel" | ...
    rank: int             # 0-based position in the provider's result set
    published_at: str | None = None  # provider-supplied publication date when available


class ProviderError(Exception):
    """Raised for transport/auth/config failures.

    Never raised for "the provider returned zero results" -- an empty list
    is a valid, meaningful outcome (it IS the signal that acquisition may be
    sparse) and must flow through as data, not as an exception.
    """

    def __init__(self, provider: str, message: str, *, retryable: bool = False):
        self.provider = provider
        self.retryable = retryable
        super().__init__(f"[{provider}] {message}")


@dataclass
class QueryOutcome:
    """The full, honest result of attempting one query against one provider.

    This is the unit acquisition diagnostics are built from. A query that
    returned zero results and a query that errored are different outcomes
    and must never be collapsed into the same "no results" bucket -- one is
    "nothing there", the other is "we don't actually know".
    """

    query: str
    provider: str
    results: list[RawResult] = field(default_factory=list)
    error: str | None = None
    elapsed_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


@runtime_checkable
class SearchProvider(Protocol):
    """Structural contract every acquisition provider implements."""

    name: str

    def search(self, query: str, *, num_results: int = 10) -> QueryOutcome:
        """Issue exactly one query. Return a QueryOutcome.

        Must not raise for an ordinary empty result set. May only raise
        ProviderError for genuine transport/auth/config failure, and even
        then callers are expected to catch it and record it as a
        QueryOutcome with .error set, not let it propagate and abort a
        whole acquisition run.
        """
        ...

    def is_configured(self) -> bool:
        """True if this provider has the credentials/config it needs."""
        ...

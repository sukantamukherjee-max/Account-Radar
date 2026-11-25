"""The ordered search-provider chain used for live research.

    1. Brave Search API  (BRAVE_SEARCH_API_KEY)  -- primary
    2. Tavily            (TAVILY_API_KEY)        -- fallback

Only providers whose key is configured are included, so an unconfigured
provider never costs a request or adds "not configured" noise to the
diagnostics. The router (research/providers/router.py) runs the first
provider in the chain for every query family and falls through to the next
one only when the family-level coverage rule says the current provider was
unavailable, errored, or genuinely sparse.

OpenRouter's search provider (openrouter_search.py) and the Serper provider
(serper.py) are intentionally NOT part of this chain; both remain in the
codebase as standalone providers (e.g. for offline benchmarking).
"""

from __future__ import annotations

from research.providers.base import SearchProvider
from research.providers.brave import BraveSearchProvider
from research.providers.tavily import TavilyProvider

SEARCH_PROVIDER_ORDER = ("brave", "tavily")
SEARCH_PROVIDER_KEYS = ("BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY")


def build_search_providers() -> list[SearchProvider]:
    """Configured providers in priority order (keys read from the environment)."""
    chain: list[SearchProvider] = [BraveSearchProvider(), TavilyProvider()]
    return [provider for provider in chain if provider.is_configured()]

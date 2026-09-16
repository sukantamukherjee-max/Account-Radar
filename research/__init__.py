"""Account Radar acquisition layer.

This package is 100% deterministic. Nothing in this package calls a
language model to enumerate, select, rank, or rewrite search results.
The only place an LLM appears is inside research.providers.openrouter_search,
and even there it is used purely to trigger a tool call; the model's
generated text is discarded and only the structured, provider-populated
citation/annotation data is used.
"""

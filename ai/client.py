"""AI client abstraction for the Phase 1D synthesis stage.

Mirrors the shape of research/providers/base.py's SearchProvider contract
deliberately: a narrow Protocol, a plain-data outcome type, and a real
implementation that never raises out of `complete()` -- transport/auth/
parse failures become a populated `.error`, not an exception that aborts
the caller. This is the ONLY place in this codebase an LLM is asked to
reason over content (as opposed to research/providers/openrouter_search.py,
which uses an LLM purely as a vehicle for a web-search tool call and never
reads the model's own text -- see that module's docstring). Nothing in
research/, commercial/, or research/analysis/ imports this module or is
ever executed by an LLM.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Per Phase 0 §F ("keep the model selection strategy"): a fast/cheap
# primary with a fallback if it's unavailable or errors. Overridable via
# env var so a deployment can swap models without a code change.
DEFAULT_PRIMARY_MODEL = os.getenv("ACCOUNT_RADAR_AI_MODEL", "inception/mercury-2.5-preview")
DEFAULT_FALLBACK_MODEL = os.getenv("ACCOUNT_RADAR_AI_FALLBACK_MODEL", "openai/gpt-4.1-nano")

# Phase 2 reliability hardening: max_tokens used to be a fixed 2000
# regardless of how much evidence the prompt actually asked the model to
# reason about and potentially cite/narrate, so completion capacity did not
# scale with account/corpus size. This estimate is a pure function of the
# PROMPT'S OWN SIZE/SHAPE (never of any organization/sector/country
# identity) -- it counts how many capabilities and how many citable/
# context evidence lines are actually present in the prompt text (see
# ai/prompt.py's "- capability_id:" / "[direct_evidence]" / etc. markers)
# and scales the output budget accordingly, bounded by a fixed floor and
# ceiling so it can never grow without bound.
_BASE_MAX_TOKENS = 2000
_MAX_TOKENS_CEILING = 6000
_TOKENS_PER_CAPABILITY = 90
_TOKENS_PER_EVIDENCE_LINE = 12


def _estimate_max_tokens(prompt: str) -> int:
    if not prompt:
        return _BASE_MAX_TOKENS
    capability_count = prompt.count("- capability_id:")
    evidence_line_count = (
        prompt.count("[direct_evidence]")
        + prompt.count("[strong_inference]")
        + prompt.count("[weak_relevance]")
    )
    budget = (
        _BASE_MAX_TOKENS
        + capability_count * _TOKENS_PER_CAPABILITY
        + evidence_line_count * _TOKENS_PER_EVIDENCE_LINE
    )
    return max(_BASE_MAX_TOKENS, min(_MAX_TOKENS_CEILING, budget))


@dataclass
class AIResponse:
    raw_text: str | None = None
    parsed_json: object | None = None
    model: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost: float | None = None

    @property
    def ok(self) -> bool:
        # Phase 2 reliability hardening: a syntactically valid JSON value
        # that is NOT an object (a bare array, string, number, bool...) is
        # not a usable Account Radar response -- every schema this client is
        # ever used for (see ai/schema.py) requires a top-level JSON object.
        # Treating a non-dict value as "ok" previously let it slip past the
        # primary/fallback model loop below (complete_json) unnoticed, so
        # the fallback model never got a chance to run for exactly the
        # failure mode that was observed live (a valid-but-wrong-shaped
        # response from the primary model).
        return self.error is None and isinstance(self.parsed_json, dict)


@runtime_checkable
class AIClient(Protocol):
    def is_configured(self) -> bool: ...

    def complete_json(self, prompt: str, *, schema_name: str) -> AIResponse:
        """Ask the model for a single JSON object matching the caller's
        expected shape (the caller -- ai/synthesis.py -- validates the
        result against the real schema; this layer's job is only to get
        back parseable JSON or an honest error). Must never raise.
        """
        ...


class OpenRouterAIClient:
    """Real AI client, used only when OPENROUTER_API_KEY is configured.

    JSON handling follows the defensive cascade Phase 0 called out as
    worth keeping from the prior implementation: try strict structured
    output, fall back to json_object mode, fall back to parsing the raw
    text as JSON -- but every stage that fails just produces an
    AIResponse.error, never an exception or a fabricated result.
    """

    name = "openrouter_ai"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_PRIMARY_MODEL,
        fallback_model: str | None = DEFAULT_FALLBACK_MODEL,
        timeout: float = 30.0,
    ):
        self._api_key = (api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
        self._model = model
        self._fallback_model = fallback_model
        self._timeout = timeout
        self._client = None

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

    def complete_json(self, prompt: str, *, schema_name: str) -> AIResponse:
        if not self.is_configured():
            return AIResponse(error="OPENROUTER_API_KEY not configured")

        models_to_try = [self._model] + ([self._fallback_model] if self._fallback_model else [])
        last_response: AIResponse | None = None
        for model in models_to_try:
            response = self._try_model(model, prompt)
            if response.ok:
                return response
            # Phase 2 reliability hardening: response.ok is False both for a
            # genuine transport/parse failure AND for a syntactically valid
            # but wrong-shaped JSON value (see AIResponse.ok above) -- either
            # way, this model didn't produce a usable response, so fall
            # through to the next configured model rather than returning
            # early. Nothing here is retried more than once per model.
            last_response = response
        return last_response or AIResponse(error="all configured models failed")

    def _try_model(self, model: str, prompt: str) -> AIResponse:
        started = time.monotonic()
        client = self._get_client()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=_estimate_max_tokens(prompt),
            )
        except Exception as exc:  # noqa: BLE001 - transport/provider failure, not a result
            return AIResponse(model=model, error=f"request failed: {exc}", elapsed_seconds=time.monotonic() - started)

        if not getattr(resp, "choices", None):
            return AIResponse(model=model, error="no choices returned", elapsed_seconds=time.monotonic() - started)

        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        parsed, parse_error = _parse_json_text(text)
        elapsed = time.monotonic() - started
        if parse_error:
            return AIResponse(raw_text=text, model=model, error=parse_error, elapsed_seconds=elapsed)
        return AIResponse(
            raw_text=text,
            parsed_json=parsed,
            model=model,
            elapsed_seconds=elapsed,
            prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
            cost=getattr(usage, "cost", None) if usage else None,
        )


def _parse_json_text(text: str) -> tuple[object | None, str | None]:
    if not text:
        return None, "empty response text"
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass
    # Defensive fallback: strip a ```json ... ``` fence if the model added
    # one despite json_object mode.
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        try:
            return json.loads(stripped.strip()), None
        except json.JSONDecodeError as exc:
            return None, f"could not parse response as JSON: {exc}"
    return None, "could not parse response as JSON"

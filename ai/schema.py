"""Strict JSON schema for the Phase 1D synthesis AI call.

The AI is asked for exactly this shape and nothing else. Schema validity is
necessary but not sufficient -- ai/synthesis.py additionally GROUNDS every
capability_id/source_url the response contains against the real evidence in
the input report before any of it is trusted (see that module).
"""

from __future__ import annotations

SYNTHESIS_JSON_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["account_narrative", "capability_narratives", "next_best_actions"],
    "properties": {
        "account_narrative": {"type": "string"},
        "capability_narratives": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["capability_id", "narrative", "cited_source_urls"],
                "properties": {
                    "capability_id": {"type": "string"},
                    "narrative": {"type": "string"},
                    "cited_source_urls": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "next_best_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["capability_id", "rationale", "rank"],
                "properties": {
                    "capability_id": {"type": "string"},
                    "rationale": {"type": "string"},
                    "rank": {"type": "integer"},
                },
            },
        },
    },
}


def validate_against_schema(payload: object) -> tuple[bool, str]:
    """Minimal, dependency-free structural validator (no jsonschema
    library dependency for this small, fixed shape). Returns (ok, reason).

    Deliberately conservative: unknown/extra top-level keys are rejected
    (additionalProperties semantics) so a model that starts inventing new
    fields fails loudly (ai_failed) rather than those fields being silently
    ignored and creating a false sense of a richer, validated response.
    """
    if not isinstance(payload, dict):
        return False, "top-level response is not a JSON object"

    required = SYNTHESIS_JSON_SCHEMA["required"]
    missing = [k for k in required if k not in payload]
    if missing:
        return False, f"missing required top-level field(s): {missing}"

    allowed_top = set(SYNTHESIS_JSON_SCHEMA["properties"].keys())
    extra_top = set(payload.keys()) - allowed_top
    if extra_top:
        return False, f"unexpected top-level field(s): {sorted(extra_top)}"

    if not isinstance(payload.get("account_narrative"), str):
        return False, "account_narrative must be a string"

    cap_narratives = payload.get("capability_narratives")
    if not isinstance(cap_narratives, list):
        return False, "capability_narratives must be an array"
    for i, item in enumerate(cap_narratives):
        ok, reason = _validate_capability_narrative_item(item)
        if not ok:
            return False, f"capability_narratives[{i}]: {reason}"

    actions = payload.get("next_best_actions")
    if not isinstance(actions, list):
        return False, "next_best_actions must be an array"
    for i, item in enumerate(actions):
        ok, reason = _validate_next_action_item(item)
        if not ok:
            return False, f"next_best_actions[{i}]: {reason}"

    return True, "ok"


def _validate_capability_narrative_item(item: object) -> tuple[bool, str]:
    if not isinstance(item, dict):
        return False, "not an object"
    required = {"capability_id", "narrative", "cited_source_urls"}
    missing = required - set(item.keys())
    if missing:
        return False, f"missing field(s): {sorted(missing)}"
    extra = set(item.keys()) - required
    if extra:
        return False, f"unexpected field(s): {sorted(extra)}"
    if not isinstance(item["capability_id"], str) or not item["capability_id"]:
        return False, "capability_id must be a non-empty string"
    if not isinstance(item["narrative"], str):
        return False, "narrative must be a string"
    urls = item["cited_source_urls"]
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        return False, "cited_source_urls must be an array of strings"
    return True, "ok"


def _validate_next_action_item(item: object) -> tuple[bool, str]:
    if not isinstance(item, dict):
        return False, "not an object"
    required = {"capability_id", "rationale", "rank"}
    missing = required - set(item.keys())
    if missing:
        return False, f"missing field(s): {sorted(missing)}"
    extra = set(item.keys()) - required
    if extra:
        return False, f"unexpected field(s): {sorted(extra)}"
    if not isinstance(item["capability_id"], str) or not item["capability_id"]:
        return False, "capability_id must be a non-empty string"
    if not isinstance(item["rationale"], str):
        return False, "rationale must be a string"
    if not isinstance(item["rank"], int) or isinstance(item["rank"], bool):
        return False, "rank must be an integer"
    return True, "ok"

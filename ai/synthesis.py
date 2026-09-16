"""Phase 1D synthesis orchestration: report -> prompt -> AI call -> schema
validation -> GROUNDING validation -> SynthesisResult.

This is the one place an AI response is turned into something the rest of
the application trusts, so it is deliberately paranoid: every fact the AI
asserts is checked against the deterministic evidence it was given before
being kept. Nothing here ever mutates the input AccountRadarReport, and
nothing here ever overrides an evidence_level/confidence the deterministic
commercial/evidence_mapping.py layer already computed -- nothing in this
module recomputes an evidence_level or a confidence value; it only ever
COPIES the value already present on the matching CapabilityMapping.
"""

from __future__ import annotations

from ai.client import AIClient
from ai.models import CapabilityNarrative, NextBestAction, SynthesisResult, SynthesisStatus
from ai.prompt import build_synthesis_prompt
from commercial.opportunities import build_opportunity_hypotheses
from ai.schema import validate_against_schema
from research.analysis.report import AccountRadarReport

_EVIDENCE_LEVEL_RANK = {"direct_evidence": 2, "strong_inference": 1, "weak_relevance": 0}

# Phase 2 reliability hardening: exactly ONE bounded corrective retry when
# the AI's response is valid JSON but fails structural schema validation
# (wrong top-level type, missing/extra keys, wrong item shapes, etc.). This
# is deliberately generic -- no organization/sector/country ever appears in
# it -- and it changes nothing about what is ACCEPTED: the retried response
# is run through the exact same validate_against_schema() and the exact
# same grounding step as a first-try response. If the retry also fails, the
# result remains ai_failed; there is never a second retry.
_SCHEMA_REPAIR_INSTRUCTION_TEMPLATE = (
    "\n\nCORRECTION REQUIRED: your previous response did not match the required shape "
    "(reason: {reason}). Return ONLY a single JSON object with exactly these three top-level "
    "keys: account_narrative (a string), capability_narratives (an array), and "
    "next_best_actions (an array) -- in precisely the shape already specified above. Do not "
    "return an array, a string, or any other JSON value as the top-level response. Do not add, "
    "omit, or rename any field. Do not wrap the JSON in markdown code fences or add any other text."
)


def synthesize_account(report: AccountRadarReport, client: AIClient) -> SynthesisResult:
    if report.status == "insufficient_evidence":
        return SynthesisResult(
            status=SynthesisStatus.INSUFFICIENT_EVIDENCE,
            organization_name=report.organization_name,
            notes=[f"underlying report has insufficient evidence: {report.qualification.corpus_reason}"],
        )

    if not client.is_configured():
        return SynthesisResult(
            status=SynthesisStatus.AI_UNCONFIGURED,
            organization_name=report.organization_name,
            notes=["no AI client configured (e.g. missing API key) -- synthesis was not attempted"],
        )

    prompt = build_synthesis_prompt(report)
    response = client.complete_json(prompt, schema_name="synthesis_v1")

    # A genuine transport/parse failure (response.error set, or no JSON
    # value could be parsed at all) is never worth retrying -- there is
    # nothing shape-related to correct. A response that DID parse to some
    # JSON value (even a non-dict one) is left to schema validation below,
    # which is what decides whether the bounded repair retry applies.
    if response.error is not None or response.parsed_json is None:
        return SynthesisResult(
            status=SynthesisStatus.AI_FAILED,
            organization_name=report.organization_name,
            model=response.model,
            notes=[f"AI call failed: {response.error}"],
        )

    schema_ok, schema_reason = validate_against_schema(response.parsed_json)
    if not schema_ok:
        repaired = _attempt_schema_repair(client, prompt, schema_reason)
        if repaired is None:
            return SynthesisResult(
                status=SynthesisStatus.AI_FAILED,
                organization_name=report.organization_name,
                model=response.model,
                notes=[
                    f"AI response failed schema validation: {schema_reason}",
                    "one bounded schema-repair retry was attempted and did not produce a valid response",
                ],
            )
        result = _ground_and_build_result(report, repaired.parsed_json, model=repaired.model)
        result.notes.insert(
            0,
            f"initial AI response failed schema validation ({schema_reason}); "
            f"recovered via one bounded schema-repair retry",
        )
        return result

    return _ground_and_build_result(report, response.parsed_json, model=response.model)


def _attempt_schema_repair(client: AIClient, prompt: str, schema_reason: str):
    """Exactly one corrective retry for a schema-shape failure. Returns the
    repaired AIResponse if -- and only if -- the retried response ALSO
    passes the same unweakened validate_against_schema() used for the
    original attempt; returns None otherwise. Never retried more than once;
    never treated as successful on anything less than full schema validity.
    """
    repair_prompt = prompt + _SCHEMA_REPAIR_INSTRUCTION_TEMPLATE.format(reason=schema_reason)
    repair_response = client.complete_json(repair_prompt, schema_name="synthesis_v1")
    if repair_response.error is not None or repair_response.parsed_json is None:
        return None
    repair_ok, _repair_reason = validate_against_schema(repair_response.parsed_json)
    if not repair_ok:
        return None
    return repair_response


def _ground_and_build_result(report: AccountRadarReport, payload: dict, *, model: str | None) -> SynthesisResult:
    notes: list[str] = []
    degraded = False

    # Index the real, deterministic evidence for O(1) grounding lookups.
    # capability_id -> {source_url -> mapping_dict}
    evidence_index: dict[str, dict[str, dict]] = {}
    # capability_id -> capability_name
    name_index: dict[str, str] = {}
    for m in report.capability_mappings:
        evidence_index.setdefault(m.capability_id, {})[m.source_url] = m.to_dict()
        name_index[m.capability_id] = m.capability_name

    eligible_ids = {
        m.capability_id
        for m in report.capability_mappings
        if m.evidence_level.value in ("direct_evidence", "strong_inference")
    }

    capability_narratives: list[CapabilityNarrative] = []
    for item in payload.get("capability_narratives", []):
        cid = item["capability_id"]
        if cid not in evidence_index:
            notes.append(f"dropped capability_narrative for unknown capability_id {cid!r} (not in this report's evidence)")
            degraded = True
            continue
        real_sources = evidence_index[cid]
        eligible_sources = {
            u: m for u, m in real_sources.items()
            if m["evidence_level"] in ("direct_evidence", "strong_inference")
        }
        cited = [u for u in item["cited_source_urls"] if u in eligible_sources]
        dropped = [u for u in item["cited_source_urls"] if u not in eligible_sources]
        if dropped:
            notes.append(
                f"dropped {len(dropped)} unsupported/ineligible cited_source_url(s) for capability {cid!r}: {dropped}"
            )
            degraded = True
        if not cited:
            notes.append(f"dropped capability_narrative for {cid!r}: no cited source survived grounding")
            degraded = True
            continue
        best = max((real_sources[u] for u in cited), key=lambda d: _EVIDENCE_LEVEL_RANK.get(d["evidence_level"], -1))
        capability_narratives.append(
            CapabilityNarrative(
                capability_id=cid,
                capability_name=name_index.get(cid, cid),
                narrative=item["narrative"],
                cited_source_urls=cited,
                best_evidence_level=best["evidence_level"],
                best_confidence=best["confidence"],
                # Phase 2 hardening: copied from the real mapping, never
                # accepted from the AI response (the schema has no field for
                # either -- see ai/schema.py).
                best_evidence_category=best.get("evidence_category") or "",
                best_epistemic_status=best.get("epistemic_status") or "",
            )
        )

    next_best_actions: list[NextBestAction] = []
    for item in payload.get("next_best_actions", []):
        cid = item["capability_id"]
        if cid not in eligible_ids:
            notes.append(
                f"dropped next_best_action for capability_id {cid!r}: not eligible "
                f"(no direct_evidence/strong_inference mapping in this report)"
            )
            degraded = True
            continue
        best = max(
            (v for v in evidence_index.get(cid, {}).values() if v["evidence_level"] in ("direct_evidence", "strong_inference")),
            key=lambda d: (_EVIDENCE_LEVEL_RANK.get(d["evidence_level"], -1), d["confidence"]),
        )
        next_best_actions.append(
            NextBestAction(
                capability_id=cid,
                capability_name=name_index.get(cid, cid),
                rank=item["rank"],
                rationale=item["rationale"],
                evidence_level=best["evidence_level"],
                confidence=best["confidence"],
                evidence_category=best.get("evidence_category") or "",
                epistemic_status=best.get("epistemic_status") or "",
            )
        )
    # Re-sort and re-number deterministically -- the AI's `rank` values are
    # a hint, never trusted as already correctly ordered/contiguous/unique.
    next_best_actions.sort(key=lambda a: (a.rank, -a.confidence, a.capability_id))
    for i, action in enumerate(next_best_actions, start=1):
        action.rank = i

    # Phase 1F safety-net: AI is allowed to rank and phrase actions, but it
    # is not allowed to suppress an otherwise eligible commercial path by
    # returning an empty list. Fill missing actions deterministically from
    # the evidence-backed opportunity hypotheses. This keeps the output a
    # hypothesis/validation list rather than inventing a confirmed need.
    deterministic = build_opportunity_hypotheses(report.capability_mappings, max_items=5)
    existing_ids = {a.capability_id for a in next_best_actions}
    if deterministic and not next_best_actions:
        for candidate in deterministic:
            if candidate.capability_id in existing_ids:
                continue
            next_best_actions.append(
                NextBestAction(
                    capability_id=candidate.capability_id,
                    capability_name=candidate.capability_name,
                    rank=len(next_best_actions) + 1,
                    rationale=(
                        f"Validation step: {candidate.next_step} "
                        f"Evidence-backed hypothesis: {candidate.hypothesis}"
                    ),
                    evidence_level=candidate.evidence_level,
                    confidence=candidate.confidence,
                    origin="deterministic_guardrail",
                    evidence_category="",  # a guardrail action spans one capability's whole evidence group, not one mapping -- no single evidence_category applies
                    epistemic_status=candidate.epistemic_status,
                )
            )
        if len(next_best_actions) > 5:
            next_best_actions = next_best_actions[:5]
        if not payload.get("next_best_actions"):
            notes.append("AI returned no next_best_actions; deterministic evidence-backed guardrail supplied the shortlist")
            degraded = True
        else:
            notes.append("AI next_best_actions were rejected by grounding; deterministic evidence-backed guardrail supplied the shortlist")
            degraded = True

    next_best_actions.sort(key=lambda a: (-a.confidence, a.capability_id))
    for i, action in enumerate(next_best_actions, start=1):
        action.rank = i

    status = SynthesisStatus.DEGRADED if degraded else SynthesisStatus.OK
    return SynthesisResult(
        status=status,
        organization_name=report.organization_name,
        account_narrative=payload.get("account_narrative", ""),
        capability_narratives=capability_narratives,
        next_best_actions=next_best_actions,
        model=model,
        notes=notes,
    )

"""Deterministic prompt construction for the Phase 1D synthesis call.

The prompt is built ENTIRELY from the already-qualified, already-graded
evidence in an AccountRadarReport -- never from raw page content, never
from anything not already present in the deterministic report. This is
what keeps the AI's job to "write prose about this evidence" rather than
"go find facts" (that job stays deterministic, per H1-H4). No organization,
sector, or country name is ever part of the template text itself -- only
values read off the report object passed in.
"""

from __future__ import annotations

from research.analysis.report import AccountRadarReport

_INSTRUCTIONS = """You are summarizing pre-verified sales research evidence for one organization. \
You must not invent facts, capabilities, sources, or evidence that is not given to you below. \
Every capability_id and cited_source_urls entry you output MUST come from the "capability_evidence" \
list below, copied exactly (capability_id and URL text). Do not cite a source_url that is not listed \
under the capability you are citing it for. Each capability's evidence below is split into two \
clearly labeled sections: "ELIGIBLE TO CITE" (direct_evidence / strong_inference -- these are the \
ONLY source URLs you may ever place in cited_source_urls) and "CONTEXT ONLY -- DO NOT CITE" \
(weak_relevance -- background only; only a sample of it may be shown, and the sample size tells you \
nothing about how much or how little evidence actually exists). Never cite a URL from the "CONTEXT \
ONLY" section under any circumstances. Do not propose a next_best_action for a capability that has \
no entry in "eligible_for_action" below. If the evidence is thin, say so plainly in account_narrative \
rather than overstating it. Weak/incidental evidence must never be described as a confirmed \
requirement. Each evidence line below also shows its evidence_category (what KIND of evidence it is), \
inference_cluster_id (evidence lines sharing the same cluster id come from the SAME underlying \
business event, not independent corroboration -- do not describe them as separate proof points), and \
epistemic_status ("confirmed_evidence" vs "inference_hypothesis"). These three fields are provided \
for your context ONLY, already decided by the application -- you have no field in your output to \
set, change, or repeat them, and your narrative wording must stay consistent with epistemic_status: \
never describe an "inference_hypothesis" line using confirmed/certain language. Use the deterministic \
action candidates as the preferred source for next-best-action selection; if eligible candidates \
exist, return at least one grounded next_best_action. Respond with ONLY a single JSON object matching \
this exact shape (no prose outside JSON, no markdown code fences, the top-level value itself must be \
a JSON object and nothing else):

{
  "account_narrative": "<2-4 sentence plain-language summary of what the evidence shows overall>",
  "capability_narratives": [
    {"capability_id": "<id from capability_evidence>", "narrative": "<1-3 sentences>", "cited_source_urls": ["<url from that capability's eligible sources>"]}
  ],
  "next_best_actions": [
    {"capability_id": "<id from eligible_for_action>", "rationale": "<1-2 sentences>", "rank": <integer, 1 = highest priority>}
  ]
}"""


def build_synthesis_prompt(report: AccountRadarReport) -> str:
    org_block = _organization_block(report)
    evidence_block = _capability_evidence_block(report)
    eligible_block = _eligible_for_action_block(report)
    actions_block = _deterministic_action_block(report)
    signals_block = _signals_block(report)
    incumbent_block = _incumbent_block(report)

    return (
        f"{_INSTRUCTIONS}\n\n"
        f"ORGANIZATION:\n{org_block}\n\n"
        f"CAPABILITY EVIDENCE (capability_evidence):\n{evidence_block}\n\n"
        f"ELIGIBLE FOR A NEXT-BEST-ACTION (eligible_for_action -- only capabilities with "
        f"direct_evidence or strong_inference support):\n{eligible_block}\n\n"
        f"DETERMINISTIC ACTION CANDIDATES (safety-net -- choose from these when recommending actions):\n{actions_block}\n\n"
        f"CUSTOMER SIGNALS (context only, not capability-specific):\n{signals_block}\n\n"
        f"INCUMBENT / VENDOR CONTEXT (context only -- never a reason to inflate confidence):\n{incumbent_block}\n"
    )


def _organization_block(report: AccountRadarReport) -> str:
    return (
        f"name: {report.organization_name}\n"
        f"country: {report.country or 'unknown'}\n"
        f"corpus_status: {report.status}\n"
        f"corpus_reason: {report.qualification.corpus_reason}"
    )


# Phase 2 reliability hardening: weak_relevance is context-only and, at
# scale, dominates the evidence block without ever being citable -- capping
# it (never direct_evidence/strong_inference, which stay fully uncapped
# because they are the only evidence eligible to be cited) keeps the prompt
# from growing without bound on large accounts. This changes ONLY what the
# model is shown; ai/synthesis.py's grounding step still validates every
# citation against the COMPLETE, uncapped report.capability_mappings, so
# capping here can never make an otherwise-ineligible URL acceptable and
# can never make an eligible URL unavailable to cite.
_MAX_CONTEXT_ONLY_LINES_PER_CAPABILITY = 5


def _capability_evidence_block(report: AccountRadarReport) -> str:
    summary = report.capability_summary()
    if not summary:
        return "(no capability evidence in this report)"
    lines = []
    for capability_id, bucket in summary.items():
        lines.append(f"- capability_id: {capability_id} ({bucket['capability_name']})")

        eligible_items = [
            (level, m) for level in ("direct_evidence", "strong_inference") for m in bucket[level]
        ]
        lines.append("    ELIGIBLE TO CITE (direct_evidence / strong_inference -- citable for this capability):")
        if eligible_items:
            for level, m in eligible_items:
                lines.append(_evidence_line(level, m))
        else:
            lines.append("      (none)")

        weak_items = bucket["weak_relevance"]
        lines.append("    CONTEXT ONLY -- DO NOT CITE (weak_relevance):")
        if weak_items:
            shown = weak_items[:_MAX_CONTEXT_ONLY_LINES_PER_CAPABILITY]
            for m in shown:
                lines.append(_evidence_line("weak_relevance", m))
            omitted = len(weak_items) - len(shown)
            if omitted > 0:
                lines.append(
                    f"      ... plus {omitted} more weak_relevance item(s) not shown here (still context "
                    f"only, never citable; omitted purely to keep this prompt a manageable size -- "
                    f"grounding always checks citations against the complete evidence set, not just what "
                    f"is shown here, so this omission never changes what you are allowed to cite)"
                )
        else:
            lines.append("      (none)")
    return "\n".join(lines)


def _evidence_line(level: str, m: dict) -> str:
    return (
        f"      [{level}] confidence={m['confidence']} source_url={m['source_url']} "
        f"matched_terms={m['matched_terms']} context={m['supporting_context'][:160]!r} "
        f"evidence_category={m.get('evidence_category')} "
        f"inference_cluster_id={m.get('inference_cluster_id')} "
        f"epistemic_status={m.get('epistemic_status')}"
    )


def _eligible_for_action_block(report: AccountRadarReport) -> str:
    eligible_ids = _eligible_capability_ids(report)
    if not eligible_ids:
        return "(none -- no capability has direct_evidence or strong_inference support)"
    return "\n".join(f"- {cid}" for cid in sorted(eligible_ids))


def _eligible_capability_ids(report: AccountRadarReport) -> set[str]:
    return {
        m.capability_id
        for m in report.capability_mappings
        if m.evidence_level.value in ("direct_evidence", "strong_inference")
    }


def _signals_block(report: AccountRadarReport) -> str:
    if not report.signals:
        return "(none)"
    lines = []
    for s in report.signals[:20]:  # keep the prompt compact; this is context, not the citable evidence
        lines.append(f"- [{s.signal_type}] {s.text[:160]!r} <- {s.source_url}")
    return "\n".join(lines)


def _incumbent_block(report: AccountRadarReport) -> str:
    if not report.incumbent_evidence:
        return "(none)"
    lines = []
    for i in report.incumbent_evidence[:20]:
        lines.append(f"- [{i.category}] {i.vendor_or_partner_term!r} <- {i.source_url}")
    return "\n".join(lines)


def _deterministic_action_block(report: AccountRadarReport) -> str:
    if not report.opportunity_hypotheses:
        return "(none)"
    lines = []
    for o in report.opportunity_hypotheses:
        lines.append(
            f"- {o.capability_id} | priority={o.priority} score={o.score} | "
            f"epistemic_status={o.epistemic_status} | "
            f"independent_domains={o.independent_domain_count} inference_clusters={o.inference_cluster_count} | "
            f"who={o.who} | next_step={o.next_step} | conversation={o.conversation}"
        )
    return "\n".join(lines)

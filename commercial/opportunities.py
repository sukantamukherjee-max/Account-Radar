"""Deterministic commercial opportunity/hypothesis engine (Phase 1F).

This module deliberately does NOT claim that a customer has a confirmed
buying requirement. It converts only direct_evidence/strong_inference
capability mappings into *potential sales hypotheses* and deterministic next
steps. AI may improve wording/ranking downstream, but this safety-net can
still produce an actionable shortlist when the model returns none.

No organization, sector, country, vendor, or customer-specific branching is
used here. All commercial language comes from the configurable Otech
capability catalogue and the evidence already present in the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from commercial.capabilities import get_catalog
from commercial.models import CapabilityMapping, EvidenceCategory, EvidenceLevel, epistemic_status_for

_EVIDENCE_RANK = {
    EvidenceLevel.DIRECT_EVIDENCE.value: 2,
    EvidenceLevel.STRONG_INFERENCE.value: 1,
}

# Evidence-relevance hardening (recommendation #1): ONLY these two evidence
# categories -- an organization's own concrete technology event, or a
# procurement/tender signal -- may inflate the domain/cluster DIVERSITY
# bonus in `_score()`. `business_signal_inference` (a generic trigger term
# plus a locally-associated qualifying business signal -- an INFERENCE, not
# a direct technology mention) may still contribute to the base
# evidence_count/independent_source_count a viewer sees (nothing here
# removes or hides that evidence), but several inference-only mappings
# across several domains must not, by themselves, make an opportunity's
# SCORE look as strong as one backed by genuine organizational technology
# events -- that is precisely "evidence diversity mistaken for evidence
# relevance."
_BONUS_ELIGIBLE_CATEGORIES = {
    EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT.value,
    EvidenceCategory.PROCUREMENT_SIGNAL.value,
}


def _bonus_eligible(mappings: list[CapabilityMapping]) -> list[CapabilityMapping]:
    return [m for m in mappings if m.evidence_category in _BONUS_ELIGIBLE_CATEGORIES]


# Current/Change/Requirement Signal Gate: EXISTING_CAPABILITY_CONTEXT
# mappings (two or more specific catalogue terms grounded to the
# organization, but no locally-grounded change/requirement signal --
# commercial/evidence_mapping.py Rule 2/3 branch 1) describe an EXISTING
# capability, not a current requirement. They must never be the SOLE basis
# for an opportunity, and must never set or inflate the strongest-evidence
# confidence/level that drives `_score()` -- being merely excluded from
# `_BONUS_ELIGIBLE_CATEGORIES` above is NOT sufficient for this, because
# `_BONUS_ELIGIBLE_CATEGORIES` only gates the diversity BONUS terms in
# `_score()`; it does nothing to stop a lone EXISTING_CAPABILITY_CONTEXT
# mapping (STRONG_INFERENCE, which IS in `_EVIDENCE_RANK`) from being
# picked up as `eligible`/`best` and independently producing an opportunity
# at `best.confidence * 0.70`. `_independently_qualifying` is the actual
# gate: a mapping counts toward "is there a reason to create/anchor an
# opportunity for this capability" only when its evidence_category is
# something OTHER than EXISTING_CAPABILITY_CONTEXT. When at least one such
# mapping exists for a capability, EXISTING_CAPABILITY_CONTEXT mappings for
# that same capability remain in the group as visible, explainable
# supporting context (still counted in evidence_count/source_urls/rationale
# diversity) -- they are simply never the mapping that DETERMINES the
# opportunity exists, nor the one whose confidence/evidence_level the
# opportunity is scored/labeled from.
def _independently_qualifying(mappings: list[CapabilityMapping]) -> list[CapabilityMapping]:
    return [m for m in mappings if m.evidence_category != EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value]


# Evidence-intelligence fix (defense in depth): an opportunity may only be
# labeled confirmed_evidence when its strongest mapping is DIRECT_EVIDENCE of
# an ACCOUNT-EVENT kind -- the organization's own technology event or a
# procurement signal. evidence_mapping.py already only produces DIRECT
# mappings of those two categories from account-centred sources; this guard
# makes the invariant structural here too, so a future rule (or a mapping
# built elsewhere) carrying a context category -- industry context,
# current-state technology, stakeholder/hiring context, incidental mention,
# customer-requirement or existing-capability context, or an inference --
# can never make an opportunity "confirmed" on its own. Such an opportunity is
# kept, scored as before, and labeled honestly as an inference hypothesis.
# A legacy mapping with no category (hand-built, pre-Phase-2) is unaffected.
_CONFIRMABLE_CATEGORIES = {
    EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT.value,
    EvidenceCategory.PROCUREMENT_SIGNAL.value,
}


def _confirms_account_evidence(m: CapabilityMapping) -> bool:
    return m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE and (
        m.evidence_category is None or m.evidence_category in _CONFIRMABLE_CATEGORIES
    )


def _domain_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


@dataclass
class OpportunityHypothesis:
    capability_id: str
    capability_name: str
    priority: str
    score: float
    evidence_level: str
    confidence: float
    evidence_count: int
    independent_source_count: int
    hypothesis: str
    rationale: str
    who: str
    conversation: str
    next_step: str
    source_urls: list[str] = field(default_factory=list)
    incumbent_context: list[str] = field(default_factory=list)
    # Phase 2 hardening (item 5/9): expose the DIVERSITY the score is
    # actually based on, not just a raw evidence_count, so a viewer (or the
    # AI, or the UI) can see whether this hypothesis rests on several
    # genuinely independent sources/events, or on one event described once.
    independent_domain_count: int = 0
    inference_cluster_count: int = 0
    # The strongest mapping's epistemic_status -- "confirmed_evidence" if
    # any contributing mapping is direct_evidence, else "inference_hypothesis".
    # Deterministic, derived the same way CapabilityMapping.epistemic_status
    # is (see commercial.models.epistemic_status_for) -- never AI-assigned.
    epistemic_status: str = ""

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "capability_name": self.capability_name,
            "priority": self.priority,
            "score": self.score,
            "evidence_level": self.evidence_level,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "independent_source_count": self.independent_source_count,
            "hypothesis": self.hypothesis,
            "rationale": self.rationale,
            "who": self.who,
            "conversation": self.conversation,
            "next_step": self.next_step,
            "source_urls": self.source_urls,
            "incumbent_context": self.incumbent_context,
            "independent_domain_count": self.independent_domain_count,
            "inference_cluster_count": self.inference_cluster_count,
            "epistemic_status": self.epistemic_status,
        }


def _catalog_by_id() -> dict[str, dict]:
    return {c["id"]: c for c in get_catalog()}


def _priority(score: float) -> str:
    if score >= 75:
        return "high"
    if score >= 55:
        return "medium"
    return "watch"


def _diversity_counts(eligible: list[CapabilityMapping]) -> tuple[int, int, int, int]:
    """Phase 2 hardening (item 5): the inputs opportunity scoring should
    actually be based on -- distinct SOURCE DOMAINS and distinct
    INFERENCE CLUSTERS (underlying events) among the eligible mappings,
    split by evidence level. Deliberately NOT raw mapping/URL counts:
    several mappings that share one inference_cluster_id all trace back to
    the SAME underlying business event (e.g. one "new facility"
    announcement legitimately producing several capability hypotheses at
    once) and must count as ONE corroborating data point, not N -- exactly
    the "do not allow ... multiple mappings from the same underlying event
    to artificially inflate opportunity confidence" requirement.

    Returns (unique_domain_count, unique_cluster_count, direct_cluster_count,
    inference_cluster_count).
    """
    unique_domains = {d for d in (_domain_of(m.source_url) for m in eligible) if d}
    # A mapping without an inference_cluster_id (shouldn't happen post-Phase
    # 2, but defensively handled) falls back to its own source_url so it is
    # never silently merged with an unrelated mapping into one cluster.
    all_clusters = {m.inference_cluster_id or f"__no_cluster__:{m.source_url}" for m in eligible}
    direct_clusters = {
        m.inference_cluster_id or f"__no_cluster__:{m.source_url}"
        for m in eligible if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE
    }
    inference_clusters = {
        m.inference_cluster_id or f"__no_cluster__:{m.source_url}"
        for m in eligible if m.evidence_level == EvidenceLevel.STRONG_INFERENCE
    }
    return len(unique_domains), len(all_clusters), len(direct_clusters), len(inference_clusters)


def _score(mappings: list[CapabilityMapping]) -> float:
    """Transparent, bounded score. The single strongest mapping's confidence
    is the dominant factor; corroboration bonuses reward genuine
    INDEPENDENCE of evidence -- distinct source domains and distinct
    underlying events -- never raw mapping or URL counts (Phase 2
    hardening, item 5). Incumbent evidence is not used as a negative or
    positive opportunity signal by itself.
    """
    eligible = [m for m in mappings if m.evidence_level.value in _EVIDENCE_RANK]
    if not eligible:
        return 0.0
    # Current/Change/Requirement Signal Gate: `best` -- whose confidence
    # anchors the whole score -- must never be an EXISTING_CAPABILITY_CONTEXT
    # mapping. If none of the eligible mappings independently qualify, this
    # capability has no opportunity-worthy evidence at all (score 0),
    # regardless of how many existing-capability mentions were found.
    qualifying = _independently_qualifying(eligible)
    if not qualifying:
        return 0.0
    best = max(qualifying, key=lambda m: (_EVIDENCE_RANK[m.evidence_level.value], m.confidence))
    # Evidence-relevance hardening (recommendation #1): the diversity bonus
    # terms below are computed ONLY over bonus-eligible evidence (see
    # `_BONUS_ELIGIBLE_CATEGORIES`) -- never over the full `eligible` set --
    # so several business_signal_inference mappings scattered across
    # several domains can no longer inflate the score the way genuine
    # organizational-technology-event/procurement-signal diversity does.
    unique_domain_count, _, direct_cluster_count, inference_cluster_count = _diversity_counts(_bonus_eligible(eligible))

    score = best.confidence * 0.70
    score += min(15.0, max(0, unique_domain_count - 1) * 5.0)
    score += min(10.0, direct_cluster_count * 5.0)
    score += min(5.0, inference_cluster_count * 1.5)
    return round(min(100.0, score), 1)


def build_opportunity_hypotheses(mappings: list[CapabilityMapping], *, max_items: int = 5) -> list[OpportunityHypothesis]:
    """Build a ranked, evidence-backed shortlist from deterministic mappings.

    Weak relevance is intentionally excluded. The resulting language is a
    validation hypothesis, not a claim that the customer is buying.
    """
    catalog = _catalog_by_id()
    grouped: dict[str, list[CapabilityMapping]] = {}
    for m in mappings:
        if m.evidence_level.value not in _EVIDENCE_RANK:
            continue
        grouped.setdefault(m.capability_id, []).append(m)

    out: list[OpportunityHypothesis] = []
    for cid, group in grouped.items():
        # Current/Change/Requirement Signal Gate: a capability whose ONLY
        # DIRECT_EVIDENCE/STRONG_INFERENCE mappings are EXISTING_CAPABILITY_
        # CONTEXT (existing-capability-only, no change/requirement signal)
        # gets NO opportunity at all -- "the airport has security systems"
        # must not, by itself, create a Cybersecurity opportunity. When a
        # genuinely qualifying mapping ALSO exists for this capability, the
        # existing-capability mapping(s) stay in `group` below as visible
        # supporting context (evidence_count, source_urls, rationale
        # diversity), but never determine whether the opportunity exists or
        # what its evidence_level/confidence is (see `best` below).
        qualifying = _independently_qualifying(group)
        if not qualifying:
            continue
        c = catalog.get(cid)
        if not c:
            continue
        best = max(qualifying, key=lambda m: (_EVIDENCE_RANK[m.evidence_level.value], m.confidence))
        unique_urls = sorted({m.source_url for m in group if m.source_url})
        score = _score(group)
        domain_count, cluster_count, _, _ = _diversity_counts(qualifying)
        signal_types = sorted({m.signal_type for m in group if m.signal_type})
        signal_phrase = ", ".join(signal_types) if signal_types else "technology evidence"
        hypothesis = (
            f"Potential {c['name']} opportunity to validate: the available evidence indicates "
            f"{signal_phrase}. This is a sales hypothesis, not confirmation of a buying requirement."
        )
        rationale = (
            f"Supported by {len(unique_urls)} source URL(s) across {domain_count} independent "
            f"domain(s) and {cluster_count} distinct underlying event/evidence cluster(s), with "
            f"strongest evidence classified as {best.evidence_level.value} "
            f"({best.confidence:.0f}/100 confidence)."
        )
        incumbent = sorted({term for m in group for term in m.incumbent_context})
        if best.evidence_level == EvidenceLevel.DIRECT_EVIDENCE and not _confirms_account_evidence(best):
            label_level = EvidenceLevel.STRONG_INFERENCE
        else:
            label_level = best.evidence_level
        out.append(
            OpportunityHypothesis(
                capability_id=cid,
                capability_name=c["name"],
                priority=_priority(score),
                score=score,
                evidence_level=label_level.value,
                confidence=best.confidence,
                evidence_count=len(group),
                independent_source_count=len(unique_urls),
                hypothesis=hypothesis,
                rationale=rationale,
                who=c["who"],
                conversation=c["conversation"],
                next_step=c["next_step"],
                source_urls=unique_urls,
                incumbent_context=incumbent,
                independent_domain_count=domain_count,
                inference_cluster_count=cluster_count,
                epistemic_status=epistemic_status_for(label_level).value,
            )
        )

    out.sort(key=lambda x: (-x.score, -_EVIDENCE_RANK.get(x.evidence_level, 0), x.capability_id))
    return out[:max_items]

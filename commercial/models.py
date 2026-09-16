"""Data model for evidence-first commercial mapping.

The conceptual chain this model exists to enforce (never skip a link):

    Customer Signal -> Business Impact -> Technology Requirement
        -> Potential Otech Capability

A keyword occurrence is not, by itself, a CapabilityMapping worth acting
on -- it must clear an evidence-level bar first. See
commercial/evidence_mapping.py for the classification logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EvidenceLevel(str, Enum):
    """How strong is the connection between what the source actually says
    and this capability -- deliberately three tiers, never a single opaque
    score, so a viewer can see WHY something is trusted or not.
    """

    DIRECT_EVIDENCE = "direct_evidence"     # the source explicitly describes this capability area in an organizational context
    STRONG_INFERENCE = "strong_inference"     # a real business signal (project/expansion/investment/...) implies this technology requirement, but no direct capability mention
    WEAK_RELEVANCE = "weak_relevance"           # an isolated/incidental keyword match; retained as evidence, never as a sales claim


class EvidenceCategory(str, Enum):
    """Phase 2 hardening: WHAT KIND of evidence this mapping is, independent
    of how strong it is (EvidenceLevel). Two mappings can share an
    evidence_level while meaning very different things to a seller -- a
    procurement RFP and a bare incidental keyword mention are both possible
    at different levels, but "what this evidence actually is" is a
    separate, useful axis for the UI/AI to reason about without inventing
    it themselves. Assigned deterministically by evidence_mapping.py based
    on which classification rule produced the mapping -- never AI-assigned.
    """

    ORGANIZATIONAL_TECHNOLOGY_EVENT = "organizational_technology_event"  # the org itself, in context, describes a concrete technology deployment/upgrade/renewal
    PROCUREMENT_SIGNAL = "procurement_signal"                              # a tender/RFP/RFQ/procurement-process signal grounds the mapping
    BUSINESS_SIGNAL_INFERENCE = "business_signal_inference"                # a generic trigger + a locally-associated qualifying business signal (inference, not a direct technology mention)
    CURRENT_STATE_TECHNOLOGY = "current_state_technology"                    # third-party technographics/current-state-of-stack evidence -- never a buying signal
    STAKEHOLDER_CONTEXT = "stakeholder_context"                                # LinkedIn personal-profile evidence -- about a person, not a confirmed org requirement
    HIRING_INTENT_CONTEXT = "hiring_intent_context"                             # LinkedIn job-posting evidence -- intent/context, not direct technology evidence
    INCIDENTAL_MENTION = "incidental_mention"                                     # a bare weak/generic term with no corroborating signal or organizational association
    CUSTOMER_REQUIREMENT_CONTEXT = "customer_requirement_context"                   # evidence-relevance hardening follow-up pass: the matched term's local context describes a THIRD PARTY's (the organization's own customer's) requirement, need, or site criteria -- not the organization's own requirement. Useful as contextual/current-state evidence; never promotable to an opportunity.
    EXISTING_CAPABILITY_CONTEXT = "existing_capability_context"                       # Current/Change/Requirement Signal Gate: two or more specific catalogue terms are locally grounded to the organization, but no local current/change/requirement signal was found -- the source establishes the org HAS or DESCRIBES this capability area, not that anything is changing. Retained as visible, explainable context; never independently promotable to an opportunity (see commercial/opportunities.py).


class EpistemicStatus(str, Enum):
    """Phase 2 hardening (item 6): deterministic, app-computed status --
    never assigned or overridable by the AI layer. Derived purely from
    evidence_level at CapabilityMapping construction time:
    DIRECT_EVIDENCE -> CONFIRMED_EVIDENCE, everything else (STRONG_INFERENCE,
    WEAK_RELEVANCE) -> INFERENCE_HYPOTHESIS. Exists so the UI/AI narrative
    layer has a structural field to branch on ("this is confirmed" vs "this
    is a hypothesis to validate") instead of leaving that distinction to
    prompt wording alone.
    """

    CONFIRMED_EVIDENCE = "confirmed_evidence"
    INFERENCE_HYPOTHESIS = "inference_hypothesis"


def epistemic_status_for(evidence_level: "EvidenceLevel") -> "EpistemicStatus":
    """The one and only place epistemic_status is decided. Pure function of
    evidence_level -- deterministic, no AI input, no organization/sector
    branching.
    """
    if evidence_level == EvidenceLevel.DIRECT_EVIDENCE:
        return EpistemicStatus.CONFIRMED_EVIDENCE
    return EpistemicStatus.INFERENCE_HYPOTHESIS


@dataclass
class Signal:
    """A customer signal: something the source actually says happened or is
    happening, independent of any capability mapping. This is the first
    link in the chain and exists whether or not it maps to anything Otech
    sells.
    """

    signal_type: str          # e.g. "project", "expansion", "investment", "partnership", "leadership_change", "technology_initiative"
    text: str                   # the matched phrase/sentence-level context
    source_url: str
    source_title: str
    snippet: str
    matched_terms: list[str] = field(default_factory=list)
    # Phase 2 hardening (item 1 -- signal-to-trigger locality): the character
    # offset of the signal's own regex match within the source text it was
    # extracted from. -1 means "unknown" (e.g. a hand-built Signal in a test
    # that didn't supply one) -- callers that need locality treat -1 as "not
    # locally verifiable" rather than guessing. Populated by
    # commercial.signals.extract_signals from the real match position.
    match_start: int = -1
    match_end: int = -1


@dataclass
class IncumbentEvidence:
    """A detected incumbent/technology-vendor/implementation-partner
    mention. Retained as evidence in its own right. NEVER converted into a
    CapabilityMapping or an opportunity by itself -- a competitor's or
    incumbent's presence is context for a sales conversation, not
    automatically a gap Otech can fill.
    """

    vendor_or_partner_term: str      # the matched vendor/partner/incumbent term or phrase
    category: str                       # "named_vendor" | "incumbent_language" | "partnership_language"
    source_url: str
    source_title: str
    snippet: str


@dataclass
class CapabilityMapping:
    capability_id: str
    capability_name: str
    evidence_level: EvidenceLevel
    matched_terms: list[str]
    source_url: str
    source_title: str
    confidence: float                     # 0-100, derived transparently -- see evidence_mapping.py
    supporting_context: str = ""            # snippet/sentence-level excerpt backing the match
    business_impact: str = ""                 # link 2 of the chain (from the capability catalogue's own "why" text)
    technology_requirement: str = ""             # link 3 of the chain (from the catalogue's "capability" text)
    signal_type: str | None = None                 # which generic signal type this mapping is grounded in, if any
    incumbent_context: list[str] = field(default_factory=list)  # notes when incumbent/vendor language co-occurs, without affecting confidence upward

    # Phase 2 hardening additions -- all three are ALWAYS app-computed in
    # commercial/evidence_mapping.py, never AI-assigned (see ai/synthesis.py
    # grounding, which strips any AI-supplied value for these fields).
    evidence_category: str | None = None            # EvidenceCategory value -- WHAT KIND of evidence this is
    inference_cluster_id: str | None = None          # shared by every mapping produced from the same underlying source/event, so N mappings from one event are never counted as N independent corroborations
    epistemic_status: str | None = None                # EpistemicStatus value -- derived purely from evidence_level via epistemic_status_for()

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "capability_name": self.capability_name,
            "evidence_level": self.evidence_level.value,
            "matched_terms": self.matched_terms,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "confidence": self.confidence,
            "supporting_context": self.supporting_context,
            "business_impact": self.business_impact,
            "technology_requirement": self.technology_requirement,
            "signal_type": self.signal_type,
            "incumbent_context": self.incumbent_context,
            "evidence_category": self.evidence_category,
            "inference_cluster_id": self.inference_cluster_id,
            "epistemic_status": self.epistemic_status,
        }

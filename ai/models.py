"""Data model for the Phase 1D AI reasoning (synthesis) layer.

These types carry AI-GENERATED PROSE alongside back-references into the
deterministic evidence the prose was built from -- they never replace or
carry a copy of evidence_level/confidence that the AI itself decided; those
values are always read from the deterministic `commercial.models
.CapabilityMapping` objects in the input `AccountRadarReport` and passed
through unchanged (see ai/synthesis.py's grounding step).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SynthesisStatus(str, Enum):
    OK = "ok"                          # AI ran, produced a grounded, schema-valid result
    DEGRADED = "degraded"                # AI ran, but some/all of its output failed grounding and was dropped
    AI_UNCONFIGURED = "ai_unconfigured"    # no AI client configured (e.g. no API key) -- never silently "successful"
    AI_FAILED = "ai_failed"                 # AI call, JSON parse, or schema validation failed outright
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # underlying report itself had no qualified evidence to synthesize


@dataclass
class CapabilityNarrative:
    """A short, grounded sales narrative for ONE capability -- only ever
    produced for a capability that has at least one direct_evidence or
    strong_inference CapabilityMapping in the deterministic report (see
    ai/synthesis.py's grounding step). `evidence_level`/`confidence` here
    are copied FROM the deterministic mapping(s) this narrative cites, not
    decided by the AI.
    """

    capability_id: str
    capability_name: str
    narrative: str
    cited_source_urls: list[str] = field(default_factory=list)
    best_evidence_level: str = ""    # the strongest evidence_level among the cited mappings, read from the report
    best_confidence: float = 0.0       # the confidence of that same mapping, read from the report
    # Phase 2 hardening: like best_evidence_level/best_confidence above,
    # these are ALWAYS copied from the real, deterministic CapabilityMapping
    # that grounds this narrative -- the AI response schema
    # (ai/schema.py::SYNTHESIS_JSON_SCHEMA) does not even accept these as
    # fields the model can supply, so there is no code path by which the AI
    # could set or override them.
    best_evidence_category: str = ""
    best_epistemic_status: str = ""

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "capability_name": self.capability_name,
            "narrative": self.narrative,
            "cited_source_urls": self.cited_source_urls,
            "best_evidence_level": self.best_evidence_level,
            "best_confidence": self.best_confidence,
            "best_evidence_category": self.best_evidence_category,
            "best_epistemic_status": self.best_epistemic_status,
        }


@dataclass
class NextBestAction:
    """One entry in the ranked next-action shortlist. `rank` is the AI's
    proposed ordering (validated to be a plain integer and re-sorted/
    re-numbered deterministically in code, never trusted as already sorted
    or contiguous).
    """

    capability_id: str
    capability_name: str
    rank: int
    rationale: str
    evidence_level: str = ""
    confidence: float = 0.0
    origin: str = "ai"  # "ai" or "deterministic_guardrail"
    # Phase 2 hardening: app-computed only, same rationale as
    # CapabilityNarrative.best_evidence_category/best_epistemic_status above.
    evidence_category: str = ""
    epistemic_status: str = ""

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "capability_name": self.capability_name,
            "rank": self.rank,
            "rationale": self.rationale,
            "evidence_level": self.evidence_level,
            "confidence": self.confidence,
            "origin": self.origin,
            "evidence_category": self.evidence_category,
            "epistemic_status": self.epistemic_status,
        }


@dataclass
class SynthesisResult:
    status: SynthesisStatus
    organization_name: str
    account_narrative: str = ""
    capability_narratives: list[CapabilityNarrative] = field(default_factory=list)
    next_best_actions: list[NextBestAction] = field(default_factory=list)
    model: str | None = None
    notes: list[str] = field(default_factory=list)   # human-readable, e.g. what was dropped and why

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "organization_name": self.organization_name,
            "account_narrative": self.account_narrative,
            "capability_narratives": [c.to_dict() for c in self.capability_narratives],
            "next_best_actions": [a.to_dict() for a in self.next_best_actions],
            "model": self.model,
            "notes": self.notes,
        }

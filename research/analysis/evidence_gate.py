"""Generic claim-level evidence relevance gate."""
from __future__ import annotations
import re
from dataclasses import dataclass

_STOPWORDS = {
    "the","and","for","with","that","this","from","into","their","they","has",
    "have","will","are","was","were","been","being","its","our","new","more",
    "than","about","through","after","under","over","between","which","also",
    "company","official","website","management","international","services",
}

def _tokens(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
        if w not in _STOPWORDS
    }

@dataclass(frozen=True)
class EvidenceGateResult:
    eligible: bool
    score: float
    reason: str

def evaluate_evidence(*, title: str, passage: str, claim_terms: list[str] | None = None) -> EvidenceGateResult:
    p = (passage or "").strip()
    if not p:
        return EvidenceGateResult(False, 0.0, "empty_passage")

    pt = _tokens(p)
    target = _tokens(title + " " + " ".join(claim_terms or []))
    if not target:
        return EvidenceGateResult(False, 0.0, "no_claim_terms")

    overlap = pt & target
    title_words = [w for w in re.findall(r"[a-z0-9]{3,}", title.lower()) if w not in _STOPWORDS]

    # Exact meaningful title phrase is strong evidence.
    if len(title_words) >= 2:
        for n in (4, 3, 2):
            for i in range(len(title_words) - n + 1):
                phrase = " ".join(title_words[i:i+n])
                if phrase in p.lower():
                    return EvidenceGateResult(True, 0.85, "claim_phrase_present")

    if len(overlap) >= 2:
        score = min(0.95, 0.42 + 0.10 * len(overlap))
        return EvidenceGateResult(True, score, "meaningful_claim_overlap")

    return EvidenceGateResult(False, min(0.40, 0.10 * len(overlap)), "insufficient_claim_overlap")

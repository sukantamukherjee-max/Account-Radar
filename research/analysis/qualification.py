"""Corpus qualification -- the pipeline stage between acquisition and
analysis: `acquisition -> QUALIFICATION -> signal extraction -> evidence-
first capability mapping -> report`.

Qualification answers two separate questions, deterministically, with no
LLM and no organization/sector-specific branching:

  1. Per-source: is THIS source usable for analysis at all? (Does it have
     real text -- fetched content or a retained snippet -- and a
     non-malformed URL?) A source that acquisition found but that carries
     no usable text (e.g. NO_CONTENT with an empty snippet) is excluded
     from analysis rather than silently passed through as if it had
     something to say.

  2. Corpus-level: does the WHOLE research run have enough usable evidence
     to be worth analyzing at all? This is the deterministic equivalent of
     spec §19's "no reliable public evidence found for this topic"
     diagnostic -- it must be reported honestly, not papered over by
     running the mapping stage on a near-empty corpus and presenting thin
     results as if they were normal.

Qualification is deliberately conservative and mechanical: it does not
judge WHAT a source says (that's signal extraction and capability mapping's
job), only whether there is enough of it to be worth analyzing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from research.sources import AccessStatus, Source

# A source needs at least this many characters of real text (content, or
# failing that, a snippet) to be worth running signal/capability analysis
# against. Below this, matches would be built on almost nothing.
MIN_USABLE_TEXT_CHARS = 40

# Corpus-level floor: below this many qualified sources, or with zero
# high-trust sources AND zero LinkedIn evidence, the corpus is reported as
# insufficient rather than silently analyzed.
MIN_QUALIFIED_SOURCES_FOR_ANALYSIS = 3

_HIGH_TRUST_SOURCE_TYPES = {"official", "technology", "procurement"}


@dataclass
class DisqualifiedSource:
    source: Source
    reason: str


@dataclass
class QualificationResult:
    qualified_sources: list[Source] = field(default_factory=list)
    disqualified_sources: list[DisqualifiedSource] = field(default_factory=list)
    corpus_status: str = "qualified"        # "qualified" | "insufficient_evidence"
    corpus_reason: str = ""

    @property
    def qualified_count(self) -> int:
        return len(self.qualified_sources)

    @property
    def disqualified_count(self) -> int:
        return len(self.disqualified_sources)

    def to_dict(self) -> dict:
        return {
            "corpus_status": self.corpus_status,
            "corpus_reason": self.corpus_reason,
            "qualified_count": self.qualified_count,
            "disqualified_count": self.disqualified_count,
            "disqualified_reasons": [
                {"url": d.source.url, "reason": d.reason} for d in self.disqualified_sources
            ],
        }


def _usable_text(source: Source) -> str:
    return (source.content or "").strip() or (source.snippet or "").strip()


def qualify_source(source: Source) -> tuple[bool, str]:
    """True/reason for whether ONE source is usable for analysis."""
    if not source.url:
        return False, "missing URL"
    if source.access_status == AccessStatus.NO_CONTENT:
        return False, "no content or snippet available (access_status=no_content)"
    text = _usable_text(source)
    if len(text) < MIN_USABLE_TEXT_CHARS:
        return False, f"usable text too short ({len(text)} chars < {MIN_USABLE_TEXT_CHARS})"
    return True, "ok"


def qualify_corpus(sources: list[Source]) -> QualificationResult:
    """Apply per-source qualification, then assess the corpus as a whole.

    Never raises for a thin/empty corpus -- an "insufficient_evidence"
    QualificationResult with zero qualified sources is a valid, honestly
    reported outcome (spec §19), not an error condition.
    """
    result = QualificationResult()
    for s in sources:
        ok, reason = qualify_source(s)
        if ok:
            result.qualified_sources.append(s)
        else:
            result.disqualified_sources.append(DisqualifiedSource(source=s, reason=reason))

    if result.qualified_count == 0:
        result.corpus_status = "insufficient_evidence"
        result.corpus_reason = "no reliable public evidence found for this topic (zero sources passed qualification)"
        return result

    if result.qualified_count < MIN_QUALIFIED_SOURCES_FOR_ANALYSIS:
        result.corpus_status = "insufficient_evidence"
        result.corpus_reason = (
            f"only {result.qualified_count} qualified source(s) found "
            f"(minimum {MIN_QUALIFIED_SOURCES_FOR_ANALYSIS}) -- corpus is too thin for reliable analysis"
        )
        return result

    has_high_trust = any(s.source_type in _HIGH_TRUST_SOURCE_TYPES for s in result.qualified_sources)
    has_linkedin = any(s.source_type in ("linkedin_company", "linkedin_profile") for s in result.qualified_sources)
    if not has_high_trust and not has_linkedin:
        result.corpus_status = "insufficient_evidence"
        result.corpus_reason = (
            "qualified sources exist but none are official/technology/procurement or LinkedIn evidence -- "
            "corpus lacks the source diversity needed for confident capability mapping"
        )
        return result

    result.corpus_status = "qualified"
    result.corpus_reason = (
        f"{result.qualified_count} qualified source(s), "
        f"{'includes' if has_high_trust else 'lacks'} high-trust official/technology/procurement evidence, "
        f"{'includes' if has_linkedin else 'lacks'} LinkedIn evidence"
    )
    return result

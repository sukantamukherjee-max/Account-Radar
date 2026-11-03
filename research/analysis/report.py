"""Report assembly -- the final pipeline stage:

    acquisition -> qualification -> signal extraction ->
        evidence-first capability mapping -> REPORT

This module does no analysis of its own. It composes the outputs of the
earlier stages (`research.analysis.qualification`, `commercial.signals`,
`commercial.evidence_mapping`) into one `AccountRadarReport` object that
explicitly represents the chain the commercial layer exists to enforce:

    Customer Signal -> Business Impact -> Technology Requirement
        -> Potential Otech Capability

...with, per mapping, evidence level, confidence, source URL, supporting
context, and incumbent context -- and it carries the corpus qualification
outcome (including the honest "insufficient_evidence" case) so a consumer
never has to guess whether thin results mean "no opportunity" or "not
enough evidence to say."

No organization/sector/country-specific logic lives here: every function
takes the resolved organization and its evidence corpus as plain data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from commercial.evidence_mapping import map_all_sources, summarize_by_capability
from commercial.incumbents import detect_all_incumbent_evidence
from commercial.opportunities import build_opportunity_hypotheses
from commercial.models import CapabilityMapping, IncumbentEvidence, Signal
from commercial.people import Person, assess_people
from commercial.signals import extract_all_signals
from commercial.text_heuristics import clean_evidence_excerpt
from research.acquisition import AcquisitionRun
from research.analysis.qualification import QualificationResult, qualify_corpus
from research.analysis.evidence_gate import evaluate_evidence
from research.identity import build_identity


@dataclass
class AccountRadarReport:
    organization_name: str
    country: str | None
    website: str | None
    linkedin_company_url: str | None

    qualification: QualificationResult

    signals: list[Signal] = field(default_factory=list)
    capability_mappings: list[CapabilityMapping] = field(default_factory=list)
    incumbent_evidence: list[IncumbentEvidence] = field(default_factory=list)
    opportunity_hypotheses: list = field(default_factory=list)
    # People / Stakeholder Intelligence (additive; see commercial.people).
    # Computed independently of the general-corpus qualification outcome
    # above -- LinkedIn person discovery is a separate data source from the
    # official/technology/procurement/business/news corpus qualify_corpus
    # assesses, so a thin/insufficient general corpus does not imply no
    # People were found, and vice versa. Defaults to an empty list so
    # existing callers/tests constructing AccountRadarReport without
    # `people` continue to work unchanged.
    people: list[Person] = field(default_factory=list)
    # Candidates whose CURRENT employment at the organization could not be
    # confirmed (bare brand, conflicting evidence, location conflict, ...).
    # Never counted as stakeholders; each carries `verification_reasons`.
    unverified_people: list[Person] = field(default_factory=list)

    # Acquisition-stage diagnostics carried through so the report is
    # self-contained (a reader doesn't need the separate acquisition JSON
    # to understand how much/what kind of evidence underlies it).
    acquisition_diagnostics: dict = field(default_factory=dict)
    # Account identity (research.identity.OrganizationIdentity.to_dict()):
    # name, country, verified official domains and logo -- deterministic and
    # separate from research evidence.
    identity: dict = field(default_factory=dict)

    @property
    def sources(self):
        """Backward-compatible alias for consumers expecting report.sources."""
        return self.qualification.qualified_sources

    @property
    def status(self) -> str:
        """Top-level status a consumer should branch on first.

        "insufficient_evidence" means: do not present capability_mappings
        as findings -- report the qualification.corpus_reason instead
        (spec §19's "no reliable public evidence found" diagnostic).
        """
        return self.qualification.corpus_status

    def capability_summary(self) -> dict:
        return summarize_by_capability(self.capability_mappings)

    def chain_view(self) -> list[dict]:
        """The explicit Signal -> Business Impact -> Technology Requirement
        -> Capability chain, one row per capability mapping, in the exact
        shape the integration requirement (item 7) calls for.
        """
        rows = []
        for m in self.capability_mappings:
            backing_signal = next(
                (s for s in self.signals if s.source_url == m.source_url and s.signal_type == m.signal_type),
                None,
            )
            source_obj = next((src for src in self.qualification.qualified_sources if src.url == m.source_url), None)
            raw_evidence = backing_signal.text if backing_signal else ''
            evidence_excerpt = clean_evidence_excerpt(raw_evidence, matched_terms=m.matched_terms)
            gate = evaluate_evidence(
                title=source_obj.title if source_obj else "",
                passage=evidence_excerpt,
                claim_terms=m.matched_terms,
            )
            if m.evidence_category == "existing_capability_context":
                evidence_excerpt = ""

            if evidence_excerpt and not gate.eligible:
                evidence_excerpt = ""
            rows.append(
                {
                    "customer_signal": {
                        "signal_type": m.signal_type,
                        "text": backing_signal.text if backing_signal else None,
                    },
                    "evidence_excerpt": evidence_excerpt or None,
                    "published_at": (
                        (source_obj.extra or {}).get("published_at")
                        if source_obj is not None else None
                    ),
                    "business_impact": m.business_impact,
                    "technology_requirement": m.technology_requirement,
                    "potential_otech_capability": {
                        "capability_id": m.capability_id,
                        "capability_name": m.capability_name,
                    },
                    "evidence_level": m.evidence_level.value,
                    "confidence": m.confidence,
                    "source_url": m.source_url,
                    "source_title": m.source_title,
                    "supporting_context": m.supporting_context,
                    "matched_terms": m.matched_terms,
                    "incumbent_context": m.incumbent_context,
                    # Phase 2 hardening: deterministic, app-computed only --
                    # never AI-decided (see ai/synthesis.py grounding).
                    "evidence_category": m.evidence_category,
                    "inference_cluster_id": m.inference_cluster_id,
                    "epistemic_status": m.epistemic_status,
                }
            )
        return rows

    def to_dict(self) -> dict:
        return {
            "organization_name": self.organization_name,
            "country": self.country,
            "website": self.website,
            "linkedin_company_url": self.linkedin_company_url,
            "status": self.status,
            "qualification": self.qualification.to_dict(),
            "signals": [
                {
                    "signal_type": s.signal_type,
                    "text": s.text,
                    "source_url": s.source_url,
                    "source_title": s.source_title,
                    "matched_terms": s.matched_terms,
                }
                for s in self.signals
            ],
            "capability_summary": self.capability_summary(),
            "chain": self.chain_view(),
            "opportunity_hypotheses": [o.to_dict() for o in self.opportunity_hypotheses],
            "incumbent_evidence": [
                {
                    "term": i.vendor_or_partner_term,
                    "category": i.category,
                    "source_url": i.source_url,
                    "source_title": i.source_title,
                    "snippet": i.snippet,
                }
                for i in self.incumbent_evidence
            ],
            "acquisition_diagnostics": self.acquisition_diagnostics,
            "people": [p.to_dict() for p in self.people],
            "unverified_people": [p.to_dict() for p in self.unverified_people],
            "identity": dict(self.identity),
        }


def build_report(run: AcquisitionRun) -> AccountRadarReport:
    """Run qualification -> signal extraction -> capability mapping over an
    AcquisitionRun's sources and assemble the final report.

    If the corpus is qualified as insufficient, signal extraction and
    capability mapping are still skipped (there is nothing reliable to
    analyze) -- the report is returned with an empty chain and the honest
    `status == "insufficient_evidence"` plus `qualification.corpus_reason`
    explaining why, rather than running the mapping stage on a near-empty
    or unusable corpus and presenting thin results as if they were normal.
    """
    qualification = qualify_corpus(run.sources)

    # People / Stakeholder Intelligence: derived ONLY from the LinkedIn
    # profile candidates already present in run.sources (AcquisitionRun
    # exposes only a flat `sources` list -- the local `linkedin_result`/
    # `profile_candidates` objects used inside run_acquisition() are not
    # attached to the returned AcquisitionRun -- so candidates are
    # identified here by their own retained source_type ==
    # "linkedin_profile", set on each Source by research.linkedin.
    # discover_linkedin and preserved through dedup).
    # Deliberately built from run.sources, NOT qualification.qualified_
    # sources: qualify_source's MIN_USABLE_TEXT_CHARS floor is tuned for
    # general web-page evidence and would incorrectly exclude the large
    # majority of legitimate LinkedIn candidates, whose only text is a
    # short search-result headline snippet (e.g. "IT Manager", 10 chars;
    # "Director", 8 chars) -- well under that bar despite being perfectly
    # usable classification evidence for the People module's own purposes.
    # This is intentionally independent of corpus_status for the same
    # reason: see the `people` field's own docstring on AccountRadarReport.
    linkedin_profile_sources = [
        s for s in run.sources
        if s.source_type == "linkedin_profile"
    ]
    people_result = assess_people(
        linkedin_profile_sources,
        organization_name=run.organization.name,
        organization_country=run.organization.country,
    )
    people = people_result.confirmed
    unverified_people = people_result.unverified

    if qualification.corpus_status == "insufficient_evidence":
        return AccountRadarReport(
            organization_name=run.organization.name,
            country=run.organization.country,
            website=run.organization.website,
            linkedin_company_url=run.organization.linkedin_company_url,
            qualification=qualification,
            people=people,
            unverified_people=unverified_people,
            acquisition_diagnostics=run.diagnostics.to_dict(),
            identity=build_identity(run.organization, run.sources).to_dict(),
        )

    qualified_sources = qualification.qualified_sources
    signals = extract_all_signals(qualified_sources)
    # Phase 1C-H3: thread the resolved organization name through so
    # direct_evidence promotion can require the matched technology term(s)
    # to actually be in context with a mention of THIS organization, not
    # just present somewhere in a broader article. Always the dynamically
    # resolved name for whatever account this run is for -- never
    # hardcoded -- so this applies identically to any organization.
    capability_mappings = map_all_sources(
        qualified_sources, organization_name=run.organization.name, country=run.organization.country
    )
    incumbent_evidence = detect_all_incumbent_evidence(qualified_sources)
    opportunity_hypotheses = build_opportunity_hypotheses(capability_mappings)

    return AccountRadarReport(
        organization_name=run.organization.name,
        country=run.organization.country,
        website=run.organization.website,
        linkedin_company_url=run.organization.linkedin_company_url,
        qualification=qualification,
        signals=signals,
        capability_mappings=capability_mappings,
        incumbent_evidence=incumbent_evidence,
        opportunity_hypotheses=opportunity_hypotheses,
        people=people,
        unverified_people=unverified_people,
        acquisition_diagnostics=run.diagnostics.to_dict(),
        identity=build_identity(run.organization, run.sources).to_dict(),
    )

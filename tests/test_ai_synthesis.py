"""Phase 1D: AI synthesis layer tests.

No live LLM calls -- a deterministic FakeAIClient stub stands in for
research's real OpenRouterAIClient, mirroring how MockSearchProvider stands
in for live search providers in the acquisition tests. Uses a generic
placeholder organization; no real organization/sector/country is
referenced.
"""

from __future__ import annotations

import ast
import pathlib

from ai.client import AIResponse
from ai.models import SynthesisStatus
from ai.synthesis import synthesize_account
from research.analysis.qualification import QualificationResult
from research.analysis.report import AccountRadarReport, build_report
from research.acquisition import AcquisitionDiagnostics, AcquisitionRun
from research.organization import ResolvedOrganization
from research.sources import AccessStatus, Source

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ORG_NAME = "Meridian Ports Authority"


class FakeAIClient:
    """Deterministic, network-free AIClient stub. `responses` is a list of
    AIResponse objects returned in order (one per call); `configured`
    controls is_configured(). Records every prompt it was called with.
    """

    def __init__(self, responses: list[AIResponse] | None = None, *, configured: bool = True):
        self._responses = list(responses or [])
        self._configured = configured
        self.call_count = 0
        self.prompts: list[str] = []

    def is_configured(self) -> bool:
        return self._configured

    def complete_json(self, prompt: str, *, schema_name: str) -> AIResponse:
        self.call_count += 1
        self.prompts.append(prompt)
        if not self._responses:
            return AIResponse(error="FakeAIClient exhausted -- no more canned responses")
        return self._responses.pop(0)


def _acquisition_run(org_name: str, sources: list[Source]) -> AcquisitionRun:
    org = ResolvedOrganization(
        name=org_name, country="Nordholm", website="https://www.example-meridian.test",
        linkedin_company_url="https://www.linkedin.com/company/example-meridian",
        website_was_supplied=False, linkedin_was_supplied=False,
    )
    diagnostics = AcquisitionDiagnostics(
        organization_name=org_name, resolved_website=org.website, resolved_linkedin_company_url=org.linkedin_company_url,
        started_at=0.0, finished_at=1.0,
    )
    return AcquisitionRun(organization=org, sources=sources, diagnostics=diagnostics)


def _qualified_report(org_name: str = ORG_NAME) -> AccountRadarReport:
    sources = [
        Source(
            url="https://www.example-meridian.test/news/digital-transformation",
            domain="example-meridian.test",
            title=f"{org_name} announces digital transformation programme",
            source_type="technology", snippet="",
            discovered_via_query="q", discovery_provider="serper", family="official_domain_restricted",
            content=(
                f"{org_name} today announced a major digital transformation programme, moving core "
                "operational workloads to a new cloud platform and cloud migration, alongside a "
                "cybersecurity programme covering zero trust architecture and a new security operations "
                "center."
            ),
            access_status=AccessStatus.FETCHED,
        ),
        Source(
            url="https://www.example-tenders.test/notices/rfp-backup-dr",
            domain="example-tenders.test",
            title="RFP: Data centre backup and disaster recovery services",
            source_type="procurement", snippet="",
            discovered_via_query="q", discovery_provider="serper", family="generic_procurement",
            content=(
                f"{org_name} has issued a request for proposal (RFP) for data centre backup and disaster "
                "recovery services, seeking storage infrastructure and backup and recovery capability."
            ),
            access_status=AccessStatus.FETCHED,
        ),
        Source(
            url="https://www.linkedin.com/in/synthetic-it-manager",
            domain="linkedin.com",
            title="Synthetic IT Manager profile",
            source_type="linkedin_profile", snippet="IT Manager with a background in cloud and storage.",
            discovered_via_query="q", discovery_provider="serper", family="linkedin_people",
            content=None, access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
        ),
        Source(
            url="https://www.example-news.test/business/generic-trends",
            domain="example-news.test",
            title="Industry trend commentary",
            source_type="news", snippet="",
            discovered_via_query="q", discovery_provider="serper", family="generic_news",
            content="Commentators discussed general cloud, storage and network trends across the sector.",
            access_status=AccessStatus.FETCHED,
        ),
    ]
    run = _acquisition_run(org_name, sources)
    return build_report(run)


def _insufficient_report() -> AccountRadarReport:
    return AccountRadarReport(
        organization_name=ORG_NAME, country="Nordholm", website=None, linkedin_company_url=None,
        qualification=QualificationResult(corpus_status="insufficient_evidence", corpus_reason="no reliable public evidence found for this topic"),
    )


def _real_direct_evidence_capability_and_source(report: AccountRadarReport) -> tuple[str, str]:
    for m in report.capability_mappings:
        if m.evidence_level.value == "direct_evidence":
            return m.capability_id, m.source_url
    raise AssertionError("fixture must contain at least one direct_evidence mapping")


# ---------------------------------------------------------------------------
# Gating: unconfigured client / insufficient-evidence report
# ---------------------------------------------------------------------------

def test_ai_unconfigured_returns_ai_unconfigured_status_without_calling_client():
    report = _qualified_report()
    client = FakeAIClient(configured=False)
    result = synthesize_account(report, client)
    assert result.status == SynthesisStatus.AI_UNCONFIGURED
    assert client.call_count == 0


def test_insufficient_evidence_report_short_circuits_without_calling_ai():
    report = _insufficient_report()
    client = FakeAIClient(responses=[AIResponse(parsed_json={"account_narrative": "x", "capability_narratives": [], "next_best_actions": []})])
    result = synthesize_account(report, client)
    assert result.status == SynthesisStatus.INSUFFICIENT_EVIDENCE
    assert client.call_count == 0


# ---------------------------------------------------------------------------
# Grounded acceptance
# ---------------------------------------------------------------------------

def test_grounded_valid_response_is_accepted():
    report = _qualified_report()
    cap_id, url = _real_direct_evidence_capability_and_source(report)
    payload = {
        "account_narrative": "The evidence shows a real technology initiative underway.",
        "capability_narratives": [
            {"capability_id": cap_id, "narrative": "Strong direct evidence found.", "cited_source_urls": [url]},
        ],
        "next_best_actions": [
            {"capability_id": cap_id, "rationale": "Reach out about this initiative.", "rank": 1},
        ],
    }
    client = FakeAIClient(responses=[AIResponse(parsed_json=payload, model="test-model")])
    result = synthesize_account(report, client)

    assert result.status == SynthesisStatus.OK
    assert result.model == "test-model"
    assert len(result.capability_narratives) == 1
    cn = result.capability_narratives[0]
    assert cn.capability_id == cap_id
    assert cn.cited_source_urls == [url]
    assert cn.best_evidence_level == "direct_evidence"
    assert cn.best_confidence > 0
    assert len(result.next_best_actions) == 1
    assert result.next_best_actions[0].rank == 1
    assert result.next_best_actions[0].evidence_level == "direct_evidence"
    assert result.notes == []


# ---------------------------------------------------------------------------
# Grounding rejects hallucination
# ---------------------------------------------------------------------------

def test_hallucinated_source_url_is_stripped_not_trusted():
    report = _qualified_report()
    cap_id, real_url = _real_direct_evidence_capability_and_source(report)
    fake_url = "https://not-in-the-evidence-corpus.example/made-up-page"
    payload = {
        "account_narrative": "Summary.",
        "capability_narratives": [
            {"capability_id": cap_id, "narrative": "Mixed real and fake citation.", "cited_source_urls": [real_url, fake_url]},
        ],
        "next_best_actions": [],
    }
    client = FakeAIClient(responses=[AIResponse(parsed_json=payload)])
    result = synthesize_account(report, client)

    assert result.status == SynthesisStatus.DEGRADED
    assert len(result.capability_narratives) == 1
    assert result.capability_narratives[0].cited_source_urls == [real_url]
    assert fake_url not in result.capability_narratives[0].cited_source_urls
    assert any("dropped" in n and fake_url in n for n in result.notes)


def test_capability_narrative_dropped_entirely_when_every_citation_is_hallucinated():
    report = _qualified_report()
    cap_id, _real_url = _real_direct_evidence_capability_and_source(report)
    payload = {
        "account_narrative": "Summary.",
        "capability_narratives": [
            {"capability_id": cap_id, "narrative": "Entirely fabricated citation.", "cited_source_urls": ["https://fake.example/x"]},
        ],
        "next_best_actions": [],
    }
    client = FakeAIClient(responses=[AIResponse(parsed_json=payload)])
    result = synthesize_account(report, client)
    assert result.status == SynthesisStatus.DEGRADED
    assert result.capability_narratives == []


def test_hallucinated_capability_id_is_dropped():
    report = _qualified_report()
    payload = {
        "account_narrative": "Summary.",
        "capability_narratives": [
            {"capability_id": "not_a_real_capability", "narrative": "Fabricated capability.", "cited_source_urls": ["https://fake.example/x"]},
        ],
        "next_best_actions": [],
    }
    client = FakeAIClient(responses=[AIResponse(parsed_json=payload)])
    result = synthesize_account(report, client)
    assert result.status == SynthesisStatus.DEGRADED
    assert result.capability_narratives == []
    assert any("not_a_real_capability" in n for n in result.notes)


def test_next_best_action_for_weak_only_capability_is_rejected():
    """A capability whose ONLY evidence is weak_relevance (e.g. a LinkedIn
    personal-profile mention) must never be turned into a recommended next
    action, even if the AI proposes one -- this is the H2/H3.1 principle
    ("weak evidence is retained as context, never presented as a sales-
    ready requirement") extended into the AI layer.
    """
    report = _qualified_report()
    weak_only_ids = {
        m.capability_id for m in report.capability_mappings if m.evidence_level.value == "weak_relevance"
    } - {
        m.capability_id for m in report.capability_mappings if m.evidence_level.value in ("direct_evidence", "strong_inference")
    }
    assert weak_only_ids, "fixture must contain at least one weak-only capability"
    weak_cap_id = sorted(weak_only_ids)[0]

    payload = {
        "account_narrative": "Summary.",
        "capability_narratives": [],
        "next_best_actions": [
            {"capability_id": weak_cap_id, "rationale": "Should not be allowed.", "rank": 1},
        ],
    }
    client = FakeAIClient(responses=[AIResponse(parsed_json=payload)])
    result = synthesize_account(report, client)
    assert result.status == SynthesisStatus.DEGRADED
    assert result.next_best_actions
    assert all(a.origin == "deterministic_guardrail" for a in result.next_best_actions)
    assert any("not eligible" in n for n in result.notes)


# ---------------------------------------------------------------------------
# Failure handling: never crash, never fabricate
# ---------------------------------------------------------------------------

def test_ai_transport_error_is_ai_failed():
    report = _qualified_report()
    client = FakeAIClient(responses=[AIResponse(error="simulated transport error")])
    result = synthesize_account(report, client)
    assert result.status == SynthesisStatus.AI_FAILED
    assert result.capability_narratives == []
    assert result.next_best_actions == []


def test_schema_invalid_response_is_ai_failed():
    report = _qualified_report()
    # Missing "next_best_actions" -- fails schema validation.
    payload = {"account_narrative": "x", "capability_narratives": []}
    client = FakeAIClient(responses=[AIResponse(parsed_json=payload)])
    result = synthesize_account(report, client)
    assert result.status == SynthesisStatus.AI_FAILED


def test_unparseable_json_is_ai_failed_not_a_crash():
    report = _qualified_report()
    # parsed_json is None because the client couldn't parse the model's
    # text -- .ok is False, must degrade gracefully.
    client = FakeAIClient(responses=[AIResponse(raw_text="not json at all", error="could not parse response as JSON")])
    result = synthesize_account(report, client)
    assert result.status == SynthesisStatus.AI_FAILED


# ---------------------------------------------------------------------------
# Determinism / non-mutation
# ---------------------------------------------------------------------------

def test_deterministic_report_is_never_mutated_by_synthesis():
    report = _qualified_report()
    before = [m.to_dict() for m in report.capability_mappings]
    cap_id, url = _real_direct_evidence_capability_and_source(report)
    payload = {
        "account_narrative": "Summary.",
        "capability_narratives": [{"capability_id": cap_id, "narrative": "n", "cited_source_urls": [url]}],
        "next_best_actions": [{"capability_id": cap_id, "rationale": "r", "rank": 1}],
    }
    client = FakeAIClient(responses=[AIResponse(parsed_json=payload)])
    synthesize_account(report, client)
    after = [m.to_dict() for m in report.capability_mappings]
    assert before == after


def test_wrapping_logic_is_deterministic_given_fixed_ai_response():
    report = _qualified_report()
    cap_id, url = _real_direct_evidence_capability_and_source(report)
    payload = {
        "account_narrative": "Summary.",
        "capability_narratives": [{"capability_id": cap_id, "narrative": "n", "cited_source_urls": [url]}],
        "next_best_actions": [{"capability_id": cap_id, "rationale": "r", "rank": 1}],
    }
    client_a = FakeAIClient(responses=[AIResponse(parsed_json=dict(payload), model="m")])
    client_b = FakeAIClient(responses=[AIResponse(parsed_json=dict(payload), model="m")])
    result_a = synthesize_account(report, client_a)
    result_b = synthesize_account(report, client_b)
    assert result_a.to_dict() == result_b.to_dict()


# ---------------------------------------------------------------------------
# No hardcoding (item 5 of the user's Phase 1D instructions)
# ---------------------------------------------------------------------------

def test_no_hardcoded_organization_names_in_ai_package():
    ai_dir = REPO_ROOT / "ai"
    offending = []
    for path in ai_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and any(isinstance(op, ast.Eq) for op in node.ops):
                for side in [node.left] + node.comparators:
                    if isinstance(side, ast.Constant) and isinstance(side.value, str) and len(side.value) > 3:
                        val = side.value
                        safe_terms = {
                            "direct_evidence", "strong_inference", "weak_relevance",
                            "ok", "degraded", "ai_unconfigured", "ai_failed", "insufficient_evidence",
                            "synthesis_v1",
                        }
                        if val in safe_terms:
                            continue
                        if val[:1].isupper() and len(val.split()) <= 4 and any(c.isalpha() for c in val):
                            offending.append((str(path), val))
    assert offending == [], f"Possible hardcoded organization/sector literal(s): {offending}"


def test_prompt_contains_no_organization_specific_template_text():
    """The prompt TEMPLATE text (ai/prompt.py's _INSTRUCTIONS) must not
    reference any specific organization -- only the data plugged in at
    call time should ever mention one.
    """
    from ai.prompt import _INSTRUCTIONS

    assert ORG_NAME not in _INSTRUCTIONS
    assert "Oman" not in _INSTRUCTIONS
    assert "Airport" not in _INSTRUCTIONS

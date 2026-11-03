"""Integration tests: acquisition -> qualification -> signal extraction ->
evidence-first capability mapping -> report, end to end.

These prove the full pipeline wiring (item 8 of the integration pass),
using a real `AcquisitionRun`-shaped object (built the same way
`research.acquisition.run_acquisition` builds one) rather than only unit
testing `commercial/` in isolation as `tests/test_capability_mapping.py`
already does.
"""

from __future__ import annotations

from dataclasses import replace

from research.acquisition import AcquisitionDiagnostics, AcquisitionRun
from research.analysis.qualification import qualify_corpus
from research.analysis.report import build_report
from research.organization import ResolvedOrganization
from research.sources import AccessStatus, Source


def _diagnostics(org_name: str) -> AcquisitionDiagnostics:
    return AcquisitionDiagnostics(
        organization_name=org_name,
        resolved_website="https://www.example-airports.test",
        resolved_linkedin_company_url="https://www.linkedin.com/company/example-airports",
        started_at=0.0,
        finished_at=1.0,
    )


def _qualified_corpus_sources(org_name: str) -> list[Source]:
    """A hand-authored corpus shaped like real Phase 1B acquisition output
    (same style as before_after_demo.py) -- an official technology source,
    a news source naming an incumbent vendor, a procurement RFP, and two
    LinkedIn profile snippets. Enough qualified, high-trust + LinkedIn
    sources to clear the qualification bar.
    """
    return [
        Source(
            url="https://www.example-airports.test/news/digital-transformation",
            domain="example-airports.test",
            title=f"{org_name} announces digital transformation programme",
            source_type="technology",
            snippet="",
            discovered_via_query="site:example-airports.test technology digital IT infrastructure cloud cybersecurity",
            discovery_provider="serper",
            family="official_domain_restricted",
            content=(
                f"{org_name} today announced a major digital transformation programme as part of a new terminal "
                "expansion project. The programme includes a cloud migration initiative, moving core operational "
                "workloads to a new cloud platform, alongside a cybersecurity programme covering zero trust "
                "architecture and a new security operations center."
            ),
            access_status=AccessStatus.FETCHED,
        ),
        Source(
            url="https://www.example-news.test/business/network-renewal",
            domain="example-news.test",
            title=f"{org_name} renews network partnership with Cisco",
            source_type="news",
            snippet="",
            discovered_via_query=f'"{org_name}" leadership CEO CIO CTO CFO appointment executive',
            discovery_provider="serper",
            family="generic_news",
            content=(
                f"{org_name} has renewed its long-standing partnership with Cisco for its network infrastructure, "
                "the incumbent vendor for the organization's core network."
            ),
            access_status=AccessStatus.FETCHED,
        ),
        Source(
            url="https://www.example-tenders.test/notices/rfp-backup-dr",
            domain="example-tenders.test",
            title="RFP: Data centre backup and disaster recovery services",
            source_type="procurement",
            snippet="",
            discovered_via_query=f'"{org_name}" tender procurement RFP RFQ award contract',
            discovery_provider="serper",
            family="generic_procurement",
            content=(
                f"{org_name} has issued a request for proposal (RFP) for data centre backup and disaster recovery "
                "services, seeking storage infrastructure and backup and recovery capability to support expanded "
                "compute capacity at its data centre."
            ),
            access_status=AccessStatus.FETCHED,
        ),
        Source(
            url="https://www.linkedin.com/company/example-airports",
            domain="linkedin.com",
            title=f"{org_name} | LinkedIn",
            source_type="linkedin_company",
            snippet=f"{org_name} is a leading airport operator.",
            discovered_via_query=f'"{org_name}" site:linkedin.com/company',
            discovery_provider="serper",
            family="linkedin_company",
            content=None,
            access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
        ),
        Source(
            url="https://www.linkedin.com/in/synthetic-it-manager",
            domain="linkedin.com",
            title="Synthetic IT Manager profile",
            source_type="linkedin_profile",
            snippet="IT Manager with a background in cloud platforms, storage infrastructure and network operations.",
            discovered_via_query=f'site:linkedin.com/in "{org_name}" technology OR IT OR digital',
            discovery_provider="serper",
            family="linkedin_people",
            content=None,
            access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
        ),
    ]


def _acquisition_run(org_name: str, sources: list[Source]) -> AcquisitionRun:
    org = ResolvedOrganization(
        name=org_name,
        country="Oman",
        website="https://www.example-airports.test",
        linkedin_company_url="https://www.linkedin.com/company/example-airports",
        website_was_supplied=False,
        linkedin_was_supplied=False,
    )
    return AcquisitionRun(organization=org, sources=sources, diagnostics=_diagnostics(org_name))


# ---------------------------------------------------------------------------
# Full pipeline: qualified corpus
# ---------------------------------------------------------------------------

def test_full_pipeline_from_acquisition_run_to_qualified_report():
    org_name = "Synthetic Test Airports Company"
    run = _acquisition_run(org_name, _qualified_corpus_sources(org_name))

    report = build_report(run)

    assert report.status == "qualified"
    assert report.organization_name == org_name
    assert report.qualification.qualified_count == 5
    assert report.qualification.disqualified_count == 0

    # Signal extraction ran on the qualified corpus.
    assert report.signals
    assert any(s.signal_type in ("technology_initiative", "expansion", "procurement_activity") for s in report.signals)

    # Evidence-first capability mapping ran and produced a graded chain.
    assert report.capability_mappings
    levels = {m.evidence_level.value for m in report.capability_mappings}
    assert "direct_evidence" in levels

    # Incumbent evidence retained separately, never as a capability mapping.
    assert any(i.vendor_or_partner_term.lower() == "cisco" for i in report.incumbent_evidence)

    chain = report.chain_view()
    assert chain
    for row in chain:
        assert row["source_url"].startswith("http")
        assert row["evidence_level"] in ("direct_evidence", "strong_inference", "weak_relevance")
        assert isinstance(row["confidence"], (int, float))
        assert "business_impact" in row and "technology_requirement" in row
        assert "potential_otech_capability" in row

    # The Cisco network mention must never be reported as clean direct
    # evidence without its incumbent context attached.
    network_rows = [r for r in chain if r["potential_otech_capability"]["capability_id"] == "network_connectivity"]
    assert network_rows
    for row in network_rows:
        if row["evidence_level"] == "direct_evidence":
            assert "cisco" in [t.lower() for t in row["incumbent_context"]]

    # LinkedIn personal-profile evidence never reaches direct_evidence.
    linkedin_rows = [r for r in chain if r["source_url"] == "https://www.linkedin.com/in/synthetic-it-manager"]
    assert linkedin_rows
    assert all(r["evidence_level"] != "direct_evidence" for r in linkedin_rows)

    as_dict = report.to_dict()
    assert as_dict["status"] == "qualified"
    assert as_dict["chain"] == chain
    assert "acquisition_diagnostics" in as_dict


# ---------------------------------------------------------------------------
# Full pipeline: thin/insufficient corpus (spec §19 diagnostic)
# ---------------------------------------------------------------------------

def test_full_pipeline_reports_insufficient_evidence_honestly_without_fabricating_mappings():
    org_name = "Synthetic Sparse Company"
    thin_sources = [
        Source(
            url="https://www.example.test/one",
            domain="example.test",
            title="Sparse snippet",
            source_type="news",
            snippet="short",
            discovered_via_query="q",
            discovery_provider="serper",
            family="generic_news",
            content=None,
            access_status=AccessStatus.NO_CONTENT,
        ),
    ]
    run = _acquisition_run(org_name, thin_sources)

    report = build_report(run)

    assert report.status == "insufficient_evidence"
    assert report.qualification.corpus_reason
    assert report.signals == []
    assert report.capability_mappings == []
    assert report.chain_view() == []

    as_dict = report.to_dict()
    assert as_dict["status"] == "insufficient_evidence"
    assert as_dict["chain"] == []


# ---------------------------------------------------------------------------
# Qualification stage in isolation, fed real acquisition-shaped sources
# ---------------------------------------------------------------------------

def test_qualification_excludes_no_content_sources_but_keeps_snippet_only_sources():
    sources = [
        Source(
            url="https://www.example.test/dead-link",
            domain="example.test",
            title="Unreachable page",
            source_type="official",
            snippet="",
            discovered_via_query="q",
            discovery_provider="serper",
            family="official",
            content=None,
            access_status=AccessStatus.NO_CONTENT,
        ),
        Source(
            url="https://www.linkedin.com/in/someone",
            domain="linkedin.com",
            title="Someone",
            source_type="linkedin_profile",
            snippet="A snippet long enough to pass the usable-text threshold for qualification purposes.",
            discovered_via_query="q",
            discovery_provider="serper",
            family="linkedin_people",
            content=None,
            access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
        ),
    ]
    result = qualify_corpus(sources)
    qualified_urls = {s.url for s in result.qualified_sources}
    assert "https://www.linkedin.com/in/someone" in qualified_urls
    assert "https://www.example.test/dead-link" not in qualified_urls
    disqualified_reasons = {d.source.url: d.reason for d in result.disqualified_sources}
    assert "no_content" in disqualified_reasons["https://www.example.test/dead-link"]


def test_acquisition_run_object_shape_is_compatible_with_build_report():
    """Guards against silent drift between research.acquisition's real
    AcquisitionRun/AcquisitionDiagnostics dataclasses and what
    research.analysis.report.build_report expects -- if a field is
    renamed on one side, this test fails loudly instead of the mismatch
    only surfacing at runtime inside run_phase1c.py.
    """
    org_name = "Shape Check Org"
    run = _acquisition_run(org_name, _qualified_corpus_sources(org_name))
    # These are the exact attributes build_report()/AccountRadarReport read
    # off an AcquisitionRun.
    assert hasattr(run, "organization")
    assert hasattr(run, "sources")
    assert hasattr(run, "diagnostics")
    assert hasattr(run.organization, "name")
    assert hasattr(run.organization, "country")
    assert hasattr(run.organization, "website")
    assert hasattr(run.organization, "linkedin_company_url")
    assert hasattr(run.diagnostics, "to_dict")

    # replace() sanity check that ResolvedOrganization is still a plain
    # dataclass with these fields (not just duck-typed by coincidence).
    renamed = replace(run.organization, name="Different Name")
    assert renamed.name == "Different Name"

    report = build_report(run)
    assert report.organization_name == org_name




def test_evidence_extractor_removes_generic_page_chrome():
    from research.fetcher import _text_from_html
    html = """<html><body>
    <header>Home Services About Contact</header>
    <nav>Products Menu Search</nav>
    <main><h1>Technology programme</h1>
    <p>The organization announced a new technology programme to modernize its operations and improve resilience across its sites.</p>
    <p>The programme will be delivered in phases.</p></main>
    <footer>Privacy Cookie Policy All rights reserved</footer>
    </body></html>"""
    text = _text_from_html(html)
    assert "new technology programme" in text
    assert "Home Services About Contact" not in text
    assert "Privacy Cookie Policy" not in text


def test_evidence_extractor_reads_explicit_publication_metadata():
    from research.fetcher import _extract_published_at
    html = '<html><head><meta property="article:published_time" content="2026-09-01T10:00:00+04:00"></head><body><article><p>Substantive content.</p></article></body></html>'
    assert _extract_published_at(html) == "2026-09-01T10:00:00+04:00"


def test_evidence_gate_rejects_unrelated_subscription_text():
    from research.analysis.evidence_gate import evaluate_evidence
    result = evaluate_evidence(
        title="Oman Airports, Algeria Sign Airport Management MOU",
        passage="Subscribe now Aviation Week Intelligence Network delivered to your inbox daily.",
        claim_terms=["MOU", "airport management", "Algeria"],
    )
    assert not result.eligible


def test_evidence_gate_accepts_claim_specific_passage():
    from research.analysis.evidence_gate import evaluate_evidence
    result = evaluate_evidence(
        title="Oman Airports, Algeria Sign Airport Management MOU",
        passage="Oman Airports and Algeria signed a memorandum of understanding for airport management cooperation.",
        claim_terms=["MOU", "airport management", "Algeria"],
    )
    assert result.eligible


def test_evidence_gate_rejects_generic_capability_buzzwords():
    from research.analysis.evidence_gate import evaluate_evidence
    result = evaluate_evidence(
        title="Oman Aviation Investment",
        passage="Digital data AI cloud storage networking security managed infrastructure services.",
        claim_terms=["AI", "aviation investment"],
    )
    assert not result.eligible

def test_evidence_extractor_preserves_structural_list_boundaries():
    from research.fetcher import _text_from_html

    html = """<html><body>
    <ul>
      <li>Oman Air awarded a new contract.</li>
      <li>Oman Airports issued an aviation tender.</li>
      <li>Oman Air is expanding its fleet.</li>
    </ul>
    </body></html>"""

    text = _text_from_html(html)

    assert text == (
        "Oman Air awarded a new contract.\n\n"
        "Oman Airports issued an aviation tender.\n\n"
        "Oman Air is expanding its fleet."
    )

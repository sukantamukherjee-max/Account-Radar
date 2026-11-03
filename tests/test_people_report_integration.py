"""Phase B tests: People / Stakeholder Intelligence integration into
AccountRadarReport / build_report(). All AcquisitionRun objects here are
constructed synthetically in-process -- no network calls, no live API
keys, no real acquisition or LinkedIn discovery call is made anywhere in
this file (research.acquisition.run_acquisition and
research.linkedin.discover_linkedin are never invoked).
"""

from __future__ import annotations

from commercial.evidence_mapping import EvidenceLevel
from research.acquisition import AcquisitionDiagnostics, AcquisitionRun
from research.analysis.report import AccountRadarReport, build_report
from research.organization import ResolvedOrganization
from research.sources import AccessStatus, Source

ORG_NAME = "Meridian Ports Authority"


def _diagnostics() -> AcquisitionDiagnostics:
    return AcquisitionDiagnostics(
        organization_name=ORG_NAME,
        resolved_website="https://www.example-meridian.test",
        resolved_linkedin_company_url="https://www.linkedin.com/company/meridian-ports-authority",
        started_at=0.0,
        finished_at=1.0,
    )


def _org() -> ResolvedOrganization:
    return ResolvedOrganization(
        name=ORG_NAME,
        country="Nordholm",
        website="https://www.example-meridian.test",
        linkedin_company_url="https://www.linkedin.com/company/meridian-ports-authority",
        website_was_supplied=False,
        linkedin_was_supplied=False,
    )


def _official_source(content: str, url: str = "https://www.example-meridian.test/news/tender") -> Source:
    return Source(
        url=url,
        domain="www.example-meridian.test",
        title="Announcement",
        source_type="official",
        snippet="",
        discovered_via_query="q",
        discovery_provider="test",
        family="official",
        content=content,
        access_status=AccessStatus.FETCHED,
        source_quality=90.0,
    )


def _linkedin_profile(url: str, title: str, snippet: str, dimension: str = "technology_digital_infrastructure") -> Source:
    s = Source(
        url=url,
        domain="linkedin.com",
        title=title,
        source_type="linkedin_profile",
        snippet=snippet,
        discovered_via_query="q",
        discovery_provider="test",
        family="linkedin_people",
        access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
        source_quality=50.0,
    )
    s.extra["discovery_dimension"] = dimension
    s.extra["linkedin_kind"] = "in"
    return s


def _linkedin_company(url: str, title: str) -> Source:
    """A linkedin_company source -- must be ignored by People (only
    linkedin_profile sources are candidates).
    """
    s = Source(
        url=url,
        domain="linkedin.com",
        title=title,
        source_type="linkedin_company",
        snippet="",
        discovered_via_query="q",
        discovery_provider="test",
        family="linkedin_company",
        access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
        source_quality=95.0,
    )
    return s


# ---------------------------------------------------------------------------
# 1. AccountRadarReport has a backward-compatible `people` default
# ---------------------------------------------------------------------------

def test_account_radar_report_people_field_defaults_to_empty_list_and_is_backward_compatible():
    from research.analysis.qualification import qualify_corpus

    # Constructed WITHOUT `people` at all -- must still work.
    report = AccountRadarReport(
        organization_name=ORG_NAME,
        country="Nordholm",
        website="https://www.example-meridian.test",
        linkedin_company_url=None,
        qualification=qualify_corpus([]),
    )
    assert report.people == []
    assert "people" in report.to_dict()
    assert report.to_dict()["people"] == []


# ---------------------------------------------------------------------------
# 2. build_report() populates people from run.sources where
#    source_type == "linkedin_profile"
# ---------------------------------------------------------------------------

def test_build_report_populates_people_from_linkedin_profile_sources_in_run_sources():
    sources = [
        _official_source(
            f"{ORG_NAME} issued a tender for storage infrastructure to support expanded compute capacity."
        ),
        _linkedin_profile(
            "https://www.linkedin.com/in/jane-doe-123",
            f"Jane Doe - Chief Information Officer - {ORG_NAME} | LinkedIn",
            "Chief Information Officer",
        ),
        _linkedin_profile(
            "https://www.linkedin.com/in/john-smith-99",
            f"John Smith - Procurement Director - {ORG_NAME} | LinkedIn",
            "Procurement Director",
            dimension="procurement_commercial",
        ),
    ]
    run = AcquisitionRun(organization=_org(), sources=sources, diagnostics=_diagnostics())
    report = build_report(run)

    assert len(report.people) == 2
    names = {p.name for p in report.people}
    assert names == {"Jane Doe", "John Smith"}
    for p in report.people:
        assert p.organization_name == ORG_NAME


# ---------------------------------------------------------------------------
# 3. Non-LinkedIn sources are ignored by People (and, separately,
#    linkedin_company sources are ignored too -- only linkedin_profile
#    counts as a People candidate)
# ---------------------------------------------------------------------------

def test_non_linkedin_and_linkedin_company_sources_are_ignored_by_people():
    sources = [
        _official_source(
            f"{ORG_NAME} issued a tender for storage infrastructure to support expanded compute capacity."
        ),
        _linkedin_company(
            "https://www.linkedin.com/company/meridian-ports-authority",
            f"{ORG_NAME} | LinkedIn",
        ),
    ]
    run = AcquisitionRun(organization=_org(), sources=sources, diagnostics=_diagnostics())
    report = build_report(run)
    assert report.people == []


# ---------------------------------------------------------------------------
# 4. People can still populate on the insufficient_evidence path
# ---------------------------------------------------------------------------

def test_build_report_populates_people_on_insufficient_evidence_path_too():
    sources = [
        _linkedin_profile(
            "https://www.linkedin.com/in/jane-doe-123",
            f"Jane Doe - Chief Information Officer - {ORG_NAME} | LinkedIn",
            "Chief Information Officer",
        ),
    ]
    run = AcquisitionRun(organization=_org(), sources=sources, diagnostics=_diagnostics())
    report = build_report(run)
    # Only one thin LinkedIn source and nothing else -- corpus-level
    # qualification is expected to report insufficient_evidence (below
    # MIN_QUALIFIED_SOURCES_FOR_ANALYSIS), yet the person is still surfaced.
    assert report.status == "insufficient_evidence"
    assert len(report.people) == 1
    assert report.people[0].name == "Jane Doe"


# ---------------------------------------------------------------------------
# 5. build_report() passes the organization name into build_people()
# ---------------------------------------------------------------------------

def test_build_report_passes_organization_name_into_build_people():
    """A candidate that only weakly/incidentally mentions a DIFFERENT
    organization name must not be attributed to this run's organization --
    proving the real run.organization.name (not a hardcoded/blank value)
    is actually threaded through to build_people().
    """
    sources = [
        _linkedin_profile(
            "https://www.linkedin.com/in/jane-doe-123",
            f"Jane Doe - Chief Information Officer - {ORG_NAME} | LinkedIn",
            "Chief Information Officer",
        ),
        _linkedin_profile(
            "https://www.linkedin.com/in/unrelated-person",
            "Someone Else - CIO - A Totally Different Company | LinkedIn",
            "Chief Information Officer",
        ),
    ]
    run = AcquisitionRun(organization=_org(), sources=sources, diagnostics=_diagnostics())
    report = build_report(run)

    names = {p.name for p in report.people}
    assert "Jane Doe" in names
    assert "Someone Else" not in names
    for p in report.people:
        assert p.organization_name == ORG_NAME


# ---------------------------------------------------------------------------
# 6. report.to_dict() serializes people
# ---------------------------------------------------------------------------

def test_report_to_dict_serializes_people():
    sources = [
        _linkedin_profile(
            "https://www.linkedin.com/in/jane-doe-123",
            f"Jane Doe - Chief Information Officer - {ORG_NAME} | LinkedIn",
            "Chief Information Officer",
        ),
    ]
    run = AcquisitionRun(organization=_org(), sources=sources, diagnostics=_diagnostics())
    report = build_report(run)
    serialized = report.to_dict()
    assert "people" in serialized
    assert len(serialized["people"]) == 1
    assert serialized["people"][0]["name"] == "Jane Doe"
    assert serialized["people"][0]["person_id"]


# ---------------------------------------------------------------------------
# 7. No existing report fields are broken
# ---------------------------------------------------------------------------

def test_existing_capability_mapping_and_opportunity_behavior_is_unaffected_by_people():
    """Same corpus, with and without an added LinkedIn source -- the
    capability_mappings/opportunity_hypotheses for the non-LinkedIn
    sources must be identical either way. Three official sources are used
    so the corpus clears MIN_QUALIFIED_SOURCES_FOR_ANALYSIS and the normal
    (non-insufficient_evidence) path is actually exercised.
    """
    officials = [
        _official_source(
            f"{ORG_NAME} issued a tender for storage infrastructure to support expanded compute capacity.",
            url="https://www.example-meridian.test/news/tender",
        ),
        _official_source(
            f"{ORG_NAME} published its annual report covering financial results for the year.",
            url="https://www.example-meridian.test/news/annual-report",
        ),
        _official_source(
            f"{ORG_NAME} announced a new appointment to its leadership team this quarter.",
            url="https://www.example-meridian.test/news/leadership",
        ),
    ]
    run_without_linkedin = AcquisitionRun(organization=_org(), sources=list(officials), diagnostics=_diagnostics())
    run_with_linkedin = AcquisitionRun(
        organization=_org(),
        sources=[
            *officials,
            _linkedin_profile(
                "https://www.linkedin.com/in/jane-doe-123",
                f"Jane Doe - Chief Information Officer - {ORG_NAME} | LinkedIn",
                "Chief Information Officer",
            ),
        ],
        diagnostics=_diagnostics(),
    )
    report_without = build_report(run_without_linkedin)
    report_with = build_report(run_with_linkedin)

    def _mapping_signature(report):
        return [
            (m.capability_id, m.evidence_level, tuple(sorted(m.matched_terms)))
            for m in report.capability_mappings
        ]

    assert _mapping_signature(report_without) == _mapping_signature(report_with)
    assert [o.capability_id for o in report_without.opportunity_hypotheses] == [
        o.capability_id for o in report_with.opportunity_hypotheses
    ]
    assert report_with.people
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in report_with.capability_mappings)


def test_build_report_does_not_mutate_run_sources():
    sources = [
        _linkedin_profile(
            "https://www.linkedin.com/in/jane-doe-123",
            f"Jane Doe - Chief Information Officer - {ORG_NAME} | LinkedIn",
            "Chief Information Officer",
        ),
    ]
    run = AcquisitionRun(organization=_org(), sources=sources, diagnostics=_diagnostics())
    before = list(run.sources)
    build_report(run)
    assert run.sources == before
    assert len(run.sources) == 1
    assert run.sources[0].source_type == "linkedin_profile"

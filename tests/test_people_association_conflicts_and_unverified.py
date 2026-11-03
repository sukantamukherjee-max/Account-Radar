"""People Identity & Association Hardening -- conflicts, location, the
Unverified Candidates data path, country threading and the Brave
extra_snippets contract. Complements
tests/test_people_identity_association_hardening.py. Fully offline: in-memory
Source objects and fake providers only; no network, no API keys.
"""

from __future__ import annotations

from commercial.people import assess_people, build_people
from commercial.people_association import (
    CONFIRMED_CURRENT,
    NEEDS_VERIFICATION,
    REJECTED,
    assess_employment,
    classify_entity,
    location_conflicts,
)
from research.acquisition import AcquisitionDiagnostics, AcquisitionRun
from research.analysis.report import build_report
from research.linkedin import _assess, discover_linkedin
from research.organization import ResolvedOrganization
from research.providers.base import QueryOutcome, RawResult
from research.sources import AccessStatus, Source

ORG = "Ooredoo Oman"
COUNTRY = "Oman"


def _profile(slug, title, snippet="", *, extras=None, quality=50.0, provider="brave"):
    s = Source(
        url=f"https://www.linkedin.com/in/{slug}", domain="linkedin.com", title=title,
        source_type="linkedin_profile", snippet=snippet, discovered_via_query="q", discovery_provider=provider,
        family="linkedin_people", access_status=AccessStatus.INDEXED_SNIPPET_ONLY, source_quality=quality,
    )
    s.extra.update({"linkedin_kind": "in", "supplemental_snippets": list(extras or [])})
    return s


def _assess_one(title, snippet="", **kw):
    return assess_employment(title, snippet, ORG, organization_country=kw.pop("country", COUNTRY), **kw)


# ---------------------------------------------------------------------------
# Entity classes stay distinct
# ---------------------------------------------------------------------------

def test_entity_classes_are_distinct():
    assert classify_entity("Ooredoo Oman", ORG) == "target"
    assert classify_entity("Ooredoo Oman SAOG", ORG) == "target"
    assert classify_entity("Ooredoo", ORG) == "bare_brand"
    assert classify_entity("Ooredoo Group", ORG) == "sibling"
    assert classify_entity("Ooredoo Kuwait", ORG) == "sibling"
    assert classify_entity("Ooredoo Group Oman", ORG) == "sibling"
    assert classify_entity("Omantel", ORG) == "other"
    assert classify_entity("Air Traffic Control", "Oman Air") == "other"


# ---------------------------------------------------------------------------
# Evidence conflicts never confirm
# ---------------------------------------------------------------------------

def test_conflict_company_segment_group_vs_snippet_target_needs_verification():
    a = _assess_one("Sam Example - IT Director - Ooredoo Group | LinkedIn", "Muscat, Oman. IT Director at Ooredoo Oman.")
    assert a.status == NEEDS_VERIFICATION
    assert any("conflicting" in r for r in a.reasons)


def test_conflict_title_target_vs_experience_other_company_needs_verification():
    a = _assess_one("Sam Example - IT Director - Ooredoo Oman | LinkedIn",
                    "Muscat, Oman. Experience: Another Telecom · Jan 2022 - Present")
    assert a.status == NEEDS_VERIFICATION


def test_conflict_between_two_results_for_the_same_profile_needs_verification():
    src_a = _profile("sam-x", "Sam Example - IT Director - Ooredoo Oman | LinkedIn", "Muscat, Oman. IT Director.")
    src_b = _profile("sam-x", "Sam Example - IT Director - Ooredoo Group | LinkedIn", "Doha, Qatar. IT Director.",
                     provider="tavily", quality=40.0)
    res = assess_people([src_a, src_b], organization_name=ORG, organization_country=COUNTRY)
    assert res.confirmed == [] and [p.name for p in res.unverified] == ["Sam Example"]


def test_historical_target_with_current_other_company_is_rejected_not_unverified():
    a = _assess_one("Sam Example - Consultant | LinkedIn",
                    "Past: IT Manager at Ooredoo Oman 2015 - 2019. Experience: Another Telecom · 2019 - Present")
    assert a.status == REJECTED


def test_historical_clause_does_not_cancel_a_separate_current_clause():
    a = _assess_one("Sam Example - IT Manager - Ooredoo Oman | LinkedIn",
                    "Muscat, Oman. Previously IT Analyst at Another Telecom. IT Manager at Ooredoo Oman.")
    assert a.status == CONFIRMED_CURRENT


# ---------------------------------------------------------------------------
# Location is a verification signal, not an automatic exclusion
# ---------------------------------------------------------------------------

def test_location_conflict_helper():
    assert location_conflicts("Dubai, United Arab Emirates", COUNTRY)
    assert not location_conflicts("Muscat, Oman", COUNTRY)
    assert not location_conflicts("Muscat Governorate, Oman, Middle East", COUNTRY)
    assert not location_conflicts("Muscat", COUNTRY)            # bare city: unknown, never a conflict
    assert not location_conflicts("Dubai, United Arab Emirates", None)  # no country on file


def test_conflicting_location_with_only_headline_text_needs_verification():
    a = _assess_one("Atif Bukhari - IT Security Lead at Ooredoo Oman | LinkedIn",
                    "Dubai, United Arab Emirates. IT Security Lead.")
    assert a.location_conflict and a.status == NEEDS_VERIFICATION


def test_conflicting_location_with_structured_company_segment_is_still_confirmed():
    a = _assess_one("Atif Bukhari - IT Security Lead - Ooredoo Oman | LinkedIn",
                    "Dubai, United Arab Emirates. IT Security Lead.")
    assert a.location_conflict and a.status == CONFIRMED_CURRENT
    assert any("location" in r for r in a.reasons)      # the override is explained


def test_conflicting_location_with_open_ended_experience_entry_is_confirmed():
    a = _assess_one("Atif Bukhari - IT Security Lead | LinkedIn",
                    "Dubai, United Arab Emirates. Experience: Ooredoo Oman · Jan 2021 - Present")
    assert a.status == CONFIRMED_CURRENT


def test_location_is_recorded_on_the_person_and_never_fabricated():
    src = _profile("loc-1", "Lee Example - IT Manager - Ooredoo Oman | LinkedIn", "Muscat, Oman. IT Manager.")
    src2 = _profile("loc-2", "Kim Example - IT Manager - Ooredoo Oman | LinkedIn", "IT Manager.")
    p1, p2 = sorted(build_people([src, src2], organization_name=ORG, organization_country=COUNTRY),
                    key=lambda p: p.name)
    assert p2.location == "Muscat, Oman" and p1.location is None


def test_no_organization_country_means_no_location_gate():
    src = _profile("noc", "Pat Example - IT Lead at Ooredoo Oman | LinkedIn", "Dubai, United Arab Emirates. IT Lead.")
    assert [p.name for p in build_people([src], organization_name=ORG)] == ["Pat Example"]
    assert build_people([src], organization_name=ORG, organization_country=COUNTRY) == []


# ---------------------------------------------------------------------------
# Unverified Candidates: separate, labelled, with reasons, never confirmed
# ---------------------------------------------------------------------------

def _mixed():
    return [
        _profile("ok", "Jane Roe - Chief Information Officer - Ooredoo Oman | LinkedIn", "Muscat, Oman."),
        _profile("brand", "Lee Example - IT Director - Ooredoo | LinkedIn", "Muscat, Oman. IT Director."),
        _profile("group", "Ahmad Al-Neama - IT Director - Ooredoo Group | LinkedIn", "Muscat, Oman."),
        _profile("nothing", "Nobody - Engineer | LinkedIn", "Engineer."),
    ]


def test_assess_people_separates_confirmed_unverified_and_rejected():
    res = assess_people(_mixed(), organization_name=ORG, organization_country=COUNTRY)
    assert [p.name for p in res.confirmed] == ["Jane Roe"]
    assert [p.name for p in res.unverified] == ["Lee Example"]
    assert {r["name"] for r in res.rejected} == {"Ahmad Al-Neama", "Nobody"}
    assert all(r["reasons"] for r in res.rejected)


def test_unverified_candidates_carry_explicit_reasons_and_a_needs_verification_chip():
    res = assess_people(_mixed(), organization_name=ORG, organization_country=COUNTRY)
    p = res.unverified[0]
    assert p.organization_association == "needs_verification"
    assert p.verification_reasons and "brand" in p.verification_reasons[0]
    assert any(c["label"] == "Needs Verification" for c in p.chips)
    assert p.to_dict()["verification_reasons"] == p.verification_reasons
    assert res.confirmed[0].verification_reasons == []


def test_build_people_never_returns_unverified_candidates():
    assert [p.name for p in build_people(_mixed(), organization_name=ORG, organization_country=COUNTRY)] == ["Jane Roe"]


def _run(sources, country=COUNTRY):
    org = ResolvedOrganization(name=ORG, country=country, website="https://www.example.test",
                               linkedin_company_url=None, website_was_supplied=False, linkedin_was_supplied=False)
    diag = AcquisitionDiagnostics(organization_name=ORG, resolved_website="https://www.example.test",
                                  resolved_linkedin_company_url=None, started_at=0.0, finished_at=1.0)
    return AcquisitionRun(organization=org, sources=sources, diagnostics=diag)


def test_report_lists_only_confirmed_people_and_keeps_unverified_separately():
    report = build_report(_run(_mixed()))
    assert [p.name for p in report.people] == ["Jane Roe"]
    assert [p.name for p in report.unverified_people] == ["Lee Example"]
    d = report.to_dict()
    assert [p["name"] for p in d["people"]] == ["Jane Roe"]
    assert [p["name"] for p in d["unverified_people"]] == ["Lee Example"]


def test_report_threads_the_organization_country_into_the_location_gate():
    atif = _profile("atif", "Atif Bukhari - IT Lead at Ooredoo Oman | LinkedIn", "Dubai, United Arab Emirates. IT Lead.")
    with_country = build_report(_run([atif], country="Oman"))
    without_country = build_report(_run([atif], country=None))
    assert with_country.people == [] and [p.name for p in with_country.unverified_people] == ["Atif Bukhari"]
    assert [p.name for p in without_country.people] == ["Atif Bukhari"]


# ---------------------------------------------------------------------------
# Supplemental / extra snippets: context only
# ---------------------------------------------------------------------------

def test_extra_snippets_never_confirm_never_classify_but_stay_as_context():
    extra = "Experience: Ooredoo Oman · 2020 - Present. Chief Information Officer."
    src = _profile("ex-1", "Robin Example - Network Engineer | LinkedIn", "Network Engineer.", extras=[extra])
    src.extra["extra_snippets"] = ["Head of Cybersecurity at Ooredoo Oman"]
    res = assess_people([src], organization_name=ORG, organization_country=COUNTRY)
    assert res.confirmed == [] and res.unverified == []


def test_context_snippets_are_retained_on_a_confirmed_person_but_do_not_classify_it():
    src = _profile("ex-2", "Robin Example - Network Engineer - Ooredoo Oman | LinkedIn", "Muscat, Oman. Network Engineer.",
                   extras=["Chief Information Officer keynote on cloud security"])
    src.extra["extra_snippets"] = ["Head of Cybersecurity panel"]
    [p] = build_people([src], organization_name=ORG, organization_country=COUNTRY)
    assert "Chief Information Officer keynote on cloud security" in p.context_snippets
    assert "Head of Cybersecurity panel" in p.context_snippets
    assert p.role_id not in ("cio", "head_of_cybersecurity") and p.seniority != "c_level"
    assert "cybersecurity" not in p.functions


class _RichProvider:
    name = "brave"
    supports_pagination = True
    supports_rich_snippets = True

    def __init__(self, rows):
        self._rows = rows

    def is_configured(self):
        return True

    def search(self, query, *, num_results=10, rich_snippets=False, page=0):
        results = [RawResult(url=u, title=t, snippet=s, query=query, provider="brave", rank=i, extra_snippets=list(x))
                   for i, (u, t, s, x) in enumerate(self._rows if page == 0 else [])]
        return QueryOutcome(query=query, provider="brave", results=results, page=page, more_results_available=False)


def _org():
    return ResolvedOrganization(name=ORG, country=COUNTRY, website="https://www.example.test", linkedin_company_url=None,
                                website_was_supplied=False, linkedin_was_supplied=False)


def test_discovery_keeps_brave_extras_as_context_and_never_counts_them_as_association():
    rows = [
        ("https://www.linkedin.com/in/only-extras", "Only Extras - Engineer | LinkedIn", "Engineer.",
         ["Experience: Ooredoo Oman · 2020 - Present"]),
        ("https://www.linkedin.com/in/real-one", "Real One - Network Engineer - Ooredoo Oman | LinkedIn",
         "Muscat, Oman. Network Engineer.", ["Speaker at a security summit."]),
    ]
    result = discover_linkedin(_org(), [_RichProvider(rows)])
    by_slug = {s.url.rsplit("/", 1)[1]: s for s in result.profile_candidates}
    assert by_slug["only-extras"].extra["extra_snippets"] == ["Experience: Ooredoo Oman · 2020 - Present"]
    assert "Ooredoo" not in by_slug["only-extras"].snippet
    people = build_people(result.profile_candidates, organization_name=ORG, organization_country=COUNTRY)
    assert [p.name for p in people] == ["Real One"]
    assert "Speaker at a security summit." in people[0].context_snippets


def test_discovery_yield_counts_only_confirmed_current_employees():
    assert _assess("Jane Roe - CIO - Ooredoo Oman | LinkedIn", "Muscat, Oman.", ORG, COUNTRY)[0]
    assert not _assess("Lee Example - IT Director - Ooredoo | LinkedIn", "Muscat, Oman.", ORG, COUNTRY)[0]   # bare brand
    assert not _assess("Ahmad - IT Director - Ooredoo Group | LinkedIn", "Muscat, Oman.", ORG, COUNTRY)[0]  # sibling
    assert not _assess("Pat - IT Consultant | LinkedIn", "Experience: Ooredoo Oman · 2012 - 2018", ORG, COUNTRY)[0]


# ---------------------------------------------------------------------------
# Titles belonging to someone else
# ---------------------------------------------------------------------------

def test_related_profile_blocks_are_ignored_for_employment_and_classification():
    a = _assess_one("Candidate Beta - Engineer | LinkedIn",
                    "Engineer. Similar profiles: Jane Roe - Chief Information Officer - Ooredoo Oman")
    assert a.status == REJECTED and "Chief Information Officer" not in a.basis_text


def test_snippet_sentence_about_a_different_person_is_ignored():
    a = _assess_one("Candidate Gamma - Analyst | LinkedIn",
                    "Ahmad Al-Neama is the Chief Information Officer at Ooredoo Oman.")
    assert a.status == REJECTED and "Chief Information Officer" not in a.basis_text


def test_snippet_sentence_about_the_candidate_themselves_is_used():
    a = _assess_one("Candidate Delta - Analyst | LinkedIn", "Candidate Delta is an IT Manager at Ooredoo Oman.")
    assert a.status == CONFIRMED_CURRENT


def test_other_persons_dash_style_title_in_snippet_is_not_attributed():
    a = _assess_one("Candidate Eps - Analyst | LinkedIn",
                    "Analyst. Jane Roe - Chief Information Officer - Ooredoo Oman | LinkedIn")
    assert a.status == REJECTED


# ---------------------------------------------------------------------------
# Brand / group handling when the target IS the group
# ---------------------------------------------------------------------------

def test_target_group_entity_matches_group_but_not_a_country_company():
    group = "Ooredoo Group"
    ok = assess_employment("A B - CIO - Ooredoo Group | LinkedIn", "", group)
    bad = assess_employment("A B - CIO - Ooredoo Oman | LinkedIn", "", group)
    assert ok.status == CONFIRMED_CURRENT and bad.status == REJECTED


def test_unrelated_company_mention_is_not_employment():
    a = _assess_one("Q R - IT Director - Unrelated Telecom | LinkedIn", "Muscat, Oman. Ooredoo Oman is a competitor.")
    assert a.status == REJECTED

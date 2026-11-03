"""Organization identity: verified official domains + logo, separate from
research evidence.

Account identity must be deterministic; research evidence may be dynamic.
Research platforms (tender aggregators, LinkedIn, news sites, directories)
can never become the account's website, a hostname containing the name is
not verification, and the logo comes only from the verified official site
(otherwise an initials avatar). Oman Airports is used as a regression case
only -- production code names no organization.
"""

from __future__ import annotations

import base64
import struct
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.identity import (
    LOGO_NOT_ATTEMPTED,
    LOGO_UNAVAILABLE,
    LOGO_VERIFIED,
    attach_official_logo,
    build_identity,
    icon_candidates,
    is_valid_logo,
    logo_data_uri,
)
from research.official_domains import is_research_source_host, title_presents_site_owner
from research.organization import OrganizationInput, discover_official_identity, resolve_organization
from research.providers.base import QueryOutcome, RawResult
from ui.identity_view import domains_html, identity_block_html, identity_for_report, initials

import streamlit as _REAL_STREAMLIT  # noqa: E402  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
OAMC = "Oman Airports Management Company"
OMAN = "Oman"

OFFICIAL_HOME = ("https://www.omanairports.co.om/", "Home | Oman Airports", "Oman Airports official website.")
OFFICIAL_ESERVICES = ("https://eservices.omanairports.co.om/Eservices/Recruitment/Vacancies.aspx",
                      "Oman Airports Management Company - Vacancies", "Join Oman Airports Management Company.")
PROTENDERS = ("https://www.protenders.com/companies/oman-airports-management-company",
              "Oman Airports Management Company | ProTenders",
              "Oman Airports Management Company official website tenders, Oman Airports Management Company Oman.")
LINKEDIN = ("https://om.linkedin.com/company/oman-airports", "Oman Airports | LinkedIn",
            "Oman Airports Management Company official page.")
NEWS = ("https://www.aviationbusinessme.com/news/oman-airports-appoints-acting-ceo",
        "Oman Airports Appoints Nasser Al Sharji As Acting CEO | Aviation Business Middle East",
        "Oman Airports Management Company official announcement.")
NEWS_TAG = ("https://timesofoman.com/tag/oman-airports", "Oman Airports - Times of Oman",
            "Latest Oman Airports Management Company news.")
AGGREGATOR = ("https://www.tendersinfo.com/oman/oman-airports-management-company-tenders",
              "Oman Airports Management Company Tenders", "Oman Airports Management Company official tenders Oman.")
LOOKALIKE = ("https://omanairports-guide.example/", "Muscat Airport Guide: flights, parking and lounges",
             "Unofficial guide to Oman Airports Management Company airports.")


class _StaticProvider:
    name = "static"

    def __init__(self, results):
        self._results = results

    def is_configured(self):
        return True

    def search(self, query, *, num_results=10):
        return QueryOutcome(query=query, provider=self.name, results=[
            RawResult(url=u, title=t, snippet=sn, query=query, provider=self.name, rank=i)
            for i, (u, t, sn) in enumerate(self._results)
        ])


def _resolve(results, name=OAMC, country=OMAN):
    website, domains, reason, _ = discover_official_identity(OrganizationInput(name=name, country=country),
                                                             [_StaticProvider(results)])
    return website, domains, reason


# ---------------------------------------------------------------------------
# Research sources can never become the account's domain
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("research", [PROTENDERS, LINKEDIN, NEWS, NEWS_TAG, AGGREGATOR],
                         ids=["protenders", "linkedin", "news", "news-tag-page", "tender-aggregator"])
def test_research_source_cannot_become_the_account_domain(research):
    # strongest-scoring first, official site present
    website, domains, reason = _resolve([research, OFFICIAL_HOME])
    assert website == "https://www.omanairports.co.om"
    assert domains[0] == "omanairports.co.om" and all(research[0].split("/")[2] not in d for d in domains)
    assert reason == "site_owner_title"
    # research source alone: honest "unresolved", never the research host
    alone, domains, reason = _resolve([research])
    assert alone is None and domains == [] and reason == ""


@pytest.mark.parametrize("url", [PROTENDERS[0], LINKEDIN[0], NEWS_TAG[0], AGGREGATOR[0],
                                 "https://www.zawya.com/en/x", "https://www.cbinsights.com/company/x",
                                 "https://www.scribd.com/document/1", "https://www.bayt.com/en/oman/jobs/"])
def test_research_platforms_are_classified_as_research_hosts(url):
    assert is_research_source_host(url, OAMC, OMAN)


def test_role_words_do_not_exclude_an_organization_whose_own_name_has_them():
    assert is_research_source_host("https://www.timesofoman.com/", OAMC, OMAN)
    assert not is_research_source_host("https://www.timesofoman.com/", "Times of Oman", OMAN)
    assert not is_research_source_host("https://www.omanairports.co.om/", OAMC, OMAN)


def test_hostname_containing_the_name_is_not_verification():
    website, domains, _ = _resolve([LOOKALIKE])
    assert website is None and domains == []
    website, _, _ = _resolve([LOOKALIKE, OFFICIAL_HOME])
    assert website == "https://www.omanairports.co.om"


@pytest.mark.parametrize("title,expected", [
    ("Home | Oman Airports", True),
    ("Oman Airports Management Company — Official Website", True),
    ("Oman Airports Management Company - Vacancies", True),
    ("Tenders — Oman Airports", True),
    ("OAMC", True),
    ("Oman Airports Management Company | ProTenders", False),
    ("Oman Airports Appoints Nasser Al Sharji As Acting CEO | Aviation Business", False),
    ("Oman Airports - Times of Oman", False),
    ("Oman Airports Management Company Tenders", False),
    ("About Oman Airports Network", False),
])
def test_title_must_present_the_organization_as_site_owner(title, expected):
    assert title_presents_site_owner(title, OAMC, OMAN) is expected


def test_knowledge_panel_website_is_verified_and_preferred():
    class _KG(_StaticProvider):
        def search(self, query, *, num_results=10):
            out = super().search(query, num_results=num_results)
            out.results.append(RawResult(url="https://www.omanairports.co.om", title="Oman Airports", snippet="",
                                         query=query, provider="serper_knowledge_graph", rank=-1))
            return out

    website, _, reason, _ = discover_official_identity(OrganizationInput(name=OAMC, country=OMAN),
                                                       [_KG([PROTENDERS, NEWS])])
    assert website == "https://www.omanairports.co.om" and reason == "knowledge_panel"


def test_supplied_research_website_is_rejected_and_discovery_runs():
    org = resolve_organization(OrganizationInput(name=OAMC, country=OMAN, website=PROTENDERS[0]),
                               [_StaticProvider([PROTENDERS, OFFICIAL_HOME])])
    assert org.website == "https://www.omanairports.co.om" and not org.website_was_supplied


# ---------------------------------------------------------------------------
# Stability and multiple official domains
# ---------------------------------------------------------------------------

def test_primary_official_domain_is_stable_across_research_runs():
    runs = [
        [PROTENDERS, OFFICIAL_ESERVICES, OFFICIAL_HOME, NEWS],      # run 1: ProTenders strongest
        [LINKEDIN, OFFICIAL_HOME, AGGREGATOR, OFFICIAL_ESERVICES],  # run 2: LinkedIn strongest
        [NEWS, NEWS_TAG, OFFICIAL_HOME, OFFICIAL_ESERVICES],        # run 3: news strongest
        [OFFICIAL_ESERVICES, PROTENDERS, OFFICIAL_HOME],            # run 4: only a deep link first
    ]
    resolved = [_resolve(r)[:2] for r in runs]
    assert {w for w, _ in resolved} == {"https://www.omanairports.co.om"}
    assert {d[0] for _, d in resolved} == {"omanairports.co.om"}


def test_session_reuse_of_a_verified_website_is_stable_without_search():
    class _Exploding(_StaticProvider):
        def search(self, query, *, num_results=10):
            if "official website" in query:
                raise AssertionError("official-site search must not run when the verified site is known")
            return super().search(query, num_results=num_results)

    org = resolve_organization(OrganizationInput(name=OAMC, country=OMAN, website="https://www.omanairports.co.om"),
                               [_Exploding([PROTENDERS, LINKEDIN])])
    assert org.website == "https://www.omanairports.co.om" and org.domain_verification == "supplied"
    assert org.official_domains == ["omanairports.co.om"]


def test_verified_official_subdomain_is_retained_as_additional_domain():
    website, domains, _ = _resolve([PROTENDERS, OFFICIAL_ESERVICES, OFFICIAL_HOME])
    assert website == "https://www.omanairports.co.om"   # deep link normalized to the canonical host
    assert domains == ["omanairports.co.om", "eservices.omanairports.co.om"]


def test_identity_adds_hosts_under_the_official_domain_only():
    org = SimpleNamespace(name=OAMC, country=OMAN, website="https://www.omanairports.co.om",
                          official_domains=["omanairports.co.om"], domain_verification="site_owner_title")
    sources = [SimpleNamespace(url=u) for u in (
        "https://eservices.omanairports.co.om/x", "https://www.omanairports.co.om/en/about",
        PROTENDERS[0], LINKEDIN[0], NEWS[0], "https://omanairports-guide.example/")]
    ident = build_identity(org, sources)
    assert ident.primary_official_domain == "omanairports.co.om"
    assert ident.additional_official_domains == ["eservices.omanairports.co.om"]


def test_second_independently_verified_domain_is_additional():
    other = ("https://www.meridianports.com/", "Meridian Ports Authority | Official Website", "")
    main = ("https://www.meridianports.example/", "Home | Meridian Ports Authority", "")
    _, domains, _ = _resolve([main, other, ("https://www.protenders.com/meridian", "Meridian Ports Authority | ProTenders", "")],
                             name="Meridian Ports Authority", country="Testland")
    assert domains[0] in ("meridianports.example", "meridianports.com") and set(domains) == {
        "meridianports.example", "meridianports.com"}


def test_identity_is_generic_for_any_organization():
    for name, home, research in [
        ("Northwind Telecom", ("https://www.northwind-telecom.example/", "Northwind Telecom - Official Website", ""),
         ("https://www.linkedin.com/company/northwind", "Northwind Telecom | LinkedIn", "")),
        ("Contoso Bank SAOG", ("https://www.contosobank.example/en", "Contoso Bank SAOG", ""),
         ("https://www.globaltenders.com/contoso", "Contoso Bank SAOG Tenders", "")),
    ]:
        website, domains, _ = _resolve([research, home], name=name, country="Testland")
        assert website and domains and domains[0] == home[0].split("/")[2].removeprefix("www.")


# ---------------------------------------------------------------------------
# Header rendering
# ---------------------------------------------------------------------------

def _identity(**over):
    base = {"organization_name": OAMC, "country": OMAN, "primary_official_domain": "omanairports.co.om",
            "additional_official_domains": [], "official_website": "https://www.omanairports.co.om",
            "domain_verification": "site_owner_title", "logo": None, "logo_source": None, "logo_status": "unavailable"}
    base.update(over)
    return base


def test_multiple_official_domains_render_compactly():
    two = identity_for_report({"identity": _identity(additional_official_domains=["eservices.omanairports.co.om"])})
    html = domains_html(two)
    assert "omanairports.co.om" in html and "eservices.omanairports.co.om" in html and "+ " not in html
    many = identity_for_report({"identity": _identity(additional_official_domains=[
        "eservices.omanairports.co.om", "careers.omanairports.co.om", "muscatairport.example"])})
    html = domains_html(many)
    assert "+ 3 official domains" in html and 'title="Official domains: omanairports.co.om, ' in html
    assert html.count('class="ar-identity-domain"') == 1


def test_no_official_domain_is_omitted_not_replaced():
    block = identity_block_html(identity_for_report({"identity": _identity(primary_official_domain=None,
                                                                            official_website=None)}))
    assert ">OA<" in block and OAMC in block and ">Oman<" in block
    assert "ar-identity-domain" not in block


def test_legacy_report_with_a_research_website_shows_no_domain():
    ident = identity_for_report({"organization_name": OAMC, "country": OMAN, "website": PROTENDERS[0]})
    assert ident["primary_official_domain"] is None
    assert "protenders" not in identity_block_html(ident)


def test_identity_with_research_domain_injected_is_still_rejected():
    ident = identity_for_report({"identity": _identity(primary_official_domain="protenders.com",
                                                       additional_official_domains=["linkedin.com"])})
    assert ident["primary_official_domain"] is None and ident["additional_official_domains"] == []


def _png(width=4, height=4) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x10\x60\xa0" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_no_logo_renders_initials():
    block = identity_block_html(identity_for_report({"identity": _identity()}))
    assert 'class="ar-identity-logo is-initials"' in block and ">OA<" in block and "<img" not in block
    assert initials("Vodafone") == "VO" and initials("Bank of Example") == "BE" and initials("") == "?"


@pytest.mark.parametrize("logo", [
    "https://www.omanairports.co.om/logo.png",                          # remote URL: could break
    "data:image/png;base64," + base64.b64encode(b"<html>not an image</html>" * 4).decode(),
    "data:image/png;base64,!!!notbase64",
    "data:text/html;base64," + base64.b64encode(b"<script>alert(1)</script>" * 4).decode(),
    "javascript:alert(1)",
])
def test_invalid_or_broken_logo_falls_back_to_initials(logo):
    block = identity_block_html(identity_for_report({"identity": _identity(logo=logo, logo_status="official_site")}))
    assert "<img" not in block and ">OA<" in block


def test_valid_logo_renders_as_embedded_image():
    uri = logo_data_uri(_png())
    assert uri and is_valid_logo(uri)
    block = identity_block_html(identity_for_report({"identity": _identity(logo=uri)}))
    assert '<img src="data:image/png;base64,' in block and 'alt=""' in block


# ---------------------------------------------------------------------------
# Logo sourcing: official site only
# ---------------------------------------------------------------------------

HOMEPAGE = ('<html><head><link rel="icon" href="/favicon-32.png" sizes="32x32">'
            '<link rel="apple-touch-icon" href="/apple-touch-icon.png"><link rel="stylesheet" href="/a.css">'
            '</head></html>')


def test_icon_candidates_are_declared_by_the_official_homepage():
    assert icon_candidates(HOMEPAGE, "https://www.omanairports.co.om") == [
        "https://www.omanairports.co.om/apple-touch-icon.png",
        "https://www.omanairports.co.om/favicon-32.png",
        "https://www.omanairports.co.om/favicon.ico",
    ]


def test_logo_is_fetched_only_from_the_verified_official_site():
    fetched = []

    def fetch_html(url):
        fetched.append(url)
        return HOMEPAGE

    def fetch_bytes(url):
        fetched.append(url)
        return _png()

    org = SimpleNamespace(name=OAMC, country=OMAN, website="https://www.omanairports.co.om",
                          official_domains=["omanairports.co.om"], domain_verification="site_owner_title")
    sources = [SimpleNamespace(url=u) for u in (PROTENDERS[0], LINKEDIN[0], NEWS[0])]
    ident = attach_official_logo(build_identity(org, sources), fetch_html=fetch_html, fetch_bytes=fetch_bytes)
    assert ident.logo_status == LOGO_VERIFIED and is_valid_logo(ident.logo)
    assert ident.logo_source == "https://www.omanairports.co.om/apple-touch-icon.png"
    assert all(u.startswith("https://www.omanairports.co.om") for u in fetched)
    assert not any(h in u for u in fetched for h in ("protenders", "linkedin", "aviationbusinessme"))


def test_failed_or_non_image_logo_fetch_gives_initials_status():
    org = SimpleNamespace(name=OAMC, country=OMAN, website="https://www.omanairports.co.om",
                          official_domains=["omanairports.co.om"], domain_verification="site_owner_title")

    def boom(url):
        raise OSError("network down")

    ident = attach_official_logo(build_identity(org), fetch_html=boom, fetch_bytes=lambda u: b"<html>404</html>" * 10)
    assert ident.logo is None and ident.logo_status == LOGO_UNAVAILABLE
    ident = attach_official_logo(build_identity(org), fetch_html=lambda u: "", fetch_bytes=lambda u: _png() * 20000)
    assert ident.logo is None and ident.logo_status == LOGO_UNAVAILABLE   # oversized


def test_no_official_site_means_no_logo_fetch_at_all():
    org = SimpleNamespace(name=OAMC, country=OMAN, website=None, official_domains=[], domain_verification="")

    def never(url):
        raise AssertionError("must not fetch without a verified official site")

    ident = attach_official_logo(build_identity(org), fetch_html=never, fetch_bytes=never)
    assert ident.logo is None and ident.logo_status == LOGO_NOT_ATTEMPTED and ident.primary_official_domain is None


def test_research_source_does_not_change_identity():
    org = SimpleNamespace(name=OAMC, country=OMAN, website="https://www.omanairports.co.om",
                          official_domains=["omanairports.co.om"], domain_verification="site_owner_title")
    base = build_identity(org, []).to_dict()
    for research in (PROTENDERS, LINKEDIN, NEWS, AGGREGATOR):
        assert build_identity(org, [SimpleNamespace(url=research[0])]).to_dict() == base


# ---------------------------------------------------------------------------
# Report + app integration
# ---------------------------------------------------------------------------

def test_report_carries_identity_and_evidence_cards_keep_research_sources():
    from ui.understand_view import build_understand_view, understand_sections_html

    report = {
        "organization_name": OAMC, "country": OMAN, "website": "https://www.omanairports.co.om", "chain": [],
        "identity": _identity(),
        "signals": [{"signal_type": "leadership_change", "text": "Oman Airports appoints a new Acting CEO.",
                     "source_url": "https://www.protenders.com/news/oman-airports-ceo",
                     "source_title": "Oman Airports appoints Acting CEO", "matched_terms": []}],
    }
    html = "\n".join(understand_sections_html(build_understand_view({"report": report})))
    assert "protenders.com" in html                      # the evidence card keeps its research source
    header = identity_block_html(identity_for_report(report))
    assert "protenders" not in header and "omanairports.co.om" in header


def _apptest():
    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(APP), default_timeout=30)


def _entry(report):
    base = {"organization_name": OAMC, "country": OMAN, "chain": [], "signals": [], "qualification": {},
            "capability_summary": {}, "incumbent_evidence": [], "acquisition_diagnostics": {}, "status": "qualified"}
    base.update(report)
    return {"report": base, "ai_synthesis": {}, "opportunity_hypotheses": []}


def test_app_header_shows_identity_block_and_never_a_research_domain():
    at = _apptest()
    at.session_state["entry"] = _entry({"website": PROTENDERS[0], "identity": _identity(
        additional_official_domains=["eservices.omanairports.co.om"])})
    at.session_state["loaded_from"] = "live research"
    at.run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert 'class="ar-identity"' in html and ">OA<" in html
    assert "omanairports.co.om" in html and "eservices.omanairports.co.om" in html
    head = html[html.index('class="ar-topbar"'):html.index('class="ar-topbar"') + 4000]
    assert "protenders" not in head


def test_app_header_without_identity_or_domain_falls_back_cleanly():
    at = _apptest()
    at.session_state["entry"] = _entry({"website": "https://www.linkedin.com/company/oman-airports"})
    at.session_state["loaded_from"] = "live research"
    at.run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert ">OA<" in html and "linkedin.com/company" not in html


def test_production_identity_code_names_no_organization():
    for rel in ("research/official_domains.py", "research/identity.py", "ui/identity_view.py", "research/organization.py"):
        src = (ROOT / rel).read_text(encoding="utf-8").lower()
        for name in ("omanairports", "oman airports", "ooredoo", "omantel", "vodafone", "siemens"):
            assert name not in src, (rel, name)


# ---------------------------------------------------------------------------
# Regression: fresh run showed no official domain (real result shapes)
# ---------------------------------------------------------------------------
#
# Live search for the LEGAL name returned only directories / research
# platforms; the official site brands itself with the SHORT name and titles
# its homepage "<Short name> Official Site | <tagline>". Before the fix the
# resolver never queried the short name and only accepted an exact-name
# title segment, so nothing verified and the domain was (correctly) omitted.

LEGAL_NAME_RESULTS = [
    ("https://www.linkedin.com/company/oman-airports-management-company", "Oman Airports Management Company", ""),
    ("https://www.ampliz.com/company/oman-airports-management-company/83343818",
     "Oman Airports Management Company - Overview, Competitors, And Employees", ""),
    ("https://www.dnb.com/business-directory/company-profiles/oman-airports-management-company.2d8c",
     "OMAN AIRPORTS MANAGEMENT COMPANY", ""),
    ("https://www.griinstitute.org/infrastructure/company-profile/oamc-oman-airports-management-company_9707",
     "OAMC - Oman Airports Management Company", ""),
    ("https://leadiq.com/c/oman-airports-management-company/5a1d8afa240000240065a8e9",
     "Oman Airports Management Company Company Overview, Contact Details & Competitors", ""),
    ("https://www.protenders.com/companies/oman-airports-management-company-saoc", "Oman Airports Management Company SAOC", ""),
    ("https://www.theworldfolio.com/company/oman-airports-management-company-oamc-/691/",
     "The Worldfolio: Oman Airports Management Company (OAMC )", ""),
    ("https://rocketreach.co/oman-airports-management-company-profile_b5e5503af42e67e3",
     "Oman Airports Management Company Information", ""),
]
SHORT_NAME_RESULTS = [
    ("https://www.linkedin.com/company/oman-airports-management-company", "Oman Airports Management Company", ""),
    ("https://www.routesonline.com/airports/6587/oman-airports/", "Overview", ""),
    ("https://play.google.com/store/apps/details?id=com.nsr.oamc&hl=en", "Oman Airports - Apps on Google Play", ""),
    ("https://www.facebook.com/omanairports/", "مطارات عُمان Oman Airports", ""),
    ("https://en.wikipedia.org/wiki/Sohar_Airport", "Sohar Airport", ""),
    ("https://www.omanairports.co.om/en", "Oman Airports Official Site | Airports, Training & Services", ""),
]


class _QueryAwareProvider(_StaticProvider):
    """Returns the short-name results only for a query quoting the short name."""

    def __init__(self):
        super().__init__([])
        self.queries = []

    def search(self, query, *, num_results=10):
        self.queries.append(query)
        self._results = SHORT_NAME_RESULTS if query.startswith('"Oman Airports"') else LEGAL_NAME_RESULTS
        return super().search(query, num_results=num_results)


def test_fresh_run_verifies_the_official_site_found_by_the_short_name_query():
    provider = _QueryAwareProvider()
    website, domains, reason, _ = discover_official_identity(OrganizationInput(name=OAMC, country=OMAN), [provider])
    assert '"Oman Airports" Oman official website' in provider.queries   # derived, not hardcoded
    assert website == "https://www.omanairports.co.om"                     # /en deep path normalized
    assert domains[0] == "omanairports.co.om" and reason == "site_owner_title"
    ident = build_identity(SimpleNamespace(name=OAMC, country=OMAN, website=website, official_domains=domains,
                                           domain_verification=reason))
    block = identity_block_html(identity_for_report({"identity": ident.to_dict()}))
    assert "omanairports.co.om" in block and ">OA<" in block


def test_legal_name_results_alone_stay_unverified_and_the_domain_is_omitted():
    website, domains, reason = _resolve(LEGAL_NAME_RESULTS)
    assert website is None and domains == [] and reason == ""
    block = identity_block_html(identity_for_report({"identity": build_identity(
        SimpleNamespace(name=OAMC, country=OMAN, website=None, official_domains=[], domain_verification="")).to_dict()}))
    assert "ar-identity-domain" not in block


@pytest.mark.parametrize("url,title", [(u, t) for u, t, _ in LEGAL_NAME_RESULTS + SHORT_NAME_RESULTS
                                       if "omanairports.co.om" not in u])
def test_every_research_result_from_the_live_run_is_rejected(url, title):
    from research.official_domains import verify_official_candidate

    assert verify_official_candidate(url, title, "tavily", OAMC, OMAN) is None


@pytest.mark.parametrize("title,expected", [
    ("Oman Airports Official Site | Airports, Training & Services", True),   # explicit official self-claim
    ("Oman Airports Official Site", True),
    ("Acme Home", False),                                                     # not this organization
    ("Oman Airports Management Company Tenders", False),                     # listing word in the name segment
    ("Oman Airports Management Company SAOC", False),
    ("OAMC - Oman Airports Management Company", False),                      # two name segments: a profile, not a site name
])
def test_site_identity_words_in_the_name_segment(title, expected):
    assert title_presents_site_owner(title, OAMC, OMAN) is expected


def test_profile_page_with_the_name_slug_in_its_path_is_never_verified():
    from research.official_domains import path_names_organization, verify_official_candidate

    url = "https://www.some-industry-body.example/company-profile/oman-airports-management-company_9707"
    assert path_names_organization(url, OAMC, OMAN)
    assert verify_official_candidate(url, "Company Profile | Oman Airports Management Company", "tavily", OAMC, OMAN) is None
    assert not path_names_organization("https://www.omanairports.co.om/en/content/tenders", OAMC, OMAN)


def test_short_name_query_is_derived_generically():
    from research.organization import _short_name

    assert _short_name(OAMC, OMAN) == "Oman Airports"
    assert _short_name("Northwind Telecom", "Testland") is None or " " in _short_name("Northwind Telecom", "Testland")
    assert _short_name("Acme", None) is None

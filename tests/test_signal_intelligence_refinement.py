"""Signal Intelligence refinement -- regression tests.

Covers the five targeted improvements and the safety requirements:

  1. account-titled single-item procurement/technology pages can establish
     account context (STRONG_INFERENCE at most); listing/aggregator pages
     and generic pages titled after the account cannot;
  2. canonical website resolution (no recruitment / e-services deep links);
  3. deterministic account aliases (full name, short name, acronym "OAMC");
  4. sector vocabulary packs (LiDAR, boarding gates, API/MQTT, airport
     systems) merged into the catalogue as data;
  5. signal classes: technology/procurement > strategic > general, with page
     chrome, directory boilerplate and company-profile statements classed as
     background and never presented as meaningful account signals;

and that the existing evidence safety rules still hold (industry context,
LinkedIn profiles, directory boilerplate, the iFactory false positive).

The "Frankfurt" fixture reproduces a REAL item from a live Oman Airports
corpus: an aggregator page titled "Latest Oman Airports Tenders" whose
LiDAR / boarding-gate / API-MQTT tender belongs to Frankfurt/Main Airport.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from commercial.capabilities import get_catalog, load_vocabulary_packs
from commercial.evidence_mapping import (
    _mentions_organization,
    _org_core_tokens,
    map_source_to_capabilities,
    organization_aliases,
    title_establishes_account_context,
)
from commercial.models import EpistemicStatus, EvidenceCategory, EvidenceLevel
from commercial.opportunities import build_opportunity_hypotheses
from research.organization import OrganizationInput, discover_official_site
from research.providers.base import QueryOutcome, RawResult
from research.sources import AccessStatus, Source
from research.url_utils import canonical_official_site, is_service_subdomain, registrable_domain
from ui.executive_summary import build_executive_summary, classify_signal, is_account_site, rank_key_signals

OAMC = "Oman Airports Management Company"
COUNTRY = "Oman"
CONFIRMED = EpistemicStatus.CONFIRMED_EVIDENCE.value


def _src(url, title, content, source_type="news"):
    return Source(
        url=url, domain=urlparse(url).netloc, title=title, source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic",
        content=content, access_status=AccessStatus.FETCHED,
    )


def _map(source, org=OAMC, country=COUNTRY):
    return map_source_to_capabilities(source, organization_name=org, country=country)


def _assert_no_confirmation(mappings):
    assert not any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in mappings)
    assert not any(m.epistemic_status == CONFIRMED for m in mappings)
    assert not any(o.epistemic_status == CONFIRMED for o in build_opportunity_hypotheses(mappings))


HEADLINE_TENDER = _src(
    "https://tenders.example/notice/12345",
    "Oman Airports issues tender for LiDAR boarding gate supervision",
    "The tender covers the delivery, implementation, operation and maintenance of LiDAR-based sensor-based "
    "supervision for boarding gates at Muscat International Airport. Event data must be integrated via API "
    "integration and MQTT. Bids are due next month.",
    source_type="procurement",
)

FRANKFURT_LISTING = _src(  # real-data shape: an aggregator listing titled after the account
    "https://www.globaltenders.com/oman/om-airports-tenders",
    "Latest Oman Airports Tenders",
    "Reporting Non-Communicative Services Prices Vietnam 03 Oct 2026 18 Oct 2026 View Detail Precision Air "
    "Acquisition For The Gps Datacenter Dominican Republic 03 Oct 2026 12 Oct 2026 View Detail Lidar -Based "
    "Sensor-Based Boarding Supervision (Sbbs) For Boarding Gates In Terminal 1Delivery, Implementation, Operation "
    "And Maintenance Of A Sensor-Based Boarding Supervision (Sbbs) At Frankfurt/Main Airport, Terminal 1. Event And "
    "Kpi Data Must Be Integrated Via Documented Interfaces, In Particular Api And Mqtt. The Rollout Is Planned For A "
    "Total Of Up To 30 Gates.",
    source_type="procurement",
)


# ---------------------------------------------------------------------------
# 1. Account-titled procurement pages
# ---------------------------------------------------------------------------

def test_account_titled_tender_page_with_specific_procurement_event_is_account_procurement_evidence():
    mappings = _map(HEADLINE_TENDER)
    promoted = [m for m in mappings if m.evidence_level != EvidenceLevel.WEAK_RELEVANCE]
    assert promoted, "a specific account tender must become stronger evidence"
    for m in promoted:
        assert m.evidence_level == EvidenceLevel.STRONG_INFERENCE, "a title can never CONFIRM anything"
        assert m.evidence_category == EvidenceCategory.PROCUREMENT_SIGNAL.value
    opps = build_opportunity_hypotheses(mappings)
    assert opps and all(o.epistemic_status == EpistemicStatus.INFERENCE_HYPOTHESIS.value for o in opps)


def test_listing_page_titled_after_account_does_not_attribute_another_buyers_tender():
    mappings = _map(FRANKFURT_LISTING)
    assert mappings, "matches are retained as weak context"
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in mappings)
    assert build_opportunity_hypotheses(mappings) == []
    assert not title_establishes_account_context(FRANKFURT_LISTING.title, FRANKFURT_LISTING.content, OAMC, country=COUNTRY)


@pytest.mark.parametrize("title", [
    "Latest Oman Airports Tenders", "Oman Airports Contracts | TendersInfo", "Oman Airports Jobs and Vacancies",
    "Oman Airports Company Tenders | TendersArabia", "Oman Airports News",
])
def test_listing_titles_never_establish_account_context(title):
    assert not title_establishes_account_context(title, "The tender covers LiDAR supervision.", OAMC, country=COUNTRY)


def test_generic_page_titled_after_account_without_a_specific_event_stays_weak():
    generic = _src("https://news.example/oman-airports-profile", "Oman Airports at a glance",
                   "The company manages several airports and focuses on security, connectivity and service.")
    mappings = _map(generic)
    assert mappings and all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in mappings)


def test_headline_title_with_a_publisher_suffix_still_counts_as_a_single_item():
    assert title_establishes_account_context(
        "Oman Airports issues tender for boarding gate systems | Aviation News", "The tender covers LiDAR.",
        OAMC, country=COUNTRY,
    )


def test_account_titled_tender_is_a_key_signal_but_the_listing_is_not():
    def signal(src, kind, text):
        return {"signal_type": kind, "text": text, "source_url": src.url, "source_title": src.title, "matched_terms": []}

    report = {
        "organization_name": OAMC, "country": COUNTRY, "website": "https://www.omanairports.co.om", "chain": [],
        "signals": [
            signal(HEADLINE_TENDER, "procurement_activity",
                   "The tender covers LiDAR-based supervision for boarding gates with MQTT integration."),
            signal(FRANKFURT_LISTING, "technology_initiative",
                   "View Detail Lidar -Based Sensor-Based Boarding Supervision (Sbbs) For Boarding Gates 03 Oct 2026 18 Oct 2026"),
        ],
    }
    signals, count = rank_key_signals(report)
    assert [s.source_url for s in signals] == [HEADLINE_TENDER.url] and count == 1
    assert signals[0].signal_class == "technology_procurement"


# ---------------------------------------------------------------------------
# 2. Canonical website resolution
# ---------------------------------------------------------------------------

class _StaticProvider:
    name = "static"

    def __init__(self, results):
        self._results = results

    def is_configured(self):
        return True

    def search(self, query, *, num_results=10):
        return QueryOutcome(query=query, provider=self.name, results=[
            RawResult(url=r.url, title=r.title, snippet=r.snippet, query=query, provider=self.name, rank=i)
            for i, r in enumerate(self._results)
        ])


def _raw(url, title, snippet=""):
    return RawResult(url=url, title=title, snippet=snippet, query="q", provider="static", rank=0)


def test_canonical_website_replaces_a_recruitment_deep_link_with_the_main_site():
    provider = _StaticProvider([
        _raw("https://eservices.omanairports.co.om/Eservices/Recruitment/Vacancies.aspx",
             "Oman Airports Management Company - Vacancies", "Oman Airports Management Company official careers"),
        _raw("https://www.omanairports.co.om/en/content/about-oman-airport", "About Oman Airports"),
    ])
    website, _ = discover_official_site(OrganizationInput(name=OAMC, country=COUNTRY), [provider])
    assert website == "https://www.omanairports.co.om"


def test_canonical_website_when_only_a_service_host_is_found():
    assert canonical_official_site(
        "https://eservices.omanairports.co.om/Eservices/Recruitment/Vacancies.aspx",
        ["https://eservices.omanairports.co.om/Eservices/Recruitment/Vacancies.aspx"],
    ) == "https://www.omanairports.co.om"


def test_canonical_website_is_generic_and_strips_deep_paths():
    assert canonical_official_site("https://careers.meridian-ports.example/jobs/42", [
        "https://careers.meridian-ports.example/jobs/42", "https://meridian-ports.example/about",
    ]) == "https://meridian-ports.example"
    assert canonical_official_site("https://www.acme.example/en/about-us", ["https://www.acme.example/en/about-us"]) \
        == "https://www.acme.example"


def test_registrable_domain_and_service_hosts():
    assert registrable_domain("https://eservices.omanairports.co.om/x") == "omanairports.co.om"
    assert registrable_domain("www.omanairports.co.om") == "omanairports.co.om"
    assert registrable_domain("https://news.example.com/a") == "example.com"
    assert is_service_subdomain("eservices.omanairports.co.om") and not is_service_subdomain("www.omanairports.co.om")


def test_account_site_check_covers_subdomains_but_not_shared_suffixes():
    assert is_account_site("https://eservices.omanairports.co.om/a", "https://www.omanairports.co.om")
    assert is_account_site("https://omanairports.co.om/b", "https://www.omanairports.co.om")
    assert not is_account_site("https://www.otherbank.bank.in/x", "https://www.sc.bank.in")
    assert not is_account_site("https://www.omanairports.co.om.evil.example/x", "https://www.omanairports.co.om")


# ---------------------------------------------------------------------------
# 3. Account aliases
# ---------------------------------------------------------------------------

def test_account_aliases_are_deterministic():
    assert organization_aliases(OAMC, COUNTRY) == ["Oman Airports Management Company", "Oman Airports", "OAMC"]
    assert organization_aliases("Siemens AG") == ["Siemens AG"]  # too short for an acronym
    assert "SCB" not in organization_aliases("Standard Chartered Bank")  # 3-letter initialisms are not used


@pytest.mark.parametrize("sentence,expected", [
    ("OAMC has issued a tender for airport systems.", True),
    ("The OAMC board approved the plan.", True),
    ("oamc issued a tender.", False),            # exact upper-case only -- no fuzzy matching
    ("The OAMCX platform was launched.", False),  # whole word only
    ("Airports such as OAMC are investing.", False),  # exemplar
])
def test_acronym_alias_matching(sentence, expected):
    assert _mentions_organization(sentence, OAMC, _org_core_tokens(OAMC, country=COUNTRY), country=COUNTRY) is expected


def test_acronym_alias_grounds_account_evidence():
    s = _src("https://news.example/aviation/muscat-boarding", "Muscat airport upgrade",
             "OAMC has deployed LiDAR-based boarding gate supervision integrated over MQTT at Muscat International Airport.")
    direct = [m for m in _map(s) if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    assert direct and direct[0].evidence_category == EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT.value


# ---------------------------------------------------------------------------
# 4. Sector vocabulary packs
# ---------------------------------------------------------------------------

def test_vocabulary_packs_are_data_merged_into_the_catalogue():
    packs = {p["id"] for p in load_vocabulary_packs()}
    assert {"operational_technology_iot", "aviation_systems"} <= packs
    keywords = {k for c in get_catalog() for k in c["keywords"]}
    for term in ("lidar", "mqtt", "boarding gates", "airport systems", "api integration"):
        assert term in keywords, term
    for c in get_catalog():  # still never in both specificity buckets
        assert not {k.lower() for k in c["keywords"]} & {w.lower() for w in c.get("weak_signal_terms", [])}


def test_vocabulary_terms_strengthen_genuine_account_events():
    s = _src("https://www.omanairports.co.om/en/media/news/gates", "Smart boarding gates go live",
             "Oman Airports has deployed new airport systems at Muscat International Airport, including "
             "LiDAR-based boarding gates integrated via MQTT.", source_type="official")
    mappings = _map(s)
    assert any(m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE for m in mappings)


def test_vocabulary_terms_in_a_generic_industry_article_stay_industry_context():
    s = _src("https://ifactoryapp.com/industries/aviation-management/airport-technology-trends-2026",
             "10 Airport Technology Trends Transforming Aviation Operations in 2026",
             "OAMC and Oman Airports are deploying LiDAR boarding gates, airport systems and MQTT-based OT "
             "networks. Ransomware remains a key cyber risk.", source_type="technology")
    mappings = _map(s)
    assert mappings and all(m.evidence_category == EvidenceCategory.INDUSTRY_CONTEXT.value for m in mappings)
    _assert_no_confirmation(mappings)
    assert build_opportunity_hypotheses(mappings) == []


# ---------------------------------------------------------------------------
# 5. Signal quality classes
# ---------------------------------------------------------------------------

def _sig(kind, text, url, title):
    return {"signal_type": kind, "text": text, "source_url": url, "source_title": title, "matched_terms": []}


SIDEBAR = _sig(
    "technology_initiative",
    "Africa Oman Airports Management Company Related Content Aleppo Airport To Begin Upgrade Works Under Saudi-Syrian Deal",
    "https://aviationweek.com/air-transport/oman-airports-algeria-sign-airport-management-mou",
    "Oman Airports, Algeria Sign Airport Management MOU | Aviation Week",
)
FUNDING = _sig(
    "investment",
    "What is Oman Airports Management Company's latest funding round? Oman Airports Management Company's latest funding round is Biz Plan Competition.",
    "https://www.cbinsights.com/company/oman-airport-management",
    "Oman Airports Management Company - Products, Competitors, Financials, Employees, Headquarters Locations",
)
OWNERSHIP = _sig(
    "investment",
    "Oman Airports is a government company affiliated with the Oman Investment Authority, established in 2002.",
    "https://www.omanairports.co.om/en/content/about-oman-airport", "About Oman Airports Network",
)
LEADERSHIP = _sig(
    "leadership_change", "Oman Airports Management Company has appointed Nasser Al Sharji as acting Chief Executive Officer.",
    "https://www.aviationbusinessme.com/news/oman-airports-appoints-acting-ceo", "Oman Airports appoints acting CEO",
)
TENDER = _sig(
    "procurement_activity", "Oman Airports has issued a tender for LiDAR-based boarding gate supervision.",
    "https://www.omanairports.co.om/en/media/news/tender", "Oman Airports tender for boarding gate supervision",
)


def _report(signals):
    return {"organization_name": OAMC, "country": COUNTRY, "website": "https://www.omanairports.co.om",
            "signals": signals, "chain": [], "people": []}


def test_sidebar_related_content_is_background_not_an_account_signal():
    assert classify_signal(SIDEBAR["signal_type"], SIDEBAR["text"], SIDEBAR["source_title"]) == "background"
    signals, count = rank_key_signals(_report([SIDEBAR]))
    assert signals == [] and count == 0


def test_directory_funding_boilerplate_is_background():
    assert classify_signal(FUNDING["signal_type"], FUNDING["text"], FUNDING["source_title"]) == "background"
    es = build_executive_summary(_report([FUNDING]), [])
    assert es.meaningful_signal_count == 0 and es.background_signal_count == 1


def test_company_ownership_profile_is_background_but_leadership_change_is_strategic():
    assert classify_signal(OWNERSHIP["signal_type"], OWNERSHIP["text"], OWNERSHIP["source_title"]) == "background"
    assert classify_signal(LEADERSHIP["signal_type"], LEADERSHIP["text"], LEADERSHIP["source_title"]) == "strategic"


def test_technology_procurement_outranks_strategic_and_low_value_items_never_outrank_it():
    es = build_executive_summary(_report([SIDEBAR, FUNDING, OWNERSHIP, LEADERSHIP, TENDER]), [])
    assert [k.signal_class for k in es.key_signals] == ["technology_procurement", "strategic"]
    assert es.key_signals[0].source_url == TENDER["source_url"]
    assert es.key_signals[1].class_label == "Strategic signal"
    assert es.meaningful_signal_count == 2 and es.background_signal_count == 3


def test_vague_technology_wording_without_specific_terms_ranks_below_strategic():
    vague = _sig("technology_initiative", "To improve operations, OAMC has implemented innovative strategies.",
                 "https://www.scribd.com/document/1/strategic-management-of-oman-airports", "Strategic Management of Oman Airports")
    es = build_executive_summary(_report([vague, LEADERSHIP]), [])
    assert [k.signal_class for k in es.key_signals] == ["strategic", "general"]


# ---------------------------------------------------------------------------
# Safety regressions
# ---------------------------------------------------------------------------

def test_original_ifactory_false_positive_pattern_still_blocked():
    s = _src("https://ifactoryapp.com/industries/aviation-management/airport-technology-trends-2026",
             "10 Airport Technology Trends Transforming Aviation Operations in 2026",
             "Airport management companies are deploying a security operations center and zero trust architecture "
             "to protect OT networks against ransomware. Every airport company's security management team is "
             "upgrading its cyber risk controls as a top technology trend for 2026.", source_type="technology")
    mappings = _map(s)
    assert mappings and all(m.evidence_category == EvidenceCategory.INDUSTRY_CONTEXT.value for m in mappings)
    _assert_no_confirmation(mappings)
    assert build_opportunity_hypotheses(mappings) == []


def test_linkedin_profile_with_alias_and_vocabulary_stays_stakeholder_context():
    s = _src("https://om.linkedin.com/in/example-engineer", "Example Engineer - OAMC",
             "OAMC has deployed LiDAR-based boarding gates integrated via MQTT; I led the implementation.",
             source_type="business")
    mappings = _map(s)
    assert mappings and all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in mappings)
    assert all(m.evidence_category == EvidenceCategory.STAKEHOLDER_CONTEXT.value for m in mappings)


def test_directory_and_aggregator_boilerplate_is_not_account_evidence():
    directory = _src("https://leadiq.com/c/oman-airports-management-company/123/employee-directory",
                     "Oman Airports Management Company Employee Directory, Headcount & Staff",
                     "Oman Airports Management Company employees work in security, compliance, AI and digital "
                     "transformation. View the employee directory and headcount.", source_type="business")
    aggregator = _src("https://www.tendersarabia.com/authorities/oman/oman-airports-company-tenders",
                      "Oman Airports Company Tenders | TendersArabia",
                      "Find eligibility requirements, technical specifications, submission deadlines, tender documents "
                      "and evaluation criteria for cybersecurity and other tenders.", source_type="procurement")
    for s in (directory, aggregator):
        mappings = _map(s)
        assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in mappings), s.url
        assert build_opportunity_hypotheses(mappings) == []

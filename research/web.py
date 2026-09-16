"""Generic (non-LinkedIn) public source discovery: official-site crawl plus
domain-restricted and open-web discovery across news/procurement/
technology/business dimensions.

Every keyword list in this file is a GENERIC DISCOVERY DIMENSION, not a
sector playbook: "leadership", "investor", "tender" apply identically to an
airport operator, a bank, or a manufacturer. No organization name, sector,
country, or industry term is ever hardcoded as a special case.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from research.fetcher import fetch_page
from research.organization import ResolvedOrganization
from research.providers.base import SearchProvider
from research.providers.router import acquire_family
from research.sources import (
    SOURCE_TYPE_BUSINESS,
    SOURCE_TYPE_NEWS,
    SOURCE_TYPE_OFFICIAL,
    SOURCE_TYPE_PROCUREMENT,
    SOURCE_TYPE_TECHNOLOGY,
    AccessStatus,
    Source,
    is_noise_host,
)

# Generic internal-link keywords -- these describe UNIVERSAL corporate site
# sections, not any sector's vocabulary.
_INTERNAL_LINK_KEYWORDS = (
    "about", "company", "leadership", "management", "executive", "governance",
    "investor", "financial", "annual", "report", "news", "media", "press",
    "project", "technology", "digital", "strategy", "tender", "procurement",
    "careers", "sustainability", "operations", "expansion",
)

# Generic discovery-dimension query templates. Each takes only the
# organization's own name/country/domain as parameters.
_DOMAIN_RESTRICTED_TEMPLATES = [
    "site:{domain} company about leadership management governance",
    "site:{domain} annual report financial results investor presentation",
    "site:{domain} project expansion investment development",
    "site:{domain} technology digital IT infrastructure cloud cybersecurity",
    "site:{domain} tender procurement RFP RFQ contract award",
    "site:{domain} news press release partnership launch operations",
]

_OPEN_WEB_TEMPLATES = {
    SOURCE_TYPE_BUSINESS: [
        '"{company}" {country} strategy investment expansion project',
        '"{company}" {country} partnership technology deployment launch facility capacity',
    ],
    SOURCE_TYPE_TECHNOLOGY: [
        '"{company}" {country} technology digital transformation infrastructure cloud data cybersecurity',
    ],
    SOURCE_TYPE_PROCUREMENT: [
        '"{company}" {country} tender procurement RFP RFQ award contract',
    ],
    SOURCE_TYPE_NEWS: [
        '"{company}" {country} leadership CEO CIO CTO CFO appointment executive',
        '"{company}" {country} annual report financial results investor presentation',
    ],
}


def crawl_official_site(website: str, company: str, *, limit: int = 18) -> list[Source]:
    """Extract relevant internal links directly from the organization's own
    homepage. No search provider call -- pure crawl. Kept because this was
    genuinely the most reliable discovery step in the original codebase.
    """
    sources: list[Source] = [
        Source(
            url=website,
            domain=urlparse(website).netloc.lower(),
            title=f"{company} — Official Website",
            source_type=SOURCE_TYPE_OFFICIAL,
            snippet="",
            discovered_via_query="(direct homepage)",
            discovery_provider="direct_crawl",
            family="official",
            source_quality=95.0,
        )
    ]
    content, status = fetch_page(website)
    if status != AccessStatus.FETCHED:
        sources[0].access_status = status
        return sources
    sources[0].content = content
    sources[0].access_status = AccessStatus.FETCHED

    html, _ = _raw_html(website)
    if not html:
        return sources
    domain = urlparse(website).netloc.lower().replace("www.", "")
    soup = BeautifulSoup(html, "html.parser")
    scored = []
    seen = {website.rstrip("/")}
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(website + "/", href).split("#", 1)[0].rstrip("/")
        if urlparse(url).netloc.lower().replace("www.", "") != domain or url in seen:
            continue
        seen.add(url)
        label = (a.get_text(" ", strip=True) + " " + urlparse(url).path).lower()
        hits = sum(1 for k in _INTERNAL_LINK_KEYWORDS if k in label)
        if hits:
            scored.append((hits, url, a.get_text(" ", strip=True)))
    scored.sort(key=lambda x: (-x[0], x[1]))
    for _, url, title in scored[:limit]:
        sources.append(
            Source(
                url=url,
                domain=urlparse(url).netloc.lower(),
                title=title or url.rsplit("/", 1)[-1].replace("-", " "),
                source_type=SOURCE_TYPE_OFFICIAL,
                snippet="",
                discovered_via_query="(homepage internal link)",
                discovery_provider="direct_crawl",
                family="official",
                source_quality=88.0,
            )
        )
    return sources


def _raw_html(url: str) -> tuple[str, str]:
    import requests

    from research.fetcher import HEADERS, TIMEOUT

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.ok:
            return resp.text, resp.headers.get("content-type", "")
    except requests.exceptions.RequestException:
        pass
    return "", ""


def discover_domain_restricted_sources(
    org: ResolvedOrganization, providers: list[SearchProvider]
) -> tuple[list[Source], object]:
    if not org.website:
        return [], None
    domain = urlparse(org.website).netloc.lower().replace("www.", "")
    queries = [t.format(domain=domain) for t in _DOMAIN_RESTRICTED_TEMPLATES]
    outcome = acquire_family(
        family="official_domain_restricted", queries=queries, providers=providers, num_results=8
    )
    out = []
    for r in outcome.results:
        if urlparse(r.url).netloc.lower().replace("www.", "") != domain:
            continue
        s = Source.from_raw(r, source_type=SOURCE_TYPE_OFFICIAL, family="official_domain_restricted")
        out.append(s)
    return out, outcome


def discover_generic_sources(
    org: ResolvedOrganization, providers: list[SearchProvider]
) -> tuple[list[Source], list]:
    """news / procurement / technology / business discovery, spec §5.

    Returns (sources, list_of_family_outcomes) so the orchestration layer
    can report per-family diagnostics for each of these dimensions too.
    """
    company = org.name
    country = org.country or ""
    all_sources: list[Source] = []
    family_outcomes = []
    for source_type, templates in _OPEN_WEB_TEMPLATES.items():
        queries = [t.format(company=company, country=country).strip() for t in templates]
        outcome = acquire_family(
            family=f"generic_{source_type}", queries=queries, providers=providers, num_results=8
        )
        family_outcomes.append(outcome)
        for r in outcome.results:
            if is_noise_host(r.url):
                continue
            all_sources.append(Source.from_raw(r, source_type=source_type, family=f"generic_{source_type}"))
    return all_sources, family_outcomes

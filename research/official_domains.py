"""Official-domain verification for organization identity.

Account identity must be deterministic; research evidence may be dynamic.
This module separates the two for the existing official-site resolver
(research.organization.discover_official_site):

  * RESEARCH SOURCE HOSTS -- third-party platforms that publish ABOUT
    organizations (social / professional networks, encyclopedias, company
    data providers and directories, job boards, tender / bid aggregators,
    news and press-release wires, document-sharing sites). A result on one
    of these hosts can be evidence, but it can never be the organization's
    website. Detected from a closed list of generic research PLATFORMS plus
    generic role words in the host's registrable label ("tenders", "news",
    "jobs", ...). No account, sector or country is named anywhere.

  * VERIFIED OFFICIAL CANDIDATES -- a result is verified as the
    organization's own site only when:
      - the search provider's knowledge panel names it as the entity's
        website, or
      - the page presents the organization as its SITE OWNER: one segment of
        the page title is one of the organization's name forms (full name,
        short name, derived acronym), alone or with site-identity words
        only, and that segment is in the site-name position (last), is
        accompanied only by page-type segments ("Vacancies", "Home"), or
        explicitly claims to be official ("<Org> Official Site"); and the URL
        path does not carry the organization's name as a slug (the shape of
        a profile page about it on another site).
    A news headline or an aggregator listing that merely contains the name
    ("<Account Name> Tenders | <Aggregator>") is not a
    verification, and neither is a hostname that contains the name -- a
    hostname match only affects ranking among verified candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from research.url_utils import registrable_domain

# Generic research / publishing PLATFORMS (registrable domains). These are
# platforms, not accounts: any organization may appear on them.
RESEARCH_PLATFORM_DOMAINS = frozenset({
    # social / professional / video
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "tiktok.com",
    "threads.net", "reddit.com", "medium.com", "quora.com", "pinterest.com",
    # reference
    "wikipedia.org", "wikidata.org", "wikiwand.com", "britannica.com",
    # company data / directories / intelligence
    "crunchbase.com", "zoominfo.com", "cbinsights.com", "leadiq.com", "rocketreach.co", "dnb.com", "owler.com",
    "apollo.io", "pitchbook.com", "bloomberg.com", "opencorporates.com", "kompass.com", "signalhire.com",
    "contactout.com", "lusha.com", "craft.co", "tracxn.com", "golden.com", "emis.com", "globaldata.com",
    "datanyze.com", "6sense.com", "theofficialboard.com", "comparably.com", "yellowpages.com", "yelp.com",
    "manta.com", "bizapedia.com", "companieshouse.gov.uk", "marketscreener.com", "reuters.com",
    # job boards
    "glassdoor.com", "indeed.com", "bayt.com", "naukrigulf.com", "gulftalent.com", "monster.com",
    "jooble.org", "careerjet.com", "ziprecruiter.com",
    # tender / bid aggregators
    "protenders.com", "tendersinfo.com", "globaltenders.com", "tendersarabia.com", "biddetail.com",
    "tenderimpulse.com", "tendersontime.com", "dgmarket.com", "devex.com", "tendersgo.com", "bidsinfo.com",
    # press-release wires / regional business news aggregators
    "prnewswire.com", "businesswire.com", "globenewswire.com", "zawya.com", "einpresswire.com",
    "menafn.com", "tradearabia.com",
    # document sharing
    "scribd.com", "slideshare.net", "issuu.com", "yumpu.com", "pdfcoffee.com", "dokumen.pub",
    "academia.edu", "researchgate.net",
})

# Generic role words in a host's registrable label that mark a research /
# publishing site ("protenders", "dailytimes", "jobsite"). Skipped when the
# organization's own name contains the same word (a newspaper account).
_RESEARCH_ROLE_RE = re.compile(
    r"(tender|bid|procure|rfp|jobs?|career|recruit|vacanc|news|times|gazette|tribune|herald|"
    r"observer|journal|magazine|press|directory|yellowpages|wiki|review|insider|report)",
    re.I,
)

# Generic page-type words that may follow a site's own name in a title
# ("<Org> - Vacancies", "<Org> | Official Website").
_PAGE_TYPE_WORDS = frozenset({
    "official", "website", "site", "web", "home", "homepage", "page", "welcome", "portal", "main",
    "vacancies", "vacancy", "careers", "career", "jobs", "recruitment", "e-services", "eservices", "services",
    "about", "about us", "contact", "contact us", "tenders", "news", "media", "en", "ar", "english", "arabic",
    "login", "sign in", "corporate", "group", "investor relations", "investors",
})
# Words that may share a title segment with the organization's own name
# ("<Org> Official Site", "<Org> Home"). Narrower than _PAGE_TYPE_WORDS:
# listing words ("<Org> Tenders", "<Org> Jobs") are aggregator titles.
_SITE_IDENTITY_WORDS = frozenset({
    "official", "website", "site", "web", "home", "homepage", "page", "portal", "main", "online",
})
_TITLE_SEPARATORS_RE = re.compile(r"\s+[|–—:·-]\s+|\s*[|–—·]\s*")


def host_of(url: str | None) -> str:
    try:
        host = urlparse(url or "").netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0].strip(".")
    return host[4:] if host.startswith("www.") else host


def _name_forms(organization_name: str, country: str | None) -> list[str]:
    # Reuse the evidence layer's deterministic name forms (full name, short
    # name, acronym, compound) so identity and evidence agree on what names
    # the account. Imported lazily to keep research importable on its own.
    from commercial.evidence_mapping import compound_aliases, organization_aliases

    forms = [a for a in organization_aliases(organization_name, country=country) if a]
    forms.extend(compound_aliases(organization_name, country=country))
    return forms


def is_research_source_host(url: str | None, organization_name: str = "", country: str | None = None) -> bool:
    """True when the URL's host is a third-party research / publishing
    platform. Such a host is never the organization's website."""
    host = host_of(url)
    if not host:
        return True
    domain = registrable_domain(host)
    if domain in RESEARCH_PLATFORM_DOMAINS or any(host.endswith("." + d) for d in RESEARCH_PLATFORM_DOMAINS):
        return True
    label = domain.split(".")[0]
    own_words = re.sub(r"[^a-z0-9]", " ", (organization_name or "").lower()).split()
    for m in _RESEARCH_ROLE_RE.finditer(label):
        word = m.group(0).lower()
        if not any(word in w or w in word for w in own_words if len(w) >= 3):
            return True
    return False


def _clean_segment(segment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"^(?:welcome to|home of)\s+", "", segment.strip(" .,'\"“”"), flags=re.I)).strip()


def _is_page_type_segment(segment: str) -> bool:
    words = re.sub(r"[^a-z\- ]", " ", segment.lower()).split()
    if not words:
        return True
    joined = " ".join(words)
    return joined in _PAGE_TYPE_WORDS or all(w in _PAGE_TYPE_WORDS for w in words)


def _name_segment(segment: str, forms: set[str]) -> tuple[bool, bool]:
    """(segment is the organization's name, segment claims to be OFFICIAL).

    A name segment is exactly a name form, or a name form plus only generic
    site-identity words ("Acme Official Site", "Acme Home"). The
    official claim is true when those page-type words include "official"."""
    seg = segment.lower().strip()
    if seg in forms:
        return True, False
    for form in sorted(forms, key=len, reverse=True):
        if seg.startswith(form + " "):
            rest = seg[len(form):].strip()
        elif seg.endswith(" " + form):
            rest = seg[: -len(form)].strip()
        else:
            continue
        words = re.sub(r"[^a-z\- ]", " ", rest).split()
        if words and all(w in _SITE_IDENTITY_WORDS for w in words):
            return True, "official" in words
    return False, False


def title_presents_site_owner(title: str, organization_name: str, country: str | None = None) -> bool:
    """The page title names the organization as the site owner (see module
    docstring). Name forms only -- never a name embedded in a headline:
      * a name segment in the site-name position ("<Page> | <Org>"), or
      * a name segment whose other segments are only page-type words
        ("<Org>", "<Org> - Vacancies"), or
      * a name segment that explicitly claims to be official
        ("<Org> Official Site | <tagline>")."""
    if not title or not organization_name:
        return False
    forms = {f.lower() for f in _name_forms(organization_name, country)}
    segments = [_clean_segment(s) for s in _TITLE_SEPARATORS_RE.split(title) if s.strip()]
    if not segments:
        return False
    for i, seg in enumerate(segments):
        is_name, official_claim = _name_segment(seg, forms)
        if not is_name:
            continue
        if official_claim:
            return True                      # "<Org> Official Site | Airports, Training & Services"
        others = segments[:i] + segments[i + 1:]
        if i == len(segments) - 1 and len(segments) > 1 and not any(_name_segment(o, forms)[0] for o in others):
            return True                      # site-name position: "<Page> | <Org>"
        if all(_is_page_type_segment(o) for o in others):
            return True                      # "<Org>" or "<Org> - Vacancies" / "Official Website"
    return False


def path_names_organization(url: str, organization_name: str, country: str | None = None) -> bool:
    """True when the URL PATH contains a multi-word name form of the
    organization as a slug ("/companies/acme-holding-company",
    "/company-profile/xyz-acme-holding_123"). That is the shape of a profile
    page ABOUT the organization on someone else's site; an organization's
    own site does not need its name in its page paths."""
    try:
        path = urlparse(url or "").path.lower()
    except ValueError:
        return False
    norm = "-" + re.sub(r"[^a-z0-9]+", "-", path).strip("-") + "-"
    for form in _name_forms(organization_name, country):
        words = re.findall(r"[a-z0-9]+", form.lower())
        if len(words) >= 2 and "-" + "-".join(words) + "-" in norm:
            return True
    return False


@dataclass(frozen=True)
class OfficialCandidate:
    url: str
    host: str
    reason: str              # "knowledge_panel" | "site_owner_title"


def verify_official_candidate(url: str, title: str, provider: str, organization_name: str,
                              country: str | None = None) -> OfficialCandidate | None:
    if is_research_source_host(url, organization_name, country):
        return None
    if provider.endswith("knowledge_graph"):
        return OfficialCandidate(url=url, host=host_of(url), reason="knowledge_panel")
    if path_names_organization(url, organization_name, country):
        return None
    if title_presents_site_owner(title, organization_name, country):
        return OfficialCandidate(url=url, host=host_of(url), reason="site_owner_title")
    return None


def additional_official_hosts(primary_url: str | None, candidate_urls: list[str],
                              verified_hosts: list[str]) -> list[str]:
    """Other account-owned hosts, deterministic and sorted:
      * any host under the primary's registrable domain (a subdomain of a
        verified official domain is owned by the same registrant), and
      * other independently VERIFIED hosts (a second official domain).
    The primary host itself (with or without www.) is excluded."""
    if not primary_url:
        return []
    primary_host = host_of(primary_url)
    primary_domain = registrable_domain(primary_host)
    out: set[str] = set()
    for url in candidate_urls:
        host = host_of(url)
        if host and registrable_domain(host) == primary_domain and host != primary_host:
            out.add(host)
    for host in verified_hosts:
        if host and host != primary_host:
            out.add(host)
    return sorted(out, key=lambda h: (registrable_domain(h) != primary_domain, h.count("."), h))

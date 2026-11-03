"""Organization resolution.

Deterministic only -- no AI call, no per-organization branching. Given a
name (required) and optionally a country/website/LinkedIn URL, this module
discovers whichever of those weren't supplied, using only generic query
templates that take the organization's own name/country as parameters.

There is no lookup table of known companies anywhere in this file, by
design (Phase 0 §6 / Phase 1B architecture constraint).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from research.providers.base import SearchProvider
from research.providers.router import acquire_family
from research.sources import is_noise_host
from research.official_domains import additional_official_hosts, host_of, is_research_source_host, verify_official_candidate
from research.url_utils import canonical_official_site, sanitize_resolved_url

_SOCIAL_OR_WIKI_HOSTS = (
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com",
    "youtube.com", "wikipedia.org", "crunchbase.com", "bloomberg.com",
    "tiktok.com",
)


@dataclass
class OrganizationInput:
    name: str
    country: str | None = None
    website: str | None = None
    linkedin_url: str | None = None


@dataclass
class ResolvedOrganization:
    name: str
    country: str | None
    website: str | None
    linkedin_company_url: str | None
    website_was_supplied: bool
    linkedin_was_supplied: bool
    # Account identity (deterministic): the primary official host first, then
    # additional verified / account-owned hosts. Never a research source.
    official_domains: list[str] = field(default_factory=list)
    domain_verification: str = ""   # "supplied" | "knowledge_panel" | "site_owner_title" | "" (unresolved)


def _score_official_candidate(url: str, title: str, snippet: str, company: str, country: str) -> float:
    text = f"{title} {snippet}".lower()
    score = 0.0
    company_words = [w for w in company.lower().split() if len(w) >= 3]
    if company.lower() in text:
        score += 50
    score += min(30, sum(5 for w in company_words[:6] if w in text))
    if country and country.lower() in text:
        score += 8
    host = urlparse(url).netloc.lower()
    # A shorter, cleaner domain (no deep path) reads as more likely to be a
    # homepage than a deep article/profile URL.
    if urlparse(url).path in ("", "/"):
        score += 10
    if any(w in host for w in company_words[:3]):
        score += 15
    return score


def _short_name(company: str, country: str | None) -> str | None:
    from commercial.evidence_mapping import organization_aliases

    aliases = organization_aliases(company, country=country)
    short = next((a for a in aliases[1:] if " " in a and a.lower() != company.lower()), None)
    return short


def discover_official_site(
    account: OrganizationInput, providers: list[SearchProvider]
) -> tuple[str | None, list]:
    website, _domains, _reason, results = discover_official_identity(account, providers)
    return website, results


def discover_official_identity(
    account: OrganizationInput, providers: list[SearchProvider]
) -> tuple[str | None, list[str], str, list]:
    """(canonical website, official hosts [primary first], verification
    reason, raw results).

    Identity fix: a result becomes the official website only when it is
    VERIFIED as the organization's own site (research.official_domains):
    research-source platforms (tender aggregators, LinkedIn, news sites,
    directories...) are never eligible, and a page merely mentioning the
    name -- or a hostname merely containing it -- is not verification.
    Among verified candidates the existing score orders them; ties break
    deterministically (provider rank, then URL), so the same evidence always
    yields the same identity regardless of which research result is
    strongest. When nothing verifies, the honest answer is None."""
    company = account.name.strip()
    country = (account.country or "").strip()
    queries = [f'"{company}" {country} official website'.strip(), f'"{company}" official website']
    # Organizations often brand their site with a shorter form of the legal
    # name ("<Name> Management Company" -> "<Name>"). The short form is
    # derived deterministically from the entered name (the same rule the
    # evidence layer uses); it only widens the candidates -- every candidate
    # must still pass the same verification.
    short = _short_name(company, country or None)
    if short:
        queries.append(f'"{short}" {country} official website'.strip())
    outcome = acquire_family(
        family="organization_official_site",
        queries=queries,
        providers=providers,
        num_results=10,
    )
    candidates = []
    for r in outcome.results:
        if is_noise_host(r.url):
            continue
        host = urlparse(r.url).netloc.lower()
        if any(h in host for h in _SOCIAL_OR_WIKI_HOSTS):
            continue
        clean = sanitize_resolved_url(r.url)
        if not clean:
            # A high-scoring but malformed candidate (an HTML fragment, an
            # encoded/concatenated string, a non-http(s) value) is skipped:
            # confident-looking garbage is worse than "could not resolve".
            continue
        score = _score_official_candidate(r.url, r.title, r.snippet, company, country)
        candidates.append((score, r, clean))
    candidates.sort(key=lambda x: (-x[0], x[1].rank, x[2]))
    clean_ranked = [c for _, _, c in candidates]
    verified = []
    for score, r, clean in candidates:
        v = verify_official_candidate(clean, r.title, r.provider, company, country or None)
        if v:
            verified.append((score + (100 if v.reason == "knowledge_panel" else 0), r.rank, clean, v))
    if not verified:
        return None, [], "", outcome.results
    verified.sort(key=lambda x: (-x[0], x[1], x[2]))
    _, _, chosen, reason = verified[0]
    # The resolved website is the organization's canonical homepage, never a
    # deep link into an application on its estate (a recruitment/e-services
    # page); see research.url_utils.canonical_official_site. Only candidates
    # on the chosen registrable domain can supply that homepage.
    website = canonical_official_site(chosen, clean_ranked)
    others = additional_official_hosts(website, clean_ranked, [v.host for _, _, _, v in verified])
    return website, [host_of(website)] + others, reason.reason, outcome.results


def discover_linkedin_company_page(
    account: OrganizationInput, providers: list[SearchProvider]
) -> tuple[str | None, list]:
    company = account.name.strip()
    country = (account.country or "").strip()
    queries = [
        f'site:linkedin.com/company "{company}"',
        f'site:linkedin.com/company "{company}" "{country}"' if country else f'site:linkedin.com/company "{company}"',
    ]
    outcome = acquire_family(
        family="organization_linkedin_company",
        queries=queries,
        providers=providers,
        num_results=10,
    )
    for r in outcome.results:
        if "linkedin.com" in urlparse(r.url).netloc.lower() and "/company/" in urlparse(r.url).path.lower():
            clean = sanitize_resolved_url(r.url, require_host_contains="linkedin.com")
            if clean:
                return clean, outcome.results
            # A malformed LinkedIn-looking URL is skipped, not returned;
            # keep scanning remaining results rather than giving up
            # immediately, since a later result may be clean.
            continue
    return None, outcome.results


def resolve_organization(
    account: OrganizationInput, providers: list[SearchProvider]
) -> ResolvedOrganization:
    """Deterministic resolution. Website discovery only runs when not
    supplied; LinkedIn company-page discovery only runs when not supplied.
    """
    # A caller-supplied website/LinkedIn URL is also untrusted input (it may
    # come from a form field, an upstream integration, or a copy/paste) --
    # validate it the same way a discovered one is validated. A malformed
    # supplied value is treated as "not supplied" so discovery gets a
    # chance to find a clean one instead.
    supplied_website = sanitize_resolved_url(account.website)
    if supplied_website and is_research_source_host(supplied_website, account.name, account.country):
        # A research platform is never the organization's website, however
        # it was supplied.
        supplied_website = None
    website = supplied_website
    website_was_supplied = bool(supplied_website)
    official_domains: list[str] = [host_of(supplied_website)] if supplied_website else []
    domain_verification = "supplied" if supplied_website else ""
    if not website:
        website, official_domains, domain_verification, _ = discover_official_identity(account, providers)

    supplied_linkedin = sanitize_resolved_url(account.linkedin_url, require_host_contains="linkedin.com")
    linkedin_url = supplied_linkedin
    linkedin_was_supplied = bool(supplied_linkedin)
    if not linkedin_url:
        linkedin_url, _ = discover_linkedin_company_page(account, providers)

    return ResolvedOrganization(
        name=account.name.strip(),
        country=(account.country or "").strip() or None,
        website=website,
        linkedin_company_url=linkedin_url,
        website_was_supplied=website_was_supplied,
        linkedin_was_supplied=linkedin_was_supplied,
        official_domains=official_domains,
        domain_verification=domain_verification,
    )

"""Organization resolution.

Deterministic only -- no AI call, no per-organization branching. Given a
name (required) and optionally a country/website/LinkedIn URL, this module
discovers whichever of those weren't supplied, using only generic query
templates that take the organization's own name/country as parameters.

There is no lookup table of known companies anywhere in this file, by
design (Phase 0 §6 / Phase 1B architecture constraint).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from research.providers.base import SearchProvider
from research.providers.router import acquire_family
from research.sources import is_noise_host
from research.url_utils import sanitize_resolved_url

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


def discover_official_site(
    account: OrganizationInput, providers: list[SearchProvider]
) -> tuple[str | None, list]:
    company = account.name.strip()
    country = (account.country or "").strip()
    queries = [f'"{company}" {country} official website'.strip(), f'"{company}" official website']
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
        candidates.append((_score_official_candidate(r.url, r.title, r.snippet, company, country), r))
    candidates.sort(key=lambda x: -x[0])
    # Walk candidates in score order; the first one that passes URL
    # validation wins. A high-scoring but malformed candidate (an HTML
    # fragment, an encoded/concatenated string, a non-http(s) value) is
    # skipped rather than returned -- confident-looking garbage is worse
    # than an honest "could not resolve" (None).
    for _, r in candidates:
        clean = sanitize_resolved_url(r.url)
        if clean:
            return clean, outcome.results
    return None, outcome.results


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
    website = supplied_website
    website_was_supplied = bool(supplied_website)
    if not website:
        website, _ = discover_official_site(account, providers)

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
    )

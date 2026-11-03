"""Defensive URL validation for anything that becomes a RESOLVED organization
identifier (official website, LinkedIn company page).

This exists because a search provider's result field is external, untrusted
input. Even a real JSON API (Serper) can occasionally hand back an oddly
encoded or concatenated string, and the OpenRouter fallback path -- despite
never letting the model enumerate results -- still passes citation fields
through an LLM API response, which is one more reason not to trust a URL
string blindly before it becomes a stored, authoritative "this is the
organization's website" fact.

The rule is: when in doubt, return None. A missing resolved website is an
honest, visible gap (reported in acquisition diagnostics). A malformed one
silently stored as fact is worse -- it looks confident and is wrong.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_MAX_URL_LENGTH = 300

# Literal/encoded markup fragments that should never appear in a real URL.
# Their presence means we captured a chunk of HTML/snippet text, not a URL.
_HTML_FRAGMENT_MARKERS = ("<", ">", "&lt;", "&gt;", "&amp;", "%3C", "%3E", "\n", "\r", "\t")

# A real hostname is letters/digits/hyphens/dots, optionally with a port.
_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9.-]+(:\d{1,5})?$")


def is_valid_resolved_url(url: str | None, *, require_host_contains: str | None = None) -> bool:
    """True only for a clean, well-formed http(s) URL suitable for storage
    as a resolved organization fact.

    `require_host_contains` optionally requires the hostname to contain a
    given substring (e.g. "linkedin.com" for LinkedIn company-page checks).
    """
    if not url or not isinstance(url, str):
        return False
    candidate = url.strip()
    if not candidate or len(candidate) > _MAX_URL_LENGTH:
        return False
    if any(ch.isspace() for ch in candidate):
        return False
    if any(marker in candidate for marker in _HTML_FRAGMENT_MARKERS):
        return False
    # More than one scheme occurrence means two URLs (or a URL plus
    # trailing garbage) got concatenated into one string.
    if candidate.count("http://") + candidate.count("https://") != 1:
        return False
    if not candidate.startswith(("http://", "https://")):
        return False

    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    if not _HOSTNAME_RE.match(parsed.netloc):
        return False
    if "." not in parsed.netloc:
        # Reject bare hostnames with no TLD/dot -- not a real public site.
        return False
    # Reject a userinfo-bearing netloc (user:pass@host) -- never expected
    # for an organization's public website/LinkedIn page and is more often
    # a sign of a malformed/garbled capture.
    if "@" in parsed.netloc:
        return False

    if require_host_contains and require_host_contains.lower() not in parsed.netloc.lower():
        return False

    return True


def sanitize_resolved_url(url: str | None, *, require_host_contains: str | None = None) -> str | None:
    """Return a clean, trailing-slash-stripped URL, or None if it fails
    validation. Never returns a malformed string -- callers must treat None
    as "could not confidently resolve this", not silently substitute
    something unvalidated.
    """
    if not is_valid_resolved_url(url, require_host_contains=require_host_contains):
        return None
    return url.strip().rstrip("/")


# ---------------------------------------------------------------------------
# Canonical site identity (Signal Intelligence refinement)
# ---------------------------------------------------------------------------
#
# Organization resolution can land on a deep link inside an organization's
# web estate -- a recruitment portal, an e-services application, a login
# page (e.g. "eservices.<domain>/.../Vacancies.aspx") -- rather than the
# organization's main website. Every downstream use of the resolved website
# (homepage crawl, `site:` restricted search, first-party source checks)
# then works against that one application instead of the organization's
# site. These helpers are generic: no organization, sector or country is
# named; the second-level label list is the standard set of ccTLD
# second-level registration labels ("co.om", "com.au", "co.uk", ...).
_SECOND_LEVEL_REGISTRATION_LABELS = frozenset({
    "co", "com", "gov", "org", "net", "ac", "edu", "mil", "ltd", "plc", "sch", "nic", "med",
    "bank", "gen", "ind", "biz", "info", "or", "go", "ne",
})
# First-label names of application/service hosts that are part of an
# organization's estate but are not its main website.
_SERVICE_SUBDOMAIN_LABELS = frozenset({
    "eservices", "eservice", "services", "portal", "careers", "career", "jobs", "recruitment", "hr",
    "apply", "login", "auth", "sso", "mail", "webmail", "app", "apps", "my", "secure", "online", "ess",
    "erp", "intranet", "extranet", "etender", "etenders", "eprocurement", "shop", "store", "support",
    "help", "docs", "status", "cdn", "static", "media", "assets",
})


def _host_of(url_or_host: str | None) -> str:
    value = (url_or_host or "").strip()
    if "://" in value:
        try:
            value = urlparse(value).netloc
        except ValueError:
            return ""
    return value.lower().split("@")[-1].split(":")[0].strip(".")


def registrable_domain(url_or_host: str | None) -> str:
    """The organization-level domain of a URL or host: "www.example.co.om",
    "eservices.example.co.om" and "example.co.om" all give "example.co.om";
    "news.example.com" gives "example.com"."""
    host = _host_of(url_or_host)
    if host.startswith("www."):
        host = host[4:]
    labels = [label for label in host.split(".") if label]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL_REGISTRATION_LABELS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def is_service_subdomain(url_or_host: str | None) -> bool:
    """True for an application/service host (careers, e-services, portal,
    login...) rather than an organization's main website."""
    host = _host_of(url_or_host)
    if host.startswith("www."):
        return False
    labels = host.split(".")
    return len(labels) > len(registrable_domain(host).split(".")) and labels[0] in _SERVICE_SUBDOMAIN_LABELS


def site_root(url: str) -> str:
    """scheme://host of a URL (no path, query or fragment)."""
    parsed = urlparse(url)
    return f"{parsed.scheme or 'https'}://{parsed.netloc.lower()}"


def canonical_official_site(chosen_url: str, ranked_candidate_urls: list[str]) -> str:
    """Canonical homepage for a resolved official-site URL.

    1. If any candidate (in rank order) is on the same registrable domain
       and is NOT a service/application host, its site root is used -- the
       organization's main website, as evidenced by the search results.
    2. Otherwise, if the chosen URL is on a service host, the main site is
       taken to be "www." + the registrable domain.
    3. Otherwise the chosen URL's own site root.
    """
    domain = registrable_domain(chosen_url)
    for url in ranked_candidate_urls:
        if registrable_domain(url) == domain and not is_service_subdomain(url):
            return site_root(url)
    if is_service_subdomain(chosen_url):
        return f"{urlparse(chosen_url).scheme or 'https'}://www.{domain}"
    return site_root(chosen_url)

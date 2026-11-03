"""A KNOWN LinkedIn profile URL as an IDENTITY lead for People discovery.

What this module is (and is not):

  * A known URL / slug is identity information ONLY. It is never evidence of
    employment, and nothing here assesses employment: whatever content is
    obtained for the profile goes through the unchanged association gate
    (commercial.people_association) like any other result.
  * Only an exact canonical slug ties retrieved content to the supplied URL.
    A name, a title or another person's snippet never resolves identity.
  * LinkedIn is never fetched or scraped. Content comes from a search-provider
    result carrying the exact slug, or from visible text the user supplies.
  * A URL without usable content creates no person of any status.

Everything here is a pure function (no I/O, no network, no keys, no names).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse

# -- identity states (identity only; employment status is the gate's business) --
INVALID_URL = "invalid_url"                                  # not a valid LinkedIn /in/<slug> profile URL
ALREADY_DISCOVERED = "already_discovered"                    # exact slug already in current discovery results
RESOLVED_VIA_SEARCH = "resolved_via_search"                  # a provider result carries the exact slug
RESOLVED_VIA_SUPPLIED_TEXT = "resolved_via_supplied_text"    # user-supplied visible text, assessed by the gate
UNRESOLVED_NO_CONTENT = "unresolved_no_content"              # valid slug, no usable content: NO person is created
STATES = (INVALID_URL, ALREADY_DISCOVERED, RESOLVED_VIA_SEARCH, RESOLVED_VIA_SUPPLIED_TEXT, UNRESOLVED_NO_CONTENT)

ORIGIN_SUPPLIED_TEXT = "user_supplied_text"
KNOWN_DIMENSION = "known_profile_probe"
EXCERPT_LIMIT = 400
TITLE_LIMIT = 300
SNIPPET_LIMIT = 8000
MIN_USABLE_ALNUM = 12

_SLUG_RE = re.compile(r"^[\w-]{3,100}$")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.I)


@dataclass(frozen=True)
class CanonicalProfile:
    """Identity only: the canonical slug and URL. No employment fields, ever."""

    slug: str
    url: str


def canonical_slug(value: str | None) -> str:
    """Percent-decoded, NFC-normalised, lower-case slug text."""
    return unicodedata.normalize("NFC", unquote(value or "")).strip().lower()


def _host_ok(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def _parse(url) -> tuple[CanonicalProfile | None, str | None]:
    if not isinstance(url, str) or not url.strip():
        return None, "empty"
    s = url.strip()
    if _SCHEME_RE.match(s) and not re.match(r"^[a-z0-9.\-]+:\d+(/|$)", s, re.I):
        if not re.match(r"^https?://", s, re.I):
            return None, "scheme"
    else:
        head = s.split("/", 1)[0]
        if (re.search(r"\s", s) and "/" not in s) or "." not in head:
            return None, "not_a_profile_path"
        s = "https://" + s
    try:
        parsed = urlparse(s)
        host = parsed.hostname
        has_userinfo = parsed.username is not None or parsed.password is not None
    except ValueError:
        return None, "not_a_profile_path"
    if not _host_ok(host) or has_userinfo:
        return None, "host"
    path = parsed.path
    if path.endswith("/"):
        path = path[:-1]
    parts = path.split("/")
    if len(parts) < 2 or parts[0] != "" or parts[1].lower() != "in" or len(parts) == 2 or parts[2] == "":
        return None, "not_a_profile_path"
    if len(parts) > 3:
        return None, "extra_subpath"
    slug = canonical_slug(parts[2])
    if not _SLUG_RE.match(slug):
        return None, "bad_slug"
    return CanonicalProfile(slug=slug, url=f"https://www.linkedin.com/in/{quote(slug, safe='-_')}"), None


def canonicalize_profile_url(url) -> CanonicalProfile | None:
    """Canonical identity for a valid LinkedIn `/in/<slug>` URL (any LinkedIn host, case,
    query, fragment, optional trailing slash); None for anything else."""
    return _parse(url)[0]


def invalid_url_reason(url) -> str | None:
    """None for a valid profile URL; otherwise a short reason CODE (never the input)."""
    return _parse(url)[1]


def _result_slug(url: str | None) -> str | None:
    """The slug of a search-result URL on a LinkedIn host (extra sub-paths tolerated: the same profile)."""
    try:
        parsed = urlparse(url or "")
        host = parsed.hostname
    except ValueError:
        return None
    if not _host_ok(host):
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2 and parts[0].lower() == "in" and parts[1]:
        return canonical_slug(parts[1])
    return None


def match_results(slug: str, results) -> list:
    """Results whose canonical slug EQUALS `slug`. Near matches, namesakes, company pages,
    posts and other hosts never match; a name or title is never consulted."""
    want = canonical_slug(slug)
    return [r for r in (results or []) if want and _result_slug(getattr(r, "url", None)) == want]


def build_probe_query(restricted: bool, slug: str) -> str:
    """The single slug probe. Assumption (unverified until an approved live run): the search
    engine's `site:` operator honours the profile path prefix. No OR, no quotes, no name."""
    return f"site:linkedin.com/in/{slug}" if restricted else f"linkedin.com/in/{slug}"


# ---------------------------------------------------------------------------
# Supplied visible profile text
# ---------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_text(text: str) -> str:
    text = _CONTROL.sub("", str(text).replace("\r\n", "\n").replace("\r", "\n"))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def source_from_supplied_text(canonical: CanonicalProfile, text):
    """A labelled `Source` for the profile's exact canonical URL, or None when the text has no
    usable content. The first non-empty line is the title and the rest the snippet; the existing
    association gate judges them like any search result. Provenance is explicit and the text is
    never treated as independently verified."""
    if not text or not isinstance(text, str):
        return None
    cleaned = _clean_text(text)
    if len(re.findall(r"[^\W_]", cleaned)) < MIN_USABLE_ALNUM:
        return None
    from research.sources import SOURCE_TYPE_LINKEDIN_PROFILE, Source  # lazy: keeps this module import-light

    first, _, rest = cleaned.partition("\n")
    hit = {"provider": ORIGIN_SUPPLIED_TEXT, "dimension": ORIGIN_SUPPLIED_TEXT, "page": 0, "query": None}
    return Source(
        url=canonical.url, domain="www.linkedin.com", title=first[:TITLE_LIMIT],
        source_type=SOURCE_TYPE_LINKEDIN_PROFILE, snippet=rest[:SNIPPET_LIMIT],
        discovered_via_query="(user-supplied profile text)", discovery_provider=ORIGIN_SUPPLIED_TEXT,
        family="linkedin_people",
        extra={"linkedin_kind": "in", "discovery_dimension": ORIGIN_SUPPLIED_TEXT,
               "discovery_providers": [ORIGIN_SUPPLIED_TEXT], "supplemental_snippets": [], "extra_snippets": [],
               "discovery_hits": [hit], "origin": ORIGIN_SUPPLIED_TEXT, "user_supplied_unverified": True},
    )


# ---------------------------------------------------------------------------
# Sanitised excerpt for diagnostics
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_SECRET_KV = re.compile(
    r"(?i)\b(?:x-subscription-token|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret|password|passwd|"
    r"authorization|bearer)\b\s*[:=]?\s*(?:bearer\s+)?[^\s,;]+")
_TRACKING = re.compile(r"(?i)\b(?:trk|utm_[a-z]+|trackingid|lipi|refid|originalsubdomain|midtoken|midsig)=[^\s&]*&?")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_TOKEN = re.compile(r"(?<![\w/-])(?=[A-Za-z0-9_\-+/=]*[A-Za-z])(?=[A-Za-z0-9_\-+/=]*\d)[A-Za-z0-9_\-+/=]{24,}(?![\w/-])")


def sanitize_excerpt(text, limit: int = EXCERPT_LIMIT) -> str:
    """A short excerpt (at most `limit` characters) with URL query strings and fragments,
    tracking parameters, key/token/password assignments, e-mail addresses and long opaque
    tokens removed. Safe to place in a report; never the full text."""
    if not text:
        return ""
    s = _CONTROL.sub("", str(text))
    urls: list[str] = []

    def keep_url(m: re.Match) -> str:
        urls.append(re.split(r"[?#]", m.group(0), maxsplit=1)[0])
        return f"\x01{len(urls) - 1}\x02"

    s = _URL_RE.sub(keep_url, s)
    s = _SECRET_KV.sub("[redacted]", s)
    s = _TRACKING.sub("", s)
    s = _EMAIL.sub("[redacted]", s)
    s = _LONG_TOKEN.sub("[redacted]", s)
    s = re.sub(r"\x01(\d+)\x02", lambda m: urls[int(m.group(1))], s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"

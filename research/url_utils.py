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

"""Organization identity: who the account IS, separate from what research
found ABOUT it.

    organization_name, country,
    primary_official_domain, additional_official_domains[],
    logo, logo_source, logo_status

Built from the existing resolver's output (research.organization:
ResolvedOrganization) -- not a second resolution system -- and stored on the
report. Research sources never feed identity, with one deterministic
exception: a host UNDER the verified official domain (e.g. an e-services
subdomain) found during acquisition is an additional official domain,
because it is owned by the same registrant.

Logo: taken only from the verified official website -- an icon the official
homepage itself declares (apple-touch-icon / icon links), else its
/favicon.ico -- downloaded once at research time, validated as a real image
by its bytes, size-capped, and embedded as a data: URI. Anything else (no
official site, fetch failure, non-image, oversized) gives logo=None and the
UI renders an initials avatar, so a broken-image icon is impossible. Research
pages, article images and search-engine image results are never used.
"""

from __future__ import annotations

import base64
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin

from research.official_domains import additional_official_hosts, host_of, is_research_source_host
from research.url_utils import registrable_domain

LOGO_VERIFIED = "official_site"        # icon fetched from the verified official website
LOGO_UNAVAILABLE = "unavailable"       # tried, nothing trustworthy -> initials
LOGO_NOT_ATTEMPTED = "not_attempted"   # no fetch performed (offline build, no official site) -> initials
MAX_LOGO_BYTES = 150_000
MIN_LOGO_BYTES = 64


@dataclass
class OrganizationIdentity:
    organization_name: str
    country: str | None = None
    primary_official_domain: str | None = None
    additional_official_domains: list[str] = field(default_factory=list)
    official_website: str | None = None
    domain_verification: str = ""
    logo: str | None = None              # data:image/...;base64,... only
    logo_source: str | None = None       # URL the logo was fetched from (on/declared by the official site)
    logo_status: str = LOGO_NOT_ATTEMPTED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_identity(org: Any, sources: list[Any] | None = None) -> OrganizationIdentity:
    """Identity from a ResolvedOrganization (no network). `sources` only
    contribute hosts under the verified official domain."""
    website = getattr(org, "website", None)
    name = getattr(org, "name", "") or ""
    country = getattr(org, "country", None)
    if website and is_research_source_host(website, name, country):
        website = None                    # defense in depth: never a research platform
    domains = [d for d in (getattr(org, "official_domains", None) or []) if d]
    primary = host_of(website) if website else None
    if primary and (not domains or domains[0] != primary):
        domains = [primary] + [d for d in domains if d != primary]
    source_urls = [getattr(s, "url", "") for s in (sources or [])]
    extra = additional_official_hosts(website, source_urls, []) if website else []
    additional = sorted(
        {d for d in domains[1:] + extra if d and d != primary},
        key=lambda h: (registrable_domain(h) != registrable_domain(primary or ""), h.count("."), h),
    )
    return OrganizationIdentity(
        organization_name=name,
        country=country,
        primary_official_domain=primary,
        additional_official_domains=additional,
        official_website=website,
        domain_verification=getattr(org, "domain_verification", "") or ("supplied" if getattr(org, "website_was_supplied", False) else ""),
    )


# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------

_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"""([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")


def icon_candidates(homepage_html: str, website: str) -> list[str]:
    """Icon URLs the official homepage itself declares, best first, then the
    site's /favicon.ico."""
    ranked: list[tuple[int, int, str]] = []
    for i, tag in enumerate(_LINK_TAG_RE.findall(homepage_html or "")):
        attrs = {m.group(1).lower(): (m.group(2) or m.group(3) or m.group(4) or "") for m in _ATTR_RE.finditer(tag)}
        rel = attrs.get("rel", "").lower()
        href = attrs.get("href", "").strip()
        if not href or href.startswith("data:") or "icon" not in rel or "mask-icon" in rel:
            continue
        sizes = re.findall(r"(\d+)x\d+", attrs.get("sizes", ""))
        size = max((int(s) for s in sizes), default=0)
        rank = 3 if "apple-touch-icon" in rel else (2 if size >= 32 or href.lower().endswith((".png", ".svg")) else 1)
        ranked.append((rank * 10_000 + min(size, 512), -i, urljoin(website, href)))
    out = [u for _, _, u in sorted(ranked, reverse=True)]
    favicon = urljoin(website, "/favicon.ico")
    if favicon not in out:
        out.append(favicon)
    return [u for u in out if u.startswith(("http://", "https://"))]


def image_mime(data: bytes) -> str | None:
    """MIME type from the bytes themselves (never trusted from headers)."""
    if not data or len(data) < MIN_LOGO_BYTES or len(data) > MAX_LOGO_BYTES:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:2048].decode("utf-8", "ignore").lower()
    if "<svg" in head and "<script" not in data.decode("utf-8", "ignore").lower() and "<html" not in head:
        return "image/svg+xml"
    return None


def logo_data_uri(data: bytes) -> str | None:
    mime = image_mime(data)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}" if mime else None


def is_valid_logo(value: str | None) -> bool:
    """True only for a data:image URI whose decoded bytes are a real image."""
    if not value or not isinstance(value, str):
        return False
    m = re.match(r"^data:(image/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=]+)$", value)
    if not m:
        return False
    try:
        data = base64.b64decode(m.group(2), validate=True)
    except (ValueError, TypeError):
        return False
    return image_mime(data) == m.group(1)


def _default_fetch_html(url: str) -> str:
    from research.web import _raw_html

    html, _ = _raw_html(url)
    return html or ""


def _default_fetch_bytes(url: str) -> bytes:
    import requests

    from research.fetcher import HEADERS

    try:
        with requests.get(url, headers=HEADERS, timeout=8, stream=True) as resp:
            if resp.status_code != 200:
                return b""
            data = b""
            for chunk in resp.iter_content(16_384):
                data += chunk
                if len(data) > MAX_LOGO_BYTES:
                    return b""
            return data
    except requests.exceptions.RequestException:
        return b""


def attach_official_logo(
    identity: OrganizationIdentity,
    *,
    fetch_html: Callable[[str], str] | None = None,
    fetch_bytes: Callable[[str], bytes] | None = None,
    max_attempts: int = 4,
) -> OrganizationIdentity:
    """Fill identity.logo from the verified official website only. Never
    raises; on any failure the identity keeps logo=None (initials)."""
    website = identity.official_website
    if not website or is_research_source_host(website, identity.organization_name, identity.country):
        identity.logo, identity.logo_source, identity.logo_status = None, None, LOGO_NOT_ATTEMPTED
        return identity
    fetch_html = fetch_html or _default_fetch_html
    fetch_bytes = fetch_bytes or _default_fetch_bytes
    try:
        html = fetch_html(website) or ""
    except Exception:  # noqa: BLE001 -- identity must never break a research run
        html = ""
    for url in icon_candidates(html, website)[:max_attempts]:
        try:
            data = fetch_bytes(url) or b""
        except Exception:  # noqa: BLE001
            data = b""
        uri = logo_data_uri(data)
        if uri:
            identity.logo, identity.logo_source, identity.logo_status = uri, url, LOGO_VERIFIED
            return identity
    identity.logo, identity.logo_source, identity.logo_status = None, None, LOGO_UNAVAILABLE
    return identity

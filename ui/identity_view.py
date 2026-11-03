"""Account identity block for the header (presentation only).

Reads the report's `identity` (research.identity.OrganizationIdentity). It
never derives a domain from research evidence: when a report carries no
identity (older reports) the only fallback is the report's resolved website,
and only if it is not a research-source platform. An unknown domain is
omitted, never replaced. The logo is rendered only when it is a validated
data:image URI; otherwise an initials avatar is shown -- so a broken-image
icon can never appear.
"""

from __future__ import annotations

import html
import re
from typing import Any

from research.identity import is_valid_logo
from research.official_domains import host_of, is_research_source_host

MAX_INLINE_DOMAINS = 2
_INITIALS_SKIP = frozenset({"and", "of", "the", "for", "&", "de", "du", "la", "le", "al", "el"})


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def initials(name: str | None) -> str:
    words = [w for w in re.findall(r"[^\W_]+", name or "", re.UNICODE) if w.lower() not in _INITIALS_SKIP]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def identity_for_report(report: dict[str, Any] | None) -> dict[str, Any]:
    """Normalized identity for display. Never uses research sources."""
    report = report or {}
    ident = dict(report.get("identity") or {})
    name = ident.get("organization_name") or report.get("organization_name") or ""
    country = ident.get("country") if "country" in ident else report.get("country")
    if ident:
        primary = ident.get("primary_official_domain")
        additional = [d for d in ident.get("additional_official_domains") or [] if d]
        website = ident.get("official_website")
    else:
        website = report.get("website")
        primary = host_of(website) if website else None
        additional = []
    # Defense in depth: a research platform is never shown as the account's domain.
    if primary and is_research_source_host("https://" + primary, name, country):
        primary, website, additional = None, None, []
    additional = [d for d in additional if not is_research_source_host("https://" + d, name, country) and d != primary]
    logo = ident.get("logo")
    return {
        "organization_name": name,
        "country": country,
        "primary_official_domain": primary,
        "additional_official_domains": additional if primary else [],
        "official_website": website if primary else None,
        "logo": logo if is_valid_logo(logo) else None,
        "logo_status": ident.get("logo_status") or "not_attempted",
    }


def domains_html(identity: dict[str, Any]) -> str:
    """Compact official-domain text: up to two inline; more collapse to
    "<primary> + N official domains" with the full list in a tooltip."""
    primary = identity.get("primary_official_domain")
    if not primary:
        return ""
    additional = identity.get("additional_official_domains") or []
    all_domains = [primary] + additional
    if len(all_domains) <= MAX_INLINE_DOMAINS:
        return " · ".join(f'<span class="ar-identity-domain">{_e(d)}</span>' for d in all_domains)
    tooltip = "Official domains: " + ", ".join(all_domains)
    n = len(additional)
    return (
        f'<span class="ar-identity-domain">{_e(primary)}</span> '
        f'<span class="ar-identity-more" title="{_e(tooltip)}">+ {n} official domain{"s" if n != 1 else ""}</span>'
    )


def logo_html(identity: dict[str, Any]) -> str:
    logo = identity.get("logo")
    if logo and is_valid_logo(logo):
        return f'<span class="ar-identity-logo has-image"><img src="{_e(logo)}" alt="" /></span>'
    return f'<span class="ar-identity-logo is-initials" aria-hidden="true">{_e(initials(identity.get("organization_name")))}</span>'


def identity_block_html(identity: dict[str, Any]) -> str:
    """[logo] Name / verified official domain(s) · Country. The verified
    primary domain leads as the disambiguator; country is secondary."""
    sub: list[str] = []
    domains = domains_html(identity)
    if domains:
        sub.append(domains)
    sub.append(f'<span>{_e(identity.get("country") or "Country not specified")}</span>')
    return (
        '<div class="ar-identity">'
        f"{logo_html(identity)}"
        '<div class="ar-identity-text">'
        f'<div class="ar-topbar-account-name">{_e(identity.get("organization_name") or "Organization")}</div>'
        f'<div class="ar-topbar-account-sub">{" · ".join(sub)}</div>'
        "</div></div>"
    )

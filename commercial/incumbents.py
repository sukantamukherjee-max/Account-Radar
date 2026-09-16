"""Incumbent / technology-vendor / implementation-partner detection.

This is a GENERIC, horizontal lexicon of well-known technology vendor/
brand names and generic incumbent-relationship language -- the same kind
of fixed reference list the Otech capability catalogue itself is. It is
NOT customer or sector hardcoding: no target organization, country, or
industry name appears here, and this file has no idea what organization is
being researched. It plays the same role for "who else is already there"
that the capability catalogue plays for "what Otech sells."

The result of this module is evidence, retained in its own right. It is
never converted into a CapabilityMapping or used to raise a mapping's
confidence -- an incumbent's or competitor's presence is sales *context*,
not an Otech opportunity by itself (a common, easy mistake: "the RFP
mentions Cisco" does not mean "Otech should pitch networking" -- it might
mean the opposite, that networking is already spoken for).
"""

from __future__ import annotations

import re

from commercial.models import IncumbentEvidence
from research.sources import Source

# A deliberately modest set of widely-recognized technology vendor/brand
# names spanning network/cloud/security/infrastructure/enterprise software.
# Not exhaustive by design -- see Known Limitations in the Phase 1C-H
# report. Matched as whole words/phrases, case-insensitive.
_KNOWN_VENDOR_TERMS = [
    "cisco", "juniper", "huawei", "fortinet", "palo alto networks", "check point",
    "zscaler", "aws", "amazon web services", "microsoft azure", "google cloud",
    "ibm cloud", "oracle cloud", "dell", "hpe", "hewlett packard enterprise",
    "nutanix", "vmware", "ibm", "oracle", "sap", "servicenow", "ericsson",
    "nokia", "nvidia", "salesforce", "workday", "citrix", "commvault",
    "veeam", "rubrik", "pure storage", "netapp", "arista", "extreme networks",
]
_VENDOR_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _KNOWN_VENDOR_TERMS) + r")\b", re.I
)

# Generic relationship language that signals "someone else is already the
# vendor/partner here" without naming a specific brand.
_INCUMBENT_LANGUAGE_PATTERN = re.compile(
    r"\b(incumbent|existing (vendor|provider|supplier)|current (vendor|provider|supplier)|"
    r"implementation partner|selected \w+ as (its|their) (provider|vendor|partner)|"
    r"long-?standing partnership|renewed (its|their) contract with|extends? (its|their) partnership with)\b",
    re.I,
)

_PARTNERSHIP_LANGUAGE_PATTERN = re.compile(
    r"\b(partners? with|in partnership with|teamed up with|collaborat(ion|es?|ing) with)\b", re.I
)


def detect_incumbent_evidence(source: Source, *, max_items: int = 5) -> list[IncumbentEvidence]:
    text = (source.content or "").strip() or f"{source.title} {source.snippet}".strip()
    if not text:
        return []

    out: list[IncumbentEvidence] = []
    seen: set[str] = set()

    for match in _VENDOR_PATTERN.finditer(text):
        term = match.group(0)
        key = f"named_vendor:{term.lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            IncumbentEvidence(
                vendor_or_partner_term=term,
                category="named_vendor",
                source_url=source.url,
                source_title=source.title,
                snippet=_context_window(text, match.start(), match.end()),
            )
        )
        if len(out) >= max_items:
            return out

    for match in _INCUMBENT_LANGUAGE_PATTERN.finditer(text):
        key = f"incumbent_language:{match.group(0).lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            IncumbentEvidence(
                vendor_or_partner_term=match.group(0),
                category="incumbent_language",
                source_url=source.url,
                source_title=source.title,
                snippet=_context_window(text, match.start(), match.end()),
            )
        )
        if len(out) >= max_items:
            return out

    for match in _PARTNERSHIP_LANGUAGE_PATTERN.finditer(text):
        key = f"partnership_language:{match.group(0).lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            IncumbentEvidence(
                vendor_or_partner_term=match.group(0),
                category="partnership_language",
                source_url=source.url,
                source_title=source.title,
                snippet=_context_window(text, match.start(), match.end()),
            )
        )
        if len(out) >= max_items:
            return out

    return out


def _context_window(text: str, start: int, end: int, pad: int = 80) -> str:
    return text[max(0, start - pad): min(len(text), end + pad)].strip()


def detect_all_incumbent_evidence(sources: list[Source]) -> list[IncumbentEvidence]:
    out: list[IncumbentEvidence] = []
    for s in sources:
        out.extend(detect_incumbent_evidence(s))
    return out

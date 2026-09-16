"""Deterministic source modeling, deduplication and quality scoring.

Nothing here is organization-specific or sector-specific. Every function
takes an organization's name/country as plain data, never as a branch
condition (`if company_name == "..."` never appears anywhere in this
codebase -- see Phase 0 §25 / Phase 1B architecture constraints).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

from research.providers.base import RawResult


class AccessStatus(str, Enum):
    FETCHED = "fetched"                              # page body retrieved and usable
    INDEXED_SNIPPET_ONLY = "indexed_snippet_only"      # fetch blocked/login-walled; snippet retained
    UNREACHABLE = "unreachable"                         # fetch failed outright; snippet retained if any
    NOT_FETCHED = "not_fetched"                          # fetch was never attempted for this source
    NO_CONTENT = "no_content"                             # neither fetch nor snippet produced usable text


SOURCE_TYPE_OFFICIAL = "official"
SOURCE_TYPE_LINKEDIN_COMPANY = "linkedin_company"
SOURCE_TYPE_LINKEDIN_PROFILE = "linkedin_profile"
SOURCE_TYPE_NEWS = "news"
SOURCE_TYPE_PROCUREMENT = "procurement"
SOURCE_TYPE_TECHNOLOGY = "technology"
SOURCE_TYPE_BUSINESS = "business"
SOURCE_TYPE_OTHER = "other"

_SOCIAL_NOISE_HOSTS = (
    "google.", "bing.com", "duckduckgo.com", "yahoo.com",
    "facebook.com", "instagram.com", "x.com", "twitter.com",
    "youtube.com", "wikipedia.org", "tiktok.com",
)


@dataclass
class Source:
    url: str
    domain: str
    title: str
    source_type: str
    snippet: str
    discovered_via_query: str
    discovery_provider: str
    family: str                                     # which acquisition family found this (e.g. "linkedin_people")
    content: str | None = None
    access_status: AccessStatus = AccessStatus.NOT_FETCHED
    source_quality: float = 0.0
    content_hash: str | None = None
    extra: dict = field(default_factory=dict)         # e.g. {"linkedin_kind": "in" | "company"}

    @classmethod
    def from_raw(cls, raw: RawResult, *, source_type: str, family: str) -> "Source":
        url = _normalize_url(raw.url)
        return cls(
            url=url,
            domain=urlparse(url).netloc.lower(),
            title=raw.title,
            source_type=source_type,
            snippet=raw.snippet,
            discovered_via_query=raw.query,
            discovery_provider=raw.provider,
            family=family,
            extra={"published_at": raw.published_at} if raw.published_at else {},
        )


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    url = url.split("#", 1)[0]
    return url.rstrip("/")


def is_noise_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return not host or any(h in host for h in _SOCIAL_NOISE_HOSTS)


def dedupe_sources(sources: list[Source]) -> list[Source]:
    """Drop malformed URLs, then normalize and keep the highest-quality
    record per normalized URL.

    Pure function, no I/O, no organization-specific logic. A LinkedIn URL
    found by two different queries/providers collapses to one Source,
    retaining whichever discovery gave it richer metadata. This is also the
    single choke point every discovered source passes through before
    becoming part of the persisted corpus, so it is where a general
    malformed-URL guard belongs (the stricter, organization-identity-specific
    check lives in research.url_utils and is applied separately to the
    resolved website/LinkedIn-company fields).
    """
    from research.url_utils import is_valid_resolved_url

    best: dict[str, Source] = {}
    for s in sources:
        if not is_valid_resolved_url(s.url):
            continue
        key = s.url.lower()
        existing = best.get(key)
        if existing is None or s.source_quality > existing.source_quality:
            best[key] = s
        elif existing is not None and not existing.snippet and s.snippet:
            # Keep the higher-quality record but backfill a missing snippet
            # rather than discarding evidence outright.
            existing.snippet = s.snippet
    return list(best.values())


def score_source(source: Source) -> float:
    """Deterministic 0-100 quality score. No AI call, no per-organization
    branching -- only source_type / access_status / family / provider.
    """
    score = 40.0
    if source.source_type == SOURCE_TYPE_OFFICIAL:
        score += 30
    elif source.source_type == SOURCE_TYPE_LINKEDIN_COMPANY:
        score += 28
    elif source.source_type == SOURCE_TYPE_LINKEDIN_PROFILE:
        score += 20
    elif source.source_type in (SOURCE_TYPE_PROCUREMENT, SOURCE_TYPE_TECHNOLOGY):
        score += 16
    elif source.source_type == SOURCE_TYPE_BUSINESS:
        score += 12
    elif source.source_type == SOURCE_TYPE_NEWS:
        score += 8

    if source.access_status == AccessStatus.FETCHED:
        score += 15
    elif source.access_status == AccessStatus.INDEXED_SNIPPET_ONLY:
        score += 6  # evidence retained, but weaker than a real fetch
    elif source.access_status == AccessStatus.UNREACHABLE:
        score += 2

    if source.snippet:
        score += 5
    if source.discovery_provider == "serper":
        score += 2  # primary-provider results are marginally preferred at tie-break

    return max(0.0, min(100.0, score))


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def bucket_by_family(sources: list[Source]) -> dict[str, list[Source]]:
    buckets: dict[str, list[Source]] = {}
    for s in sources:
        buckets.setdefault(s.family, []).append(s)
    return buckets

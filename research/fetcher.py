"""Direct page fetching with explicit access-status classification.

This module never authenticates to any site and never bypasses an access
control. It attempts a plain, unauthenticated HTTP GET; when that is
blocked (a login wall, a bot challenge) the caller is told so explicitly
via AccessStatus rather than being handed login-wall HTML disguised as
content. This is the fix for a real Phase 0 finding: the previous
implementation accepted LinkedIn login-wall HTML as "content" whenever it
was merely long enough, which silently corrupted the evidence corpus.

A best-effort public-page reader (r.jina.ai) is used only as a secondary
attempt for LinkedIn URLs specifically, matching the boundary the original
codebase already drew correctly: this is not a login bypass, it is a
public-page reader service, and it is never required -- when it also fails,
the caller falls back to the search snippet already captured by the
acquisition layer, never to fabricated content.
"""

from __future__ import annotations

import re
import threading

import requests
from bs4 import BeautifulSoup

from research.sources import AccessStatus

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 Account-Radar/2.0"
    )
}
TIMEOUT = 8.0
MAX_CONTENT_CHARS = 16000
_PUBLISHED_AT_CACHE: dict[str, str] = {}
_PUBLISHED_AT_CACHE_LOCK = threading.Lock()

_LOGIN_WALL_SIGNATURES = (
    "sign in", "join now", "log in to see", "please log in",
    "this content isn't available", "you must log in", "join linkedin",
    "welcome back sign in",
)


def _looks_like_login_wall(text: str) -> bool:
    low = text.lower()
    hits = sum(1 for sig in _LOGIN_WALL_SIGNATURES if sig in low)
    # A single incidental match (e.g. a nav bar "Sign in" link on an
    # otherwise-real page) shouldn't condemn real content; require either a
    # strong repeated signal or a very short page dominated by wall text.
    return hits >= 2 or (hits >= 1 and len(text.strip()) < 600)


_BOILERPLATE_CLASS_RE = re.compile(
    r"(?:^|[-_\s])(nav|navigation|navbar|menu|mega-menu|breadcrumb|breadcrumbs|"
    r"header|footer|sidebar|side-bar|cookie|consent|social|share|search|"
    r"pagination|pager|related|recommend|newsletter|subscribe|popup|modal|"
    r"advert|ads|banner)(?:$|[-_\s])", re.I,
)

# Elements that are, or wrap, the page's main content are never removed as
# boilerplate, whatever their attributes say. Sites routinely put layout
# words on wrappers (WordPress <body class="has-sidebar header-fixed">, a
# <div class="article-header"> holding the H1), and removing such a wrapper
# would delete the very content the extractor exists to keep.
_PROTECTED_TAGS = frozenset({"html", "body", "main", "article"})
_PROTECTED_DESCENDANTS = ("main", "article", "h1")


def _attribute_text(tag) -> str:
    """id / class / role / aria-label as one space-separated string.
    BeautifulSoup returns multi-valued attributes such as class as a list;
    those are joined with spaces (str() of the list would yield
    "['footer', 'clearfix']", which no word boundary can match)."""
    parts: list[str] = []
    for attr in ("id", "class", "role", "aria-label"):
        value = tag.get(attr)
        if not value:
            continue
        if isinstance(value, (list, tuple)):
            parts.extend(str(v) for v in value if v)
        else:
            parts.append(str(value))
    return " ".join(parts)


def _is_protected_content_node(tag) -> bool:
    if tag.name in _PROTECTED_TAGS:
        return True
    if str(tag.get("role", "") or "").lower() == "main":
        return True
    return tag.find(_PROTECTED_DESCENDANTS) is not None or tag.find(attrs={"role": "main"}) is not None


def _is_boilerplate_node(tag) -> bool:
    if not getattr(tag, "name", None):
        return False
    is_boilerplate = bool(_BOILERPLATE_CLASS_RE.search(_attribute_text(tag))) or (
        str(tag.get("role", "") or "").lower() in {"navigation", "banner", "contentinfo", "complementary"}
    )
    return is_boilerplate and not _is_protected_content_node(tag)

def _extract_published_at(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").strip().lower()
        if prop in {"article:published_time", "datepublished", "datepublishedtime"}:
            value = (meta.get("content") or "").strip()
            if value:
                return value
    for node in soup.find_all(attrs={"itemprop": re.compile(r"^(datePublished|dateCreated)$", re.I)}):
        value = (node.get("content") or node.get("datetime") or node.get_text(" ", strip=True) or "").strip()
        if value:
            return value
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            import json
            payload = json.loads(raw)
        except Exception:
            continue
        def walk(obj):
            if isinstance(obj, dict):
                for key in ("datePublished", "dateCreated"):
                    value = obj.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                for value in obj.values():
                    found = walk(value)
                    if found:
                        return found
            elif isinstance(obj, list):
                for value in obj:
                    found = walk(value)
                    if found:
                        return found
            return None
        found = walk(payload)
        if found:
            return found
    return None

def _text_from_html(html: str, limit: int = MAX_CONTENT_CHARS) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template", "form", "nav", "footer", "header", "aside"]):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        # A tag inside an already-removed boilerplate block is gone with it.
        if getattr(tag, "decomposed", False):
            continue
        if _is_boilerplate_node(tag):
            tag.decompose()

    candidates = []
    for tag in soup.find_all(["article", "main"]):
        text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
        if len(text) >= 120:
            candidates.append(text)
    if candidates:
        text = max(candidates, key=len)
    else:
        blocks = []
        for tag in soup.find_all(["h1", "h2", "h3", "p", "li", "blockquote", "figcaption"]):
            value = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
            if len(value) >= 25:
                blocks.append(value)
        text = "\n\n".join(blocks)
    return _normalize_whitespace(text)[:limit]


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs to one space and never allow more than one
    blank line, while KEEPING the "\\n\\n" separators between paragraph and
    list blocks (downstream evidence logic relies on those boundaries)."""
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def published_at_for_url(url: str) -> str | None:
    with _PUBLISHED_AT_CACHE_LOCK:
        return _PUBLISHED_AT_CACHE.get(url)


def fetch_page(url: str, *, timeout: float = TIMEOUT) -> tuple[str, AccessStatus]:
    """Attempt a direct, unauthenticated fetch of `url`.

    Returns (content, access_status). content is "" when nothing usable
    was retrieved -- callers must fall back to the search snippet already
    captured at discovery time rather than treating "" as evidence.
    """
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    is_linkedin = host.endswith("linkedin.com")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.exceptions.RequestException:
        if is_linkedin:
            reader_content = _linkedin_reader_fallback(url)
            if reader_content:
                return reader_content, AccessStatus.INDEXED_SNIPPET_ONLY
        return "", AccessStatus.UNREACHABLE

    if not resp.ok:
        if is_linkedin:
            reader_content = _linkedin_reader_fallback(url)
            if reader_content:
                return reader_content, AccessStatus.INDEXED_SNIPPET_ONLY
        return "", AccessStatus.UNREACHABLE

    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype and "<html" not in resp.text.lower():
        return "", AccessStatus.NO_CONTENT

    text = _text_from_html(resp.text)
    published_at = _extract_published_at(resp.text)
    if published_at:
        with _PUBLISHED_AT_CACHE_LOCK:
            _PUBLISHED_AT_CACHE[url] = published_at

    if is_linkedin and _looks_like_login_wall(text):
        reader_content = _linkedin_reader_fallback(url)
        if reader_content and not _looks_like_login_wall(reader_content):
            return reader_content, AccessStatus.INDEXED_SNIPPET_ONLY
        # Retain nothing fabricated; caller falls back to the discovery
        # snippet. Explicitly mark this as a known login wall, not silence.
        return "", AccessStatus.INDEXED_SNIPPET_ONLY

    if len(text) < 30:
        return "", AccessStatus.NO_CONTENT

    return text, AccessStatus.FETCHED


def _linkedin_reader_fallback(url: str) -> str:
    """Best-effort public-page reader for LinkedIn URLs only.

    r.jina.ai is a public-page reader service; it does not authenticate to
    LinkedIn and cannot see anything a logged-out visitor couldn't. This is
    a secondary attempt, never a requirement -- failure here is normal and
    silent (caller falls back to the discovery snippet).
    """
    try:
        reader_url = "https://r.jina.ai/" + url
        resp = requests.get(reader_url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=12)
        if resp.ok and len(resp.text.strip()) > 80:
            return resp.text.strip()[:MAX_CONTENT_CHARS]
    except requests.exceptions.RequestException:
        pass
    return ""






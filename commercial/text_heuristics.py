"""Shared, generic text-shape heuristics used by both signal extraction
(commercial/signals.py) and capability mapping (commercial/evidence_mapping.py).

Evidence-relevance hardening (this module is new for that pass): a keyword
match can be structurally "real" (the regex found the word) while the text
it was found in is not actually a sentence describing what an organization
is doing -- e.g. an infographic caption listing technology category labels,
a page's navigation/"Read More" chrome, or a roundup article's concatenated
headlines. This module gives both callers one shared, disclosed heuristic
for recognizing that shape of text, rather than each guessing independently
or (worse) one of them being fixed and the other left exploitable by the
same false-positive pattern.

Deliberately generic and deterministic: no organization, sector, country,
or vendor name appears anywhere in this file -- only structural/lexical
shape (token density, punctuation, presence of a verb, common navigation/
biographical boilerplate phrasing found on essentially any web page,
regardless of topic).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Deliberately simple sentence boundary: end-of-sentence punctuation
# followed by whitespace and a capital/digit/quote, OR a paragraph break.
# Not real NLP sentence segmentation -- a disclosed, generic heuristic for
# "roughly one narrative unit," applied identically regardless of
# organization/sector/country. (Moved here, unchanged, from
# commercial/evidence_mapping.py so commercial/signals.py can share it.)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n\s*\n+")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split text into rough (start, end) sentence spans."""
    spans: list[tuple[int, int]] = []
    pos = 0
    for m in _SENTENCE_BOUNDARY_RE.finditer(text):
        if m.start() > pos:
            spans.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        spans.append((pos, len(text)))
    return spans


def span_containing(text: str, spans: list[tuple[int, int]], pos: int) -> str:
    """The text of whichever span in `spans` contains character offset
    `pos`, or a small local window as a fallback if no span matches
    (should not normally happen for a `pos` inside `text`)."""
    for s, e in spans:
        if s <= pos < e:
            return text[s:e]
    return text[max(0, pos - 80): min(len(text), pos + 80)]


# --- "looks like a category-label list / infographic caption, not a
# sentence" -----------------------------------------------------------
#
# e.g. "ENABLING TECHNOLOGIES IIOT Analytics Microchip Sensors Connectivity
# / IPC Cybersecurity 5G Digital Twin (Simulation) Industrial 3D Printing
# (Additive Manufacturing) AR and VR in Manufacturing" -- many short
# Title-Case/ALLCAPS technology-category tokens in a row, and no verb
# anywhere: nobody is described as DOING anything, it is a label taxonomy.
_LABEL_TOKEN_RE = re.compile(r"[A-Z][A-Za-z0-9/+\-]{1,20}")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/+\-]*")
_MIN_LABEL_TOKENS_FOR_LIST = 5
# A segment must be OVERWHELMINGLY made up of capitalized/label-shaped
# tokens (not merely contain a handful of them) to count as a list -- an
# ordinary sentence that happens to mention a multi-word proper noun (an
# organization name is very often 2-4 capitalized words) is not a list just
# because it contributes several capitalized tokens; it is still mostly
# ordinary lowercase connective prose. Guards against exactly that case
# (e.g. "Separately, in an unrelated announcement, Acme National Freight
# Authority confirmed a new investment..." must NOT be flagged just because
# the organization's own name is capitalized).
_LABEL_DENSITY_THRESHOLD = 0.7

_ACTION_VERB_HINTS_RE = re.compile(
    r"\b(is|are|was|were|has|have|had|will|would|can|could|should|announce[sd]?|launch(es|ed)?|"
    r"deploy(s|ed|ing)?|invest(s|ed|ing)?|partner(s|ed|ing)?|sign(s|ed)?|open(s|ed|ing)?|"
    r"expand(s|ed|ing)?|build(s)?|deliver(s|ed)?|implement(s|ed|ing)?|upgrad(e|es|ed|ing)?|"
    r"work(s|ed|ing)?|provide(s|d)?|offer(s|ed)?|plan(s|ned)?|commit(s|ted)?|help(s|ed)?|"
    r"support(s|ed)?|bring(s)?|introduc(e|es|ed)|enabl(e|es|ed)|driv(e|es|en)|lead(s)?|us(e|es|ed)|"
    r"need(s|ed)?|selects?|chose|chooses|awards?|said|says|saying|stated|states|stating|noted|"
    r"notes|noting|confirm(s|ed|ing)?|report(s|ed|ing)?|show(s|ed|ing)?|reach(es|ed|ing)?|"
    r"grow(s|ing)?|grew|includ(e|es|ed|ing)|became|becomes|becoming|remain(s|ed|ing)?)\b",
    re.I,
)


def looks_like_label_list(segment: str) -> bool:
    if not segment:
        return False
    words = _WORD_RE.findall(segment)
    label_tokens = _LABEL_TOKEN_RE.findall(segment)
    if len(label_tokens) < _MIN_LABEL_TOKENS_FOR_LIST or not words:
        return False
    if (len(label_tokens) / len(words)) < _LABEL_DENSITY_THRESHOLD:
        return False
    return _ACTION_VERB_HINTS_RE.search(segment) is None


# --- "looks like page navigation / aggregator chrome, not article prose"
# ------------------------------------------------------------------------
#
# e.g. "...Read More Review of the Strategy... Date Published : 16 April
# 2026 Read More With the participation of 66 companies.. Laun[ch]" --
# concatenated "Read More" / listing-widget boilerplate rather than a
# single narrative about one organization.
_NAV_AGGREGATOR_MARKERS_RE = re.compile(
    r"\b(read more|skip to (the )?content|related articles?|you may also like|date published|"
    r"share this (article|page|post)?|subscribe now|sign up (now|today)?|load more|"
    r"continue reading|back to (top|news|articles)|browse (all )?(news|articles|topics))\b",
    re.I,
)


def looks_like_navigation_or_aggregator_text(segment: str) -> bool:
    if not segment:
        return False
    return bool(_NAV_AGGREGATOR_MARKERS_RE.search(segment))


# --- "looks like a third-party executive biography/interview, not a
# business-event announcement" -------------------------------------------
#
# e.g. "...before joining Siemens. At Siemens AG, she is responsible for
# the company's digital transformation..." -- generic biographical-profile
# phrasing usable for any executive at any organization; a vague statement
# of someone's REMIT is not itself a concrete technology initiative.
_EXECUTIVE_BIO_MARKERS_RE = re.compile(
    r"\b(is responsible for|before joining|prior to joining|joined \w+ as|"
    r"in (her|his|their) role as|(her|his|their) (career|formula for|journey)|"
    r"holds a (degree|master'?s|bachelor'?s|mba|phd)|is a graduate of|"
    r"with over \d+ years?(\s+of)? experience|serves as (the )?(chief|head of|vp|vice president|director)|"
    r"brings? (her|his|their|over) .{0,30}years?)\b",
    re.I,
)


def executive_bio_marker_count(text: str) -> int:
    """Number of DISTINCT biographical-profile marker phrases found
    anywhere in `text`. Used at the whole-source level (not per-span) --
    a source containing several of these is treated as profile/interview
    content, the same way a LinkedIn personal profile already is.
    """
    return len({m.group(0).lower() for m in _EXECUTIVE_BIO_MARKERS_RE.finditer(text)})


# --- "looks like a customer-facing retail/product/pricing page, not the
# organization's own internal technology/expansion narrative" ------------
#
# e.g. a bank's "Growth Plan" savings account or "Expansion Finance" loan
# product page: product/marketing names and pricing copy can legitimately
# contain words like "expansion," "capacity," or "growth" without describing
# anything about the ORGANIZATION's own facilities or technology footprint.
# Detected generically from URL path shape and page-content characteristics
# common to retail/product/pricing pages on essentially any company's site
# -- never from any specific company, brand, or product name.
#
# Evidence-relevance hardening, follow-up pass (fixing the recommendation #5
# gap the follow-up audit found): the original version only matched an
# EXACT path segment ("/loans/"). Real retail-banking URLs very often
# encode the product name as a single HYPHENATED SLUG instead
# ("/business-expansion/business-instalment-loan") -- the marker word is a
# whole token within that slug, not its own directory level. Detection is
# now token-based: the path is split on any non-alphanumeric character and
# a marker word must match a WHOLE TOKEN (never a bare substring, so
# "loan" cannot match inside an unrelated word like "loaner" or "download"
# -- "download" tokenizes as "download", not "loan"). This alone is still
# NOT sufficient to classify a page as a product page (see
# `looks_like_customer_product_page` below) -- the existing dual
# requirement (path evidence AND >= 2 content markers) is unchanged, so a
# single incidental token match can never be sufficient by itself.
_PRODUCT_PAGE_MARKER_WORDS = frozenset({
    "product", "products", "pricing", "plan", "plans", "package", "packages",
    "card", "cards", "loan", "loans", "account", "accounts", "retail",
    "shop", "store", "offer", "offers",
})
# Multi-word marker phrases that don't reduce to a single token match.
_PRODUCT_PAGE_PATH_PHRASE_RE = re.compile(r"personal-banking|personal/banking", re.I)
_PATH_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _path_has_product_marker_token(path: str) -> bool:
    tokens = _PATH_TOKEN_RE.findall(path.lower())
    return any(t in _PRODUCT_PAGE_MARKER_WORDS for t in tokens)


_PRODUCT_PAGE_CONTENT_MARKERS_RE = re.compile(
    r"\b(apply now|learn more|terms and conditions apply|t&cs? apply|starting (at|from)|"
    r"per month|/mo\b|interest rate|annual percentage rate|\bapr\b|minimum balance|"
    r"eligibility criteria|add to cart|buy now|from only|compare plans|choose your plan|"
    r"monthly (fee|instal(l)?ment)|processing fee|redeem now)\b",
    re.I,
)
_MIN_PRODUCT_CONTENT_MARKERS = 2


def looks_like_customer_product_page(url: str, text: str) -> bool:
    """True when both the URL path and the page content carry the generic
    shape of a retail/product/pricing page -- requiring both, rather than
    either alone, keeps this from over-triggering on (for example) a news
    article that merely mentions a product name once, or a "/products/"
    URL that is actually an investor-relations product-roadmap
    announcement written in ordinary narrative prose. The path check now
    also matches a marker word appearing as a whole token inside a
    hyphenated slug (e.g. "/business-instalment-loan"), not only an exact
    "/loan/" directory segment -- but a path match alone is still never
    enough on its own: the >= 2 distinct content-marker requirement is
    unchanged, so a single generic word like "loan" appearing in a path
    can never by itself classify a page as a product page.
    """
    if not url or not text:
        return False
    try:
        path = urlparse(url).path or ""
    except ValueError:
        path = ""
    if not (_path_has_product_marker_token(path) or _PRODUCT_PAGE_PATH_PHRASE_RE.search(path)):
        return False
    content_hits = len(set(m.group(0).lower() for m in _PRODUCT_PAGE_CONTENT_MARKERS_RE.finditer(text)))
    return content_hits >= _MIN_PRODUCT_CONTENT_MARKERS


# --- "looks like a description of a THIRD PARTY's (the organization's own
# customer's) requirement, not the organization's own requirement" --------
#
# e.g. a vendor's press release describing what ITS PROSPECTIVE CUSTOMERS
# need at their own sites ("close to gas, water and fiber") is evidence
# about the customer's siting criteria, not a statement that the
# publishing organization itself has a connectivity requirement. Detected
# from generic customer-facing linguistic constructions -- never from any
# specific company, sector, or geography.
_CUSTOMER_REQUIREMENT_MARKERS_RE = re.compile(
    r"\b(prospective customers?|(its|their|our|your) customers?|end customers?|end users?|"
    r"customers?(?:'|’)?s?\s+(own\s+)?(sites?|facilit(y|ies)|infrastructure|needs?|"
    r"requirements?|deployments?|use cases?)|"
    r"customer (sites?|requirements?|needs?|deployments?|infrastructure|use cases?)|"
    r"siting criteria|"
    r"for (its|their|our|your|prospective) (sites?|facilit(y|ies)|operations?)|"
    r"available (to|for) customers?|offered to customers?)\b",
    re.I,
)


def looks_like_customer_requirement_span(segment: str) -> bool:
    """True when a local span (roughly one sentence) uses generic
    customer-facing/third-party language -- describing what a CUSTOMER
    needs or has, rather than what the publishing organization itself
    needs or has. Applied span-locally (like `is_low_trust_span`), not at
    the whole-source level: a source may legitimately mention "customers"
    in one place while genuinely describing its own technology elsewhere,
    and only the specific local context around a matched term should be
    disqualified, not the entire source.
    """
    if not segment:
        return False
    return bool(_CUSTOMER_REQUIREMENT_MARKERS_RE.search(segment))


# --- "looks like a collective/aggregate-scope statement about an entire
# government, sector, or industry -- not a claim specifically about the
# researched organization" -----------------------------------------------
#
# Evidence-relevance hardening, false-positive-class fix (Oman Airports
# live-validation finding, 2026-09): an organization can be genuinely,
# locally co-located with capability-specific vocabulary and a qualifying
# business signal, and still not be the actual SUBJECT of that claim --
# e.g. "Oman Airports is among the government entities progressing
# digital transformation, which spans cloud, cybersecurity and managed
# services investments across the sector." The organization is named only
# as one member of a collective (the whole of government, a sector, an
# industry); the claim itself is about the collective, not the
# organization. A source can also independently satisfy this the way the
# real Oman Airports source did: a genuinely organization-specific
# sentence elsewhere in the article, plus a SEPARATE, collective-scope
# sentence ("...a cloud project that aims to create a shared
# infrastructure network across the whole of government...") that must
# not be allowed to ground organization-specific capability evidence on
# its own.
#
# This is a narrow, precision-first marker set covering the specific
# confirmed failure shape -- deliberately NOT a broad "the organization
# is mentioned alongside others" detector, and deliberately NOT keyed off
# any bare occurrence of "other", "several", "entities", "government",
# "sector", or "industry" by themselves. Ordinary peer-enumeration
# language where the organization still makes its own specific claim
# must NOT match -- e.g. "Standard Chartered, along with several other
# banks, also announced its own new AI-driven fraud detection platform"
# names no government/state/sector/industry-wide scope, so it is
# deliberately left untouched; likewise "Oman Airports and other
# government entities announced separate projects" does not by itself
# use any of the reviewed constructions below (no "among the government
# entities" / "several other government entities" / "-wide" / "across
# the government|sector|industry" phrasing), so it does not disqualify
# a genuinely organization-specific sentence elsewhere in the source.
_COLLECTIVE_SCOPE_MARKERS_RE = re.compile(
    r"\bacross (the )?(whole of )?government\b"
    r"|\bgovernment[- ]wide\b"
    r"|\bacross (the )?([a-z]+(?:-[a-z]+)?\s+)?sector\b"
    r"|\bsector[- ]wide\b"
    r"|\bacross (the )?industry\b"
    r"|\bindustry[- ]wide\b"
    r"|\bamong (the )?(government|state|public[- ]sector) (entities|agencies|organi[sz]ations|bodies)\b"
    r"|\bseveral other (government|state|public[- ]sector) (entities|agencies|organi[sz]ations|bodies)\b",
    re.I,
)


def _is_collective_scope_span(segment: str) -> bool:
    """True when a local span (roughly one sentence) describes a
    collective/aggregate scope -- an entire government, sector, or
    industry -- rather than making a claim specifically about the
    researched organization. Applied span-locally, exactly like
    `looks_like_customer_requirement_span`: only the specific sentence
    that reads as collective-scope is disqualified from GROUNDING
    organization-association there, not the whole source -- an
    organization-specific claim made in a different sentence of the same
    article (even one that also happens to mention government/sector
    language elsewhere) remains eligible.
    """
    if not segment:
        return False
    return bool(_COLLECTIVE_SCOPE_MARKERS_RE.search(segment))


def is_low_trust_span(segment: str) -> bool:
    """A local span (roughly one sentence) that should NOT be treated as
    genuine organizational narrative for the purpose of matching a
    business signal or a capability term to it -- either because it reads
    as a category-label list/infographic caption, or because it reads as
    page navigation/aggregator chrome. (Executive-biography detection is
    deliberately NOT folded in here -- see `executive_bio_marker_count`;
    that one is applied at the whole-source level because a single stray
    biographical phrase in an otherwise-substantive sentence should not by
    itself disqualify that sentence.)
    """
    return looks_like_label_list(segment) or looks_like_navigation_or_aggregator_text(segment)


def clean_evidence_excerpt(text: str, *, matched_terms: list[str] | None = None, max_chars: int = 900) -> str:
    """Return a human-reviewable excerpt or empty string.

    This is deliberately conservative. It removes obvious navigation/list
    chrome and refuses text that is dominated by generic website labels.
    It never rewrites or summarizes source text.
    """
    raw = re.sub(r"\s+", " ", (text or "")).strip()
    if not raw:
        return ""
    low = raw.lower()
    boilerplate_markers = (
        "skip to content", "skip to main content", "all rights reserved",
        "advertising marketing services", "marketplace store market sector",
        "services services", "menu close search", "cookie policy",
    )
    marker_hits = sum(1 for m in boilerplate_markers if m in low)
    if marker_hits >= 1:
        # If a useful sentence exists after obvious chrome, keep only the
        # non-boilerplate sentence rather than displaying the chrome.
        parts = re.split(r"(?<=[.!?])\s+", raw)
        useful = [
            p.strip() for p in parts
            if p.strip() and not any(m in p.lower() for m in boilerplate_markers)
        ]
        raw = " ".join(useful).strip()
        if not raw:
            return ""
    # Navigation-like text is usually a very high ratio of short labels.
    tokens = raw.split()
    if len(tokens) >= 12:
        short_ratio = sum(len(t.strip(".,:;()[]")) <= 3 for t in tokens) / len(tokens)
        if short_ratio > 0.42 and not re.search(r"[.!?]", raw):
            return ""
    # A long run of title-case labels separated without sentence punctuation
    # is generally navigation/category chrome, not evidence.
    if len(tokens) >= 16 and not re.search(r"[.!?]", raw):
        titleish = sum(t[:1].isupper() for t in tokens if t)
        if titleish / len(tokens) > 0.55:
            return ""
    return raw[:max_chars].rstrip()

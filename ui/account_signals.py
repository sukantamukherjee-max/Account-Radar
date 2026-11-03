"""Normalized account-signal layer -- the single source of truth for which
report signals are ACCOUNT-SPECIFIC developments and how they rank.

Both the Executive Account Summary and UNDERSTAND read this layer; neither
reads the other. The layer is deterministic presentation logic over an
existing report dict (AccountRadarReport.to_dict()):

  * it never runs research, calls an AI model, or calls external services;
  * it never changes an evidence level, category, confidence or epistemic
    status -- it only selects, classifies and ranks signals already present;
  * industry context, directory boilerplate, page chrome, personal LinkedIn
    profiles and incidental mentions never become account developments.

Provenance tiers produced here:

  account signals  -- tied to the account by the evidence chain (direct /
                      strong mapping), or account-specific by the rules in
                      `normalize_account_signals`; classed technology /
                      procurement > strategic > general; page chrome,
                      directory and static profile statements are kept apart
                      as BACKGROUND.
  reported news    -- a news item on a site that is NOT the account's own
                      (for example a parent or group company's site, or a
                      third-party publisher) in which the account is the
                      subject of the headline ("<Account> signs ...").
                      It may inform strategic understanding but is never
                      direct account evidence and never ranks alongside the
                      account signals above (the Executive Summary ignores
                      this tier).
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import urlparse

import commercial.evidence_mapping as _evidence_layer
from commercial.capabilities import get_catalog
from commercial.evidence_mapping import (
    _is_generic_industry_content,
    _match_terms,
    _mentions_organization,
    _org_core_tokens,
    organization_aliases,
)

SNIPPET_CHARS = 170

DIRECT = "direct_evidence"
STRONG = "strong_inference"
WEAK = "weak_relevance"
CONFIRMED = "confirmed_evidence"
INFERENCE = "inference_hypothesis"

# Evidence categories that describe the ACCOUNT's own event -- the only
# categories that may confirm an opportunity (mirrors the frozen
# commercial/opportunities.py guard).
ACCOUNT_EVENT_CATEGORIES = {"organizational_technology_event", "procurement_signal"}
# Categories that are context, never account evidence.
CONTEXT_CATEGORIES = {
    "industry_context", "current_state_technology", "stakeholder_context", "hiring_intent_context",
    "incidental_mention", "customer_requirement_context", "existing_capability_context",
}
# Mapping categories that make a SOURCE background rather than account
# evidence when every mapping from that source carries one of them.
BACKGROUND_SOURCE_CATEGORIES = {
    "industry_context", "current_state_technology", "stakeholder_context", "hiring_intent_context",
}

SIGNAL_LABELS = {
    "capacity_change": "capacity change",
    "contract_award": "contract award",
    "expansion": "expansion",
    "investment": "investment",
    "leadership_change": "leadership change",
    "partnership": "partnership",
    "procurement_activity": "procurement activity",
    "project": "project",
    "technology_initiative": "technology initiative",
}
SIGNAL_TYPE_PRIORITY = {
    "procurement_activity": 9, "technology_initiative": 8, "contract_award": 7, "project": 6,
    "capacity_change": 5, "expansion": 4, "investment": 3, "partnership": 2, "leadership_change": 1,
}

TECHNOLOGY_PROCUREMENT_TYPES = {"technology_initiative", "procurement_activity", "contract_award", "capacity_change", "project"}
PROCUREMENT_TYPES = {"procurement_activity", "contract_award"}
CLASS_LABELS = {
    "technology_procurement": "Technology / procurement",
    "strategic": "Strategic signal",
    "general": "General activity",
    "background": "Background",
}
CLASS_RANK = {"technology_procurement": 3, "strategic": 2, "general": 1, "background": 0}

REPORTED_NEWS = "reported_news"
REPORTED_NEWS_LABEL = "Reported news"

# Invisible / bidi control characters that LinkedIn and RTL pages embed in
# names and titles (LRM/RLM, embeddings, isolates, zero-width, BOM).
_INVISIBLE_RE = re.compile("[​-‏‪-‮⁠-⁩﻿]")


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

# Two independent dimensions of every development (never conflated):
#   account relationship -- how the source relates to the account;
#   evidence strength    -- what the evidence chain says about an Otech
#                           capability (the frozen evidence layer's levels).
ACCOUNT_SPECIFIC = "account_specific"
RELATIONSHIP_LABELS = {
    ACCOUNT_SPECIFIC: "Account-specific",
    "stakeholder_reported": "Stakeholder-reported",
    "reported_news": "Reported news",
    "context": "Industry / context",
}
NO_CAPABILITY_LINK = "no_capability_link"
EVIDENCE_LABELS = {
    DIRECT: "Direct evidence",
    STRONG: "Strong inference",
    WEAK: "Weak relevance",
    NO_CAPABILITY_LINK: "No capability link",
}
TECHNOLOGY_RELATED_TAG = "Technology-related"
# Signal types that describe technology activity. When such a signal is
# merged into an event whose primary class is not technology / procurement
# (no specific technology, no procurement), it becomes a TAG, not a class.
TECHNOLOGY_TYPES = {"technology_initiative", "project", "capacity_change"}
# Two signals from the same source describe the same underlying event when
# their source windows share at least this many characters verbatim.
EVENT_OVERLAP_CHARS = 40


# An explicit, named partnership in the development's own text ("Strategic
# Partnership with Bank Muscat", "signs an agreement with Acme Corp"). The
# counterparty must be a capitalized name; stop words end it. Verbatim only.
_COUNTERPARTY_RE = re.compile(
    r"\b(?i:partnership|agreement|memorandum of understanding|mou|collaboration)\s+(?i:with)\s+"
    r"((?:[A-Z][\w&'.-]*)(?:\s+(?!(?:To|For|And|On|In|Of|At|By|The|A|An|With|From|As)\b)[A-Z][\w&'.-]*){0,4})"
)


def explicit_counterparty(text: str, organization_name: str = "", country: str | None = None) -> str:
    m = _COUNTERPARTY_RE.search(text or "")
    if not m:
        return ""
    name = m.group(1).strip(" .,'")
    if organization_name:
        aliases = {a.lower() for a in organization_aliases(organization_name, country=country)}
        if name.lower() in aliases:
            return ""
    return name


@dataclass
class KeySignal:
    """One ACCOUNT DEVELOPMENT: an underlying event, possibly detected as
    several signal types. `signal_type`/`label` are its primary type;
    `signal_types`/`tags` list every classification attached to it."""
    signal_type: str
    label: str
    excerpt: str
    source_title: str
    source_url: str
    strength: str          # legacy combined wording: "confirmed evidence" | "inference" | "account-specific" | "reported news"
    strength_rank: int
    signal_class: str = "strategic"            # "technology_procurement" | "strategic" | "general" | "background"
    class_label: str = "Strategic signal"
    specific_technology: bool = False          # the excerpt names specific catalogue technology terms
    provenance: str = "account"                # "account" | "reported_news"
    raw_text: str = ""                         # the signal's full source window (for deterministic checks)
    signal_types: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    relationship: str = ACCOUNT_SPECIFIC
    relationship_label: str = "Account-specific"
    evidence_level: str = NO_CAPABILITY_LINK
    evidence_label: str = "No capability link"


@dataclass
class AccountSignalSet:
    ranked: list[KeySignal] = field(default_factory=list)          # account developments (deduplicated events), ranked
    meaningful_count: int = 0                                       # == len(ranked): the ONE shared development count
    background: list[KeySignal] = field(default_factory=list)      # account-linked page chrome / directory / profile
    background_count: int = 0
    reported_news: list[KeySignal] = field(default_factory=list)   # non-account-site news about the account

    @property
    def development_count(self) -> int:
        return len(self.ranked)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def strip_invisible(text: str | None) -> str:
    return _INVISIBLE_RE.sub("", text or "")


def domain(url: str | None) -> str:
    try:
        host = urlparse(url or "").netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def clean_excerpt(text: str | None, max_chars: int = SNIPPET_CHARS) -> str:
    """Trim a fixed-width source window to whole words, verbatim otherwise."""
    t = re.sub(r"https?://\S+", " ", text or "")  # bare links add nothing to an excerpt
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    starts_mid_word = t[0].islower()
    if starts_mid_word and " " in t:
        t = t.split(" ", 1)[1]
    clipped = len(t) > max_chars
    if clipped:
        t = t[:max_chars].rsplit(" ", 1)[0]
    t = t.strip(" ,;:-")
    return ("…" if starts_mid_word else "") + t + ("…" if clipped else "")


def is_account_site(url: str | None, website: str | None) -> bool:
    """True when `url` is on the account's own website host or one of its
    subdomains. A host-suffix check, not a registrable-domain check, so
    unrelated sites that merely share a public suffix never count."""
    base = domain(website)
    host = domain(url)
    return bool(base and host and (host == base or host.endswith("." + base)))


def is_linkedin_profile(url: str | None) -> bool:
    host = domain(url)
    try:
        path = urlparse(url or "").path
    except ValueError:
        return False
    return (host == "linkedin.com" or host.endswith(".linkedin.com")) and path.startswith("/in/")


# ---------------------------------------------------------------------------
# Evidence-layer interfaces (read-only)
# ---------------------------------------------------------------------------

GenericCheck = Callable[[str, str, str, "str | None"], bool]


def _accepts_keyword(func: Any, name: str) -> bool:
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def call_generic_industry_check(func: Any, url: str, title: str, org: str, country: str | None) -> bool:
    """Call the Evidence Intelligence layer's generic-industry rule with
    exactly the arguments its signature accepts (current: account context
    keywords; earlier installs: the source alone)."""
    source = SimpleNamespace(url=url, title=title)
    if _accepts_keyword(func, "organization_name") and _accepts_keyword(func, "country"):
        return bool(func(source, organization_name=org, country=country))
    return bool(func(source))


def generic_industry_source(url: str, title: str, org: str, country: str | None) -> bool:
    return call_generic_industry_check(_is_generic_industry_content, url, title, org, country)


def chain_by_url(report: dict[str, Any]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in report.get("chain") or []:
        out.setdefault(row.get("source_url") or "", []).append(row)
    return out


def is_context_source(
    url: str, title: str, by_url: dict[str, list[dict]], org: str, country: str | None,
    *, generic_check: GenericCheck | None = None,
) -> bool:
    check = generic_check or generic_industry_source
    if check(url, title, org, country):
        return True
    rows = by_url.get(url) or []
    return bool(rows) and all(r.get("evidence_category") in BACKGROUND_SOURCE_CATEGORIES for r in rows)


def is_directory_page(url: str, title: str) -> bool:
    """Directory / data-provider profile pages (employee directories,
    "Products, Competitors, Financials" pages): the evidence layer's own
    rule, plus the same title markers used for background signals."""
    func = getattr(_evidence_layer, "_is_directory_profile_page", None)
    if func is not None and func(SimpleNamespace(url=url, title=title)):
        return True
    return bool(DIRECTORY_TITLE_RE.search(title or ""))


def title_establishes_account_context(title: str, text: str, org: str, country: str | None) -> bool:
    func = getattr(_evidence_layer, "title_establishes_account_context", None)
    if func is None:  # an earlier evidence layer without the title rule
        return False
    return bool(func(title, text, org, country=country))


def mentions_account(text: str, org: str, country: str | None) -> bool:
    if not org or not text:
        return False
    return _mentions_organization(text, org, _org_core_tokens(org, country=country), country=country)


# ---------------------------------------------------------------------------
# Signal classes
# ---------------------------------------------------------------------------
#   technology_procurement -- a procurement or contract award, or a
#                             technology/project/capacity event that names
#                             specific technology terms;
#   strategic              -- leadership, ownership/investment, expansion or
#                             partnership;
#   general                -- a technology/project-type event naming no
#                             specific technology;
#   background             -- page chrome, directory / data-provider
#                             boilerplate and static company-profile
#                             statements: counted, never presented as
#                             meaningful account signals.
# All markers are generic English page/listing phrasing.

PAGE_CHROME_RE = re.compile(
    r"\b(related (content|articles?|stories|news|posts|reading|coverage)|you may also like|recommended (for you|reading|articles?|stories)|"
    r"more from|most (read|popular|viewed)|trending( now)?|read (more|next)|also read|see also|top stories)\b",
    re.I,
)
DIRECTORY_TITLE_RE = re.compile(
    r"\b(products,? competitors|competitors|alternatives|employee directory|headcount|company profile|org chart|"
    r"financials|contact (information|details)|funding|investors)\b",
    re.I,
)
DIRECTORY_TEXT_RE = re.compile(
    r"\b(what is|when was|where is|who (are|is)|how many)\b[^?]{1,90}\?|\b(latest )?funding rounds?\b|\binvestors of\b|"
    r"\bheadcount\b|\bemployee directory\b",
    re.I,
)
PROFILE_STATEMENT_RE = re.compile(
    r"\bis an? (?:[\w-]+ ){0,3}(company|organi[sz]ation|corporation|entity|enterprise|authority|operator)\b|"
    r"\b(?:was )?(established|founded|incorporated) in \d{4}\b|\bheadquartered in\b|\bis a subsidiary of\b|"
    r"\bwholly[- ]owned\b|\baffiliated with\b",
    re.I,
)


def classify_signal(kind: str, text: str, title: str, *, specific_technology: bool = False) -> str:
    """technology_procurement | strategic | general | background (see above)."""
    if PAGE_CHROME_RE.search(text or "") or DIRECTORY_TITLE_RE.search(title or "") or DIRECTORY_TEXT_RE.search(text or ""):
        return "background"
    if kind in PROCUREMENT_TYPES or (kind in TECHNOLOGY_PROCUREMENT_TYPES and specific_technology):
        return "technology_procurement"
    if kind in TECHNOLOGY_PROCUREMENT_TYPES:
        return "general"
    if PROFILE_STATEMENT_RE.search(text or ""):
        return "background"
    return "strategic"


def specific_technology_terms(text: str) -> list[str]:
    """Specific catalogue technology terms (including the sector vocabulary
    packs merged into the catalogue) present in `text`."""
    found: list[str] = []
    for capability in get_catalog():
        found.extend(t for t in _match_terms(text or "", capability["keywords"]) if t not in found)
    return found


# ---------------------------------------------------------------------------
# Reported news tier
# ---------------------------------------------------------------------------

# Headline verbs: the account is the SUBJECT of a reported news item
# ("<Account> signs ...", "<Account> has announced ..."). Generic English.
_NEWS_VERBS = (
    r"(?i:signs?|signed|announces?|announced|appoints?|appointed|launch(?:es|ed)?|awards?|awarded|"
    r"partners?|partnered|selects?|selected|implements?|implemented|opens?|opened|completes?|completed|"
    r"inaugurates?|inaugurated|deploys?|deployed|introduces?|introduced|unveils?|unveiled|wins?|won|"
    r"receives?|received|agrees?|agreed|expands?|expanded|invests?|invested|names?|named)"
)


def _account_headline(text: str, org: str, country: str | None) -> bool:
    for alias in organization_aliases(org, country=country):
        if len(alias) < 3:
            continue
        pattern = r"(?<![A-Za-z0-9])" + re.escape(alias) + r"\s+(?i:has\s+|have\s+)?" + _NEWS_VERBS + r"\b"
        if re.search(pattern, text or ""):
            return True
    return False


# Tender / bid aggregator pages: their rows can belong to any buyer, so a
# headline-shaped row on such a page is never reported news about the account.
_TENDER_LISTING_TITLE_RE = re.compile(r"\b(tenders?|bids?|contracts|rfps?|rfqs?|procurement notices?)\b", re.I)


def _is_listing(title: str, text: str) -> bool:
    looks_row = getattr(_evidence_layer, "looks_like_listing_row", None)
    if looks_row is not None and looks_row(text):
        return True
    return bool(_TENDER_LISTING_TITLE_RE.search(title or ""))


def is_reported_news(kind: str, text: str, title: str, url: str, org: str, country: str | None, website: str | None) -> bool:
    """A non-account-site item in which the account is the subject of a news
    headline. Never a listing row, never page chrome / directory content."""
    if not org or is_account_site(url, website) or is_linkedin_profile(url):
        return False
    if kind not in SIGNAL_LABELS:
        return False
    if _is_listing(title, text) or classify_signal(kind, text, title) == "background":
        return False
    return _account_headline(text, org, country)


# ---------------------------------------------------------------------------
# Normalization + ranking
# ---------------------------------------------------------------------------

def normalize_account_signals(report: dict[str, Any], *, generic_check: GenericCheck | None = None) -> AccountSignalSet:
    """Classify every report signal and rank the account-specific ones.

    A signal is an ACCOUNT signal when it is a known business signal type,
    does not come from industry/context content or a personal LinkedIn
    profile, and is tied to the account by at least one of:
      3 -- it backs a direct_evidence mapping (confirmed evidence);
      2 -- it backs a strong_inference mapping;
      1 -- its own text names the account (never only as an example) AND the
           source is the account's own website or is titled after the
           account; OR it is a technology/procurement event naming specific
           technology terms on the account's own site, or on a single-item
           page whose title names the account (never a listing page).
    Account signals are classed (see `classify_signal`); background items
    are kept apart. Remaining signals that report the account as the subject
    of a news headline on a non-account site form the REPORTED NEWS tier.
    """
    org = report.get("organization_name") or ""
    country = report.get("country")
    tokens = _org_core_tokens(org, country=country) if org else []
    website = report.get("website")
    by_url = chain_by_url(report)

    candidates: list[KeySignal] = []
    reported: list[KeySignal] = []
    for s in report.get("signals") or []:
        kind = s.get("signal_type") or "other"
        url = s.get("source_url") or ""
        title = s.get("source_title") or ""
        if kind not in SIGNAL_LABELS or not url or is_linkedin_profile(url):
            continue
        if is_context_source(url, title, by_url, org, country, generic_check=generic_check):
            continue
        backing = [
            r for r in by_url.get(url, [])
            if (r.get("customer_signal") or {}).get("signal_type") == kind
            and r.get("evidence_category") not in CONTEXT_CATEGORIES
        ]
        levels = {r.get("evidence_level") for r in backing}
        text = s.get("text") or ""
        specific_terms = specific_technology_terms(text)
        own_site = is_account_site(url, website)
        if DIRECT in levels:
            rank, strength = 3, "confirmed evidence"
        elif STRONG in levels:
            rank, strength = 2, "inference"
        elif org and _mentions_organization(text, org, tokens, country=country) and (
            own_site or _mentions_organization(title, org, tokens, country=country)
        ):
            rank, strength = 1, "account-specific"
        elif org and kind in TECHNOLOGY_PROCUREMENT_TYPES and specific_terms and (
            own_site or title_establishes_account_context(title, text, org, country)
        ):
            rank, strength = 1, "account-specific"
        else:
            if is_reported_news(kind, text, title, url, org, country, website):
                signal_class = classify_signal(kind, text, title, specific_technology=bool(specific_terms))
                reported.append(KeySignal(
                    signal_type=kind, label=SIGNAL_LABELS[kind], excerpt=_headline_excerpt(text, org, country),
                    source_title=title, source_url=url, strength="reported news", strength_rank=0,
                    signal_class=signal_class, class_label=CLASS_LABELS[signal_class],
                    specific_technology=bool(specific_terms), provenance=REPORTED_NEWS, raw_text=text,
                    relationship=REPORTED_NEWS, relationship_label=RELATIONSHIP_LABELS[REPORTED_NEWS],
                    evidence_level=_source_evidence_level(url, by_url, 0),
                ))
            continue
        signal_class = classify_signal(kind, text, title, specific_technology=bool(specific_terms))
        candidates.append(KeySignal(
            signal_type=kind, label=SIGNAL_LABELS[kind], excerpt=clean_excerpt(text),
            source_title=title, source_url=url, strength=strength, strength_rank=rank,
            signal_class=signal_class, class_label=CLASS_LABELS[signal_class],
            specific_technology=bool(specific_terms), raw_text=text,
            evidence_level=_source_evidence_level(url, by_url, rank),
        ))

    background = merge_events([k for k in candidates if k.signal_class == "background"])
    meaningful = merge_events([k for k in candidates if k.signal_class != "background"])
    # A reported-news item that is the same event as an account development
    # (same source, overlapping text) is not shown a second time.
    reported = [
        r for r in merge_events(reported)
        if not any(r.source_url == m.source_url and _same_event_text(r.raw_text, m.raw_text) for m in meaningful)
    ]
    meaningful.sort(key=_rank_key)
    return AccountSignalSet(
        ranked=meaningful,
        meaningful_count=len(meaningful),
        background=background,
        background_count=len(background),
        reported_news=reported,
    )


def _rank_key(k: KeySignal) -> tuple:
    return (-k.strength_rank, -CLASS_RANK[k.signal_class], -int(k.specific_technology),
            -SIGNAL_TYPE_PRIORITY.get(k.signal_type, 0))


def _source_evidence_level(url: str, by_url: dict[str, list[dict]], rank: int) -> str:
    """The evidence-strength dimension, read from the chain as-is."""
    if rank == 3:
        return DIRECT
    if rank == 2:
        return STRONG
    levels = {r.get("evidence_level") for r in by_url.get(url, [])}
    # A rank-1 / reported item never backs a direct or strong mapping of its
    # own type; any capability mapping from its source is weak relevance at
    # most for this development.
    return WEAK if levels else NO_CAPABILITY_LINK


def _same_event_text(a: str, b: str) -> bool:
    a, b = re.sub(r"\s+", " ", a or "").strip(), re.sub(r"\s+", " ", b or "").strip()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    from difflib import SequenceMatcher

    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size >= EVENT_OVERLAP_CHARS


def _same_event(a: KeySignal, b: KeySignal) -> bool:
    if a.excerpt and a.excerpt.lower() == b.excerpt.lower():
        return True              # identical excerpt (any source) -- one development
    if a.source_url != b.source_url:
        return False
    return a.signal_type == b.signal_type or _same_event_text(a.raw_text, b.raw_text)


def merge_events(items: list[KeySignal]) -> list[KeySignal]:
    """Group signals into underlying events and build ONE development per
    event. Classification of the event:
      * technology_procurement only if a member is (a procurement / contract
        award, or a technology event naming specific technology);
      * otherwise strategic if any member is strategic -- a technology-type
        member then becomes the "Technology-related" TAG;
      * otherwise general (or background, for background-only groups).
    Strength is the strongest member's; nothing is upgraded."""
    groups: list[list[KeySignal]] = []
    for k in sorted(items, key=_rank_key):
        for g in groups:
            if any(_same_event(k, m) for m in g):
                g.append(k)
                break
        else:
            groups.append([k])
    return [_event_from(g) for g in groups]


def _event_from(members: list[KeySignal]) -> KeySignal:
    classes = {m.signal_class for m in members}
    # A named partnership stated in the item's own text is a strategic
    # aspect of the event even when the extractor typed the window only as
    # a technology initiative. It never creates technology / procurement.
    if (
        "technology_procurement" not in classes and "strategic" not in classes and "background" not in classes
        and not any(m.signal_type == "partnership" for m in members)
        and any(explicit_counterparty(m.excerpt) for m in members)
    ):
        src = next(m for m in members if explicit_counterparty(m.excerpt))
        members = members + [KeySignal(
            signal_type="partnership", label=SIGNAL_LABELS["partnership"], excerpt=src.excerpt,
            source_title=src.source_title, source_url=src.source_url, strength=src.strength,
            strength_rank=src.strength_rank, signal_class="strategic", class_label=CLASS_LABELS["strategic"],
            provenance=src.provenance, raw_text=src.raw_text, relationship=src.relationship,
            evidence_level=src.evidence_level,
        )]
        classes.add("strategic")
    if "technology_procurement" in classes:
        event_class = "technology_procurement"
    elif "strategic" in classes:
        event_class = "strategic"
    elif "general" in classes:
        event_class = "general"
    else:
        event_class = "background"
    best = min(members, key=_rank_key)
    primary = min((m for m in members if m.signal_class == event_class), key=_rank_key, default=best)
    types: list[str] = []
    for m in sorted(members, key=lambda m: -SIGNAL_TYPE_PRIORITY.get(m.signal_type, 0)):
        if m.signal_type not in types:
            types.append(m.signal_type)
    ordered = [primary.signal_type] + [t for t in types if t != primary.signal_type]
    tags = [SIGNAL_LABELS[primary.signal_type].capitalize()]
    for t in ordered[1:]:
        if event_class != "technology_procurement" and t in TECHNOLOGY_TYPES:
            # Technology wording on a non-technology event is a tag, not a
            # second classification ("Technology-related").
            if primary.signal_type not in TECHNOLOGY_TYPES and TECHNOLOGY_RELATED_TAG not in tags:
                tags.append(TECHNOLOGY_RELATED_TAG)
        elif SIGNAL_LABELS[t].capitalize() not in tags:
            tags.append(SIGNAL_LABELS[t].capitalize())
    order = {DIRECT: 3, STRONG: 2, WEAK: 1, NO_CAPABILITY_LINK: 0}
    level = max((m.evidence_level for m in members), key=lambda lv: order.get(lv, 0))
    return KeySignal(
        signal_type=primary.signal_type, label=SIGNAL_LABELS[primary.signal_type], excerpt=primary.excerpt,
        source_title=primary.source_title, source_url=primary.source_url,
        strength=best.strength, strength_rank=best.strength_rank,
        signal_class=event_class, class_label=CLASS_LABELS[event_class],
        specific_technology=any(m.specific_technology for m in members),
        provenance=primary.provenance, raw_text=primary.raw_text,
        signal_types=ordered, tags=tags,
        relationship=primary.relationship, relationship_label=RELATIONSHIP_LABELS.get(primary.relationship, ""),
        evidence_level=level, evidence_label=EVIDENCE_LABELS[level],
    )


def _headline_excerpt(text: str, org: str, country: str | None) -> str:
    """For reported news, start the excerpt at the account headline so the
    reader sees the reported item rather than the surrounding page window."""
    for alias in organization_aliases(org, country=country):
        m = re.search(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"\s+(?i:has\s+|have\s+)?" + _NEWS_VERBS + r"\b", text or "")
        if m:
            item = text[m.start():]
            # A news strip runs straight into the next item's date stamp
            # ("... Payment System 01 Dec At a Glance"); stop there.
            stop = _NEXT_ITEM_DATE_RE.search(item, m.end() - m.start())
            return clean_excerpt(item[:stop.start()] if stop else item)
    return clean_excerpt(text)


_NEXT_ITEM_DATE_RE = re.compile(
    r"\s\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b(?!\s+\d{4})", re.I
)

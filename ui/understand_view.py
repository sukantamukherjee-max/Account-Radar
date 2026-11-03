"""UNDERSTAND v2 -- a deterministic account-understanding view.

`build_understand_view(entry)` turns one stored analysis entry
({"report": AccountRadarReport.to_dict(), "ai_synthesis": ..., ...}) into a
structured view that answers, in order:

  1. What is happening?              -> developments (ranked account signals)
  2. What does it mean?              -> deterministic meaning sentences
  3. What is the Otech capability posture?
  4. What technology / incumbent context is actually evidenced?
  5. Who matters?                    -> People Intelligence (structured fields only)
  6. What do we know about buying / engagement?
  7. What don't we know?

`understand_sections_html(view)` renders that view to HTML fragments; the
Streamlit wrapper `render_understand(view)` only writes those fragments.

Guarantees (the builder is presentation logic only):
  * it never runs research, never calls an AI model or any external service;
  * it never changes an evidence level, category, confidence or epistemic
    status, never creates or promotes an opportunity, and never upgrades
    weak / context evidence;
  * ranked developments come from the normalized account-signal layer
    (ui.account_signals) -- not from the Executive Summary;
  * named vendors are shown as account context only with account-specific
    provenance; market-only vendors are collapsed into a market landscape;
  * a budget, timeline or active buying process is only ever "observed" from
    account-specific evidence -- never inferred.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from commercial.capabilities import get_catalog
from ui import account_signals as sig
from ui.account_signals import (
    ACCOUNT_EVENT_CATEGORIES,
    CONFIRMED,
    DIRECT,
    PROCUREMENT_TYPES,
    STRONG,
    KeySignal,
)

MAX_DEVELOPMENTS_PER_GROUP = 5
MAX_PEOPLE_PER_GROUP = 6
EXCERPT_CHARS = 200

# Capability-posture buckets and statuses -------------------------------------------------
NEED_CATEGORIES = ACCOUNT_EVENT_CATEGORIES | {"business_signal_inference"}
CURRENT_STATE_CATEGORIES = {"current_state_technology", "existing_capability_context"}
STAKEHOLDER_CATEGORIES = {"stakeholder_context", "hiring_intent_context"}

BUCKET_LABELS = {
    "account_need": "Account evidence of need",
    "current_state": "Current-state evidence",
    "stakeholder": "Stakeholder context",
    "market": "Market / industry context",
}
STATUS_EVIDENCE = "Evidence identified"
STATUS_MONITOR = "Monitor"
STATUS_CONTEXT = "Context only"
STATUS_NONE = "No current signal"
STATUS_NOTES = {
    STATUS_EVIDENCE: "The account's own activity points at this area. Validate with the account; this is not an opportunity.",
    STATUS_MONITOR: "Only current-state or stakeholder context links the account to this area. Monitor; no need is evidenced.",
    STATUS_CONTEXT: "Only market, industry or incidental material mentions this area. It says nothing about the account's need.",
    STATUS_NONE: "No evidence in the qualified sources.",
}

# Buying-context items --------------------------------------------------------------------
OBSERVED, INFERRED, UNKNOWN = "observed", "inferred", "unknown"
BUYING_ITEMS = (
    ("procurement_channels", "Procurement channels"),
    ("procurement_stakeholders", "Procurement stakeholders"),
    ("decision_makers", "Decision makers"),
    ("governance", "Governance / ownership"),
    ("incumbent_relationships", "Incumbent relationships"),
    ("active_buying", "Active buying process"),
    ("budget", "Budget"),
    ("timeline", "Timeline"),
)
_BUDGET_RE = re.compile(
    r"(?:\b(?:USD|US\$|OMR|RO|EUR|GBP|AED|SAR|QAR|KWD|BHD|INR)\s?\d[\d,.]*|[$€£]\s?\d[\d,.]*)"
    r"(?:\s?(?:million|billion|mn|bn|m|k)\b)?|\b\d[\d,.]*\s?(?:million|billion)\s?(?:USD|OMR|dollars|rials|euros|riyals|dirhams)\b",
    re.I,
)
_TIMELINE_RE = re.compile(
    r"\b(?:deadline|closing date|submission date|due (?:date|by)|go-live|completion (?:date|by)|"
    r"(?:by|in|during|before) (?:Q[1-4] |H[12] |(?:early|mid|late|end of) )?20\d\d)\b",
    re.I,
)
_OWNERSHIP_RE = re.compile(
    r"\b(affiliated with|subsidiary of|wholly[- ]owned|owned by|government(?:-owned)? (?:company|entity|organi[sz]ation)|"
    r"state[- ]owned|part of the [A-Z][\w&]*(?: [A-Z][\w&]*)* group)\b",
    re.I,
)

# Current-state relationship language -----------------------------------------------------
_RELATIONSHIP_RE = re.compile(
    r"\b(in partnership with|partner(?:s|ed|ing|ship)?(?: with)?|collaborat\w*|contract(?:ed)? with|selected|"
    r"deploy\w*|implement\w*|powered by|signed (?:with|an?)|agreement with|MoU)\b",
    re.I,
)
_RELATIONSHIP_LABELS = (
    ("partner", "Partnership"),
    ("collaborat", "Collaboration"),
    ("contract", "Contract"),
    ("agreement", "Agreement"),
    ("mou", "Agreement"),
    ("signed", "Agreement"),
    ("selected", "Selected supplier"),
    ("deploy", "Deployment"),
    ("implement", "Implementation"),
    ("powered by", "Technology provider"),
)
PROVENANCE_LABELS = {
    "account_stated": "Account-stated",
    "reported": "Reported news",
    "stakeholder_reported": "Stakeholder-reported",
    "market": "Market context",
}

# People ----------------------------------------------------------------------------------
PEOPLE_GROUPS = (
    ("decision_makers", "Decision makers", ("decision_maker", "potential_decision_maker")),
    ("technical", "Technical stakeholders", ("technical_stakeholder", "likely_technical_stakeholder")),
    ("procurement", "Procurement stakeholders", ("procurement_stakeholder",)),
)
SENIORITY_LABELS = {"c_level": "C-level", "director": "Director / VP", "head": "Head", "manager": "Manager"}
ASSOCIATION_LABELS = {"confirmed": "Confirmed association", "weak": "Likely association"}
AREA_LABELS = {
    "it": "IT", "ai": "AI", "data_center": "Data centre", "digital_transformation": "Digital transformation",
    "cybersecurity": "Cybersecurity", "infrastructure": "Infrastructure", "procurement": "Procurement",
    "applications": "Applications", "cloud": "Cloud", "network": "Network", "data": "Data",
}

_AI_OK_STATUSES = {"ok", "degraded"}


# ---------------------------------------------------------------------------
# View model
# ---------------------------------------------------------------------------

@dataclass
class Development:
    signal_type: str
    label: str
    class_key: str
    class_label: str
    strength: str
    excerpt: str
    source_title: str
    source_url: str
    source_domain: str
    provenance: str            # "account" | "reported_news"
    tags: list[str] = field(default_factory=list)          # every classification attached to the event
    relationship_label: str = ""                           # account relationship dimension
    evidence_level: str = ""                               # evidence strength dimension (chain level)
    evidence_label: str = ""
    dimension_note: str = ""                               # plain-language reading of the two dimensions
    raw_text: str = ""                                     # source window, for deterministic phrasing only


@dataclass
class CapabilityPosture:
    capability_id: str
    capability_name: str
    status: str
    status_note: str
    counts: dict[str, int]     # bucket key -> mapping count
    best_level: str | None     # strongest level among account-need rows (never weak)


@dataclass
class CurrentStateItem:
    entity: str
    relationship: str
    provenance: str            # key of PROVENANCE_LABELS
    provenance_label: str
    source_title: str
    source_url: str
    source_domain: str
    excerpt: str


@dataclass
class MarketVendor:
    entity: str
    source_count: int
    domains: list[str]


@dataclass
class PersonCard:
    name: str
    title: str
    seniority: str
    areas: list[str]
    association: str
    association_label: str
    confidence: float | None
    linkedin_url: str
    influence_label: str


@dataclass
class BuyingItem:
    key: str
    label: str
    status: str                # observed | inferred | unknown
    detail: str
    source_url: str = ""


@dataclass
class UnderstandView:
    organization_name: str
    ai_narrative: str
    ai_status: str
    meaning: list[str] = field(default_factory=list)
    posture_summary: str = ""
    developments: dict[str, list[Development]] = field(default_factory=dict)
    development_counts: dict[str, int] = field(default_factory=dict)
    reported_news: list[Development] = field(default_factory=list)
    background_count: int = 0
    posture: list[CapabilityPosture] = field(default_factory=list)
    current_state: list[CurrentStateItem] = field(default_factory=list)
    market_landscape: list[MarketVendor] = field(default_factory=list)
    omitted_incumbent_mentions: int = 0
    people_available: bool = False
    people_groups: dict[str, list[PersonCard]] = field(default_factory=dict)
    people_other_count: int = 0
    people_unconfirmed_count: int = 0
    people_weak_count: int = 0
    buying: list[BuyingItem] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


DEVELOPMENT_GROUPS = (
    ("technology_procurement", "Technology / procurement"),
    ("strategic", "Strategic"),
    ("general", "General activity"),
)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def clean_text(text: str | None) -> str:
    """Display-safe text: compatibility-normalized (styled "math bold"
    letters become plain letters), invisible / bidi control characters
    removed, whitespace collapsed. Wording is not changed."""
    t = unicodedata.normalize("NFKC", text or "")
    t = sig.strip_invisible(t)
    return re.sub(r"\s+", " ", t).strip()


def clean_excerpt(text: str | None, max_chars: int = EXCERPT_CHARS) -> str:
    return sig.clean_excerpt(clean_text(text), max_chars)


def clean_person_title(person: dict[str, Any], organization_name: str, country: str | None) -> str:
    """The person's role. Drops a repeated name prefix, a "| LinkedIn"
    suffix, a truncated trailing " at ..." and a title that is only the
    organization's name. Nothing else is rewritten."""
    title = clean_text(person.get("current_title"))
    name = clean_text(person.get("name"))
    title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", title, flags=re.I)
    if name and title.lower().startswith(name.lower()):
        title = title[len(name):].lstrip(" -–—|,:")
    title = re.sub(r"\s+(?:at|@)\s*(?:\.\.\.|…)\s*$", "", title)
    title = re.sub(r"\s*(?:\.\.\.|…)\s*$", "", title).strip(" -–—|,:")
    if organization_name and title:
        aliases = sig.organization_aliases(organization_name, country=country)
        if title.lower() in {a.lower() for a in aliases}:
            return ""
        for alias in sorted(aliases, key=len, reverse=True):
            # "ICT Systems Manager at <Account>" -> "ICT Systems Manager"
            trimmed = re.sub(r"\s+(?:at|@|-|\u2013|\|)\s+" + re.escape(alias) + r"\.?$", "", title, flags=re.I)
            if trimmed != title:
                return trimmed.strip()
    return title


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------------------
# 1. Developments
# ---------------------------------------------------------------------------

def _dimension_note(k: KeySignal) -> str:
    if k.relationship == sig.REPORTED_NEWS:
        return "Reported on a non-account site; not direct account evidence."
    if k.evidence_level == DIRECT:
        return "About the account, and the evidence chain confirms a capability link."
    if k.evidence_level == STRONG:
        return "About the account; implies a capability requirement to validate, not a confirmed one."
    return "About the account, but does not establish an Otech requirement."


def _development(k: KeySignal) -> Development:
    return Development(
        signal_type=k.signal_type, label=k.label, class_key=k.signal_class, class_label=k.class_label,
        strength=k.strength, excerpt=clean_excerpt(k.excerpt), source_title=clean_text(k.source_title),
        source_url=k.source_url, source_domain=sig.domain(k.source_url), provenance=k.provenance,
        tags=list(k.tags) or [k.label.capitalize()],
        relationship_label=k.relationship_label or sig.RELATIONSHIP_LABELS.get(k.relationship, ""),
        evidence_level=k.evidence_level,
        evidence_label=k.evidence_label or sig.EVIDENCE_LABELS.get(k.evidence_level, ""),
        dimension_note=_dimension_note(k), raw_text=clean_text(k.raw_text),
    )


def _build_developments(signal_set: sig.AccountSignalSet) -> tuple[dict[str, list[Development]], dict[str, int]]:
    groups: dict[str, list[Development]] = {key: [] for key, _ in DEVELOPMENT_GROUPS}
    counts: dict[str, int] = {key: 0 for key, _ in DEVELOPMENT_GROUPS}
    for k in signal_set.ranked:
        if k.signal_class not in groups:
            continue
        counts[k.signal_class] += 1
        if len(groups[k.signal_class]) < MAX_DEVELOPMENTS_PER_GROUP:
            groups[k.signal_class].append(_development(k))
    return groups, counts


# ---------------------------------------------------------------------------
# 3. Capability posture
# ---------------------------------------------------------------------------

def _row_bucket(row: dict[str, Any], report: dict[str, Any]) -> str:
    """Which posture bucket a capability mapping supports. Context rules
    only ever move a row DOWN (towards market context), never up."""
    org = report.get("organization_name") or ""
    country = report.get("country")
    url = row.get("source_url") or ""
    title = row.get("source_title") or ""
    category = row.get("evidence_category")
    level = row.get("evidence_level")
    # Generic industry pages and directory / data-provider pages are context
    # however their mappings are categorized.
    generic = sig.generic_industry_source(url, title, org, country) or sig.is_directory_page(url, title)
    if sig.is_linkedin_profile(url) or category in STAKEHOLDER_CATEGORIES:
        return "stakeholder"
    if category in NEED_CATEGORIES and level in (DIRECT, STRONG) and not generic:
        return "account_need"
    if category in CURRENT_STATE_CATEGORIES and not generic and _row_is_account_linked(row, report):
        return "current_state"
    return "market"


def _row_is_account_linked(row: dict[str, Any], report: dict[str, Any]) -> bool:
    org = report.get("organization_name") or ""
    country = report.get("country")
    url = row.get("source_url") or ""
    if sig.is_account_site(url, report.get("website")):
        return True
    for text in (row.get("source_title"), row.get("evidence_excerpt"), row.get("supporting_context")):
        if text and sig.mentions_account(text, org, country):
            return True
    return False


def _build_posture(report: dict[str, Any]) -> list[CapabilityPosture]:
    names = {c["id"]: c.get("name") or c["id"] for c in get_catalog()}
    order = list(names)
    per_cap: dict[str, dict[str, int]] = {}
    best: dict[str, str] = {}
    for row in report.get("chain") or []:
        cap = row.get("potential_otech_capability") or {}
        cid = cap.get("capability_id")
        if not cid:
            continue
        if cid not in names:
            names[cid] = cap.get("capability_name") or cid
            order.append(cid)
        bucket = _row_bucket(row, report)
        counts = per_cap.setdefault(cid, {k: 0 for k in BUCKET_LABELS})
        counts[bucket] += 1
        if bucket == "account_need":
            level = row.get("evidence_level")
            if best.get(cid) != DIRECT:
                best[cid] = level
    out: list[CapabilityPosture] = []
    for cid in order:
        counts = per_cap.get(cid, {k: 0 for k in BUCKET_LABELS})
        if counts["account_need"]:
            status = STATUS_EVIDENCE
        elif counts["current_state"] or counts["stakeholder"]:
            status = STATUS_MONITOR
        elif counts["market"]:
            status = STATUS_CONTEXT
        else:
            status = STATUS_NONE
        out.append(CapabilityPosture(
            capability_id=cid, capability_name=names[cid], status=status, status_note=STATUS_NOTES[status],
            counts=counts, best_level=best.get(cid),
        ))
    rank = {STATUS_EVIDENCE: 0, STATUS_MONITOR: 1, STATUS_CONTEXT: 2, STATUS_NONE: 3}
    return sorted(out, key=lambda p: (rank[p.status], order.index(p.capability_id)))


def _confirmed_need_capabilities(report: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in report.get("chain") or []:
        cap = (row.get("potential_otech_capability") or {})
        if (
            row.get("evidence_level") == DIRECT and row.get("epistemic_status") == CONFIRMED
            and row.get("evidence_category") in ACCOUNT_EVENT_CATEGORIES
            and _row_bucket(row, report) == "account_need"
        ):
            name = cap.get("capability_name") or cap.get("capability_id") or ""
            if name and name not in out:
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# 4. Current state / incumbents
# ---------------------------------------------------------------------------

def _relationship_label(snippet: str) -> str:
    m = _RELATIONSHIP_RE.search(snippet or "")
    if not m:
        return "Named in account context"
    word = m.group(0).lower()
    for marker, label in _RELATIONSHIP_LABELS:
        if marker in word:
            return label
    return "Named in account context"


def _vendor_in(term: str, text: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9])#?" + re.escape(term) + r"(?![A-Za-z0-9])", text or ""))


def _linked_people_urls(report: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for p in report.get("people") or []:
        if p.get("organization_association") in ("confirmed", "weak"):
            for u in [p.get("linkedin_url"), *(p.get("source_urls") or [])]:
                if u:
                    urls.add(u)
    return urls


def _build_current_state(report: dict[str, Any]) -> tuple[list[CurrentStateItem], list[MarketVendor], int]:
    org = report.get("organization_name") or ""
    country = report.get("country")
    website = report.get("website")
    linked_profiles = _linked_people_urls(report)
    by_source: dict[str, list[dict]] = {}
    for item in report.get("incumbent_evidence") or []:
        by_source.setdefault(item.get("source_url") or "", []).append(item)

    items: list[CurrentStateItem] = []
    market: dict[str, MarketVendor] = {}
    omitted = 0
    seen: set[tuple[str, str]] = set()
    for url, rows in by_source.items():
        title = rows[0].get("source_title") or ""
        snippets = [clean_text(r.get("snippet")) for r in rows if r.get("snippet")]
        vendors: list[str] = []
        for r in rows:
            if r.get("category") == "named_vendor" and r.get("term") and r["term"] not in vendors:
                vendors.append(r["term"])
        linkedin = sig.is_linkedin_profile(url)
        generic = sig.generic_industry_source(url, title, org, country) or sig.is_directory_page(url, title)
        if linkedin:
            provenance = "stakeholder_reported" if url in linked_profiles else None
        elif generic:
            provenance = "market"
        elif sig.is_account_site(url, website):
            provenance = "account_stated"
        else:
            provenance = "reported"
        for vendor in vendors:
            # A named vendor is account context only when a snippet from the
            # same source names the vendor AND the account together.
            linked_snippet = next(
                (s for s in snippets if _vendor_in(vendor, s) and (
                    provenance == "account_stated" or sig.mentions_account(s, org, country)
                ) and (provenance != "stakeholder_reported" or _RELATIONSHIP_RE.search(s))),
                None,
            )
            if provenance in ("account_stated", "reported", "stakeholder_reported") and linked_snippet:
                key = (vendor.lower(), provenance)
                if key in seen:
                    continue
                seen.add(key)
                items.append(CurrentStateItem(
                    entity=vendor, relationship=_relationship_label(linked_snippet), provenance=provenance,
                    provenance_label=PROVENANCE_LABELS[provenance], source_title=clean_text(title),
                    source_url=url, source_domain=sig.domain(url),
                    excerpt=_vendor_excerpt(linked_snippet, vendor),
                ))
            elif linkedin:
                omitted += 1        # personal-profile mention with no account link: not shown
            else:
                mv = market.setdefault(vendor.lower(), MarketVendor(entity=vendor, source_count=0, domains=[]))
                d = sig.domain(url)
                if d and d not in mv.domains:
                    mv.domains.append(d)
                    mv.source_count += 1
        # Bare relationship phrases ("partner with") are never shown on their
        # own: they only matter through a named counterparty above.
        omitted += sum(1 for r in rows if r.get("category") != "named_vendor" and not vendors)
    order = {"account_stated": 0, "reported": 1, "stakeholder_reported": 2}
    items.sort(key=lambda i: (order.get(i.provenance, 9), i.entity.lower()))
    landscape = sorted(market.values(), key=lambda v: (-v.source_count, v.entity.lower()))
    return items, landscape, omitted


def _vendor_excerpt(snippet: str, vendor: str) -> str:
    """Start the excerpt near the vendor mention so the relationship is visible."""
    m = re.search(re.escape(vendor), snippet)
    if m and m.start() > 80:
        snippet = snippet[m.start() - 80:]
    return clean_excerpt(snippet)


# ---------------------------------------------------------------------------
# 5. People
# ---------------------------------------------------------------------------

def _person_card(p: dict[str, Any], org: str, country: str | None, influence_label: str) -> PersonCard:
    areas: list[str] = []
    for a in list(p.get("functions") or []) + list(p.get("technology_areas") or []):
        label = AREA_LABELS.get(a, str(a).replace("_", " ").capitalize())
        if label not in areas:
            areas.append(label)
    conf = p.get("confidence")
    assoc = p.get("organization_association") or ""
    return PersonCard(
        name=clean_text(p.get("name")) or "Unnamed profile",
        title=clean_person_title(p, org, country),
        seniority=SENIORITY_LABELS.get(p.get("seniority") or "", ""),
        areas=areas,
        association=assoc,
        association_label=ASSOCIATION_LABELS.get(assoc, ""),
        confidence=float(conf) if isinstance(conf, (int, float)) else None,
        linkedin_url=p.get("linkedin_url") or "",
        influence_label=influence_label,
    )


def _build_people(report: dict[str, Any]) -> tuple[bool, dict[str, list[PersonCard]], int, int, int]:
    if "people" not in report:
        return False, {}, 0, 0, 0
    org = report.get("organization_name") or ""
    country = report.get("country")
    people = report.get("people") or []
    eligible = [p for p in people if p.get("organization_association") in ("confirmed", "weak")]
    unconfirmed = len(people) - len(eligible)
    weak = sum(1 for p in eligible if p.get("organization_association") == "weak")
    groups: dict[str, list[PersonCard]] = {}
    grouped_ids: set[int] = set()
    for key, label, influences in PEOPLE_GROUPS:
        members = [p for p in eligible if p.get("influence_type") in influences]
        members.sort(key=lambda p: (
            p.get("organization_association") == "confirmed", p.get("relevance_score") or 0, p.get("confidence") or 0,
        ), reverse=True)
        grouped_ids.update(id(p) for p in members)
        groups[key] = [_person_card(p, org, country, label.rstrip("s")) for p in members[:MAX_PEOPLE_PER_GROUP]]
    other = sum(1 for p in eligible if id(p) not in grouped_ids)
    return True, groups, other, unconfirmed, weak


# ---------------------------------------------------------------------------
# 6. Buying / engagement context
# ---------------------------------------------------------------------------

def _build_buying(
    report: dict[str, Any], signal_set: sig.AccountSignalSet, people_available: bool,
    people_groups: dict[str, list[PersonCard]], current_state: list[CurrentStateItem],
) -> list[BuyingItem]:
    website = report.get("website")
    tech_proc = [k for k in signal_set.ranked if k.signal_class == "technology_procurement"]
    procurement = [k for k in signal_set.ranked if k.signal_type in PROCUREMENT_TYPES]
    items: dict[str, BuyingItem] = {}

    own_proc = [k for k in procurement if sig.is_account_site(k.source_url, website)]
    if own_proc:
        items["procurement_channels"] = BuyingItem(
            "procurement_channels", "", OBSERVED,
            f"Procurement activity published on the account's own site ({sig.domain(own_proc[0].source_url)}).",
            own_proc[0].source_url)
    elif procurement:
        items["procurement_channels"] = BuyingItem(
            "procurement_channels", "", OBSERVED,
            f"Account-specific procurement activity reported via {sig.domain(procurement[0].source_url)}.",
            procurement[0].source_url)
    else:
        items["procurement_channels"] = BuyingItem("procurement_channels", "", UNKNOWN,
                                                   "No account-specific procurement channel found.")

    for key, group in (("procurement_stakeholders", "procurement"), ("decision_makers", "decision_makers")):
        members = people_groups.get(group) or []
        noun = "procurement-function profile" if group == "procurement" else "senior profile"
        if not people_available:
            items[key] = BuyingItem(key, "", UNKNOWN, "People Intelligence was not run for this report.")
        elif any(m.association == "confirmed" for m in members):
            items[key] = BuyingItem(key, "", OBSERVED, (
                f"{_plural(len(members), noun)} linked to the account on LinkedIn. "
                "Roles show position, not buying authority."))
        elif members:
            items[key] = BuyingItem(key, "", INFERRED, (
                f"{_plural(len(members), noun)} with a likely (unconfirmed) link to the account."))
        else:
            items[key] = BuyingItem(key, "", UNKNOWN, "None identified.")

    governance = next((k for k in signal_set.background
                       if sig.is_account_site(k.source_url, website) and _OWNERSHIP_RE.search(k.raw_text)), None)
    leadership = [k for k in signal_set.ranked if k.signal_type == "leadership_change"]
    if governance:
        m = _OWNERSHIP_RE.search(governance.raw_text)
        start = max(0, m.start() - 90) if m else 0
        items["governance"] = BuyingItem("governance", "", OBSERVED,
                                         "Account site: “" + clean_excerpt(governance.raw_text[start:], 160) + "”",
                                         governance.source_url)
        if leadership:
            items["governance"].detail += f" Leadership change reported ({sig.domain(leadership[0].source_url)})."
    elif leadership:
        items["governance"] = BuyingItem("governance", "", OBSERVED,
                                         f"Leadership change reported: “{clean_excerpt(leadership[0].excerpt, 140)}”",
                                         leadership[0].source_url)
    else:
        items["governance"] = BuyingItem("governance", "", UNKNOWN, "No ownership or governance statement found.")

    stated = [c for c in current_state if c.provenance in ("account_stated", "reported")]
    reported = [c for c in current_state if c.provenance == "stakeholder_reported"]
    if stated:
        items["incumbent_relationships"] = BuyingItem(
            "incumbent_relationships", "", OBSERVED,
            f"{_join(sorted({c.entity for c in stated}))} named with the account ({stated[0].provenance_label.lower()}).",
            stated[0].source_url)
    elif reported:
        items["incumbent_relationships"] = BuyingItem(
            "incumbent_relationships", "", INFERRED,
            f"{_join(sorted({c.entity for c in reported}))} reported only on a stakeholder's LinkedIn profile.",
            reported[0].source_url)
    else:
        items["incumbent_relationships"] = BuyingItem("incumbent_relationships", "", UNKNOWN,
                                                      "No vendor relationship with account-specific provenance.")

    if procurement:
        items["active_buying"] = BuyingItem("active_buying", "", OBSERVED,
                                            f"Account-specific {procurement[0].label}: “{clean_excerpt(procurement[0].excerpt, 140)}”",
                                            procurement[0].source_url)
    else:
        items["active_buying"] = BuyingItem("active_buying", "", UNKNOWN,
                                            "No account-specific tender, RFP or contract award found.")

    for key, regex, missing in (
        ("budget", _BUDGET_RE, "No budget or contract value stated in account-specific evidence."),
        ("timeline", _TIMELINE_RE, "No deadline or delivery timeline stated in account-specific evidence."),
    ):
        hit = next(((k, regex.search(k.raw_text or k.excerpt)) for k in tech_proc if regex.search(k.raw_text or k.excerpt)), None)
        if hit:
            k, m = hit
            items[key] = BuyingItem(key, "", OBSERVED, f"“{m.group(0).strip()}” stated in a {k.label} item.", k.source_url)
        else:
            items[key] = BuyingItem(key, "", UNKNOWN, missing)

    labels = dict(BUYING_ITEMS)
    out = []
    for key, _ in BUYING_ITEMS:
        item = items[key]
        item.label = labels[key]
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 2 + 7. Meaning and unknowns
# ---------------------------------------------------------------------------

_TYPE_PHRASES = {
    "expansion": "an expansion", "investment": "an investment", "contract_award": "a contract award",
    "procurement_activity": "procurement activity", "technology_initiative": "a technology initiative",
    "project": "a project", "capacity_change": "a capacity change", "partnership": "a partnership",
}


def _counterparty(text: str, organization_name: str, country: str | None) -> str:
    return sig.explicit_counterparty(text, organization_name, country)


def _phrase(d: Development, org: str, country: str | None) -> str:
    if d.signal_type == "partnership" or "Partnership" in d.tags:
        party = _counterparty(d.excerpt, org, country) or _counterparty(d.raw_text, org, country)
        return f"a {party} partnership" if party else "a partnership"
    return _TYPE_PHRASES.get(d.signal_type, f"a {d.label}")


def _short_name(org: str, country: str | None) -> str:
    aliases = sig.organization_aliases(org, country=country) if org else []
    multi = [a for a in aliases[1:] if " " in a]
    return multi[0] if multi else (org or "This account")


def _build_meaning(view: UnderstandView, country: str | None, confirmed_need: list[str]) -> list[str]:
    """A short deterministic interpretation built only from the developments,
    their tags and the evidence chain's own levels. No facts are invented:
    names come verbatim from the development text."""
    org = view.organization_name
    name = _short_name(org, country)
    all_devs = [d for key, _ in DEVELOPMENT_GROUPS for d in view.developments.get(key) or []]
    out: list[str] = []
    if all_devs:
        kinds = {"technology_procurement": "technology / procurement", "strategic": "strategic", "general": "general business"}
        present = [kinds[key] for key, _ in DEVELOPMENT_GROUPS if view.developments.get(key)]
        phrases: list[str] = []
        leadership = [d for d in all_devs if d.signal_type == "leadership_change"]
        # Name the substantive developments; general activity is only named
        # when nothing more specific exists.
        specific = [d for d in all_devs if d.class_key != "general"]
        for d in (specific or all_devs):
            if d.signal_type == "leadership_change":
                continue
            p = _phrase(d, org, country) if d.class_key != "general" else "general operational activity"
            if p not in phrases:
                phrases.append(p)
        if leadership:
            phrases.insert(min(1, len(phrases)), "recent leadership changes" if len(leadership) > 1 else "a recent leadership change")
        out.append(f"{name} is showing {_join(present)} activity, including {_join(phrases[:3])}.")
    else:
        out.append(f"The public evidence collected does not show account-specific developments for {name} yet.")
    if confirmed_need:
        out.append(f"Account evidence of need is identified for {_join(confirmed_need)}; validate it with the account.")
    elif view.developments.get("technology_procurement"):
        tp = view.developments["technology_procurement"][0]
        out.append(f"Technology / procurement activity is visible ({tp.label}), but available evidence does not "
                   "confirm an Otech-specific requirement yet.")
    else:
        lead = "However, available" if all_devs else "Available"
        out.append(f"{lead} evidence does not currently establish an Otech-specific technology or procurement requirement.")
    if view.reported_news:
        d = view.reported_news[0]
        phrase = _phrase(d, org, country)
        out.append(
            f"{phrase[0].upper() + phrase[1:]} is also reported on {d.source_domain}, not the account's own site; "
            "it adds strategic context but is not direct account evidence."
        )
    if view.current_state:
        named = _join([f"{c.entity} ({c.provenance_label.lower()})" for c in view.current_state[:3]])
        out.append(f"Technology relationships with account-specific provenance: {named}.")
    return out


def _posture_summary(view: UnderstandView) -> str:
    n = {s: sum(1 for p in view.posture if p.status == s) for s in (STATUS_EVIDENCE, STATUS_MONITOR, STATUS_CONTEXT, STATUS_NONE)}
    return (f"{n[STATUS_EVIDENCE]} evidence identified, {n[STATUS_MONITOR]} monitor, "
            f"{n[STATUS_CONTEXT]} context only, {n[STATUS_NONE]} no current signal.")


def _build_unknowns(view: UnderstandView, report: dict[str, Any], confirmed_need: list[str]) -> list[str]:
    out: list[str] = []
    if not confirmed_need:
        out.append("No confirmed Otech need: no capability has direct, account-specific evidence of need.")
    inference_only = [p.capability_name for p in view.posture if p.status == STATUS_EVIDENCE and p.capability_name not in confirmed_need]
    if inference_only:
        out.append(f"{_join(inference_only)}: account evidence is inference only and needs validation.")
    buying = {b.key: b for b in view.buying}
    if buying["budget"].status == UNKNOWN:
        out.append("No budget or contract value is evidenced.")
    if buying["timeline"].status == UNKNOWN:
        out.append("No buying timeline is evidenced.")
    if buying["active_buying"].status == UNKNOWN:
        out.append("No active RFP, tender or contract award is attributed to the account.")
    context_only = [p.capability_name for p in view.posture if p.status == STATUS_CONTEXT]
    if context_only:
        out.append(f"Supported only by market / industry context: {_join(context_only)}.")
    if view.people_available:
        bits = []
        if view.people_weak_count:
            bits.append(f"{_plural(view.people_weak_count, 'profile')} matched only by name tokens (likely association)")
        if view.people_unconfirmed_count:
            bits.append(f"{_plural(view.people_unconfirmed_count, 'profile')} with no confirmed link excluded")
        out.append(
            "Stakeholder relationships are unverified: LinkedIn roles show position, not buying authority"
            + (f"; {'; '.join(bits)}." if bits else ".")
        )
    else:
        out.append("Stakeholders are unknown: People Intelligence was not run for this report.")
    if view.reported_news:
        out.append("Reported news from non-account sites has not been confirmed on the account's own channels.")
    q = report.get("qualification") or {}
    if q.get("corpus_reason"):
        out.append(str(q["corpus_reason"]))
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_understand_view(entry: dict[str, Any]) -> UnderstandView:
    """Deterministic UNDERSTAND view for one analysis entry."""
    entry = entry or {}
    report = entry.get("report") or {}
    ai = entry.get("ai_synthesis") or {}
    narrative = clean_text(ai.get("account_narrative")) if isinstance(ai, dict) else ""
    ai_status = (ai.get("status") or "") if isinstance(ai, dict) else ""
    if ai_status and ai_status not in _AI_OK_STATUSES:
        narrative = ""

    signal_set = sig.normalize_account_signals(report)
    developments, dev_counts = _build_developments(signal_set)
    view = UnderstandView(
        organization_name=clean_text(report.get("organization_name")),
        ai_narrative=narrative,
        ai_status=ai_status,
        developments=developments,
        development_counts=dev_counts,
        reported_news=[_development(k) for k in signal_set.reported_news[:MAX_DEVELOPMENTS_PER_GROUP]],
        background_count=signal_set.background_count,
    )
    view.posture = _build_posture(report)
    view.current_state, view.market_landscape, view.omitted_incumbent_mentions = _build_current_state(report)
    (view.people_available, view.people_groups, view.people_other_count,
     view.people_unconfirmed_count, view.people_weak_count) = _build_people(report)
    view.buying = _build_buying(report, signal_set, view.people_available, view.people_groups, view.current_state)
    confirmed_need = _confirmed_need_capabilities(report)
    view.meaning = _build_meaning(view, report.get("country"), confirmed_need)
    view.posture_summary = _posture_summary(view)
    view.unknowns = _build_unknowns(view, report, confirmed_need)
    return view


# ---------------------------------------------------------------------------
# Renderer (HTML fragments; styling lives in theme_engine.py)
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _link(url: str, text: str) -> str:
    if not url or not re.match(r"https?://", url):
        return _e(text)
    return f'<a href="{_e(url)}" target="_blank" rel="noopener">{_e(text)}</a>'


def _section(title: str, body: str, *, lead: str = "", meta: str = "") -> str:
    lead_html = f'<div class="ar-panel-lead">{_e(lead)}</div>' if lead else ""
    meta_html = f'<div class="ar-panel-meta">{_e(meta)}</div>' if meta else ""
    return (
        '<div class="ar-panel ar-u-section">'
        f'<div class="ar-panel-head"><div class="ar-panel-title">{_e(title)}</div>{meta_html}</div>'
        f"{lead_html}{body}</div>"
    )


def _empty(text: str) -> str:
    return f'<div class="ar-exec-empty">{_e(text)}</div>'


def _dev_html(d: Development) -> str:
    tags = "".join(f'<span class="ar-chip is-plain">{_e(t)}</span>' for t in d.tags)
    dims = (
        f'<span class="ar-u-dim" title="Account relationship">{_e(d.relationship_label)}</span>'
        f'<span class="ar-u-dim is-{_e(d.evidence_level)}" title="Evidence strength">{_e(d.evidence_label)}</span>'
    )
    return (
        '<div class="ar-u-item">'
        f'<div class="ar-u-item-head">{tags}</div>'
        f'<div class="ar-u-dims">{dims}</div>'
        f'<div class="ar-u-excerpt">“{_e(d.excerpt)}”</div>'
        f'<div class="ar-u-note">{_e(d.dimension_note)}</div>'
        f'<div class="ar-u-src">{_link(d.source_url, d.source_title or d.source_domain)} · {_e(d.source_domain)}</div>'
        "</div>"
    )


def narrative_html(view: UnderstandView) -> str:
    facts = "".join(f"<p>{_e(s)}</p>" for s in view.meaning)
    det = (
        '<div class="ar-u-block">'
        '<div class="ar-exec-label">Deterministic summary · from ranked evidence</div>'
        f'<div class="ar-exec-summary">{facts}</div></div>'
    )
    if view.posture_summary:
        det += (
            '<div class="ar-u-block">'
            '<div class="ar-exec-label">Capability posture</div>'
            f'<div class="ar-u-note">{_e(view.posture_summary)} Details below; a status is never an opportunity.</div></div>'
        )
    if view.ai_narrative:
        ai = (
            '<div class="ar-u-block ar-u-ai">'
            '<div class="ar-exec-label">AI interpretation · verify against the sources</div>'
            f'<div class="ar-exec-summary"><p>{_e(view.ai_narrative)}</p></div></div>'
        )
    else:
        ai = (
            '<div class="ar-u-block ar-u-ai">'
            '<div class="ar-exec-label">AI interpretation</div>'
            f'{_empty("Not available for this run. The deterministic summary above stands on its own.")}</div>'
        )
    return _section("What does it mean?", det + ai)


def developments_html(view: UnderstandView) -> str:
    cols = []
    for key, label in DEVELOPMENT_GROUPS:
        items = view.developments.get(key) or []
        count = view.development_counts.get(key, 0)
        body = "".join(_dev_html(d) for d in items) or _empty("None found.")
        cols.append(f'<div class="ar-u-col"><div class="ar-exec-label">{_e(label)} · {count}</div>{body}</div>')
    body = f'<div class="ar-u-cols">{"".join(cols)}</div>'
    if view.reported_news:
        news = "".join(_dev_html(d) for d in view.reported_news)
        body += (
            '<div class="ar-u-block"><div class="ar-exec-label">Reported news · non-account sites</div>'
            '<div class="ar-panel-lead">Account news found on another site (for example a parent, group or media site). '
            "Strategic context only; not direct account evidence.</div>"
            f"{news}</div>"
        )
    meta = f"{_plural(view.background_count, 'background item')} excluded" if view.background_count else ""
    return _section(
        "What is happening?", body, meta=meta,
        lead="Account-specific developments only. Industry context, directory pages and incidental mentions are excluded.",
    )


def posture_html(view: UnderstandView) -> str:
    rows = []
    for p in view.posture:
        status_cls = {
            STATUS_EVIDENCE: "is-evidence", STATUS_MONITOR: "is-monitor", STATUS_CONTEXT: "is-context", STATUS_NONE: "is-none",
        }[p.status]
        counts = "".join(
            f'<span class="ar-u-count"><b>{p.counts.get(k, 0)}</b> {_e(label)}</span>'
            for k, label in BUCKET_LABELS.items()
        )
        rows.append(
            '<div class="ar-u-posture">'
            f'<div class="ar-u-posture-head"><span class="ar-u-cap">{_e(p.capability_name)}</span>'
            f'<span class="ar-u-status {status_cls}">{_e(p.status)}</span></div>'
            f'<div class="ar-u-counts">{counts}</div>'
            f'<div class="ar-u-note">{_e(p.status_note)}</div>'
            "</div>"
        )
    return _section(
        "Otech capability posture", "".join(rows) or _empty("No capability catalogue available."),
        lead="Evidence status per capability area. A status is never an opportunity.",
    )


def current_state_html(view: UnderstandView) -> str:
    if view.current_state:
        body = "".join(
            '<div class="ar-u-item">'
            f'<div class="ar-u-item-head"><span class="ar-u-entity">{_e(c.entity)}</span>'
            f'<span class="ar-chip is-plain">{_e(c.relationship)}</span>'
            f'<span class="ar-u-strength">{_e(c.provenance_label)}</span></div>'
            f'<div class="ar-u-excerpt">“{_e(c.excerpt)}”</div>'
            f'<div class="ar-u-src">{_link(c.source_url, c.source_title or c.source_domain)} · {_e(c.source_domain)}</div>'
            "</div>"
            for c in view.current_state
        )
    else:
        body = _empty("No named vendor or partner is linked to the account by account-specific evidence.")
    if view.market_landscape:
        vendors = ", ".join(f"{_e(v.entity)} ({v.source_count})" for v in view.market_landscape)
        body += (
            '<div class="ar-u-block"><div class="ar-exec-label">Market landscape · not account evidence</div>'
            f'<div class="ar-u-note">Named in market or industry sources only: {vendors}.</div></div>'
        )
    return _section(
        "Technology & incumbent context", body,
        lead="Only vendors named together with the account are shown as account context.",
    )


def people_html(view: UnderstandView) -> str:
    if not view.people_available:
        return _section("Who matters?", _empty("People Intelligence was not run for this report."))
    cols = []
    for key, label, _ in PEOPLE_GROUPS:
        members = view.people_groups.get(key) or []
        cards = "".join(
            '<div class="ar-u-person">'
            f'<div class="ar-u-person-name">{_link(m.linkedin_url, m.name)}</div>'
            + (f'<div class="ar-u-person-title">{_e(m.title)}</div>' if m.title else "")
            + '<div class="ar-u-person-meta">'
            + " · ".join(_e(x) for x in (m.seniority, m.association_label,
                                          f"confidence {m.confidence:.0f}" if m.confidence is not None else "") if x)
            + "</div>"
            + (f'<div class="ar-u-note">{_e(", ".join(m.areas))}</div>' if m.areas else "")
            + "</div>"
            for m in members
        ) or _empty("None identified.")
        cols.append(f'<div class="ar-u-col"><div class="ar-exec-label">{_e(label)} · {len(members)}</div>{cards}</div>')
    meta = []
    if view.people_other_count:
        meta.append(f"{_plural(view.people_other_count, 'other linked profile')} in PEOPLE")
    return _section(
        "Who matters?", f'<div class="ar-u-cols">{"".join(cols)}</div>', meta="; ".join(meta),
        lead="From People Intelligence. Confirmed and likely account associations only.",
    )


def buying_html(view: UnderstandView) -> str:
    rows = "".join(
        '<div class="ar-u-buy">'
        f'<span class="ar-u-buy-label">{_e(b.label)}</span>'
        f'<span class="ar-u-tag is-{_e(b.status)}">{_e(b.status)}</span>'
        f'<span class="ar-u-buy-detail">{_e(b.detail)}'
        + (f' {_link(b.source_url, "source")}' if b.source_url else "")
        + "</span></div>"
        for b in view.buying
    )
    return _section(
        "Buying & engagement context", rows,
        lead="Observed = stated in evidence; inferred = derived from a weaker link; unknown = no evidence. Nothing is assumed.",
    )


def unknowns_html(view: UnderstandView) -> str:
    items = "".join(f"<li>{_e(u)}</li>" for u in view.unknowns)
    return _section("What we don't know", f'<ul class="ar-u-list">{items}</ul>' if items else _empty("No gaps recorded."))


def understand_sections_html(view: UnderstandView) -> list[str]:
    """HTML fragments in reading order (questions 1-7)."""
    return [
        developments_html(view),
        narrative_html(view),
        posture_html(view),
        current_state_html(view),
        people_html(view),
        buying_html(view),
        unknowns_html(view),
    ]


def render_understand(view: UnderstandView) -> None:
    """Streamlit wrapper: writes the HTML fragments. No logic."""
    import streamlit as st

    for fragment in understand_sections_html(view):
        st.markdown(fragment, unsafe_allow_html=True)

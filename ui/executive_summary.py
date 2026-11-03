"""Executive Account Summary -- a concise, evidence-faithful executive view
of ONE account, built purely from what the research pipeline already
produced (report signals, evidence chain, deterministic opportunity
hypotheses, and People Intelligence).

This module is presentation logic only:

  * it never runs research, never calls an AI model, never creates people;
  * it never changes an evidence level, category, confidence or epistemic
    status -- it only selects, ranks and words what is already there;
  * every opportunity it shows keeps its evidence level, confidence,
    epistemic status and a traceable source.

Evidence rules enforced here (defense in depth on top of the frozen
Evidence Intelligence layer in commercial/):

  1. Industry context is never account evidence: a signal from a source the
     evidence layer classifies as generic industry/trend/explainer content
     (same rule, imported read-only), or from a source whose only mappings
     are `industry_context`, is never a key account signal.
  2. `industry_context` is never a confirmed opportunity: an opportunity is
     shown as CONFIRMED only when its own epistemic_status is
     confirmed_evidence AND it is direct_evidence AND the evidence chain holds
     a direct_evidence row for that capability of an account-event category.
     Anything else is shown as a sales hypothesis.
  3. Weak relevance never becomes an opportunity: opportunities whose level
     is not direct_evidence/strong_inference are not shown at all.
  4. No invented facts: every sentence is templated from counts, labels and
     verbatim source excerpts already in the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Read-only reuse of the frozen evidence layer's own rule (kept as a module
# attribute so the generic-industry check is resolved at call time).
from commercial.evidence_mapping import _is_generic_industry_content

# The normalized account-signal layer is the single source of truth for
# which signals are account-specific and how they rank. These names are
# re-exported for backwards compatibility with existing imports.
from ui import account_signals as _signals
from ui.account_signals import (  # noqa: F401  (re-exports)
    ACCOUNT_EVENT_CATEGORIES,
    BACKGROUND_SOURCE_CATEGORIES,
    CONTEXT_CATEGORIES,
    PROCUREMENT_TYPES,
    SIGNAL_LABELS,
    SIGNAL_TYPE_PRIORITY,
    SNIPPET_CHARS,
    TECHNOLOGY_PROCUREMENT_TYPES,
    KeySignal,
    _accepts_keyword,
    classify_signal,
    clean_excerpt,
    is_account_site,
)
from ui.account_signals import CLASS_LABELS as _CLASS_LABELS  # noqa: F401
from ui.account_signals import CLASS_RANK as _CLASS_RANK  # noqa: F401
from ui.account_signals import domain as _domain  # noqa: F401
from ui.account_signals import is_linkedin_profile as _is_linkedin_profile  # noqa: F401
from ui.account_signals import specific_technology_terms as _specific_technology_terms  # noqa: F401
from ui.account_signals import title_establishes_account_context as _title_establishes_account_context  # noqa: F401

MAX_KEY_SIGNALS = 5
MAX_STAKEHOLDERS = 3
HIGH_CONFIDENCE_THRESHOLD = 75.0

CONFIRMED = "confirmed_evidence"
INFERENCE = "inference_hypothesis"
DIRECT = "direct_evidence"
STRONG = "strong_inference"
OPPORTUNITY_LEVELS = (DIRECT, STRONG)

# Which People Intelligence functions/technology areas relate to each
# catalogue capability -- used only to prefer, never to create, stakeholders.
CAPABILITY_PEOPLE_AFFINITY = {
    "cybersecurity_resilience": {"cybersecurity", "it", "infrastructure"},
    "cloud_ai_infrastructure": {"cloud", "ai", "data", "digital_transformation", "it"},
    "data_centre_infrastructure": {"data_center", "infrastructure", "it", "cloud"},
    "network_connectivity": {"network", "infrastructure", "it"},
    "enterprise_managed_services": {"it", "infrastructure", "digital_transformation", "cloud"},
}
_SENIORITY_RANK = {"c_level": 4, "director": 3, "head": 3, "manager": 2}


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class ExecutiveOpportunity:
    capability_id: str
    capability_name: str
    confirmed: bool
    evidence_level: str
    epistemic_status: str
    confidence: float
    priority: str
    why: str
    evidence_category: str | None
    evidence_excerpt: str
    source_title: str
    source_url: str
    source_count: int
    downgraded: bool = False   # True when the opportunity claimed confirmation the evidence chain cannot support


@dataclass
class Stakeholder:
    person: dict[str, Any]
    relevant_capability: str | None


@dataclass
class ExecutiveSummary:
    organization_name: str
    meaningful_signal_count: int
    opportunity_count: int
    high_confidence_count: int
    stakeholder_count: int | None                 # None = People Intelligence not available in this report
    summary_sentences: list[str] = field(default_factory=list)
    key_signals: list[KeySignal] = field(default_factory=list)
    confirmed_opportunities: list[ExecutiveOpportunity] = field(default_factory=list)
    hypothesis_opportunities: list[ExecutiveOpportunity] = field(default_factory=list)
    stakeholders: list[Stakeholder] = field(default_factory=list)
    conversation: str = ""
    conversation_basis: str = ""
    context_source_count: int = 0                  # industry/context sources kept as background only
    background_signal_count: int = 0               # account-linked items classed as page chrome / directory / profile boilerplate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def display_title(person: dict[str, Any]) -> str:
    """A stakeholder's title for display. LinkedIn headlines often repeat
    the person's name ("Jane Doe - CIO - Org | LinkedIn"); the repeated name
    and the "| LinkedIn" suffix are dropped. Nothing else is changed."""
    title = (person.get("current_title") or "").strip()
    name = (person.get("name") or "").strip()
    title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", title, flags=re.I)
    if name and title.lower().startswith(name.lower()):
        title = title[len(name):].lstrip(" -\u2013\u2014|,:\u200f")
    return title.strip()


def stakeholder_name(person: dict[str, Any]) -> str:
    """Display name with invisible / bidi control characters removed --
    the same cleaning PEOPLE and DECIDE apply."""
    from ui.understand_view import clean_text
    return clean_text(person.get("name"))


def stakeholder_title(person: dict[str, Any], report: dict[str, Any]) -> str:
    """Display title using the same cleaning DECIDE uses (repeated name,
    "| LinkedIn", truncated trailing "at ..." and bidi characters removed)."""
    from ui.understand_view import clean_person_title
    return clean_person_title(person, report.get("organization_name") or "", report.get("country"))


def _generic_industry_source(url: str, title: str, org: str, country: str | None) -> bool:
    """The Evidence Intelligence layer's generic-industry rule, called with
    exactly the arguments its loaded signature accepts (resolved from this
    module's attribute at call time, so a reloaded module is honoured)."""
    return _signals.call_generic_industry_check(_is_generic_industry_content, url, title, org, country)


def _is_context_source(url: str, title: str, chain_by_url: dict[str, list[dict]], org: str, country: str | None) -> bool:
    return _signals.is_context_source(url, title, chain_by_url, org, country, generic_check=_generic_industry_source)


# ---------------------------------------------------------------------------
# Signals -- read from the normalized account-signal layer
# ---------------------------------------------------------------------------

def rank_key_signals(report: dict[str, Any]) -> tuple[list[KeySignal], int]:
    """Return (top signals, meaningful signal count). See `_rank_signals`."""
    top, meaningful, _ = _rank_signals(report)
    return top, meaningful


def _rank_signals(report: dict[str, Any]) -> tuple[list[KeySignal], int, int]:
    """Return (top signals, meaningful signal count, background count) from
    the normalized account-signal layer (ui.account_signals). The reported-
    news tier is deliberately not part of the executive key signals."""
    signal_set = _signals.normalize_account_signals(report, generic_check=_generic_industry_source)
    return signal_set.ranked[:MAX_KEY_SIGNALS], signal_set.meaningful_count, signal_set.background_count


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------

def _supporting_row(capability_id: str, chain: list[dict]) -> dict | None:
    """Strongest non-context evidence row for a capability (direct before
    strong, then confidence). Context rows are never used as the evidence
    shown for an opportunity."""
    rows = [
        r for r in chain
        if (r.get("potential_otech_capability") or {}).get("capability_id") == capability_id
        and r.get("evidence_level") in OPPORTUNITY_LEVELS
        and r.get("evidence_category") not in CONTEXT_CATEGORIES
    ]
    if not rows:
        return None
    order = {DIRECT: 2, STRONG: 1}
    return max(rows, key=lambda r: (order.get(r.get("evidence_level"), 0), r.get("confidence") or 0))


def _chain_confirms(capability_id: str, chain: list[dict]) -> bool:
    return any(
        (r.get("potential_otech_capability") or {}).get("capability_id") == capability_id
        and r.get("evidence_level") == DIRECT
        and r.get("epistemic_status") == CONFIRMED
        and (r.get("evidence_category") in ACCOUNT_EVENT_CATEGORIES or r.get("evidence_category") is None)
        for r in chain
    )


def split_opportunities(
    opportunities: list[dict[str, Any]], report: dict[str, Any]
) -> tuple[list[ExecutiveOpportunity], list[ExecutiveOpportunity]]:
    chain = report.get("chain") or []
    org = report.get("organization_name") or ""
    country = report.get("country")
    chain_by_url: dict[str, list[dict]] = {}
    for r in chain:
        chain_by_url.setdefault(r.get("source_url") or "", []).append(r)
    confirmed: list[ExecutiveOpportunity] = []
    hypotheses: list[ExecutiveOpportunity] = []
    for o in opportunities or []:
        level = o.get("evidence_level")
        if level not in OPPORTUNITY_LEVELS:
            continue  # weak relevance is never an opportunity
        cid = o.get("capability_id") or ""
        row = _supporting_row(cid, chain)
        claims_confirmed = o.get("epistemic_status") == CONFIRMED and level == DIRECT
        is_confirmed = claims_confirmed and _chain_confirms(cid, chain)
        why_parts = []
        if row and row.get("business_impact"):
            why_parts.append(row["business_impact"].rstrip(".") + ".")
        signal_type = ((row or {}).get("customer_signal") or {}).get("signal_type")
        if signal_type in SIGNAL_LABELS:
            why_parts.append(f"Grounded in a {SIGNAL_LABELS[signal_type]} signal.")
        if not why_parts:
            why_parts.append(o.get("rationale") or o.get("hypothesis") or "")
        # Never fall back to an industry/context source as an opportunity's
        # evidence link.
        source_urls = [
            u for u in (o.get("source_urls") or [])
            if not _is_context_source(u, ((chain_by_url.get(u) or [{}])[0]).get("source_title") or "", chain_by_url, org, country)
        ]
        item = ExecutiveOpportunity(
            capability_id=cid,
            capability_name=o.get("capability_name") or cid,
            confirmed=is_confirmed,
            evidence_level=level if (is_confirmed or level != DIRECT) else STRONG,
            epistemic_status=CONFIRMED if is_confirmed else INFERENCE,
            confidence=float(o.get("confidence") or 0.0),
            priority=o.get("priority") or "",
            why=" ".join(p for p in why_parts if p),
            evidence_category=(row or {}).get("evidence_category"),
            evidence_excerpt=clean_excerpt((row or {}).get("evidence_excerpt") or (row or {}).get("supporting_context")),
            source_title=(row or {}).get("source_title") or "",
            source_url=(row or {}).get("source_url") or (source_urls[0] if source_urls else ""),
            source_count=int(o.get("independent_source_count") or len(source_urls)),
            downgraded=claims_confirmed and not is_confirmed,
        )
        (confirmed if is_confirmed else hypotheses).append(item)
    key = lambda x: -x.confidence  # noqa: E731
    return sorted(confirmed, key=key), sorted(hypotheses, key=key)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

def select_stakeholders(
    report: dict[str, Any], focus_capabilities: list[str], limit: int = MAX_STAKEHOLDERS
) -> tuple[list[Stakeholder], int | None]:
    """Existing People Intelligence only. People whose organization
    association is unconfirmed are never suggested. Preference: confirmed
    association, relevance to the focus opportunity capabilities, then the
    People module's own relevance score and seniority."""
    if "people" not in report:
        return [], None
    eligible = [p for p in report.get("people") or [] if p.get("organization_association") in ("confirmed", "weak")]

    def match(p: dict) -> str | None:
        areas = set(p.get("functions") or []) | set(p.get("technology_areas") or [])
        for cid in focus_capabilities:
            if areas & CAPABILITY_PEOPLE_AFFINITY.get(cid, set()):
                return cid
        return None

    ranked = sorted(
        eligible,
        key=lambda p: (
            p.get("organization_association") == "confirmed",
            match(p) is not None,
            p.get("relevance_score") or 0,
            _SENIORITY_RANK.get(p.get("seniority") or "", 0),
            p.get("confidence") or 0,
        ),
        reverse=True,
    )
    return [Stakeholder(person=p, relevant_capability=match(p)) for p in ranked[:limit]], len(eligible)


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def build_summary_sentences(es: ExecutiveSummary, report: dict[str, Any]) -> list[str]:
    org = es.organization_name or "This account"
    out: list[str] = []
    # 1 -- what is happening
    if es.meaningful_signal_count:
        labels: list[str] = []
        for k in es.key_signals:
            if k.label not in labels:
                labels.append(k.label)
        out.append(
            f"Public evidence shows {_plural(es.meaningful_signal_count, 'account-specific signal')} for {org}, "
            f"led by {_join(labels[:3])} signals."
        )
    else:
        out.append(f"The public evidence collected does not yet show account-specific signals for {org}.")
    # 2 -- strongest signal
    if es.key_signals:
        top = es.key_signals[0]
        source = f" ({top.source_title})" if top.source_title else ""
        out.append(f"The strongest is a {top.label} signal{source}: “{top.excerpt}”")
    # 3 -- what matters to Otech
    if es.confirmed_opportunities:
        names = [o.capability_name for o in es.confirmed_opportunities]
        line = f"{_join(names)} {'is' if len(names) == 1 else 'are'} backed by confirmed account evidence"
        if es.hypothesis_opportunities:
            line += f"; {_join([o.capability_name for o in es.hypothesis_opportunities])} remain sales hypotheses to validate"
        out.append(line + ".")
    elif es.hypothesis_opportunities:
        names = [o.capability_name for o in es.hypothesis_opportunities]
        out.append(
            f"No Otech opportunity is confirmed yet; {_join(names)} "
            f"{'is a sales hypothesis' if len(names) == 1 else 'are sales hypotheses'} to validate with the account."
        )
    else:
        out.append("No Otech opportunity clears the evidence bar yet; see DECIDE for the recommended next step.")
    # 4 -- context kept out of account evidence
    if es.context_source_count:
        out.append(
            f"{_plural(es.context_source_count, 'industry or context source')} "
            f"{'was' if es.context_source_count == 1 else 'were'} kept as background only and "
            "not treated as account evidence."
        )
    # 5 -- people
    if es.stakeholders:
        p = es.stakeholders[0].person
        who = stakeholder_name(p) or "an identified stakeholder"
        clean_title = stakeholder_title(p, report)
        title = f", {clean_title}" if clean_title else ""
        out.append(f"{_plural(es.stakeholder_count or 0, 'relevant stakeholder')} identified; start with {who}{title}.")
    elif es.stakeholder_count == 0:
        out.append("No stakeholders with a confirmed or likely link to the account have been identified yet.")
    return out[:5]


def build_conversation(es: ExecutiveSummary) -> tuple[str, str]:
    """One conversation starter anchored on the strongest relevant signal.
    Returns (starter, basis)."""
    if not es.key_signals:
        return "", "No account-specific signal is strong enough to anchor a conversation yet."
    sig = es.key_signals[0]
    focus = (es.confirmed_opportunities or es.hypothesis_opportunities or [None])[0]
    if focus:
        starter = (
            f"We saw {es.organization_name}'s recent {sig.label} activity. "
            f"How is it shaping your {focus.capability_name} priorities over the next 12 months?"
        )
        basis = (
            f"Anchored on the {sig.label} signal ({sig.strength}); "
            + ("supports a confirmed opportunity." if focus.confirmed else "opens a sales hypothesis to validate, not a confirmed requirement.")
        )
    else:
        starter = (
            f"We saw {es.organization_name}'s recent {sig.label} activity. "
            "What technology changes does it bring for your team?"
        )
        basis = f"Anchored on the {sig.label} signal ({sig.strength}); discovery conversation, no opportunity asserted."
    return starter, basis


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_executive_summary(report: dict[str, Any], opportunities: list[dict[str, Any]] | None = None) -> ExecutiveSummary:
    """Build the executive view for one account from an existing report dict
    (AccountRadarReport.to_dict()) and its opportunity hypotheses."""
    report = report or {}
    opportunities = opportunities if opportunities is not None else report.get("opportunity_hypotheses", [])
    org = report.get("organization_name") or ""
    country = report.get("country")

    key_signals, meaningful, background_count = _rank_signals(report)
    confirmed, hypotheses = split_opportunities(opportunities, report)
    focus = [o.capability_id for o in confirmed + hypotheses]
    stakeholders, stakeholder_count = select_stakeholders(report, focus)

    chain = report.get("chain") or []
    chain_by_url: dict[str, list[dict]] = {}
    for row in chain:
        chain_by_url.setdefault(row.get("source_url") or "", []).append(row)
    context_sources = {
        url for url, rows in chain_by_url.items()
        if url and _is_context_source(url, rows[0].get("source_title") or "", chain_by_url, org, country)
    }

    shown = confirmed + hypotheses
    es = ExecutiveSummary(
        organization_name=org,
        meaningful_signal_count=meaningful,
        opportunity_count=len(shown),
        high_confidence_count=sum(1 for o in confirmed if o.confidence >= HIGH_CONFIDENCE_THRESHOLD),
        stakeholder_count=stakeholder_count,
        key_signals=key_signals,
        confirmed_opportunities=confirmed,
        hypothesis_opportunities=hypotheses,
        stakeholders=stakeholders,
        context_source_count=len(context_sources),
        background_signal_count=background_count,
    )
    es.summary_sentences = build_summary_sentences(es, report)
    es.conversation, es.conversation_basis = build_conversation(es)
    return es

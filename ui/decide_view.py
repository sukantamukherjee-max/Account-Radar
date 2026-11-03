"""DECIDE v2 -- the sales-judgment layer between UNDERSTAND and ACT.

    "What should Otech do with this account right now?"

`build_decision(entry)` produces exactly one of four decision states from
the outputs the pipeline already produced -- it is not a second evidence or
scoring engine:

  * account developments  -> ui.account_signals (normalized, deduplicated)
  * opportunity authority -> Opportunity Intelligence (the entry's
                             opportunity hypotheses), qualified by the
                             existing chain confirmation check
                             (ui.executive_summary.split_opportunities)
  * capability posture,
    buying context        -> ui.understand_view (deterministic)
  * stakeholders          -> People Intelligence (structured fields only)

Decision rules (deterministic, strongest evidence first):

  PURSUE     a CONFIRMED opportunity exists: Opportunity Intelligence
             qualified it AND the evidence chain confirms it with direct,
             account-event evidence. Nothing else can produce PURSUE --
             not weak relevance, industry context, a leadership change, a
             stakeholder's technology mention, or a capability mapping.
  VALIDATE   no confirmed opportunity, but a plausible Otech-relevant
             validation path exists: an opportunity hypothesis (strong
             inference), an account-specific technology / procurement
             development, account evidence of need for a capability, OR an
             account-specific strategic development (leadership,
             expansion, investment, partnership) together with a
             technology-relevant stakeholder whose link to the account is
             CONFIRMED.
  MONITOR    account-specific developments (or news reported about the
             account on another site) exist, but none of the above.
  NO ACTION  no account-specific development at all. Industry context is
             background only and never moves the decision.

The builder never invents a person, an opportunity, a requirement or a
fact: every sentence is templated from labels, counts and excerpts already
in the report, and every reason carries its source when one exists.
"""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ui import account_signals as sig
from ui.executive_summary import CAPABILITY_PEOPLE_AFFINITY, split_opportunities
from ui.understand_view import (
    AREA_LABELS,
    SENIORITY_LABELS,
    STATUS_EVIDENCE,
    UNKNOWN,
    build_understand_view,
    clean_excerpt,
    clean_person_title,
    clean_text,
)

PURSUE, VALIDATE, MONITOR, NO_ACTION = "PURSUE", "VALIDATE", "MONITOR", "NO ACTION"
DECISION_STATES = (PURSUE, VALIDATE, MONITOR, NO_ACTION)
HIGH, MEDIUM, LOW = "High", "Medium", "Low"
HIGH_CONFIDENCE_OPPORTUNITY = 75.0
MAX_REASONS = 4
MAX_QUESTIONS = 3
MAX_FACTS_PER_GROUP = 3

STRATEGIC_TYPES = {"leadership_change", "expansion", "investment", "partnership"}
TECH_FUNCTIONS = {"it", "infrastructure", "cybersecurity", "cloud", "network", "data", "digital_transformation",
                  "applications", "ai", "data_center"}
_SENIORITY_RANK = {"c_level": 4, "director": 3, "head": 3, "manager": 2}
_INFLUENCE_RANK = {
    "decision_maker": 4, "potential_decision_maker": 3, "technical_stakeholder": 3,
    "likely_technical_stakeholder": 2, "procurement_stakeholder": 1, "business_stakeholder": 0,
}
_INFLUENCE_LABELS = {
    "decision_maker": "decision maker", "potential_decision_maker": "potential decision maker",
    "technical_stakeholder": "technical stakeholder", "likely_technical_stakeholder": "likely technical stakeholder",
    "procurement_stakeholder": "procurement stakeholder", "business_stakeholder": "business stakeholder",
}
# Plain-language area words for questions and actions.
_AREA_WORDS = {
    "infrastructure": "infrastructure", "cybersecurity": "cybersecurity", "cloud": "cloud",
    "data_center": "data-centre", "network": "network", "digital_transformation": "digital-transformation",
    "it": "IT", "ai": "AI", "data": "data", "applications": "applications",
}
_CAPABILITY_FOR_AREA = {
    "cybersecurity": "cybersecurity_resilience", "cloud": "cloud_ai_infrastructure", "ai": "cloud_ai_infrastructure",
    "data": "cloud_ai_infrastructure", "data_center": "data_centre_infrastructure",
    "infrastructure": "data_centre_infrastructure", "network": "network_connectivity",
    "it": "enterprise_managed_services", "digital_transformation": "enterprise_managed_services",
}
# The capability's own area: a person working in it aligns more closely than
# one in a merely related area (CAPABILITY_PEOPLE_AFFINITY).
_CAPABILITY_PRIMARY_AREA = {
    "cybersecurity_resilience": "cybersecurity", "cloud_ai_infrastructure": "cloud",
    "data_centre_infrastructure": "data_center", "network_connectivity": "network",
    "enterprise_managed_services": "it",
}
_TRIGGER_NOUNS = {
    "leadership_change": "the leadership change", "expansion": "the reported expansion",
    "investment": "the reported investment", "partnership": "the reported partnership",
}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class Fact:
    text: str
    source_url: str = ""
    source_label: str = ""
    detail: str = ""            # e.g. evidence level / association
    anchor: str = ""            # in-page anchor (opportunity board), when linkable


@dataclass
class EngageStakeholder:
    person_id: str
    name: str
    title: str
    why: str
    association: str
    linkedin_url: str = ""
    areas: list[str] = field(default_factory=list)


@dataclass
class Decision:
    organization_name: str
    state: str
    headline: str
    confidence: str
    confidence_reason: str
    reasons: list[Fact] = field(default_factory=list)
    known: dict[str, list[Fact]] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    stakeholder: EngageStakeholder | None = None
    next_action: str = ""
    trigger: Fact | None = None
    opportunity_count: int = 0
    confirmed_opportunity_count: int = 0
    account_key: str = ""         # the report's organization_name, verbatim (ACT matches on it)
    trigger_type: str = ""        # signal type of the triggering development (handoff only)
    trigger_source_count: int = 0 # independent sources reporting that type of development (handoff only)

    def handoff(self) -> dict[str, Any]:
        """The decision context ACT receives (no research is re-run)."""
        evidence = [asdict(f) for group in self.known.values() for f in group if f.source_url]
        return {
            "source": "DECIDE",
            "account_key": self.account_key,
            "organization_name": self.organization_name,
            "decision": self.state,
            "headline": self.headline,
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
            "reasons": [asdict(r) for r in self.reasons],
            "validation_questions": list(self.questions),
            "next_action": self.next_action,
            "stakeholder": asdict(self.stakeholder) if self.stakeholder else None,
            "evidence": evidence,
            "unknown": list(self.unknown),
            # ACT compatibility (additive): the trigger, so ACT can explain
            # "why now" without recomputing anything.
            "trigger": asdict(self.trigger) if self.trigger else None,
            "trigger_type": self.trigger_type,
            "trigger_source_count": self.trigger_source_count,
            "opportunity_count": self.opportunity_count,
            "confirmed_opportunity_count": self.confirmed_opportunity_count,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _domain(url: str) -> str:
    return sig.domain(url)


def _join(items: list[str], word: str = "and") -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {word} " + items[-1]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _people(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in report.get("people") or [] if isinstance(p, dict) and p.get("person_id") and p.get("name")]


def _person_areas(person: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for a in list(person.get("functions") or []) + list(person.get("technology_areas") or []):
        if a in TECH_FUNCTIONS and a not in out:
            out.append(a)
    return out


def _is_technology_relevant(person: dict[str, Any]) -> bool:
    """A stakeholder who can carry a technology validation conversation:
    confirmed link, a technology function/area, and a senior or technical
    role. Weak / unconfirmed links never qualify."""
    return (
        person.get("organization_association") == "confirmed"
        and bool(_person_areas(person))
        and (_SENIORITY_RANK.get(person.get("seniority") or "", 0) >= 2
             or person.get("influence_type") in ("technical_stakeholder", "decision_maker", "potential_decision_maker"))
    )


# ---------------------------------------------------------------------------
# Stakeholder selection
# ---------------------------------------------------------------------------

def select_stakeholder(report: dict[str, Any], focus_capabilities: list[str], state: str) -> EngageStakeholder | None:
    """The strongest relevant stakeholder from People Intelligence only.
    Order: confirmed association, alignment with the decision's capability
    focus, technology relevance, influence, seniority, the People module's
    own relevance score and confidence, then name (deterministic)."""
    org = report.get("organization_name") or ""
    country = report.get("country")
    eligible = [p for p in _people(report) if p.get("organization_association") in ("confirmed", "weak")]
    if not eligible:
        return None
    focus_areas: set[str] = set()
    primary_areas = {_CAPABILITY_PRIMARY_AREA[c] for c in focus_capabilities if c in _CAPABILITY_PRIMARY_AREA}
    for cid in focus_capabilities:
        focus_areas |= CAPABILITY_PEOPLE_AFFINITY.get(cid, set())

    def key(p: dict[str, Any]):
        areas = set(_person_areas(p))
        procurement = "procurement" in (p.get("functions") or [])
        alignment = 2 if areas & primary_areas else (1 if areas & focus_areas else 0)
        return (
            p.get("organization_association") == "confirmed",
            alignment,
            bool(areas),
            procurement and state == PURSUE,
            _INFLUENCE_RANK.get(p.get("influence_type") or "", 0),
            _SENIORITY_RANK.get(p.get("seniority") or "", 0),
            p.get("relevance_score") or 0,
            p.get("confidence") or 0,
        )

    best = sorted(eligible, key=lambda p: (key(p), clean_text(p.get("name"))), reverse=True)[0]
    return _stakeholder_from(best, org, country, focus_areas)


def _stakeholder_from(p: dict[str, Any], org: str, country: str | None, focus_areas: set[str]) -> EngageStakeholder:
    areas = _person_areas(p)
    title = clean_person_title(p, org, country)
    parts: list[str] = []
    seniority = SENIORITY_LABELS.get(p.get("seniority") or "")
    functions = [AREA_LABELS.get(f, f.replace("_", " ")) for f in p.get("functions") or []]
    tech = [AREA_LABELS.get(t, t.replace("_", " ")) for t in p.get("technology_areas") or []]
    if seniority and functions:
        parts.append(f"{seniority} in {_join([f.lower() if f not in ('IT', 'AI') else f for f in functions])}")
    elif seniority:
        parts.append(f"{seniority}-level role")
    elif functions:
        parts.append(f"works in {_join([f.lower() if f not in ('IT', 'AI') else f for f in functions])}")
    if tech:
        parts.append(f"profile evidence covers {_join([t.lower() if t not in ('IT', 'AI') else t for t in tech])}")
    assoc = p.get("organization_association")
    parts.append("confirmed link to the account" if assoc == "confirmed" else "likely (unconfirmed) link to the account")
    if p.get("influence_type") in _INFLUENCE_LABELS:
        parts.append(f"classified as a {_INFLUENCE_LABELS[p['influence_type']]}")
    why = "; ".join(parts) + "."
    if focus_areas and set(areas) & focus_areas:
        why += " Aligned with the decision's capability focus."
    return EngageStakeholder(
        person_id=str(p.get("person_id")), name=clean_text(p.get("name")), title=title,
        why=why[0].upper() + why[1:], association=assoc or "", linkedin_url=p.get("linkedin_url") or "",
        areas=areas,
    )


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

def _fact_from_signal(k: sig.KeySignal) -> Fact:
    return Fact(
        text=clean_excerpt(k.excerpt, 170), source_url=k.source_url,
        source_label=clean_text(k.source_title) or _domain(k.source_url),
        detail=f"{k.label} · {k.relationship_label or 'Account-specific'} · {k.evidence_label or ''}".strip(" ·"),
    )


def _corroborating_domains(devs: list[sig.KeySignal], signal_type: str) -> list[str]:
    out: list[str] = []
    for k in devs:
        if k.signal_type == signal_type and _domain(k.source_url) not in out:
            out.append(_domain(k.source_url))
    return out


def _area_words(areas: list[str], fallback: str = "technology") -> str:
    words = []
    for a in areas:
        w = _AREA_WORDS.get(a)
        if w and w not in words:
            words.append(w)
    return _join(words[:3], "or") if words else fallback


def build_decision(entry: dict[str, Any]) -> Decision:
    entry = entry or {}
    report = entry.get("report") or {}
    org = clean_text(report.get("organization_name")) or "This account"
    opportunities = entry.get("opportunity_hypotheses")
    if opportunities is None:
        opportunities = report.get("opportunity_hypotheses") or []

    signal_set = sig.normalize_account_signals(report)
    ranked = signal_set.ranked
    tech_proc = [k for k in ranked if k.signal_class == "technology_procurement"]
    strategic = [k for k in ranked if k.signal_class == "strategic"]
    general = [k for k in ranked if k.signal_class == "general"]
    confirmed, hypotheses = split_opportunities(opportunities, report)
    understand = build_understand_view({"report": report, "ai_synthesis": {}})
    need_caps = [p for p in understand.posture if p.status == STATUS_EVIDENCE]
    buying = {b.key: b for b in understand.buying}
    people = _people(report)
    tech_people = [p for p in people if _is_technology_relevant(p)]

    # ---- state ----------------------------------------------------------
    trigger: sig.KeySignal | None = None
    focus_caps: list[str] = []
    if confirmed:
        state = PURSUE
        focus_caps = [o.capability_id for o in confirmed]
    elif hypotheses or tech_proc or need_caps:
        state = VALIDATE
        focus_caps = [o.capability_id for o in hypotheses] + [p.capability_id for p in need_caps]
        trigger = tech_proc[0] if tech_proc else None
    elif strategic and tech_people:
        state = VALIDATE
        trigger = strategic[0]
    elif ranked or signal_set.reported_news:
        state = MONITOR
        trigger = (strategic or general or signal_set.reported_news)[0]
    else:
        state = NO_ACTION

    stakeholder = select_stakeholder(report, focus_caps, state) if state != NO_ACTION else None
    if state == VALIDATE and not focus_caps and stakeholder and not _is_technology_relevant(
            next((p for p in people if str(p.get("person_id")) == stakeholder.person_id), {})):
        # The validation path needs a technology-relevant person; prefer one.
        best_tech = sorted(tech_people, key=lambda p: (
            _INFLUENCE_RANK.get(p.get("influence_type") or "", 0), _SENIORITY_RANK.get(p.get("seniority") or "", 0),
            p.get("relevance_score") or 0), reverse=True)
        if best_tech:
            stakeholder = _stakeholder_from(best_tech[0], report.get("organization_name") or "", report.get("country"), set())

    d = Decision(organization_name=org, state=state, headline="", confidence=LOW, confidence_reason="",
                 stakeholder=stakeholder, trigger=_fact_from_signal(trigger) if trigger else None,
                 opportunity_count=len(confirmed) + len(hypotheses), confirmed_opportunity_count=len(confirmed),
                 account_key=report.get("organization_name") or "")
    if trigger is not None:
        d.trigger_type = trigger.signal_type
        d.trigger_source_count = len(_corroborating_domains(ranked + signal_set.reported_news, trigger.signal_type))
    areas = stakeholder.areas if stakeholder else []
    area_text = _area_words(areas)
    who = f"{stakeholder.name} ({stakeholder.title})" if stakeholder and stakeholder.title else (
        stakeholder.name if stakeholder else "the account's technology leadership")

    # ---- headline, confidence, reasons, action, questions ---------------
    if state == PURSUE:
        top = confirmed[0]
        d.headline = (f"Confirmed account evidence supports a {top.capability_name} opportunity "
                      f"(direct evidence, {top.confidence:.0f}% confidence).")
        strong = top.confidence >= HIGH_CONFIDENCE_OPPORTUNITY and top.source_count >= 2
        d.confidence = HIGH if strong else MEDIUM
        d.confidence_reason = (
            f"Opportunity Intelligence qualifies it on direct, account-specific evidence"
            + (f" from {_plural(top.source_count, 'independent source')}." if top.source_count else ".")
            + ("" if strong else " A single source or sub-75% confidence keeps this at Medium.")
        )
        d.reasons.append(Fact(f"{top.capability_name}: confirmed opportunity ({top.confidence:.0f}% confidence)",
                              top.source_url, top.source_title or _domain(top.source_url), "Direct evidence",
                              anchor=f"ar-opp-{top.capability_id}"))
        if tech_proc:
            d.reasons.append(Fact(f"Account-specific {tech_proc[0].label}: {clean_excerpt(tech_proc[0].excerpt, 120)}",
                                  tech_proc[0].source_url, _domain(tech_proc[0].source_url)))
        d.next_action = (f"Engage {who} to qualify the {top.capability_name} opportunity: confirm scope, budget, "
                         "timeline and procurement route.")
        d.questions.append(f"What is the procurement route, timeline and budget for {top.capability_name}, and who signs off?")
        if top.evidence_excerpt:
            d.questions.append(f"How far has the work described as “{clean_excerpt(top.evidence_excerpt, 90)}” progressed, "
                               "and which requirements are still open?")
    elif state == VALIDATE:
        if hypotheses and not tech_proc:
            h = hypotheses[0]
            d.headline = (f"Evidence suggests a possible {h.capability_name} requirement (strong inference), "
                          "which needs confirmation before it can be pursued.")
            d.reasons.append(Fact(f"{h.capability_name}: opportunity hypothesis ({h.confidence:.0f}% confidence), not confirmed",
                                  h.source_url, h.source_title or _domain(h.source_url), "Strong inference",
                                  anchor=f"ar-opp-{h.capability_id}"))
            d.next_action = f"Validate the {h.capability_name} hypothesis with {who} before treating it as an opportunity."
            d.questions.append(f"Is {h.capability_name} an active requirement, and is there a defined evaluation, "
                               "budget or timeline for it?")
        elif tech_proc:
            k = tech_proc[0]
            d.headline = (f"An account-specific {k.label} points to a possible requirement, but its Otech relevance "
                          "and buying process still need confirmation.")
            d.next_action = f"Validate the {k.label} with {who}: scope, timing and decision owner."
            d.questions.append(f"What is the scope and timeline of the {k.label} (“{clean_excerpt(k.excerpt, 90)}”), "
                               "and who owns the decision?")
        elif need_caps:
            cap = need_caps[0]
            d.headline = (f"Account evidence points to a {cap.capability_name} need, but no opportunity is qualified "
                          "yet and the buying requirement needs confirmation.")
            d.next_action = f"Validate the {cap.capability_name} need with {who} before treating it as an opportunity."
            d.questions.append(f"Is {cap.capability_name} an active requirement, and is there a defined evaluation, "
                               "budget or timeline for it?")
        else:
            k = trigger
            noun = _TRIGGER_NOUNS.get(k.signal_type, f"the reported {k.label}")
            lead = "Recent leadership change" if k.signal_type == "leadership_change" else noun[0].upper() + noun[1:]
            d.headline = (f"{lead} creates a reasonable discovery point, but available evidence does not establish an "
                          "Otech-specific technology or procurement requirement.")
            if k.signal_type == "leadership_change":
                plural = "changes" if len([x for x in strategic if x.signal_type == "leadership_change"]) > 1 else "change"
                d.questions.append(f"Have the recent leadership {plural} altered your {area_text} priorities "
                                   "for the next 12–18 months?")
            else:
                party = sig.explicit_counterparty(k.raw_text or k.excerpt, report.get("organization_name") or "",
                                                  report.get("country"))
                subject = f"the {party} partnership" if party else noun
                d.questions.append(f"Which {area_text} requirements does {subject} create, and on what timeline?")
            d.next_action = (f"Schedule a discovery conversation with {who} to validate {area_text} priorities "
                             f"following {noun}.")
        # confidence for VALIDATE
        if hypotheses or any(k.evidence_level in (sig.DIRECT, sig.STRONG) for k in tech_proc) or need_caps:
            d.confidence = MEDIUM
            d.confidence_reason = "The validation path rests on direct or strong-inference evidence, but no opportunity is confirmed."
        else:
            basis = trigger or (tech_proc[0] if tech_proc else None)
            domains = _corroborating_domains(ranked, basis.signal_type) if basis else []
            if len(domains) >= 2:
                d.confidence = MEDIUM
                d.confidence_reason = (f"The {basis.label} is reported by {_plural(len(domains), 'independent source')}; "
                                       "Otech relevance is not yet evidenced by the account.")
            else:
                d.confidence = LOW
                d.confidence_reason = (f"A single source reports the {basis.label if basis else 'development'}, "
                                       "and Otech relevance is not yet evidenced by the account.")
    elif state == MONITOR:
        k = trigger
        noun = _TRIGGER_NOUNS.get(k.signal_type, f"the reported {k.label}")
        lead = noun[0].upper() + noun[1:]
        reported = k.provenance == sig.REPORTED_NEWS
        missing = ("a technology or procurement development" if not tech_people
                   else "an Otech-relevant requirement")
        d.headline = (f"{lead} {'was reported on another site' if reported else 'has been reported'}, but there is "
                      f"currently no {missing} or technology-relevant stakeholder to engage on it."
                      if not tech_people else
                      f"{lead} has been reported, but it does not currently create an actionable Otech requirement.")
        domains = _corroborating_domains(ranked + signal_set.reported_news, k.signal_type)
        d.confidence = MEDIUM if len(ranked) >= 2 and len({_domain(x.source_url) for x in ranked}) >= 2 else LOW
        d.confidence_reason = (
            f"{_plural(len(ranked), 'account-specific development')} found"
            + (f" across {_plural(len({_domain(x.source_url) for x in ranked}), 'source')}" if ranked else "")
            + "; none gives a validation path today."
        )
        d.next_action = (f"Monitor the account and revisit when {noun} leads to a reported technology, "
                         "infrastructure or procurement initiative.")
        d.questions.append(f"Does {noun} lead to an announced technology, infrastructure or procurement initiative?")
        _ = domains
    else:
        q = report.get("qualification") or {}
        qualified = int(q.get("qualified_count") or 0)
        d.headline = "The public evidence shows no account-specific development that currently warrants sales action."
        insufficient = (report.get("status") or q.get("corpus_status")) == "insufficient_evidence"
        d.confidence = LOW if insufficient or qualified < 5 else (HIGH if qualified >= 20 else MEDIUM)
        d.confidence_reason = (
            "The research corpus is thin, so a development may have been missed." if d.confidence == LOW
            else f"{_plural(qualified, 'qualified source')} were reviewed and none describes an account-specific development."
        )
        d.next_action = "No sales action now; re-run research when new account developments are expected."

    # ---- reasons (2-4, traceable) ----------------------------------------
    if state in (VALIDATE, MONITOR) and trigger:
        domains = _corroborating_domains(ranked, trigger.signal_type)
        what = f"Account-specific {trigger.label}" if trigger.provenance != sig.REPORTED_NEWS else f"Reported {trigger.label} (another site)"
        if len(domains) >= 2:
            what += f", reported by {_plural(len(domains), 'source')}"
        d.reasons.insert(0, Fact(what, trigger.source_url, _domain(trigger.source_url),
                                 f"{trigger.relationship_label} · {trigger.evidence_label}"))
    if stakeholder and state in (PURSUE, VALIDATE):
        title = f", {stakeholder.title}" if stakeholder.title else ""
        assoc = "confirmed" if stakeholder.association == "confirmed" else "likely"
        d.reasons.append(Fact(f"Relevant stakeholder identified: {stakeholder.name}{title} ({assoc} association)",
                              stakeholder.linkedin_url, "People Intelligence"))
    if state in (VALIDATE, MONITOR) and not tech_proc:
        d.reasons.append(Fact("No direct technology or procurement event is currently established for the account"))
    if state in (VALIDATE, MONITOR) and not need_caps:
        chain = report.get("chain") or []
        if chain:
            d.reasons.append(Fact(f"Existing capability evidence is contextual rather than account-specific "
                                  f"({_plural(len(chain), 'mapping')}, none is account evidence of need)"))
    if state == MONITOR and not tech_people:
        d.reasons.append(Fact("No technology-relevant stakeholder with a confirmed link to the account was identified"))
    if state == NO_ACTION:
        q = report.get("qualification") or {}
        d.reasons.append(Fact(f"No account-specific development in {_plural(int(q.get('qualified_count') or 0), 'qualified source')}"))
        context = len({r.get('source_url') for r in report.get("chain") or [] if r.get("evidence_category") == "industry_context"})
        if context:
            d.reasons.append(Fact(f"{_plural(context, 'industry or context source')} kept as background only"))
        if not opportunities:
            d.reasons.append(Fact("Opportunity Intelligence qualifies no opportunity"))
    d.reasons = d.reasons[:MAX_REASONS]

    # ---- what we know -----------------------------------------------------
    known: dict[str, list[Fact]] = {}
    seen: set[str] = set()

    def add(group: str, fact: Fact) -> None:
        key = (fact.text or "").lower()
        if key in seen:
            return
        seen.add(key)
        known.setdefault(group, []).append(fact)

    for k in strategic[:MAX_FACTS_PER_GROUP]:
        add("Strategic developments", _fact_from_signal(k))
    for k in tech_proc[:MAX_FACTS_PER_GROUP]:
        add("Technology / procurement developments", _fact_from_signal(k))
    rel = [p for p in tech_people if not stakeholder or str(p.get("person_id")) != stakeholder.person_id]
    if stakeholder:
        add("Relevant stakeholders", Fact(f"{stakeholder.name}" + (f" — {stakeholder.title}" if stakeholder.title else ""),
                                          stakeholder.linkedin_url, "LinkedIn", stakeholder.association))
    for p in sorted(rel, key=lambda p: (_SENIORITY_RANK.get(p.get("seniority") or "", 0), p.get("relevance_score") or 0),
                    reverse=True)[:1]:
        t = clean_person_title(p, report.get("organization_name") or "", report.get("country"))
        add("Relevant stakeholders", Fact(clean_text(p.get("name")) + (f" — {t}" if t else ""), p.get("linkedin_url") or "",
                                          "LinkedIn", p.get("organization_association") or ""))
    for row in report.get("chain") or []:
        if (row.get("evidence_level") == sig.DIRECT and row.get("epistemic_status") == sig.CONFIRMED
                and row.get("evidence_category") in sig.ACCOUNT_EVENT_CATEGORIES):
            cap = (row.get("potential_otech_capability") or {}).get("capability_name") or ""
            add("Confirmed account evidence", Fact(f"{cap}: {clean_excerpt(row.get('evidence_excerpt') or row.get('supporting_context'), 140)}",
                                                   row.get("source_url") or "", _domain(row.get("source_url") or ""), "Direct evidence"))
            if len(known.get("Confirmed account evidence", [])) >= MAX_FACTS_PER_GROUP:
                break
    for o in confirmed + hypotheses:
        add("Existing opportunity evidence", Fact(
            f"{o.capability_name}: {'confirmed opportunity' if o.confirmed else 'sales hypothesis'} ({o.confidence:.0f}% confidence)",
            o.source_url, _domain(o.source_url), "Direct evidence" if o.confirmed else "Strong inference",
            anchor=f"ar-opp-{o.capability_id}"))
    d.known = known

    # ---- what we don't know (decision-relevant, never contradicting data) --
    if state != NO_ACTION:
        if not tech_proc and not confirmed and not hypotheses:
            after = f" following {_TRIGGER_NOUNS[trigger.signal_type]}" if trigger and trigger.signal_type in _TRIGGER_NOUNS else ""
            d.unknown.append(f"Technology priorities{after}: no account-stated technology or procurement priority in the evidence.")
        if not any(k.signal_type in ("project", "technology_initiative", "capacity_change") for k in tech_proc):
            d.unknown.append("Active projects: no account-specific technology project is reported.")
        if buying.get("timeline") and buying["timeline"].status == UNKNOWN:
            d.unknown.append("Procurement timing: no deadline or delivery timeline is stated.")
        if buying.get("budget") and buying["budget"].status == UNKNOWN:
            d.unknown.append("Budget: no budget or contract value is stated.")
        if buying.get("procurement_channels") and buying["procurement_channels"].status == UNKNOWN:
            dm = buying.get("decision_makers")
            d.unknown.append("Decision process: " + (
                "senior roles are identified, but the procurement route and decision owner are not."
                if dm and dm.status != UNKNOWN else "no procurement route or decision owner is established."))
        if stakeholder and state in (VALIDATE, MONITOR):
            caps = []
            names = {p.capability_id: p for p in understand.posture}
            for a in stakeholder.areas:
                cid = _CAPABILITY_FOR_AREA.get(a)
                if cid in names and names[cid].status != STATUS_EVIDENCE and names[cid].capability_name not in caps:
                    caps.append(names[cid].capability_name)
            if caps:
                d.unknown.append(f"{_join(caps[:3])} roadmap: no account-stated plans in the evidence.")
    # A second, evidence-based question when the trigger is a leadership
    # change and the report does not establish who decides or how buying is routed.
    if (state == VALIDATE and trigger is not None and trigger.signal_type == "leadership_change"
            and buying.get("procurement_channels") and buying["procurement_channels"].status == UNKNOWN):
        d.questions.append(f"Under the new leadership, who owns {area_text} decisions, and how are "
                           "technology purchases routed?")
    d.questions = list(dict.fromkeys(d.questions))[:MAX_QUESTIONS]
    return d


# ---------------------------------------------------------------------------
# Renderer (HTML; styling in theme_engine.py). Interactive controls (Open in
# PEOPLE, Take to ACT) are Streamlit buttons rendered by app.py.
# ---------------------------------------------------------------------------

def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _link(url: str, text: str) -> str:
    if url and re.match(r"https?://", url):
        return f'<a href="{_e(url)}" target="_blank" rel="noopener">{_e(text)} &#8599;</a>'
    return _e(text)


def _fact_html(f: Fact) -> str:
    src = ""
    if f.anchor:
        src = f'<a href="#{_e(f.anchor)}">View opportunity detail &#8595;</a>'
        if f.source_url:
            src += " · " + _link(f.source_url, f.source_label or _domain(f.source_url))
    elif f.source_url:
        src = _link(f.source_url, f.source_label or _domain(f.source_url))
    detail = f'<span class="ar-u-strength">{_e(f.detail)}</span>' if f.detail else ""
    return (
        '<li class="ar-d-fact">'
        f'<div class="ar-d-fact-text">{_e(f.text)}</div>'
        + (f'<div class="ar-u-src">{detail}{" · " if detail and src else ""}{src}</div>' if (detail or src) else "")
        + "</li>"
    )


STATE_CLASS = {PURSUE: "is-pursue", VALIDATE: "is-validate", MONITOR: "is-monitor", NO_ACTION: "is-none"}


def decision_header_html(d: Decision) -> str:
    return (
        '<div class="ar-panel ar-decision">'
        '<div class="ar-exec-label">Current recommendation</div>'
        '<div class="ar-decision-row">'
        f'<span class="ar-decision-state {STATE_CLASS[d.state]}">{_e(d.state)}</span>'
        f'<div class="ar-decision-headline">{_e(d.headline)}</div>'
        '</div>'
        '<div class="ar-decision-confidence">'
        f'<span class="ar-u-tag is-{_e(d.confidence.lower())}">Confidence · {_e(d.confidence)}</span>'
        f'<span class="ar-u-note">{_e(d.confidence_reason)}</span>'
        '</div></div>'
    )


def reasons_html(d: Decision) -> str:
    items = "".join(_fact_html(r) for r in d.reasons)
    return (f'<div class="ar-panel"><div class="ar-panel-head"><div class="ar-panel-title">Why this decision</div></div>'
            f'<ul class="ar-d-list">{items}</ul></div>')


def known_html(d: Decision) -> str:
    if not d.known:
        body = '<div class="ar-exec-empty">No account-specific facts beyond background context.</div>'
    else:
        body = "".join(
            f'<div class="ar-u-block"><div class="ar-exec-label">{_e(group)}</div>'
            f'<ul class="ar-d-list">{"".join(_fact_html(f) for f in facts)}</ul></div>'
            for group, facts in d.known.items()
        )
    return f'<div class="ar-panel"><div class="ar-panel-head"><div class="ar-panel-title">What we know</div></div>{body}</div>'


def unknown_html(d: Decision) -> str:
    if not d.unknown:
        body = '<div class="ar-exec-empty">No decision-relevant gaps: there is no account development to qualify yet.</div>'
    else:
        body = '<ul class="ar-u-list">' + "".join(f"<li>{_e(u)}</li>" for u in d.unknown) + "</ul>"
    return f'<div class="ar-panel"><div class="ar-panel-head"><div class="ar-panel-title">What we don&#x27;t know</div></div>{body}</div>'


def validate_html(d: Decision) -> str:
    title = "What to watch for" if d.state == MONITOR else "What to validate"
    if not d.questions:
        body = '<div class="ar-exec-empty">Nothing to validate until an account development is reported.</div>'
    else:
        body = '<ol class="ar-d-questions">' + "".join(f"<li>{_e(q)}</li>" for q in d.questions) + "</ol>"
    return (f'<div class="ar-panel ar-d-validate"><div class="ar-panel-head"><div class="ar-panel-title">{title}</div></div>'
            f"{body}</div>")


def engage_html(d: Decision) -> str:
    s = d.stakeholder
    if not s:
        body = '<div class="ar-exec-empty">No stakeholder with a confirmed or likely link to the account was identified.</div>'
    else:
        body = (
            f'<div class="ar-u-person-name">{_e(s.name)}</div>'
            + (f'<div class="ar-u-person-title">{_e(s.title)}</div>' if s.title else "")
            + f'<div class="ar-u-note">{_e(s.why)}</div>'
        )
    return f'<div class="ar-panel"><div class="ar-panel-head"><div class="ar-panel-title">Who to engage</div></div>{body}</div>'


def action_html(d: Decision) -> str:
    return (f'<div class="ar-panel ar-d-action"><div class="ar-panel-head"><div class="ar-panel-title">Recommended next action</div></div>'
            f'<div class="ar-decision-headline">{_e(d.next_action)}</div></div>')


def handoff_brief(context: dict[str, Any]) -> str:
    """Plain-text brief ACT builds from the handed-off decision context."""
    lines = [f"{context.get('organization_name', '')} — {context.get('decision', '')} "
             f"(confidence: {context.get('confidence', '')})", "", context.get("headline", ""), ""]
    if context.get("stakeholder"):
        s = context["stakeholder"]
        lines.append(f"Engage: {s.get('name')}" + (f", {s.get('title')}" if s.get("title") else ""))
        lines.append("")
    if context.get("validation_questions"):
        lines.append("Questions to validate:")
        lines += [f"{i}. {q}" for i, q in enumerate(context["validation_questions"], 1)]
        lines.append("")
    if context.get("next_action"):
        lines.append(f"Next action: {context['next_action']}")
        lines.append("")
    if context.get("evidence"):
        lines.append("Evidence:")
        lines += [f"- {e.get('text')} ({e.get('source_url')})" for e in context["evidence"]]
    return "\n".join(lines).strip()

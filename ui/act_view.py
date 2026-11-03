"""ACT v2 -- the execution layer after DECIDE.

    "Given the DECIDE result, what should the salesperson actually do next?"

`build_action_plan(context, report)` turns the DECIDE handoff (the dict
produced by ui.decide_view.Decision.handoff(), carried by "Take to ACT", or
derived directly from DECIDE when ACT is opened without a handoff) into an
action plan. It is deterministic and needs no AI:

  * it never re-runs research and never reads the evidence chain itself;
  * the recommended action, stakeholder, evidence, gaps and validation
    questions are DECIDE's, reused verbatim -- ACT only arranges them for
    execution and adds wording that restates them;
  * it never manufactures urgency and never states inference as fact: no
    project, budget, procurement activity, deployment, timeline,
    responsibility or opportunity appears unless DECIDE carried it.

Status by decision state:
  PURSUE    -> Ready for Outreach                 (advance an evidenced opportunity)
  VALIDATE  -> Discovery Recommended              (validate the unknowns first)
  MONITOR   -> Monitor / No Immediate Outreach    (no outreach draft)
  NO ACTION -> No Action                          (no outreach, no forced follow-up)

Optional AI enrichment (the synthesis' next-best actions) is shown
separately by the app and never replaces any part of this plan.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from ui.decide_view import MONITOR, NO_ACTION, PURSUE, VALIDATE, build_decision

STATUS = {
    PURSUE: "Ready for Outreach",
    VALIDATE: "Discovery Recommended",
    MONITOR: "Monitor / No Immediate Outreach",
    NO_ACTION: "No Action",
}
STATUS_CLASS = {PURSUE: "is-pursue", VALIDATE: "is-validate", MONITOR: "is-monitor", NO_ACTION: "is-none"}
SENDER = "Sukanta"   # same signature the existing outreach draft builder uses

_TRIGGER_PHRASES = {
    "leadership_change": "a recent account-specific leadership change",
    "expansion": "an account-specific expansion",
    "investment": "an account-specific investment",
    "partnership": "an account-specific partnership",
    "technology_initiative": "an account-specific technology initiative",
    "project": "an account-specific project",
    "procurement_activity": "account-specific procurement activity",
    "contract_award": "an account-specific contract award",
    "capacity_change": "an account-specific capacity change",
}
_OUTREACH_OPENERS = {
    "leadership_change": "I saw the recent leadership announcements at {org}",
    "expansion": "I saw the reported expansion at {org}",
    "investment": "I saw the reported investment at {org}",
    "partnership": "I saw the partnership {org} recently announced",
    "technology_initiative": "I saw the recent technology announcement from {org}",
    "project": "I saw the recently reported project at {org}",
    "procurement_activity": "I saw the procurement activity {org} recently published",
    "contract_award": "I saw the recent contract award at {org}",
    "capacity_change": "I saw the recently reported capacity change at {org}",
}
# DECIDE gap -> conversation-objective item, in the order they matter for a
# first discovery conversation. Only gaps DECIDE actually listed are used;
# budget and roadmap detail stay in DECIDE's gap list.
_OBJECTIVE_FROM_GAP = (
    ("Technology priorities", "current {areas} priorities"),
    ("Decision process", "who owns them"),
    ("Active projects", "any active initiatives"),
    ("Procurement timing", "procurement direction"),
)


@dataclass
class EvidenceLink:
    text: str
    source_url: str
    source_label: str = ""
    detail: str = ""


@dataclass
class ActionPlan:
    organization_name: str
    state: str
    status: str
    confidence: str
    handed_over: bool                     # True when it came through "Take to ACT"
    recommended_action: str
    why_now: str
    objective: str
    questions: list[str] = field(default_factory=list)
    outreach_subject: str = ""
    outreach_email: str = ""
    outreach_linkedin: str = ""
    evidence: list[EvidenceLink] = field(default_factory=list)
    stakeholder: dict[str, Any] | None = None
    headline: str = ""
    unknown: list[str] = field(default_factory=list)

    @property
    def outreach_recommended(self) -> bool:
        return bool(self.outreach_email)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def context_for(entry: dict[str, Any], handoff: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """The DECIDE context ACT works from: the handed-over context when it
    belongs to this account, otherwise DECIDE's result derived directly from
    the already-loaded entry (no research)."""
    report = (entry or {}).get("report") or {}
    if handoff and handoff.get("account_key") == (report.get("organization_name") or ""):
        return handoff, True
    opportunities = (entry or {}).get("opportunity_hypotheses")
    if opportunities is None:
        opportunities = report.get("opportunity_hypotheses") or []
    return build_decision({"report": report, "opportunity_hypotheses": opportunities}).handoff(), False


def _first_name(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name or "") if p]
    return parts[0] if parts else "there"


def _short_org(context: dict[str, Any]) -> str:
    name = context.get("organization_name") or "the account"
    try:
        from commercial.evidence_mapping import organization_aliases

        aliases = organization_aliases(context.get("account_key") or name)
        multi = [a for a in aliases[1:] if " " in a]
        return multi[0] if multi else name
    except Exception:  # noqa: BLE001 -- naming must never break ACT
        return name


def _areas_from_questions(questions: list[str]) -> str:
    """The area words DECIDE already used in its first question
    ("... your infrastructure, cloud or data-centre priorities ...")."""
    for q in questions:
        m = re.search(r"your (.+?) priorities", q) or re.search(r"Which (.+?) requirements", q)
        if m:
            return m.group(1)
    return ""


def _why_now(context: dict[str, Any], state: str) -> str:
    trigger_type = context.get("trigger_type") or ""
    phrase = _TRIGGER_PHRASES.get(trigger_type)
    sources = int(context.get("trigger_source_count") or 0)
    corroboration = f" (reported by {sources} independent sources)" if sources >= 2 else ""
    if state == PURSUE:
        return ("Confirmed account evidence supports the opportunity, so the next step is to advance it with the "
                "account. Nothing in the evidence sets a deadline.")
    if state == VALIDATE:
        if phrase:
            if trigger_type == "leadership_change":
                return (f"{phrase[0].upper() + phrase[1:]}{corroboration} creates a legitimate reason to understand "
                        "whether priorities have changed. Nothing in the evidence sets a deadline.")
            return (f"{phrase[0].upper() + phrase[1:]}{corroboration} is a reason to validate whether it creates a "
                    "requirement. Nothing in the evidence sets a deadline.")
        return ("The evidence points to a possible requirement that has not been confirmed. Nothing in the evidence "
                "sets a deadline.")
    if state == MONITOR:
        if phrase:
            return (f"No immediate reason for outreach: {phrase}{corroboration} has been reported, but it does not "
                    "establish an Otech-relevant requirement yet.")
        return "No immediate reason for outreach."
    return "There is no account-specific trigger for sales action."


def _objective(context: dict[str, Any], state: str, areas: str) -> str:
    if state == PURSUE:
        reasons = context.get("reasons") or []
        cap = re.match(r"(.+?): confirmed opportunity", reasons[0]["text"]) if reasons else None
        name = cap.group(1) if cap else "the opportunity"
        return f"Advance {name}: confirm scope, budget, timeline and the procurement route with the account."
    if state == VALIDATE:
        gaps = " ".join(context.get("unknown") or [])
        items = [item.format(areas=areas or "technology") for marker, item in _OBJECTIVE_FROM_GAP if marker in gaps]
        if not items:
            items = ["whether the development creates a requirement", "who owns the decision"]
        joined = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
        return f"Establish {joined} — before positioning any Otech solution."
    if state == MONITOR:
        return "No conversation objective yet: watch for the development below to turn into a reported initiative."
    return ""


def _outreach(context: dict[str, Any], state: str, areas: str) -> tuple[str, str, str]:
    """(subject, email, LinkedIn message). Only for PURSUE / VALIDATE with a
    real stakeholder. Restates DECIDE's trigger and questions; claims
    nothing the evidence does not establish."""
    s = context.get("stakeholder")
    if state not in (PURSUE, VALIDATE) or not s or not s.get("name"):
        return "", "", ""
    org = _short_org(context)
    first = _first_name(s.get("name", ""))
    questions = [q for q in context.get("validation_questions") or []][:2]
    area_phrase = areas or "technology"
    trigger_type = context.get("trigger_type") or ""
    opener = _OUTREACH_OPENERS.get(trigger_type, "").format(org=org)
    if state == PURSUE:
        subject = f"{org} — next steps"
        intro = (f"Following the recent developments at {org}, I would welcome a short conversation about how your "
                 "team is approaching the work and where Otech could support it.")
    elif opener:
        subject = f"{org} — {area_phrase} priorities"
        if trigger_type == "leadership_change":
            intro = (f"{opener}. Changes like these are often a natural point to revisit {area_phrase} priorities, "
                     "and I would value your perspective on whether that is the case for your team.")
        else:
            intro = (f"{opener}, and I would value your perspective on what it means for your {area_phrase} "
                     "priorities.")
    else:
        subject = f"{org} — {area_phrase} priorities"
        intro = f"I would value your perspective on how your team is approaching {area_phrase} priorities at {org}."
    asks = ""
    if questions:
        asks = "\n\nIn particular, I would like to understand:\n" + "\n".join(f"- {q}" for q in questions)
    close = ("There is no specific proposal behind this; the aim is to understand your priorities first."
             if state == VALIDATE else "")
    email = (
        f"Subject: {subject}\n\n"
        f"Dear {first},\n\n"
        f"{intro}\n\n"
        "Would you be open to a 20-minute conversation in the coming weeks?"
        f"{asks}"
        + (f"\n\n{close}" if close else "")
        + f"\n\nBest regards,\n{SENDER}"
    )
    linkedin = (
        f"Hello {first},\n\n{intro} Would you be open to a short conversation in the coming weeks?"
        f"\n\nBest regards,\n{SENDER}"
    )
    return subject, email, linkedin


def _evidence(context: dict[str, Any]) -> list[EvidenceLink]:
    out: list[EvidenceLink] = []
    seen: set[str] = set()
    trigger = context.get("trigger")
    candidates = ([trigger] if trigger else []) + list(context.get("evidence") or []) + list(context.get("reasons") or [])
    for f in candidates:
        url = (f or {}).get("source_url") or ""
        if not url or "linkedin.com/in/" in url:
            continue          # profiles are stakeholder context, shown under Stakeholder
        if url in seen:
            continue          # one item per source; the first (most specific) wording wins
        seen.add(url)
        out.append(EvidenceLink(text=f.get("text") or "", source_url=url, source_label=f.get("source_label") or "",
                                detail=f.get("detail") or ""))
    return out


def build_action_plan(context: dict[str, Any], *, handed_over: bool = False) -> ActionPlan:
    state = context.get("decision") or NO_ACTION
    questions = list(context.get("validation_questions") or [])
    areas = _areas_from_questions(questions)
    subject, email, linkedin = _outreach(context, state, areas)
    if state == NO_ACTION:
        action = "No sales action now. Re-run research when new account developments are expected."
    else:
        action = context.get("next_action") or ""
    return ActionPlan(
        organization_name=context.get("organization_name") or "",
        state=state,
        status=STATUS.get(state, "No Action"),
        confidence=context.get("confidence") or "",
        handed_over=handed_over,
        recommended_action=action,
        why_now=_why_now(context, state),
        objective=_objective(context, state, areas),
        questions=questions if state in (PURSUE, VALIDATE, MONITOR) else [],
        outreach_subject=subject,
        outreach_email=email,
        outreach_linkedin=linkedin,
        evidence=_evidence(context),
        stakeholder=context.get("stakeholder") if state in (PURSUE, VALIDATE) else None,
        headline=context.get("headline") or "",
        unknown=list(context.get("unknown") or []),
    )


def plan_for_entry(entry: dict[str, Any], handoff: dict[str, Any] | None = None) -> ActionPlan:
    context, handed_over = context_for(entry, handoff)
    return build_action_plan(context, handed_over=handed_over)


# ---------------------------------------------------------------------------
# Renderer (HTML; styling in theme_engine.py). Buttons, the outreach text
# area and the optional AI section are rendered by app.py.
# ---------------------------------------------------------------------------

def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _panel(title: str, body: str, extra_class: str = "") -> str:
    return (f'<div class="ar-panel{(" " + extra_class) if extra_class else ""}"><div class="ar-panel-head">'
            f'<div class="ar-panel-title">{_e(title)}</div></div>{body}</div>')


def header_html(plan: ActionPlan) -> str:
    origin = "Handed over from DECIDE" if plan.handed_over else "From the current DECIDE result"
    return (
        '<div class="ar-panel ar-decision ar-act">'
        f'<div class="ar-exec-label">{_e(origin)}</div>'
        '<div class="ar-decision-row">'
        f'<span class="ar-decision-state {STATUS_CLASS.get(plan.state, "is-none")}">{_e(plan.state)}</span>'
        f'<span class="ar-act-status">{_e(plan.status)}</span>'
        + (f'<span class="ar-u-tag is-{_e(plan.confidence.lower())}">Confidence · {_e(plan.confidence)}</span>'
           if plan.confidence else "")
        + "</div>"
        '<div class="ar-exec-label" style="margin-top:6px">Recommended action</div>'
        f'<div class="ar-decision-headline">{_e(plan.recommended_action)}</div>'
        "</div>"
    )


def why_now_html(plan: ActionPlan) -> str:
    return _panel("Why now", f'<div class="ar-block-text">{_e(plan.why_now)}</div>')


def objective_html(plan: ActionPlan) -> str:
    if not plan.objective:
        return ""
    return _panel("Conversation objective", f'<div class="ar-block-text">{_e(plan.objective)}</div>')


def questions_html(plan: ActionPlan) -> str:
    title = "What to watch for" if plan.state == MONITOR else "Discovery / validation questions"
    if not plan.questions:
        return ""
    items = "".join(f"<li>{_e(q)}</li>" for q in plan.questions)
    return _panel(title, f'<ol class="ar-d-questions">{items}</ol>', "ar-d-validate")


def evidence_html(plan: ActionPlan) -> str:
    if not plan.evidence:
        body = '<div class="ar-exec-empty">No account-specific evidence item to link.</div>'
    else:
        body = '<ul class="ar-d-list">' + "".join(
            '<li class="ar-d-fact">'
            f'<div class="ar-d-fact-text">{_e(e.text)}</div>'
            '<div class="ar-u-src">'
            + (f'<span class="ar-u-strength">{_e(e.detail)}</span> · ' if e.detail else "")
            + f'<a href="{_e(e.source_url)}" target="_blank" rel="noopener">{_e(e.source_label or e.source_url)} &#8599;</a>'
            "</div></li>"
            for e in plan.evidence
        ) + "</ul>"
    return _panel("Evidence", body)


def stakeholder_html(plan: ActionPlan) -> str:
    s = plan.stakeholder
    if not s:
        text = ("No outreach is recommended, so no stakeholder is put forward."
                if plan.state in (MONITOR, NO_ACTION) else
                "No stakeholder with a confirmed or likely link to the account was identified.")
        return _panel("Stakeholder", f'<div class="ar-exec-empty">{_e(text)}</div>')
    body = (
        f'<div class="ar-u-person-name">{_e(s.get("name"))}</div>'
        + (f'<div class="ar-u-person-title">{_e(s.get("title"))}</div>' if s.get("title") else "")
        + (f'<div class="ar-u-note">{_e(s.get("why"))}</div>' if s.get("why") else "")
    )
    return _panel("Stakeholder", body)

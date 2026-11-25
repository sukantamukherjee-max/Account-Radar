from __future__ import annotations


import html
import os
from contextlib import nullcontext
from typing import Any

import streamlit as st
from theme_engine import init_theme_state, inject_theme, render_theme_controls

from ui.data import (
    account_label,
    account_labels,
    capability_summary_rows,
    epistemic_status_label,
    evidence_category_label,
    evidence_level_label,
    extract_organizations,
    priority_label,
    source_is_linkedin,
    weak_evidence_total,
    research_items,
    research_recency,
)

from ui.executive_summary import (
    ExecutiveOpportunity, build_executive_summary, stakeholder_name, stakeholder_title,
)
from ui.understand_view import build_understand_view, render_understand
from ui.identity_view import identity_block_html, identity_for_report
from ui.decide_view import (
    action_html, build_decision, decision_header_html, engage_html, handoff_brief, known_html,
    reasons_html, unknown_html, validate_html,
)
from ui.decide_view import STATE_CLASS as DECISION_STATE_CLASS
from ui.act_view import (
    context_for as act_context_for, build_action_plan, evidence_html as act_evidence_html,
    header_html as act_header_html, objective_html as act_objective_html, questions_html as act_questions_html,
    stakeholder_html as act_stakeholder_html, why_now_html as act_why_now_html,
)

from ai.client import AIResponse, OpenRouterAIClient
from ai.synthesis import synthesize_account
from commercial.opportunities import build_opportunity_hypotheses
from research.acquisition import run_acquisition
from research.analysis.report import build_report
from research.identity import attach_official_logo, build_identity
from research.organization import OrganizationInput
from research.providers.openrouter_search import OpenRouterSearchProvider
from research.providers.tavily import TavilyProvider	
from research.providers.registry import build_search_providers
from ui import demo_mode
from ui.account_signals import normalize_account_signals


APP_TITLE = "Account Radar"


def _unconfigured_ai():
    class Client:
        def is_configured(self) -> bool:
            return False

        def complete_json(self, prompt: str, *, schema_name: str) -> AIResponse:
            return AIResponse(error="OPENROUTER_API_KEY not configured")

    return Client()


def _run_live(
    name: str,
    country: str | None,
    *,
    progress: Any = None,
    reuse_verified_identity: bool = True,
) -> dict[str, Any]:
    """The ONE live research path (normal mode and Demo Mode). Always runs
    the real providers -- there is no result cache. `progress` (optional) is
    called with short stage messages for a status display. Demo Mode passes
    reuse_verified_identity=False so its fresh run also re-verifies identity
    instead of re-using a domain verified earlier in the session."""
    step = progress or (lambda _message: None)
    # Search-provider chain (research/providers/registry.py): Brave -> Tavily,
    # configured providers only. The router falls through to the next
    # provider only when a query family is unavailable, errored or genuinely
    # sparse. OpenRouter search and Serper are not part of the chain.
    providers = build_search_providers()
    if not providers:
        raise RuntimeError(
            "No search provider API key is configured: BRAVE_SEARCH_API_KEY and TAVILY_API_KEY are not configured in the environment."
        )

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()

    # Identity stability: an official website already VERIFIED for this
    # organization in this session is re-used through the resolver's
    # existing supplied-website path, so a later run cannot drift to a
    # different domain just because different research results come back.
    known = st.session_state.setdefault("verified_identities", {})
    key = (name.strip().lower(), (country or "").strip().lower())
    website = known.get(key) if reuse_verified_identity else None
    org = OrganizationInput(name=name.strip(), country=(country or "").strip() or None, website=website)
    step("Searching current public sources…")
    run = run_acquisition(org, providers)
    step("Qualifying evidence and mapping capabilities…")
    report = build_report(run)
    # Logo: only from the verified official website (validated, embedded);
    # never from research sources. Failure leaves the initials avatar.
    step("Verifying the organization's official identity…")
    identity = attach_official_logo(build_identity(run.organization, run.sources))
    report.identity = identity.to_dict()
    if run.organization.website and run.organization.domain_verification in ("knowledge_panel", "site_owner_title", "supplied"):
        known[key] = run.organization.website
    ai_client = OpenRouterAIClient(api_key=openrouter_key) if openrouter_key else _unconfigured_ai()
    step("Preparing the optional AI interpretation…" if openrouter_key else "AI interpretation not configured — continuing without it…")
    synthesis = synthesize_account(report, ai_client)
    step("Qualifying opportunities…")
    opportunities = build_opportunity_hypotheses(report.capability_mappings, max_items=5)

    return {
        "report": report.to_dict(),
        "ai_synthesis": synthesis.to_dict(),
        "opportunity_hypotheses": [o.to_dict() for o in opportunities],
        # Runtime completion time of THIS run (UTC); shown in Demo Mode.
        "researched_at": demo_mode.researched_at_iso(),
    }


def _render_metric_row(report: dict[str, Any], opportunities: list[dict[str, Any]], *, empty: bool = False) -> None:
    q = report.get("qualification", {})
    diag = report.get("acquisition_diagnostics", {})
    # Reports store the query count as "total_queries"; older keys kept as fallbacks.
    queries = diag.get("query_count", diag.get("total_queries", diag.get("queries", 0)))
    # Phase 2 hardening (item 9): uncertainty stays visible at a glance --
    # excluded/unusable sources and weak/incidental evidence are counted
    # elsewhere in the pipeline but were previously never surfaced once a
    # corpus qualified as usable.
    values = [
        ("Qualified sources", q.get("qualified_count", 0), "doc", "k-blue", False),
        ("Signals", len(report.get("signals", [])), "signal", "k-green", False),
        ("Capability mappings", len(report.get("chain", [])), "nodes", "k-amber", False),
        ("Opportunities", len(opportunities), "target", "k-red", True),
        ("Research queries", queries, "search", "k-purple", False),
        ("Disqualified sources", q.get("disqualified_count", 0), "alert", "k-slate", False),
    ]
    if empty:
        # No account loaded: nothing to navigate to, so the cards stay static.
        cards = "".join(
            _kpi_card(label, "&ndash;", icon, accent, emphasis=False, empty=True)
            for label, value, icon, accent, emph in values
        )
        st.markdown(f'<div class="ar-kpis">{cards}</div>', unsafe_allow_html=True)
        return
    # Each card is a real control: a transparent full-card button (styled in
    # theme_engine.py) opens the existing destination for that figure.
    with st.container(key="ar_kpi_grid"):
        for label, value, icon, accent, emph in values:
            nav_item, research_view = KPI_NAVIGATION[label]
            slug = _slug(label)
            with st.container(key=f"ar_kpi_nav_{slug}"):
                st.markdown(
                    _kpi_card(label, f'<span class="ar-num">{_esc(value)}</span>', icon, accent, emphasis=emph),
                    unsafe_allow_html=True,
                )
                st.button(
                    f"Open {label}",
                    key=f"kpi_nav_{slug}",
                    on_click=_navigate_to,
                    args=(nav_item, research_view),
                )


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(text).lower()).strip("_")


def _navigate_to(nav_item: str, research_view: str | None = None) -> None:
    """Open an existing section through the sidebar navigation state (the
    same mechanism the sidebar uses). The sequence number forces a fresh tab
    set, so the target tab opens even if it is already the nav selection."""
    st.session_state["ar_nav"] = nav_item
    st.session_state["ar_nav_seq"] = st.session_state.get("ar_nav_seq", 0) + 1
    if research_view:
        st.session_state["research_source_view"] = research_view


def _open_person_in_people(person_id: str) -> None:
    """Open the existing People intelligence for one stakeholder: clear the
    PEOPLE filters so the person is visible, select (expand) them, and open
    the PEOPLE tab."""
    for key, value in PEOPLE_FILTER_DEFAULTS.items():
        st.session_state[key] = value
    st.session_state["people_selected"] = person_id
    _navigate_to("People")


def _take_decision_to_act(context: dict[str, Any]) -> None:
    """Carry the DECIDE context into ACT (no research is re-run)."""
    st.session_state["act_context"] = dict(context)
    _navigate_to("Next actions")


# ---------------------------------------------------------------------------
# Presentation helpers (executive design pass). Pure rendering: they only read
# the report / opportunity / people data the pipeline already produced and
# never compute or alter evidence, opportunities, people or classification.
# All styling lives in theme_engine.py.
# ---------------------------------------------------------------------------

SIGNAL_LABELS = {
    "capacity_change": "Capacity Change",
    "contract_award": "Contract Award",
    "expansion": "Expansion",
    "investment": "Investment",
    "leadership_change": "Leadership Change",
    "partnership": "Partnership",
    "procurement_activity": "Procurement Activity",
    "project": "Project",
    "technology_initiative": "Technology Initiative",
    "other": "Other",
}

SIGNAL_ORDER = [
    "expansion",
    "investment",
    "partnership",
    "leadership_change",
    "technology_initiative",
    "capacity_change",
    "contract_award",
    "procurement_activity",
    "project",
    "other",
]

# Semantic accent per signal type (presentation only).
SIGNAL_ACCENT = {
    "expansion": "k-blue",
    "investment": "k-green",
    "partnership": "k-purple",
    "leadership_change": "k-slate",
    "technology_initiative": "k-blue",
    "capacity_change": "k-amber",
    "contract_award": "k-green",
    "procurement_activity": "k-red",
    "project": "k-amber",
    "other": "k-slate",
}

# Signal types that describe a concrete technology / commercial event.
KEY_EVENT_TYPES = ("technology_initiative", "procurement_activity", "contract_award", "capacity_change", "project")

WORKFLOW_STEPS = ("WATCH", "UNDERSTAND", "DECIDE", "ACT")

RESEARCH_VIEW_QUALIFIED = "Qualified intelligence"
RESEARCH_VIEW_DISQUALIFIED = "Disqualified sources"

# Top KPI card -> existing destination (sidebar navigation item, optional
# RESEARCH view). Only sections that already exist.
KPI_NAVIGATION = {
    "Qualified sources": ("Research queries", RESEARCH_VIEW_QUALIFIED),
    "Signals": ("Signals", None),
    "Capability mappings": ("Capability mapping", None),
    "Opportunities": ("Opportunities", None),
    "Research queries": ("Research queries", None),
    "Disqualified sources": ("Research queries", RESEARCH_VIEW_DISQUALIFIED),
}

# Sidebar navigation -> the existing six tabs (labels unchanged).
NAV_ITEMS = {
    "Executive summary": ("WATCH", ":material/dashboard:"),
    "Signals": ("WATCH", ":material/sensors:"),
    "Capability mapping": ("UNDERSTAND", ":material/hub:"),
    "Opportunities": ("DECIDE", ":material/track_changes:"),
    "Next actions": ("ACT", ":material/bolt:"),
    "Research queries": ("RESEARCH", ":material/travel_explore:"),
    "People": ("PEOPLE", ":material/group:"),
}

_ICON_PATHS = {
    "doc": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>',
    "signal": '<circle cx="12" cy="12" r="2"/><path d="M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8"/>',
    "nodes": '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7.2 8.2l3.6 7.6M16.8 8.2l-3.6 7.6M8.5 6h7"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "alert": '<path d="M12 3l9.5 17H2.5z"/><path d="M12 10v4M12 17.5v.01"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.2A5 5 0 0 1 21.5 19"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "cloud": '<path d="M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 9.5 4.3 4.3 0 0 0 7 18z"/>',
    "database": '<ellipse cx="12" cy="5.5" rx="7" ry="2.5"/><path d="M5 5.5v13c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-13M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5"/>',
    "cart": '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2.5 3.5h3l2.5 12h11l2-8H6.5"/>',
    "bulb": '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9V16h7v-2.1A6 6 0 0 0 12 3z"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "send": '<path d="M21 3L3 10.5l7 2.5 2.5 7z"/><path d="M10 13L21 3"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "bolt": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    "link": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
}

_RADAR_ART = (
    '<svg class="ar-topbar-art" viewBox="0 0 420 260" fill="none" stroke="currentColor" aria-hidden="true">'
    '<g stroke-opacity="0.32" stroke-width="1">'
    '<circle cx="300" cy="150" r="40"/><circle cx="300" cy="150" r="80"/>'
    '<circle cx="300" cy="150" r="120"/><circle cx="300" cy="150" r="160"/>'
    '<path d="M300 -10V310M140 150H460M187 37L413 263M187 263L413 37"/>'
    '</g>'
    '<path d="M300 150L410 60" stroke-opacity="0.75" stroke-width="2"/>'
    '<g fill="currentColor" stroke="none" fill-opacity="0.85">'
    '<circle cx="352" cy="104" r="3.5"/><circle cx="246" cy="98" r="2.5"/><circle cx="338" cy="202" r="2.5"/>'
    '</g></svg>'
)


def _icon(name: str) -> str:
    paths = _ICON_PATHS.get(name, _ICON_PATHS["doc"])
    return (
        '<svg class="ar-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )


def _esc(value: Any) -> str:
    """HTML-escape any value rendered inside unsafe_allow_html markup."""
    return html.escape("" if value is None else str(value), quote=True)


def _signal_label(kind: str) -> str:
    return SIGNAL_LABELS.get(kind, str(kind).replace("_", " ").title())


def _signal_counts(signals: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for signal in signals:
        kind = signal.get("signal_type", "other")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _top_opportunity(opportunities: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The highest-scoring opportunity the pipeline already produced, if any."""
    if not opportunities:
        return None
    return max(opportunities, key=lambda o: (o.get("score") or 0))


def _strongest_evidence(report: dict[str, Any]) -> dict[str, Any] | None:
    """Highest-confidence evidence-chain row, preferring stronger evidence
    levels. Used only when there is no qualifying opportunity."""
    rank = {"direct_evidence": 2, "strong_inference": 1}
    # Audit fix: weak-relevance or industry/market-context rows must never
    # headline WATCH as "strongest evidence" for the account.
    from ui.account_signals import CONTEXT_CATEGORIES
    chain = [
        r for r in (report.get("chain") or [])
        if r.get("evidence_level") in rank
        and r.get("evidence_category") not in CONTEXT_CATEGORIES
    ]
    if not chain:
        return None
    return max(chain, key=lambda r: (rank.get(r.get("evidence_level", ""), 0), r.get("confidence") or 0))


def _strongest_empty_text(report: dict[str, Any]) -> str:
    """Honest empty-state copy for the WATCH strongest-evidence panel."""
    n = len(report.get("chain") or [])
    if not n:
        return "This research run produced no capability mappings."
    return (f"All {n} capability mapping{'s' if n != 1 else ''} are weak relevance or "
            "background context, not account evidence. See DECIDE for what to validate.")


def _latest_evidence_age(report: dict[str, Any]) -> str | None:
    """Freshness of the newest DATED evidence in the chain, or None. Never
    invents a date."""
    best = None
    for row in report.get("chain") or []:
        published = row.get("published_at")
        if not published:
            continue
        recency = research_recency(published)
        age = recency.get("age_days")
        if age is not None and (best is None or age < best[0]):
            best = (age, recency.get("age_label", ""))
    return f"Latest dated evidence: {best[1]}" if best else None


def _domain(url: str | None) -> str:
    if not url:
        return ""
    text = str(url).split("//", 1)[-1]
    return text.split("/", 1)[0].removeprefix("www.")


def _render_topbar(report: dict[str, Any] | None) -> None:
    """Premium navy header band: brand + workflow on the left, the current
    account (if any) on the right, over an abstract radar treatment."""
    flow = "<i>&#9679;</i>".join(f"<span>{step}</span>" for step in WORKFLOW_STEPS)
    if report:
        # Account identity only (name, country, verified official domains,
        # logo) -- never a research-source domain. See ui/identity_view.py.
        account = identity_block_html(identity_for_report(report))
    else:
        account = (
            '<div class="ar-topbar-account-name">No account loaded</div>'
            '<div class="ar-topbar-account-sub">Analyze an organization to begin</div>'
        )
    st.markdown(
        '<div class="ar-topbar">'
        f'{_RADAR_ART}'
        '<div class="ar-topbar-brand">'
        '<div class="ar-topbar-eyebrow"><b>OTECH</b> &nbsp;|&nbsp; ACCOUNT INTELLIGENCE</div>'
        '<div class="ar-topbar-title">Account Radar</div>'
        f'<div class="ar-topbar-flow">{flow}</div>'
        '</div>'
        f'<div class="ar-topbar-account">{account}</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _page_header(title: str, icon: str, question: str, sub: str = "") -> None:
    sub_html = f'<div class="ar-page-sub">{_esc(sub)}</div>' if sub else ""
    st.markdown(
        '<div class="ar-page">'
        f'<div class="ar-page-title">{_esc(title)} {_icon(icon)}</div>'
        f'<div class="ar-page-question">{_esc(question)}</div>'
        f'{sub_html}'
        '</div>',
        unsafe_allow_html=True,
    )


def _kpi_card(label: str, value: str, icon: str, accent: str, *, sub: str = "", emphasis: bool = False, empty: bool = False) -> str:
    classes = f"ar-kpi {accent}" + (" is-emph" if emphasis else "") + (" is-empty" if empty else "")
    sub_html = f'<div class="ar-kpi-sub">{_esc(sub)}</div>' if sub else ""
    return (
        f'<div class="{classes}">'
        f'<div class="ar-kpi-icon">{_icon(icon)}</div>'
        '<div class="ar-kpi-body">'
        f'<div class="ar-kpi-label">{_esc(label)}</div>'
        f'<div class="ar-kpi-value">{value}</div>'
        f'{sub_html}'
        '</div></div>'
    )


def _priority_badge(priority: str | None) -> str:
    key = (priority or "").lower()
    cls = {"high": " is-high", "medium": " is-medium"}.get(key, "")
    return f'<span class="ar-priority{cls}">Priority &middot; {_esc(priority_label(priority or "") or "-")}</span>'


def _signal_badge(kind: str) -> str:
    return f'<span class="ar-badge {SIGNAL_ACCENT.get(kind, "k-slate")}">{_esc(_signal_label(kind))}</span>'


def _signal_distribution_html(counts: dict[str, int]) -> str:
    kinds = [k for k in SIGNAL_ORDER if k in counts] + sorted(k for k in counts if k not in SIGNAL_ORDER)
    kinds.sort(key=lambda k: -counts.get(k, 0))
    peak = max(counts.values()) if counts else 0
    rows = []
    for kind in kinds:
        count = counts.get(kind, 0)
        width = round(100 * count / peak) if peak else 0
        rows.append(
            f'<div class="ar-dist-row {SIGNAL_ACCENT.get(kind, "k-slate")}">'
            f'<div class="ar-dist-label">{_esc(_signal_label(kind))}</div>'
            f'<div class="ar-dist-track"><div class="ar-dist-fill" style="width:{width}%"></div></div>'
            f'<div class="ar-dist-count">{count}</div>'
            '</div>'
        )
    return "".join(rows)


def _analyze_account(name: str, country: str) -> str | None:
    """Run the existing live-research path for one organization and load the
    result into session state. Returns an error message, or None on success.
    Shared by the sidebar and the landing search panel so the research call
    exists in exactly one place.

    Demo Mode (app.run_demo()) uses this same call, always fresh: the
    previous result is cleared first, progress is shown, and a failed run
    loads nothing -- never an older result, never a stack trace."""
    demo = _demo_active()
    if demo:
        _begin_demo_run(name, country)
    with (_demo_status_box(name, country) if demo else nullcontext()) as box:
        try:
            entry = _run_live(name, country, progress=box.write if demo else None,
                              reuse_verified_identity=not (demo and demo_mode.DEMO.fresh_research))
        except Exception as exc:
            if demo:
                return _fail_demo_run(box, demo_mode.failure_from_exception(exc))
            return str(exc)
        if demo:
            failure = demo_mode.failure_from_report(entry.get("report") or {})
            if failure:
                return _fail_demo_run(box, failure)
            box.update(label=f"Research complete \u00b7 {_demo_label(name, country)}", state="complete", expanded=False)
    st.session_state.entry = entry
    # Live research always produces exactly one account -- this keeps the
    # selector's "more than one account" check accurate and clears out any
    # selector left over from a previously loaded multi-account report.
    st.session_state.accounts = [entry]
    st.session_state.loaded_from = "live research (demo)" if demo else "live research"
    st.session_state.last_account = name.strip()
    return None


def _render_sidebar_nav(has_entry: bool) -> str:
    """Sidebar navigation list. Returns the tab it points to; main() opens
    that tab. Disabled until an account is loaded."""
    st.sidebar.divider()
    choice = st.sidebar.radio(
        "Navigation",
        list(NAV_ITEMS),
        key="ar_nav",
        format_func=lambda item: f"{NAV_ITEMS[item][1]} {item}",
        label_visibility="collapsed",
        disabled=not has_entry,
        width="stretch",
    )
    return NAV_ITEMS.get(choice, ("WATCH", ""))[0]


# ---------------------------------------------------------------------------
# Live Demo Mode (presentation only; see ui/demo_mode.py, demo/demo_app.py).
# The research itself is the shared _analyze_account -> _run_live path.
# ---------------------------------------------------------------------------

def _demo_active() -> bool:
    """True when the app was launched through demo/demo_app.py."""
    return bool(st.session_state.get("ar_demo"))


# Session keys that belong to a loaded account result. Demo Mode clears them
# before every live run so a failed run can never leave an old result showing.
_RESULT_STATE_KEYS = ("entry", "accounts", "act_context", "loaded_from", "last_account", "people_selected")


def _demo_label(name: str, country: str) -> str:
    return name.strip() + (f", {country.strip()}" if (country or "").strip() else "")


def _begin_demo_run(name: str, country: str) -> None:
    for key in _RESULT_STATE_KEYS:
        st.session_state.pop(key, None)
    st.session_state["demo_failure"] = None
    st.session_state["demo_last_request"] = {"organization": name.strip(), "country": (country or "").strip()}


def _demo_status_box(name: str, country: str):
    return st.status(f"Live research \u00b7 {_demo_label(name, country)}", expanded=True)


def _fail_demo_run(box: Any, failure: demo_mode.ResearchFailure) -> str:
    box.update(label="Live research could not be completed", state="error", expanded=False)
    st.session_state["demo_failure"] = failure
    return failure.cause


def _request_demo_retry() -> None:
    st.session_state["demo_retry"] = True


def _render_demo_failure() -> None:
    """Failure panel (Demo Mode): no current result, likely cause, Retry."""
    failure = st.session_state.get("demo_failure")
    request = st.session_state.get("demo_last_request") or {}
    st.markdown(demo_mode.failure_html(failure, request.get("organization", ""), request.get("country", "")),
                unsafe_allow_html=True)
    if request.get("organization"):
        st.button("Retry research", key="demo_retry_button", type="primary", icon=":material/refresh:",
                  on_click=_request_demo_retry)


def _render_demo_status(entry: dict[str, Any], report: dict[str, Any]) -> None:
    """Compact LIVE RESEARCH strip from the current result (read-only)."""
    decision = build_decision(entry)
    st.markdown(
        demo_mode.status_html(
            researched_at=entry.get("researched_at"),
            sources=(report.get("qualification") or {}).get("qualified_count", 0),
            developments=normalize_account_signals(report).meaningful_count,
            opportunities=decision.opportunity_count,
            decision=decision.state,
            confidence=decision.confidence or "",
        ),
        unsafe_allow_html=True,
    )


def _render_account_header(entry: dict[str, Any], report: dict[str, Any]) -> None:
    """Compact account identity: name, location/site, research status and
    freshness -- only values present in the loaded report."""
    ai = entry.get("ai_synthesis", {}) or {}
    q = report.get("qualification", {}) or {}
    diag = report.get("acquisition_diagnostics", {}) or {}
    status = report.get("status") or q.get("corpus_status") or "unknown"

    bits = [f"<span>{_esc(report.get('country') or 'Country not specified')}</span>"]
    identity = identity_for_report(report)
    if identity.get("official_website") and identity.get("primary_official_domain"):
        bits.append(
            f'<a href="{_esc(identity["official_website"])}" target="_blank" rel="noopener">'
            f'{_esc(identity["primary_official_domain"])}</a>'
        )
    elapsed = diag.get("elapsed_seconds")
    if isinstance(elapsed, (int, float)):
        bits.append(f"<span>Research run {elapsed:.0f}s</span>")
    freshness = _latest_evidence_age(report)
    if freshness:
        bits.append(f"<span>{_esc(freshness)}</span>")
    status_chip = (
        '<span class="ar-chip is-warn">Insufficient evidence</span>'
        if status == "insufficient_evidence"
        else f'<span class="ar-chip is-ok">Research {_esc(str(status).replace("_", " "))}</span>'
    )
    ai_status = ai.get("status", "unknown")
    ai_chip = f'<span class="ar-chip{" is-ok" if ai_status == "ok" else ""}">AI synthesis: {_esc(ai_status)}</span>'
    source_chip = f'<span class="ar-chip is-plain">Source: {_esc(st.session_state.get("loaded_from", "report"))}</span>'
    sep = '<span class="ar-dot">&bull;</span>'
    st.markdown(
        '<div class="ar-acct">'
        f'<div class="ar-acct-name">{_esc(report.get("organization_name", "Organization"))}</div>'
        f'<div class="ar-acct-sub">{sep.join(bits)}{sep}{status_chip}{ai_chip}{source_chip}</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _render_landing() -> None:
    """Landing page when no account is loaded. The sidebar is the single place
    to enter an account and start analysis, so the main area only explains
    the workflow: start card, empty KPI row, empty signal landscape and the
    account-intelligence empty state. Shows no numbers -- there is no data."""
    st.markdown(
        '<div class="ar-panel ar-start">'
        f'<div class="ar-start-icon">{_icon("search")}</div>'
        '<div class="ar-start-body">'
        '<div class="ar-start-title">Start with an account</div>'
        '<div class="ar-start-text">Enter an organization in the sidebar to begin research.</div>'
        '<div class="ar-start-flow" aria-label="How Account Radar builds its view">'
        + '<span class="ar-start-sep">&rarr;</span>'.join(
            f'<span class="ar-start-step">{stage}</span>'
            for stage in ("Public evidence", "Signals", "Capabilities", "Opportunities", "Actions")
        )
        + '</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    _render_metric_row({}, [], empty=True)

    left, right = st.columns([1.45, 1], gap="medium")
    with left:
        st.markdown(
            f'<div class="ar-panel"><div class="ar-panel-head"><div class="ar-panel-title">{_icon("chart")} Signal landscape</div></div>'
            f'<div class="ar-empty ar-empty-center"><div class="ar-empty-icon">{_icon("signal")}</div><div>'
            '<div class="ar-empty-title">No signals yet</div>'
            '<div class="ar-empty-text">Analyze an account to discover expansion, investment, partnership, '
            'technology and procurement signals.</div>'
            '</div></div></div>',
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f'<div class="ar-panel"><div class="ar-panel-head"><div class="ar-panel-title">{_icon("eye")} Account intelligence</div></div>'
            f'<div class="ar-empty"><div class="ar-empty-icon">{_icon("search")}</div><div>'
            '<div class="ar-empty-title">No account loaded</div>'
            '<div class="ar-empty-text">Enter an organization in the sidebar and select <strong>Analyze account</strong> to build its '
            'account radar. Account Radar never assumes a customer requirement from a keyword alone.</div>'
            '</div></div>'
            '<div class="ar-howto">'
            '<div class="ar-howto-item"><div class="ar-howto-num">01 WATCH</div><div class="ar-howto-text">See what is changing at the account.</div></div>'
            '<div class="ar-howto-item"><div class="ar-howto-num">02 UNDERSTAND</div><div class="ar-howto-text">Trace each signal to its evidence.</div></div>'
            '<div class="ar-howto-item"><div class="ar-howto-num">03 DECIDE &middot; ACT</div><div class="ar-howto-text">Validate hypotheses, then engage.</div></div>'
            '</div></div>',
            unsafe_allow_html=True,
        )


def _render_evidence_legend(report: dict[str, Any]) -> None:
    """Explains the three evidence levels the engine already assigns, with
    how many capability mappings in this report fall in each. Read-only:
    classification is unchanged."""
    counts: dict[str, int] = {}
    for row in report.get("chain") or []:
        level = row.get("evidence_level", "")
        counts[level] = counts.get(level, 0) + 1
    items = [
        ("direct_evidence", "k-green", "Confirmed evidence",
         "The source itself describes the capability at this organization."),
        ("strong_inference", "k-blue", "Inference hypothesis",
         "A real business signal implies the requirement; validate before acting."),
        ("weak_relevance", "k-slate", "Context only",
         "Incidental or third-party mention; retained for traceability, never a sales claim."),
    ]
    cells = "".join(
        f'<div class="ar-legend-item">'
        f'<span class="ar-badge {accent}">{_esc(evidence_level_label(level))}</span>'
        f'<span class="ar-legend-status">{_esc(status)} &middot; {counts.get(level, 0)} mappings</span>'
        f'<div class="ar-legend-text">{_esc(text)}</div>'
        '</div>'
        for level, accent, status, text in items
    )
    st.markdown(f'<div class="ar-legend">{cells}</div>', unsafe_allow_html=True)


def _render_strongest_panel(report: dict[str, Any], opportunities: list[dict[str, Any]]) -> None:
    """Strongest opportunity (or, failing that, strongest evidence) plus the
    next-step guidance into UNDERSTAND / DECIDE."""
    top = _top_opportunity(opportunities)
    if top:
        badges = (
            _priority_badge(top.get("priority"))
            + f'<span class="ar-chip is-accent">{_esc(evidence_level_label(top.get("evidence_level", "")))}</span>'
            + f'<span class="ar-chip is-plain">{_esc(epistemic_status_label(top.get("epistemic_status")))}</span>'
        )
        body = (
            f'<div class="ar-panel-head"><div class="ar-panel-title">{_icon("target")} Strongest opportunity</div>'
            f'<div class="ar-panel-meta">Score {_esc(top.get("score", "-"))}</div></div>'
            f'<div class="ar-highlight-name">{_esc(top.get("capability_name", "Capability"))}</div>'
            f'<div class="ar-highlight-badges">{badges}</div>'
            f'<div class="ar-highlight-text">{_esc(top.get("hypothesis", ""))}</div>'
            '<div class="ar-facts">'
            f'<div class="ar-fact"><div class="ar-fact-label">Confidence</div><div class="ar-fact-value">{_esc(top.get("confidence", "-"))}</div></div>'
            f'<div class="ar-fact"><div class="ar-fact-label">Evidence</div><div class="ar-fact-value">{_esc(top.get("evidence_count", "-"))}</div></div>'
            f'<div class="ar-fact"><div class="ar-fact-label">Sources</div><div class="ar-fact-value">{_esc(top.get("independent_source_count", "-"))}</div></div>'
            '</div>'
        )
    else:
        row = _strongest_evidence(report)
        if row:
            capability = (row.get("potential_otech_capability") or {}).get("capability_name", "Capability")
            body = (
                f'<div class="ar-panel-head"><div class="ar-panel-title">{_icon("target")} Strongest evidence</div>'
                '<div class="ar-panel-meta">No qualifying opportunity yet</div></div>'
                f'<div class="ar-highlight-name">{_esc(capability)}</div>'
                '<div class="ar-highlight-badges">'
                f'<span class="ar-chip is-accent">{_esc(evidence_level_label(row.get("evidence_level", "")))}</span>'
                f'<span class="ar-chip is-plain">{_esc(evidence_category_label(row.get("evidence_category")))}</span>'
                '</div>'
                f'<div class="ar-highlight-text">{_esc(row.get("supporting_context") or row.get("source_title") or "")}</div>'
                '<div class="ar-facts">'
                f'<div class="ar-fact"><div class="ar-fact-label">Confidence</div><div class="ar-fact-value">{_esc(row.get("confidence", "-"))}</div></div>'
                f'<div class="ar-fact"><div class="ar-fact-label">Mappings</div><div class="ar-fact-value">{len(report.get("chain") or [])}</div></div>'
                '<div class="ar-fact"><div class="ar-fact-label">Opportunities</div><div class="ar-fact-value">0</div></div>'
                '</div>'
            )
        else:
            body = (
                f'<div class="ar-panel-head"><div class="ar-panel-title">{_icon("target")} Strongest evidence</div></div>'
                f'<div class="ar-empty"><div class="ar-empty-icon">{_icon("search")}</div><div>'
                '<div class="ar-empty-title">No account evidence of need yet</div>'
                f'<div class="ar-empty-text">{_strongest_empty_text(report)}</div>'
                '</div></div>'
            )
    st.markdown(f'<div class="ar-panel">{body}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="ar-panel"><div class="ar-panel-head"><div class="ar-panel-title">{_icon("bolt")} Next step</div></div>'
        '<div class="ar-next">'
        '<div class="ar-next-item"><div class="ar-next-step">UNDERSTAND</div>'
        '<div class="ar-next-title">Review the evidence chain</div>'
        '<div class="ar-next-text">Signal &rarr; impact &rarr; requirement &rarr; capability</div></div>'
        '<div class="ar-next-item is-primary"><div class="ar-next-step">DECIDE</div>'
        '<div class="ar-next-title">Validate the hypotheses</div>'
        '<div class="ar-next-text">Confirm what needs validation before you engage</div></div>'
        '</div></div>',
        unsafe_allow_html=True,
    )


def _account_development_count(report: dict[str, Any]) -> int:
    """The ONE shared account-specific development count (same as the
    Executive Summary and UNDERSTAND). Display only; no logic change."""
    from ui.account_signals import normalize_account_signals
    try:
        return normalize_account_signals(report).meaningful_count
    except Exception:  # never let a display helper break WATCH
        return 0


def _watch_count_meta(report: dict[str, Any], signals: list, signal_types: dict) -> str:
    """WATCH header: account-specific developments first, raw research
    signals (which include industry/background context) second."""
    n = _account_development_count(report)
    return (f"{_plural_text(n, 'account-specific development')} &middot; "
            f"{_plural_text(len(signals), 'research signal')} incl. background "
            f"across {_plural_text(len(signal_types), 'type')}")


def _watch_count_note(report: dict[str, Any], signals: list) -> str:
    n = _account_development_count(report)
    return (
        '<div class="ar-opp-why">The distribution below counts <strong>all research signals</strong>, '
        'including industry and background context &mdash; they are not account developments. '
        f'This account has <strong>{_plural_text(n, "account-specific development")}</strong> '
        '(see UNDERSTAND).</div>'
    )


def _plural_text(n: int, word: str) -> str:
    return f"{n} {word if n == 1 else word + 's'}"


def _render_watch(report: dict[str, Any], opportunities: list[dict[str, Any]]) -> None:
    _page_header(
        "WATCH", "eye", "What is happening in this account?",
        "What is changing, what is emerging, and what deserves attention.",
    )

    signals = report.get("signals", [])

    if not signals:
        st.info("No reliable public signals were extracted from this research run.")
        return

    signal_types = _signal_counts(signals)

    # Styling for WATCH components (.ar-dist-row, .ar-evidence-card, ...)
    # is provided by theme_engine.inject_theme(), called from main().
    left, right = st.columns([1.5, 1], gap="medium")
    with left:
        st.markdown(
            f'<div class="ar-panel"><div class="ar-panel-head"><div class="ar-panel-title">{_icon("chart")} Signal overview</div>'
            f'<div class="ar-panel-meta">{_watch_count_meta(report, signals, signal_types)}</div></div>'
            + _watch_count_note(report, signals)
            + _signal_distribution_html(signal_types)
            + '</div>',
            unsafe_allow_html=True,
        )

        events = [s_ for s_ in signals if s_.get("signal_type") in KEY_EVENT_TYPES][:6]
        if events:
            rows = []
            for sig in events:
                kind = sig.get("signal_type", "other")
                text = str(sig.get("text") or sig.get("snippet") or "").replace("?", "")
                title = str(sig.get("source_title") or sig.get("title") or "Source").replace("?", "")
                link = (
                    f'<a class="ar-evidence-link" href="{_esc(sig.get("source_url"))}" target="_blank" rel="noopener">Source &#8599;</a>'
                    if sig.get("source_url") else ""
                )
                rows.append(
                    '<div class="ar-event">'
                    f'<div>{_signal_badge(kind)}</div>'
                    f'<div><div class="ar-event-text">{_esc(text)}</div><div class="ar-event-src">{_esc(title)}</div></div>'
                    f'<div>{link}</div>'
                    '</div>'
                )
            st.markdown(
                f'<div class="ar-panel"><div class="ar-panel-head"><div class="ar-panel-title">{_icon("bolt")} Key technology &amp; business events</div>'
                '<div class="ar-panel-meta">Technology initiatives, procurement, contracts, capacity and projects</div></div>'
                + "".join(rows)
                + '</div>',
                unsafe_allow_html=True,
            )
    with right:
        _render_strongest_panel(report, opportunities)

    # ---------------------------------------------------------
    # Recent / relevant evidence
    # ---------------------------------------------------------
    st.markdown(
        '<div class="ar-section-head"><div class="ar-section-title">Recent / relevant signals</div>'
        '<div class="ar-section-sub">Public evidence extracted from the research run. '
        'Signal presence does not by itself establish a customer requirement.</div></div>',
        unsafe_allow_html=True,
    )

    cards = []
    for signal in signals[:12]:
        kind = signal.get("signal_type", "other")
        source_title = signal.get("source_title") or signal.get("title") or "Source"
        text = signal.get("text") or signal.get("snippet") or "No extracted evidence text available."
        matched_terms = signal.get("matched_terms") or []

        # Defensive cleanup for previously observed encoding artefacts.
        source_title = str(source_title).replace("?", "")
        text = str(text).replace("?", "")
        matched = ", ".join(str(term).replace("_", " ") for term in matched_terms)

        matched_html = (
            f'<div class="ar-evidence-match"><strong>Matched:</strong> {_esc(matched)}</div>' if matched else '<div></div>'
        )
        source_url = signal.get("source_url")
        link_html = (
            f'<a class="ar-evidence-link" href="{_esc(source_url)}" target="_blank" rel="noopener">Open source &#8599;</a>'
            if source_url else ""
        )
        # Keep the HTML flat so Streamlit does not interpret
        # interpolated markup as literal text.
        cards.append(
            '<div class="ar-evidence-card">'
            '<div class="ar-evidence-head">'
            f'<div class="ar-evidence-title">{_esc(source_title)}</div>'
            f'{_signal_badge(kind)}'
            '</div>'
            f'<div class="ar-evidence-text">{_esc(text)}</div>'
            f'<div class="ar-evidence-foot">{matched_html}{link_html}</div>'
            '</div>'
        )

    st.markdown(f'<div class="ar-evidence-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def _render_understand(report: dict[str, Any]) -> None:
    """UNDERSTAND v2: the deterministic view from ui.understand_view (built
    from the normalized account-signal layer, never from the Executive
    Summary) plus the evidence-limitation detail. Rendering only."""
    _page_header(
        "UNDERSTAND", "layers", "What does this mean?",
        "What is happening, what it means, capability posture, evidenced context, people, buying context and gaps.",
    )
    _render_evidence_legend(report)

    entry = dict(st.session_state.get("entry") or {})
    entry["report"] = report
    render_understand(build_understand_view(entry))

    # Evidence limitations ("What we don't know" detail): excluded sources
    # and weak/incidental mappings stay visible for traceability.
    q = report.get("qualification", {})
    disqualified_count = q.get("disqualified_count", 0)
    weak_count = weak_evidence_total(report.get("capability_summary", {}))
    notes = []
    if disqualified_count:
        notes.append(f"{disqualified_count} source(s) found during research were excluded from analysis as unusable.")
    if weak_count:
        notes.append(
            f"{weak_count} capability mapping(s) are weak/incidental relevance: retained for traceability, "
            "never used to support a capability hypothesis or sales action."
        )
    if notes:
        st.caption(" ".join(notes))
    disqualified_reasons = q.get("disqualified_reasons", [])
    if disqualified_reasons:
        with st.expander(f"Disqualified sources ({len(disqualified_reasons)})"):
            for item in disqualified_reasons[:30]:
                st.markdown(f"- {item.get('reason', 'No reason provided')}")
                if item.get("url"):
                    st.caption(item.get("url"))



def _render_decide(
    report: dict[str, Any],
    opportunities: list[dict[str, Any]],
) -> None:
    _page_header(
        "DECIDE", "target", "Where should Otech focus?",
        "Turn evidence-backed capability hypotheses into focused validation conversations.",
    )

    # DECIDE v2: one deterministic decision from existing outputs
    # (ui/decide_view.py). The opportunity board below remains the
    # opportunity detail; Opportunity Intelligence stays the authority.
    decision = build_decision({"report": report, "opportunity_hypotheses": opportunities})
    st.markdown(decision_header_html(decision), unsafe_allow_html=True)

    left, right = st.columns([1.05, 1], gap="medium")
    with left:
        st.markdown(reasons_html(decision), unsafe_allow_html=True)
        st.markdown(known_html(decision), unsafe_allow_html=True)
    with right:
        st.markdown(validate_html(decision), unsafe_allow_html=True)
        st.markdown(engage_html(decision), unsafe_allow_html=True)
        if decision.stakeholder:
            st.button(
                "Open in PEOPLE",
                key="decide_open_person",
                icon=":material/person_search:",
                on_click=_open_person_in_people,
                args=(decision.stakeholder.person_id,),
            )
        st.markdown(action_html(decision), unsafe_allow_html=True)
        if decision.state != "NO ACTION":
            st.button(
                "Take to ACT",
                key="decide_take_to_act",
                type="primary",
                icon=":material/arrow_forward:",
                on_click=_take_decision_to_act,
                args=(decision.handoff(),),
            )
    st.markdown(unknown_html(decision), unsafe_allow_html=True)

    if not opportunities:
        st.caption(
            "Opportunity Intelligence qualifies no opportunity for this account. The recommendation above is a "
            "decision about what to do next, not an opportunity."
        )
        return

    # ================================================================
    # OPPORTUNITY BOARD
    # ================================================================
    st.markdown("### Opportunity board")

    st.caption(
        "These are hypotheses worth validating — not confirmed customer requirements."
    )

    for idx, opportunity in enumerate(
        opportunities,
        1,
    ):

        capability = opportunity.get(
            "capability_name",
            "Capability hypothesis",
        )

        score = opportunity.get(
            "score",
            0,
        )

        priority = priority_label(
            opportunity.get("priority")
        )

        epistemic_status = epistemic_status_label(
            opportunity.get("epistemic_status")
        )

        hypothesis = opportunity.get(
            "hypothesis",
            "No hypothesis provided.",
        )

        rationale = opportunity.get(
            "rationale",
            "No rationale provided.",
        )

        who = opportunity.get(
            "who",
            "Stakeholder not identified.",
        )

        conversation = opportunity.get(
            "conversation",
            "Validate whether this capability is relevant to the account.",
        )

        evidence_count = opportunity.get(
            "evidence_count",
            0,
        )

        independent_domains = opportunity.get(
            "independent_domain_count",
            0,
        )

        inference_clusters = opportunity.get(
            "inference_cluster_count",
            0,
        )

        confidence = opportunity.get(
            "confidence",
            0,
        )

        incumbent_context = opportunity.get(
            "incumbent_context",
            [],
        )

        source_urls = opportunity.get(
            "source_urls",
            [],
        )

	
        # ============================================================
        # OPPORTUNITY CARD
        # ============================================================
        with st.container(border=True):

            # In-page anchor: DECIDE's opportunity facts link here.
            st.markdown(
                f'<div id="ar-opp-{_esc(opportunity.get("capability_id", ""))}"></div>',
                unsafe_allow_html=True,
            )
            top_left, top_mid, top_right = st.columns(
                [5, 2, 2]
            )

            with top_left:

                st.markdown(
                    f"### {idx}. {capability}"
                )

                st.caption(
                    "Opportunity hypothesis"
                )

            with top_mid:

                st.metric(
                    "Evidence score",
                    score,
                )

            with top_right:

                st.markdown(
                    f"**{priority}**"
                )

                st.caption(
                    epistemic_status
                )

            st.markdown("---")

            # ========================================================
            # WHY THIS IS ON THE RADAR
            # ========================================================
            st.markdown(
                "#### Why this is on the radar"
            )

            st.markdown(
                f"""
                <div class="ar-decision-hypothesis">
                    {hypothesis}
                </div>
                """,
                unsafe_allow_html=True,
            )

            st.caption(
                rationale
            )

            # ========================================================
            # ENGAGEMENT
            # ========================================================
            engage_col, angle_col = st.columns(2)

            with engage_col:

                st.markdown(
                    "**Who to engage**"
                )

                st.write(
                    who
                )

            with angle_col:

                st.markdown(
                    "**Conversation angle**"
                )

                st.write(
                    conversation
                )

            # ========================================================
            # EVIDENCE STRENGTH
            # ========================================================
            st.markdown(
                "#### Evidence strength"
            )

            e1, e2, e3, e4 = st.columns(4)

            with e1:

                st.metric(
                    "Evidence items",
                    evidence_count,
                )

            with e2:

                st.metric(
                    "Independent domains",
                    independent_domains,
                )

            with e3:

                st.metric(
                    "Evidence clusters",
                    inference_clusters,
                )

            with e4:

                if isinstance(
                    confidence,
                    (int, float),
                ):

                    confidence_display = (
                        f"{confidence:.0%}"
                        if confidence <= 1
                        else f"{confidence:.0f}%"
                    )

                else:

                    confidence_display = str(
                        confidence
                    )

                st.metric(
                    "Confidence",
                    confidence_display,
                )

            # ========================================================
            # CURRENT STATE
            # ========================================================
            if incumbent_context:

                st.markdown(
                    "#### Current-state context"
                )

                st.caption(
                    "Incumbent/vendor context is context only; "
                    "it does not prove an Otech opportunity."
                )

                st.write(
                    " · ".join(
                        str(x)
                        for x in incumbent_context
                        if x
                    )
                )

            # ========================================================
            # SUPPORTING SOURCES
            # ========================================================
            if source_urls:

                with st.expander(
                    f"Supporting evidence ({len(source_urls)})"
                ):

                    for url in source_urls:

                        st.link_button(
                            "Open source",
                            url,
                        )

        st.write("")

    # ================================================================
    # DECISION DISCIPLINE
    # ================================================================
    st.markdown(
        "### Decision discipline"
    )

    d1, d2, d3 = st.columns(3)

    with d1:

        st.markdown(
            """
            **1 · Validate**

            Treat the hypothesis as a conversation starter,
            not as a customer requirement.
            """
        )

    with d2:

        st.markdown(
            """
            **2 · Test**

            Confirm the business problem, technology context,
            stakeholder ownership and timing with the account.
            """
        )

    with d3:

        st.markdown(
            """
            **3 · Progress**

            Only move toward an opportunity when customer
            validation strengthens the evidence.
            """
        )


def _render_act(entry: dict[str, Any]) -> None:
    """ACT v2: the execution layer after DECIDE (ui/act_view.py). Works from
    the context handed over by "Take to ACT", or from the current DECIDE
    result for the loaded account -- never re-runs research. Optional AI
    next-best actions are shown separately and never replace the plan."""
    _page_header(
        "ACT", "bolt", "What do I do next?",
        "The next step for this account, carried over from DECIDE.",
    )
    report = entry.get("report", {}) or {}
    context, handed_over = act_context_for(entry, st.session_state.get("act_context"))
    plan = build_action_plan(context, handed_over=handed_over)

    st.markdown(act_header_html(plan), unsafe_allow_html=True)
    left, right = st.columns([1.15, 1], gap="medium")
    with left:
        st.markdown(act_why_now_html(plan), unsafe_allow_html=True)
        objective = act_objective_html(plan)
        if objective:
            st.markdown(objective, unsafe_allow_html=True)
        questions = act_questions_html(plan)
        if questions:
            st.markdown(questions, unsafe_allow_html=True)
    with right:
        st.markdown(act_stakeholder_html(plan), unsafe_allow_html=True)
        with st.container(horizontal=True, key="ar_act_controls"):
            if plan.stakeholder and plan.stakeholder.get("person_id"):
                st.button("Open in PEOPLE", key="act_open_person", icon=":material/person_search:",
                          on_click=_open_person_in_people, args=(str(plan.stakeholder["person_id"]),))
            st.button("Back to DECIDE", key="act_back_to_decide", icon=":material/arrow_back:",
                      on_click=_navigate_to, args=("Opportunities",))
        st.markdown(act_evidence_html(plan), unsafe_allow_html=True)

    # Suggested outreach: only when DECIDE recommends engaging someone.
    st.markdown('<div class="ar-exec-label" style="margin-top:6px">Suggested outreach</div>', unsafe_allow_html=True)
    if plan.outreach_recommended:
        channel = st.radio("Channel", ["Email", "LinkedIn"], key="act_outreach_channel", horizontal=True,
                           label_visibility="collapsed")
        text = plan.outreach_email if channel == "Email" else plan.outreach_linkedin
        st.text_area(f"{channel} draft (editable)", value=text, height=320,
                     key=f"act_outreach_{channel.lower()}_{_slug(plan.organization_name)}")
        st.caption("Review and personalize before sending. Account Radar does not send the message automatically.")
        with st.expander("Discovery brief (copy-ready)"):
            st.text_area("Brief", value=handoff_brief(context), height=240, key="act_brief",
                         label_visibility="collapsed")
    else:
        st.info(
            "No outreach is recommended for this decision. "
            + ("Monitor the account and revisit when the development turns into a reported initiative."
               if plan.state == "MONITOR" else "There is no account-specific reason to contact the account now.")
        )

    _render_act_ai_enrichment(entry)


def _render_act_ai_enrichment(entry: dict[str, Any]) -> None:
    """Optional AI enrichment: the synthesis' grounded next-best actions,
    clearly labelled. ACT's plan above never depends on it."""
    synthesis = entry.get("ai_synthesis", {}) or {}
    actions = synthesis.get("next_best_actions") or []
    if synthesis.get("status") not in ("ok", "degraded") or not actions:
        st.caption("AI suggestions are not available for this run; the plan above does not depend on them.")
        return
    with st.expander(f"AI suggestions ({len(actions)}) · optional enrichment"):
        for action in actions:
            name = action.get("capability_name") or "Suggestion"
            status = epistemic_status_label(action.get("epistemic_status", "")) if action.get("epistemic_status") else ""
            st.markdown(f"**{name}**" + (f" · {status}" if status else ""))
            if action.get("rationale"):
                st.write(action["rationale"])
        st.caption("AI interpretation of the capability evidence. Verify against the sources before acting.")


def _research_store_key(entry: dict[str, Any]) -> str:
    report = entry.get("report", {})
    return str(report.get("organization_name") or id(entry))


def _get_research_overrides(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    store = st.session_state.setdefault("research_overrides", {})
    return store.setdefault(_research_store_key(entry), {})


def _render_research(entry: dict[str, Any]) -> None:
    """Research v2: compact intelligence feed with human review controls.

    Public evidence, AI interpretation and account-manager opinion remain
    separate. Changes affect only the current prototype session and never
    mutate the original research report.
    """
    _page_header(
        "RESEARCH", "search", "What does the public evidence actually say?",
        "A working intelligence feed: inspect the evidence, understand the AI interpretation, "
        "then validate or correct it.",
    )

    disqualified = (entry.get("report", {}).get("qualification", {}) or {}).get("disqualified_reasons", []) or []
    view = st.radio(
        "Sources",
        [RESEARCH_VIEW_QUALIFIED, RESEARCH_VIEW_DISQUALIFIED],
        key="research_source_view",
        horizontal=True,
        label_visibility="collapsed",
    )
    if view == RESEARCH_VIEW_DISQUALIFIED:
        st.caption(f"{len(disqualified)} source(s) were excluded from analysis as unusable.")
        if not disqualified:
            st.info("No sources were disqualified in this research run.")
        for item in disqualified:
            url = item.get("url") or ""
            st.markdown(
                '<div class="ar-panel ar-disq">'
                f'<div class="ar-panel-head"><div class="ar-panel-title">{_icon("alert")} {_esc(_domain(url) or "Source")}</div></div>'
                f'<div class="ar-block-text">{_esc(item.get("reason") or "No reason provided")}</div>'
                + (f'<div class="ar-factors"><a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(url)}</a></div>' if url else "")
                + "</div>",
                unsafe_allow_html=True,
            )
        return

    items = research_items(entry)
    overrides = _get_research_overrides(entry)
    if not items:
        st.info("No public research items are available for this account.")
        return

    # Remove items explicitly deleted from this workspace. The original report is untouched.
    visible_items = [i for i in items if not overrides.get(i["id"], {}).get("deleted")]
    statuses = ["All", "Unverified", "Needs Review", "Validated", "Rejected"]
    keybase = _research_store_key(entry)
    filter_value = st.selectbox("Review status", statuses, key=f"research_filter::{keybase}")
    search_text = st.text_input(
        "Search intelligence",
        placeholder="Source, capability, evidence, person, keyword…",
        key=f"research_search::{keybase}",
    ).strip().lower()

    counts = {status: 0 for status in statuses[1:]}
    for item in visible_items:
        status = overrides.get(item["id"], {}).get("status", "Unverified")
        counts[status] = counts.get(status, 0) + 1

    summary = st.columns(5)
    summary[0].metric("Items", len(visible_items))
    for idx, status in enumerate(statuses[1:], 1):
        summary[idx].metric(status, counts.get(status, 0))

    shown: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in visible_items:
        state = overrides.get(item["id"], {})
        status = state.get("status", "Unverified")
        haystack = " ".join([
            item.get("title", ""), item.get("text", ""), item.get("why_relevant", ""),
            item.get("capability", ""), " ".join(item.get("matched_terms", [])),
        ]).lower()
        if filter_value != "All" and status != filter_value:
            continue
        if search_text and search_text not in haystack:
            continue
        shown.append((item, state))

    st.caption(f"Showing {len(shown)} of {len(visible_items)} intelligence items")

    for item, state in shown:
        status = state.get("status", "Unverified")
        level = evidence_level_label(item.get("evidence_level", "")) or "Evidence"
        category = evidence_category_label(item.get("evidence_category"))
        capability = item.get("capability") or "General account intelligence"
        source_excerpt = item.get("text") or ""
        edited_text = state.get("edited_text") or ""
        opinion = state.get("opinion") or ""
        expanded = state.get("expanded", False)

        with st.container(border=True):
            head = st.columns([6, 2, 1.3])
            head[0].markdown(f"**{item.get('title') or 'Untitled research item'}**")
            head[0].caption(f"{capability}  ·  {category}")
            head[1].markdown(f"**{level}**")
            head[1].caption(status)
            head[2].caption(f"Confidence: {item.get('confidence', '—')}")

            action = st.columns([1.2, 1.2, 1.2, 1.2, 1.4])
            toggle_label = "Hide evidence" if expanded else "Expand evidence"
            if action[0].button(toggle_label, key=f"expand::{item['id']}"):
                state["expanded"] = not expanded
                overrides[item["id"]] = state
                st.rerun()
            if action[1].button("✓ Validate", key=f"validate::{item['id']}"):
                state["status"] = "Validated"
                state["deleted"] = False
                overrides[item["id"]] = state
                st.rerun()
            if action[2].button("⚠ Review", key=f"review::{item['id']}"):
                state["status"] = "Needs Review"
                overrides[item["id"]] = state
                st.rerun()
            if action[3].button("✕ Reject", key=f"reject::{item['id']}"):
                state["status"] = "Rejected"
                overrides[item["id"]] = state
                st.rerun()
            if action[4].button("✎ Edit / opinion", key=f"edit::{item['id']}"):
                state["editing"] = not state.get("editing", False)
                state["expanded"] = True
                overrides[item["id"]] = state
                st.rerun()

            if expanded:
                st.divider()
                st.markdown("**SOURCE SAYS**")
                if source_excerpt:
                    st.info(source_excerpt)
                elif item.get("source_context"):
                    st.info(item["source_context"])
                    st.caption("Relevant source passage retained by the research pipeline; verify against the original source.")
                else:
                    st.warning("No usable evidence excerpt is available for this item. Account Radar will not invent one.")

                if item.get("published_at"):
                    recency = item.get("recency") or research_recency(item.get("published_at"))
                    st.caption(f"Published: {item['published_at']} · Freshness: {recency.get('freshness_band', 'unknown').replace('_', ' ').title()} · {recency.get('age_label', 'Date unavailable')}")
                if item.get("source_url"):
                    st.link_button("Open original source", item["source_url"])

                st.markdown("**ACCOUNT RADAR THINKS**")
                st.write(item.get("why_relevant") or "No AI relevance interpretation was retained.")

                evidence_meta = []
                if item.get("evidence_level"):
                    evidence_meta.append(f"Evidence: {level}")
                if category:
                    evidence_meta.append(f"Category: {category}")
                if item.get("epistemic_status"):
                    evidence_meta.append(f"Epistemic status: {epistemic_status_label(item.get('epistemic_status'))}")
                if item.get("matched_terms"):
                    evidence_meta.append("Matched: " + ", ".join(item["matched_terms"]))
                if evidence_meta:
                    st.caption(" · ".join(evidence_meta))

                if edited_text:
                    st.markdown("**ACCOUNT MANAGER EDIT**")
                    st.warning(edited_text)
                if opinion:
                    st.markdown("**ACCOUNT MANAGER SAYS**")
                    st.success(opinion)

                if state.get("editing"):
                    st.markdown("**EDIT INTELLIGENCE**")
                    corrected = st.text_area(
                        "Corrected source note / interpretation",
                        value=edited_text or source_excerpt,
                        key=f"edited_text::{item['id']}",
                    )
                    comment = st.text_area(
                        "My opinion / account-manager comment",
                        value=opinion,
                        key=f"opinion::{item['id']}",
                    )
                    save = st.columns(2)
                    if save[0].button("Save changes", key=f"save::{item['id']}"):
                        state["edited_text"] = corrected
                        state["opinion"] = comment
                        state["status"] = "Needs Review"
                        state["editing"] = False
                        overrides[item["id"]] = state
                        st.rerun()
                    if save[1].button("Delete from workspace", key=f"delete::{item['id']}"):
                        state["deleted"] = True
                        state["status"] = "Rejected"
                        state["editing"] = False
                        overrides[item["id"]] = state
                        st.rerun()

def _people_bucket_members(people: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Members (person_ids) of each PEOPLE summary bucket -- delegated to
    commercial.people.group_members (People Intelligence v2), which derives
    groups from each person's taxonomy role, functions and technology areas.
    A person can be in more than one bucket; Technology Leadership requires
    leadership seniority AND technology relevance; "Other" is residual."""
    from commercial.people import group_members

    return group_members(people)


def _people_summary_buckets(people: list[dict[str, Any]]) -> dict[str, int]:
    """Summary counts for the PEOPLE tab header (see _people_bucket_members)."""
    return {label: len(members) for label, members in _people_bucket_members(people).items()}


def _filter_people(
    people: list[dict[str, Any]],
    *,
    search: str = "",
    function: str = "All",
    seniority: str = "All",
    influence: str = "All",
    technology: str = "All",
    bucket: str | None = None,
) -> list[dict[str, Any]]:
    """Pure local filtering over an already-built people list -- never
    triggers research/discovery. All criteria are cumulative (AND).
    `bucket` is one of the summary-card buckets, membership computed over
    the full people list so it matches the card's count.
    """
    filtered = people
    if bucket:
        members = _people_bucket_members(people).get(bucket, set())
        filtered = [p for p in filtered if p.get("person_id") in members]
    q = search.strip().lower()
    if q:
        filtered = [
            p for p in filtered
            if q in (p.get("name") or "").lower() or q in (p.get("current_title") or "").lower()
        ]
    if function != "All":
        filtered = [p for p in filtered if function in p.get("functions", [])]
    if seniority != "All":
        filtered = [p for p in filtered if p.get("seniority") == seniority]
    if influence != "All":
        filtered = [p for p in filtered if p.get("influence_type") == influence]
    if technology != "All":
        filtered = [p for p in filtered if technology in p.get("technology_areas", [])]
    return filtered


# PEOPLE filter state (session_state keys) and their "no filter" values.
PEOPLE_FILTER_DEFAULTS = {
    "people_bucket": None,
    "people_search": "",
    "people_function_filter": "All",
    "people_seniority_filter": "All",
    "people_influence_filter": "All",
    "people_tech_filter": "All",
}
# Card chip dimension -> the filter it sets.
PEOPLE_CHIP_FILTER_KEYS = {
    "function": "people_function_filter",
    "technology": "people_tech_filter",
    "influence": "people_influence_filter",
}


def _people_toggle_bucket(label: str) -> None:
    current = st.session_state.get("people_bucket")
    st.session_state["people_bucket"] = None if current == label else label


def _people_set_filter(key: str, value: str) -> None:
    st.session_state[key] = value


def _people_clear_filters() -> None:
    for key, value in PEOPLE_FILTER_DEFAULTS.items():
        st.session_state[key] = value


def _people_toggle_selected(pid: str) -> None:
    st.session_state["people_selected"] = None if st.session_state.get("people_selected") == pid else pid


def _people_active_filters() -> list[tuple[str, str]]:
    """(dimension, value) pairs for every active PEOPLE filter."""
    labels = {
        "people_bucket": "Group",
        "people_search": "Search",
        "people_function_filter": "Function",
        "people_seniority_filter": "Seniority",
        "people_influence_filter": "Influence",
        "people_tech_filter": "Technology",
    }
    out = []
    for key, default in PEOPLE_FILTER_DEFAULTS.items():
        value = st.session_state.get(key, default)
        if isinstance(value, str):
            value = value.strip()
        if value and value != default:
            out.append((labels[key], str(value).replace("_", " ")))
    return out


def _why_this_person_matters(person: dict[str, Any]) -> str:
    """Short evidence-derived explanation -- templated purely from fields
    commercial.people already computed on this person; introduces no new
    inference and no new chip/classification logic.
    """
    chips = person.get("chips", [])
    role_label = next((c["label"] for c in chips if c.get("category") == "role"), None)
    functions = person.get("functions", [])
    tech_areas = person.get("technology_areas", [])
    influence_label = next((c["label"] for c in chips if c.get("category") == "influence"), None)

    parts: list[str] = []
    if role_label:
        parts.append(f"Holds the {role_label} role")
    elif person.get("seniority"):
        parts.append(f"Senior ({person['seniority'].replace('_', ' ')}) stakeholder")
    if functions:
        parts.append("involved in " + ", ".join(f.replace("_", " ") for f in functions))
    if tech_areas:
        parts.append("with evidence of engagement in " + ", ".join(t.replace("_", " ") for t in tech_areas))
    if influence_label:
        parts.append(f"classified as a {influence_label.lower()}")

    if not parts:
        return "Associated with the organization; limited additional classification evidence available."
    text = "; ".join(parts) + "."
    return text[0].upper() + text[1:]


PEOPLE_BUCKET_STYLE = {
    "Technology Leadership": ("users", "k-blue"),
    "Cybersecurity": ("shield", "k-green"),
    "Infrastructure / Cloud": ("cloud", "k-amber"),
    "Digital / Data": ("database", "k-red"),
    "Procurement": ("cart", "k-purple"),
    "Other": ("users", "k-slate"),
}

PEOPLE_CHIP_ACCENT = {"role": "k-blue", "function": "k-green", "technology": "k-purple", "influence": "k-amber"}


def _initials(name: str) -> str:
    parts = [p for p in str(name or "").replace("-", " ").split() if p[:1].isalpha()]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


def _people_chip_values(person: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(dimension, filter value, display label) for the person's chips that
    correspond to real filter dimensions. Values come from the structured
    fields; labels from the person's own chips (same order as built by
    commercial.people)."""
    chips = person.get("chips", [])
    out: list[tuple[str, str, str]] = []
    for dim, field_values in (
        ("function", person.get("functions", []) or []),
        ("technology", person.get("technology_areas", []) or []),
        ("influence", [person["influence_type"]] if person.get("influence_type") else []),
    ):
        labels = [c.get("label") for c in chips if c.get("category") == dim]
        for i, value in enumerate(field_values):
            label = labels[i] if i < len(labels) and labels[i] else str(value).replace("_", " ")
            out.append((dim, value, label))
    return out


def _render_people_card(person: dict[str, Any]) -> None:
    chips = person.get("chips", [])
    pid = str(person.get("person_id") or person.get("name") or "unknown")
    pkey = _slug(pid)
    selected = st.session_state.get("people_selected") == pid
    role_label = next((c["label"] for c in chips if c.get("category") == "role"), None)
    association = person.get("organization_association") or "—"
    assoc_cls = {"confirmed": "is-ok", "weak": "is-warn"}.get(association, "is-muted")
    factors = person.get("score_factors", {})
    factor_text = " · ".join(f"{k.replace('_', ' ')} +{v:g}" for k, v in factors.items() if v)

    with st.container(border=True, key=f"ar_person_{'selected_' if selected else ''}{pkey}"):
        # Static chips (role / evidence) stay as tags; function, technology
        # and influence chips are filter controls below the header.
        static_tags = "".join(
            f'<span class="ar-tag {PEOPLE_CHIP_ACCENT.get(c.get("category"), "k-slate")}" title="{_esc(c.get("evidence", ""))}">{_esc(c.get("label"))}</span>'
            for c in chips if c.get("category") not in PEOPLE_CHIP_FILTER_KEYS
        )
        from commercial.people import sales_priority

        tier, tier_label = sales_priority(person)
        static_tags = (f'<span class="ar-tag k-slate" title="Sales priority tier {tier} of 7">{_esc(tier_label)}</span>'
                       + static_tags)
        factors_html = f'<div class="ar-factors">{_esc(factor_text)}</div>' if factor_text else ""
        st.markdown(
            '<div class="ar-person-head">'
            f'<div class="ar-avatar">{_esc(_initials(person.get("name")))}</div>'
            '<div>'
            f'<div class="ar-person-name">{_esc(person.get("name") or "Unknown")}</div>'
            f'<div class="ar-person-title">{_esc(person.get("current_title") or "—")}</div>'
            f'<div class="ar-tags">{static_tags}</div>'
            '</div></div>',
            unsafe_allow_html=True,
        )
        chip_values = _people_chip_values(person)
        if chip_values:
            with st.container(horizontal=True, key=f"ar_person_chips_{pkey}", gap="small"):
                for dim, value, label in chip_values:
                    key = PEOPLE_CHIP_FILTER_KEYS[dim]
                    active = st.session_state.get(key) == value
                    st.button(
                        label,
                        key=f"people_chip_{pkey}_{dim}_{_slug(value)}",
                        type="primary" if active else "secondary",
                        help=f"Filter stakeholders by {dim}: {label}",
                        on_click=_people_set_filter,
                        args=(key, "All" if active else value),
                    )
        st.markdown(
            f'<div class="ar-block-title">{_icon("bulb")} Why this person matters</div>'
            f'<div class="ar-block-text">{_esc(_why_this_person_matters(person))}</div>'
            '<div class="ar-person-scores">'
            '<div class="ar-score-box"><div class="ar-score-label">Relevance</div>'
            f'<div class="ar-score-value">{int(round(person.get("relevance_score", 0) or 0))}</div></div>'
            '<div class="ar-score-box is-plain"><div class="ar-score-label">Organization association</div>'
            f'<div class="ar-score-value {assoc_cls}">{_esc(str(association).title())}</div></div>'
            '</div>'
            f'{factors_html}',
            unsafe_allow_html=True,
        )
        st.button(
            "Hide intelligence" if selected else "View intelligence",
            key=f"people_select_{pkey}",
            icon=":material/expand_less:" if selected else ":material/expand_more:",
            on_click=_people_toggle_selected,
            args=(pid,),
        )
        if not selected:
            return

        mid_col, side_col = st.columns([1.5, 0.8], gap="medium")
        with mid_col:
            links = "".join(
                f'<li><a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(_domain(url) or url)} &#8599;</a></li>'
                for url in person.get("source_urls", [])
            )
            assoc_evidence = person.get("organization_association_evidence") or []
            assoc_html = f'<div class="ar-factors">{_esc("; ".join(assoc_evidence))}</div>' if assoc_evidence else ""
            st.markdown(
                f'<div class="ar-block-title" style="margin-top:0">{_icon("users")} Source says</div>'
                f'<div class="ar-block-text">{_esc(person.get("classification_text") or "No title/snippet text was available.")}</div>'
                f'{f"<ul class=ar-links>{links}</ul>" if links else ""}'
                f'<div class="ar-block-title">{_icon("chart")} Account Radar thinks</div>'
                '<dl class="ar-dl">'
                f'<dt>Role</dt><dd>{_esc(role_label or "—")}</dd>'
                f'<dt>Seniority</dt><dd>{_esc((person.get("seniority") or "—").replace("_", " "))}</dd>'
                f'<dt>Functions</dt><dd>{_esc(", ".join(person.get("functions", [])) or "—")}</dd>'
                f'<dt>Technology areas</dt><dd>{_esc(", ".join(person.get("technology_areas", [])) or "—")}</dd>'
                f'<dt>Influence type</dt><dd>{_esc((person.get("influence_type") or "—").replace("_", " "))}</dd>'
                f'<dt>Organization association</dt><dd>{_esc(association)}</dd>'
                f'<dt>Confidence</dt><dd>{(person.get("confidence", 0) or 0):.0f}/100</dd>'
                f'<dt>Discovered via</dt><dd>{_esc(" · ".join(str(x).title() for x in person.get("discovery_providers") or []) or "—")}</dd>'
                '</dl>'
                f'{assoc_html}',
                unsafe_allow_html=True,
            )
        with side_col:
            signals = person.get("recent_signals") or []
            signal_html = (
                "<ul>" + "".join(f"<li>{_esc(sig)}</li>" for sig in signals) + "</ul>"
                if signals else '<div class="ar-block-text">No recent signals recorded for this person.</div>'
            )
            st.markdown(
                f'<div class="ar-side-box"><div class="ar-block-title" style="margin-top:0">{_icon("clock")} Recent signals</div>'
                f'{signal_html}</div>',
                unsafe_allow_html=True,
            )

        _render_people_outreach(
            person,
            person.get("organization_name") or "this account",
        )


def _build_outreach_draft(
    person: dict[str, Any],
    account_name: str,
    purpose: str,
    tone: str,
    channel: str,
) -> str:
    name = person.get("name") or "there"
    title = person.get("current_title") or ""
    technology = person.get("technology_areas", [])
    signals = person.get("recent_signals", [])

    first_name = name.split()[0] if name and name != "there" else "there"

    tech_text = ", ".join(
        str(x).replace("_", " ") for x in technology[:3]
    )
    signal_text = str(signals[0]) if signals else ""

    if purpose == "Request introductory meeting":
        subject = f"Introduction and discussion with {account_name}"
        opening = (
            f"I wanted to reach out regarding {account_name} and the technology "
            f"priorities relevant to your role."
        )
        ask = (
            "Would you be open to a short 20-minute discussion at a convenient "
            "time? I would be happy to keep it focused on your priorities and "
            "where we may be able to contribute."
        )
    elif purpose == "Discuss a specific technology area":
        subject = f"{account_name} - {tech_text or 'technology priorities'}"
        opening = (
            f"I wanted to connect regarding {tech_text or 'some of the technology priorities'} "
            f"that may be relevant to {account_name}."
        )
        ask = (
            "Would you be available for a brief discussion to understand your "
            "current priorities and challenges in this area?"
        )
    elif purpose == "Follow up on a recent initiative":
        subject = f"{account_name} - recent technology priorities"
        opening = (
            f"I came across some recent developments relevant to {account_name} "
            "and thought it would be useful to connect."
        )
        if signal_text:
            opening += f" In particular, {signal_text}."
        ask = (
            "I would appreciate 20 minutes to understand how this aligns with "
            "your priorities and whether there is an opportunity for us to help."
        )
    else:
        subject = f"Introduction - {account_name}"
        opening = (
            f"I wanted to introduce myself and connect regarding the technology "
            f"priorities at {account_name}."
        )
        ask = (
            "If useful, I would be glad to arrange a short introductory discussion "
            "at a convenient time."
        )

    if tone == "Executive / concise":
        greeting = f"Dear {first_name},"
        closing = "Regards,"
        body = (
            f"{greeting}\n\n"
            f"{opening}\n\n"
            f"{ask}\n\n"
            f"{closing}\n"
            "Sukanta"
        )
    elif tone == "Consultative":
        greeting = f"Dear {first_name},"
        closing = "Best regards,"
        body = (
            f"{greeting}\n\n"
            f"I have been following the technology priorities relevant to "
            f"{account_name}, and given your role as {title or 'a senior stakeholder'}, "
            "I thought it would be useful to connect directly.\n\n"
            f"{opening}\n\n"
            f"{ask}\n\n"
            f"I will be happy to keep the conversation brief and focused on your "
            "current priorities rather than make it a formal sales presentation.\n\n"
            f"{closing}\n"
            "Sukanta"
        )
    else:
        greeting = f"Hello {first_name},"
        closing = "Best regards,"
        body = (
            f"{greeting}\n\n"
            f"{opening}\n\n"
            f"{ask}\n\n"
            f"{closing}\n"
            "Sukanta"
        )

    if channel == "LinkedIn":
        return body

    return f"Subject: {subject}\n\n{body}"


def _render_people_outreach(person: dict[str, Any], account_name: str) -> None:
    pid = person.get('person_id', person.get('name', 'unknown'))
    bar = st.container(key=f"ar_outreach_{pid}")
    title_col, purpose_col, tone_col, channel_col, button_col = bar.columns(
        [0.9, 2, 2, 1.5, 1.3], vertical_alignment="bottom"
    )
    title_col.markdown(f'<div class="ar-outreach-title">{_icon("send")} Outreach</div>', unsafe_allow_html=True)

    purpose = purpose_col.selectbox(
        "Purpose",
        [
            "Request introductory meeting",
            "Discuss a specific technology area",
            "Follow up on a recent initiative",
            "General relationship building",
        ],
        key=f"people_outreach_purpose_{person.get('person_id', person.get('name', 'unknown'))}",
    )

    tone = tone_col.selectbox(
        "Tone",
        [
            "Executive / concise",
            "Professional / warm",
            "Consultative",
        ],
        key=f"people_outreach_tone_{person.get('person_id', person.get('name', 'unknown'))}",
    )

    channel = channel_col.selectbox(
        "Channel",
        ["Email", "LinkedIn"],
        key=f"people_outreach_channel_{person.get('person_id', person.get('name', 'unknown'))}",
    )

    if button_col.button(
        "Draft outreach",
        key=f"people_outreach_button_{person.get('person_id', person.get('name', 'unknown'))}",
        type="primary",
        icon=":material/mail:",
        use_container_width=True,
    ):
        draft = _build_outreach_draft(
            person,
            account_name,
            purpose,
            tone,
            channel,
        )
        st.session_state[
            f"people_outreach_draft_{person.get('person_id', person.get('name', 'unknown'))}"
        ] = draft

    draft_key = (
        f"people_outreach_draft_"
        f"{person.get('person_id', person.get('name', 'unknown'))}"
    )

    if st.session_state.get(draft_key):
        bar.text_area(
            "Draft",
            value=st.session_state[draft_key],
            height=280,
            key=f"people_outreach_text_{person.get('person_id', person.get('name', 'unknown'))}",
        )
        bar.caption(
            "Review and personalize before sending. Account Radar does not send "
            "the message automatically."
        )


def _render_people(report: dict[str, Any]) -> None:
    _page_header(
        "PEOPLE", "users", "Who should we engage?",
        "Who are the important technology and business stakeholders in this account, what do they appear to care about, and why should I engage them.",
    )

    people = report.get("people", [])
    if not people:
        st.info("No stakeholder profiles were discovered for this account.")
        return

    for key, value in PEOPLE_FILTER_DEFAULTS.items():
        st.session_state.setdefault(key, value)
    buckets = _people_summary_buckets(people)
    active_bucket = st.session_state.get("people_bucket")
    if active_bucket not in buckets:
        st.session_state["people_bucket"] = active_bucket = None
    # Summary cards are filter controls: a transparent full-card button
    # (theme_engine.py) toggles the group filter; the active card is marked.
    with st.container(key="ar_people_buckets"):
        for label, count in buckets.items():
            icon, accent = PEOPLE_BUCKET_STYLE.get(label, ("users", "k-slate"))
            slug = _slug(label)
            with st.container(key=f"ar_people_bucket_{slug}"):
                st.markdown(
                    f'<div class="ar-cat {accent}{" is-active" if label == active_bucket else ""}">'
                    f'<div class="ar-kpi-icon">{_icon(icon)}</div>'
                    f'<div><div class="ar-cat-label">{_esc(label)}</div><div class="ar-cat-value">{count}</div></div>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                st.button(
                    f"Filter: {label}",
                    key=f"people_bucket_{slug}",
                    on_click=_people_toggle_bucket,
                    args=(label,),
                )

    all_functions = sorted({f for p in people for f in p.get("functions", [])})
    all_seniorities = sorted({p.get("seniority") for p in people if p.get("seniority")})
    all_influences = sorted({p.get("influence_type") for p in people if p.get("influence_type")})
    all_tech = sorted({t for p in people for t in p.get("technology_areas", [])})
    # A filter value left over from another account is reset, never crashes.
    for key, options in (("people_function_filter", all_functions), ("people_seniority_filter", all_seniorities),
                         ("people_influence_filter", all_influences), ("people_tech_filter", all_tech)):
        if st.session_state.get(key) not in ["All"] + options:
            st.session_state[key] = "All"

    search_col, function_col, seniority_col, influence_col, tech_col = st.columns([2.2, 1, 1, 1, 1])
    search = search_col.text_input(
        "Search by name or title", key="people_search", placeholder="Search people, titles, or keywords…"
    )
    function = function_col.selectbox("Function", ["All"] + all_functions, key="people_function_filter")
    seniority = seniority_col.selectbox("Seniority", ["All"] + all_seniorities, key="people_seniority_filter")
    influence = influence_col.selectbox("Influence", ["All"] + all_influences, key="people_influence_filter")
    technology = tech_col.selectbox("Technology", ["All"] + all_tech, key="people_tech_filter")

    filtered = _filter_people(
        people, search=search, function=function, seniority=seniority, influence=influence, technology=technology,
        bucket=active_bucket,
    )
    active = _people_active_filters()
    with st.container(horizontal=True, key="ar_people_filterbar", vertical_alignment="center"):
        st.caption(f"Showing {len(filtered)} of {len(people)} relevant stakeholders")
        if active:
            st.markdown(
                '<div class="ar-active-filters">'
                + "".join(f'<span class="ar-chip is-accent">{_esc(dim)}: {_esc(value)}</span>' for dim, value in active)
                + "</div>",
                unsafe_allow_html=True,
            )
            st.button("Clear filters", key="people_clear_filters", icon=":material/filter_alt_off:",
                      on_click=_people_clear_filters)
    if not filtered:
        st.info("No stakeholders match the current filters.")

    # The backend (commercial.people.build_people) already ranked and
    # capped this list to the target 20-30 range -- the UI only filters
    # locally and never re-ranks or re-selects it.
    # PEOPLE page order: sales-priority tier (enterprise technology sales
    # usefulness), then relevance. Display order only -- relevance_score and
    # the report's people list (read by other sections) are unchanged.
    from commercial.people import sort_people_for_sales

    for person in sort_people_for_sales(filtered):
        _render_people_card(person)


def _load_accounts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Shared by both "load a report" actions below: extracts every
    account entry present in the payload (one, for a live-research-shaped
    payload; possibly several, for a multi-account runner report) and
    stores them so the account selector can be populated from them.
    """
    accounts = extract_organizations(payload)
    st.session_state.accounts = accounts
    return accounts


def _render_account_selector() -> None:
    """Multi-account selector for an ALREADY-LOADED report (Phase 2 UI
    hardening): when the most recently loaded report/upload contains more
    than one organization, show a dropdown -- populated purely from the
    organization names actually present in that JSON, never hardcoded --
    that lets the user switch which account the WATCH/UNDERSTAND/DECIDE/ACT
    dashboard below renders. Selecting a different account only swaps
    `st.session_state.entry` to that account's already-loaded data; it
    never calls `_run_live`/research/Analyze account, and it never runs for
    a single-account load (live research, or a report/upload containing
    exactly one organization), which keeps existing single-account
    behavior identical to before this selector existed.
    """
    accounts = st.session_state.get("accounts") or []
    if len(accounts) <= 1:
        return

    labels = account_labels(accounts)
    current_entry = st.session_state.get("entry")
    try:
        default_index = accounts.index(current_entry)
    except ValueError:
        default_index = 0

    st.sidebar.divider()
    st.sidebar.subheader("Account selector")
    # Keying the widget off the current set of labels means a NEWLY loaded
    # report (a different set of accounts) always gets a fresh widget --
    # defaulting to `default_index` -- instead of accidentally reusing a
    # stale selection left over from a previously loaded report.
    widget_key = "account_selector::" + "|".join(labels)
    selected_label = st.sidebar.selectbox(
        f"This report contains {len(accounts)} accounts — choose one to view",
        labels,
        index=default_index,
        key=widget_key,
    )
    selected_index = labels.index(selected_label)
    st.session_state.entry = accounts[selected_index]
    st.session_state.last_account = labels[selected_index]


def _render_sidebar() -> None:
    st.sidebar.header("Account")
    demo = _demo_active()
    # Demo Mode only pre-fills an example account; any organization/country
    # can be entered. Normal mode keeps empty inputs.
    name = st.sidebar.text_input("Organization", value=demo_mode.DEMO.default_organization if demo else "",
                                 placeholder="Enter any organization")
    country = st.sidebar.text_input("Country (optional)", value=demo_mode.DEMO.default_country if demo else "",
                                    placeholder="Country")
    clicked = st.sidebar.button("Analyze account", type="primary", icon=":material/arrow_forward:", use_container_width=True)
    retry = demo and st.session_state.pop("demo_retry", False)
    if retry:  # Demo Mode "Retry research": re-run the last requested account
        request = st.session_state.get("demo_last_request") or {}
        name, country = request.get("organization", ""), request.get("country", "")
    if clicked or retry:
        if not name.strip():
            st.sidebar.error("Enter an organization name.")
        else:
            waiting = nullcontext() if demo else st.spinner("Researching account and building evidence model…")
            with waiting:
                error = _analyze_account(name, country)
            if error and not demo:  # Demo Mode shows its failure panel instead
                st.sidebar.error(error)

    # MVP: reopening saved or local report files is intentionally not exposed --
    # the only workflow is Organization -> Country -> Analyze account. A
    # proper Reports / Account History feature may reintroduce it later.
    # The selector below renders only for multi-account data, which live
    # research never produces, so it stays inert in the MVP.
    _render_account_selector()


def _render_sidebar_footer() -> None:
    st.sidebar.divider()
    if _demo_active():
        st.sidebar.caption("Live demo mode — every analysis runs fresh research against current public sources. "
                           "Research starts only when you select Analyze account.")
        return
    st.sidebar.caption("Phase 2 UI — research is run only when you explicitly select Analyze account. API keys are read from environment variables and are never displayed.")


# ---------------------------------------------------------------------------
# Executive Account Summary (presentation only). All selection/ranking and
# evidence-rule enforcement lives in ui/executive_summary.py; this renderer
# only formats its result. Every value shown comes from the existing report.
# ---------------------------------------------------------------------------

_ASSOCIATION_LABEL = {"confirmed": ("Confirmed association", "is-ok"), "weak": ("Likely association", "is-warn")}


def _exec_opportunity_html(o: ExecutiveOpportunity) -> str:
    status_chip = (
        '<span class="ar-chip is-ok">Confirmed evidence</span>'
        if o.confirmed
        else '<span class="ar-chip is-warn">Sales hypothesis &middot; validate</span>'
    )
    chips = [status_chip, f'<span class="ar-chip is-plain">{_esc(evidence_level_label(o.evidence_level))}</span>']
    if o.evidence_category:
        chips.append(f'<span class="ar-chip is-plain">{_esc(evidence_category_label(o.evidence_category))}</span>')
    note = (
        '<div class="ar-opp-note">Shown as a hypothesis: the evidence chain does not support confirmation.</div>'
        if o.downgraded else ""
    )
    excerpt = f'<div class="ar-opp-evidence">\u201c{_esc(o.evidence_excerpt)}\u201d</div>' if o.evidence_excerpt else ""
    source = ""
    if o.source_url:
        label = o.source_title or _domain(o.source_url) or "Source"
        source = (
            f'<a class="ar-evidence-link" href="{_esc(o.source_url)}" target="_blank" rel="noopener">'
            f'{_esc(label)} &#8599;</a>'
        )
    count = f'{o.source_count} source{"" if o.source_count == 1 else "s"}'
    return (
        f'<div class="ar-opp{" is-confirmed" if o.confirmed else ""}">'
        '<div class="ar-opp-head">'
        f'<div class="ar-opp-name">{_esc(o.capability_name)}</div>'
        f'<div class="ar-opp-conf"><span>{o.confidence:.0f}%</span>confidence</div>'
        '</div>'
        f'<div class="ar-opp-chips">{"".join(chips)}</div>'
        f'<div class="ar-opp-why"><strong>Why it is on the radar:</strong> {_esc(o.why)}</div>'
        f'{excerpt}{note}'
        f'<div class="ar-opp-src">{source}<span>{_esc(count)}</span></div>'
        '</div>'
    )


def _render_executive_summary(report: dict[str, Any], opportunities: list[dict[str, Any]]) -> None:
    es = build_executive_summary(report, opportunities)

    people_value = "&ndash;" if es.stakeholder_count is None else str(es.stakeholder_count)
    pulse = [
        ("Meaningful signals", str(es.meaningful_signal_count), "Account-specific, deduplicated"),
        ("Opportunities", str(es.opportunity_count), "Confirmed + hypotheses"),
        ("High-confidence", str(es.high_confidence_count), "Confirmed, \u2265 75% confidence"),
        ("Stakeholders", people_value, "Confirmed or likely link" if es.stakeholder_count is not None else "Not available"),
    ]
    pulse_html = "".join(
        f'<div class="ar-pulse-item"><div class="ar-pulse-value">{value}</div>'
        f'<div class="ar-pulse-label">{_esc(label)}</div><div class="ar-pulse-sub">{_esc(sub)}</div></div>'
        for label, value, sub in pulse
    )

    summary_html = "".join(f"<p>{_esc(sentence)}</p>" for sentence in es.summary_sentences)

    if es.conversation:
        conversation_html = (
            f'<div class="ar-convo-quote">\u201c{_esc(es.conversation)}\u201d</div>'
            f'<div class="ar-convo-basis">{_esc(es.conversation_basis)}</div>'
        )
    else:
        conversation_html = f'<div class="ar-exec-empty">{_esc(es.conversation_basis)}</div>'

    if es.key_signals:
        signals_html = "".join(
            '<div class="ar-event">'
            f'<div>{_signal_badge(k.signal_type)}</div>'
            f'<div><div class="ar-event-text">{_esc(k.excerpt)}</div>'
            f'<div class="ar-event-src">{_esc(k.source_title or _domain(k.source_url))} &middot; {_esc(k.relationship_label)} &middot; {_esc(k.evidence_label)} &middot; {_esc(k.class_label)}</div></div>'
            f'<a class="ar-evidence-link" href="{_esc(k.source_url)}" target="_blank" rel="noopener">Source &#8599;</a>'
            '</div>'
            for k in es.key_signals
        )
    else:
        signals_html = '<div class="ar-exec-empty">No account-specific signals in the evidence collected.</div>'

    if es.stakeholders:
        people_rows = []
        for s in es.stakeholders:
            p = s.person
            assoc_label, assoc_cls = _ASSOCIATION_LABEL.get(p.get("organization_association"), ("Association unknown", ""))
            link = (
                f'<a class="ar-evidence-link" href="{_esc(p["linkedin_url"])}" target="_blank" rel="noopener">Profile &#8599;</a>'
                if p.get("linkedin_url") else ""
            )
            relevance = (
                f'<span class="ar-chip is-accent">Relevant to {_esc(next((o.capability_name for o in es.confirmed_opportunities + es.hypothesis_opportunities if o.capability_id == s.relevant_capability), s.relevant_capability))}</span>'
                if s.relevant_capability else ""
            )
            people_rows.append(
                '<div class="ar-engage">'
                f'<div class="ar-avatar ar-avatar-sm">{_esc(_initials(p.get("name")))}</div>'
                '<div class="ar-engage-body">'
                f'<div class="ar-engage-name">{_esc(stakeholder_name(p) or "Unnamed stakeholder")}</div>'
                f'<div class="ar-engage-title">{_esc(stakeholder_title(p, report))}</div>'
                f'<div class="ar-opp-chips"><span class="ar-chip {assoc_cls}">{_esc(assoc_label)}</span>{relevance}</div>'
                f'<div class="ar-engage-why">{_esc(_why_this_person_matters(p))}</div>'
                '</div>'
                f'<div>{link}</div>'
                '</div>'
            )
        people_html = "".join(people_rows)
    elif es.stakeholder_count is None:
        people_html = '<div class="ar-exec-empty">People Intelligence is not available for this report.</div>'
    else:
        people_html = '<div class="ar-exec-empty">No stakeholders with a confirmed or likely link to the account yet.</div>'

    confirmed_html = "".join(_exec_opportunity_html(o) for o in es.confirmed_opportunities) or (
        '<div class="ar-exec-empty">No opportunity is backed by confirmed account evidence yet.</div>'
    )
    hypotheses_html = "".join(_exec_opportunity_html(o) for o in es.hypothesis_opportunities) or (
        '<div class="ar-exec-empty">No sales hypotheses clear the evidence bar.</div>'
    )

    def panel(label: str, icon: str, body: str, meta: str = "") -> str:
        meta_html = f'<div class="ar-panel-meta">{_esc(meta)}</div>' if meta else ""
        return (
            '<div class="ar-panel ar-exec-panel">'
            f'<div class="ar-panel-head"><div class="ar-panel-title">{_icon(icon)} {_esc(label)}</div>{meta_html}</div>'
            f'{body}</div>'
        )

    st.markdown(
        '<section class="ar-exec">'
        '<div class="ar-exec-head">'
        f'<div class="ar-exec-title">{_icon("layers")} Executive account summary</div>'
        '<div class="ar-exec-meta">Built only from the evidence below &middot; industry context is never treated as account evidence</div>'
        '</div>'
        f'<div class="ar-exec-label">Account pulse</div><div class="ar-pulse">{pulse_html}</div>'
        '<div class="ar-exec-grid is-wide">'
        + panel("Executive summary", "doc", f'<div class="ar-exec-summary">{summary_html}</div>')
        + panel("Suggested conversation", "send", conversation_html)
        + '</div><div class="ar-exec-grid">'
        + panel("Key signals", "signal", signals_html, f"Top {len(es.key_signals)} by evidence strength" if es.key_signals else "")
        + panel("Who to engage", "users", people_html, "From existing People Intelligence")
        + '</div>'
        + panel(
            "Otech opportunities", "target",
            '<div class="ar-opp-cols">'
            f'<div><div class="ar-exec-label">Confirmed evidence &middot; {len(es.confirmed_opportunities)}</div>{confirmed_html}</div>'
            f'<div><div class="ar-exec-label">Sales hypotheses &middot; {len(es.hypothesis_opportunities)}</div>{hypotheses_html}</div>'
            '</div>',
            "Weak relevance and industry context are never shown as opportunities",
        )
        + '</section>',
        unsafe_allow_html=True,
    )


def run_demo() -> None:
    """Live Demo Mode entry (demo/demo_app.py): the same product as main(),
    with the live-demo presentation layer -- banner, example account,
    always-fresh research, status strip and a no-stale-result failure panel."""
    st.session_state["ar_demo"] = True
    main()


def main() -> None:
    demo = _demo_active()
    page_title = f"{APP_TITLE} · Live Demo" if demo else APP_TITLE
    st.set_page_config(page_title=page_title, page_icon="🛰️", layout="wide", initial_sidebar_state="expanded")
    # Theme first: palette/mode state + CSS for every component on the page.
    init_theme_state(st)
    inject_theme(st)
    if demo:
        st.markdown(demo_mode.banner_html(), unsafe_allow_html=True)

    _render_sidebar()
    entry = st.session_state.get("entry")
    target_tab = _render_sidebar_nav(bool(entry))
    render_theme_controls(st)
    _render_sidebar_footer()

    if not entry:
        _render_topbar(None)
        if demo and st.session_state.get("demo_failure"):
            _render_demo_failure()
            return
        _render_landing()
        return

    report = entry.get("report", {})
    opportunities = entry.get("opportunity_hypotheses", report.get("opportunity_hypotheses", []))

    _render_topbar(report)
    if demo:
        _render_demo_status(entry, report)
    _render_account_header(entry, report)
    # Partial provider failure: results were retrieved, but a provider failed
    # or was skipped, so coverage may be incomplete (B5.6). A run that
    # retrieved nothing is handled by the existing total-failure paths.
    coverage = demo_mode.partial_coverage_from_report(report)
    if coverage:
        st.warning(coverage.message)
    _render_executive_summary(report, opportunities)
    _render_metric_row(report, opportunities)

    if report.get("status") == "insufficient_evidence":
        st.warning(report.get("qualification", {}).get("corpus_reason", "Insufficient public evidence."))

    # The sidebar navigation opens the matching tab: a new key per target
    # gives a fresh tab set whose default is that tab. All tab content still
    # renders, and the six tab labels are unchanged.
    nav_seq = st.session_state.get("ar_nav_seq", 0)
    tabs = st.tabs(["WATCH", "UNDERSTAND", "DECIDE", "ACT", "RESEARCH", "PEOPLE"], default=target_tab,
                   key=f"ar_tabs::{target_tab}::{nav_seq}")
    with tabs[0]:
        _render_watch(report, opportunities)
    with tabs[1]:
        _render_understand(report)
    with tabs[2]:
        _render_decide(report, opportunities)
    with tabs[3]:
        _render_act(entry)
    with tabs[4]:
        _render_research(entry)
    with tabs[5]:
        _render_people(report)


if __name__ == "__main__":
    main()





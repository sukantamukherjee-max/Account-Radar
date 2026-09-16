from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import streamlit as st

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

from ai.client import AIResponse, OpenRouterAIClient
from ai.synthesis import synthesize_account
from commercial.opportunities import build_opportunity_hypotheses
from research.acquisition import run_acquisition
from research.analysis.report import build_report
from research.organization import OrganizationInput
from research.providers.openrouter_search import OpenRouterSearchProvider
from research.providers.serper import SerperProvider


APP_TITLE = "Account Radar"
REPORT_PATH = Path("phase1d_report.json")


def _unconfigured_ai():
    class Client:
        def is_configured(self) -> bool:
            return False

        def complete_json(self, prompt: str, *, schema_name: str) -> AIResponse:
            return AIResponse(error="OPENROUTER_API_KEY not configured")

    return Client()


def _run_live(name: str, country: str | None) -> dict[str, Any]:
    serper_key = os.getenv("SERPER_API_KEY", "").strip()
    if not serper_key:
        raise RuntimeError("SERPER_API_KEY is not configured in the environment.")

    providers = [SerperProvider(api_key=serper_key)]
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if openrouter_key:
        providers.append(OpenRouterSearchProvider(api_key=openrouter_key))

    org = OrganizationInput(name=name.strip(), country=(country or "").strip() or None)
    run = run_acquisition(org, providers)
    report = build_report(run)
    ai_client = OpenRouterAIClient(api_key=openrouter_key) if openrouter_key else _unconfigured_ai()
    synthesis = synthesize_account(report, ai_client)
    opportunities = build_opportunity_hypotheses(report.capability_mappings, max_items=5)

    return {
        "report": report.to_dict(),
        "ai_synthesis": synthesis.to_dict(),
        "opportunity_hypotheses": [o.to_dict() for o in opportunities],
    }


def _load_report_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _render_metric_row(report: dict[str, Any], opportunities: list[dict[str, Any]]) -> None:
    q = report.get("qualification", {})
    diag = report.get("acquisition_diagnostics", {})
    cols = st.columns(6)
    cols[0].metric("Qualified sources", q.get("qualified_count", 0))
    cols[1].metric("Signals", len(report.get("signals", [])))
    cols[2].metric("Capability mappings", len(report.get("chain", [])))
    cols[3].metric("Opportunities", len(opportunities))
    cols[4].metric("Research queries", diag.get("query_count", diag.get("queries", 0)))
    # Phase 2 hardening (item 9): uncertainty stays visible at a glance --
    # excluded/unusable sources and weak/incidental evidence are counted
    # elsewhere in the pipeline but were previously never surfaced once a
    # corpus qualified as usable.
    cols[5].metric("Disqualified sources", q.get("disqualified_count", 0))


def _render_watch(report: dict[str, Any], opportunities: list[dict[str, Any]]) -> None:
    st.subheader("WATCH")
    st.caption("What is changing, what is emerging, and what deserves attention.")
    signals = report.get("signals", [])
    if not signals:
        st.info("No reliable public signals were extracted from this research run.")
    else:
        signal_types: dict[str, int] = {}
        for s in signals:
            signal_types[s.get("signal_type", "other")] = signal_types.get(s.get("signal_type", "other"), 0) + 1
        cols = st.columns(min(4, max(1, len(signal_types))))
        for i, (kind, count) in enumerate(sorted(signal_types.items())):
            cols[i % len(cols)].metric(kind.replace("_", " ").title(), count)
        st.markdown("#### Recent / relevant signals")
        for s in signals[:12]:
            with st.expander(f"{s.get('signal_type','Signal').replace('_',' ').title()} — {s.get('source_title') or 'Source'}"):
                st.write(s.get("text") or s.get("snippet") or "No extracted text.")
                if s.get("matched_terms"):
                    st.caption("Matched: " + ", ".join(s["matched_terms"]))
                if s.get("source_url"):
                    st.link_button("Open source", s["source_url"])

    st.markdown("#### Opportunity radar")
    if not opportunities:
        st.info("No qualifying opportunity hypotheses were generated.")
    else:
        for o in opportunities:
            st.write(f"**{o['capability_name']}** — {priority_label(o['priority'])} — score {o['score']}")
            st.progress(min(100, int(round(o["score"]))))


def _render_understand(report: dict[str, Any]) -> None:
    st.subheader("UNDERSTAND")
    st.caption("Business context, technology context, people evidence, and current-state context.")

    st.markdown("#### Account narrative")
    narrative = st.session_state.entry.get("ai_synthesis", {}).get("account_narrative")
    if narrative:
        st.info(narrative)
    else:
        st.write("AI narrative is unavailable for this run.")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Technology / capability context")
        summary = report.get("capability_summary", {})
        rows = capability_summary_rows(summary)
        if rows:
            for row in rows:
                pieces = []
                if row["direct_count"]:
                    pieces.append(f"{row['direct_count']} confirmed")
                if row["strong_count"]:
                    pieces.append(f"{row['strong_count']} inference")
                if row["weak_count"]:
                    pieces.append(f"{row['weak_count']} weak/context")
                st.write(f"**{row['capability_name']}** — " + (", ".join(pieces) if pieces else "no evidence"))
        else:
            st.info("No capability summary available.")

    with right:
        st.markdown("#### Current-state / incumbent context")
        incumbents = report.get("incumbent_evidence", [])
        terms = []
        for i in incumbents:
            term = i.get("term")
            if term and term not in terms:
                terms.append(term)
        if terms:
            st.write(", ".join(terms))
            st.caption("Incumbent evidence is context, not proof of an Otech opportunity.")
        else:
            st.info("No incumbent/vendor context detected.")

    st.markdown("#### People & buying context")
    people = []
    seen = set()
    for row in report.get("chain", []):
        url = row.get("source_url", "")
        if source_is_linkedin(url) and url not in seen:
            seen.add(url)
            people.append((row.get("source_title") or "LinkedIn evidence", url))
    if people:
        for title, url in people[:30]:
            st.write(f"**{title}**")
            st.link_button("Open LinkedIn evidence", url)
    else:
        st.info("No LinkedIn people evidence was retained in this report.")

    # Phase 2 hardening (item 9): "what we don't know" stays visible even
    # for a qualified corpus, not only in the insufficient_evidence banner.
    st.markdown("#### What we don't know")
    q = report.get("qualification", {})
    weak_count = weak_evidence_total(report.get("capability_summary", {}))
    gap_lines = []
    if q.get("corpus_reason"):
        gap_lines.append(q["corpus_reason"])
    if q.get("disqualified_count"):
        gap_lines.append(
            f"{q['disqualified_count']} source(s) found during research were excluded from analysis "
            "as unusable (no content, or too little text to be reliable)."
        )
    if weak_count:
        gap_lines.append(
            f"{weak_count} additional evidence item(s) were classified as weak/incidental relevance -- "
            "retained for traceability, but never used to support a capability hypothesis or a sales action."
        )
    if gap_lines:
        for line in gap_lines:
            st.write(f"- {line}")
        if q.get("disqualified_reasons"):
            with st.expander(f"Disqualified sources ({len(q['disqualified_reasons'])})"):
                for d in q["disqualified_reasons"][:30]:
                    st.write(f"- {d.get('reason')} — {d.get('url')}")
    else:
        st.info("No known evidence gaps for this run.")


def _render_decide(report: dict[str, Any], opportunities: list[dict[str, Any]]) -> None:
    st.subheader("DECIDE")
    st.caption("Potential Otech opportunities — hypotheses to validate, not confirmed customer requirements.")
    if not opportunities:
        st.warning("No opportunity hypotheses passed the current evidence threshold.")
        return

    for idx, o in enumerate(opportunities, 1):
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 1, 1])
            c1.markdown(f"### {idx}. {o['capability_name']}")
            c2.metric("Score", o["score"])
            c3.write(f"**{priority_label(o['priority'])}**")
            st.caption(epistemic_status_label(o.get("epistemic_status")))
            st.write(o["hypothesis"])
            st.caption(o["rationale"])
            a, b = st.columns(2)
            with a:
                st.markdown("**Who to engage**")
                st.write(o["who"])
            with b:
                st.markdown("**Conversation angle**")
                st.write(o["conversation"])
            st.markdown("**Evidence**")
            st.write(
                f"{o['evidence_count']} evidence item(s) across {o.get('independent_domain_count', 0)} independent "
                f"domain(s) and {o.get('inference_cluster_count', 0)} distinct event/evidence cluster(s) / "
                f"{o['confidence']:.0f}/100 confidence"
            )
            if o.get("incumbent_context"):
                st.caption("Current-state context: " + ", ".join(o["incumbent_context"]))
            with st.expander("Supporting sources"):
                for url in o.get("source_urls", []):
                    st.link_button(url, url)


def _render_act(entry: dict[str, Any]) -> None:
    st.subheader("ACT")
    st.caption("What to do next, based on the strongest available evidence.")
    synthesis = entry.get("ai_synthesis", {})
    actions = synthesis.get("next_best_actions", [])
    if actions:
        for action in actions:
            with st.container(border=True):
                st.markdown(f"### {action.get('rank','')}. {action.get('capability_name','Next action')}")
                badge = epistemic_status_label(action.get("epistemic_status"))
                if action.get("origin") == "deterministic_guardrail":
                    st.caption(f"{badge} · deterministic guardrail action (AI returned no usable action here)")
                else:
                    st.caption(badge)
                st.write(action.get("rationale", ""))
                if action.get("action"):
                    st.write("**Action:** " + action["action"])
    else:
        st.info("No AI next-best actions were returned. Use the opportunity hypotheses in DECIDE to validate the next step.")

    st.markdown("#### Evidence explorer")
    report = entry.get("report", {})
    for row in report.get("chain", [])[:40]:
        title = row.get("source_title") or row.get("source_url") or "Evidence"
        level = evidence_level_label(row.get("evidence_level", ""))
        category = evidence_category_label(row.get("evidence_category"))
        with st.expander(f"{row.get('potential_otech_capability',{}).get('capability_name','Capability')} — {level} — {title}"):
            st.caption(f"{category} · {epistemic_status_label(row.get('epistemic_status'))}")
            st.write("**Customer signal:**", row.get("customer_signal", {}).get("text") or "—")
            st.write("**Business impact:**", row.get("business_impact") or "—")
            st.write("**Technology requirement:**", row.get("technology_requirement") or "—")
            st.write("**Confidence:**", row.get("confidence"))
            st.write("**Matched terms:**", ", ".join(row.get("matched_terms", [])) or "—")
            if row.get("source_url"):
                st.link_button("Open source", row["source_url"])



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
    st.subheader("RESEARCH")
    st.caption(
        "A working intelligence feed: inspect the evidence, understand the AI interpretation, "
        "then validate or correct it."
    )

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
    name = st.sidebar.text_input("Organization", placeholder="Enter any organization")
    country = st.sidebar.text_input("Country (optional)", placeholder="Country")
    if st.sidebar.button("Analyze account", type="primary", use_container_width=True):
        if not name.strip():
            st.sidebar.error("Enter an organization name.")
        else:
            with st.spinner("Researching account and building evidence model…"):
                try:
                    entry = _run_live(name, country)
                    st.session_state.entry = entry
                    # Live research always produces exactly one account --
                    # this keeps the selector's "more than one account"
                    # check accurate and clears out any selector left over
                    # from a previously loaded multi-account report.
                    st.session_state.accounts = [entry]
                    st.session_state.loaded_from = "live research"
                    st.session_state.last_account = name.strip()
                except Exception as exc:
                    st.sidebar.error(str(exc))

    st.sidebar.divider()
    st.sidebar.subheader("Load existing report")
    uploaded = st.sidebar.file_uploader("JSON report", type=["json"])
    if uploaded is not None and st.sidebar.button("Load uploaded report", use_container_width=True):
        try:
            payload = json.load(uploaded)
            accounts = _load_accounts(payload)
            st.session_state.entry = accounts[0]
            st.session_state.loaded_from = "uploaded report"
            st.session_state.last_account = st.session_state.entry.get("report", {}).get("organization_name", "")
        except Exception as exc:
            st.sidebar.error(str(exc))

    if REPORT_PATH.exists() and st.sidebar.button("Load local phase1d_report.json", use_container_width=True):
        try:
            accounts = _load_accounts(_load_report_file(REPORT_PATH))
            st.session_state.entry = accounts[0]
            st.session_state.loaded_from = "local report"
            st.session_state.last_account = st.session_state.entry.get("report", {}).get("organization_name", "")
        except Exception as exc:
            st.sidebar.error(str(exc))

    _render_account_selector()

    st.sidebar.divider()
    st.sidebar.caption("Phase 2 UI — research is run only when you explicitly select Analyze account. API keys are read from environment variables and are never displayed.")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🛰️", layout="wide", initial_sidebar_state="expanded")
    st.title("🛰️ Account Radar")
    st.caption("WATCH → UNDERSTAND → DECIDE → ACT")

    _render_sidebar()

    entry = st.session_state.get("entry")
    if not entry:
        st.info("Enter an organization in the left panel and select **Analyze account**, or load an existing JSON report.")
        st.markdown("### What this dashboard does")
        st.write("Turns public evidence into signals, technology context, evidence-backed Otech capability hypotheses, stakeholders, and next actions. It does not assume a customer requirement from a keyword alone.")
        return

    report = entry.get("report", {})
    opportunities = entry.get("opportunity_hypotheses", report.get("opportunity_hypotheses", []))
    ai = entry.get("ai_synthesis", {})

    name = report.get("organization_name", "Organization")
    country = report.get("country") or "Country not specified"
    st.header(name)
    st.caption(f"{country} · Source: {st.session_state.get('loaded_from','report')} · AI: {ai.get('status','unknown')}")
    _render_metric_row(report, opportunities)

    if report.get("status") == "insufficient_evidence":
        st.warning(report.get("qualification", {}).get("corpus_reason", "Insufficient public evidence."))

    tabs = st.tabs(["WATCH", "UNDERSTAND", "DECIDE", "ACT", "RESEARCH"])
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


if __name__ == "__main__":
    main()

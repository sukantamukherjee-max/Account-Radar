# Account Radar — Phase 2 UI

## Purpose

Phase 2 turns the Phase 1F intelligence engine into the first Account Radar Command Center UI.

The interface follows the agreed flow:

**WATCH → UNDERSTAND → DECIDE → ACT**

It is organization/sector/country agnostic. No target customer is hard-coded into the UI or commercial logic.

## What is included

- `app.py` — Streamlit Command Center
- Full Phase 1F research, evidence, AI and commercial modules
- Existing Phase 1F tests
- Phase 2 UI tests
- `requirements.txt` updated with Streamlit

## Current Phase 2 scope

### WATCH
- Research corpus metrics
- Signal categories and evidence
- Opportunity radar

### UNDERSTAND
- AI account narrative
- Capability context
- Current-state/incumbent context
- LinkedIn people/buying context

### DECIDE
- Ranked opportunity hypotheses
- Scores and evidence levels
- Confidence and independent-source counts
- Who to engage
- Conversation angle
- Supporting evidence
- Incumbent context

### ACT
- AI next-best actions
- Evidence explorer
- Source-level traceability

## Important product rule

The dashboard presents opportunities as **sales hypotheses to validate**, not confirmed customer requirements.

Personal LinkedIn profiles, jobs and third-party technographics remain context/weak evidence and cannot independently establish an Otech opportunity.

## Run

From the project folder:

```cmd
py -m streamlit run app.py
```

Then open the local Streamlit URL shown in the terminal.

For live research, the environment must contain:

- `SERPER_API_KEY`
- `OPENROUTER_API_KEY` (recommended for AI synthesis)

The UI never displays those values.

## Existing report mode

The sidebar can load `phase1d_report.json` if present, or an uploaded JSON report. This allows the UI to be reviewed without spending a new search run.

## Not yet in Phase 2

Persistent PostgreSQL account memory, scheduled refresh, customer feedback workflow, advanced change detection, and production authentication are deliberately deferred to later phases.

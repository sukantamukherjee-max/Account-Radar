# Account Radar — Phase 1C-H Integration Pass Report

**Honest note on continuity, stated once:** I searched the full filesystem and git history for `run_phase1c.py`, `research/analysis/`, and any qualification module before starting this pass. None existed. The workspace, before this pass, contained exactly what the accepted Phase 1C-H hardening report described: Phase 1B acquisition + the standalone `commercial/` evidence-mapping module — not yet wired into a runnable pipeline. Everything under "New files" below is new work built to the pipeline shape you specified (`acquisition → qualification → signal extraction → evidence-first capability mapping → report`), not a restoration of pre-existing files. The already-accepted `commercial/evidence_mapping.py` rules themselves were **not** touched in this pass — only integration/wiring around them, per your instruction.

## 1. What this pass did

Wired the previously-standalone `commercial/` evidence-mapping layer into a complete, runnable pipeline sitting between Phase 1B acquisition output and a final report, and added the runner/tests/packaging the integration requirement asked for.

## 2. Files changed

**New files:**
- `research/analysis/__init__.py`
- `research/analysis/qualification.py` — the qualification stage: per-source usability check (URL present, `access_status != NO_CONTENT`, usable text ≥ 40 chars) plus corpus-level adequacy check (≥ 3 qualified sources, and at least one high-trust-or-LinkedIn source), producing `QualificationResult` with `qualified_sources` / `disqualified_sources` / `corpus_status` (`"qualified"` | `"insufficient_evidence"`) / `corpus_reason` — the deterministic equivalent of spec §19's "no reliable public evidence found" diagnostic.
- `research/analysis/report.py` — `build_report(run: AcquisitionRun) -> AccountRadarReport`, composing qualification → `commercial.signals.extract_all_signals` → `commercial.evidence_mapping.map_all_sources` → `commercial.incumbents.detect_all_incumbent_evidence`. `AccountRadarReport.chain_view()` returns one row per capability mapping in the exact `Customer Signal → Business Impact → Technology Requirement → Potential Otech Capability` shape, each row carrying `evidence_level`, `confidence`, `source_url`, `source_title`, `supporting_context`, `matched_terms`, and `incumbent_context`. If the corpus qualifies as insufficient, signal extraction and capability mapping are skipped entirely — the report comes back with an empty chain and `status == "insufficient_evidence"` plus the reason, rather than running the mapping stage on unusable evidence.
- `run_phase1c.py` — the end-to-end CLI runner: `run_acquisition()` → `build_report()`, for one or more organizations with zero code change between them (same `--org`/`--country`/`--mock`/`--live`/`--out` interface as `run_acquisition_test.py`). Falls back to `MockSearchProvider` and labels output `*** SYNTHETIC / MOCK RUN ***` exactly as the Phase 1B harness does when no `SERPER_API_KEY` is present.
- `tests/test_integration_pipeline.py` — 4 new tests (see §4).

**Modified files:** none. `commercial/evidence_mapping.py`, `commercial/signals.py`, `commercial/incumbents.py`, `commercial/models.py`, and all of `research/` (Phase 1B acquisition) are byte-for-byte unchanged from the accepted Phase 1C-H hardening state.

**Not touched, still true from the prior report:** `commercial/capabilities.py`/`capabilities.json` (already JSON-backed from the previous turn — item 6 was already satisfied and remains so; nothing in `research/` imports or references it, keeping acquisition fully decoupled from the Otech catalogue).

## 3. Pipeline shape (item 2 / item 7)

```
run_acquisition(account, providers)          # Phase 1B, unchanged
        -> AcquisitionRun(organization, sources, diagnostics)
                -> qualify_corpus(sources)                    # research/analysis/qualification.py
                        -> QualificationResult
                                -> extract_all_signals(qualified_sources)          # commercial/signals.py
                                -> map_all_sources(qualified_sources)               # commercial/evidence_mapping.py
                                -> detect_all_incumbent_evidence(qualified_sources) # commercial/incumbents.py
                                        -> AccountRadarReport                       # research/analysis/report.py
                                                -> .chain_view() / .to_dict()
```

Every row of `chain_view()` (and every mapping in `to_dict()["chain"]`) explicitly carries: `customer_signal` (signal_type + backing text), `business_impact`, `technology_requirement`, `potential_otech_capability` (id + name), `evidence_level`, `confidence`, `source_url`, `source_title`, `supporting_context`, `matched_terms`, `incumbent_context`.

## 4. Tests

```
40 passed in 2.50s
```

- 9 Phase 1B tests + 14 URL-hardening tests + 13 capability-mapping tests = 36 (the previously-accepted count) — **all still pass unchanged**.
- **4 new integration tests** in `tests/test_integration_pipeline.py` (item 8):
  - `test_full_pipeline_from_acquisition_run_to_qualified_report` — builds a real `AcquisitionRun` (5 sources: official tech article, Cisco/network news, procurement RFP, LinkedIn company page, LinkedIn profile) and asserts the full chain comes out the other end with a `direct_evidence` mapping present, the Cisco network mention carrying `incumbent_context` whenever it reaches `direct_evidence`, the LinkedIn profile never reaching `direct_evidence`, and every chain row shaped correctly.
  - `test_full_pipeline_reports_insufficient_evidence_honestly_without_fabricating_mappings` — a thin, `NO_CONTENT` corpus produces `status == "insufficient_evidence"`, an empty chain, and a populated `corpus_reason` — proving the pipeline never fabricates a confident result from unusable evidence.
  - `test_qualification_excludes_no_content_sources_but_keeps_snippet_only_sources` — qualification stage in isolation, fed acquisition-shaped `Source` objects.
  - `test_acquisition_run_object_shape_is_compatible_with_build_report` — a drift guard: fails loudly if `research.acquisition`'s real dataclasses and `research.analysis.report`'s expectations of them ever diverge, rather than only failing at runtime inside `run_phase1c.py`.

`pyflakes` across `research/`, `commercial/`, `tests/`, `run_acquisition_test.py`, `run_phase1c.py`, `before_after_demo.py`: clean, zero warnings.

## 5. Regression checks (item 4)

- `python before_after_demo.py` re-run: identical before/after numbers as the accepted hardening report (14 undifferentiated hits → 4 direct + 3 inference + 7 weak, 3 incumbent items) — the evidence-mapping rules are untouched.
- `python run_acquisition_test.py --mock`: runs clean, same acquisition behavior as Phase 1B.
- `python run_phase1c.py --mock`: runs the full pipeline end-to-end against the three Phase 1B/1C test organizations (Oman Airports Management Company/Oman, Standard Chartered Bank/UK, Siemens AG/Germany) with **zero code change between them**. Observed (synthetic mock data): all three orgs' corpora qualified (57-65 qualified sources each); Siemens AG's mock corpus produced 5 signals and 15 `strong_inference` capability mappings grounded in `technology_initiative` signals — demonstrating the qualified→signals→mappings path actually fires, not just the insufficient-evidence path. Mock text is still synthetic (see `run_phase1c.py`'s own honesty docstring) — this is a pipeline-wiring proof, not a real finding.

## 6. Constraints maintained

- No customer/sector/country/industry/organization name appears in any new file's logic — `research/analysis/qualification.py` and `research/analysis/report.py` operate purely on `Source`/`AcquisitionRun` shape, never on organization identity. The existing AST-based `test_no_hardcoded_organization_names_in_research_package` test's scope (`research/*.py`) covers `research/analysis/` too, and continues to pass.
- No LLM introduced anywhere in this pass.
- No change to the already-accepted evidence-mapping rules, confidence formula, or false-positive/incumbent-separation logic.
- Otech capability catalogue remains JSON-backed and decoupled from `research/`.
- Did not begin Phase 1D (no AI-reasoning-layer work, no opportunity scoring/radar UI).

## 7. Known limitations (unchanged from the prior report, plus one new item)

- All limitations listed in `PHASE_1C_H_HARDENING_REPORT.md` §E still apply unchanged (illustrative vendor lexicon, regex/pattern-based signal and term extraction, heuristic `direct_evidence` bar).
- **New:** `report.chain_view()`'s `customer_signal.text` is populated only when a signal of the *same type* was independently extracted from the *same source* as the mapping — if a capability mapping's `signal_type` is set but no matching `Signal` object exists in `report.signals` (an edge case the current signal/mapping logic should not produce, but isn't structurally impossible), the row's `customer_signal.text` falls back to `None` rather than raising. This is a defensive fallback, not observed in any test or demo run so far.
- The qualification stage's thresholds (40 chars usable text, 3 qualified sources minimum) are fixed constants, not yet configurable — consistent with "do not redesign the architecture," but worth flagging as a future tuning knob.

Stopping here per your instruction: "Stop after the integration/test/packaging pass." Not proceeding to Phase 1D.

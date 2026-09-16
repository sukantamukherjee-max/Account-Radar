# Account Radar — Phase 2 Hardening Implementation Report

Implemented in the exact order approved in the gap report's Section F. All 9 steps were completed; no blocker was hit. No unrelated architectural changes were made, no customer/sector/country-specific logic was introduced, and every existing Phase 1 protection (URL hardening, LinkedIn caps, organization-association gate, incumbent separation, AI grounding, qualification handling) is untouched.

## A. Files changed

| File | Nature of change |
|---|---|
| `commercial/models.py` | New `EvidenceCategory` and `EpistemicStatus` enums, `epistemic_status_for()`. `CapabilityMapping` gained `evidence_category`, `inference_cluster_id`, `epistemic_status`. `Signal` gained `match_start`/`match_end`. |
| `commercial/signals.py` | `extract_signals()` now records each signal's own regex match position. |
| `commercial/evidence_mapping.py` | Core fix: narrowed generic-trigger-qualifying signal set (`_INFERENCE_QUALIFYING_SIGNAL_TYPES`), new `_signal_locally_associated_with_trigger()` locality gate, new `_cluster_id()` helper. Every classification rule (0/1/2-3/4/5) now assigns `evidence_category`, `inference_cluster_id`, `epistemic_status`. |
| `commercial/opportunities.py` | `_score()` and `build_opportunity_hypotheses()` rewritten to use distinct source-domain and distinct inference-cluster counts instead of raw mapping/URL counts. `OpportunityHypothesis` gained `independent_domain_count`, `inference_cluster_count`, `epistemic_status`. |
| `research/analysis/report.py` | `chain_view()` now carries the three new fields through to the serialized report. |
| `ai/models.py` | `CapabilityNarrative` gained `best_evidence_category`/`best_epistemic_status`; `NextBestAction` gained `evidence_category`/`epistemic_status` — both always app-copied, never AI-settable. |
| `ai/prompt.py` | Evidence block now shows `evidence_category`/`inference_cluster_id`/`epistemic_status` as read-only context; `_INSTRUCTIONS` explicitly forbids the AI from inventing or overriding them and requires narrative tone to match `epistemic_status`. |
| `ai/synthesis.py` | Grounding step copies the three new fields from the real `CapabilityMapping`/`OpportunityHypothesis` into the AI-facing result objects. |
| `ai/schema.py` | Unchanged — already `additionalProperties: false` with a closed field set, which is what makes the "AI cannot invent these fields" guarantee structural rather than a matter of prompt wording. |
| `app.py` | Metric row shows a "Disqualified sources" count; UNDERSTAND's capability-summary block rewritten (fixes the pre-existing nested-dict rendering bug) and gained a "What we don't know" section; DECIDE and ACT surface `epistemic_status`/`evidence_category`/diversity counts. |
| `ui/data.py` | New helpers: `capability_summary_rows()`, `weak_evidence_total()`, `epistemic_status_label()`, `evidence_category_label()`. |
| `tests/test_phase2_inference_hardening.py` | **New file**, 18 tests (see C). |
| `tests/test_phase2_ui.py` | +5 tests for the new UI helpers and uncertainty surfacing. |

No file outside this list was modified. `commercial/capabilities.json` was deliberately left unchanged — the gap report noted `"acquisition"` as a fan-out risk, but the mechanism fix (localization + narrowing) makes catalogue pruning unnecessary, and pruning it would have been an unrelated data change.

## B. Exact behavioural changes

1. **Fan-out is blocked at the source.** A generic trigger (e.g. `"acquisition"`, shared by all five capabilities) can only be promoted to `strong_inference` when: (a) it sits in the same sentence as the organization's own name (unchanged from Phase 1C-H3), **and** (b) a qualifying signal exists whose own match is in the same sentence or an adjacent one — same locality radius used elsewhere in this module, **and** (c) that signal's type is one of `expansion`, `project`, `technology_initiative`, `capacity_change`, `procurement_activity`. `partnership`, `investment`, and `contract_award` are real signals (still extracted and retained via `commercial.signals`) but can never — by themselves, anywhere in the source — promote a generic trigger, regardless of position.
2. **Genuine multi-capability inference is preserved, unchanged in shape.** A real, locally-described event (e.g. "opening a new facility... this expansion will require network capacity, data storage, and cybersecurity operations") still produces several `strong_inference` mappings — the fix targets locality/signal-type, not mapping count.
3. **`evidence_category`** is now assigned deterministically to every mapping: `current_state_technology` (technographics), `stakeholder_context`/`hiring_intent_context` (LinkedIn profile/job), `organizational_technology_event`/`procurement_signal` (direct evidence), `business_signal_inference` (generic-trigger inference), `incidental_mention` (bare weak match).
4. **`inference_cluster_id`** now lets a consumer tell "N mappings from one event" apart from "N independently corroborating pieces of evidence." Verified: four capabilities inferred from one expansion event share one cluster id; mappings from two distinct sources/events get distinct cluster ids.
5. **`epistemic_status`** (`confirmed_evidence` for `direct_evidence`, `inference_hypothesis` otherwise) is now a first-class, app-computed field on every mapping, every `OpportunityHypothesis`, and every AI-facing narrative/action — never AI-decided.
6. **Opportunity scores no longer inflate from raw counts.** The corroboration bonus now counts distinct source **domains** (not raw URLs — two articles on one domain no longer earn a diversity bonus) and distinct **inference clusters** (not raw mapping count — several mappings from one event count once). A second same-domain source can nudge the score by at most ~1.5–2.0 points (the small cluster-bonus term); a genuinely independent domain earns the full 5-point-per-domain bonus.
7. **AI grounding is now provably closed over the new fields**, not just by convention: `ai/schema.py`'s `additionalProperties: false` shape means a response that tries to add `evidence_category`/`inference_cluster_id`/`epistemic_status`/`evidence_level` to any item fails schema validation outright (`ai_failed`) — there is no code path where the AI's values for these fields could reach the output. Existing `capability_id`/`cited_source_urls` grounding is unchanged.
8. **UI uncertainty is visible even for a "qualified" account**, not only the `insufficient_evidence` banner: disqualified-source count, `corpus_reason`, and total weak/incidental evidence count are now always shown (UNDERSTAND's new "What we don't know" section; a new metric-row column).
9. **The pre-existing UNDERSTAND rendering bug is fixed.** `capability_summary` is a nested dict of mapping-dict-lists; the old code `st.write(f"**{name}** — {value}")` would have dumped a raw Python structure into the UI. It now renders `"X confirmed, Y inference, Z weak/context"` per capability via `capability_summary_rows()`.
10. **WATCH/UNDERSTAND/DECIDE/ACT semantics are preserved as-is.** Per your instruction not to make unrelated changes, I did not restructure the WATCH "Opportunity radar" vs. DECIDE duplication noted in the gap report (B.2) — that was flagged as a product decision for you, not a defect, and is out of scope for this approved implementation.

## C. Test results

```
124 passed, 0 failed
```

Method note (unchanged from the gap report): this sandbox has no outbound access to PyPI, so I used the same hand-written pytest-compatible runner (bare `test_*` functions + `@pytest.mark.parametrize`, the only pytest feature this suite uses) rather than the literal `pytest` binary. Please re-confirm with real `pytest -q` on your machine.

- **101/101 original tests unchanged and still passing** — I did not edit, delete, or weaken a single pre-existing test.
- **23 new tests**, all passing:
  - `tests/test_phase2_inference_hardening.py` — 18 tests: the fan-out regression (and its three signal-type-specific variants), two "genuine inference preserved" tests, two `inference_cluster_id` tests, one `evidence_category`-per-branch test (6 branches in one test), two `epistemic_status` tests, three opportunity-scoring diversity tests, and three AI-cannot-invent-new-fields tests (two schema-rejection tests, one end-to-end grounding test).
  - `tests/test_phase2_ui.py` — 5 new tests for `capability_summary_rows`, `weak_evidence_total`, the new label helpers, and a text-level check that `app.py` actually surfaces the uncertainty fields.
- `pyflakes`-equivalent check: `ast.parse()` on every changed file — all clean (no real `pyflakes` package available in this sandbox for the same PyPI-access reason noted above; recommend running it for real before merging).

## D. Before/after fan-out example

**Case 1 — the reported fan-out pattern**, run against `map_source_to_capabilities` directly:

> "Meridian Ports Authority today confirmed a strategic partnership with a regional logistics operator to expand joint marketing efforts across the region. In entirely separate news, industry observers noted that Meridian Ports Authority's planned acquisition of a smaller regional operator is expected to close next quarter, pending regulatory approval. No further details were shared about the transaction structure or financing."

| | Before Phase 2 hardening | After |
|---|---|---|
| Network & Connectivity | `strong_inference` (60) | `weak_relevance` |
| Data Centre & Infrastructure | `strong_inference` (60) | `weak_relevance` |
| Cloud & AI Infrastructure | `strong_inference` (60) | `weak_relevance` |
| Cybersecurity & Resilience | `strong_inference` (60) | `weak_relevance` |
| Enterprise Managed Services | `strong_inference` (60) | `weak_relevance` |
| **Capabilities promoted** | **5 / 5** | **0 / 5** |

(Verified directly against the actual pre-Phase-2 baseline commit, not just described — I ran the identical fixture through both versions of `evidence_mapping.py`.)

**Case 2 — the genuine local expansion event, same mechanism, must NOT be collateral damage:**

> "Meridian Ports Authority announced today that it is opening a new facility in the northern district. This expansion will require additional network capacity, data storage infrastructure, and improved cybersecurity operations to support the new site."

| Capability | Evidence level | evidence_category | inference_cluster_id |
|---|---|---|---|
| Network & Connectivity | `strong_inference` | `business_signal_inference` | `ea0f616604dfc748` |
| Data Centre & Infrastructure | `strong_inference` | `business_signal_inference` | `ea0f616604dfc748` |
| Cybersecurity & Resilience | `strong_inference` | `business_signal_inference` | `ea0f616604dfc748` |
| Enterprise Managed Services | `strong_inference` | `business_signal_inference` | `ea0f616604dfc748` |

4 capabilities legitimately promoted, all sharing **one** `inference_cluster_id` — correctly recorded as one business event producing four related hypotheses, not four independent corroborations.

Both cases are now permanent regression tests (`test_unrelated_signal_elsewhere_in_source_does_not_promote_generic_trigger` and `test_genuine_local_expansion_event_still_produces_multiple_related_capability_hypotheses`).

## E. Remaining limitations

1. **A pre-existing drift, discovered but not introduced by this work:** `before_after_demo.py`'s standing fixture (the "Cisco/network renewal" scenario) no longer reproduces the "2 strong_inference" figure recorded in the accepted `PHASE_1C_H3_HARDENING_REPORT.md`. I verified this against the true pre-Phase-2 baseline commit before touching any code: the fixture's "The organization is expanding into a new facility..." sentence refers to the org only via "The organization," not its name/tokens, so the existing (unchanged-by-me) same-sentence organization-association gate for generic triggers already excluded it. My changes make no difference to this specific fixture's output (`4 direct_evidence, 0 strong_inference, 10 weak_relevance` before and after). This looks like the demo fixture's wording drifted in a later phase without the historical report being updated to match — worth a look if you want that specific documented example reproducible again, but it's a demo-script/documentation issue, not a mapping-engine defect, and out of scope for what was approved here.
2. **Signal locality is still sentence-based regex, not true NLP**, consistent with every other heuristic in this module (disclosed since Phase 1C-H3). A genuine event described in unusual phrasing that the sentence-splitter mis-segments could still be under- or over-associated; this is the same class of conservative-by-design trade-off already documented for the organization-association gate.
3. **`inference_cluster_id` clusters within one source's classification pass**, keyed on `(source_url, grounding signal type, signal match position)`. It does not attempt cross-source event-identity resolution — two different articles (even on the same domain) describing what a human would recognize as "the same real-world event" get different cluster ids, because nothing in this deterministic, non-NLP pipeline can safely establish that they're the same event. This is a deliberate scope boundary, not an oversight: doing it "for real" would require semantic event matching, which is out of scope for a keyword/regex-based, LLM-free evidence layer.
4. **The WATCH vs. DECIDE opportunity-radar duplication noted in the gap report (B.2) is unchanged**, per your instruction not to make unrelated changes — still worth a product decision on your side.
5. **Real `pytest`/`pyflakes` were not available in this sandbox** (PyPI returns 403 here) — both the 101-test baseline and the 124-test post-implementation result were verified with a hand-written pytest-compatible substitute and `ast.parse()`, not the literal upstream tools. Please re-run with real `pytest -q` and `pyflakes` before treating this as final.
6. **No live `SERPER_API_KEY`/`OPENROUTER_API_KEY` in this sandbox** (same disclosed limitation as every prior phase) — all verification here is against hand-built and mock fixtures, not a live research run. I'd want to see a `run_phase1c.py --live` result against a real account before calling the fan-out fix fully validated against real-world source text shapes.

## F. Original 101 tests: unchanged and passing

Confirmed. `tests/test_acquisition_harness.py` through `tests/test_phase2_ui.py` (the original file, only additively extended) were not edited in any way that changes their assertions — the diff against the baseline commit for every pre-existing test file other than `test_phase2_ui.py` is zero, and `test_phase2_ui.py`'s existing 7 tests are byte-for-byte unchanged with 5 new ones appended after them. All 101 pass exactly as before; the 23 new tests are additive.

# Phase 1D — AI Reasoning / Synthesis Layer

## 1. Objective

Phase 1D adds exactly one new capability on top of the fully-deterministic,
hardened H1–H4 pipeline (`acquisition → qualification → signal extraction →
evidence-first capability mapping`): turning an already-graded
`AccountRadarReport` into short, human-readable sales prose — an account
narrative, a per-capability narrative, and a ranked next-best-action
shortlist — **without ever letting the AI decide what counts as evidence.**

Because H1–H4 already made evidence extraction and capability grading fully
deterministic (no LLM anywhere in that chain, enforced by AST tests), the
AI's job in Phase 1D is narrower than the original Phase 0 architecture
sketch implied: it does not extract, classify, or score anything. It only
writes prose about facts it is handed, and every fact it asserts is checked
against those facts before being trusted. If the AI is not configured, or
its output doesn't survive that check, Phase 1D degrades honestly instead
of fabricating a result.

## 2. Inputs and outputs

**Input:** one deterministic `AccountRadarReport` (the same object
`run_phase1c.py` already builds — qualification result, extracted signals,
capability mappings with their `evidence_level`/`confidence`/matched
terms/source URLs, incumbent evidence). Phase 1D reads this object; it
never re-reads raw source text and never re-runs acquisition/qualification.

**Output:** a `SynthesisResult` with:
- `status`: `ok`, `degraded`, `ai_unconfigured`, `ai_failed`, or
  `insufficient_evidence` — never silently "successful" when something was
  skipped or dropped.
- `account_narrative`: 2–4 sentence plain-language summary.
- `capability_narratives`: one entry per capability that has surviving,
  grounded evidence, each carrying `best_evidence_level`/`best_confidence`
  **copied from the real `CapabilityMapping`**, never computed by the AI.
- `next_best_actions`: a ranked shortlist, restricted to capabilities that
  already have `direct_evidence` or `strong_inference` support.
- `notes`: a plain-English audit trail of anything dropped during
  grounding (hallucinated URL, hallucinated capability, ineligible
  next-action, etc.) — so a degraded result is visibly degraded, not
  silently thinned.

## 3. What remains deterministic vs. what uses AI

**Unchanged, deterministic (H1–H4, untouched by Phase 1D):**
acquisition, qualification, signal extraction, capability mapping,
evidence-level assignment, confidence scoring, the LinkedIn cap, the
organization-association gate, incumbent-evidence detection.

**New, AI-assisted (Phase 1D only):**
- `account_narrative` prose.
- `capability_narratives[].narrative` prose.
- `next_best_actions[].rationale` prose and the AI's *proposed* rank
  (a hint only — see below).

**Explicitly never AI-decided, even in Phase 1D:**
- `evidence_level` and `confidence` — always copied from the deterministic
  `CapabilityMapping` the narrative/action cites, never invented or
  reweighted by the model.
- Which capabilities exist, which sources support them, or which
  capabilities are "eligible" for a next-best-action — computed in
  `ai/prompt.py`/`ai/synthesis.py` from the report object, then handed to
  the model as a closed list it must choose from, not asked to determine.
- The final ordering of `next_best_actions` — the AI's `rank` field is
  read, but the list is **re-sorted and re-numbered deterministically**
  (`(rank, -confidence, capability_id)`) rather than trusted as already
  correctly ordered, contiguous, or unique.

## 4. How evidence traceability is preserved (grounding)

A schema-valid AI response is not treated as a trustworthy one. Every
`capability_id` and `cited_source_urls` entry the AI returns is checked
against an index built directly from `report.capability_mappings` before
being kept:

- An unknown `capability_id` → the whole narrative for it is dropped, with
  a note.
- A cited URL not actually present in that capability's real evidence →
  the URL is stripped from the narrative silently in the AI's payload but
  loudly in `notes`; if that drops every citation, the narrative itself is
  dropped.
- A `next_best_action` for a capability that isn't `direct_evidence` or
  `strong_inference` in the real report → dropped, with a note (this is
  the H2/H3.1 "weak evidence is never presented as sales-ready" principle
  extended into the AI layer).
- `best_evidence_level`/`best_confidence` attached to what survives are
  never generated text — they're the matching real `CapabilityMapping`
  field, looked up and copied.

If anything was dropped, `status` becomes `degraded` rather than `ok`, so a
caller can distinguish "AI said exactly what the evidence supports" from
"AI overreached and some of it had to be discarded."

Two structural guarantees back this up (both directly tested):
`report.capability_mappings` is never mutated by synthesis
(`test_deterministic_report_is_never_mutated_by_synthesis`), and grounding
logic is pure/deterministic given a fixed AI response
(`test_wrapping_logic_is_deterministic_given_fixed_ai_response`).

## 5. How the design stays organization/sector/country agnostic

- `ai/prompt.py`'s template text (`_INSTRUCTIONS`) contains no
  organization, sector, or country name — only generic instructions;
  organization-specific content enters solely as values read off the
  `AccountRadarReport` passed in. Verified by
  `test_prompt_contains_no_organization_specific_template_text`, which
  asserts the fixture org name, `"Oman"`, and `"Airport"` are not
  substrings of `_INSTRUCTIONS`.
- An AST-based static-analysis test
  (`test_no_hardcoded_organization_names_in_ai_package`, mirroring the
  existing `research/`-scoped and `commercial/`-scoped hardcoding guards)
  scans every string/constant literal in `ai/*.py` for known example
  organization names and fails the suite if one appears.
- `ai/` is never imported by `research/` or `commercial/` (confirmed by
  `grep -rn "from ai\." research/ commercial/` → no matches, and by
  `before_after_demo.py` / `run_phase1c.py --mock` producing byte-for-byte
  the same acquisition/mapping behavior as before Phase 1D was added) — the
  AI layer is strictly additive and downstream, structurally unable to
  influence acquisition, qualification, or capability mapping.

## 6. Tests and acceptance criteria

`tests/test_ai_synthesis.py` — 14 new tests, using a deterministic,
network-free `FakeAIClient` stub (mirrors `MockSearchProvider`'s role for
the acquisition layer) and a synthetic org (`"Meridian Ports Authority"`):

1. `test_ai_unconfigured_returns_ai_unconfigured_status_without_calling_client`
2. `test_insufficient_evidence_report_short_circuits_without_calling_ai`
3. `test_grounded_valid_response_is_accepted`
4. `test_hallucinated_source_url_is_stripped_not_trusted`
5. `test_capability_narrative_dropped_entirely_when_every_citation_is_hallucinated`
6. `test_hallucinated_capability_id_is_dropped`
7. `test_next_best_action_for_weak_only_capability_is_rejected`
8. `test_ai_transport_error_is_ai_failed`
9. `test_schema_invalid_response_is_ai_failed`
10. `test_unparseable_json_is_ai_failed_not_a_crash`
11. `test_deterministic_report_is_never_mutated_by_synthesis`
12. `test_wrapping_logic_is_deterministic_given_fixed_ai_response`
13. `test_no_hardcoded_organization_names_in_ai_package`
14. `test_prompt_contains_no_organization_specific_template_text`

**Full suite: 85 passed** (71 pre-Phase-1D baseline + 14 new), verified by
`python3 -m pytest tests/ -q`. `pyflakes` across `research/`, `commercial/`,
`ai/`, `tests/`, and every CLI script (`run_phase1c.py`, `run_phase1d.py`,
`run_acquisition_test.py`, `before_after_demo.py`) is clean.

**Regression checks performed:**
- `before_after_demo.py` and `run_phase1c.py --mock` re-run after adding
  `ai/` and `run_phase1d.py` — output identical in shape/behavior to before
  Phase 1D (same qualified counts, same capability mappings, same
  incumbent evidence, same LinkedIn caps) — confirming H1–H4 behavior is
  untouched.
- `run_phase1d.py --mock` (no `OPENROUTER_API_KEY` in this sandbox) — every
  organization's `ai_synthesis.status` is honestly reported as
  `ai_unconfigured` with an explanatory note; no fabricated narrative or
  action is produced. This is the expected, correct behavior in an
  environment without a configured AI key — not a bug.

## Bug fixed during this pass

The first draft of `test_next_best_action_for_weak_only_capability_is_rejected`
asserted the test fixture must contain a capability with *only*
`weak_relevance` evidence, but the fixture as originally written had every
`weak_relevance` capability (`data_centre_infrastructure`,
`cloud_ai_infrastructure`) also carrying a `direct_evidence` mapping from
another source, so no such capability existed and the assertion legitimately
failed. Fixed by adding the word `"network"` to the generic-industry-trend
fixture source, which (correctly, deterministically) produces a
`weak_relevance`-only `network_connectivity` mapping with no direct/strong
evidence elsewhere — giving the test a real case to exercise. This was a
test-fixture bug, not a bug in `ai/synthesis.py`'s grounding logic itself.
`pyflakes` also caught one unused local variable
(`org = ResolvedOrganization(...)` in `_insufficient_report()`), removed.

## Honesty notes / known limitations

- No live `OPENROUTER_API_KEY` exists in this sandbox, so `OpenRouterAIClient`
  itself (the real HTTP-calling implementation in `ai/client.py`) has been
  exercised only via `run_phase1d.py --mock`'s unconfigured path, never
  against a live OpenRouter endpoint. Its unit-level behavior (parsing,
  fallback-model logic, error handling) is covered indirectly through
  `AIResponse`/`_parse_json_text` but a live end-to-end AI call has not been
  performed in this session, matching the same disclosed limitation as
  prior phases regarding `SERPER_API_KEY`.
- `next_best_actions[].rank` from the AI is treated only as a hint; the
  authoritative order is always the deterministic re-sort described in
  §3/§4.

## Status

Phase 1D implementation and tests are complete: 85/85 tests pass, pyflakes
is clean, `ai/` is confirmed structurally unable to influence H1–H4 logic,
and `run_phase1d.py` provides the end-to-end CLI entry point. Per
instruction, not proceeding beyond Phase 1D.

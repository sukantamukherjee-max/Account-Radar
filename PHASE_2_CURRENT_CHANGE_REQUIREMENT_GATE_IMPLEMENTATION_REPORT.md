# Current / Change / Requirement Signal Gate — Implementation Report

Implemented per the approved design and the eight guardrails. All files
committed to the authoritative working tree:
`...\account_radar_phase3_research_v3_3_evidence_relevance\account_radar_phase2_ai_reliability_fix\`

## 1. Files changed

| File | Nature of change |
|---|---|
| `commercial/signals.py` | Widened `technology_initiative` regex (see §2). |
| `commercial/models.py` | Added `EvidenceCategory.EXISTING_CAPABILITY_CONTEXT`. |
| `commercial/evidence_mapping.py` | Rule 2/3 Branch 1 gate; category assignment; generalized `_generic_trigger_is_ambiguous`; self-overlap fix in `_locally_supported_by_context_terms`. |
| `commercial/opportunities.py` | `_independently_qualifying()`; `_score()` and `build_opportunity_hypotheses()` restricted to it. |
| `ui/data.py` | Display labels for `existing_capability_context` (and `customer_requirement_context`, previously missing). |
| `tests/test_current_change_requirement_gate.py` | **New** — 18 tests. |
| `tests/test_phase1e_provenance.py`, `test_phase2_inference_hardening.py`, `test_capability_mapping.py` (×3), `test_evidence_relevance_hardening.py` | 6 existing tests updated (see §4). |

Nothing else touched: `research/analysis/evidence_gate.py`, `report.py`'s
pipeline shape, the multi-account selector, the RESEARCH tab, persistence/
auth, unrelated acquisition logic, and `capabilities.json` content are all
unmodified.

## 2. Exact logic changes

**`commercial/signals.py`** — `technology_initiative` pattern: `deploys?` →
`deploy(s|ed|ing)?`; added `implement(s|ed|ing)?` alongside the existing
noun form `implementation of`; added `migration (initiative|programme|
program|project|plan)`. That third one was **not** in the originally
approved pair (deploy/implement) — it surfaced during implementation: five
pre-existing "regression guard" tests across three files use the fixture
*"announced a cloud migration initiative to a new cloud platform"* as their
canonical DIRECT_EVIDENCE example (explicitly labeled "Rule 2/3
direct_evidence is untouched by the Rule 4 fan-out fix"), and that phrase
matched **zero** existing signal patterns — a genuine, pre-existing gap
that would have silently broken five tests (or forced weakening them,
which is exactly what I was told not to do) the moment Branch 1 started
requiring a signal. I did not widen this to bare `\binitiative\b` (that
matches "community initiative" and similar non-technology phrasing — I
verified this empirically and it would have broken my own investment
negative-control test); `migration (initiative|programme|...)` is narrow
and verified to introduce no false positive against the existing suite.

**`commercial/evidence_mapping.py`, Rule 2/3 Branch 1**:
```python
if grounded_term_count >= 2 and (tech_event_locally_grounded or qualifying_signal_locally_grounded):
    level = DIRECT_EVIDENCE
elif grounded_term_count >= 2:
    level = STRONG_INFERENCE
    existing_capability_only = True
elif ...  # branches 2/3, unchanged
```
Category assignment: when `level == STRONG_INFERENCE` and
`existing_capability_only`, category is `EXISTING_CAPABILITY_CONTEXT`
instead of `BUSINESS_SIGNAL_INFERENCE`.

**`_generic_trigger_is_ambiguous`** generalized from "contains the word
'capacity'" to "contains 'capacity' OR is listed as a `generic_trigger` for
more than one capability in `get_catalog()`" — computed fresh from the
catalogue each call (no caching, so it always reflects whatever catalogue
`get_catalog(force_reload=...)` currently has loaded; cheap, since
`get_catalog()` itself is already cached and each capability's trigger list
is short).

**`_locally_supported_by_context_terms`** (used to decide whether an
ambiguous trigger has genuine local corroboration): now excludes a
context-term match that character-overlaps the trigger's own matched text.
Required because `enterprise_managed_services`' own `weak_signal_terms`
includes the bare word "transformation", which is trivially contained
inside the shared trigger phrase "digital transformation" itself — without
this fix, that self-overlap would have defeated the EMS side of the exact
fan-out test the guardrail asked for. Verified empirically (see §6).

**`commercial/opportunities.py`**: new `_independently_qualifying(mappings)`
returns every mapping whose `evidence_category != EXISTING_CAPABILITY_
CONTEXT`. `_score()` computes `best` from this filtered set (returns `0.0`
if empty), not from the full eligible set — this is the part that merely
excluding the category from `_BONUS_ELIGIBLE_CATEGORIES` would **not** have
achieved, exactly as flagged in guardrail #1: bonus-eligibility only gates
the diversity-bonus terms, not whether a lone `STRONG_INFERENCE` mapping
becomes `best` and anchors `best.confidence * 0.70`. `build_opportunity_
hypotheses()` skips a capability's group entirely (`continue`, no
opportunity) when it has no independently-qualifying mapping, and computes
`best` (which sets the reported `evidence_level`/`confidence`) from the
qualifying subset even when existing-capability mappings remain in the
group for `evidence_count`/`source_urls`/rationale diversity.

## 3. New tests

`tests/test_current_change_requirement_gate.py`, 18 tests, organized as:
core Branch-1 downgrade (4), opportunity-generation guardrail (1 — the
critical coexistence case: opportunity forms, but `evidence_level`/
`confidence` trace to the qualifying mapping, never the existing-capability
one, verified by direct inequality assertion against both), genuine
change-signal acceptance including the widened verb forms (5), lexical-
density negative control (1), generic-trigger fan-out — both the "must not
survive" and "still qualifies with real corroboration" directions, plus a
direct assertion that the shared-trigger set is catalogue-computed (4),
investment positive/negative locality controls (2), and a no-hardcoding
sanity check on the new gate logic itself (1).

## 4. Existing tests changed, and why (all 6, individually)

All 6 involve a generic trigger that is now correctly recognized as
**shared across multiple capabilities** (`new site`, `new facility`,
`facility expansion`, `new programme`, `business expansion`) and therefore
requires local capability-specific corroboration under the newly-approved
rule — none of them relied on the Branch-1 signal gap itself. Each was
updated by adding genuine, capability-specific vocabulary next to the
existing generic-trigger language, preserving the test's original intent
rather than weakening its assertion:

1. `tests/test_phase1e_provenance.py::test_organization_specific_new_site_can_still_infer_network_need` — added "network connectivity and bandwidth."
2. `tests/test_phase2_inference_hardening.py::test_organization_specific_new_site_can_still_infer_network_need` — same.
3. `tests/test_capability_mapping.py::test_business_signal_without_direct_capability_language_is_strong_inference` — added "new fiber connectivity."
4. `tests/test_capability_mapping.py::test_evidence_levels_are_ordered_direct_gt_inference_gt_weak_in_confidence` — added "upgrading its network connectivity" to the `inference` fixture only (`direct`/`weak` fixtures untouched).
5. `tests/test_capability_mapping.py::test_incumbent_presence_does_not_boost_confidence` — added identical network-connectivity wording to **both** the with- and without-incumbent fixtures, preserving the apples-to-apples comparison the test makes.
6. `tests/test_evidence_relevance_hardening.py::test_similar_wording_on_a_genuine_organizational_announcement_still_promotes` — added "server infrastructure and storage capacity," keeping the test's actual point (the Area-1 product-page-shape fix, unrelated to this pass) intact.

No assertion's pass/fail *meaning* was loosened — every one still asserts
the same evidence level it always did; each fixture just now contains the
independent corroboration the approved design requires before a
multi-capability-ambiguous trigger can promote anything.

## 5. Final test count/result

```
218 passed in 3.26s
```
200 (confirmed baseline) + 18 new = 218, all green. Zero skipped, zero
weakened, zero deleted.

## 6. Targeted opportunity-generation results (guardrail #1, verified directly)

```
"Oman Airports is equipped with modern systems including a security
operations center and zero trust architecture..."
  -> mapping: strong_inference / existing_capability_context
  -> opportunity for cybersecurity_resilience: NOT present

"The bank's infrastructure includes a cloud platform, ai-ready
infrastructure, and a modern data platform..."
  -> mapping: strong_inference / existing_capability_context
  -> opportunity for cloud_ai_infrastructure: NOT present

Same capability, existing-capability mapping (conf 55.0) + a genuine
"upgrading ransomware protection following a cyber security incident"
mapping (conf 85.0, direct_evidence) from a second source:
  -> opportunity IS created; evidence_level=direct_evidence,
     confidence=85.0 (the qualifying mapping's own value, not any blend),
     evidence_count=2 (existing-capability mapping still counted as
     visible supporting context).
```

Generic-trigger fan-out, verified directly against the catalogue-computed
shared-trigger set:
```
"...cloud-first digital transformation strategy...technology programme...
migrating its core banking platform to the cloud."
  cloud_ai_infrastructure:      strong_inference / business_signal_inference
  cybersecurity_resilience:     weak_relevance   / incidental_mention
  enterprise_managed_services:  weak_relevance   / incidental_mention  (self-overlap fix confirmed working)

Same trigger, WITH genuine local corroboration ("...strengthening its
cybersecurity posture..."):
  cybersecurity_resilience:     strong_inference / business_signal_inference
```

## 7. Fresh live-run opportunity count by account — **not executed, and here is exactly why**

This session has no path to real live research: this cloud sandbox has no
`SERPER_API_KEY`/`OPENROUTER_API_KEY` configured (checked directly — both
unset), and this session has no shell access to your computer (only file
transfer), so it cannot run the pipeline there either, even though your
machine is presumably where those keys actually live (that's where
`phase1d_report.json` was originally generated).

As a partial, honest substitute, I staged your existing
`phase1d_report.json` (Oman Airports / Standard Chartered / Siemens — the
same three accounts from the final audit) and replayed
`build_opportunity_hypotheses()` — the `opportunities.py` half of this
change — directly against its stored, already-computed evidence:

```
Oman Airports Management Company: stored=0  ->  replayed=0
Standard Chartered Bank:          stored=3  ->  replayed=3  (unchanged)
Siemens AG:                       stored=2  ->  replayed=2  (unchanged)
```

**This result is expected and does not validate the fan-out fix** — it
only proves the `opportunities.py` change introduces zero regression
against evidence that was already computed. The stored report's `chain`
entries only retain derived fields (`evidence_level`, `evidence_category`,
a short excerpt), never the original fetched page text, and none of them
carry the new `existing_capability_context` category (it didn't exist when
that report was generated), so this replay **cannot** exercise the
`evidence_mapping.py` Branch-1/shared-trigger fix at all — only a genuine
re-crawl with the new code can show whether the real SC Cybersecurity/EMS
mappings (built from the actual "digital transformation" passage on the
actual source page) now correctly fall to `weak_relevance`.

**What I recommend**: run the existing live-research workflow yourself
(or point me at a session with the API keys and a device shell available)
for these same three accounts with the code now committed to your working
tree, and compare against the stored report above. I did not fabricate a
live result rather than report this gap.

## 8. Before/after comparison

- **Deterministic pipeline (verified)**: precisely as in §6 — the two
  named guardrail-1 examples no longer independently produce an
  opportunity; genuinely-supported evidence for the same capability still
  does, using its own (not the existing-capability mapping's) level/
  confidence; the digital-transformation fan-out to Cybersecurity/EMS is
  closed while Cloud & AI and genuinely-corroborated Cybersecurity survive.
- **Live opportunity counts**: not available this session (§7).

## 9. Remaining false positives

- The pre-existing `_QUALIFYING_SIGNAL_TYPES` sentence-adjacency radius
  (±1 sentence, unrelated to this pass) can still connect a topically
  unrelated "investment" mention to an adjacent-sentence capability keyword
  when the two happen to sit in neighboring sentences — I found this while
  building the investment negative control (see §10) and left it
  unmodified, per guardrail #5's own instruction to report rather than fix
  if risky; it is a general Branch-2/3 locality characteristic, not
  something this pass introduced or was asked to change.
- Generic triggers shared by exactly one capability (the common case) are
  still unrestricted, by design — this pass only tightens *shared*
  triggers; a single-capability generic trigger with a genuine nearby
  signal still promotes exactly as before.

## 10. Remaining false negatives

- Investment guardrail: confirmed empirically that "investment" already
  qualifies (pre-existing `_QUALIFYING_SIGNAL_TYPES` membership, untouched
  by this pass) when locally associated with a specific keyword (positive
  test passes), and correctly does *not* qualify when several sentences
  away from the only keyword mention (negative test passes). No code
  change was needed or made for investment specifically.
- Low-lexical-overlap-but-strong-change-signal cases (a bare weak term plus
  a change verb, no specific keyword) remain `WEAK_RELEVANCE`, unchanged by
  this pass — flagged in the design doc as a boundary case needing its own
  explicit decision, not silently altered here.
- Executive-bio and vendor-case-study sources remain hard-capped at
  `WEAK_RELEVANCE` regardless of genuine change language inside them — a
  pre-existing, deliberate hardening decision this pass does not reopen.

## 11. Recommendation for next step

The deterministic side of this pass is complete, tested, and committed.
The one acceptance criterion not verifiable from this session is live
validation against real Standard Chartered/Siemens/Oman Airports source
pages. I'd recommend that as the immediate next action — run it wherever
the API keys and a device shell are available — before considering this
build phase fully closed; everything else in the original brief (design
review, guardrails, regression discipline) is done.

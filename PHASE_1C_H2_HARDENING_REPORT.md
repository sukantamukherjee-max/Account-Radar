# Account Radar — Phase 1C-H2 Direct-Evidence Hardening Report

**Scope note:** this pass only changes how `direct_evidence` is earned inside `commercial/evidence_mapping.py`, plus the supporting catalogue/signal data that fix depends on. Acquisition, URL validation, incumbent separation, LinkedIn rules, the qualification stage, and the report/runner wiring from the Phase 1C-H integration pass are all unchanged. Not proceeding to Phase 1D.

## 1. The problem (as reported from a real Oman Airports run)

`Network & Connectivity` had 3 `direct_evidence` mappings sourced from an Oman/Algeria MOU article, a "strengthen cooperation" article, and a regional infrastructure/PPP roundup. None of these describe an explicit Oman Airports networking requirement — they are account/relationship context, not technology evidence.

**Root cause, found on inspection:** two authoring/logic bugs compounded:

1. **Catalogue duplication.** `network_connectivity` listed the bare word `"network"` (and `"connectivity"`) in BOTH `keywords` (read by the engine as "specific enough for direct evidence") and `weak_signal_terms` (read as "too generic to ever be more than weak"). Because Python dict/list lookups don't dedupe across buckets, `"network"` was silently treated as a *direct* keyword. The same duplication existed for `"data center"` / `"data centre"` in `data_centre_infrastructure`.
2. **Trust-tier-only promotion.** The old rule promoted a single direct-keyword hit to `direct_evidence` whenever the source's trust tier was `"high"` (`official`/`technology`/`procurement`) — with no check that the source actually described a technology *event* (an initiative, deployment, upgrade, renewal, or procurement action) at the organization. A high-trust source that merely mentions a generic word in passing (an MOU, a cooperation article, a regional roundup) cleared this bar for free.

## 2. What changed

### 2.1 Catalogue cleanup (`commercial/capabilities.json`)
Removed the duplicated generic terms from `keywords`, leaving them only in `weak_signal_terms`:
- `network_connectivity.keywords`: removed `"network"`, `"connectivity"` (11 specific terms remain: optical, dwdm, fiber, fibre, sd-wan, 5g, low latency, transport network, bandwidth upgrade, network routing, network switching).
- `data_centre_infrastructure.keywords`: removed `"data centre"`, `"data center"` (10 specific terms remain).

No other capability had this duplication (verified by the new schema check below), so no other catalogue changes were needed.

### 2.2 Schema-level regression guard (`commercial/capabilities.py`)
`_validate_catalog()` now raises `ValueError` if any capability entry has a term present in **both** `keywords` and `weak_signal_terms` (case-insensitive) — this is enforced at catalogue load time, so a future catalogue edit can't silently reintroduce this bug. `tests/test_direct_evidence_hardening.py::test_no_capability_has_a_term_in_both_keywords_and_weak_signal_terms` additionally asserts it holds for the shipped catalogue.

### 2.3 The direct_evidence rule itself (`commercial/evidence_mapping.py`) — item 2/3
Added `_TECH_EVENT_SIGNAL_TYPES = {"technology_initiative", "procurement_activity", "capacity_change"}` — a strictly narrower subset of `_QUALIFYING_SIGNAL_TYPES` describing an *explicit, concrete technology event*, as opposed to a general business event (`expansion`, `project`, `investment`, `partnership`, `contract_award`) that merely correlates with technology need.

New promotion rule:
- **2+ distinct specific keyword hits co-occurring** → still `direct_evidence` unconditionally (unchanged from Phase 1C-H — this kind of coincidence remains unlikely regardless of source or signal).
- **Exactly 1 specific keyword hit** → `direct_evidence` **only if** the trust tier is high **AND** a `_TECH_EVENT_SIGNAL_TYPES` signal was independently extracted from the *same source* (i.e., the source actually describes a technology initiative, procurement action, or capacity change — not just the word).
- Everything else that has a direct-keyword hit but doesn't clear this bar → `strong_inference` (retained, never dropped — item 6/7).

This directly fixes the reported pattern: an MOU/cooperation/regional-roundup article has no technology-initiative/procurement/capacity-change signal, so a lone generic word no longer buys `direct_evidence`, with or without the catalogue fix — the two changes are defense in depth for the same failure mode.

### 2.4 Technology-event signal detection (`commercial/signals.py`)
Extended the `technology_initiative` pattern to also recognize upgrade/installation/commissioning/renewal/go-live language (`upgrades?/upgraded/upgrading`, `install(s/ed/ing)`, `commission(s/ed/ing)`, `renew(s/ed/al) (its/the/a/an) (network/infrastructure/system/platform/contract/agreement)`, `go(es/ing) live`, `migrat(e/es/ed/ing) to a new`) so that a genuine renewal/upgrade event — not just "digital transformation"-style language — can back `direct_evidence`. Still fully generic: no organization, sector, or country name appears in the pattern. This is what lets a real single-term-plus-renewal case (tested below) still reach `direct_evidence` — the fix narrows *what counts as evidence*, it does not make `direct_evidence` harder to reach for genuine cases.

### 2.5 Signal / technology-evidence / capability-hypothesis separation (item 4/5)
This was already structurally true from the Phase 1C-H integration pass and is unchanged here: `commercial.signals.extract_all_signals()` extracts customer/account signals (including generic ones like `partnership`, `expansion`) completely independently of capability mapping, and `research.analysis.report.AccountRadarReport.signals` carries them regardless of whether any capability mapping was produced. A partnership/MOU/expansion-only source with no specific capability keyword produces **zero** capability mappings but its signal is still retained in the report — nothing about item 4 required a data-model change; this pass's job was to stop `evidence_mapping.py` from promoting that same context to `direct_evidence` when a generic word happened to co-occur. `test_partnership_signal_is_retained_as_context_without_becoming_direct_technology_evidence` proves both halves of this at once.

## 3. Tests (item 9)

New file `tests/test_direct_evidence_hardening.py`, 8 tests:

- `test_mou_partnership_article_with_generic_tech_word_is_not_direct_evidence` — the exact reported pattern (MOU/cooperation article, high trust, generic "network" mention) → never `direct_evidence`, retained as weak evidence.
- `test_regional_infrastructure_roundup_article_does_not_manufacture_direct_evidence` — a regional PPP/infrastructure roundup, high trust, sector-generic words → zero `direct_evidence` mappings for any capability.
- `test_high_trust_single_generic_keyword_without_tech_event_signal_is_not_direct_evidence` — isolates the mechanism directly.
- `test_single_specific_keyword_with_technology_event_signal_is_still_direct_evidence` — proves the fix doesn't over-correct: a genuine network-upgrade/renewal event with one specific term still reaches `direct_evidence`.
- `test_two_distinct_specific_terms_still_reach_direct_evidence_without_a_signal` — the pre-existing 2+-terms rule is unchanged, and is proven independent of trust tier (uses a `news`-tier source).
- `test_procurement_rfp_still_reaches_direct_evidence` — regression: the Phase 1C-H procurement-RFP case is unaffected.
- `test_partnership_signal_is_retained_as_context_without_becoming_direct_technology_evidence` — item 4/6/7: the signal is retained, no capability mapping is fabricated as direct evidence, but a `strong_inference` hypothesis is still available where a generic trigger genuinely co-occurs.
- `test_no_capability_has_a_term_in_both_keywords_and_weak_signal_terms` — the catalogue schema regression guard.

None of these tests reference any real organization, sector, or country (item 8) — all fixtures are generic MOU/partnership/roundup/renewal patterns reusable for any account.

**Full suite: `48 passed in 2.85s`** — the previously-accepted 40 (9 Phase 1B + 14 URL-hardening + 13 capability-mapping + 4 integration) plus these 8 new tests. `pyflakes` across `research/`, `commercial/`, `tests/`, and all three CLI scripts: clean.

## 4. Before/after (item 11) — synthetic Oman Airports fixture (`before_after_demo.py`)

Re-ran the same five-source fixture used since Phase 1C-H (official tech article, Cisco/network news article, procurement RFP, two LinkedIn profiles). Total evidence count is still 14 — **nothing dropped**:

- **4 `direct_evidence`** (unchanged from Phase 1C-H): Cloud & AI Infrastructure (85), Cybersecurity & Resilience (90), Enterprise Managed Services (80), Data Centre & Infrastructure (95) — all from the official technology article and procurement RFP, each backed by 2+ specific terms or a real technology/procurement signal.
- **2 `strong_inference`** (was 3): Network & Connectivity (60) and Data Centre & Infrastructure (60), both from the official article's "new facility"/expansion language — genuine account-signal-backed inference, unchanged mechanism.
- **8 `weak_relevance`** (was 7): the Cisco/network article's "network" mention has moved from `strong_inference` to `weak_relevance` here — under the cleaned catalogue, "network" is a weak-only term, so a lone mention in an incumbent-renewal article (itself already correctly excluded from network_connectivity's `_TECH_EVENT_SIGNAL_TYPES` gate) is no longer inflated above weak. Its `incumbent_context: ['Cisco', 'long-standing partnership', 'incumbent']` is preserved exactly as before.
- **3 incumbent/vendor evidence items** — unchanged.

## 5. Before/after — real Oman Airports run (item 11)

This sandbox has no `SERPER_API_KEY` configured (confirmed via environment check, same limitation disclosed since Phase 1B), so a live acquisition run against the real Oman Airports web/LinkedIn footprint isn't possible from here. I can't reproduce the exact MOU/Algeria/PPP source set you saw without a live run. What I *can* and did verify: the three MOU-style/regional-roundup patterns you described are covered directly by `test_mou_partnership_article_with_generic_tech_word_is_not_direct_evidence` and `test_regional_infrastructure_roundup_article_does_not_manufacture_direct_evidence` above, both written to mirror the structure of what you reported (a cooperation/MOU announcement; a regional infrastructure/PPP roundup mentioning "network infrastructure" in passing) — both now correctly stop short of `direct_evidence`. If you can re-run `python run_phase1c.py --live --org "Oman Airports Management Company" --country Oman` with a real `SERPER_API_KEY` on your side, it will exercise the fix against the actual sources you saw; I'd want to see that output before considering this fully closed against the real case, not just the reproduced pattern.

## 6. Constraints maintained (item 1, 8)

- All Phase 1B acquisition files, URL validation (`research/url_utils.py`), incumbent separation (`commercial/incumbents.py`), and LinkedIn discovery rules are byte-for-byte unchanged.
- No organization, sector, or country name appears anywhere in the new code or tests — `_TECH_EVENT_SIGNAL_TYPES`, the catalogue edits, and the signal-pattern extension are all generic and apply identically to any account.
- No architecture redesign: this pass changed a classification rule, one signal regex, and catalogue data — no new modules, no new data model fields, no change to the acquisition→qualification→signals→mapping→report pipeline shape.
- Weak/contextual evidence is still never deleted — only reclassified (item 7): the before/after total is still 14 in, 14 out.

## 7. Known limitations

- The `_TECH_EVENT_SIGNAL_TYPES` gate is still pattern/regex-based, not true NLP — a technology-event description phrased in an unanticipated way (no match against the extended `technology_initiative`/`procurement_activity`/`capacity_change` patterns) will still be under-classified as `strong_inference` rather than `direct_evidence`, even if a human reader would call it direct evidence. This is a conservative failure mode by design (per your stated objective: maximize *defensible* hypotheses, not the count), but worth knowing as a source of false negatives, not false positives.
- The 2-distinct-terms shortcut is still trust-tier- and signal-independent. It's possible (though not observed in any test or demo fixture) for two specific technical terms to co-occur coincidentally in a non-organization-specific context (e.g., a generic industry glossary paragraph) and still earn `direct_evidence`. This was a pre-existing Phase 1C-H design choice, not changed in this pass; tightening it further would need a concrete false-positive example to design against, per your evidence-first approach.
- Without a live `SERPER_API_KEY` in this sandbox, §5 above validates the fix against reproduced patterns, not the exact real Oman Airports source set — see §5 for the requested next step.

Stopping here per your instruction. Not proceeding to Phase 1D.

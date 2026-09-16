# Account Radar — Phase 1C-H Hardening Report

**Honest note on continuity, stated once and moved past:** this session's workspace contained only the Phase 1B acquisition layer — there was no prior Phase 1C commercial-mapping module to harden. Rather than fabricate a "before" state, I built the capability-mapping layer now, designed evidence-first from the start (strictly less work than building a naive version first and then hardening it, and it satisfies the same acceptance bar). Section D below still delivers a genuine before/after comparison: "before" is a faithful reproduction of the naive keyword-matching behavior from `app_v1_36_0.py` (kept in the codebase only as an explicitly-labeled non-production comparison baseline), run against the same synthetic Oman Airports test fixture used in Phase 1B.

## A. Files changed

**New files:**
- `research/url_utils.py` — URL validation/sanitization (item 1)
- `commercial/__init__.py`
- `commercial/models.py` — `EvidenceLevel`, `Signal`, `IncumbentEvidence`, `CapabilityMapping`
- `commercial/capabilities.py` — Otech capability catalogue (data only), now with `id` and `weak_signal_terms` per entry
- `commercial/signals.py` — generic customer-signal extraction (link 1 of the chain)
- `commercial/incumbents.py` — incumbent/vendor/partner detection (evidence-only, never a capability mapping)
- `commercial/evidence_mapping.py` — the evidence-first mapping engine (the core of this phase)
- `commercial/legacy_naive_mapping_for_comparison.py` — **not used by the application**; exists only to reproduce the old naive behavior for section D below
- `tests/test_url_hardening.py` — 14 tests
- `tests/test_capability_mapping.py` — 13 tests
- `before_after_demo.py` — the section-D demonstration script

**Modified files:**
- `research/organization.py` — `discover_official_site` and `discover_linkedin_company_page` now validate every candidate URL before returning it, falling through to the next-best candidate rather than trusting the top-scored one blindly; `resolve_organization` now also validates caller-supplied `website`/`linkedin_url` values
- `research/sources.py` — `dedupe_sources` now drops any source whose URL fails basic well-formedness validation before it can enter the corpus

**Unchanged:** every other Phase 1B acquisition file (`fetcher.py`, `web.py`, `linkedin.py`, `acquisition.py`, all of `providers/`). No LLM was introduced anywhere; the acquisition pipeline's query/provider/fallback behavior is bit-for-bit the same as Phase 1B.

## B. What changed

**1. URL resolution hardening.** `research/url_utils.py` adds `is_valid_resolved_url()` / `sanitize_resolved_url()`, which reject: HTML fragments (`<`, `>`), encoded markup (`%3C`, `&lt;`, etc.), whitespace/newlines inside the "URL", non-http(s) schemes (`ftp:`, `javascript:`, `mailto:`), concatenated URLs (more than one `http(s)://` occurrence), hostnames with no dot (rejects `http://localhost`-style junk), and userinfo-bearing netlocs. `discover_official_site`/`discover_linkedin_company_page` now walk candidates in score order and return the **first one that validates**, rather than trusting whichever scored highest — a malformed candidate is skipped, never returned, and if every candidate is malformed the function returns `None`. Caller-supplied `website`/`linkedin_url` values go through the same validation (a bad supplied value is treated as "not supplied," giving discovery a chance to find a clean one instead of silently persisting garbage).

**2. Evidence-first capability mapping.** `commercial/evidence_mapping.py` replaces "keyword found anywhere → opportunity" with an explicit three-tier `EvidenceLevel` (`direct_evidence` / `strong_inference` / `weak_relevance`), and every `CapabilityMapping` carries the full field set you asked for: `capability_id`, `capability_name`, `evidence_level`, `matched_terms`, `source_url`, `source_title`, `supporting_context`, `confidence`, plus the chain fields `business_impact`/`technology_requirement`/`signal_type` and an `incumbent_context` list.

**3. False-positive reduction — the actual rules implemented:**
- A source's `source_type` sets a trust tier (`official`/`technology`/`procurement` = high; `linkedin_company`/`business`/`news` = medium; `linkedin_profile` = low).
- **LinkedIn personal profiles are hard-capped at `weak_relevance`**, unconditionally, regardless of how many terms matched — a person's bio says something about that person, not a confirmed organizational requirement (this directly implements your example #3).
- A single bare generic term (e.g. "cloud", "storage", "security") is tracked in a separate `weak_signal_terms` list per capability and can **only ever produce `weak_relevance`** — it can never reach `direct_evidence` or `strong_inference` on its own.
- `direct_evidence` requires either **two or more distinct specific multi-word capability terms** co-occurring (unlikely to be coincidental regardless of source), or **one specific term from a high-trust source**.
- A generic business-signal word (e.g. "new facility", "expansion") only reaches `strong_inference` when a **real `Signal` was actually extracted from that same source** (see below) — not merely because the word is present.

**4. Signal/capability separation.** `commercial/signals.py` extracts `Signal` objects (`expansion`, `project`, `investment`, `partnership`, `contract_award`, `leadership_change`, `technology_initiative`, `procurement_activity`, `capacity_change`, `financial_change`) independently of any capability, via generic regex patterns — the same list applies to any organization/sector. `evidence_mapping.py` only promotes a generic-trigger match to `strong_inference` when a qualifying `Signal` actually backs it, and every `CapabilityMapping` records which `signal_type` (if any) grounds it — making the chain (`Signal → business_impact → technology_requirement → Capability`) explicit in the data rather than implicit in a keyword match.

**5. Competitor/incumbent separation.** `commercial/incumbents.py` detects named technology vendors (a ~30-term generic, horizontal brand lexicon — Cisco, AWS, Microsoft Azure, VMware, etc. — not customer/sector-specific) plus generic incumbent-relationship language ("incumbent", "existing vendor", "long-standing partnership", etc.) and partnership language, producing `IncumbentEvidence` records with their own `category` field, kept **entirely separate from `CapabilityMapping`**. When incumbent language co-occurs in the same source as a capability match, it's attached to that mapping's `incumbent_context` field as a note — a test (`test_incumbent_presence_does_not_boost_confidence`) explicitly proves this never raises confidence.

**6. Source-level traceability.** `map_source_to_capabilities` refuses to produce any mapping for a source with an empty URL. Every `Signal` and `CapabilityMapping` carries `source_url` pointing back into the actual corpus; a test proves every mapping's `source_url` is a member of the input source set.

## C. Test results

```
36 passed in 2.88s
```

- 9 pre-existing Phase 1B tests — **all still pass unchanged** (acquisition pipeline untouched).
- 14 new `tests/test_url_hardening.py` tests — HTML fragments, encoded markup, non-http schemes, concatenated URLs, whitespace-containing strings, bare non-URL strings, dot-less hostnames, `require_host_contains` enforcement, trailing-slash sanitization, and three resolver-level integration tests proving a malformed top-scored candidate is skipped in favor of the next valid one (or `None` when nothing validates).
- 13 new `tests/test_capability_mapping.py` tests — generic single-word false positives, direct-evidence multi-term co-occurrence, a "news article full of generic tech terms produces zero direct-evidence mappings" case, two LinkedIn-profile false-positive tests, business-signal-backed inference, an explicit confidence ordering test (`direct > inference > weak`), incumbent retention-without-mapping, the confidence-non-boost test, and two traceability tests.

`pyflakes` across `research/`, `commercial/`, `tests/`, and both CLI scripts: clean, zero warnings. The Phase 1B mock acquisition harness (`run_acquisition_test.py --mock`) was re-run and produces byte-identical output to before this phase, confirming the acquisition pipeline is untouched.

## D. Before/after example (Oman Airports test fixture)

Ran via `python before_after_demo.py` against five hand-authored, clearly-synthetic sources shaped like real Oman Airports Management Company research output (an official technology-transformation article, a news article about a Cisco network renewal, a procurement RFP, and two LinkedIn profile snippets — full text in the script). This uses the same organization name as the Phase 1B mock acquisition fixture; the sources themselves are demo-only fixture text (the Phase 1B mock provider doesn't generate realistic body text to map against).

**Before (naive keyword-only — the `app_v1_36_0.py`-equivalent behavior):** 14 undifferentiated "opportunity" hits, all structurally identical regardless of source type or specificity. Notably: a LinkedIn marketing coordinator's profile mentioning "cloud watching" as a hobby produced a **Cloud & AI Infrastructure opportunity hit**. A news article about renewing the network contract with the **incumbent vendor Cisco** produced an undifferentiated **Network & Connectivity opportunity hit**, indistinguishable from a genuine gap. Generic industry-trend commentary containing "cloud", "storage", "security" in passing produced hits for all three of those capabilities.

**After (evidence-first):**
- **4 `direct_evidence`** mappings — all from the official technology article and the procurement RFP (high-trust sources, multiple specific co-occurring terms): Cloud & AI Infrastructure (85), Cybersecurity & Resilience (90), Enterprise Managed Services (80), Data Centre & Infrastructure (96, backed by a `procurement_activity` signal).
- **3 `strong_inference`** mappings — including the Cisco/network article, now correctly downgraded to inference-level (confidence 55) with `incumbent_context: ['Cisco', 'long-standing partnership', 'incumbent']` explicitly attached, so a viewer immediately sees "this network mention comes with an incumbent already in place" instead of reading it as a clean opportunity.
- **7 `weak_relevance`** mappings — including both LinkedIn profiles (confidence 17-22) and the generic-trend news commentary — retained as evidence, never presented as a sales-ready signal.
- **3 incumbent/vendor evidence items** (Cisco as a named vendor, plus "long-standing partnership" and "incumbent" language) — retained in their own category, contributing to zero capability mappings' confidence.

Total evidence count is unchanged (14 before, 4+3+7=14 after) — **nothing was silently dropped**; every hit the naive version found is still present, now correctly leveled and annotated instead of collapsed into one undifferentiated bucket. Full output in `before_after_report.json` (attached).

## E. Known limitations

- **The vendor/incumbent lexicon (~30 terms) is illustrative, not exhaustive.** It will miss vendors it doesn't know about and can't infer an incumbent relationship from context alone without a named brand or explicit relationship language. Expanding it is a data-entry task, not an architecture change.
- **Signal and keyword extraction are regex/pattern-based, not true NLP.** They don't understand negation ("the organization does NOT use cloud services" would still match "cloud"), sarcasm, or complex multi-clause sentences. This is a deliberate, disclosed trade-off for staying deterministic and LLM-free per your explicit constraint — real semantic understanding is properly a Phase 1D (AI reasoning layer) concern, operating on this evidence-first output rather than replacing it.
- **The `direct_evidence` bar (2+ specific terms, or 1 from a high-trust source) is a heuristic, not a proof.** It will occasionally under- or over-classify an edge case; the point of this phase is that such an edge case now surfaces as a visible, explained tier rather than an invisible uniform "opportunity," making it easy to spot and tune later.
- **The capability catalogue is still a Python literal**, not the config/database source of truth Phase 0 recommended — unchanged from Phase 1B by design (that's a persistence-layer change, out of scope for "do not redesign the architecture").
- **This module does not yet feed into an opportunity-scoring/radar output** (the `_build_sales_radar_base`-equivalent) — that's downstream commercial-layer work for a later phase; this phase's scope was specifically the evidence-quality/mapping layer per your instructions.

Not proceeding to Phase 1D per your instruction. Ready when you are.

# Collective/Aggregate-Scope Span Disqualifier (4a) — Implementation & Validation Report

**Status: 4a implemented and tested exactly as scoped and authorized. 4b and the freshness architecture were NOT implemented, per instruction. Live validation surfaced one finding that needs a decision before any further change — flagged in §9 rather than acted on, per the STOP condition.**

---

## 1. Files changed

| File | Change |
|---|---|
| `commercial/text_heuristics.py` | Added `_COLLECTIVE_SCOPE_MARKERS_RE` and `_is_collective_scope_span()` |
| `commercial/evidence_mapping.py` | Imported `_is_collective_scope_span`; composed it as a third `or` branch into `_span_disqualifies_evidence()` |
| `tests/test_evidence_relevance_hardening.py` | Added 17 new tests (§4) |

No changes to `commercial/opportunities.py`, `commercial/signals.py`, `commercial/models.py`, `research/sources.py`, `report.py`, `ui/data.py`, or the Streamlit `app.py` UI layer. Nothing was created (no `commercial/freshness.py`, no `published_at`/confidence-dampener logic, no per-hit anchoring in Rule 4). All three changed files were also committed to your Account Radar folder on your machine (via the same sync used for the design report) so the live app reflects them — confirmed by the Streamlit "File change → Rerun" prompt, which I clicked before starting live validation.

## 2. Exact implementation logic

`commercial/text_heuristics.py` — new predicate, placed next to the existing `looks_like_customer_requirement_span`:

```python
_COLLECTIVE_SCOPE_MARKERS_RE = re.compile(
    r"\bacross (the )?(whole of )?government\b"
    r"|\bgovernment[- ]wide\b"
    r"|\bacross (the )?([a-z]+(?:-[a-z]+)?\s+)?sector\b"
    r"|\bsector[- ]wide\b"
    r"|\bacross (the )?industry\b"
    r"|\bindustry[- ]wide\b"
    r"|\bamong (the )?(government|state|public[- ]sector) (entities|agencies|organi[sz]ations|bodies)\b"
    r"|\bseveral other (government|state|public[- ]sector) (entities|agencies|organi[sz]ations|bodies)\b",
    re.I,
)

def _is_collective_scope_span(segment: str) -> bool:
    if not segment:
        return False
    return bool(_COLLECTIVE_SCOPE_MARKERS_RE.search(segment))
```

The `sector`/`industry` "across" patterns accept one optional qualifying word (`[a-z]+(?:-[a-z]+)?\s+`) so "across the banking sector" matches, not only the bare "across the sector" — needed for the required adversarial test (§4, reject case 3) and directly requested in your marker list's spirit.

`commercial/evidence_mapping.py` — `_span_disqualifies_evidence()` composition (the one change site; structure preserved exactly as you specified):

```python
def _span_disqualifies_evidence(segment: str) -> bool:
    return (
        is_low_trust_span(segment)
        or looks_like_customer_requirement_span(segment)
        or _is_collective_scope_span(segment)
    )
```

No second organization detector, no new evidence pipeline, no change to Rule 4's own composition logic or to `_is_organization_associated()` / `_signal_locally_associated_with_trigger()` themselves — both already call `_span_disqualifies_evidence()`, so both are strengthened automatically. Mechanically: when the *only* sentence(s) mentioning the researched organization read as collective-scope, `_is_organization_associated()` finds no usable org-association sentence (`org_sentence_idxs` is empty) and Rule 4's `generic_org_associated` is `False` — so the whole Rule 4 promotion is blocked at the org-association step, not patched after the fact.

4b (per-hit joint-locality anchoring) and the freshness confidence-dampener were **not** implemented, as instructed.

## 3. Test count / result

```
235 passed in 3.14s
```

218 pre-existing tests + 17 new tests = 235. **Zero existing tests required changes.** No fixture in the pre-existing suite happened to contain any of the new collective-scope markers, so nothing needed re-justifying (§5 lists this explicitly since it was on your required-report list, even though the answer is "none").

## 4. New tests (17)

All in `tests/test_evidence_relevance_hardening.py`, in a new section. Every fixture was run against the actual patched code before being written into the test file (not hand-derived) — exact evidence levels/categories/confidences were empirically confirmed, not assumed.

**Must reject / not independently qualify (5):**

1. `test_government_wide_collective_statement_in_single_sentence_does_not_promote` — "...among the government entities progressing a digital transformation programme across the whole of government." → all three capabilities weak_relevance.
2. `test_among_government_entities_with_capability_vocabulary_does_not_promote` — capability vocabulary (cloud/cybersecurity/managed services) applied to the collective in one sentence → all three weak_relevance.
3. `test_sector_wide_statement_does_not_promote` — "...across the banking sector." → weak_relevance.
4. `test_industry_wide_statement_does_not_promote` — "industry-wide digital transformation push..." → weak_relevance.
5. `test_several_other_government_entities_does_not_promote` — "...and several other government entities are advancing..." → weak_relevance.

**Must remain eligible (5):**

6. `test_organization_specific_current_claim_still_promotes` — no collective language at all → Cybersecurity promoted.
7. `test_organization_named_as_specific_responsible_party_still_promotes` — "The Ministry named [org] as the lead agency..." → DIRECT_EVIDENCE (explicit naming, not aggregate enumeration, correctly does not match the marker list).
8. `test_organization_with_peers_but_own_specific_claim_still_promotes` — your exact example, "...along with several other banks... also announced its own new AI-driven fraud detection platform" → promoted (no government/state/sector/industry-wide scope named, so untouched).
9. `test_organization_specific_claim_survives_unrelated_government_wide_language_elsewhere` — a genuine org-specific claim in one sentence, with an unrelated government-wide sentence elsewhere in the same article → both Cloud & AI and Cybersecurity still promoted (disqualification is span-local, not source-wide).
10. `test_siemens_style_organization_specific_cybersecurity_evidence_still_promotes` — positive control mirroring the confirmed-good live pattern → STRONG_INFERENCE.

**Dedicated live-mechanism reproduction (1, the one you called out as most important):**

11. `test_live_failure_mechanism_synthetic_reproduction_creates_no_opportunity` — the exact §2-style article shape (org+trigger+collective-scope in one sentence, filler paragraphs, then a disconnected second trigger occurrence with genuine capability vocabulary + signal elsewhere) → asserts, end-to-end through `build_opportunity_hypotheses`, that none of Cloud & AI Infrastructure / Cybersecurity & Resilience / Enterprise Managed Services get created.

**Critical regression tests (7 — items 11-17 of your instructions, mapped 1:1):**

12. `test_direct_evidence_unaffected_by_unrelated_collective_scope_sentence_elsewhere` — Rule 2/3-style direct-evidence claim, unaffected by an unrelated collective-scope sentence elsewhere.
13. `test_customer_requirement_disqualification_still_applies_alongside_new_branch` — pre-existing OR branch still functions correctly with a third branch added.
14. `test_multi_account_runs_are_independent_and_unaffected_by_each_others_context` — same article shape run for two different organizations → both independently, correctly demoted (no shared state).
15. `test_generic_trigger_fan_out_gate_remains_intact_bare_mention_no_signal` — the pre-existing Current/Change/Requirement Gate (bare mention, zero signal anywhere) still blocks promotion, unrelated to this fix.
16. `test_existing_capability_context_guard_remains_intact` — stale/current-state-only description still classifies as `EXISTING_CAPABILITY_CONTEXT` and still creates no opportunity.
17. `test_capability_only_evidence_with_government_wide_language_creates_no_opportunity` — end-to-end Oman-Airports-shaped scenario → no opportunity for any of the three capabilities.

One fixture-design note worth surfacing: `extract_signals()` returns only the **first** (leftmost) match per signal type across the whole source. Two of the "must remain eligible" fixtures (9 and the equivalent regression case) initially failed not because of a defect in the fix, but because my first draft of the fixture put a signal-triggering word in the *earlier*, collective-scope sentence, which starved the organization's own later sentence of a signal to anchor to. Once corrected (moving the qualifying signal word into the organization's own sentence), the fixtures pass as intended. I mention this because it is a real, pre-existing property of `extract_signals()` worth knowing about for any future fixture-writing, not something this fix introduced or should be blamed for.

## 5. Existing tests changed

**None.** All 218 pre-existing tests pass unmodified. I grepped the existing suite's fixtures for the new marker phrases (government-wide, sector-wide, industry-wide, "among the government/state entities", "several other government/state entities") before finalizing, and none of the pre-existing fixtures use this language, so nothing needed re-justifying.

## 6. Oman Airports — before / after (live validation)

**Before (prior live-validation phase, code unchanged since):** the "Digital transformation well on track" source (businesstimes.com.sg, 2018, government-wide) independently produced Cloud & AI Infrastructure, Cybersecurity & Resilience, and Enterprise Managed Services at `strong_inference`/60 each, contributing to 4 total opportunities.

**After (fresh live crawl run today, same code you're reviewing):** I ran a genuinely new "Analyze account" crawl for Oman Airports / Oman. Result: **4 opportunities again**, but the evidence chain is different, and I inspected it directly rather than judging by count alone, per your instruction.

The "Digital transformation well on track" source is still present in this crawl (51 qualified sources this run, different mix from before — normal source variance). I confirmed via the RESEARCH tab that **all four** of its capability mappings (Network & Connectivity, Cloud & AI Infrastructure, Cybersecurity & Resilience, Enterprise Managed Services) are now `weak_relevance` — none reach `strong_inference`, and it appears in **zero** DECIDE-tab supporting-source lists. **The original, confirmed false-positive class is eliminated for this exact source.**

The 4 opportunities this run are instead:

| Capability | Score | Level | Top evidence | Supporting source(s) |
|---|---:|---|---|---|
| Cybersecurity & Resilience | 42.0 | strong_inference/60 | "Muscat International Airport unveils Wi-Fi 7 deployment" | developingtelecoms.com |
| Cloud & AI Infrastructure | 38.5 | strong_inference/55 | same | same |
| Enterprise Managed Services | 38.5 | strong_inference/55 | (different source, not inspected in depth — not implicated in the finding below) | — |
| Network & Connectivity | 38.5 | strong_inference/55 | same Wi-Fi 7 source | same |

This is a **different** source driving the fan-out than the one the original bug report was about — see §9 for why I did not treat this as either a clean pass or a confirmed new defect, and stopped to report it instead of guessing further.

## 7. Standard Chartered — before / after

**Before:** originally-reported "Digital Transformation Strategies" source no longer independently drove any opportunity (confirmed in the earlier live-validation phase, before this fix); 3 opportunities backed by specific Broadcom/VMware-partnership and GlobalData sources.

**After (fresh live crawl run today):** 3 opportunities again. The originally-problematic source didn't even appear in this crawl's 90 qualified sources (ordinary source-retrieval variance across runs — same pattern seen throughout this project). All 3 opportunities are backed by specific, dated, organization-attributed sources: Cybersecurity & Resilience (78.0/HIGH/direct_evidence 90 — businesschief.com, cybermagazine.com, technologymagazine.com, all about the Broadcom private-cloud partnership), Cloud & AI Infrastructure (61.0/MEDIUM/direct_evidence 80 — the same Broadcom article plus cio.inc and two LinkedIn posts), Enterprise Managed Services (42.0/WATCH/strong_inference 60). **No collective-scope or generic-trigger fan-out pattern present.** The originally-fixed defect remains fixed.

## 8. Siemens — result

Ran a third independent fresh live crawl (the first two were run in the prior live-validation phase, before this fix; this is the first crawl since 4a was implemented). Same 3 opportunities as both prior crawls: Cloud & AI Infrastructure (42.0/strong_inference 60, wiz.io), Cybersecurity & Resilience (42.0/strong_inference 60, backed by the same four real sources as the confirmed-good pattern from the original live-validation report: news.siemens.com, siemens.com/.../cybersecurity, siemensgovt.com/.../secure-cloud, wiz.io/customers/siemens), Enterprise Managed Services (42.0/strong_inference 60). **The existing legitimate cybersecurity evidence survives unchanged.** Network & Connectivity again does not appear in DECIDE and again shows no strong/direct RESEARCH-tab mapping — the third consecutive independent crawl with this same result, further supporting the earlier assessment that this is source-retrieval variance, not a gate regression. Per your instruction, I made no attempt to "restore" it.

## 9. Finding that needs a decision — not acted on, per the STOP condition

While inspecting Oman Airports's evidence chain (not just its opportunity count, per your instruction), I found something I want to flag precisely rather than either wave through or silently "fix."

The "Muscat International Airport unveils Wi-Fi 7 deployment" source (developingtelecoms.com, published 2025-11-17, 9 months old) produced `strong_inference`/60 for Network & Connectivity, Cloud & AI Infrastructure, and Cybersecurity & Resilience — a 3-way promotion from one source, superficially resembling the shape of the original bug.

I fetched the real article directly (with your permission prompt for the domain) to check whether this is legitimate. **The real article body does contain genuine, organization-specific, current material that plausibly justifies multiple capabilities**: "The initiative forms a core component of Oman Airports' broader digital transformation programme..." (Oman Airports named directly, not a collective) and "Beyond Wi-Fi 7, Oman Airports is progressing with several high-tech projects, including next-generation smart-security systems, modern visual technology platforms and advanced artificial intelligence applications" (Oman Airports directly named as the subject of both a security-relevant and an AI-relevant claim). On its face, this looks like a legitimate Case-A-style event: one organization, one article, several genuinely related capability angles.

However, two things left me short of confidently calling this "confirmed legitimate":

1. The RESEARCH tab's displayed "SOURCE SAYS" excerpt for the Network & Connectivity mapping is not a sentence — it's the article's own tag/category list ("Middle East North Africa Huawei Oman Wi-fi Video streaming Low Latency 5GHz band Digital Transformation Artificial Intelligence 6GHz Spectrum Wi-Fi Alliance 2.4GHz Airports Wi-Fi 7 AR"), confirmed by opening the real page directly — that exact string is the tag block at the top of the article, not narrative prose.
2. I reconstructed the real article's full visible text as faithfully as I could (title, tag list, full body, in the order the live page renders them) and ran it through the actual patched `map_source_to_capabilities()` in a controlled test. Result: **all four capabilities came back `weak_relevance`, not `strong_inference`** — I could not reproduce the live app's promotion using my best-effort reconstruction of the same real content.

That gap means one of two things, and I can't fully distinguish them without either raw pipeline access on your machine or the exported JSON report: (a) the pipeline's actually-fetched/stored `source.content` for this URL differs from what a human sees on the rendered page (e.g. it may include the tag list at a different position, duplicated boilerplate, a differently-extracted body) in a way that changes sentence-span boundaries or local corroboration — which would mean the tag list, or some other non-narrative fragment, may be contributing to the promotion after all; or (b) my reconstruction is simply incomplete in some other respect (spacing, hidden text, a paragraph the "main content" extraction dropped) and the live promotion is entirely legitimate, grounded in the genuine "Oman Airports is progressing with... smart-security systems... and advanced artificial intelligence applications" sentence I found in the real body.

I am treating this as exactly what your instruction anticipated — "a disconnected-occurrence false positive... or another attribution problem" surfacing during live validation — and stopping rather than guessing further or attempting another fix. **I have not changed anything in response to this finding.** It is a different source, and arguably a different mechanism (a tag-list excerpt-display anomaly plus an inconclusive reconstruction, not a confirmed collective-scope gap), from the original bug — it should not be read as evidence that 4a failed on the case it was built for (§6 shows the original source is cleanly fixed), but it is a real open question about a different source that deserves a decision before any further implementation.

## 10. Is the original Oman Airports false-positive class eliminated?

**Yes, for the exact confirmed case.** The "Digital transformation well on track" article — stale, government-wide, not Oman-Airports-specific — no longer independently produces any of the three capabilities it previously fanned out to; all four of its mappings are now correctly `weak_relevance`. This was verified against the real, live-crawled data (not a synthetic reproduction) in today's fresh Oman Airports run, and is additionally covered by 6 dedicated automated tests (§4, items 1-5 and 11) that will catch a regression.

The harder, same-sentence collective-enumeration variant I tested during design ("Oman Airports is among the government entities progressing digital transformation, which spans cloud, cybersecurity and managed services investments across the sector") is also fully closed by 4a alone — confirmed both in the design-phase reproduction and via test #2 above. This was the case I had originally thought might require 4b; it did not.

## 11. Residual false-positive class

The §9 finding is the one open item: a different, real, current, org-specific source (Wi-Fi 7 deployment) producing a 3-way promotion that is plausibly legitimate but has one unresolved inconsistency (excerpt-display tag-list anomaly + a reconstruction that undershoots the live result). This is NOT confirmed to be the same disconnected-occurrence/collective-scope mechanism 4a targets — it may simply be genuine multi-capability evidence about one organization, which is exactly what the system should produce. I'm not able to fully resolve which it is without deeper access than I have right now, so I'm reporting it rather than deciding it.

Two smaller, previously-flagged, out-of-scope items remain and were not touched this round (both pre-date this fix and are unrelated to it): the Enterprise Managed Services keyword ("digital transformation programme" is literally one of EMS's own catalogue keywords, so any sentence using that exact phrase tends to co-promote EMS alongside whichever capability the sentence is actually about); and the fact that `extract_signals()` only returns the first match per signal type source-wide (noted in §4, relevant to fixture design, not to production correctness in any case I found).

## 12. Is 4b still necessary?

**Based on everything tested so far, no.** Both reproduction cases 4b was specifically designed to close — the disconnected-occurrence fan-out (§2 of the design report) and the harder same-sentence collective-enumeration variant (§3) — are fully closed by 4a alone, confirmed by both synthetic tests and today's live Oman Airports crawl for the exact confirmed source. I'd recommend holding off on 4b unless the §9 finding, once resolved, turns out to be a genuine disconnected-occurrence case that 4a can't reach — which would be the first real evidence that 4b's additional plumbing and risk (noted in the design report, §14) is actually needed.

## 13. Is freshness still necessary?

**Not necessary to fix the confirmed defect.** The confirmed Oman Airports false positive was, as the design report anticipated, invalid on organization-specificity grounds — 4a alone eliminates it without needing to touch source age at all. I don't see a case from today's validation that specifically requires freshness as a fix (the §9 finding, if it turns out to be a real problem, is about attribution/excerpt-grounding, not staleness — the Wi-Fi 7 source is 9 months old, "aging" but not "old," and its own age isn't what's in question). I'd recommend continuing to hold off on the freshness architecture per your instruction, and revisiting only if a future finding specifically implicates source age rather than attribution.

---

## Summary

4a is implemented exactly as scoped: one new predicate, one new composition branch, no other architecture touched. 235/235 tests pass (218 baseline + 17 new, zero existing tests changed). Live validation against fresh crawls confirms the original, confirmed Oman Airports defect is eliminated, Standard Chartered's fan-out fix remains intact, and Siemens's legitimate cybersecurity evidence survives. One new, different finding from today's live validation (§9) is reported rather than acted on, per your stop condition — it needs your decision on how to proceed, not another automatic fix.

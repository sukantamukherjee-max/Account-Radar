# Oman Airports Fan-Out — Design & Investigation Report

**Status: design/investigation only. No code has been edited, no tests added to the repo, nothing committed. This report is for review before any implementation is authorized, per instruction.**

---

## 0. Summary

The fresh live-validation crawl surfaced a real, reproducible false-positive class: a single stale (2018), government-wide "digital transformation" article independently produced three `strong_inference`/60-confidence WATCH opportunities for Oman Airports (Cloud & AI Infrastructure, Cybersecurity & Resilience, Enterprise Managed Services), none of which are actually about Oman Airports doing anything specific.

Root cause: **Rule 4 of `map_source_to_capabilities()` treats "the organization is associated with the trigger term" and "the capability is locally corroborated near the trigger term" as independent existential checks over all occurrences of the trigger in the source text, with no requirement that a single occurrence — or even a single coherent claim — satisfy both.** A long, multi-topic article can satisfy each check from a different, unrelated sentence, producing a false composite.

I traced this to an exact, reproducible mechanism (§2), confirmed a stronger "same-sentence enumeration" variant that a naive same-occurrence fix would **not** catch (§3), and designed a narrow, composable fix that extends the existing `_span_disqualifies_evidence()` union point rather than rewriting Rule 4 or the locality primitives (§4). I also worked through freshness (§5), organization-specificity reuse (§6), interactions with the existing rule set (§7-§9), a 17-item adversarial test matrix (§10), a second live Siemens crawl confirming Network & Connectivity behaves consistently across two independent crawls (§11), and the UI auditability question (§12).

---

## 1. Exact Oman Airports root cause — the real data

From the live-validation crawl (`PHASE_2_LIVE_VALIDATION_REPORT.md`, §5), inspected directly via the DECIDE and RESEARCH tabs (not inferred):

**Source:** "Digital transformation well on track" — businesstimes.com.sg, Oman National Day feature, **published 2018-11-19 (7 years old at crawl time)**.

**Retained passage** (the exact text the pipeline kept, centered on its Network & Connectivity match): *"...ices have been digitised with a cloud project that aims to create a shared infrastructure network across the whole of government. Research about the impact of big data on the delivery of..."*

This is government-wide, not Oman-Airports-specific, language. Note the phrase **"across the whole of government"** — this is the exact aggregate-scope marker that the proposed fix in §4 targets, confirmed present in the real source rather than assumed.

Four capability mappings came out of this one source:

| Capability | Evidence level | Category | Confidence |
|---|---|---|---|
| Network & Connectivity | weak_relevance | incidental_mention | 27 |
| Cloud & AI Infrastructure | **strong_inference** | business_signal_inference | **60** |
| Cybersecurity & Resilience | **strong_inference** | business_signal_inference | **60** |
| Enterprise Managed Services | **strong_inference** | business_signal_inference | **60** |

The Network & Connectivity mapping — the one whose matched term ("network") is actually visible in the retained excerpt — was correctly downgraded to weak/incidental. The other three, whose capability-specific vocabulary must therefore live *elsewhere* in the full article text (not in the retained excerpt), were promoted to `strong_inference`/60 and, combined with one other source, independently produced full WATCH-tier opportunities (42.0 each).

This confirms the failure is not "capability vocabulary is absent" — it's that the vocabulary supporting Cloud/Cybersecurity/EMS is present somewhere in the article but disconnected from both the organization mention and the network-relevant excerpt actually shown.

## 2. Code path responsible, and reproduction

**File:** `commercial/evidence_mapping.py`, Rule 4 (~lines 1114-1198), "business-signal-backed generic trigger."

```python
usable_generic_hits = _usable_generic_hits(text, generic_hits, capability)
generic_org_associated = _is_organization_associated(
    text, usable_generic_hits, organization_name, radius=0, country=country
)
inference_signal = next(
    (
        s for s in signals
        if s.signal_type in _INFERENCE_QUALIFYING_SIGNAL_TYPES
        and _signal_locally_associated_with_trigger(text, usable_generic_hits, s)
    ),
    None,
)
if (
    usable_generic_hits and inference_signal is not None and trust_tier in ("high", "medium")
    and generic_org_associated and not is_customer_product_page
):
    level = EvidenceLevel.STRONG_INFERENCE
    ...
```

`usable_generic_hits` is the set of *all* regex match positions of the shared generic trigger ("digital transformation," etc.) anywhere in the text that survive `_usable_generic_hits()`'s ambiguity/local-support filter (itself an existential check — capability keywords found within a radius of *any* hit). `generic_org_associated` is computed once, over the *same set of hit positions*, via `_is_organization_associated(text, usable_generic_hits, organization_name, radius=0, ...)` — itself existential: true if the organization name appears within radius 0 of **any** hit. `inference_signal` is found similarly: the first qualifying signal whose text is locally associated with **any** hit.

None of these three checks are told *which* hit satisfied them. So the boolean composition `usable_generic_hits and inference_signal and generic_org_associated` is satisfied as soon as:
- hit #1 (early in the article) has the organization name within radius 0, and
- hit #2 (anywhere else in the article) has capability vocabulary + a qualifying signal within radius 1,

even though hit #1 and hit #2 are unrelated sentences about unrelated things.

I reproduced this exactly in `repro_fanout.py` (scratchpad, not committed) with a synthetic article: sentence A ("Oman Airports Management Company and several other state entities are advancing the digital transformation programme...") establishes `generic_org_associated`; three unrelated filler paragraphs; sentence B ("Separately, the wider digital transformation initiative is being expanded to include new cloud infrastructure, enhanced cybersecurity measures, and additional managed services capacity across government agencies") establishes capability corroboration + signal for all three capabilities. Output:

```
cloud_ai_infrastructure        strong_inference / business_signal_inference / 55.0 / signal=project
cybersecurity_resilience       strong_inference / business_signal_inference / 55.0 / signal=project
enterprise_managed_services    strong_inference / business_signal_inference / 55.0 / signal=technology_initiative
```

(Confidence differs slightly from the live 60 because of trust-tier/signal-type differences in the synthetic fixture — the mechanism match is what matters, not the exact number.) This reproduces the observed 3-way fan-out shape precisely and confirms the mechanism, independent of the exact real article text I could not fetch directly (`businesstimes.com.sg` is blocked to WebFetch; I did not attempt a workaround, per the web-content-restriction rule — the retained excerpt from the live report in §1 is the authoritative real evidence, and the repro is clearly labeled as a synthetic mechanism demonstration, not a claim about the real article's exact wording).

## 3. Why "same trigger occurrence" alone is not a sufficient fix

The obvious first fix is: require `generic_org_associated`, capability corroboration, and the qualifying signal to all be anchored to the **same occurrence** of the trigger (or occurrences within some small span of each other), instead of "any occurrence, independently."

I tested this hypothesis's ceiling *before* proposing it, using two additional synthetic cases (`repro_case_a_b.py`), run against the current, unmodified code:

**Case A — legitimate, same sentence:** *"Standard Chartered is pursuing a digital transformation programme and is strengthening its cybersecurity posture."* → Cybersecurity & Resilience promoted to `strong_inference`. Correct. (Unexpected secondary finding: Enterprise Managed Services was *also* promoted here, because "programme" is in EMS's own weak_signal_terms list and is trivially satisfied by the same sentence even though the sentence says nothing about managed services. This is a distinct, narrower bug — weak_signal_terms overlap across capabilities — flagged in §13, not fixed here.)

**Case B, hard same-sentence variant:** *"Oman Airports is among the government entities progressing digital transformation, which spans cloud, cybersecurity and managed services investments across the sector."* → **All three capabilities still promoted to `strong_inference`/55**, because org, trigger, capability vocabulary, and signal are all genuinely co-located in **one sentence**. A same-occurrence/same-sentence anchoring rule would not touch this case at all — it would consider the joint-locality condition trivially satisfied, since it is.

**Conclusion:** joint-occurrence locality is a real weakness (§2) but is **not sufficient by itself**. The harder problem is Case B: the organization is *genuinely, locally* mentioned in the same breath as capability-specific vocabulary, but only as one member of a **collective/aggregate scope** ("among the government entities," "across the sector") rather than as the actual subject of an organization-specific claim. Any fix has to separately detect this shape, regardless of how far apart the org mention and the capability vocabulary are.

## 4. Proposed minimal fix

Two composable, independent changes — both narrow, both additive, neither touching Rule 0-3/5 or rewriting the locality primitives.

### 4a. Collective/aggregate-scope span disqualifier (primary fix — addresses §2 and §3 together)

Add a new predicate, `_is_collective_scope_span()`, in `commercial/text_heuristics.py`, modeled exactly on the existing `is_low_trust_span()` / `looks_like_customer_requirement_span()` pattern already there. It detects a narrow set of aggregate/collective-scope markers **near the organization mention**, e.g.:

- "across the whole of government" / "across government" / "government-wide"
- "across the sector" / "sector-wide" / "industry-wide"
- "among the government entities" / "among state entities" / "several other government/state entities"

Compose it into the **existing** single union point, `_span_disqualifies_evidence()` (currently `is_low_trust_span(...) or looks_like_customer_requirement_span(...)`):

```python
def _span_disqualifies_evidence(span_text: str) -> bool:
    return (
        is_low_trust_span(span_text)
        or looks_like_customer_requirement_span(span_text)
        or _is_collective_scope_span(span_text)
    )
```

Because `_span_disqualifies_evidence()` is already called from all three locality primitives Rule 4 depends on — `_is_organization_associated()`, `_signal_locally_associated_with_trigger()`, and `_locally_supported_by_context_terms()` — this single change automatically strengthens all three simultaneously, without touching Rule 4's own composition logic, without touching Rules 0/1/2-3/5, and without hardcoding any organization or industry name. It disqualifies the *span around the organization mention* when that span reads as collective/aggregate rather than organization-specific — which directly invalidates `generic_org_associated` for Case B (§3) and, independently, for the disconnected §2 case where the org-adjacent span never had capability content near it in the first place (already correctly handled today, unaffected).

This does **not** fully close §2's disconnected-occurrence weakness on its own — it only disqualifies collective-scope org mentions. A source where the organization is mentioned specifically and genuinely (no collective-scope language) but the capability vocabulary lives in an unrelated, disconnected sentence elsewhere in a long multi-topic article would still pass under 4a alone. That residual gap is addressed by 4b.

### 4b. Bounded joint-locality requirement (secondary, narrows the residual gap from §2)

Independently of 4a, tighten `_is_organization_associated()`'s role in Rule 4 specifically (not Rules 2/3, which use it differently and are out of scope here): instead of "any usable generic hit has the org within radius 0," require that **at least one** usable generic hit that also participates in capability corroboration (i.e., is the specific hit that made `_locally_supported_by_context_terms()` or `_signal_locally_associated_with_trigger()` true) has the org within radius 0 *of that same hit*. Concretely, this means passing the specific matched hit position through instead of the whole `usable_generic_hits` set at the final gating step, and checking org-association per-hit rather than per-text.

This is a narrower, more surgical version of the "same occurrence" idea from §3 — narrower because it only needs to anchor org-association to the *specific* hit already used for capability/signal corroboration (which the code already isolates internally), not to invent a new global "same sentence" concept. Combined with 4a, this closes both the disconnected-occurrence case (§2) and the collective-scope-but-colocated case (§3): 4a disqualifies the collective-scope org mention itself; 4b prevents an org-specific mention from "lending" its association to a capability corroboration event happening in a wholly unrelated part of the article.

**Recommendation:** implement 4a first (smaller, lower-risk, directly maps to the real Oman Airports excerpt's own language) and evaluate against the test matrix in §10 before deciding whether 4b is still needed — it's possible 4a alone, plus the freshness dampener in §5, brings acceptable behavior, and 4b carries more plumbing risk (touching per-hit data flow) for a case (fully disconnected occurrences with a genuinely org-specific, non-collective org mention) that may be rarer in practice than Case B.

## 5. Freshness strategy

**Not a global rejection rule.** The user's Case D (old source with a genuinely current signal in the text) must not be penalized, and `research_recency()` already exists as a pure, reusable function (`ui/data.py`, lines ~140-187) — currently wired only to UI display, disconnected from the evidence/opportunity engine.

**Proposed design:**

1. Relocate the pure banding logic (age→freshness_band/freshness_factor computation) out of `ui/data.py` into a neutral shared module (e.g. `commercial/freshness.py`, or into `research/sources.py` next to where `published_at` is already parsed) so both `ui/data.py` (display) and `commercial/evidence_mapping.py` (scoring) import the same function — avoiding a duplicate age-computation implementation, consistent with the "reuse machinery" principle used elsewhere in this design.
2. In `evidence_mapping.py`, on the Rule 4 (`business_signal_inference`) path specifically — not Rules 0/1/2-3/5, which already carry their own trust/directness signals — use `source.extra["published_at"]` (already available at this point, confirmed in `research/sources.py`) to compute a freshness factor and apply it as a **confidence dampener**, not an evidence-level gate: `confidence = base_confidence * freshness_factor` (or an additive penalty capped so it cannot push evidence below `weak_relevance`'s floor by itself).
3. Freshness must be a **prior, not an override**: it should only dampen confidence for the generic-trigger/business-signal-inference path (where staleness is corroborating evidence of "this is describing a historical/ambient state, not a current one"). It should explicitly **not** apply to, or should be overridden by, evidence that the extracted signal itself is described in present/future/current tense or tied to a dated recent event — this is already partially implicit in `_INFERENCE_QUALIFYING_SIGNAL_TYPES` and the signal-extraction machinery's own date handling; freshness should compose with that, not replace it. This preserves Case D.
4. **Important limitation to flag explicitly:** confidence dampening alone does **not** prevent a false opportunity, because `opportunities.py::_independently_qualifying()` currently excludes only the `EXISTING_CAPABILITY_CONTEXT` category — it does not gate on confidence at all. So freshness must be treated as a **secondary, complementary measure** layered on top of the primary structural fix (§4), not a substitute for it. On its own, freshness would make the Oman Airports opportunities score lower (e.g. 42.0 → high-20s/low-30s) but would very likely still cross the WATCH threshold, and would do nothing for a fresh, same-day version of the same collective-scope-language problem (Case B, §3, is not stale — it would defeat any freshness-only fix entirely, and is real).

**Trade-off:** Applying freshness only to Rule 4 (not universally) means an old, non-org-specific source that reaches STRONG_INFERENCE via correct joint-locality (post-fix) would still get some confidence dampening even though it's now legitimately org-specific and capability-specific — this is intentional and mild (a same-year-old, org-specific "Siemens deployed X in 2019" statement is weaker current-opportunity evidence than a same-week announcement, even when it's genuinely about Siemens), and is exactly Case C's stated concern (stale account-specific context should not automatically read as a current 2026 opportunity). This is the desired behavior, not a side effect to correct.

## 6. Organization-specificity — reuse, not duplication

Confirmed there is exactly **one** organization-association detection mechanism in the codebase: `_org_core_tokens()` → `_mentions_organization()` → `_is_organization_associated()`, used consistently by both Rule 2/3 and Rule 4. No second/duplicate detector exists, and none is proposed. Both fixes in §4 compose into this existing machinery (`_span_disqualifies_evidence()`, called from `_is_organization_associated()` itself) rather than building a parallel one — directly satisfying the user's stated preference to reuse/strengthen/compose existing locality machinery rather than create a duplicate organization detector.

The desired joint condition — `organization-specific + capability-specific + current/change → stronger support`, `generic government/industry statement → context only/weak relevance even with capability vocabulary present` — falls out of composing 4a (disqualifies collective-scope org spans, so a "generic government/industry statement" can no longer satisfy `generic_org_associated` at all) with the pre-existing Current/Change/Requirement Gate (already requires a qualifying signal type) and, optionally, 4b (anchors capability corroboration to the same org-specific hit). No new "organization specificity score" or parallel scoring system is needed.

## 7. Interaction with existing Rule 0-5

- **Rule 0/1 (direct evidence, explicit named-entity mentions):** unaffected. These rules don't go through `usable_generic_hits`/Rule 4's shared-trigger path at all.
- **Rule 2/3:** these already call `_is_organization_associated()` and, transitively, `_span_disqualifies_evidence()`. 4a will apply to them too — this is a feature, not a side effect: a Rule 2/3 mapping built on a collective-scope org mention ("Oman Airports, among other government entities, ...") has exactly the same false-attribution risk and should be disqualified the same way. This needs explicit test coverage (§10, item 12-13 area) to confirm Rule 2/3's existing legitimate cases aren't affected — I expect no regression since Rule 2/3's typical cases involve explicit, singular organization-subject sentences, not collective enumerations, but this must be verified against the 218-test baseline (§14).
- **Rule 4/5:** Rule 4 is the primary target (§2-§4). Rule 5, if it has independent locality logic, should be audited during implementation for the same "independent existential checks" pattern — out of scope to fully trace in this design pass but flagged as a follow-up check before implementation is finalized.

## 8. Interaction with the Current/Change/Requirement Gate

The existing gate (`_INFERENCE_QUALIFYING_SIGNAL_TYPES`, requiring the signal to be `expansion`/`project`/`technology_initiative`/`capacity_change`/`procurement_activity`) already correctly filters out the case the user originally reported (bare capability-mention language with no signal at all does not qualify). The Oman Airports fan-out is a **different, orthogonal failure**: the signal-type gate is satisfied because a genuine `technology_initiative`/`project`-type signal *does* exist in the text (in sentence B of the reconstructed mechanism, or in the real article's undisclosed capability-adjacent sentence) — it's just not about Oman Airports specifically. The gate correctly answers "is there a change/current signal at all?" but has no mechanism to answer "is that signal attributable to the researched organization, or to a collective it happens to be a member of?" — that's precisely what §4's fix adds. The two mechanisms are complementary, not overlapping: the signal-type gate stays untouched.

## 9. Interaction with the generic-trigger fan-out fix (this session's earlier work)

This design directly extends, rather than duplicates or weakens, the earlier Current/Change/Requirement Signal Gate work. That gate closed the "bare mention, no signal" fan-out (confirmed fixed for Standard Chartered, §1 of the live-validation report). This design closes a second, narrower fan-out mode that survives the gate: shared-trigger + genuine signal + genuine capability vocabulary, but attributed to the wrong (collective, not organization-specific) subject. Both fixes live in the same rule (Rule 4) and the same composition point (`_span_disqualifies_evidence()`), so they stack cleanly rather than requiring separate code paths.

## 10. Adversarial test matrix (17 cases — design only, not implemented)

All cases assume Rule 4's shared-trigger path unless noted. "Expected (post-fix)" reflects §4a+4b combined; where 4a alone differs, that's noted.

| # | Case | Text shape | Expected evidence level | Expected category | Expected opportunity behavior | Reason |
|---|---|---|---|---|---|---|
| 1 | Stale government-wide article | 2018 article, "across the whole of government," org named once, capability vocabulary elsewhere in article | weak_relevance (org-adjacent span disqualified) | incidental_mention or existing_capability_context | Does not independently qualify | 4a disqualifies the collective-scope org span; freshness (§5) further dampens if it somehow still qualified |
| 2 | Stale industry-wide article | "across the banking sector," 5-year-old, org named once among peers | weak_relevance | incidental_mention | Does not independently qualify | Same as #1, industry variant of the collective-scope marker list |
| 3 | Current organization-specific article | This-week article, org is sole subject, explicit capability + signal in same sentence | strong_inference or direct_evidence | business_signal_inference | Qualifies, full confidence (no freshness dampening) | No collective-scope language; org genuinely and solely associated |
| 4 | Current government programme explicitly involving the org | "The Ministry named Oman Airports as the lead agency for the new cloud migration programme" | strong_inference (arguably direct_evidence, org is explicitly singled out, not merely enumerated) | business_signal_inference or direct_evidence | Qualifies | Org is explicitly singled out by name for a specific role, not merely listed among many — 4a's marker list must not match this shape (explicit naming, not aggregate enumeration) |
| 5 | Old organization-specific capability description | "Siemens implemented a cybersecurity platform in 2019" | weak_relevance / existing_capability_context (unaffected by this fix — this is what the pre-existing gate already handles) | existing_capability_context | Does not independently qualify (no qualifying signal type — it's a completed past event) | No new mechanism needed; existing signal-type gate already excludes completed-only descriptions |
| 6 | Old organization-specific completed project | "Siemens completed its network upgrade in 2018" | weak_relevance / existing_capability_context | existing_capability_context | Does not independently qualify | Same as #5 |
| 7 | Old article + new/current event in same passage (Case D) | 2019 article body, but one sentence: "Siemens announced this month it is expanding..." | strong_inference, minimal/no freshness dampening | business_signal_inference | Qualifies | The current/change signal in-text should override the article-level freshness prior (§5 point 3) |
| 8 | Generic trigger + cloud-specific evidence, disconnected | Org+trigger sentence A; unrelated filler; cloud vocabulary + signal in sentence B elsewhere | weak_relevance (post 4a+4b) / strong_inference (4a alone, if org span wasn't collective-scope) | incidental_mention (post-fix) | Does not independently qualify post-4b | This is §2's exact reproduced mechanism; needs 4b to fully close, not just 4a |
| 9 | Generic trigger + cybersecurity-specific evidence, disconnected | Same shape as #8, cybersecurity vocabulary | Same as #8 | Same as #8 | Same as #8 | Parity check across capabilities |
| 10 | Generic trigger + managed-services-specific evidence, disconnected | Same shape as #8, EMS vocabulary | Same as #8 | Same as #8 | Same as #8 | Parity check; also probe the weak_signal_terms overlap noted in §3 (EMS's "programme" term) |
| 11 | Shared trigger + capability word in unrelated neighboring sentence | Org+trigger sentence, next sentence (topically unrelated) happens to contain a capability word with no signal | weak_relevance | incidental_mention | Does not qualify (no qualifying signal present at all) | Existing signal-type gate already covers this; regression check only |
| 12 | Organization name far from capability statement (no collective-scope language, no signal issue) | Org named in paragraph 1; genuine current capability+signal statement about the SAME org in paragraph 3, both clearly about the org, just far apart structurally (e.g., a well-written single-subject profile article) | strong_inference | business_signal_inference | Qualifies | Must confirm 4b's per-hit anchoring doesn't over-tighten and break legitimate long-form single-subject articles — false-negative risk to explicitly test (§13) |
| 13 | Organization name in same sentence as genuine change signal | "Oman Airports announced a new cloud migration initiative this quarter" | direct_evidence or strong_inference | business_signal_inference or direct | Qualifies, full confidence | Baseline positive control, should be untouched by this fix |
| 14 | Capability-specific current initiative, no generic trigger at all | "Oman Airports is deploying a new SD-WAN network across its terminals" (no "digital transformation" trigger phrase anywhere) | direct_evidence (via Rule 0/1/2-3, not Rule 4 at all) | direct or organizational_technology_event | Qualifies | Confirms this fix doesn't touch non-Rule-4 paths; regression check |
| 15 | Strong Siemens-style positive control | Real shape from live crawl: "Siemens Advancing Government: Embracing Secure Cloud Services," org-specific, capability-specific, current | strong_inference / direct_evidence per current behavior | business_signal_inference | Qualifies, unaffected | Must not regress the confirmed-good Standard Chartered/Siemens Cybersecurity cases from the live validation |
| 16 | Collective-scope language present but org has its own separate, specific clause | "Standard Chartered, along with several other banks, also announced its own new AI-driven fraud detection platform" | strong_inference | business_signal_inference | Qualifies | Explicit false-negative guard: 4a's marker list (§4a) must be narrow enough (government/sector/industry-WIDE scope words) that "along with several other banks" does NOT match, since the org still makes its own specific claim — this is the trade-off flagged in the earlier problem-solving notes and must be a named regression test |
| 17 | Enumeration marker adjacent to org, but capability content is about a DIFFERENT, unrelated org in the list | "Oman Airports, Muscat Municipality, and the Ministry of Health are each pursuing digital initiatives; the Ministry of Health's new telehealth platform is..." | weak_relevance for Oman Airports | incidental_mention | Does not qualify for Oman Airports | Tests that collective-scope disqualification correctly withholds credit from the wrong list member, not just generically dampens all members |

Tests 1, 2, 8, 9, 10, 17 are expected to change behavior (currently false-positive, should become weak/non-qualifying post-fix). Tests 3, 4, 5, 6, 7, 11, 12, 13, 14, 15, 16 are regression/parity guards that must continue to pass unchanged (some, like 5/6/11/14, already pass today and must keep passing; others, like 3/4/7/12/13/15/16, are the false-negative risk set that most needs real fixture text drawn from or modeled closely on the confirmed-good Standard Chartered/Siemens live-crawl cases before implementation, not just synthetic sentences).

## 11. Siemens second live crawl (Part 7)

Ran one additional, genuinely fresh Siemens crawl today (2026-09-12) via the live Streamlit app, code completely unchanged since the first crawl. Confirmed via the browser: sidebar "Analyze account" re-triggered with Organization="Siemens", Country blank (matching the first crawl's parameters); report header again showed "Source: live research."

**Result: DECIDE tab shows the same 3 opportunities as the first crawl** — Cloud & AI Infrastructure (42.0, WATCH), Cybersecurity & Resilience (42.0, WATCH), Enterprise Managed Services (42.0, WATCH). Network & Connectivity again does **not** appear as an opportunity.

Checked the RESEARCH tab specifically for any strong/direct Network & Connectivity mapping (`find("Network & Connectivity — Strong")` / `find("Network & Connectivity — Direct")`): **zero matches**, same as the first crawl. What *does* exist this time: 6 `weak_relevance` rows and 12 `incidental_mention` rows for Network & Connectivity (versus the first crawl, where per the live-validation report it found "no `strong_inference`/`direct_evidence` Network & Connectivity mapping anywhere... only `weak_relevance`/`incidental_mention`" as well, though I did not capture exact counts in that pass).

**Assessment: this is source-retrieval variance, not a code regression.** Across two independent live crawls, the deterministic evidence-mapping code behaved identically in kind — it never promotes Network & Connectivity past weak/incidental for Siemens with whatever sources happen to be retrieved that run — while the *specific set* of retrieved sources and the exact weak/incidental counts differ slightly between runs (a property of live web search returning different or differently-ranked results each time, not of the evidence-mapping logic itself, which is unchanged and was not touched between the two crawls). I found no evidence of a strong/direct Network & Connectivity source being retrieved and then suppressed by the gate — the gate has nothing to suppress here, because no source in either crawl reached the corroboration bar for Network & Connectivity to begin with. Per instruction, I have **not** attempted to "fix" this to restore the original opportunity count; the finding is that the original count (from whatever baseline crawl first surfaced Network & Connectivity as a genuine opportunity) reflects a source that these two live crawls simply did not retrieve, not a logic defect in the current hardened code.

## 12. UI auditability (Part 8)

Confirmed (code reading, not UI interaction) that the underlying data model is fully auditable programmatically, even though the UI is not:

- `report.py::chain_view()` returns one row per `CapabilityMapping.to_dict()` in `report["chain"]` — every individual capability mapping (source_url, matched_terms, supporting_context, evidence_category, evidence_level, confidence, signal_type, inference_cluster_id, epistemic_status) is present in the raw report JSON.
- The RESEARCH tab's apparent "one row per source" limitation is a pure UI-layer artifact: `ui/data.py::research_items()` deduplicates via `key = ("chain", url, title)` (missing `capability_id`), so when one source maps to multiple capabilities, only one survives display — but the dropped rows remain present both in the raw report JSON and inside each surviving item's own `"original"` field.
- **No data is lost; only display is incomplete.** Any of the §10 test cases, or a full independent audit of every capability mapping for a given account, can already be done today by reading `report["chain"]` directly (e.g. via the exported JSON report) without any UI change.

Not fixing this now, per instruction. Flagging as a good candidate for a small, separate future UI change: add `capability_id` to the RESEARCH tab's dedup key so every mapping for a source is visible, and/or surface the exact matched span per capability (not just per source) so a reviewer doesn't need to fall back to raw JSON. Out of scope for this design pass.

## 13. False-positive risks (of the proposed fix itself)

- **4a's marker list is a judgment call.** Too broad, and it starts disqualifying legitimate enumerations (test #16). Too narrow, and real-world phrasings the drafted list doesn't anticipate (e.g. "nationwide," "across all ministries," "government agencies broadly") slip through. This needs a real, reviewed word list, not just the three example phrases in §4a, and should probably be treated as an allow-list-style narrow set that's easy to extend later rather than an attempt at completeness on day one.
- **The EMS/weak_signal_terms overlap noted in §3** ("programme" satisfying Enterprise Managed Services's own corroboration from an unrelated Cybersecurity sentence) is a separate, real false-positive source that this design does not fix. It would still let EMS over-qualify in cases where 4a/4b don't apply (i.e., genuinely organization-specific, non-collective, non-stale sentences that happen to contain a generic word like "programme"/"initiative" that's in EMS's weak-signal list). Recommend a follow-up, narrower pass on capability-specific weak_signal_terms lists to remove overly generic words that overlap across capabilities — out of scope here but should be tracked.
- **4b's per-hit plumbing** is more invasive than 4a and risks subtly changing which hit is "the" hit used elsewhere in Rule 4's confidence/category selection logic if not implemented carefully — recommend implementing and testing 4a in isolation first (§4, recommendation) before deciding whether 4b's complexity is justified by the residual gap it closes.

## 14. False-negative risks (of the proposed fix itself)

- Test #12 (organization genuinely the sole subject of a long-form article, but capability statement structurally far from the org mention) is the main false-negative risk for 4b specifically — legitimate single-subject profile pieces could have their capability corroboration anchored to a different paragraph's mention of the org than the one nearest the capability text, and a naive per-hit anchoring could fail this if not given enough radius/tolerance. Needs explicit fixture-based testing before implementation, not just reasoning.
- Test #16 (collective-scope marker present, but org still makes its own specific claim) is the main false-negative risk for 4a — an overly broad marker regex would wrongly disqualify organization-specific evidence merely because it appears near enumeration language. This is the most important single regression test to write before implementation, since it directly tests the trade-off named in the original design brief.
- Freshness dampening (§5), if miscalibrated, risks under-scoring genuinely current opportunities that happen to cite one older contextual source alongside newer ones — mitigated by applying freshness per-source/per-mapping rather than account-wide, and by the Case D override, but worth an explicit test combining one fresh and one stale source for the same capability.

## 15. Exact files that would need changing (implementation not yet authorized)

| File | Change |
|---|---|
| `commercial/text_heuristics.py` | Add `_is_collective_scope_span()` (or equivalent name); compose into `_span_disqualifies_evidence()` (4a) |
| `commercial/evidence_mapping.py` | If 4b is implemented: adjust Rule 4's org-association check to anchor to the specific corroborating hit rather than any usable hit; add freshness-based confidence dampening on the Rule 4 path (§5), reading `source.extra["published_at"]` |
| `commercial/freshness.py` (new) or `research/sources.py` | Relocate/expose the pure freshness-banding logic currently only in `ui/data.py::research_recency()`, so it can be imported by `evidence_mapping.py` without duplicating it |
| `ui/data.py` | Update `research_recency()`'s caller (or its own definition) to import from the new shared location instead of owning the logic, if relocated; no behavior change to the UI itself |
| `tests/test_evidence_relevance_hardening.py` | Add the 17 adversarial cases from §10 (and any additional fixture-based variants of #12/#16 identified during implementation) |
| `commercial/models.py` | Only if freshness needs to be visible on `CapabilityMapping` itself (e.g. for future UI display of "why was this dampened") — not required for the confidence-only approach in §5; flag as optional/deferred |

No changes anticipated to `commercial/opportunities.py`, `commercial/signals.py`, `report.py`, or the Streamlit `app.py` UI layer for this fix.

## 16. Expected impact on the 218-test baseline

All 218 existing tests are expected to continue passing, because:
- 4a only *adds* a new disqualification condition to `_span_disqualifies_evidence()`; it cannot cause a previously-disqualified span to become non-disqualified, so any existing test asserting a span IS disqualified is unaffected, and only tests whose fixture text happens to contain one of the new collective-scope markers near an organization mention could newly disqualify — a quick grep of existing test fixtures for phrases like "across the whole of," "sector-wide," "government-wide," "among the," "several other" before implementation is recommended to catch this in advance rather than discovering it via a failing run.
- 5's freshness dampening only reduces confidence on the Rule 4 path; if any existing test asserts an exact confidence value for a `business_signal_inference` mapping built from a source with a `published_at` in `source.extra`, that exact number could shift — same pre-implementation grep recommended (search existing fixtures for Rule-4-shaped tests that set `published_at`).
- 4b (if implemented) is the highest-risk change for the baseline, since it alters which hit within `usable_generic_hits` is treated as authoritative — recommend implementing and running the full suite after 4a alone first, then again after 4b, to isolate which change (if any) causes a baseline regression.

No baseline test is expected to newly *fail to find* a promotion it currently correctly asserts (the changes only add a disqualification path and a confidence dampener, never remove an existing positive-qualification path), but exact-confidence-value assertions are the specific, named risk to check before merging.

---

## Appendix: scope discipline confirmation

Per the binding constraints for this phase, this report does not: create organization-specific rules, hardcode "Oman Airports," hardcode "aviation," propose a global source-age rejection rule, propose a global government-source rejection rule, redesign the evidence architecture, weaken Rule 0-5's existing behavior, or touch the UI. The proposed fixes (§4) are both narrow additions to the existing, single composition point (`_span_disqualifies_evidence()`) already shared by all three locality primitives Rule 4 depends on, and the freshness strategy (§5) is scoped to a confidence dampener on one specific evidence category, not a blanket rule. No files have been edited; no tests have been added to the repository; nothing has been committed.

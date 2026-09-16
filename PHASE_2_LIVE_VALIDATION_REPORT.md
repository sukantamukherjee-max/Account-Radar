# Current/Change/Requirement Signal Gate — Fresh Live Validation Report

**Do not treat this report as "validation succeeded." It documents a genuine fresh live crawl, and that crawl surfaced a real, reproducible defect. No code has been modified — per instruction, I stopped and am reporting first.**

---

## 1. Working tree confirmation

Authoritative tree used for this validation:

```
C:\Users\smukherjee\OneDrive - omandatapark.com\Desktop\Account Rader\account_radar_phase3_research_v3_3_evidence_relevance\account_radar_phase2_ai_reliability_fix
```

I cannot directly inspect the running Streamlit process's working directory from this session (no shell/process access to the device). To verify the running `localhost:8501` server is actually serving from this tree rather than one of the other three folders connected to this session (`Account-Radar`, `Account-Radar-Fresh`, and a second, older `account_radar_phase2_ai_reliability_fix` folder that sits *outside* the `v3_3_evidence_relevance` wrapper), I fingerprinted the candidates by content instead of trusting the path:

- `Account-Radar` and `Account-Radar-Fresh` are monolithic single-file legacy apps (a 280KB+ `app.py`, no `commercial/`/`research/`/`ui/` packages, no WATCH/UNDERSTAND/DECIDE/ACT/RESEARCH terminology anywhere). They cannot produce the UI I drove.
- The older, non-wrapped `account_radar_phase2_ai_reliability_fix` folder *does* share the modular architecture, but its `app.py` (19,039 bytes) contains **zero** occurrences of "Search intelligence", "Review status", or "Validate" — strings that are all over the live RESEARCH tab I actually used. Its `commercial/opportunities.py` is 10,776 bytes (pre-hardening; no `_independently_qualifying`), and its `ui/data.py` (5,432 bytes) has no `existing_capability_context`/`customer_requirement_context` labels.
- The authoritative nested folder's `app.py` contains those RESEARCH-tab strings 5 times; its `commercial/opportunities.py` is 13,838 bytes and matches the hardened version I wrote this session (`_independently_qualifying`, the `EXISTING_CAPABILITY_CONTEXT` gate comments, etc.), with `commercial/*.py` and `ui/data.py` mtimes all in the same recent batch as my session's edits.

By elimination, the authoritative nested tree is the only one of the four connected folders structurally capable of producing the UI I interacted with. I cannot fully rule out a fifth, unconnected copy running the server, but this is the strongest verification available without shell/process access, and I flag the residual uncertainty rather than asserting certainty I don't have.

**218/218 tests pass** on this tree, reconfirmed just now:

```
218 passed in 3.47s
```

## 2. Fresh live-run status

All three runs were genuine fresh live crawls through the normal Streamlit UI (Organization + Country fields, "Analyze account" button), **not** a `phase1d_report.json` replay. Confirmed by: each report header reads "Source: live research"; qualified-source, signal, and capability-mapping counts differ from the stored `phase1d_report.json` baseline (which I never uploaded or loaded this session); and the actual source URLs surfaced (e.g. a Broadcom/VMware partnership article published 57 days ago) don't exist in the stored report.

One caveat worth surfacing: Standard Chartered's and Siemens's report headers both show **"AI: degraded"** (Oman Airports showed "AI: ok"). I don't know the specific cause from the UI alone — it may affect AI-assisted synthesis/ranking layered on top of the deterministic evidence-mapping and opportunity engine that this hardening pass actually touches, but it's a variable I can't fully control for and want to be explicit about.

## 3. Opportunity count by account

| Account | Opportunities (this run) |
|---|---|
| Oman Airports | 4 |
| Standard Chartered | 3 |
| Siemens | 3 |

## 4. Before/after comparison

| Account | Previous | New | Change | Explanation |
|---|---:|---:|---:|---|
| Oman Airports | 0 | 4 | +4 | New: Cloud & AI Infrastructure (42.0), Cybersecurity & Resilience (42.0), Enterprise Managed Services (42.0) — all `strong_inference`/60 confidence, all traced to the **same two sources**, one of which is a 7-year-old (2018), government-wide (not Oman-Airports-specific) "digital transformation" puff piece. This is a **regression of the exact failure mode the gate was built to prevent** — see §9. Network & Connectivity (38.5) is separately and legitimately supported by a real, specific "Muscat International Airport unveils Wi-Fi 7 deployment" article — this one looks correct. |
| Standard Chartered | 3 | 3 | 0 (composition changed) | The **originally-reported** "digital transformation" fan-out source ("Standard Chartered Plc: Digital Transformation Strategies") no longer independently drives any opportunity — it does not appear in any of the 3 opportunities' supporting-source lists. All 3 current opportunities (Cybersecurity & Resilience 61.0/MEDIUM/`direct_evidence` 80, Cloud & AI Infrastructure 42.0/`strong_inference` 60, Enterprise Managed Services 42.0/`strong_inference` 60) are instead backed by specific, dated, organization-attributed sources: a "Standard Chartered and Broadcom Create AI Cloud Partnership" article (57 days old), a GlobalData enterprise-tech analysis, and a LinkedIn "2025 Technology & Digital Security Centre" post. This is the intended, desirable outcome for the case the user originally flagged. |
| Siemens | 2 | 3 | +1, and a swap | Cybersecurity & Resilience (42.0, `strong_inference` 60, 4 independent domains including `siemens.com/.../cybersecurity`, `siemensgovt.com/.../secure-cloud`, `wiz.io/customers/siemens`) **survived**, as required. Cloud & AI Infrastructure (42.0) and Enterprise Managed Services (42.0) are **new**. **Network & Connectivity — previously flagged as genuinely supported — did not reappear**; I confirmed there is no `strong_inference`/`direct_evidence` Network & Connectivity mapping anywhere in this crawl's 62 RESEARCH items, only `weak_relevance`/`incidental_mention`. My best assessment (§10) is that this is fresh-crawl source variance rather than a gate regression, but I can't fully rule out gate involvement without the original audit's raw passage data, which I don't have. |

I am **not** treating the Oman Airports 0→4 change as "the gate got more permissive and that's bad by default," nor the Siemens Network & Connectivity drop as automatically fine because "count-wise it's still net positive." Both are assessed on the evidence below, not the count.

## 5. Oman Airports — capability-only negative control

I could not find the user's two illustrative example sentences verbatim in this crawl (they were illustrative, not confirmed to exist on Oman Airports's actual indexed content), so I can't report a direct hit/miss against those exact strings. What I *did* find is arguably a more serious instance of the same underlying problem:

**Source:** "Digital transformation well on track" (businesstimes.com.sg, Oman National Day feature, **published 2018-11-19 — 7 years old**)

**Passage retained by the pipeline** (excerpt centered on the Network & Connectivity match): *"...ices have been digitised with a cloud project that aims to create a shared infrastructure network across the whole of government. Research about the impact of big data on the delivery of..."*

This passage is about **Oman's government-wide** digital transformation, not Oman Airports specifically, and it is stale. The pipeline correctly downgrades it to `weak_relevance`/`incidental_mention` for Network & Connectivity (confidence 27, matched term "network"). But the **same source independently produced three additional mappings** that I could only see via the DECIDE tab (the RESEARCH tab collapses a source to one representative row and hides the rest — see the UI note in §11):

| Capability | Evidence level | Category | Confidence |
|---|---|---|---|
| Network & Connectivity | weak_relevance | incidental_mention | 27 |
| Cloud & AI Infrastructure | **strong_inference** | business_signal_inference | **60** |
| Cybersecurity & Resilience | **strong_inference** | business_signal_inference | **60** |
| Enterprise Managed Services | **strong_inference** | business_signal_inference | **60** |

All three `strong_inference` mappings, together with one other source, independently produced full opportunities (Cloud & AI 42.0, Cybersecurity 42.0, EMS 42.0 — all WATCH tier). This is not organization-specific evidence of Oman Airports doing anything; it's a stale, generic, government-level article satisfying the local-corroboration bar for three unrelated capabilities from one passage. It does **not** independently create a cybersecurity opportunity from bare capability-mention language (the user's literal ask), but it does independently create three opportunities from language that isn't really about Oman Airports's current/change state at all — which is the same underlying evidence-relevance failure the whole hardening effort has been about.

## 6. Standard Chartered — fan-out result

**Result: the originally-reported fan-out appears fixed.**

The source that caused the original problem, "Standard Chartered Plc: Digital Transformation Strategies," still individually scores `strong_inference`/60 for Cloud & AI Infrastructure alone (visible in the RESEARCH tab) — but it does **not** appear in the supporting-source list for either the Cybersecurity & Resilience or the Enterprise Managed Services opportunity. Those two capabilities are instead backed by different, more specific sources:

- **Cybersecurity & Resilience** (61.0, MEDIUM, `direct_evidence` 80/100): "Standard Chartered and Broadcom Create AI Cloud Partnership" (businesschief.com, published 2026-07-17, 57 days old) + a GlobalData enterprise-tech analysis. Passage excerpt (from the Network-tagged view of the same source, since I couldn't reach the Cybersecurity-tagged excerpt directly through the UI): *"...ment may also strengthen the bank's capacity to deliver services across its international network, which spans multiple regulatory jurisdictions with distinct compliance frameworks..."* — this is a real, dated, organization-specific article about an actual Broadcom/VMware partnership; "Current-state context: VMware" in the opportunity card is consistent with that. I was not able to pull the exact Cybersecurity-specific excerpt (see UI limitation in §11), so I can't independently confirm the 80/100 direct_evidence classification is textually justified versus the Cloud/Network view I could read — flagging that as an open item rather than asserting it's fully verified.
- **Cloud & AI Infrastructure** (42.0, `strong_inference` 60): same Broadcom article + same GlobalData report + a LinkedIn "Standard Chartered 2025 Technology, Digital Transformation & Security Centre" post.
- **Enterprise Managed Services** (42.0, `strong_inference` 60): GlobalData report + the same LinkedIn post.

This satisfies the user's requirement: Cybersecurity and EMS did **not** survive merely because "digital transformation" is a shared generic trigger; where they do have opportunities, it's backed by specific, capability-relevant, dated sources.

## 7. Siemens — positive-control result

**Cybersecurity & Resilience survived**, as required: 42.0, WATCH, `strong_inference` 60/100, 4 independent source domains (`news.siemens.com` critical-infrastructure-investment piece, `siemens.com/.../digital-transformation/cybersecurity`, `siemensgovt.com/.../secure-cloud`, `wiz.io/customers/siemens`).

**Network & Connectivity did not survive.** I confirmed via `find()` across the full RESEARCH tab (62 items) that there is no `strong_inference` or `direct_evidence` Network & Connectivity mapping anywhere in this crawl — every Network & Connectivity item is `weak_relevance`/`incidental_mention`. This is a genuine discrepancy from the "previous" baseline, which the user said had Network & Connectivity as genuinely supported. I was not able to obtain the original audit's specific source/passage for that prior Network & Connectivity opportunity to compare directly, so I can't definitively attribute this to the gate versus ordinary source variance between two different live crawls (different search results, different day, "AI: degraded" flag). I'm reporting it as an open question rather than concluding either way.

## 8. Evidence-level inspection: `EXISTING_CAPABILITY_CONTEXT` specifically

I did not observe a single `existing_capability_context` mapping surface anywhere across all three live crawls (Oman Airports's 49 RESEARCH items, SC's 63, Siemens's 62 — none carried that category label). I can't fully explain this from the UI alone. Two plausible, non-exclusive explanations: (a) the specific two-or-more-grounded-terms-with-no-local-signal shape the category targets genuinely didn't occur in what got crawled this time, or (b) most of what would previously have hit that branch is instead being caught earlier and downgraded straight to `weak_relevance` (which is if anything more conservative, not less). I did not find live evidence of the `EXISTING_CAPABILITY_CONTEXT` category actively suppressing an opportunity, but I also can't positively confirm it fired correctly on a real capability-only passage, because none appeared. This is a genuine gap in what this live run was able to validate, not a claim that the branch is broken.

## 9. Unexpected/concerning finding — do not fix without direction

**The Oman Airports 3-way fan-out (§5) is the headline issue.** Summary for a decision:

- **Source:** a single, 7-year-old, Oman-government-wide (not Oman-Airports-specific) news article.
- **Current classification:** independently `strong_inference` (business_signal_inference) for Cloud & AI Infrastructure, Cybersecurity & Resilience, and Enterprise Managed Services simultaneously, each anchoring a WATCH-tier opportunity.
- **Why it's wrong:** the article isn't evidence of anything Oman Airports itself is currently doing, changing, or requiring — it's generic country-level messaging, and it's stale. Three different capabilities being independently promoted from one weak, generic passage is exactly the "generic trigger + local corroboration" failure mode the Current/Change/Requirement Signal Gate targeted, just with the corroborating terms happening to be capability-specific words scattered through a long generic article rather than the same shared trigger phrase repeating.
- **Which rule produced it:** most likely `_locally_supported_by_context_terms`'s local-corroboration check inside Rule 2/3/4 of `evidence_mapping.py` — a capability-specific keyword occurring anywhere within the sentence-adjacency radius of the generic trigger is treated as sufficient corroboration, regardless of how generic, old, or organization-non-specific the surrounding article is. The gate has no freshness or organization-specificity floor.
- **Regression or pre-existing?** I believe this is a **gap the hardening pass didn't close**, not a new regression introduced by it — the mechanism (local corroboration satisfying a shared generic trigger) is the same mechanism the SC fix relies on; it happened to work correctly for SC's specific sources and not for this one. It's the same class of risk flagged in the implementation report's "remaining false positives" section, now confirmed live rather than theoretical.

I have made no code changes. This needs a decision: is this worth a targeted follow-up (e.g., requiring the corroborating capability-specific term to be reasonably close/organization-attributed, or applying a staleness/organization-specificity floor to what can satisfy local corroboration for a shared generic trigger), or is it acceptable as a known, documented limitation for now?

## 10. Remaining false positives

- **Oman Airports: Cloud & AI Infrastructure, Cybersecurity & Resilience, Enterprise Managed Services** (all 42.0/WATCH) — per §5/§9, all three trace back to a stale, non-org-specific generic article and should be treated as likely false positives pending a decision on §9.

## 11. Remaining false negatives / open items

- **Siemens: Network & Connectivity** — previously a genuine opportunity, absent from this crawl entirely (no strong/direct evidence found at all, not merely downgraded). Likely source variance rather than a gate defect, but unconfirmed (§7).
- **UI limitation, not a code defect, but relevant to how much I could verify:** the RESEARCH tab collapses each source to one representative capability-tagged row plus one generic "evidence" row, even when a source produces multiple capability mappings (confirmed directly: "Digital transformation well on track" for Oman Airports produced 4 separate mappings, and "Standard Chartered and Broadcom Create AI Cloud..." produced 4 for SC, but each showed only 1 row in RESEARCH). The DECIDE tab's "Supporting sources" links are the only place all contributing URLs are visible, and it doesn't show the underlying passage text or matched terms — only the RESEARCH tab does, and only for whichever single mapping it chose to surface. This meant I could not directly read the passage behind SC's Cybersecurity `direct_evidence` (80/100) classification, or Oman Airports's/Siemens's individual Cloud&AI/Cybersecurity/EMS `strong_inference` classifications — I inferred them from the one visible sibling row plus the DECIDE tab's source list and confidence math. This is worth knowing independent of whether §9 gets another hardening pass, since it limits how auditable the live evidence trail currently is through the UI alone.

## 12. Recommendation

**Do not close this hardening iteration yet.** The core, originally-reported problem (Standard Chartered's "digital transformation" fan-out) does appear fixed, and Oman Airports's literal capability-mention negative control (the two example sentences) wasn't disproven — but the live run surfaced a live, reproducible instance of the *same class* of defect on a different account (Oman Airports), via a mechanism (local corroboration with no freshness/org-specificity floor) that the current gate doesn't address. I'd recommend a targeted follow-up scoped narrowly to §9 before calling this closed, with the Siemens Network & Connectivity gap (§7) re-checked against a second fresh crawl to determine whether it's source variance or a real gap, since one crawl isn't enough to distinguish the two.

I have made no code changes and am awaiting direction on whether to proceed with a targeted fix for §9, investigate §7 further, or something else.

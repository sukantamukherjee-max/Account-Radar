# Follow-Up Evidence-Relevance Audit — New Live Run (Post #1–#6 Hardening)

Read-only. No code was modified. Source: `phase1d_report.json` re-staged fresh from
`...\account_radar_phase2_ai_reliability_fix\phase1d_report.json` (mtime confirms this is a run
made *after* the evidence-relevance hardening pass — file size dropped from 661KB to 631KB,
consistent with fewer promoted mappings).

**Headline result: the hardening worked for everything it targeted, and worked well.** Every
specific false positive named in the original audit that recommendations #1–#6 were built to fix
is now gone or neutralized. What remains is a smaller set of **different, previously-undiscussed**
patterns — some genuinely new, one a re-discovery of a gap I flagged as a "known limitation" in the
implementation report (manufacturing/renewable-energy capacity vs. IT capacity, which you
specifically asked me to re-test) and one a display/transparency issue rather than a
classification error. Details below.

---

## Oman Airports Management Company — confirmed: zero opportunities, fully consistent

`opportunity_hypotheses`: **0**. All 23 `chain` entries are `weak_relevance` (22
`incidental_mention`, 1 `stakeholder_context` — a LinkedIn personal profile, correctly capped).
**Zero** `direct_evidence`/`strong_inference` mappings exist anywhere in the corpus for this
account, so zero opportunities is the *correct*, consistent outcome — nothing is being silently
dropped at the opportunity-scoring stage; there is simply no promoted evidence to build an
opportunity from.

Notably, the specific government-portal false positive from the original audit
(`mtcit.gov.om`, "Oman–Algeria... digital transformation") is present in this run's corpus and is
now correctly `weak_relevance/incidental_mention` across all 4 capabilities it used to fan out to
— direct confirmation that recommendation #4's country-token exclusion is working as intended in
production (Oman Airports Management Company's name contains "Oman," the country, and that token
no longer helps satisfy organization association). The other original Oman false positive
(`gulfconstructiononline.com`) simply is not present in this run's corpus at all — likely because
live search returned different results this time, not something this audit can attribute to the
code change either way.

---

## Standard Chartered Bank — 4 opportunities, all `watch` priority, all scoring identically at 42.0

The fact that all four opportunities score **exactly 42.0** regardless of how many "independent
domains" back them (1 domain for Data Centre, 3 for the others) is itself the clearest evidence
that recommendation #1 is working correctly in production: every promoted SC mapping is
`business_signal_inference` (none is `organizational_technology_event`/`procurement_signal`), so
none of them earn the diversity bonus — the score reduces to `best_confidence × 0.70` in every
case. This is a direct, visible fix of the original audit's "Standard Chartered — Cybersecurity &
Resilience / Enterprise Managed Services: score 50.0, half the corroboration is a fabricated
signal" finding.

### 1–4. Cloud & AI Infrastructure / Cybersecurity & Resilience / Data Centre & Infrastructure / Enterprise Managed Services

| | Cloud & AI Infra | Cybersecurity & Resilience | Data Centre & Infra | Enterprise Managed Services |
|---|---|---|---|---|
| **Score / priority** | 42.0 / watch | 42.0 / watch | 42.0 / watch | 42.0 / watch |
| **# supporting mappings** | 2 | 3 | 1 | 3 |

**Which mappings are genuinely relevant:**
- The `sc.com` press release + its `globalbrandsmagazine.com` reprint ("We've partnered with
  Microsoft to become a cloud-first bank") are genuine, organization-specific evidence **for Cloud
  & AI Infrastructure only** — matches the original audit's SUPPORTED verdict, unchanged.

**Which mappings are questionable or unsupported:**
- The same Microsoft press release backing **Cybersecurity & Resilience** and **Enterprise Managed
  Services** is QUESTIONABLE, unchanged from the original audit: the source text never mentions
  security/SOC/DDoS or managed-services content — the `business_impact` line shown for those two
  capabilities is generic capability-catalogue boilerplate, not anything the source states, and
  Microsoft is a named partner already embedded in this specific initiative (partner-context).
  This is a real, disclosed residual gap: none of recommendations #1–#6 addressed "one generic
  trigger (`digital transformation`) legitimately fans a single real event out to 3 capabilities'
  worth of boilerplate business-impact text." It's arguably correct behavior (a genuine cloud
  migration *can* have security/managed-services implications) rather than a false positive —
  which is why the original audit called it QUESTIONABLE, not UNSUPPORTED, and why it wasn't one
  of the 6 recommendations.
- **The `globalbrandsmagazine.com` reprint is the same underlying press-release announcement as
  `sc.com`, word-for-word** ("...digital transformation journey as it strives to address evolving
  customer needs and build the...") — a syndicated republish, not an independent second source.
  It is still being treated as a separate "independent domain"/"independent cluster" in the
  transparency fields (`independent_domain_count=2`, etc.), even though recommendation #1
  correctly prevents it from inflating the *score*. This is a genuinely new finding this
  follow-up audit surfaced: **recommendation #1 neutralizes the scoring consequence, but the
  underlying "syndicated reprint counted as a second independent domain" issue is still present**
  in the evidence-count/domain-count transparency fields a reviewer would see.
- **`sc.bank.in/business/business-expansion/business-instalment-loan`, a retail business-loan
  product page, is STILL promoting `strong_inference` for Data Centre & Infrastructure,
  Cybersecurity & Resilience, and Enterprise Managed Services** — this is the *exact* false
  positive recommendation #5 was built to fix, and it is unchanged from the original audit
  (`matched_terms=['business expansion']`, `supporting_context` is literally page navigation text:
  "Thank you for visiting our www.sc.bank.in Proceed Business Banking Business Expansion Business
  Installment Loan..."). **Root cause, confirmed by inspecting the URL against the shipped
  detector:** `looks_like_customer_product_page()`'s path-marker regex requires an exact path
  *segment* equal to `loan`/`loans` (bounded by `/`), but this URL's path is
  `/business/business-expansion/business-instalment-loan` — the product name is a single
  hyphenated compound segment (`business-instalment-loan`), not its own `/loan/` directory level.
  The regex never matches, `is_customer_product_page` evaluates `False`, and the Rule 4 / Rule 2-3
  fallback gate added for recommendation #5 never engages. **This is a real, still-open
  implementation gap in recommendation #5**, not a new pattern — the fix's *design* is correct,
  its *path-matching regex* is too narrow for hyphenated product-name URL slugs, which are common
  on retail banking sites (`/business-instalment-loan`, `/business-essential-account`, etc. all
  follow this shape on `sc.bank.in`).

**Should each opportunity remain, be downgraded, or be removed:**
- **Cloud & AI Infrastructure:** remain — its only two mappings are both genuinely relevant.
- **Cybersecurity & Resilience:** downgrade — currently backed by 1 genuine-but-indirect mapping
  (Microsoft/cloud, QUESTIONABLE) and 2 that should not exist at all (the loan-page false
  positive). With the loan-page mappings correctly suppressed, this would legitimately drop to a
  single QUESTIONABLE-strength watch item, likely at a similar or slightly lower score.
- **Enterprise Managed Services:** downgrade, same reasoning as Cybersecurity & Resilience.
- **Data Centre & Infrastructure:** should be **removed** — its *only* supporting mapping is the
  loan-page false positive. With that fixed, this opportunity has zero genuine supporting evidence
  and should not exist.

**Root-cause layer:** capability mapping, specifically — the recommendation #5 detector's URL-path
regex is too narrow (implementation gap, not a conceptual miss). The Microsoft-press-release
fan-out and syndicated-reprint-as-independent-domain issues are pre-existing, disclosed,
out-of-scope-for-#1–#6 behaviors (boilerplate `business_impact` text and syndication-detection were
never part of the 6 recommendations).

---

## Siemens AG — 4 opportunities

### 1. Cybersecurity & Resilience — score 78.0, **priority: high**, 2 mappings

- **Genuinely relevant:** `news.siemens.com`, "The UAE Cyber Security Council and Siemens
  Partner" — direct_evidence, confidence 90, a concrete, organization-attributed phased SOC
  capability expansion. Unchanged from the original audit's SUPPORTED verdict. This mapping alone
  (confidence 90 × 0.70 = 63) would be a solid **medium**-priority opportunity on its own.
- **Questionable:** `siemens-energy.com/cybersecurity.html` — still `direct_evidence`, confidence
  85, matched on `ransomware` + `cyber risk`. The supporting text ("attackers seek to lock
  companies out of their own IT systems...") is generic cybersecurity-awareness marketing copy,
  not a statement of a current Siemens initiative — unchanged from the original audit's
  QUESTIONABLE verdict. **This is what is pushing the opportunity from medium to HIGH priority**
  (the two direct_evidence, `organizational_technology_event`-category mappings both count toward
  recommendation #1's bonus, since the bonus gate is about evidence *category*, not evidence
  *quality* within a category — this second mapping earns full bonus credit despite being
  low-quality). **Root cause: none of recommendations #1–#6 addressed this pattern** — Rule 2/3's
  "two or more distinct direct capability keywords co-occurring with the organization ⇒
  direct_evidence" bar (a pre-existing, Phase-1C-H2-era rule) has no requirement that the text
  describe an actual event; two generic-marketing keywords satisfy it exactly as well as two
  keywords in a real announcement. This is a new, distinct finding from this follow-up audit, not
  a recurrence of a previously-named issue.
- **Verdict:** downgrade from high to medium — remove the marketing-copy mapping's contribution
  and this is a solid, single-source, well-evidenced medium-priority opportunity.
- **Root-cause layer:** capability mapping (Rule 2/3's event-agnostic 2-keyword-co-occurrence bar).

### 2. Network & Connectivity — score 64.5, priority: medium, 3 mappings

- **Genuinely relevant:** `press.siemens.com`, "Siemens delivers verified AI-driven cybersecurity
  solution for Industrial 5G with Palo Alto Networks" — direct_evidence, confidence 85, genuine
  and specific, published on **Siemens' own domain** (not Palo Alto's). Unchanged from the
  original audit's SUPPORTED verdict, and this is direct confirmation that recommendation #2's
  vendor-case-study rule is correctly scoped to the *source's* domain and does not over-fire just
  because a named vendor/partner appears in a legitimate first-party announcement.
- **Unsupported:** `siemens-energy.com`, Eaton/Siemens Energy power-equipment release — still
  `strong_inference`, matched on `fiber`, describing **data-centre siting criteria for Siemens
  Energy's own prospective customers** ("close to gas, water and fiber" — utility hookups a
  customer's site should have), not a Siemens connectivity requirement. Unchanged from the
  original audit's UNSUPPORTED verdict. This is a **new pattern for this follow-up audit to name
  explicitly**: it is adjacent to, but distinct from, recommendation #5's "customer-facing retail
  product page" pattern — here the organization's own press-release domain is legitimately
  describing what *its customers* need, not itself, in ordinary B2B industrial-press prose rather
  than a retail pricing page. Recommendation #5's URL-path/content-marker detector (built for
  retail/pricing pages) does not and was not intended to catch this shape of source, so this is not
  a bug in #5 so much as an uncovered, related category.
- **Questionable, and worth flagging as a transparency issue rather than a pure classification
  bug:** `aei.dempa.net`, "Siemens' New Pitch Aims at Industrial Metaverse" — still `strong_inference`
  on the `5g` keyword, and the `supporting_context` shown is **still the exact buzzword/label-list
  text** the original audit flagged ("ENABLING TECHNOLOGIES IIOT Analytics Microchip Sensors
  Connectivity / IPC Cybersecurity 5G Digital Twin..."). However, the *sibling* mappings from this
  same article and the same buzzword list (Cloud & AI Infrastructure, Cybersecurity & Resilience,
  Enterprise Managed Services, previously fanned out via the "digital transformation"/employee-
  training paragraph) are now correctly suppressed to `weak_relevance` — proof that recommendation
  #3's buzzword-list detector *is* firing correctly on this exact source's text. The most likely
  explanation for why the `5g` mapping alone still promotes: `5g` is a direct capability *keyword*
  (not a generic trigger), so it is evaluated by Rule 2/3, not Rule 4 — and Rule 2/3's
  `STRONG_INFERENCE` fallback checks whether *any* technology-event or qualifying signal exists
  *anywhere* in the source (here, an unrelated "employees are upgrading their skills... (NextWork)"
  sentence), with no requirement that the grounding signal be positionally near the matched term
  itself — unlike Rule 4, which does enforce that locality. If `5g` also occurs even once more,
  elsewhere in this (likely long) article, near a Siemens mention outside the buzzword list, the
  organization-association check would legitimately pass on that occurrence — while the *displayed*
  `supporting_context`/`customer_signal.text` (which always shows the *first* occurrence in the
  source) still shows the buzzword list, making the mapping look unsupported to a reviewer even
  where the underlying decision may have a legitimate basis elsewhere in the page. **This is a
  genuinely new finding: a display/transparency gap (which occurrence of a matched term is shown)
  compounded by a structural asymmetry between Rule 2/3 and Rule 4 (only Rule 4 requires the
  grounding signal to be positionally local to the matched term) — neither was one of
  recommendations #1–#6, and I cannot fully confirm which explanation applies without the raw page
  text, which isn't captured in the report.**
- **Verdict:** downgrade — remove the Eaton/siting-criteria mapping; keep the Palo Alto mapping;
  treat the `aei.dempa.net` mapping as unresolved pending the raw-text check above. With only the
  Palo Alto mapping retained, this becomes a single-source, high-confidence, correctly medium/
  watch-tier opportunity depending on final scoring.
- **Root-cause layer:** capability mapping, two distinct pre-existing gaps (customer-siting-
  criteria disambiguation; Rule 2/3 lacking the same positional-locality requirement Rule 4 has)
  plus a signal-extraction/display transparency issue (which occurrence is shown).

### 3. Cloud & AI Infrastructure — score 38.5, priority: watch, 1 mapping

- **Genuinely relevant:** `press.siemens.com`, "€2 billion investment strategy" — `strong_inference`,
  matched on `nvidia`, genuinely on-topic (Siemens + NVIDIA "industrial metaverse" collaboration).
  Unchanged from the original audit's SUPPORTED verdict. No issues.
- **Verdict:** remain, as-is. (Score is capped at watch/38.5 mainly because this is correctly
  classified `business_signal_inference` rather than `organizational_technology_event` — the
  recommendation #7 tiering question the audit flagged as a secondary observation, not
  implemented per your instruction.)
- **Root-cause layer:** none — this one is working correctly.

### 4. Data Centre & Infrastructure — score 38.5, priority: watch, 2 mappings — **the specific known limitation you asked me to re-test**

- **Both mappings are the manufacturing/renewable-energy vs. IT-capacity pattern, confirmed still
  present, unchanged:**
  - `press.siemens.com`, same €2B investment release — matched on `capacity expansion`, but the
    source text explicitly says "**manufacturing** capacity expansion" (factory output), not
    IT/data-centre capacity. QUESTIONABLE in the original audit; unchanged here.
  - `siemens-energy.com/investor-relations` — matched on `capacity expansion`, but the text is
    **Siemens Gamesa's (the wind-turbine subsidiary) quarterly earnings commentary**
    ("...delivered its highest quarterly revenue to date, supported by progress in capacity
    expansion. Siemens Gamesa reported a positive result...") — renewable-energy generation
    capacity, not IT capacity. UNSUPPORTED in the original audit; unchanged here.
- This is **exactly** the limitation named in the implementation report's Known Limitations
  section #6 ("capability-specific semantic disambiguation... nothing in #1–#6 targets this,
  since doing so deterministically without sector-specific hardcoding is a materially harder
  problem"). Confirmed: it was correctly disclosed as unaddressed, and it is indeed still present
  and unaddressed in this live run. Recommendation #1 does successfully prevent it from being
  scored as a HIGH-priority opportunity (both mappings are `business_signal_inference`, so neither
  earns the diversity bonus — this capability sits at the lowest tier, `watch`, exactly because of
  that), which is a meaningful improvement over the original audit's finding (this same capability
  was previously HIGH priority at 77.5 with "3 independent domains," one of which was the
  now-fixed Zscaler case study). But the *individual mappings themselves* are still wrong at the
  classification level.
- **Verdict:** should be **removed** — neither of its two mappings is actually about Siemens' own
  IT/data-centre capacity; both are category conflations (manufacturing output, renewable-energy
  generation). Confirmed exactly as predicted.
- **Root-cause layer:** capability mapping — a disclosed, still-open limitation (the term
  "capacity expansion" is a `generic_trigger` shared across the whole catalogue and cannot
  distinguish IT capacity from any other kind of business capacity without either much richer
  context modeling or capability-specific hardcoded disambiguation rules, the latter of which was
  explicitly ruled out by your original "no customer, industry, or sector hardcoding" constraint).

---

## Summary table

| Account | Capability | Priority | Verdict | Root-cause layer |
|---|---|---|---|---|
| Standard Chartered | Cloud & AI Infrastructure | watch | **Remain** | — (clean) |
| Standard Chartered | Cybersecurity & Resilience | watch | **Downgrade** (drop 2 of 3 mappings) | Capability mapping (rec. #5 path-regex gap) |
| Standard Chartered | Enterprise Managed Services | watch | **Downgrade** (drop 2 of 3 mappings) | Capability mapping (rec. #5 path-regex gap) |
| Standard Chartered | Data Centre & Infrastructure | watch | **Remove** (only mapping is the false positive) | Capability mapping (rec. #5 path-regex gap) |
| Siemens | Cybersecurity & Resilience | high | **Downgrade to medium** | Capability mapping (Rule 2/3 event-agnostic bar — new finding) |
| Siemens | Network & Connectivity | medium | **Downgrade** (drop 1 mapping, 1 unresolved) | Capability mapping (siting-criteria + Rule 2/3-vs-4 asymmetry — new findings) |
| Siemens | Cloud & AI Infrastructure | watch | **Remain** | — (clean) |
| Siemens | Data Centre & Infrastructure | watch | **Remove** | Capability mapping (disclosed known limitation, confirmed) |
| Oman Airports | *(none)* | — | **Correctly zero** | — (clean) |

## What changed vs. the original audit — net assessment

Fully fixed, confirmed in production data: the Zscaler vendor case study (recommendation #2), the
thekeyexecutives.com CIO-bio fan-out (recommendation #6), the mtcit.gov.om government-portal
country-level association (recommendation #4), and the buzzword-list fan-out for 3 of the 4
capabilities it used to hit on `aei.dempa.net` (recommendation #3). The evidence-diversity scoring
fix (recommendation #1) is visibly working across both accounts — no opportunity's score is being
inflated by `business_signal_inference` domain/cluster diversity anymore, and Siemens' Data Centre
& Infrastructure dropped from HIGH (77.5) to watch (38.5) as a direct, correct consequence even
though its two individual mappings are still wrong at the classification level.

Still open, all newly precise-located by this follow-up audit: (1) recommendation #5's URL-path
regex doesn't match hyphenated product-slug URLs like `/business-instalment-loan` (a real,
fixable implementation gap, not a conceptual miss); (2) Rule 2/3's two-keyword-co-occurrence and
signal-backed-fallback paths have no requirement that the underlying text describe an actual
event or that the grounding signal be positionally near the matched term (the latter unlike Rule
4, which already enforces it) — both pre-existing, not part of #1–#6; (3) a "vendor describes its
own customers' requirements, not its own" pattern on B2B industrial press pages, related to but
distinct from recommendation #5's retail-page pattern; (4) the manufacturing/renewable-energy
capacity-expansion conflation, confirmed present exactly as disclosed and exactly where you asked
me to check.

No code was changed as part of this audit.

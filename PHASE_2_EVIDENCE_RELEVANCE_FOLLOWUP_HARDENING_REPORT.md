# Phase 2 Evidence-Relevance Hardening — Follow-Up Pass

Implements the four gaps identified in `PHASE_2_EVIDENCE_RELEVANCE_FOLLOWUP_AUDIT.md`
(the audit of the live `phase1d_report.json` produced after recommendations #1–#6).
Scope was constrained to exactly these four areas; `ai/prompt.py`, `ai/client.py`,
and `ai/synthesis.py` were not touched, and no behavior from the #1–#6 pass was
changed except where a specific gap below required it.

---

## 1. Files changed

| File | Nature of change |
|---|---|
| `commercial/models.py` | Added one new `EvidenceCategory` value: `CUSTOMER_REQUIREMENT_CONTEXT`. |
| `commercial/text_heuristics.py` | Rewrote `looks_like_customer_product_page`'s path-matching from exact-segment to token-based (Area 1). Added `looks_like_customer_requirement_span` and its marker regex (Area 3). |
| `commercial/evidence_mapping.py` | Added `_span_disqualifies_evidence`, `_direct_evidence_locally_grounded`, `_has_customer_requirement_language_near_terms`, `_generic_trigger_is_ambiguous`, `_locally_supported_by_context_terms`, `_usable_generic_hits`. Rewrote Rule 2/3's three promotion branches and Rule 4's promotion gate. Updated Rule 2/3's and Rule 5's non-promoted category labeling. |
| `tests/test_evidence_relevance_hardening.py` | Added 13 new tests (regressions + positive-preservation) covering all four areas, appended as a new "Follow-up hardening pass" section. |

No other file was modified. `ai/prompt.py`, `ai/client.py`, `ai/synthesis.py` are
byte-identical to their state before this pass.

---

## 2. Exact behavioral changes

### Area 1 — Hyphenated product-slug detection

`looks_like_customer_product_page()`'s path check previously required an EXACT
path segment (e.g. `/loans/`). It now tokenizes the URL path on any
non-alphanumeric character and checks whether a marker word (`loan`, `account`,
`plan`, `card`, `product`, `retail`, `offer`, etc.) appears as a **whole token**
anywhere in the path — so `/business-expansion/business-instalment-loan` is now
recognized (the token `loan` inside the hyphenated slug), while `/download-app`
still does **not** match (`download` tokenizes as one token, not `loan` + something).
The dual requirement is unchanged and was not weakened: a path match alone is
still never sufficient — the function still additionally requires ≥2 distinct
product/pricing content markers (`apply now`, `interest rate`, `apr`, `terms and
conditions apply`, etc.) in the page's own text. A single keyword, in the path or
the content, can never by itself classify a page as a product page.

### Area 2 — Rule 2/3 locality + event gaps

Two related gaps in the `direct_hits` branch of `map_source_to_capabilities`:

- **Branch 1 (“two or more distinct terms” → DIRECT_EVIDENCE)** only required
  organization-association from *at least one* of the matched terms (via
  `_is_organization_associated`'s any-match semantics). A second, unrelated term
  occurring anywhere else in a long source was sufficient to clear the "two
  distinct terms" bar without itself being anywhere near the organization. New
  helper `_direct_evidence_locally_grounded()` instead counts how many *distinct*
  terms are **each individually** locally associated with an organization
  mention, and branch 1 now requires that count to be ≥2.
- **Branches 2/3 ("single term + high-trust tech event" / "single term +
  qualifying signal")** treated `tech_event_signal` / `qualifying_signal` as
  usable simply because *some* such signal was extracted anywhere from the
  source — with no positional check, unlike Rule 4's existing signal-locality
  gate. Both branches now additionally require the chosen signal's own match to
  be locally associated with the matched `direct_hits` term (reusing
  `_signal_locally_associated_with_trigger`, the exact mechanism Rule 4 already
  used for `generic_hits`). `grounding_signal` (used for `signal_type` and
  `inference_cluster_id`) now only ever holds a signal that is actually locally
  grounded, never merely present in the source.

Non-promoted (`WEAK_RELEVANCE`) direct-hit mappings are now labeled
`CUSTOMER_REQUIREMENT_CONTEXT` instead of `INCIDENTAL_MENTION` when the matched
term's local span reads as third-party/customer-facing language (see Area 3).

`_span_disqualifies_evidence()` (a small union of the pre-existing
`is_low_trust_span` and the new `looks_like_customer_requirement_span`) now
gates every per-occurrence locality check in `_is_organization_associated`,
`_signal_locally_associated_with_trigger`, and the two new Area 2/4 helpers —
so a customer-requirement span is disqualified from grounding evidence in
exactly the same structural way a buzzword list or navigation span already was.

### Area 3 — Organization requirements vs. customer requirements

New `looks_like_customer_requirement_span(segment)` in `text_heuristics.py`
detects generic, third-party-facing language at the single-span (roughly
one-sentence) level: `prospective customers`, `(its/their/our/your) customers`,
`customer sites/requirements/needs/deployments/use cases`, `siting criteria`,
`for (its/their/our/your/prospective) sites/facilities/operations`, etc. — no
company, sector, or geography name anywhere in the pattern.

Because this check is folded into `_span_disqualifies_evidence()` (used
everywhere a term or signal's local sentence context is checked), a matched
capability term whose own sentence describes what a customer needs — e.g.
"prospective customers should site their data centres close to gas, water and
fiber connections" — can no longer ground organization-association, direct
evidence, strong-inference promotion, or Rule 4's ambiguous-trigger context
check. It also cannot make the organization's own name (if it happens to appear
in that same customer-facing sentence) count as an association anchor for other
terms. The evidence is not dropped: it is retained as `WEAK_RELEVANCE` and
labeled `EvidenceCategory.CUSTOMER_REQUIREMENT_CONTEXT` (new helper
`_has_customer_requirement_language_near_terms`, applied in both Rule 2/3's
non-promoted branch and Rule 5's fallthrough) — useful as contextual/
current-state evidence, never as grounds for an opportunity (opportunity
formation already only considers `DIRECT_EVIDENCE`/`STRONG_INFERENCE`, so a
`WEAK_RELEVANCE` + `CUSTOMER_REQUIREMENT_CONTEXT` mapping structurally cannot
back an opportunity hypothesis — no change to `commercial/opportunities.py` was
needed).

### Area 4 — Generic capacity-expansion semantic ambiguity

New, purely lexical, catalogue-driven mechanism — no capability id, industry,
sector, or company name is branched on anywhere:

- `_generic_trigger_is_ambiguous(term)`: any `generic_trigger` string
  containing the word "capacity" (case-insensitive, e.g. "capacity expansion")
  is treated as inherently ambiguous — its plain-English meaning does not by
  itself specify *what kind* of capacity is meant.
- `_locally_supported_by_context_terms(text, trigger_terms, context_terms)`:
  true when at least one occurrence of an ambiguous trigger has one of
  `context_terms` locally associated with it (same sentence, or an immediately
  adjacent one — the same radius used everywhere else in this module).
- `_usable_generic_hits(text, generic_hits, capability)`: every non-ambiguous
  trigger passes through unchanged; an ambiguous one is kept only when it is
  locally supported by **that capability's own `keywords` + `weak_signal_terms`**
  (already sector-agnostic catalogue data — reused, not a new hardcoded list).

Rule 4's promotion decision (`generic_org_associated`, `inference_signal`
locality, and the final promotion condition) now runs against
`usable_generic_hits` instead of the raw `generic_hits`. The resulting
`CapabilityMapping.matched_terms` still records the **full** `generic_hits` list
for transparency — an unsupported ambiguous trigger is still disclosed
evidence, just not promotion-eligible on its own. Concretely: "capacity
expansion" at a manufacturing plant, a renewable-generation facility, or with no
further context at all no longer promotes Data Centre & Infrastructure to
`STRONG_INFERENCE`; "capacity expansion" locally accompanied by "data centre",
"server", "storage", "compute" (etc. — the capability's own vocabulary) still
does. This mechanism is fully generic: if a future catalogue edit adds the word
"capacity" to a generic trigger for a *different* capability, the same rule
applies automatically, driven by catalogue data rather than this module's code.

### Epistemic-rule compliance

Per the explicit instruction, no unsupported evidence was silently converted
into a *weak-but-still-implicitly-promotable* shape merely to keep a mapping
alive. Every one of the four fixes above, when it blocks promotion, routes the
mapping to `EvidenceLevel.WEAK_RELEVANCE` via the pre-existing Rule 5 fallthrough
(unchanged mechanism) — and, where the reason is specifically "this describes a
third party's requirement, not the organization's own," the mapping is now
additionally and explicitly labeled `CUSTOMER_REQUIREMENT_CONTEXT` rather than
left indistinguishable from an ordinary incidental keyword mention.

---

## 3. Tests added

All 13 new tests live in `tests/test_evidence_relevance_hardening.py`, appended
under a new "Follow-up hardening pass" section (existing #1–#6 tests are
unchanged and still pass).

**Area 1 (product-slug detection):**
- `test_hyphenated_product_slug_false_positive_is_now_detected` — regression: the exact `/business-expansion/business-instalment-loan` shape.
- `test_similar_wording_on_a_genuine_organizational_announcement_still_promotes` — positive: same vocabulary, non-product page, still promotes.

**Area 2 (Rule 2/3 locality + event gaps):**
- `test_unrelated_technology_signal_elsewhere_in_article_does_not_ground_direct_term` — regression: a single direct term near the org, a technology-event signal describing an unrelated fact two sentences away (previously wrongly promoted to `DIRECT_EVIDENCE`).
- `test_generic_cybersecurity_marketing_copy_with_two_keywords_does_not_promote` — regression: two direct keywords in "helps its customers..." marketing copy.
- `test_siemens_5g_buzzword_list_case_does_not_promote_via_unrelated_signal` — regression: the audit's own 5G/buzzword-list-plus-unrelated-signal pattern.
- `test_genuine_locally_associated_technology_announcement_still_promotes` — positive: signal one sentence away from the term, describing the same event, still promotes `DIRECT_EVIDENCE`.

**Area 3 (organization vs. customer requirements):**
- `test_customer_siting_criteria_language_is_not_promoted_as_organizations_own_requirement` — regression: the "prospective customers ... close to gas, water and fiber" pattern; asserts the mapping is retained as `WEAK_RELEVANCE` / `CUSTOMER_REQUIREMENT_CONTEXT`, not silently dropped.
- `test_organizations_own_site_requirement_language_still_promotes` — positive: identical siting language describing the organization's own facility still promotes as `BUSINESS_SIGNAL_INFERENCE`.

**Area 4 (capacity-expansion ambiguity):**
- `test_manufacturing_capacity_expansion_does_not_promote_data_centre_infrastructure` — regression.
- `test_renewable_energy_capacity_expansion_does_not_promote_data_centre_infrastructure` — regression.
- `test_generic_business_capacity_expansion_does_not_promote_data_centre_infrastructure` — regression (also asserts the mapping survives as `WEAK_RELEVANCE`, not dropped).
- `test_explicit_data_centre_capacity_expansion_still_promotes` — positive: "capacity expansion of its data centre... storage and compute" still promotes `STRONG_INFERENCE`.
- `test_infrastructure_capacity_expansion_with_clear_it_context_still_promotes` — positive: "capacity expansion of its IT infrastructure... server and storage" still promotes `STRONG_INFERENCE`.

**Differential verification (not a permanent test, done during implementation):**
both `test_unrelated_technology_signal_elsewhere_in_article_does_not_ground_direct_term`
and `test_manufacturing_capacity_expansion_does_not_promote_data_centre_infrastructure`
were re-run against a monkeypatched simulation of the pre-fix logic
(`_signal_locally_associated_with_trigger` forced to always return `True`,
`_usable_generic_hits` forced to pass every generic trigger through unfiltered)
to confirm they would in fact have false-positived before this pass —
`DIRECT_EVIDENCE`/`organizational_technology_event` and
`STRONG_INFERENCE`/`business_signal_inference` respectively — and now correctly
resolve to `WEAK_RELEVANCE`.

---

## 4. Complete pytest result

Run from `/home/claude/project/phase2_fixed`:

```
PYTHONPATH=/root/.local/lib/python3.11/site-packages:/usr/local/lib/python3.11/dist-packages pytest -q

........................................................................ [ 42%]
........................................................................ [ 84%]
...........................                                              [100%]
171 passed in 2.76s
```

171 = 158 pre-existing (all still passing, unmodified) + 13 new tests. No test
was skipped, xfailed, or modified to make it pass artificially. No test outside
`tests/test_evidence_relevance_hardening.py` needed any change.

---

## 5. Known limitations

- **Area 4's "ambiguous trigger" detection is lexical (the word "capacity"), not
  semantic.** A generic trigger phrase that describes the same kind of
  cross-domain ambiguity without literally containing the word "capacity" (the
  catalogue currently has none, but a future one might, e.g. a hypothetical
  "throughput expansion") would not automatically get this treatment unless the
  catalogue author also adds "capacity" wording or a future maintainer extends
  the detection regex.
- **The IT-context vocabulary for Area 4 is exactly each capability's own
  `keywords` + `weak_signal_terms`.** This is deliberately reused catalogue
  data (not a new hardcoded list), but it means the quality of the ambiguity
  gate is bounded by how complete that capability's own vocabulary is; a
  capability with a very sparse keyword list has a correspondingly narrow
  "what counts as local IT context" bar.
- **Area 3's customer-requirement detector is a fixed set of generic English
  constructions** (`prospective customers`, `(its/their/our/your) customers`,
  `customer sites/requirements/...`, `siting criteria`, etc.). A genuine
  third-party-requirement description that uses none of these constructions
  (e.g. naming the actual named customer instead of the word "customer") would
  not be caught by this pass — this is a precision/recall tradeoff deliberately
  made to avoid a broader, less-disclosed heuristic.
- **Area 2's branch-1 fix (`_direct_evidence_locally_grounded`) addresses the
  "one term near the org, the second term anywhere else" gap, and the
  marketing-copy case tested here is caught via the Area 3 customer-language
  detector, not a dedicated "is this an event vs. a service description"
  classifier.** A hypothetical case where two direct terms are both
  individually near the organization's name, are not customer-facing language,
  and yet still describe generic marketing/awareness copy rather than a
  concrete event (with no recognizable disqualifying shape at all) is not
  addressed by this pass — closing that fully would require either a new,
  generically-justified "event vs. description" heuristic or accepting some
  residual reliance on the two-distinct-term threshold as a deliberately loose
  bar, consistent with the original Phase 1C-H2 design rationale (independent
  co-occurrence of two specific terms is treated as unlikely regardless of
  source type).
- **This pass was not run against a fresh live acquisition (`phase1d_report.json`).**
  Per instructions, no live research was run; validation is via the unit-test
  suite and the differential (before/after) spot-checks above, not a new
  end-to-end report.

---

## 6. No customer/industry/sector-specific hardcoding

Confirmed by inspection and by grep across all three modified production files
(`commercial/models.py`, `commercial/text_heuristics.py`,
`commercial/evidence_mapping.py`):

- No organization, customer, subsidiary, vendor, industry, sector, or
  geography name appears in any conditional, constant, or regex in the new
  code. The only occurrences of names like "Oman Airports," "Siemens," or
  "manufacturing"/"renewable-energy" in these files are pre-existing
  illustrative **comments** (already present from the #1–#6 pass) or new
  **prose comments** explaining what the Area 4 fix generalizes over (never
  code that branches on those words).
- Area 1's fix operates on generic path-token vocabulary (`loan`, `account`,
  `plan`, `card`, `product`, `retail`, `offer`, etc.) and generic
  product/pricing content markers — the same words apply to a bank, an
  airline, or a retailer's website identically.
- Area 2's fix reuses the existing, already-generic sentence-locality
  machinery (`sentence_spans`, `_is_organization_associated`,
  `_signal_locally_associated_with_trigger`) with no new company- or
  sector-specific input.
- Area 3's fix is a generic English-language customer/third-party-language
  detector (`prospective customers`, `siting criteria`, etc.) — it does not
  name Siemens, Siemens Energy, Eaton, or any other company, and applies
  identically regardless of who is speaking or who the customer is.
- Area 4's fix is driven entirely by (a) a single lexical marker — the word
  "capacity" — applied to whatever generic triggers the catalogue happens to
  contain, and (b) each capability's own existing keyword/weak-signal
  vocabulary, already sector-agnostic reference data. No capability id,
  industry, or company is referenced in the control flow; only comments
  reference "manufacturing" or "renewable-energy" as illustrative examples of
  the ambiguity the fix generalizes over.
- Test fixtures use the same fictional organization names already established
  in this file (`Verimark National Airports Authority`, `Castellane Bank`,
  `Oakridge Industrial AG`) — consistent with the existing #1–#6 test suite's
  convention of never referencing a real organization, matching the standard
  already set for this file.

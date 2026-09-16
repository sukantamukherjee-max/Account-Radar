# Account Radar — Phase 1C-H3.1 LinkedIn-Cap Regression Fix Report

**Scope note:** this pass fixes one regression in `commercial/evidence_mapping.py` — how the personal-LinkedIn-profile cap is detected and enforced. It does not touch the H3 organization-association gate, the H2 generic-term/technology-event safeguards, acquisition, URL validation, incumbent separation, or qualification. Not proceeding to Phase 1D.

## Why H3 allowed this H2 rule to be bypassed

The H2 cap ("a personal LinkedIn profile can never be more than `weak_relevance`, regardless of match count") was implemented as a single `if` branch at the top of `map_source_to_capabilities`, gated entirely on one upstream flag:

```python
is_linkedin_profile = source.source_type == SOURCE_TYPE_LINKEDIN_PROFILE
```

`source.source_type` is set **once, at acquisition time**, by whichever discovery code path found the URL — `research/linkedin.py` tags results from the dedicated LinkedIn-people search family as `SOURCE_TYPE_LINKEDIN_PROFILE`. But a personal LinkedIn URL can also surface as an ordinary organic hit from a *different* discovery family — a generic "business"/"news"/"technology" query template returning a `linkedin.com/in/...` result, or a country-localized host like `om.linkedin.com` that the upstream family classifier wasn't written to recognize as LinkedIn. In that case the source is tagged with *that* family's `source_type` (e.g. `"technology"`) instead, and the evidence-mapping engine — which trusted `source_type` as the single source of truth for "is this a personal profile" — had no independent way to notice.

H2's rule itself was never wrong; it was architecturally fragile: the invariant ("personal profile → capped") depended entirely on one upstream tag being correct, with no check inside `evidence_mapping.py` re-deriving that fact from the one piece of ground truth that's always available — the URL. H3 didn't touch this logic at all, but H3's organization-association gate happened to let a misclassified personal profile clear the direct-evidence bar cleanly (the profile's own title/content legitimately names the organization right next to the matched terms, so association trivially holds), which is what surfaced the pre-existing fragility as a visible regression.

## What changed

### 1. URL-based personal-profile detection (root-cause fix)
Added `_is_linkedin_personal_profile_url(url)`: parses the URL directly and returns `True` only for a `linkedin.com` (or `*.linkedin.com`, covering localized subdomains like `om.linkedin.com`) host with a `/in/...` path. `/company/...` paths (and everything else) are explicitly excluded (item 3).

`is_linkedin_profile` is now computed as:
```python
is_linkedin_profile = (
    source.source_type == SOURCE_TYPE_LINKEDIN_PROFILE or _is_linkedin_personal_profile_url(source.url)
)
```
— i.e. either signal is sufficient. `trust_tier` is also forced to `"low"` whenever this combined flag is true, regardless of what `source_type` claims, so a misclassified source can no longer borrow a higher trust tier either.

### 2. Hard output clamp (item 2 — "applied after all other logic, cannot be overridden")
Before `map_source_to_capabilities` returns, if the source is a personal LinkedIn profile (by either detection path), every mapping in its output is force-clamped to `evidence_level = WEAK_RELEVANCE` with `confidence = min(existing_confidence, 35)`. This runs **after** every promotion rule (organization association, technology-event signals, multi-term co-occurrence, source trust) has already executed — so the invariant no longer depends on the early `is_linkedin_profile` branch being the only gate. In today's code this clamp is a no-op (the early branch already produces weak-only output for a correctly-detected profile), but it is a structural guarantee: a future change to the promotion rules cannot silently reopen this bypass, because the clamp doesn't know or care which rule produced the mapping — it only looks at the source.

Evidence is never discarded (item 4): the clamp only overwrites `evidence_level`/`confidence`; `matched_terms`, `source_url`, `source_title`, `supporting_context`, `signal_type`, and `incumbent_context` are all preserved exactly as computed, so this evidence remains available for later stakeholder/person-mapping use.

### 3. Nothing else changed
- The H3 organization-association gate (`_is_organization_associated`) is untouched (item 5).
- The H2 generic-term catalogue cleanup and `_TECH_EVENT_SIGNAL_TYPES` gate are untouched (item 6).
- No organization, sector, country, or person name appears anywhere in the fix — `_is_linkedin_personal_profile_url` operates purely on URL structure (item 8).

## Tests (item 7)

New file `tests/test_linkedin_cap_regression.py`, 7 tests, using a generic placeholder organization (`"Meridian Ports Authority"`) and a synthetic (non-real) person:

- `test_personal_linkedin_profile_with_multiple_specific_terms_cannot_be_direct_evidence` — the exact reported shape: a personal profile on a localized subdomain (`om.linkedin.com/in/...`), deliberately tagged with a non-LinkedIn `source_type` to reproduce the misclassification, with three specific cybersecurity terms and a real technology-event signal → never `direct_evidence`.
- `test_personal_linkedin_profile_cannot_be_strong_inference_either` — same shape, explicitly asserts every mapping lands at `weak_relevance` (confidence ≤ 35), not merely "not direct," and that matched terms are preserved.
- `test_correctly_classified_linkedin_profile_still_capped_too` — the original H2 detection path (correct `source_type`) still works.
- `test_linkedin_company_page_is_not_treated_as_personal_profile` (item 3) — a `/company/` page on a localized subdomain is unaffected by the cap and can still reach `direct_evidence` on its own merits.
- `test_linkedin_company_url_helper_does_not_match_company_paths` — direct unit coverage of `_is_linkedin_personal_profile_url` for `/in/`, `/company/`, non-LinkedIn hosts, and empty input.
- `test_official_source_technology_evidence_still_direct_evidence_after_h3_1` / `test_procurement_source_technology_evidence_still_direct_evidence_after_h3_1` (item 8) — genuine organization-specific technology evidence from non-LinkedIn official/procurement sources still reaches `direct_evidence` after this fix.

## Full suite (item 9)

```
62 passed in 3.44s
```
The previously-accepted 55 (H1 through H3) plus these 7 new tests. `pyflakes` across `research/`, `commercial/`, `tests/`, and all three CLI scripts: clean.

## Regression checks

- `before_after_demo.py` re-run: identical to the H3 numbers (4 direct, 2 inference, 8 weak, 3 incumbent items) — none of that fixture's sources are personal LinkedIn profiles misclassified in a way this fix would touch differently than before (the one LinkedIn profile in that fixture was always correctly tagged `linkedin_profile` and was already capped).
- `run_phase1c.py --mock`: runs clean end-to-end, no errors.

Stopping here per your instruction. Not proceeding to Phase 1D.

# Account Radar — Phase 1C-H4 LinkedIn Personal-Profile Acquisition Fix Report

**Scope note:** this pass touches only LinkedIn person-discovery query construction in `research/linkedin.py`, plus the synthetic `tests/mock_provider.py` test double so it still exercises the new query shape. It does not touch the H3.1 personal-profile safety cap, the H3 organization-association gate, the H2 generic-term/technology-event safeguards, qualification, or report assembly. Not proceeding to Phase 1D.

## 1. The problem

A real Oman Airports live run resolved the LinkedIn **company** page fine (`au.linkedin.com/company/oman-airports-management-company`) but all seven LinkedIn **people** discovery dimensions returned zero candidates (`linkedin_dimension_counts` all 0; `source_type_counts` showed `linkedin_company: 1` and no `linkedin_profile` entries at all).

## 2. Root cause

`research/linkedin.py`'s `_person_queries()` built queries like:

```
site:linkedin.com/in "Oman Airports Management Company" ("Chief Technology Officer" OR "Chief Information Officer" OR ... )
```

— a `site:` operator restricting to `linkedin.com/in`, an exact-phrase-quoted company name, and a parenthesized boolean-OR group of quoted role terms. Even the plainest variant of this pattern, `general_workforce`'s `site:linkedin.com/in "Oman Airports Management Company"` (no OR clause at all), still returned zero — which rules out the OR clause as the sole cause and points at the `site:linkedin.com/in` restriction itself.

The structurally similar `site:linkedin.com/company "Oman Airports Management Company"` query (used by `research/organization.py`'s `discover_linkedin_company_page`, unchanged by this pass since it demonstrably works) succeeded. The difference: individual LinkedIn profile (`/in/...`) pages are far more sparsely indexed by Google than company pages, and a heavily operator-constrained query against a thinly indexed target tends to come back completely empty on Serper's free tier rather than degrading gracefully to a broader match. This matches the acquisition design's own original intent (documented in `research/linkedin.py`'s module docstring): *"generic discovery dimensions... build a broad candidate pool first... then hand it to the future classification layer"* — i.e., broad queries + post-filtering, not a search-engine-side operator filter. The `site:`/quote/OR-heavy query style had drifted away from that design for the `/in/` breadth pass specifically.

## 3. Fix

### 3.1 Query construction (`research/linkedin.py`)
`_person_queries()` now builds **plain, unrestricted** queries — no `site:` operator, no exact-phrase quoting, no boolean OR (item 4):

- `general_workforce`: `"{company} linkedin"` (plus country, unquoted, when available) — was `site:linkedin.com/in "{company}"`.
- Each other dimension: one query per representative role term (capped at 2 terms per dimension — see `_MAX_TERMS_PER_DIMENSION`), each shaped `"{company} {term} linkedin"` — e.g. `"Oman Airports Management Company Chief Technology Officer linkedin"` — was a single big OR-joined clause.

`GENERIC_LINKEDIN_DIMENSIONS` changed shape from `(dimension, "ROLE OR ROLE OR ..."` string`)` to `(dimension, [role_term, role_term])` — a short list of plain representative terms per dimension, still fully generic business-function categories (no sector/organization knowledge).

This produces 13 total LinkedIn-person queries per organization (1 + 6×2), comparable to the pre-fix 7 and well inside the ~30-queries/run budget the Phase 1A provider validation tested Serper's free tier against for a full acquisition run.

**Deliberately left unchanged:** `discover_linkedin_company_page` in `research/organization.py` still uses `site:linkedin.com/company "<company>"` — it works (proven by the live run finding the company page), and rewriting a working query risks a regression for no benefit. Item 4's "preserve the existing Serper free-tier constraints" is applied here as *"the new queries must follow tested-safe patterns,"* not as a mandate to rewrite already-working queries elsewhere in the pipeline.

### 3.2 Post-filtering (item 5) — already present, unchanged
`discover_linkedin()`'s result-processing loop already inspects every returned URL's host/path directly (`"linkedin.com" not in host: continue` / `"/company/" in path` / `"/in/" in path`) regardless of which query produced it — this is exactly the "broad candidate pool, then post-filter" design the fix restores reliance on. No changes were needed here; the bug was entirely upstream, in what the queries asked for.

### 3.3 Localized LinkedIn hosts (item 6) — verified, not a separate bug
The host check `"linkedin.com" in host` is a substring match, so `om.linkedin.com`, `au.linkedin.com`, `uk.linkedin.com`, etc. were already recognized correctly (this is *why* the company page — on `au.linkedin.com` — resolved successfully even before this fix). The zero-candidate problem was entirely at the query-construction stage, never at host recognition. Tests below cover this explicitly rather than leaving it as an unverified assumption.

### 3.4 `/company/` vs `/in/` separation (item 7) — unchanged
Company-page and personal-profile classification remain two separate branches in `discover_linkedin()`'s processing loop, with distinct `source_type` and `extra["linkedin_kind"]` values. Not touched by this fix.

### 3.5 `tests/mock_provider.py` updated to match
The synthetic `MockSearchProvider` (used by `run_acquisition_test.py --mock`, `run_phase1c.py --mock`, and the acceptance-harness tests) detected LinkedIn-person queries by looking for the literal `site:linkedin.com/in` substring and extracted the company name from exact-phrase quotes — both of which the new plain query format no longer contains. Updated: (a) `_extract_quoted_company` now falls back to the text before the literal word "linkedin" when no quoted phrase is present (purely cosmetic — only feeds synthetic title/snippet text, never real matching logic); (b) the LinkedIn-person branch is now also triggered by `"linkedin" in query and "site:" not in query`. Without this, the mock/demo tooling itself would have silently kept reporting zero LinkedIn candidates forever, defeating its purpose as a regression check.

## 4. Tests (items 8, 9, 10)

New file `tests/test_linkedin_discovery_hardening.py`, 9 tests, using a generic placeholder organization (`"Meridian Ports Authority"`) and a synthetic, network-free `_StaticLinkedInProvider` stub (no live API calls, no fabricated "real" results):

- `test_in_urls_from_generic_results_are_retained_across_linkedin_subdomains` (items 5, 6, 8) — a mixed synthetic result set containing `/in/` URLs on `www.`, `om.`, `au.`, and `uk.linkedin.com` are all retained as personal-profile candidates; a non-LinkedIn URL is not.
- `test_company_page_url_in_generic_results_is_not_treated_as_a_profile_candidate` (item 7) — a `/company/` URL in the same result set becomes/confirms the company page and never a profile candidate.
- `test_already_resolved_company_page_is_not_overwritten_or_double_counted` — an upstream-resolved company page isn't clobbered or duplicated by a `/company/` URL re-appearing in generic search results.
- `test_all_seven_dimensions_generate_queries` (item 9) — `_person_queries` produces queries tagged with exactly the 7 expected dimension names, each query mentioning the organization.
- `test_all_seven_dimensions_process_returned_in_candidates` (item 9) — with a provider returning real-shaped `/in/` URLs for every query, every one of the seven dimensions ends up with at least one counted candidate, proving dimension-tagged processing works end to end.
- `test_zero_results_across_all_dimensions_is_still_reported_honestly` — the honest-failure case: a provider returning nothing still reports all 7 dimensions at count 0 (not missing keys) — this is what made the original bug diagnosable from the report in the first place, and must keep working.
- `test_person_queries_avoid_site_operator_quoting_and_boolean_or` (item 4) — direct assertion that no LinkedIn-person query contains `site:`, `"`, or ` OR `.
- `test_general_workforce_query_is_bare_and_includes_country_when_available` / `test_person_queries_work_without_a_country` — query-shape regression coverage.

## 5. Full suite (item 11)

```
71 passed in 2.23s
```
The previously-accepted 62 (H1 through H3.1) plus these 9 new tests. `pyflakes` across `research/`, `commercial/`, `tests/`, and all three CLI scripts: clean.

## 6. Regression checks

- `run_acquisition_test.py --mock` (all 3 test organizations): `linkedin_people` family now runs 13 queries per organization and returns non-zero counts across all 7 dimensions (e.g. Oman Airports Management Company fixture: `general_workforce: 8, executive_leadership: 14, technology_digital_infrastructure: 13, procurement_commercial: 11, operations_projects_engineering: 16, finance_strategy: 20, director_and_management_general: 11`) — confirming the fix and the updated mock tooling work together end to end. (These are synthetic mock counts, not real LinkedIn data — flagged as such by the harness's own `*** SYNTHETIC / MOCK RUN ***` banner.)
- `run_phase1c.py --mock`: runs clean end-to-end with the updated LinkedIn discovery.
- `before_after_demo.py`: unchanged output (that fixture builds its `Source` objects by hand, independent of `research/linkedin.py`'s query construction, so it was never affected).

## 7. What this fix does *not* claim

Per your explicit instruction, this is an acquisition fix, not a qualification-rule change — nothing in `research/analysis/qualification.py` or `commercial/` was touched, and the fix does not make a previously-zero result "look acceptable" by loosening any downstream threshold. It changes what is actually requested from the search provider so that real LinkedIn profile candidates have a chance to be found. Whether it produces real candidates against the actual Oman Airports LinkedIn footprint can only be confirmed with a live `SERPER_API_KEY` run on your side — this sandbox still has none configured. I'd want to see your next `run_phase1c.py --live` (or `run_acquisition_test.py --live`) output showing non-zero `linkedin_profile` sources before considering this fully closed against the real case.

Stopping here per your instruction. Not proceeding to Phase 1D.

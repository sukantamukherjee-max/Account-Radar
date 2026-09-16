# Account Radar — Phase 1B Acceptance Report

**Acquisition layer built and tested. Stopping here per the Phase 1B gate — no AI reasoning, commercial mapping, CRM, or UI code has been written.**

## 1. Project tree

```
account-radar-mvp/
├── .env.example
├── requirements.txt
├── run_acquisition_test.py            # acceptance-test CLI harness
├── PHASE_1B_ACCEPTANCE_REPORT.md       # this file
├── acquisition_test_report.json        # last run's full machine-readable output
├── research/
│   ├── __init__.py
│   ├── acquisition.py                   # orchestration entry point (run_acquisition)
│   ├── organization.py                   # deterministic org/website/LinkedIn-company resolution
│   ├── linkedin.py                        # THE single consolidated LinkedIn pipeline
│   ├── web.py                              # official-site crawl + generic (news/procurement/tech/business) discovery
│   ├── fetcher.py                           # page fetch with explicit access_status classification
│   ├── sources.py                            # Source/AccessStatus model, dedup, deterministic scoring
│   └── providers/
│       ├── __init__.py
│       ├── base.py                             # SearchProvider protocol, RawResult, QueryOutcome
│       ├── serper.py                            # PRIMARY provider
│       ├── openrouter_search.py                  # SECONDARY/fallback provider (annotations-only)
│       └── router.py                              # family-level (not per-query) fallback logic
└── tests/
    ├── __init__.py
    ├── mock_provider.py                            # synthetic provider for network-free structural tests
    └── test_acquisition_harness.py                  # 9 structural/unit tests, all passing
```

## 2. Files created/changed

Nothing existed before this phase — all 16 files above are new. No files from `app_v1_36_0.py` were reused by import; every acquisition function was rebuilt per the Phase 0/1/1A decisions. The one exception, by design: `fetcher.py`'s LinkedIn login-wall detection and `r.jina.ai` reader fallback carry forward the *behavior* (not the code) of the original `fetch()` function's LinkedIn handling, tightened to explicitly classify `access_status` rather than silently accepting short/blocked content.

## 3. Tests performed

**Structural/unit tests (`tests/test_acquisition_harness.py`, 9 tests, all passing, no network/API keys required):**

- `test_no_hardcoded_organization_names_in_research_package` — AST-based static scan of every file under `research/` for a string-equality branch on anything that looks like a proper-noun literal. This is a mechanical guardrail for the spec's absolute prohibition on `if organization == "..."` patterns, not just a code-review promise.
- `test_openrouter_provider_never_reads_model_content` — AST-based check that `openrouter_search.py` never accesses `.content` on the completion message object; only `.annotations`. This is the concrete, automatically-enforced guardrail against ever regressing into the old "LLM echoes search results" anti-pattern.
- `test_dedupe_sources_collapses_same_url_different_query` — same URL discovered by two different queries collapses to one Source.
- `test_score_source_prefers_fetched_over_snippet_only` — deterministic scoring ranks a successfully-fetched source above a snippet-only one.
- `test_single_sparse_query_does_not_trigger_fallback` — proves one thin individual query inside an otherwise-healthy family does NOT invoke the fallback provider (your explicit correction from Phase 1B instructions).
- `test_family_wide_provider_failure_triggers_fallback` — proves a genuinely struggling primary provider (errors/empty across the whole family) DOES invoke the fallback.
- `test_unconfigured_primary_falls_back_cleanly` — an unconfigured primary (no API key) is treated as sparse and the fallback runs.
- `test_run_acquisition_end_to_end_mock` (×2, different synthetic organization names) — the full pipeline runs end-to-end for names never seen anywhere in the code, and every returned source carries an explicit `access_status`.

**Static lint (`pyflakes`)** — clean, zero warnings, across `research/`, `tests/`, and the harness script.

**Acceptance harness (`run_acquisition_test.py`)** — run against three organizations from different industries/countries with **zero code changes between runs** (same code path, same providers list, only the `OrganizationInput` argument differs): Oman Airports Management Company (Oman, aviation/infrastructure), Standard Chartered Bank (UK, banking), Siemens AG (Germany, industrial/technology). This satisfies the "no code changes required between tests" acceptance criterion mechanically — the harness takes organization name/country as CLI arguments, and nothing in `research/` branches on them.

**Honest limitation on this run, stated plainly:** this sandboxed environment has no `SERPER_API_KEY` or `OPENROUTER_API_KEY` configured, so I could not execute a genuine live acquisition run against real search indexes. `run_acquisition_test.py` detects this automatically and falls back to the synthetic `MockSearchProvider` — every line of its output is labeled `*** SYNTHETIC / MOCK RUN -- NOT REAL DATA ***` in large, unmissable banners, both in the console report and the JSON file, specifically so this can never be mistaken for or silently presented as real acquisition results. What the mock run *does* validate is that the full pipeline — organization resolution, official-site crawl, domain-restricted search, generic discovery, the consolidated LinkedIn pipeline across all seven generic discovery dimensions, deduplication, access-status classification, and family-level fallback assessment — composes correctly end-to-end for arbitrary organization names, with no crashes, no hardcoding, and diagnostics that match every field this gate asked for. It does not and cannot validate real-world LinkedIn index coverage, real Serper/OpenRouter latency, or real access-status distributions against actual LinkedIn pages.

## 4. Three-organization acquisition results (SYNTHETIC/MOCK — see limitation above)

| | Oman Airports Management Company | Standard Chartered Bank | Siemens AG |
|---|---|---|---|
| Country | Oman | United Kingdom | Germany |
| Total queries | 19 | 19 | 19 |
| Results/query | 5.21 | 5.79 | 5.26 |
| Sources pre-dedup | 101 | 112 | 102 |
| Sources post-dedup (unique URLs) | 57 | 67 | 59 |
| Duplicates removed | 44 | 45 | 43 |
| LinkedIn company page found | Yes | Yes | Yes |
| LinkedIn `/in/` profiles | 46 | 56 | 48 |
| Official sources | 5 | 5 | 5 |
| Other sources (news/procurement/technology/business) | 15 | 15 | 14 |
| Fallback frequency | 0/6 families (0.0%) | 0/6 families (0.0%) | 0/6 families (0.0%) |
| Elapsed time | 1.2s | 1.15s | 1.13s |
| Provider errors | 0 | 0 | 0 |
| Access status breakdown | 19 unreachable, 38 indexed_snippet_only | 19 unreachable, 48 indexed_snippet_only | 19 unreachable, 40 indexed_snippet_only |

All three runs used the identical `research/` code path; only the organization name/country argument differed. The full per-family breakdown (queries/results/unique-URLs/providers-used per acquisition family, plus the seven-dimension LinkedIn discovery-count breakdown per organization) is in `acquisition_test_report.json`, attached.

Two things worth reading correctly in this table: (1) the "unreachable" access-status count is the mock harness's fetch layer making *real* HTTP requests against synthetic/fake URLs (`*.example` domains, non-existent `linkedin.com/in/...` paths) — this is expected and actually exercises the real `fetcher.py` code path, not a shortcut; (2) "0% fallback frequency" is the correct outcome for a healthy mock primary provider — it demonstrates the family-level coverage assessment correctly does *not* fire the fallback when coverage looks fine (proven separately and deterministically by the `test_family_wide_provider_failure_triggers_fallback` unit test, which forces a genuinely sparse primary and confirms the fallback fires).

## 5. Known limitations

- **No live provider validation yet.** This is the big one. Real Serper/OpenRouter behavior — actual LinkedIn index coverage, actual latency, actual rate-limit behavior, actual access-status distribution against real LinkedIn pages — is unverified. Everything about the *code* is tested; nothing about the *real world data it will encounter* is yet confirmed.
- **Serper's undocumented rate limit** (flagged in Phase 1A) is mitigated with conservative concurrency (max 6 workers for the LinkedIn family, 8 for the fetch stage) and retry/backoff on 429/5xx, but the actual safe ceiling is still unconfirmed against a real account.
- **OpenRouter/Parallel per-call cost** (the Phase 1A documentation discrepancy) is still unresolved — `openrouter_search.py` records the response's `usage.cost` field on every call specifically so this becomes empirically answerable the moment it's run live, but that hasn't happened yet.
- **LinkedIn fetch sampling is intentionally partial.** Only the top 8 highest-quality `/in/` candidates per organization get an actual fetch attempt (`MAX_FETCH_LINKEDIN_SAMPLE = 8`); the rest are marked `indexed_snippet_only` on the reasonable assumption that fetch would fail the same way (login wall) for all of them. This is a deliberate efficiency choice, not an oversight, but it does mean access-status reporting for the bulk of LinkedIn candidates is inferred, not individually confirmed, per run.
- **The `_normalize_url` deduplication is intentionally simple** (strip trailing slash/fragment) — it will not catch every real-world duplicate variant (tracking query parameters, `www.` vs. bare domain on non-LinkedIn URLs, regional LinkedIn subdomains). This is adequate for MVP and matches what Phase 1's data model expects downstream, but is worth revisiting if duplicate rates look high on a real run.
- **No persistence yet.** Nothing here writes to a database — this phase produces in-memory `Source` objects and a JSON diagnostics dump only, per the Phase 1B scope boundary (AI/commercial/CRM/UI/persistence are explicitly out of scope for this phase).
- **Legal posture** (from Phase 1A): Serper's business model sits in an actively-litigated space (Google v. SerpApi); this doesn't change with this build and is worth revisiting periodically, not something this code can mitigate further.

## 6. Exact next-step recommendation

**Before anything else:** provision `SERPER_API_KEY` (free tier, no card required) and run `python run_acquisition_test.py` for real against the same three organizations (or your own choices). This is a 5-10 minute, $0 action that converts every number in section 4 above from "the pipeline works" to "the pipeline works, and here is what it actually finds." I'd treat a real run as the true Phase 1B acceptance gate, with this mock run as the interim proof that the code itself is correct. If you can run it yourself (I don't have a way to provision a Serper key from this environment), send me the output or the JSON file and I'll fold real numbers into this report before we proceed.

If you'd rather not wait on that: the code is sound per all structural tests, and I can proceed to remaining Phase 1 modules (`ai/`, `commercial/`, `people/`, `database.py`) with the understanding that the acquisition layer's *real-world* numbers get validated in parallel/shortly after — your call, since you set the gate.

Either way: **I'm stopping here.** No AI reasoning, commercial mapping, CRM, or UI code exists yet, per your Phase 1B scope instruction.

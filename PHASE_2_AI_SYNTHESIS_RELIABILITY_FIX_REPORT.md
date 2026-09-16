# Phase 2 — AI Synthesis Reliability Fix: Implementation Report

Implements the Section E fixes approved after `PHASE_2_AI_SYNTHESIS_DIAGNOSTIC_REPORT.md`,
under the explicit constraints: no weakened grounding, no unsupported URL ever becomes
a valid citation, no removal of evidence safeguards, no acceptance of arbitrary
non-JSON/wrong-shaped output as final, no organization/sector/country-specific
behavior anywhere.

---

## A. Files changed

- `ai/prompt.py` — evidence block capped/separated (item 1); instructions text updated to explain the split.
- `ai/client.py` — `AIResponse.ok` now requires a JSON object (item 2); `max_tokens` now scales with prompt size within a fixed floor/ceiling (item 4).
- `ai/synthesis.py` — bounded (exactly-one) schema-repair retry added (item 3).
- `tests/test_ai_reliability_hardening.py` — **new**, 15 tests (item 7 / Section F).

Nothing else was touched: `ai/schema.py`, `ai/models.py`, `commercial/*`, `research/analysis/*`, `ui/*`, `app.py` are byte-for-byte unchanged. No evidence classification, evidence_category, inference_cluster_id, epistemic_status, capability mapping rule, opportunity scoring rule, LinkedIn safeguard, incumbent-separation rule, organization-association rule, or citation-grounding-eligibility rule was modified.

---

## B. Exact fixes

**1. Prompt size / evidence block (`ai/prompt.py`)**

`_capability_evidence_block()` now emits, per capability, two clearly labeled sections:

- `ELIGIBLE TO CITE (direct_evidence / strong_inference -- citable for this capability):` — **every** `direct_evidence` and `strong_inference` mapping, uncapped, unchanged from before.
- `CONTEXT ONLY -- DO NOT CITE (weak_relevance):` — capped at 5 lines per capability (`_MAX_CONTEXT_ONLY_LINES_PER_CAPABILITY = 5`), with an explicit line stating how many more were omitted and re-stating that grounding checks the *complete* evidence set regardless of what is shown.

`_INSTRUCTIONS` was extended to name the two sections explicitly and to state plainly that the top-level response "must be a JSON object and nothing else" (reinforcing, in-prompt, the shape the schema will enforce).

Grounding (`ai/synthesis.py`) was **not touched** — `_ground_and_build_result()` still builds its `evidence_index` from `report.capability_mappings` (the complete, deterministic set), never from the prompt text. A test proves this explicitly (see F.9 below): a weak URL that is capped *out* of the prompt entirely is still correctly rejected if an AI response happens to cite it.

**2. `AIResponse.ok` / fallback (`ai/client.py`)**

```python
@property
def ok(self) -> bool:
    return self.error is None and isinstance(self.parsed_json, dict)
```

A bare array, string, number, or boolean — even though `json.loads()` parsed it without error — is no longer treated as a usable response. `complete_json()`'s existing primary→fallback model loop was already structured to keep trying the next model whenever `.ok` is `False`; fixing `.ok` is what lets that loop actually reach the fallback model (`openai/gpt-4.1-nano` by default) when the primary model (Mercury) returns valid-but-wrong-shaped JSON — which is exactly the failure mode diagnosed for Standard Chartered. No schema validation was changed; this is purely a client-level "was this response usable at all" fix.

**3. Bounded schema-repair retry (`ai/synthesis.py`)**

```python
schema_ok, schema_reason = validate_against_schema(response.parsed_json)
if not schema_ok:
    repaired = _attempt_schema_repair(client, prompt, schema_reason)
    if repaired is None:
        return SynthesisResult(status=AI_FAILED, ...)   # unchanged final outcome
    result = _ground_and_build_result(report, repaired.parsed_json, model=repaired.model)
    result.notes.insert(0, f"initial AI response failed schema validation ({schema_reason}); "
                            f"recovered via one bounded schema-repair retry")
    return result
```

`_attempt_schema_repair()` sends the **same** prompt plus one short, generic corrective instruction ("CORRECTION REQUIRED... return ONLY a single JSON object with exactly these three top-level keys...") and validates the retried response with the **exact same, unweakened** `validate_against_schema()`. If the retry also fails — for any reason, non-dict or otherwise — the result is `ai_failed`, identical to today's behavior. There is never a second retry. This applies to non-dict responses too: a non-dict `parsed_json` with `error=None` now passes the earlier `response.error is not None or response.parsed_json is None` gate (which only screens out genuine transport/parse failures) and reaches `validate_against_schema()`, which correctly reports `"top-level response is not a JSON object"` and triggers the one retry.

**4. `max_tokens` (`ai/client.py`)**

```python
def _estimate_max_tokens(prompt: str) -> int:
    capability_count = prompt.count("- capability_id:")
    evidence_line_count = (prompt.count("[direct_evidence]") + prompt.count("[strong_inference]")
                            + prompt.count("[weak_relevance]"))
    budget = 2000 + capability_count * 90 + evidence_line_count * 12
    return max(2000, min(6000, budget))
```

`max_tokens=2000` (fixed) is replaced with this estimate, computed purely from the prompt's own size/shape (never from organization/sector/country identity), bounded to `[2000, 6000]`. The prior fixed value is preserved as the floor, so small accounts are unaffected; larger accounts now get a proportionally larger completion budget, capped so it can never grow without bound.

**Item 5 (grounding) and item 6 (strict `json_schema` mode):** grounding was deliberately left untouched (see item 1 above and F.9's test). Strict `json_schema`/`strict: true` mode was **not** implemented — this sandbox has no live OpenRouter/Mercury access to verify the route reliably supports it, and the approved instructions explicitly say to leave it as a documented future enhancement if uncertain (see Section H).

---

## C. Test results

Run with the real `pytest` (9.0.3), not the hand-written substitute used earlier in this engagement — a working `pytest` binary was found in this sandbox this time (`/root/.local/bin/pytest`), so this run is real, standard pytest, not a stand-in:

```
$ PYTHONPATH=/root/.local/lib/python3.11/site-packages:/usr/local/lib/python3.11/dist-packages pytest -q
........................................................................ [ 51%]
...................................................................      [100%]
139 passed in 3.61s
```

All 124 pre-existing tests pass unchanged (no assertion was edited, weakened, or deleted), plus the 15 new tests below.

## D. New test count

**139 total** (124 existing + 15 new), all passing. The 15 new tests, in `tests/test_ai_reliability_hardening.py`:

1. `test_capability_evidence_block_separates_eligible_and_context_only`
2. `test_all_eligible_evidence_remains_represented_at_large_account_scale`
3. `test_prompt_size_stays_bounded_as_weak_evidence_volume_grows`
4. `test_estimate_max_tokens_scales_within_bounds`
5. `test_openrouter_client_sends_prompt_size_scaled_max_tokens_to_the_api`
6. `test_ai_response_ok_requires_dict_parsed_json`
7. `test_primary_model_non_dict_response_triggers_fallback_model`
8. `test_both_models_returning_non_dict_json_leaves_response_not_ok`
9. `test_schema_repair_retry_succeeds_on_second_attempt`
10. `test_schema_repair_retry_fails_both_attempts_remains_ai_failed`
11. `test_schema_repair_retry_is_attempted_exactly_once`
12. `test_schema_repair_retry_not_attempted_for_transport_or_parse_failure`
13. `test_weak_only_evidence_url_is_still_rejected_by_grounding_after_prompt_capping`
14. `test_grounding_rejects_weak_citation_even_when_omitted_from_the_capped_prompt`
15. `test_hallucinated_source_url_still_stripped_after_prompt_capping`

---

## E. Before/after prompt size (large synthetic corpus)

Same shape used in the diagnostic report (5 capabilities × 3 direct + 2 strong + 9 weak = 70 mappings, matching Standard Chartered's real scale), measured directly against the code before and after this fix:

| Corpus | Before (uncapped) | After (capped/separated) | Reduction |
|---|---|---|---|
| Real-account scale (70 mappings) | 26,303 chars (~6,575 tokens) | 22,238 chars (~5,559 tokens) | 15.5% |
| Stress test (1,025 mappings, 200 weak/capability) | 335,758 chars (~83,939 tokens) | 22,248 chars (~5,562 tokens) | 93.4% |

The real-account-scale reduction is modest because that shape only had 9 weak-evidence items per capability to begin with (capping at 5 removes less than half of them); the stress test shows the actual point of the fix — the prompt no longer grows unboundedly with corpus size, because eligible evidence (the only evidence that ever needed to scale) is what's uncapped, and everything else is capped.

`max_tokens` for the 70-mapping case is now **3,050** (computed from that prompt's own capability/evidence-line counts), versus the previous fixed **2,000** — more completion room for a case with real output to produce (2 account-level fields plus up to 5 capability narratives plus up to 5 actions), while an enormous corpus is still capped at 6,000, never unbounded.

---

## F. Before/after live results for the three accounts

**Could not be run in this sandbox** — no `OPENROUTER_API_KEY` or `SERPER_API_KEY` is configured here (confirmed: both unset), the same constraint noted in the earlier diagnostic report. The original live results you reported were produced on your own Windows machine; that is where this fix needs to be re-validated against real data. I am not claiming success from the test suite alone — the acceptance criteria you set (Standard Chartered no longer failing on shape, fewer dropped citations for Oman/Siemens) can only be confirmed against the real API.

**To re-run the same three-account comparison on your machine:**

```
python run_phase1d.py --org "Oman Airports Management Company" --country "Oman"
python run_phase1d.py --org "Standard Chartered Bank" --country "United Kingdom"
python run_phase1d.py --org "Siemens AG" --country "Germany"
```
(or `python run_phase1d.py` with no args, which runs all three default test organizations together, exactly as your original live run did), with `OPENROUTER_API_KEY` and `SERPER_API_KEY` set as before.

**What to check in the output, per account, against your original run:**

- **Standard Chartered** — `AI synthesis status` should no longer be `ai_failed` with `"top-level response is not a JSON object"`. If it recovered via the repair retry, a note will explicitly say `"initial AI response failed schema validation (...); recovered via one bounded schema-repair retry"`. If it recovered via the fallback model instead (or the retry wasn't even needed because the larger `max_tokens`/smaller prompt let the primary model succeed outright), you'll see a normal `ok`/`degraded` result with no such note.
- **Oman Airports / Siemens** — compare the `dropped N unsupported/ineligible cited_source_url(s) for capability X` notes against your original counts (8/6/4 for Oman). A reduction is expected because the prompt now visually separates citable from non-citable evidence instead of interleaving all three tiers under one inline tag, but grounding is unchanged, so any citation the model still gets wrong will still be dropped exactly as before — dropped-citation notes appearing at all is not itself a regression.
- **All three** — qualified source counts, capability mapping counts, and opportunity hypothesis counts should be **identical** to your original run (nothing upstream of the AI layer was touched); only the AI-synthesis-stage numbers (status, dropped citations, surviving narratives/actions) should differ.

---

## G. Remaining degraded AI runs

Cannot be predicted with certainty without a live re-run (per F), but by design, some `degraded` results should still be expected and are **not** a sign the fix failed:

- Any citation the model proposes that is genuinely not eligible (wrong capability, wrong evidence tier, or fabricated entirely) is still stripped by grounding, unchanged — a `degraded` status from real dropped citations is grounding doing its job, not a residual bug.
- On a sufficiently large/complex account, the primary model could still occasionally emit a wrong-shaped response through both configured models *and* the one bounded repair retry; that remains an honest `ai_failed`, never a fabricated success — this fix reduces the odds of that outcome (smaller/clearer prompt, more completion budget, two extra recovery chances) but cannot claim to eliminate it, especially for a diffusion-architecture model whose structured-output reliability under complex prompts is inherently less proven than an autoregressive model's.
- The exact reduction in Oman/Siemens's dropped-citation counts is a live-data question this sandbox cannot answer (see F).

---

## H. Remaining limitations

1. **Live validation is outstanding** (Section F) — this sandbox has no configured API keys and no way to reach your OpenRouter/Serper accounts; the acceptance criteria you set can only be confirmed on your machine.
2. **Strict `json_schema`/`strict: true` mode (item 6) was deliberately not implemented.** Verifying whether the Mercury/OpenRouter route reliably honors it requires a live call this sandbox cannot make; per your instructions, this is left as a documented future enhancement rather than guessed at. If you'd like, a safe next step would be a one-off live probe on your machine (send one real request with `response_format={"type": "json_schema", ...}` to the Mercury route and confirm it's honored, not silently ignored) before considering it for implementation.
3. **`_estimate_max_tokens()` is a structural proxy, not an exact token count.** It counts capability/evidence-line markers in the prompt text rather than running a real tokenizer, so it can be somewhat over- or under-generous for unusual evidence content (e.g. unusually long `supporting_context` snippets). It is bounded either way (2,000–6,000), so it can never regress to worse than the old fixed value or grow unbounded.
4. **The weak-evidence cap (5 lines per capability) is a fixed generic constant**, not adaptively tuned per account size — chosen to keep every capability's context section small and legible without making it organization- or sector-specific. If live validation shows it's too aggressive or not aggressive enough, it's a one-line constant to revisit (`ai/prompt.py::_MAX_CONTEXT_ONLY_LINES_PER_CAPABILITY`).
5. **The repair retry roughly doubles worst-case latency/cost for the (hopefully now rarer) shape-failure case** — one extra full round-trip to the same model(s) only when the first response fails schema validation. This trade-off (a slower but successful result vs. an immediate `ai_failed`) was explicit in your instructions and is bounded to exactly one extra attempt, never more.
6. **This sandbox's own test verification changed tooling mid-engagement**: earlier phases in this project used a hand-written pytest-compatible substitute because no real `pytest` could be found; this round, a real `pytest` (9.0.3) was available and used for all 139 tests, which is a strictly more reliable verification than before — but it's still `pytest` running in this cloud sandbox, not on your machine, so a `pytest -q` run on your own machine remains a good independent check.

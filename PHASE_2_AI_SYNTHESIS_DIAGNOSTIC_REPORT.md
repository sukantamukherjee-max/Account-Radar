# Phase 2 Hardening — AI Synthesis Live-Run Diagnostic Report

**Scope:** Why the OpenRouter/Mercury synthesis call degrades (Oman Airports, Siemens)
or fails schema validation outright (Standard Chartered) against real account data,
despite a minimal Mercury request returning valid JSON.

**Status:** Diagnostic only. No code was modified to produce this report.

---

## A. Root cause(s)

Two independent, generic (organization-agnostic) mechanisms are responsible, and they
explain all three observed outcomes:

**A1 — The prompt's evidence block is uncapped and undifferentiated, so it grows
linearly with corpus size and mixes citable and non-citable evidence in one list.**
`_capability_evidence_block()` in `ai/prompt.py` emits one line per
`CapabilityMapping`, for *all* mappings, with no cap — unlike `_signals_block()` and
`_incumbent_block()` in the same file, which are explicitly capped at 20 entries "to
keep the prompt compact." For a Standard-Chartered-scale report (135 qualified
sources, 74 mappings) this produces a long, repetitive, mostly `weak_relevance` prompt
in which citable (`direct_evidence`/`strong_inference`) and non-citable
(`weak_relevance`) URLs for the same capability sit in the same list, distinguished
only by a text label. This is a harder structured-generation task than the minimal
test, and is the direct cause of both the over-citation observed for Oman Airports
(investigation point 8) and the outright shape failure for Standard Chartered
(investigation point 1).

**A2 — `response_format={"type": "json_object"}` only guarantees syntactically valid
JSON, not the required object shape — and the client has no repair path when that
weaker guarantee is all that's honored.** `ai/client.py`'s `_try_model()` requests
OpenAI-API "JSON mode," which constrains the model to emit *some* valid JSON value,
not necessarily a JSON *object* matching the three required top-level keys. Under a
harder prompt (A1), the model is free to emit a differently-shaped-but-still-valid
JSON value (most consistent with the observed error, a non-dict top-level value — see
B2). The current pipeline has no step that requests a shape correction when this
happens; it fails once and stops.

A contributing structural factor makes A1+A2 look like two different failure classes
(graceful degrade vs. hard failure) rather than one problem at two severities — see D3.

---

## B. Evidence from the code

**B1 — No cap on the evidence block (`ai/prompt.py`):**
```python
def _signals_block(report):
    for s in report.signals[:20]:      # capped, "keep the prompt compact"
        ...
def _incumbent_block(report):
    for i in report.incumbent_evidence[:20]:   # capped
        ...
def _capability_evidence_block(report):
    for capability_id, bucket in summary.items():
        for level in ("direct_evidence", "strong_inference", "weak_relevance"):
            for m in bucket[level]:            # NOT capped — every mapping, every level
                lines.append(...)
```
Measured empirically against an unmodified copy of the code (no files edited to
produce this measurement): a synthetic report shaped like Standard Chartered's real
scale (70 mappings: 3 direct + 2 strong + 9 weak per capability × 5 capabilities)
produces a `build_synthesis_prompt()` output of **28,286 characters (~7,071 tokens)**,
of which the capability-evidence block alone is 70 lines, all three evidence tiers
interleaved per capability.

**B2 — The exact reported error string traces to one specific check
(`ai/schema.py`):**
```python
def validate_against_schema(payload: object) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "top-level response is not a JSON object"
```
This is the only place in the codebase that produces the string
`"top-level response is not a JSON object"` reported for Standard Chartered
(`ai/synthesis.py` line 58: `f"AI response failed schema validation: {schema_reason}"`).
Reaching this line requires that `response.ok` was already `True` at the client
level — i.e. `json.loads()` succeeded and produced *some* non-`None` Python value —
but that value is not a `dict`. This rules out a parse failure (which would surface as
`"could not parse response as JSON"` from `ai/client.py` instead) and confirms the
model returned syntactically valid JSON of the wrong top-level shape (e.g. a bare
array, or a JSON value nested one level differently than requested).

**B3 — The client's own fallback-model safety net cannot fire on this exact failure
mode, because `AIResponse.ok` treats "non-None" as "ok":**
```python
@property
def ok(self) -> bool:
    return self.error is None and self.parsed_json is not None
```
```python
def complete_json(self, prompt, *, schema_name):
    models_to_try = [self._model] + ([self._fallback_model] if self._fallback_model else [])
    for model in models_to_try:
        response = self._try_model(model, prompt)
        if response.ok:
            return response          # <-- stops here
        last_error = response.error
    return AIResponse(error=last_error or "all configured models failed")
```
`_try_model()` sets `parsed_json = parsed` whenever `json.loads()` succeeds,
regardless of whether `parsed` is a dict, list, string, or number. So for Standard
Chartered, the primary model's malformed-shape-but-valid-JSON response is `.ok is
True` at the client, `complete_json()` returns immediately, and
`DEFAULT_FALLBACK_MODEL` ("openai/gpt-4.1-nano") is never tried — even though the
client was explicitly built with a fallback for exactly this kind of situation
("a fast/cheap primary with a fallback if it's unavailable or errors," per the
module's own docstring). The shape mismatch is only discovered one layer up, in
`ai/synthesis.py`, by which point `complete_json()` has already returned and no
retry or fallback is attempted. This is a generic client-level gap (not
account-specific) and is the most direct explanation of investigation point 7: why
Standard Chartered fails outright with zero recovery while Oman Airports/Siemens
recover (partially) via a *different* safety net — deterministic grounding, which sits
downstream of schema validation and never gets a chance to run once schema validation
itself has already rejected the whole payload.

**B4 — Grounding degrades gracefully by design, but only once schema validation has
already passed; it cannot rescue a schema failure.** `ai/synthesis.py`'s
`_ground_and_build_result()` drops individual ungrounded `cited_source_urls` per
capability and records a note (`degraded`), which is exactly what produced Oman
Airports' "8/6/4 dropped" and Siemens' partial narrative survival. But this function
is only reached if `validate_against_schema()` already returned `True`
(`synthesize_account()`, lines 52–61). For Standard Chartered the pipeline never
reaches grounding at all — it fails one stage earlier, which is why that account gets
a hard `ai_failed` instead of a `degraded` result with per-URL drop notes.

**B5 — `max_tokens=2000` is a fixed constant, independent of corpus/evidence
volume** (`ai/client.py`, `_try_model()`). For a 70-evidence-line prompt asking for up
to 3 `next_best_actions` plus one `capability_narratives` entry per eligible
capability plus `cited_source_urls` arrays, 2000 completion tokens is a plausible but
not generous budget; a truncated response would independently produce invalid JSON
(missing closing brackets) and would surface via the `"could not parse response as
JSON"` path in `ai/client.py`, not the observed `"top-level response is not a JSON
object"` path. This does not appear to be what happened in the three observed runs
(the error signature points to B2, not to a truncation/parse failure), but it remains
a real, unmeasured risk for even larger accounts and should be closed as a matter of
robustness (see E3).

---

## C. Why the minimal Mercury test succeeds

The user's minimal test (`{"test":"ok"}`) is a trivial single-key completion with a
short, non-repetitive prompt. Under `response_format={"type": "json_object"}`,
producing a small flat object is close to the model's default behavior for "return
JSON" — there is very little structural ambiguity for the model to resolve, so
`json_object` mode's weaker guarantee (syntactic JSON validity only, not shape) is
sufficient in practice. This test correctly demonstrates connectivity, auth, model
availability, and basic JSON-mode compliance; it does not and cannot exercise shape
fidelity under a large, repetitive, multi-array, multi-constraint prompt, which is a
substantially harder structured-generation task, especially for a diffusion-style
model whose generation dynamics differ from standard autoregressive models and whose
structured-output reliability under long/complex prompts is comparatively less proven.

## D. Why full Account Radar requests fail/degrade

1. **Prompt complexity scales with corpus size** (B1) — evidence block size, evidence
   heterogeneity (3 interleaved tiers), and required-output complexity (nested arrays,
   cross-referencing capability_ids and URLs) all grow with account size, with no cap.
2. **`json_object` mode does not enforce the required top-level shape** (A2) — so as
   prompt complexity rises, the probability the model emits *some* valid-but-wrong-shaped
   JSON rises with it. This is consistent with the severity ordering observed:
   Standard Chartered (135 sources / 74 mappings, the largest by qualified-source
   count) is the one that produced a wrong-shaped payload; Siemens, despite more raw
   mappings (82), degraded rather than failed outright, and Oman Airports (32 sources
   / 29 mappings, the smallest) degraded least severely. Source/evidence *volume and
   repetitiveness*, not mapping count alone, is the better predictor — consistent with
   B1's prompt-size mechanism.
3. **The failure modes look categorically different (a clean pass/fail split) only
   because schema validation is all-or-nothing and sits upstream of the graceful
   grounding degrade path** (B4) — a model that gets the shape 95% right on a hard
   prompt is rejected exactly as completely as one that gets it 5% right. There is no
   partial credit at the schema layer, only at the grounding layer, and the grounding
   layer is unreachable once schema validation fails.
4. **The one designed recovery path for a bad primary-model response — falling back
   to `openai/gpt-4.1-nano` — cannot trigger for this specific failure mode** (B3),
   because `AIResponse.ok` does not check that the parsed JSON is a dict before
   declaring the primary model's response "ok" and returning it. The fallback exists
   in the code but is unreachable for exactly the failure the user is seeing.
5. **The over-citation seen for Oman Airports (investigation point 8)** is best
   explained by the same root cause as the shape failure, at lower severity: the
   undifferentiated evidence block (B1) asks the model to compose per-capability
   `cited_source_urls` arrays while reading a list that visually interleaves eligible
   (direct/strong) and ineligible (weak) URLs for the same capability, distinguished
   only by an inline text tag rather than any structural separation. This is a
   citation-selection task the model is getting partially wrong at scale; grounding
   (B4) is correctly catching and dropping every ineligible URL it proposes — no
   unsupported URL reached the user in either surviving case — but the volume of
   dropped citations shows the model is guessing rather than reliably discriminating.

**None of this indicates the grounding or schema-validation safeguards are
misbehaving.** Both are working exactly as designed: schema validation is correctly
rejecting a malformed response, and grounding is correctly stripping every
unsupported citation it's given the chance to see. The gap is entirely upstream —
in how much and how the evidence is presented to the model, and in how few chances
the pipeline gives a capable-but-imperfect model to get the shape right.

---

## E. Recommended fix (described only — not implemented)

Consistent with the stated constraints: no weakening of grounding, no acceptance of
unsupported URLs as citations, no removal of evidence safeguards, no acceptance of
non-JSON/wrong-shaped output as final, and nothing organization-specific. All of the
following operate purely on corpus *size/shape*, which is generic across any account.

1. **Cap and structurally separate the evidence block, the same way `_signals_block`
   and `_incumbent_block` already do.** Always include *all* `direct_evidence` and
   `strong_inference` mappings per capability (these are the only ones eligible to be
   cited — omitting any would be a real information loss), but cap `weak_relevance`
   entries per capability (e.g. to a small fixed number, or omit them from the
   citable-evidence listing entirely and instead give the model only an aggregate
   count/summary of weak evidence per capability, since weak evidence is explicitly
   "context only, never cite" already). This directly shrinks and simplifies the
   hardest part of the prompt without touching what evidence is available for citation
   or grounding — grounding continues to validate against the full, uncapped
   `report.capability_mappings`, unchanged.
2. **Structurally separate "citable" from "context-only" evidence in the prompt
   text**, e.g. two clearly labeled sub-lists per capability ("ELIGIBLE TO CITE" vs.
   "CONTEXT ONLY — DO NOT CITE") instead of one interleaved list distinguished only by
   an inline tag. This is a prompt-clarity change, not a grounding change — the actual
   enforcement of what may be cited continues to happen entirely in
   `ai/synthesis.py`'s grounding step, unchanged and unweakened.
3. **Scale `max_tokens` to the evidence volume actually included in the prompt**
   (e.g. a base allowance plus a per-eligible-capability increment, with a sane cap),
   so output truncation risk doesn't grow silently with account size. This is a pure
   capacity change with no effect on what is accepted as valid.
4. **Add a bounded, generic schema-repair retry**, entirely inside the existing
   fail-closed contract: if `validate_against_schema()` rejects a response specifically
   because the top-level payload is not a dict (or is a dict missing required keys),
   issue exactly one corrective follow-up call — same prompt plus a short, generic
   instruction such as "Your previous response was not a JSON object with the three
   required top-level keys; return ONLY the JSON object in the exact shape specified" —
   and validate/ground that response through the identical unweakened path. If the
   retry also fails validation, the result is `ai_failed` exactly as today; nothing
   about the acceptance criteria changes, only the number of chances given to reach a
   validatable shape. This directly addresses the fallback-model gap in B3 as well:
   the repair retry (or a corrected fallback condition) should be tried before giving
   up, not only a same-model repeat.
5. **Fix `AIResponse.ok` (or the `complete_json` loop) so a non-dict `parsed_json` is
   not treated as a usable response**, so the existing primary→fallback-model
   mechanism that was already designed into the client actually gets a chance to run
   for this failure mode, before or alongside the repair-retry in item 4. This is
   restoring an existing designed safeguard, not adding new leniency.
6. **Consider requesting the stronger `{"type": "json_schema", "json_schema": {...},
   "strict": true}` response-format mode** (rather than `json_object`) for models/
   routes that support grammar-constrained structured output, with the current
   `json_object` behavior kept as the automatic fallback for models that reject or
   ignore the stricter mode. This would shrink the space of wrong-shaped-but-valid
   responses at the source, on top of (not instead of) items 1–5, and requires no
   change to schema semantics, grounding, or citation rules — only which response-format
   parameter is sent to a given model.

None of items 1–6 loosens `ai/schema.py`'s validation, loosens `ai/synthesis.py`'s
grounding, allows any URL to be cited that isn't already present under that
capability's real deterministic evidence, or branches on organization identity —
every change is a function of corpus size/shape and model-response shape only.

---

## F. Tests that should be added

1. **Prompt-size regression guard:** given a synthetic report with N mappings per
   capability (parametrized across small/medium/large, e.g. 5/30/70), assert
   `build_synthesis_prompt()` output stays under a defined size ceiling once evidence
   capping (E1) is implemented — this is the direct regression test for the uncapped
   growth documented in B1.
2. **Citable vs. context-only separation is structural, not just prose:** assert the
   prompt text places `weak_relevance` evidence in a clearly distinct section/marker
   from `direct_evidence`/`strong_inference` evidence for the same capability (once
   E2 is implemented), rather than only checking for an inline `[weak_relevance]` tag.
3. **All eligible evidence is still represented after capping:** assert every
   `direct_evidence` and `strong_inference` mapping still appears verbatim (capability
   id + source URL) in the prompt regardless of corpus size, and that capping only
   ever reduces `weak_relevance` line count — a regression guard against accidentally
   capping the wrong tier.
4. **`AIResponse.ok` / fallback-model dispatch:** a fake client stage returns
   `parsed_json` as a non-dict (e.g. `["a", "b"]`) with no `error` set; assert the
   client's model-selection loop treats this as not-ok and proceeds to try the
   fallback model (regression test for B3), using a fake transport so no real network
   call is made.
5. **Schema-repair retry, bounded and fail-closed:** a fake client returns a non-dict
   payload on the first call and a valid, correctly-shaped payload on a second call;
   assert `synthesize_account()` succeeds using the second response, and that exactly
   one retry occurs (not zero, not unbounded). A companion test where BOTH calls
   return a non-dict payload must assert the final result is still `ai_failed` — the
   retry must never become a way to eventually accept an invalid shape.
6. **Grounding is unaffected by any of the above:** re-run the existing grounding
   tests in `tests/test_phase2_inference_hardening.py`
   (`test_ai_response_with_invented_*_field_fails_schema_validation`,
   `test_grounded_capability_narrative_carries_only_app_computed_category_and_status`)
   unchanged, plus a new test asserting that even after prompt capping/separation, an
   AI response citing a `weak_relevance`-only source URL is still dropped by grounding
   exactly as today — proving E1/E2 changed only what's *shown*, never what's
   *accepted*.
7. **Large-corpus end-to-end shape test:** using a fake client that echoes back a
   deliberately oversized/malformed response only when the incoming prompt exceeds a
   threshold (simulating model behavior degrading under complexity), assert the full
   `synthesize_account()` pipeline still returns a well-defined status
   (`degraded`/`ai_failed`, never an exception, never a fabricated `ok`) — a coarse
   regression guard against future changes reintroducing today's failure shape at
   scale.
8. **`max_tokens` scaling (if E3 implemented):** assert the value passed to
   `chat.completions.create` grows with the number of eligible capabilities/mappings
   in the report, within a defined floor and ceiling, via a fake client capturing the
   call kwargs.

---

No code was changed to produce this report. Per your instruction, stopping here for
review before any implementation.

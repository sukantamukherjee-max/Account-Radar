# Phase 1F — Commercial Intelligence Hardening

## Objective

Turn the Phase 1E evidence engine into a commercially useful, evidence-backed hypothesis layer without allowing AI to invent customer requirements.

The commercial chain is now:

**Research → Evidence → Context → Otech Capability → Sales Hypothesis → Next Best Action**

## Delivered

### 1. Deterministic opportunity/hypothesis engine

`commercial/opportunities.py` groups only `direct_evidence` and `strong_inference` mappings by Otech capability and produces a bounded ranked shortlist.

Each hypothesis includes:
- deterministic score
- strongest evidence level and confidence
- evidence/source counts
- hypothesis wording that explicitly says it is a validation hypothesis
- relevant Otech buyer/function
- conversation starter
- next step
- source URLs
- incumbent context

`weak_relevance` mappings cannot create a sales hypothesis.

### 2. Deterministic Next Best Action safety-net

AI remains responsible for useful narrative and ranking/wording, but it is no longer able to suppress all actions by returning an empty action array.

If the AI returns no grounded actions (including when all proposed actions are rejected by grounding), the deterministic opportunity shortlist supplies up to five evidence-backed validation actions.

Guardrail actions are explicitly marked with `origin=deterministic_guardrail`.

AI transport/schema failures remain honest `ai_failed` states; the guardrail does not hide an AI failure.

### 3. AI citation hardening

Capability narratives may cite only `direct_evidence` or `strong_inference` sources. `weak_relevance` sources remain context and cannot be used as narrative support.

This prevents a LinkedIn personal profile or other weak-context source from becoming an apparent proof point merely because the model selected its URL.

### 4. Opportunity data is carried in the report

`AccountRadarReport` now exposes `opportunity_hypotheses` and serializes them into the full report JSON.

This makes the commercial shortlist available to the future Account Command Center UI without requiring the UI to recreate scoring logic.

## Design boundaries preserved

- No organization/sector/country hard-coding.
- No confirmed deal/opportunity claims from public evidence alone.
- No capability promotion from weak relevance.
- No incumbent/vendor mention automatically treated as an opportunity.
- AI does not assign evidence levels or confidence.
- AI cannot introduce unsupported capability IDs or source URLs.

## Validation

The Phase 1E baseline was 90 tests. Phase 1F adds dedicated commercial guardrail tests. The focused Phase 1F regression suite passes in the development environment; the long-running acquisition harness is unchanged from Phase 1E.

## Acceptance criteria for the user's machine

1. Existing full Phase 1E tests remain green.
2. Live run completes with real Serper/OpenRouter credentials.
3. The JSON report contains `opportunity_hypotheses`.
4. When qualifying evidence exists, the final synthesis does not silently show zero next-best-actions solely because the AI returned an empty list.
5. Weak LinkedIn/technographics evidence remains context-only.


## Integration correction
The Phase 1F opportunity engine is now explicitly persisted in the runner output under `opportunity_hypotheses`, so opportunity scores, evidence counts, hypotheses, next steps, source URLs, and incumbent context are available to the future UI/API layer. The existing `ai_synthesis.next_best_actions` remains the action presentation layer; the deterministic opportunity shortlist is the underlying commercial guardrail.

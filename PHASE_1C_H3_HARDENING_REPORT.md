# Account Radar — Phase 1C-H3 Organization-Association / Evidence-Boundary Hardening Report

**Scope note:** this pass adds one new check — organization association — to `direct_evidence` promotion in `commercial/evidence_mapping.py`, and threads the resolved organization name through the pipeline so that check can run. It does not touch acquisition, URL validation, incumbent separation, LinkedIn rules, qualification, or the H2 generic-term/technology-event-signal fixes. Not proceeding to Phase 1D.

## 1. The problem (from a real Oman Airports live run, post-H2)

The last remaining false `direct_evidence`: Enterprise Managed Services, matched term `"digital transformation programme"`, confidence 80, signal `technology_initiative`, source an Oman–Algeria cooperation article from MCIT. The supporting context was the *National* Digital Transformation Programme and its 66 participating companies — a government/national initiative, not anything said about Oman Airports' own technology requirement. Oman Airports was mentioned elsewhere in the same article (in the cooperation/MOU context), entirely disconnected from the national-programme passage.

**Root cause:** H2's `direct_evidence` rule checked *that* a specific keyword and a technology-event signal existed *somewhere in the source*, but never checked *whether either was actually about the target organization*. A source's trust tier and a matched phrase say nothing about which entity the source is describing at that point in the text — a government/MOU/partnership/roundup article routinely discusses multiple entities and topics in the same document.

## 2. What changed

### 2.1 Organization-association gate (`commercial/evidence_mapping.py`) — items 1, 2, 3, 6
Added `_is_organization_associated(text, terms, organization_name)`: for each occurrence of a matched capability term, it checks whether a mention of the **target organization** (full name, or ≥2 of its core name tokens for multi-word names, 1 for single-word names) falls in the **same sentence or an immediately adjacent sentence** (`_SENTENCE_ASSOCIATION_RADIUS = 1`). This is deliberately *not* "does the organization name appear anywhere in the source" (item 6 explicitly rules that out) — it requires the organization mention to be in *local narrative context* with the technology evidence itself, which is what separates "Org X deployed a new cloud platform" (associated) from "Org X signed an MOU. [unrelated paragraph later] The national cloud programme now includes 66 companies" (not associated).

Sentence splitting (`_sentence_spans`) is a simple, disclosed heuristic (punctuation + whitespace + capital/digit, or a paragraph break) — not true NLP segmentation, consistent with the rest of this module's regex-based approach.

### 2.2 direct_evidence rule updated
Both direct-evidence promotion paths (2+ distinct specific terms; 1 specific term + high trust + technology-event signal) now additionally require `_is_organization_associated(...)` to hold. When it doesn't, the mapping is **not dropped** (item 4) — it falls to `strong_inference` if a real signal backs it, otherwise `weak_relevance`, exactly mirroring the "preserve as context, downgrade when the organization's own requirement isn't established" instruction (item 4).

### 2.3 Organization name threaded through the pipeline — no hardcoding (item 5)
- `map_source_to_capabilities(source, *, organization_name=None)` and `map_all_sources(sources, *, organization_name=None)` gained an optional keyword parameter. Default `None` means the association gate is a deliberate no-op — this is what keeps every pre-H3 unit test (all 48 from H1/H2) passing unchanged without modification, since they call these functions directly without organization context.
- `research/analysis/report.py`'s `build_report()` now passes `organization_name=run.organization.name` — the organization name *actually resolved for that research run*, never a literal. This is the only place in the real pipeline that supplies it, so the fix applies identically to any organization, sector, or country by construction: nothing here reads or branches on a specific name.
- `before_after_demo.py` updated to pass `organization_name=ORG_NAME` too, so the demo now reflects real production behavior (previously it exercised only the H2 fixes; it now exercises H3 as well).

## 3. Tests (item 7, 8)

New file `tests/test_org_association_hardening.py`, 7 tests, using a generic placeholder organization (`"Meridian Ports Authority"`) — no real organization/sector/country referenced:

- `test_org_mentioned_in_broader_national_programme_article_is_not_direct_evidence` — **the exact reported pattern**: target org named in an MOU/cooperation passage, a national digital-transformation programme (66 participating companies) described several sentences later in the same article → the Enterprise Managed Services mapping is retained but never `direct_evidence`.
- `test_map_all_sources_applies_the_same_gate_via_organization_name_kwarg` — same pattern through the real entry point (`map_all_sources`), confirming the kwarg actually threads through.
- `test_without_organization_context_the_association_gate_is_a_no_op` — proves the gate is strictly opt-in: with `organization_name` supplied, the false positive is blocked; without it, pre-H3 behavior (still direct_evidence) is preserved unchanged.
- `test_organization_specific_technology_announcement_in_official_source_is_still_direct_evidence` (item 8) — an official source where the organization announces its **own** digital transformation programme, in the same sentence as the org name, still reaches `direct_evidence`.
- `test_organization_specific_procurement_still_direct_evidence_with_org_context` — regression: the H2 procurement-RFP case, now also passing organization context, is unaffected (org name is in the same sentence as the technology terms).
- `test_two_distinct_terms_near_organization_mention_still_direct_evidence` — proves association works independent of trust tier (uses a `news`-tier source).
- `test_unassociated_national_programme_mention_is_preserved_as_lower_tier_evidence` (item 4) — the mismatch case is retained as `strong_inference`/`weak_relevance`, never silently dropped.

**Full suite: `55 passed in 2.77s`** — the previously-accepted 48 (40 H2-integration + 8 H2 direct-evidence-hardening) plus these 7 new tests. `pyflakes` across `research/`, `commercial/`, `tests/`, and all three CLI scripts: clean.

## 4. Preserving H2 (item 9)

- Generic-term catalogue cleanup, the `_TECH_EVENT_SIGNAL_TYPES` gate, and the schema-level keyword/weak_signal_terms overlap check are all untouched.
- Incumbent separation (`commercial/incumbents.py`), LinkedIn rules, URL validation (`research/url_utils.py`), and the qualification stage are all untouched.
- Re-ran `before_after_demo.py` (now passing `organization_name`, since every source in that fixture already names Oman Airports Management Company directly alongside its technology evidence): identical result to the accepted H2 numbers — 4 `direct_evidence`, 2 `strong_inference`, 8 `weak_relevance`, 14 total, 3 incumbent items. This is expected and correct: none of that fixture's genuine direct-evidence sources have the organization/technology disconnect this pass targets, so H3 changes nothing there — it only fires on the specific false-positive shape reported.

## 5. On validating against the real Oman Airports MCIT source

This sandbox still has no `SERPER_API_KEY` (same disclosed limitation since Phase 1B), so I can't re-run acquisition against the real Oman-Algeria MCIT article. `test_org_mentioned_in_broader_national_programme_article_is_not_direct_evidence` is built to mirror its structure as closely as I can from your description (an organization named in an MOU/cooperation passage; a national digital-transformation programme with dozens of participating companies described separately in the same article) and confirms the fix handles that shape. As before, I'd want to see your next `run_phase1c.py --live` output against the real MCIT source before calling the specific case fully closed — the synthetic reproduction proves the mechanism works, not that it's been checked against the exact source text you saw.

## 6. Known limitations

- Sentence splitting is a simple regex heuristic, not true sentence-boundary detection — it can mis-split on some abbreviations or unusual punctuation. A rare consequence: a genuinely organization-specific sentence could be mis-segmented such that it reads as two "sentences," pushing the org mention just outside the adjacency radius. This is a possible source of false negatives (under-classifying real direct evidence to strong_inference), not false positives — consistent with the standing conservative-by-design trade-off.
- The adjacency radius (1 sentence) is a fixed constant, not yet configurable, chosen to balance normal narrative structure against genuine topic separation. A future case might reveal it's too tight or too loose for a different writing style; that would be a targeted constant change, not an architecture change.
- Organization-name matching uses simple token overlap (≥2 of the organization's own name tokens, or the full name string) — an organization referred to by an unrelated abbreviation or a translated/localized name variant the resolver didn't capture in `ResolvedOrganization.name` would not be recognized as "the organization" by this check. This mirrors the acquisition layer's existing reliance on the resolved name/website as the organization's canonical identity.

Stopping here per your instruction. Not proceeding to Phase 1D.

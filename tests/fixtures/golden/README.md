# Golden Case Validation Set

Objective acceptance tests for the evidence -> opportunity pipeline. Run them with:

    py -m pytest tests/test_golden_cases.py -v

Each case asserts the **actual opportunity list** (what the user sees), that generic or
vendor attribution never yields a confirmed customer requirement, and that any opportunity
that appears carries the "sales hypothesis, not confirmation of a buying requirement" wording.

## Cases

| # | Case | Fixture | Frozen expectation |
|---|------|---------|--------------------|
| 1 | Generic vendor/marketing article (Aufait RFP article) | replica + real snapshot | ZERO opportunities for Oman Air |
| 2 | Unrelated vendor press release (Zawya Otech/Kaspersky) | replica + real snapshot | ZERO opportunities for Oman Air |
| 3 | Genuine customer expansion (Oman Air fleet article) | replica + real snapshot | at most ONE opportunity: Enterprise Managed Services, WATCH, `strong_inference`, `inference_hypothesis`. Replica frozen exactly at 38.5 (55 confidence) |
| 4 | Genuine customer technology initiative | existing fixture text, org named | Cloud & AI `direct_evidence` 61.0 (medium, `confirmed_evidence`) + EMS WATCH 42.0 |
| 4b | Attribution twin: same wording, different organization | same text | ZERO opportunities |
| 5 | Genuine procurement/RFP requirement | existing fixture text, org named | Data Centre `direct_evidence` 64.5 (medium, `confirmed_evidence`) |
| 5b | Attribution twin: same wording, different organization | same text | ZERO opportunities |
| G1 | Vendor page: "Trusted by customers such as `<org>`" + generic compliance text | synthetic, 2 orgs | ZERO opportunities |
| G3 | MOU with the org genuinely named, only a homonym keyword ("transport network") | synthetic | ZERO opportunities; keyword evidence retained at `weak_relevance` only |

## Known-gap tests (currently FAIL, on purpose)

`G1` (2 parametrisations) and both `G3` tests encode required behavior that the current code does
not deliver:

* **G1** -- today the page is promoted to Cybersecurity `direct_evidence` (85 confidence, score 64.5,
  `confirmed_evidence`): the customer-listing phrase matches none of the vendor-case-study markers.
* **G3** -- today the MOU yields a `network_connectivity` WATCH opportunity (42.0) through a
  `partnership` signal, which the corroboration gate does not cover.

Do not weaken these to make the suite green. They are the acceptance gate for the fix.

**G3 interpretation:** "retain as WATCH/context only, do NOT create a commercial opportunity" is
implemented as: no entry in `build_opportunity_hypotheses`, and the keyword evidence still recorded
at `weak_relevance`. If you meant that a WATCH-priority opportunity entry is acceptable here, change
`test_golden_g3_...creates_no_opportunity` deliberately -- do not loosen it silently.

## Replicas vs real snapshots

* `replicas/*.txt` are original-wording reproductions of the structural features of each real page
  (they are not copies of third-party text). `test_golden_replicas_preserve_their_documented_structure`
  guards those features so an edit cannot quietly defeat a case.
* `snapshots/*.txt` hold the exact text `research.fetcher.fetch_page()` returns for the real URL.
  Capture them once (needs network access; will not overwrite existing snapshots):

      py tests/fixtures/golden/capture_snapshots.py

  Commit them. Snapshot tests are **skipped** until the files exist. They contain third-party
  page text -- keep the repository private or check the sources' terms.

## Provenance of the frozen numbers

All frozen scores were produced by running these fixtures through the current
`map_source_to_capabilities` + `build_opportunity_hypotheses`. The Case 3 replica reproduces the
live result reported for the real article (EMS WATCH, 55 confidence, 38.5): the unique EMS trigger
"modernization" combined with a `capacity_change` signal from "increased capacity" (aircraft seats).
That mechanism is verified on the replica only, not on the real page's fetched text.

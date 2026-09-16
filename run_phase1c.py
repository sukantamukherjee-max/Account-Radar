#!/usr/bin/env python3
"""Phase 1C-H end-to-end runner.

Runs the complete pipeline for one or more organizations, with no code
change required between them:

    acquisition -> qualification -> signal extraction ->
        evidence-first capability mapping -> report

This is the integration-pass counterpart to `run_acquisition_test.py`
(which only exercises the acquisition layer): it wires the Phase 1B
acquisition substrate into the Phase 1C-H commercial evidence layer via
`research.analysis.qualification` and `research.analysis.report`, and
prints/writes the final chain-view report
(Customer Signal -> Business Impact -> Technology Requirement ->
Potential Otech Capability).

Usage:
    python run_phase1c.py                      # uses default 3 test orgs
    python run_phase1c.py --org "Acme Corp" --country "United Arab Emirates"
    python run_phase1c.py --mock                # force synthetic providers
    python run_phase1c.py --live                 # require real API keys, fail loudly if missing

Like run_acquisition_test.py, this falls back to synthetic
MockSearchProvider(s) when no SERPER_API_KEY is configured, and labels
every report SYNTHETIC / MOCK RUN -- it never silently presents fabricated
data as a real research result.

Honest note: MockSearchProvider (see tests/mock_provider.py) generates
enough synthetic article-style body text that --mock runs do exercise the
full qualified -> signals -> mappings path (verified: mock runs regularly
qualify and produce strong_inference mappings from generic
technology-initiative language). But it is still synthetic text, not real
research -- treat --mock output only as a pipeline-wiring check, never as
a real finding. before_after_demo.py and tests/test_integration_pipeline.py
additionally exercise the qualified path deterministically with
hand-authored fixture text (including direct_evidence and incumbent-context
cases) without requiring network access. A --live run (real SERPER_API_KEY)
against an organization with genuine public footprint is what produces an
actual research result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from research.acquisition import run_acquisition
from research.analysis.report import build_report
from research.organization import OrganizationInput
from research.providers.openrouter_search import OpenRouterSearchProvider
from research.providers.serper import SerperProvider
from tests.mock_provider import MockSearchProvider

DEFAULT_TEST_ORGANIZATIONS = [
    # Test fixtures for the runner (same three used by the Phase 1B
    # acceptance harness), not special-cased business logic. Nothing in
    # this pipeline branches on any of these names.
    OrganizationInput(name="Oman Airports Management Company", country="Oman"),
    OrganizationInput(name="Standard Chartered Bank", country="United Kingdom"),
    OrganizationInput(name="Siemens AG", country="Germany"),
]


def _build_providers(force_mock: bool, require_live: bool) -> tuple[list, bool]:
    serper_key = os.getenv("SERPER_API_KEY", "").strip()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    live_available = bool(serper_key)

    if require_live and not live_available:
        print("FATAL: --live was requested but SERPER_API_KEY is not set in the environment.", file=sys.stderr)
        sys.exit(2)

    if force_mock or not live_available:
        primary = MockSearchProvider(name="mock_primary")
        fallback = MockSearchProvider(name="mock_fallback_openrouter_parallel")
        return [primary, fallback], False

    providers = [SerperProvider(api_key=serper_key)]
    if openrouter_key:
        providers.append(OpenRouterSearchProvider(api_key=openrouter_key))
    else:
        print("NOTE: OPENROUTER_API_KEY not set -- running with Serper only, no fallback provider configured.")
    return providers, True


def print_report(reports: list[dict], *, is_live: bool) -> None:
    banner = "LIVE PHASE 1C-H RUN (real providers)" if is_live else "*** SYNTHETIC / MOCK RUN -- NOT REAL DATA ***"
    print("=" * 100)
    print(banner)
    print("=" * 100)
    for r in reports:
        print(f"\n--- {r['organization_name']} ---")
        print(f"  Status:                {r['status']}")
        print(f"  Qualified sources:     {r['qualification']['qualified_count']}")
        print(f"  Disqualified sources:  {r['qualification']['disqualified_count']}")
        print(f"  Corpus reason:         {r['qualification']['corpus_reason']}")
        if r["status"] == "insufficient_evidence":
            print("  (no signals/mappings computed -- corpus did not qualify for analysis)")
            continue
        print(f"  Signals extracted:     {len(r['signals'])}")
        print(f"  Capability mappings:   {len(r['chain'])}")
        print(f"  Incumbent evidence:    {len(r['incumbent_evidence'])}")
        for level in ("direct_evidence", "strong_inference", "weak_relevance"):
            rows = [row for row in r["chain"] if row["evidence_level"] == level]
            print(f"    {level:<17} {len(rows)}")
            for row in rows[:5]:
                cap = row["potential_otech_capability"]["capability_name"]
                print(
                    f"      - {cap:<28} confidence={row['confidence']:<5} "
                    f"signal={row['customer_signal']['signal_type']}  <- {row['source_url']}"
                )
                if row["incumbent_context"]:
                    print(f"          incumbent_context: {row['incumbent_context']}")
    print("\n" + "=" * 100)
    print(banner)
    print("=" * 100)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", action="append", help="Organization name (repeatable). Default: 3 built-in test orgs.")
    parser.add_argument("--country", action="append", help="Country, aligned by position with --org.")
    parser.add_argument("--mock", action="store_true", help="Force synthetic providers even if API keys are set.")
    parser.add_argument("--live", action="store_true", help="Require real API keys; fail if not present.")
    parser.add_argument("--out", default="phase1c_report.json", help="Path to write the full JSON report.")
    args = parser.parse_args()

    if args.org:
        countries = args.country or []
        orgs = [
            OrganizationInput(name=name, country=(countries[i] if i < len(countries) else None))
            for i, name in enumerate(args.org)
        ]
    else:
        orgs = DEFAULT_TEST_ORGANIZATIONS

    providers, is_live = _build_providers(force_mock=args.mock, require_live=args.live)

    reports = []
    for org in orgs:
        run = run_acquisition(org, providers)
        report = build_report(run)
        reports.append(report.to_dict())

    print_report(reports, is_live=is_live)

    with open(args.out, "w") as f:
        json.dump({"is_live_run": is_live, "organizations": reports}, f, indent=2, default=str)
    print(f"\nFull JSON report written to: {args.out}")


if __name__ == "__main__":
    main()

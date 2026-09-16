#!/usr/bin/env python3
"""Phase 1D end-to-end runner.

Extends run_phase1c.py's pipeline with the Phase 1D AI synthesis stage:

    acquisition -> qualification -> signal extraction ->
        evidence-first capability mapping -> report -> AI synthesis

The AI stage is strictly additive and downstream: it reads an already-built
AccountRadarReport and never influences acquisition, qualification, signal
extraction, or capability mapping (see ai/synthesis.py, ai/client.py). If no
OPENROUTER_API_KEY is configured, this runner does NOT fabricate a synthesis
result -- it reports status "ai_unconfigured" plainly, the same honesty
discipline run_phase1c.py applies to SYNTHETIC/MOCK acquisition data.

Usage:
    python run_phase1d.py                      # uses default 3 test orgs
    python run_phase1d.py --org "Acme Corp" --country "United Arab Emirates"
    python run_phase1d.py --mock                # force synthetic search providers
    python run_phase1d.py --live                 # require real SERPER_API_KEY, fail loudly if missing

AI synthesis requires OPENROUTER_API_KEY separately from --live/--mock
(which control the *search* providers only). Without it, every report's
"ai_synthesis" section will show status "ai_unconfigured" -- this is
expected and correct in this sandbox, not a bug.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from ai.client import AIClient, AIResponse, OpenRouterAIClient
from ai.synthesis import synthesize_account
from commercial.opportunities import build_opportunity_hypotheses
from research.acquisition import run_acquisition
from research.analysis.report import build_report
from research.organization import OrganizationInput
from research.providers.openrouter_search import OpenRouterSearchProvider
from research.providers.serper import SerperProvider
from tests.mock_provider import MockSearchProvider

DEFAULT_TEST_ORGANIZATIONS = [
    # Test fixtures for the runner (same three used by the Phase 1B/1C
    # acceptance harnesses), not special-cased business logic. Nothing in
    # this pipeline or the ai/ package branches on any of these names.
    OrganizationInput(name="Oman Airports Management Company", country="Oman"),
    OrganizationInput(name="Standard Chartered Bank", country="United Kingdom"),
    OrganizationInput(name="Siemens AG", country="Germany"),
]


class _UnconfiguredAIClient:
    """Explicit no-op AIClient used when OPENROUTER_API_KEY is absent, so
    the runner never silently skips synthesis without saying so."""

    def is_configured(self) -> bool:
        return False

    def complete_json(self, prompt: str, *, schema_name: str) -> AIResponse:
        return AIResponse(error="OPENROUTER_API_KEY not configured")


def _build_search_providers(force_mock: bool, require_live: bool) -> tuple[list, bool]:
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
        print("NOTE: OPENROUTER_API_KEY not set -- running with Serper only, no fallback search provider configured.")
    return providers, True


def _build_ai_client() -> AIClient:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        return _UnconfiguredAIClient()
    return OpenRouterAIClient(api_key=key)


def print_report(entries: list[dict], *, is_live_search: bool, ai_configured: bool) -> None:
    search_banner = "LIVE SEARCH (real providers)" if is_live_search else "*** SYNTHETIC / MOCK SEARCH -- NOT REAL DATA ***"
    ai_banner = "AI SYNTHESIS: configured" if ai_configured else "*** AI SYNTHESIS UNAVAILABLE -- no OPENROUTER_API_KEY configured ***"
    print("=" * 100)
    print(f"PHASE 1D RUN -- {search_banner} / {ai_banner}")
    print("=" * 100)
    for entry in entries:
        r = entry["report"]
        s = entry["ai_synthesis"]
        print(f"\n--- {r['organization_name']} ---")
        print(f"  Corpus status:         {r['status']}")
        print(f"  Qualified sources:     {r['qualification']['qualified_count']}")
        print(f"  Disqualified sources:  {r['qualification']['disqualified_count']}")
        print(f"  Capability mappings:   {len(r.get('chain', []))}")
        opportunities = entry.get("opportunity_hypotheses", [])
        print(f"  Opportunity hypotheses:{len(opportunities)}")
        for o in opportunities:
            print(f"    - [{o['priority']}] {o['capability_name']} score={o['score']}: {o['hypothesis']}")
        print(f"  AI synthesis status:   {s['status']}")
        if s["notes"]:
            for note in s["notes"]:
                print(f"    note: {note}")
        if s["status"] == "ok" or s["status"] == "degraded":
            print(f"  Account narrative:     {s['account_narrative']}")
            print(f"  Capability narratives: {len(s['capability_narratives'])}")
            for c in s["capability_narratives"]:
                print(f"    - [{c['best_evidence_level']}] {c['capability_name']}: {c['narrative']}")
            print(f"  Next best actions:     {len(s['next_best_actions'])}")
            for a in s["next_best_actions"]:
                print(f"    {a['rank']}. {a['capability_name']} -- {a['rationale']}")
    print("\n" + "=" * 100)
    print(f"PHASE 1D RUN -- {search_banner} / {ai_banner}")
    print("=" * 100)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", action="append", help="Organization name (repeatable). Default: 3 built-in test orgs.")
    parser.add_argument("--country", action="append", help="Country, aligned by position with --org.")
    parser.add_argument("--mock", action="store_true", help="Force synthetic search providers even if SERPER_API_KEY is set.")
    parser.add_argument("--live", action="store_true", help="Require real SERPER_API_KEY; fail if not present.")
    parser.add_argument("--out", default="phase1d_report.json", help="Path to write the full JSON report.")
    args = parser.parse_args()

    if args.org:
        countries = args.country or []
        orgs = [
            OrganizationInput(name=name, country=(countries[i] if i < len(countries) else None))
            for i, name in enumerate(args.org)
        ]
    else:
        orgs = DEFAULT_TEST_ORGANIZATIONS

    search_providers, is_live_search = _build_search_providers(force_mock=args.mock, require_live=args.live)
    ai_client = _build_ai_client()

    entries = []
    for org in orgs:
        run = run_acquisition(org, search_providers)
        report = build_report(run)
        synthesis = synthesize_account(report, ai_client)
        opportunities = build_opportunity_hypotheses(report.capability_mappings, max_items=5)
        entries.append({"report": report.to_dict(), "ai_synthesis": synthesis.to_dict(), "opportunity_hypotheses": [o.to_dict() for o in opportunities]})

    print_report(entries, is_live_search=is_live_search, ai_configured=ai_client.is_configured())

    with open(args.out, "w") as f:
        json.dump(
            {"is_live_search": is_live_search, "ai_configured": ai_client.is_configured(), "organizations": entries},
            f, indent=2, default=str,
        )
    print(f"\nFull JSON report written to: {args.out}")


if __name__ == "__main__":
    main()

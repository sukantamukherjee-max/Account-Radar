#!/usr/bin/env python3
"""Phase 1B acquisition acceptance-test harness.

Runs the acquisition layer against three unrelated organizations (from
different industries/countries, per Phase 0 §23 / Phase 1B acceptance
criteria) WITHOUT any code change between them, and reports the exact
diagnostics fields the Phase 1B acceptance gate requires.

Usage:
    python run_acquisition_test.py                     # uses default 3 test orgs
    python run_acquisition_test.py --org "Acme Corp" --country "United Arab Emirates"
    python run_acquisition_test.py --mock               # force synthetic providers
    python run_acquisition_test.py --live                # require real API keys, fail loudly if missing

If SERPER_API_KEY is not set in the environment, this harness automatically
falls back to synthetic MockSearchProvider(s) and labels every report
SYNTHETIC / MOCK RUN in large, unmissable text -- it will never silently
present fabricated data as if it were a real acquisition result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from research.acquisition import run_acquisition
from research.organization import OrganizationInput
from research.providers.openrouter_search import OpenRouterSearchProvider
from research.providers.serper import SerperProvider
from research.sources import Source
from tests.mock_provider import MockSearchProvider

DEFAULT_TEST_ORGANIZATIONS = [
    # These are TEST FIXTURES for the acceptance harness (per spec §23's own
    # suggested categories), not special-cased business logic. The
    # acquisition code above never branches on any of these names.
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


def _source_summary(sources: list[Source]) -> dict:
    linkedin_company = sum(1 for s in sources if s.source_type == "linkedin_company")
    linkedin_profiles = sum(1 for s in sources if s.source_type == "linkedin_profile")
    official = sum(1 for s in sources if s.source_type == "official")
    other = len(sources) - linkedin_company - linkedin_profiles - official
    return {
        "total_sources": len(sources),
        "linkedin_company_pages": linkedin_company,
        "linkedin_in_profiles": linkedin_profiles,
        "official_sources": official,
        "other_sources": other,
    }


def run_one(org: OrganizationInput, providers: list, *, is_live: bool) -> dict:
    run = run_acquisition(org, providers)
    diag = run.diagnostics.to_dict()
    diag["source_summary"] = _source_summary(run.sources)
    diag["is_live_run"] = is_live
    diag["sample_linkedin_profiles"] = [
        {"url": s.url, "title": s.title, "access_status": s.access_status.value, "dimension": s.extra.get("discovery_dimension")}
        for s in sorted(run.sources, key=lambda x: -x.source_quality)
        if s.source_type == "linkedin_profile"
    ][:10]
    diag["company_page_found"] = run.organization.linkedin_company_url is not None
    return diag


def print_report(all_results: list[dict], *, is_live: bool) -> None:
    banner = "LIVE ACQUISITION RUN (real providers)" if is_live else "*** SYNTHETIC / MOCK RUN -- NOT REAL DATA ***"
    print("=" * 100)
    print(banner)
    print("=" * 100)
    for r in all_results:
        print(f"\n--- {r['organization']} ---")
        print(f"  Resolved website:              {r['resolved_website']}")
        print(f"  Resolved LinkedIn company URL: {r['resolved_linkedin_company_url']}")
        print(f"  Company page found:            {r['company_page_found']}")
        print(f"  Elapsed seconds:                {r['elapsed_seconds']}")
        print(f"  Total queries:                   {r['total_queries']}")
        print(f"  Total raw results returned:      {r['total_results_returned']}")
        print(f"  Results per query:                {r['results_per_query']}")
        print(f"  Sources pre-dedup:                 {r['total_sources_pre_dedup']}")
        print(f"  Sources post-dedup (unique URLs):   {r['total_sources_post_dedup']}")
        print(f"  Duplicates removed:                  {r['duplicates_removed']}")
        ss = r["source_summary"]
        print(f"  -- LinkedIn company pages:            {ss['linkedin_company_pages']}")
        print(f"  -- LinkedIn /in/ profiles:             {ss['linkedin_in_profiles']}")
        print(f"  -- Official sources:                    {ss['official_sources']}")
        print(f"  -- Other sources:                        {ss['other_sources']}")
        print(f"  Access status breakdown:                  {r['access_status_counts']}")
        print(f"  Source type breakdown:                     {r['source_type_counts']}")
        print(f"  LinkedIn discovery-dimension counts:         {r['linkedin_dimension_counts']}")
        print(f"  Fallback frequency:                            {r['fallback_frequency']}")
        if r["fallback_families"]:
            print(f"  Fallback triggered in families:              {r['fallback_families']}")
        print("  Provider(s) used by family:")
        for f in r["families"]:
            marker = " <- FALLBACK FIRED" if f["fallback_triggered"] else ""
            print(
                f"      {f['family']:<32} queries={f['queries_run']:<3} results={f['results_returned']:<4} "
                f"unique={f['unique_urls']:<4} providers={f['providers_used']}{marker}"
            )
        if r["errors"]:
            print(f"  Errors ({len(r['errors'])}):")
            for e in r["errors"][:10]:
                print(f"      - {e}")
        else:
            print("  Errors: none")
        if r["sample_linkedin_profiles"]:
            print(f"  Sample LinkedIn /in/ candidates (top {len(r['sample_linkedin_profiles'])} by quality):")
            for p in r["sample_linkedin_profiles"]:
                print(f"      - [{p['access_status']:<20}] {p['title']}  ({p['dimension']})  {p['url']}")
    print("\n" + "=" * 100)
    print(banner)
    print("=" * 100)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", action="append", help="Organization name (repeatable). Default: 3 built-in test orgs.")
    parser.add_argument("--country", action="append", help="Country, aligned by position with --org.")
    parser.add_argument("--mock", action="store_true", help="Force synthetic providers even if API keys are set.")
    parser.add_argument("--live", action="store_true", help="Require real API keys; fail if not present.")
    parser.add_argument("--out", default="acquisition_test_report.json", help="Path to write the full JSON report.")
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

    all_results = []
    for org in orgs:
        result = run_one(org, providers, is_live=is_live)
        all_results.append(result)

    print_report(all_results, is_live=is_live)

    with open(args.out, "w") as f:
        json.dump({"is_live_run": is_live, "organizations": all_results}, f, indent=2, default=str)
    print(f"\nFull JSON report written to: {args.out}")


if __name__ == "__main__":
    main()

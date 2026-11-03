"""DIAGNOSTIC ONLY -- Brave vs Tavily People-discovery comparison.

Runs the first N plain-wording People queries (research.linkedin._person_queries,
default 13) once through Brave and once through Tavily, then processes each
provider's results with the CURRENT People pipeline (commercial.people.build_people,
which has no people cap) and the CURRENT PEOPLE-page grouping
(app._people_bucket_members). NOTE: this uses Tavily credits -- run it only
when the Tavily quota allows (it is not part of the app's brave_only mode). No application code is changed, no full
Account Radar analysis is run, and nothing is written outside this folder.

Usage (from the project folder):

    py _diagnostics\\people_provider_comparison.py "Ooredoo Oman" "Oman"

Output: _diagnostics\\out\\people_comparison_<org>_<timestamp>.json and .md
API keys are read from the environment as usual and are never printed or saved.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from urllib.parse import urlparse  # noqa: E402

import commercial.people as people_module  # noqa: E402
from commercial.people import _profile_slug, build_people  # noqa: E402
from research.linkedin import _person_queries  # noqa: E402
from research.providers.brave import BraveSearchProvider  # noqa: E402
from research.providers.tavily import TavilyProvider  # noqa: E402
from research.sources import SOURCE_TYPE_LINKEDIN_PROFILE, Source, score_source  # noqa: E402

NUM_RESULTS = 20  # same as research.linkedin.discover_linkedin
MAX_QUERIES = 13  # same query count as the original comparison


def build_uncapped(sources: list[Source], organization: str) -> list:
    """build_people (no people cap since People Intelligence v2)."""
    return build_people(sources, organization_name=organization)


def _bucket_members(people: list[dict]) -> dict[str, set]:
    """The CURRENT PEOPLE-page grouping (app._people_bucket_members)."""
    try:
        import app  # noqa: WPS433 (imports streamlit; does not start the UI)
        return app._people_bucket_members(people)
    except Exception as exc:  # pragma: no cover - environment specific
        print(f"  (could not import app grouping: {type(exc).__name__}); groups not computed")
        return {}


def run_provider(provider, queries: list[tuple[str, str]]) -> dict:
    started = time.monotonic()
    outcomes = []
    for dimension, query in queries:
        outcome = provider.search(query, num_results=NUM_RESULTS)
        outcomes.append((dimension, outcome))
    return {"outcomes": outcomes, "elapsed": round(time.monotonic() - started, 1)}


def analyze(provider_name: str, run: dict, organization: str) -> tuple[dict, list[Source]]:
    outcomes = run["outcomes"]
    errors = [o.error for _, o in outcomes if not o.ok]
    total_results = sum(len(o.results) for _, o in outcomes)
    linkedin_in, company_pages, per_query = [], 0, []
    for dimension, o in outcomes:
        n_in = 0
        for r in o.results:
            host = urlparse(r.url).netloc.lower()
            path = urlparse(r.url).path.lower()
            if "linkedin.com" not in host:
                continue
            if "/company/" in path:
                company_pages += 1
            elif "/in/" in path:
                s = Source.from_raw(r, source_type=SOURCE_TYPE_LINKEDIN_PROFILE, family="linkedin_people")
                s.extra["linkedin_kind"] = "in"
                s.extra["discovery_dimension"] = dimension
                s.source_quality = score_source(s)
                linkedin_in.append(s)
                n_in += 1
        per_query.append({"dimension": dimension, "query": o.query, "results": len(o.results),
                          "linkedin_profiles": n_in, "error": o.error})

    unique_slugs = {(_profile_slug(s.url) or s.url) for s in linkedin_in}
    snippet_lengths = [len(s.snippet or "") for s in linkedin_in]
    people = [p.to_dict() for p in build_uncapped(linkedin_in, organization)]
    buckets = {k: len(v) for k, v in _bucket_members(people).items()}

    def top(p):
        return {
            "name": p["name"], "title": (p["current_title"] or "")[:140],
            "association": p["organization_association"], "seniority": p["seniority"],
            "functions": p["functions"], "technology_areas": p["technology_areas"],
            "influence": p["influence_type"], "score": p["relevance_score"], "url": p["linkedin_url"],
        }

    summary = {
        "provider": provider_name,
        "requests": len(outcomes),
        "errors": len(errors),
        "error_samples": sorted(set(e[:120] for e in errors))[:5],
        "elapsed_seconds": run["elapsed"],
        "total_results": total_results,
        "linkedin_profile_urls": len(linkedin_in),
        "linkedin_company_page_urls": company_pages,
        "unique_profiles": len(unique_slugs),
        "qualified_people": len(people),
        "association": dict(Counter(p["organization_association"] for p in people)),
        "rejected_unconfirmed": len(unique_slugs) - len(people),
        "seniority": dict(Counter(p["seniority"] or "none" for p in people)),
        "functions": dict(Counter(f for p in people for f in p["functions"])),
        "technology_areas": dict(Counter(t for p in people for t in p["technology_areas"])),
        "influence": dict(Counter(p["influence_type"] or "none" for p in people)),
        "snippet_length": {
            "median": statistics.median(snippet_lengths) if snippet_lengths else 0,
            "min": min(snippet_lengths) if snippet_lengths else 0,
            "max": max(snippet_lengths) if snippet_lengths else 0,
        },
        "current_ui_groups": buckets,
        "per_query": per_query,
        "strongest_profiles": [top(p) for p in people[:12]],
        "_slugs": sorted(unique_slugs),
    }
    return summary, linkedin_in


def to_markdown(org: str, country: str, results: dict, merged: dict) -> str:
    lines = [f"# People discovery comparison: {org} / {country}", "",
             f"Run at {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. First {MAX_QUERIES} plain People queries; diagnostic only.", ""]
    keys = [("requests", "Requests"), ("errors", "Errors"), ("total_results", "Total results"),
            ("linkedin_profile_urls", "LinkedIn /in/ URLs"), ("unique_profiles", "Unique profiles"),
            ("qualified_people", "Qualified people (uncapped)"),
            ("rejected_unconfirmed", "Rejected (unconfirmed)")]
    names = list(results)
    lines.append("| Metric | " + " | ".join(names) + " |")
    lines.append("|---|" + "---|" * len(names))
    for k, label in keys:
        lines.append(f"| {label} | " + " | ".join(str(results[n][k]) for n in names) + " |")
    for k, label in [("association", "Association"), ("seniority", "Seniority"), ("functions", "Functions"),
                     ("technology_areas", "Technology"), ("current_ui_groups", "Current UI groups"),
                     ("snippet_length", "Snippet length")]:
        lines.append(f"| {label} | " + " | ".join(json.dumps(results[n][k]) for n in names) + " |")
    lines += ["", f"Profiles found by both: {merged['overlap']}; merged unique: {merged['merged_unique']}; "
              f"merged qualified (uncapped): {merged['merged_qualified']}", ""]
    for n in names:
        lines.append(f"## {n}: strongest profiles")
        for p in results[n]["strongest_profiles"]:
            lines.append(f"- {p['name']} | {p['title']} | {p['association']} | {p['seniority']} | "
                         f"{', '.join(p['functions'] + p['technology_areas']) or '-'} | score {p['score']}")
        if results[n]["error_samples"]:
            lines.append(f"- errors: {results[n]['error_samples']}")
        lines.append("")
    return "\n".join(lines)


def main(org: str, country: str, providers: dict | None = None, out_dir: Path | None = None) -> Path:
    providers = providers or {"brave": BraveSearchProvider(), "tavily": TavilyProvider()}
    queries = _person_queries(org, country)[:MAX_QUERIES]
    print(f"People comparison for {org!r} / {country!r}: {len(queries)} queries per provider")
    results, sources = {}, {}
    for name, provider in providers.items():
        if not provider.is_configured():
            print(f"  {name}: NOT CONFIGURED (key missing) - skipped")
            continue
        print(f"  {name}: running {len(queries)} queries ...")
        summary, srcs = analyze(name, run_provider(provider, queries), org)
        results[name], sources[name] = summary, srcs
        print(f"  {name}: {summary['linkedin_profile_urls']} LinkedIn URLs, {summary['unique_profiles']} unique, "
              f"{summary['qualified_people']} qualified, {summary['errors']} errors")

    slug_sets = [set(r.pop("_slugs")) for r in results.values()]
    overlap = len(set.intersection(*slug_sets)) if len(slug_sets) > 1 else 0
    merged_people = build_uncapped([s for srcs in sources.values() for s in srcs], org)
    merged = {"overlap": overlap, "merged_unique": len(set.union(*slug_sets)) if slug_sets else 0,
              "merged_qualified": len(merged_people)}

    out_dir = out_dir or (Path(__file__).resolve().parent / "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = out_dir / f"people_comparison_{re.sub(r'[^A-Za-z0-9]+', '_', org).strip('_').lower()}_{stamp}"
    payload = {"organization": org, "country": country, "queries": [q for _, q in queries],
               "providers": results, "merged": merged}
    base.with_suffix(".json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    base.with_suffix(".md").write_text(to_markdown(org, country, results, merged), encoding="utf-8")
    print(f"  merged: {merged}")
    print(f"Saved: {base.with_suffix('.json').name} and {base.with_suffix('.md').name} in {out_dir}")
    return base


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args[0] if args else "Ooredoo Oman", args[1] if len(args) > 1 else "Oman")

"""People discovery FUNNEL diagnostic (standalone, read-only).

Purpose: show, stage by stage, where a specific person (e.g. Dr. Jihad
Al-Ansari) is lost between the search provider and the final People list:

    raw provider results
      -> LinkedIn /in/ profile URLs (before de-duplication)
      -> de-duplicated profiles (discover_linkedin candidates)
      -> association status + reasons (confirmed / unverified / rejected)
      -> final People list (commercial.people.assess_people().confirmed)

It uses the REAL ``research.linkedin.discover_linkedin`` and the REAL
``commercial.people.assess_people`` / ``commercial.people_association``
functions. It changes nothing in production code, ranking, query strategy or
the UI.

SAFETY
  * Default invocation is a DRY RUN: it prints the plan and makes ZERO
    requests. Live Brave requests need BOTH ``--live`` and
    ``--confirm-live-brave``.
  * Only the Brave provider is ever constructed or passed in. Tavily and
    Serper are never instantiated by this script.
  * A hard request cap (default 45 = the production hard ceiling) is enforced
    by the recording wrapper itself, independently of discovery's budget.
  * Brave's own HTTP retries are disabled (``max_retries=0``) so one logical
    request is at most one HTTP call.
  * The Brave key is read only by the existing BraveSearchProvider, from
    BRAVE_SEARCH_API_KEY (the variable the app uses); BRAVE_API_KEY is NOT used.
  * Output never contains API keys or environment variables. Environment
    information is limited to the effective People mode and booleans.
  * Output goes to ``_diagnostics/out/people_funnel_<org>_<UTC timestamp>.json``
    (+ ``.md``). Files are opened exclusively and never overwritten.

Usage (dry run, no requests):
    python _diagnostics/people_funnel_diagnostic.py --org "Ooredoo Oman" --country Oman

Live (only after explicit approval):
    python _diagnostics/people_funnel_diagnostic.py --org "Ooredoo Oman" --country Oman \\
        --watch jihad --live --confirm-live-brave
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = Path(__file__).resolve().parent / "out"
DEFAULT_HARD_CAP = 45
SNIPPET_LIMIT = 400


# ---------------------------------------------------------------------------
# Recording wrapper (counts and records every request; enforces a hard cap)
# ---------------------------------------------------------------------------

@dataclass
class RecordingProvider:
    """Transparent proxy around a search provider. It forwards the attributes
    discovery inspects (name, pagination, rich snippets, is_configured) and
    records every request and every raw result BEFORE discovery dedups them.
    """

    inner: object
    max_requests: int = DEFAULT_HARD_CAP
    requests: list[dict] = field(default_factory=list)
    raw_results: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return getattr(self.inner, "name", "")

    @property
    def supports_pagination(self) -> bool:
        return bool(getattr(self.inner, "supports_pagination", False))

    @property
    def supports_rich_snippets(self) -> bool:
        return bool(getattr(self.inner, "supports_rich_snippets", False))

    def is_configured(self) -> bool:
        return bool(self.inner.is_configured())

    def search(self, query, **kwargs):
        from research.providers.base import QueryOutcome

        seq = len(self.requests) + 1
        page = int(kwargs.get("page", 0) or 0)
        if seq > self.max_requests:
            self.requests.append({"seq": seq, "provider": self.name, "query": query, "page": page,
                                  "blocked_by_hard_cap": True, "ok": False, "results": 0})
            return QueryOutcome(query=query, provider=self.name, page=page,
                                error=f"diagnostic hard cap of {self.max_requests} requests reached")
        outcome = self.inner.search(query, **kwargs)
        results = list(getattr(outcome, "results", []) or [])
        self.requests.append({
            "seq": seq, "provider": self.name, "query": query, "page": page,
            "ok": bool(getattr(outcome, "ok", False)), "error": _safe_error(getattr(outcome, "error", None)),
            "results": len(results), "more_results_available": getattr(outcome, "more_results_available", None),
        })
        for rank, r in enumerate(results):
            self.raw_results.append({
                "request_seq": seq, "query": query, "page": page, "rank": getattr(r, "rank", rank),
                "url": r.url, "title": r.title or "", "snippet": r.snippet or "",
                "extra_snippet_count": len(getattr(r, "extra_snippets", None) or []),
            })
        return outcome


# ---------------------------------------------------------------------------
# Funnel
# ---------------------------------------------------------------------------

ERROR_PLACEHOLDER = "request failed (provider message not recorded)"


def _safe_error(error) -> str | None:
    """Provider error text can echo request details; only the fact of failure is kept."""
    return ERROR_PLACEHOLDER if error else None


def _slug(url: str) -> str | None:
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) >= 2 and parts[0].lower() == "in":
        return parts[1].lower()
    return None


def _is_profile(url: str) -> bool:
    return "linkedin.com" in urlparse(url).netloc.lower() and _slug(url) is not None


def max_provider_requests(config=None) -> dict:
    """Upper bound on requests discovery can make PER PROVIDER (every search
    call -- including org-wide paging, family paging and the plain-wording
    fallback -- is counted against the same budget)."""
    from research.linkedin import PeopleDiscoveryConfig

    cfg = config or PeopleDiscoveryConfig()
    return {
        "soft_budget": cfg.soft_budget,
        "extension_step": cfg.extension_step,
        "hard_ceiling": max(cfg.hard_ceiling, cfg.soft_budget),
        "serper_fallback_max": cfg.serper_max_requests,
        "max_brave_requests": max(cfg.hard_ceiling, cfg.soft_budget),
    }


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[-_/.]+", " ", (text or "").lower()).split())


def _slug_text(url: str) -> str:
    return _slug(url) or ""


def _own_match(raw: dict, watch_text: str) -> bool:
    """True when the raw result IS the watched person's profile: the watch text
    is in the profile URL slug or the result title. Snippets never count."""
    return _norm(watch_text) in _norm(_slug_text(raw["url"]) + " " + raw["title"])


_SLUG_WITH_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{6,10}")


def _truncate(text: str, limit: int = SNIPPET_LIMIT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _as_slug(value: str) -> str | None:
    """A profile slug from a slug or a full LinkedIn /in/ URL; None if it is neither."""
    value = (value or "").strip().strip("/")
    if "/" in value or "linkedin.com" in value.lower():
        return _slug(value if value.startswith("http") else "https://" + value)
    return value.lower() or None


def run_funnel(org, providers, *, watch=(), config=None, max_requests=DEFAULT_HARD_CAP,
               env=None, target_slugs=(), known_urls=(), known_text=None) -> dict:
    """Run discovery + assessment and return the funnel report (plain dict).

    ``org`` is a ``ResolvedOrganization``; ``providers`` are ready provider
    objects (they are wrapped in RecordingProviders). ``env`` is the mapping
    used ONLY to read the People mode / whether a Tavily key is configured --
    nothing from it is copied to the output except those two facts.

    ``known_urls`` / ``known_text`` are the optional known-profile identity leads
    (see research.known_profiles). Supplied text lives in memory for this run only:
    the report carries provenance and a short sanitised excerpt, never the text.
    """
    from commercial.people import _name_from_title, assess_people, deduplicate_profile_sources
    from commercial.people_association import assess_results
    from research.known_profiles import ORIGIN_SUPPLIED_TEXT, canonical_slug, sanitize_excerpt
    from research.linkedin import (PEOPLE_MODE_ENV, PeopleDiscoveryConfig, discover_linkedin, people_mode)

    env = os.environ if env is None else env
    watch = [w.strip() for w in watch if w and w.strip()]
    cfg = config or PeopleDiscoveryConfig()
    recorders = [RecordingProvider(p, max_requests=max_requests) for p in providers]

    result = discover_linkedin(org, recorders, config=cfg, known_profile_urls=known_urls, known_profile_text=known_text)
    metrics = result.family_outcome.coverage.metrics

    # --- Stage 1: raw provider results -----------------------------------
    raw = [r for rec in recorders for r in rec.raw_results]
    raw_profiles = [r for r in raw if _is_profile(r["url"])]
    raw_by_slug: dict[str, list[dict]] = {}
    for r in raw_profiles:
        raw_by_slug.setdefault(_slug(r["url"]), []).append(r)

    # --- Stage 3/4: assessment (same functions the report uses) ----------
    country = org.country or None
    people = assess_people(result.profile_candidates, organization_name=org.name, organization_country=country)
    confirmed_urls = {p.linkedin_url for p in people.confirmed}
    unverified_urls = {p.linkedin_url for p in people.unverified}
    rejected_urls = {r["url"] for r in people.rejected}

    supplied_urls = {s.url for s in result.profile_candidates if s.extra.get("origin") == ORIGIN_SUPPLIED_TEXT}
    profiles = []
    for group in deduplicate_profile_sources(result.profile_candidates):
        primary = max(group, key=lambda s: s.source_quality)
        supplied = any(s.extra.get("origin") == ORIGIN_SUPPLIED_TEXT for s in group)
        slug = _slug(primary.url)
        assessment = assess_results([(s.title or "", s.snippet or "") for s in group], org.name,
                                    organization_country=country)
        if primary.url in confirmed_urls:
            final = "confirmed (in final People list)"
        elif primary.url in unverified_urls:
            final = "unverified (NOT in final People list)"
        elif primary.url in rejected_urls:
            final = "rejected (NOT in final People list)"
        else:
            final = "not assessed (no profile text)"
        raw_hits = raw_by_slug.get(slug or "", [])
        # User-supplied text is never reproduced: title, snippet, evidence and reasons are cut to
        # short sanitised excerpts (<= 400 characters each; evidence <= 400 in total).
        if supplied:
            title_out = sanitize_excerpt(primary.title or "")
            snippet_out = sanitize_excerpt(primary.snippet or "")
            evidence_out = [sanitize_excerpt(" | ".join(assessment.evidence))] if assessment.evidence else []
            reasons_out = [sanitize_excerpt(x) for x in assessment.reasons]
        else:
            title_out, snippet_out = primary.title or "", (primary.snippet or "")[:SNIPPET_LIMIT]
            evidence_out, reasons_out = list(assessment.evidence), list(assessment.reasons)
        profiles.append({
            "slug": slug, "url": primary.url, "title": title_out,
            "origin": ORIGIN_SUPPLIED_TEXT if supplied else "provider_search",
            "snippet": snippet_out,
            "raw_result_count": len(raw_hits),
            "queries_that_found_it": sorted({h["query"] for h in raw_hits}),
            "providers": sorted({p for s in group for p in (s.extra.get("discovery_providers") or [])}),
            "association_status": assessment.status,
            "association_level": assessment.level,
            "reasons": reasons_out,
            "evidence": evidence_out,
            "location_conflict": assessment.location_conflict,
            "final_people_list": final,
            "in_final_people_list": primary.url in confirmed_urls,
        })

    dedup = {
        "raw_profile_results": len(raw_profiles),
        "unique_profile_slugs": len(raw_by_slug),
        "deduplicated_candidates": len(result.profile_candidates),
        "duplicate_results_collapsed": len(raw_profiles) - len(raw_by_slug),
        "slugs_seen_more_than_once": {s: len(v) for s, v in sorted(raw_by_slug.items()) if len(v) > 1},
    }

    # --- Watch targets: where is each lost? --------------------------------
    # A watched person is matched ONLY on their OWN profile: the URL slug or
    # the result title. Text inside someone else's snippet is a third-party
    # MENTION: it is reported separately and is never a profile, a dedup loss
    # or evidence of anything about the watched person.
    watch_report = {}
    for w in watch:
        raw_own = [r for r in raw_profiles if _own_match(r, w)]
        raw_mentions = [r for r in raw_profiles if not _own_match(r, w) and _norm(w) in _norm(r["snippet"])]
        prof = [p for p in profiles if _norm(w) in _norm(_slug_text(p["url"]) + " " + p["title"])]
        if raw_own and not prof:
            code = "lost_in_dedup_or_absorb"
            stage = "in raw results but absent from deduplicated candidates (lost in dedup/absorb)"
        elif prof and any(p["in_final_people_list"] for p in prof):
            code = "present_in_final_people_list"
            stage = "present in final People list"
        elif prof:
            code = "not_in_final_people_list"
            stage = "deduplicated candidate but NOT in final People list: " + "; ".join(
                sorted({p["association_status"] for p in prof}))
        elif raw_mentions:
            sources = sorted({r["url"] for r in raw_mentions})
            code = "mentioned_only_in_third_party_snippets"
            stage = ("mentioned_only_in_third_party_snippets: no profile of their own in the raw results; "
                     "named only in other people's snippets (source profiles: " + ", ".join(sources) + ")")
        else:
            code = "not_in_raw_results"
            stage = "NOT in raw provider results (missed in discovery / provider coverage)"
        watch_report[w] = {
            "stage": stage,
            "stage_code": code,
            "own_profile_raw_results": [{k: r[k] for k in ("url", "title", "query", "page", "rank")}
                                        | {"snippet": r["snippet"][:SNIPPET_LIMIT]} for r in raw_own],
            "third_party_mentions": [{"source_profile_url": r["url"], "source_profile_title": r["title"],
                                      "query": r["query"], "page": r["page"], "rank": r["rank"],
                                      "snippet": r["snippet"][:SNIPPET_LIMIT]} for r in raw_mentions],
            "profiles": prof,
        }
        # kept for older readers of the report
        watch_report[w]["raw_results"] = watch_report[w]["own_profile_raw_results"]

    # --- Lead follow-up trace: the individual results each follow-up query returned
    # A watch term that is an exact profile slug (or one seen in the raw results) is
    # also a target slug; a bare name is never guessed into a slug.
    targets = {t for t in (_as_slug(x) for x in target_slugs) if t}
    targets |= {w.lower() for w in watch if w.lower() in raw_by_slug or _SLUG_WITH_ID.fullmatch(w.lower())}
    target_list = sorted(targets)
    by_slug = {p["slug"]: p for p in profiles if p["slug"]}
    first_seq_for_query: dict[str, int] = {}
    for r in raw:
        first_seq_for_query.setdefault(r["query"], r["request_seq"])
    lead_trace = []
    for lead in metrics.get("lead_followups") or []:
        entry = {k: v for k, v in lead.items() if k != "error"}          # never the raw provider error text
        entry["errored"] = bool(lead.get("error"))
        results, seen = [], set()
        lead_queries = list(lead.get("queries") or ([lead["query"]] if lead.get("query") else []))
        entry["queries"] = lead_queries
        traced = [(q, first_seq_for_query[q]) for q in lead_queries if q in first_seq_for_query]
        if lead.get("status") == "queried" and traced:
            for r in (x for q, seq in traced for x in raw if x["query"] == q and x["request_seq"] == seq):
                slug = _slug(r["url"]) if _is_profile(r["url"]) else None
                dup_key = slug or r["url"]
                assessment = None
                if slug:
                    assessment = assess_results([(r["title"], r["snippet"])], org.name, organization_country=country,
                                                candidate_name=_name_from_title(r["title"]) or None)
                cand = by_slug.get(slug) if slug else None
                results.append({
                    "rank": r["rank"], "url": r["url"], "title": r["title"], "query": r["query"],
                    "provider": next((rec.name for rec in recorders if r in rec.raw_results), None),
                    "snippet": _truncate(r["snippet"]),
                    "is_linkedin_profile": slug is not None, "profile_slug": slug,
                    "matches_target_slug": (None if not targets else (slug in targets if slug else False)),
                    "duplicate_of_earlier_result": dup_key in seen,
                    "association_status": assessment.status if assessment else None,
                    "association_reasons": [_truncate(x, 300) for x in assessment.reasons] if assessment else [],
                    "candidate_association_status": cand["association_status"] if cand else None,
                    "in_final_people_list": bool(cand and cand["in_final_people_list"]),
                })
                seen.add(dup_key)
        entry["returned_results"] = results
        entry["returned_result_count"] = len(results)
        entry["unique_profiles_returned"] = len({x["profile_slug"] for x in results if x["profile_slug"]})
        entry["target_slug_returned"] = (None if not targets else any(x["matches_target_slug"] for x in results))
        lead_trace.append(entry)

    # --- Known profile URL leads: identity state + the probe's returned results ---------
    known_trace = []
    for rec in metrics.get("known_profile_leads") or []:
        entry = dict(rec)                                     # holds no raw URL, query string or error text
        results, seen = [], set()
        if rec.get("query") in first_seq_for_query:
            seq = first_seq_for_query[rec["query"]]
            for r in (x for x in raw if x["query"] == rec["query"] and x["request_seq"] == seq):
                slug = _slug(r["url"]) if _is_profile(r["url"]) else None
                assessment = None
                if slug:
                    assessment = assess_results([(r["title"], r["snippet"])], org.name, organization_country=country,
                                                candidate_name=_name_from_title(r["title"]) or None)
                cand = by_slug.get(slug) if slug else None
                dup_key = slug or r["url"]
                results.append({
                    "rank": r["rank"], "url": r["url"].split("?")[0].split("#")[0], "title": _truncate(r["title"], 300),
                    "snippet": _truncate(r["snippet"]), "is_linkedin_profile": slug is not None, "profile_slug": slug,
                    "matches_known_slug": bool(slug and rec.get("slug") and canonical_slug(slug) == rec["slug"]),
                    "duplicate_of_earlier_result": dup_key in seen,
                    "association_status": assessment.status if assessment else None,
                    "in_final_people_list": bool(cand and cand["in_final_people_list"]),
                })
                seen.add(dup_key)
        entry["returned_results"] = results
        entry["returned_result_count"] = len(results)
        known_trace.append(entry)

    tavily_key_present = bool((env.get("TAVILY_API_KEY") or "").strip())
    report = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "organization": {"name": org.name, "country": org.country},
        "effective_people_mode": cfg.mode,
        "people_mode_env_var": PEOPLE_MODE_ENV,
        "people_mode_resolved_from_env": people_mode() if env is os.environ else None,
        "tavily_key_configured": tavily_key_present,
        "tavily_called": bool(metrics.get("tavily_called")),
        "providers_passed_in": [r.name for r in recorders],
        "provider_requests": {r.name: len(r.requests) for r in recorders},
        "max_requests_limits": max_provider_requests(cfg),
        "diagnostic_hard_cap": max_requests,
        "provider_stop_reasons": metrics.get("provider_notes"),
        "queries_run": [q for r in recorders for q in r.requests],
        "discovery_request_log": [dict(e, error=_safe_error(e.get("error"))) for e in metrics.get("request_log") or []],
        "family_metrics": metrics.get("families"),
        "target_slugs": target_list,
        "lead_followups": lead_trace,
        "known_profile_leads": known_trace,
        "raw_profile_urls": sorted({r["url"] for r in raw_profiles}),
        "dedup": dedup,
        "funnel_counts": {
            "raw_results_all": len(raw),
            "raw_profile_results": len(raw_profiles),
            "deduplicated_profiles": len(profiles),
            "confirmed": len(people.confirmed),
            "unverified": len(people.unverified),
            "rejected": len(people.rejected),
            "final_people_list": len(people.confirmed),
        },
        "profiles": sorted(profiles, key=lambda p: (not p["in_final_people_list"], p["slug"] or "")),
        "rejected": [{"name": r["name"], "url": r["url"],
                      "reasons": [sanitize_excerpt(x) for x in r["reasons"]] if r["url"] in supplied_urls else r["reasons"]}
                     for r in people.rejected],
        "watch": watch_report,
    }
    return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _safe_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "org"


def render_markdown(report: dict) -> str:
    c = report["funnel_counts"]
    lines = [
        f"# People funnel diagnostic -- {report['organization']['name']}",
        f"Generated (UTC): {report['generated_at_utc']}",
        "",
        f"* Effective People mode: **{report['effective_people_mode']}**",
        f"* Tavily key configured: {report['tavily_key_configured']} / Tavily called: **{report['tavily_called']}**",
        f"* Providers passed in: {', '.join(report['providers_passed_in']) or '-'}",
        f"* Provider requests: {report['provider_requests']}",
        "",
        "## Funnel",
        f"raw profile results {c['raw_profile_results']} -> de-duplicated {c['deduplicated_profiles']} -> "
        f"confirmed {c['confirmed']} / unverified {c['unverified']} / rejected {c['rejected']} "
        f"-> final People list {c['final_people_list']}",
        "",
    ]
    if report["watch"]:
        lines.append("## Watched people")
        for w, info in report["watch"].items():
            lines.append(f"### `{w}`\n**{info['stage']}**")
            for p in info["profiles"]:
                lines.append(f"* {p['url']} -- {p['association_status']} -- {p['final_people_list']}")
                for reason in p["reasons"]:
                    lines.append(f"    * {reason}")
            for m in info.get("third_party_mentions", []):
                lines.append(f"* third-party mention (NOT their profile) in {m['source_profile_url']} "
                             f"via `{m['query']}`")
            lines.append("")
    if report.get("lead_followups"):
        lines += ["## Lead follow-ups (a mention is only a lead, never a person)",
                  "| status | lead | source profile | queries (one per spelling) |", "|---|---|---|---|"]
        for lead in report["lead_followups"]:
            lines.append(f"| {lead['status']} | {lead['name']} | {lead['source_profile_url']} | "
                         f"{' ; '.join(lead.get('queries') or [lead.get('query', '-')])} |")
        lines.append("")
        for lead in report["lead_followups"]:
            if lead.get("status") != "queried":
                continue
            lines.append(f"### Returned results for the follow-up on {lead['name']} "
                         f"({lead['returned_result_count']} results, target slug returned: {lead['target_slug_returned']})")
            if lead.get("errored"):
                lines.append("* the request failed (error text intentionally not recorded)")
            for x in lead["returned_results"]:
                flags = ", ".join(f for f, on in (("TARGET SLUG", x["matches_target_slug"]),
                                                  ("duplicate", x["duplicate_of_earlier_result"]),
                                                  ("in final People list", x["in_final_people_list"])) if on)
                lines.append(f"* #{x['rank']} {x['url']} -- {x['association_status'] or 'not a profile'}"
                             f"{' [' + flags + ']' if flags else ''}")
            lines.append("")
    if report.get("known_profile_leads"):
        lines += ["## Known profile leads (identity only, never employment evidence)",
                  "| state | slug | requests | resolved by | provenance | reason |", "|---|---|---|---|---|---|"]
        for k in report["known_profile_leads"]:
            lines.append(f"| {k['state']} | {k.get('slug') or '-'} | {k['requests']} | {k.get('resolved_by') or '-'} | "
                         f"{k.get('provenance') or '-'} | {k.get('reason') or '-'} |")
        lines.append("")
        for k in report["known_profile_leads"]:
            if k.get("excerpt"):
                lines.append(f"* supplied-text excerpt for {k['slug']} (sanitised, max 400 chars): {k['excerpt']}")
            for x in k.get("returned_results") or []:
                lines.append(f"* probe result #{x['rank']} {x['url']} -- {x['association_status'] or 'not a profile'}"
                             f"{' [EXACT SLUG]' if x['matches_known_slug'] else ''}")
        lines.append("")
    lines += ["## Queries run", "| # | provider | page | results | ok | query |", "|---|---|---|---|---|---|"]
    for q in report["queries_run"]:
        lines.append(f"| {q['seq']} | {q['provider']} | {q['page']} | {q['results']} | {q['ok']} | {q['query']} |")
    lines += ["", "## Profiles", "| status | final | url | title |", "|---|---|---|---|"]
    for p in report["profiles"]:
        lines.append(f"| {p['association_status']} | {p['final_people_list']} | {p['url']} | {p['title'].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def write_outputs(report: dict, out_dir: Path | None = None) -> list[Path]:
    """Write timestamped .json/.md files; never overwrite an existing file."""
    out_dir = Path(out_dir) if out_dir is not None else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"people_funnel_{_safe_name(report['organization']['name'])}_{stamp}"
    n = 0
    while True:
        suffix = f"_{n}" if n else ""
        json_path = out_dir / f"{stem}{suffix}.json"
        md_path = out_dir / f"{stem}{suffix}.md"
        try:
            with open(json_path, "x", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2, ensure_ascii=False)
            break
        except FileExistsError:
            n += 1
    try:
        with open(md_path, "x", encoding="utf-8") as fh:
            fh.write(render_markdown(report))
    except FileExistsError:  # pragma: no cover - paired name already taken
        md_path = out_dir / f"{stem}{suffix}_md.md"
        with open(md_path, "x", encoding="utf-8") as fh:
            fh.write(render_markdown(report))
    return [json_path, md_path]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--org", required=True)
    ap.add_argument("--country", default="")
    ap.add_argument("--watch", action="append", default=[], help="substring to trace (repeatable), e.g. jihad")
    ap.add_argument("--target-slug", action="append", default=[],
                    help="exact LinkedIn profile slug (or /in/ URL) of the person being traced (repeatable)")
    ap.add_argument("--known-url", action="append", default=[],
                    help="a known LinkedIn /in/<slug> profile URL to resolve (repeatable; identity lead only)")
    ap.add_argument("--known-profile-text-file", default=None,
                    help="UTF-8 file with the visible text of the profile (needs exactly one --known-url); "
                         "kept in memory for the run, never written to the reports")
    ap.add_argument("--max-requests", type=int, default=DEFAULT_HARD_CAP)
    ap.add_argument("--live", action="store_true", help="allow live Brave requests (also needs --confirm-live-brave)")
    ap.add_argument("--confirm-live-brave", action="store_true")
    args = ap.parse_args(argv)

    from research.known_profiles import canonicalize_profile_url
    from research.linkedin import PeopleDiscoveryConfig

    valid_known = [u for u in args.known_url if canonicalize_profile_url(u) is not None]
    if args.known_profile_text_file and len(args.known_url) != 1:
        print("--known-profile-text-file needs exactly one --known-url. Nothing was run.")
        return 2

    cfg = PeopleDiscoveryConfig()
    limits = max_provider_requests(cfg)
    print(f"Effective People mode: {cfg.mode}")
    print(f"Maximum Brave requests: {min(limits['max_brave_requests'], args.max_requests)} "
          f"(soft {limits['soft_budget']}, hard ceiling {limits['hard_ceiling']}, retries disabled)")

    if args.known_url:
        print(f"Known profile URLs: {len(valid_known)} valid, {len(args.known_url) - len(valid_known)} invalid "
              f"(at most {cfg.known_profile_max} slug probes, counted in the same budget)")

    if not (args.live and args.confirm_live_brave):
        print("DRY RUN: no provider was constructed and no request was made. "
              "Add --live --confirm-live-brave to run for real.")
        return 0
    if cfg.mode != "brave_only":
        print(f"Refusing to run: People mode is {cfg.mode!r}, expected 'brave_only'.")
        return 2

    from research.organization import ResolvedOrganization
    from research.providers.brave import BRAVE_KEY_ENV, BraveSearchProvider

    # The provider reads its own key (BRAVE_KEY_ENV == BRAVE_SEARCH_API_KEY);
    # this script never reads, stores or prints the key itself.
    brave = BraveSearchProvider(max_retries=0)
    if not brave.is_configured():
        print(f"Brave is not configured ({BRAVE_KEY_ENV} is not set in this session). No requests made.")
        return 2
    org = ResolvedOrganization(name=args.org, country=args.country or None, website=None,
                               linkedin_company_url=None, website_was_supplied=False, linkedin_was_supplied=False)
    known_text = None
    if args.known_profile_text_file:
        try:
            known_text = Path(args.known_profile_text_file).read_text(encoding="utf-8")
        except OSError:
            print("The profile text file could not be read. No requests made.")
            return 2
    report = run_funnel(org, [brave], watch=args.watch, config=cfg, max_requests=args.max_requests,
                        target_slugs=args.target_slug, known_urls=args.known_url, known_text=known_text)
    paths = write_outputs(report)
    print("Requests made:", report["provider_requests"])
    print("Funnel:", report["funnel_counts"])
    for w, info in report["watch"].items():
        print(f"Watch '{w}': {info['stage']}")
    for p in paths:
        print("Wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""LinkedIn acquisition -- the single, consolidated pipeline.

    Organization
        -> Company LinkedIn discovery
        -> Public profile discovery (taxonomy query families, adaptive)
        -> Candidate pool, deduplicated by LinkedIn profile, with provenance
        -> Profile/snippet evidence capture (search-engine results only)
        -> (classification happens downstream in commercial.people)

Only publicly indexed search results are used: no LinkedIn login, no
authenticated scraping, no bypass of LinkedIn access controls.

People Intelligence -- Brave optimisation
-----------------------------------------
* Provider MODE (ACCOUNT_RADAR_PEOPLE_MODE, authoritative for People only;
  the general research order Brave -> Tavily -> Serper is unchanged):
    - "brave_only" (default): Brave is the People provider. Tavily is never
      called -- not even when TAVILY_API_KEY is configured.
    - "brave_tavily": Tavily first, Brave supplemental (maximum coverage;
      for when the Tavily quota allows it).
  In both modes Serper is a controlled fallback only: it runs when the
  People providers failed or found fewer than `serper_thin_threshold`
  organization-associated profiles, with its own small budget.
* Query FAMILIES come from commercial/people_taxonomy.json
  (people_discovery.query_families): generic, organization-agnostic search
  terms in priority order. For Brave, queries are restricted to public
  profile pages (`site:linkedin.com/in "<organization>" <term>`); if that
  wording returns nothing for an account, a plain wording is tried, and
  used for the rest of the run when it works better.
* Adaptive DISCOVERY YIELD: after every request the loop measures the new
  organization-associated people it produced (weighted by their
  enterprise-technology-sales value) and picks the next best action: the
  next page of a productive query, the next term of a productive family, or
  a family not yet explored. Families stop after repeated zero yield; the
  run stops when discovery saturates.
* SEARCH BUDGET (requests, never people): a floor (organization-wide search
  + the first term of every family), a soft budget, and extensions up to a
  hard ceiling only while the recent yield stays high. There is no cap on
  the number of qualified people.
"""

from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlparse

from research.organization import ResolvedOrganization
from research.known_profiles import (
    ALREADY_DISCOVERED,
    INVALID_URL,
    KNOWN_DIMENSION,
    ORIGIN_SUPPLIED_TEXT,
    RESOLVED_VIA_SEARCH,
    RESOLVED_VIA_SUPPLIED_TEXT,
    UNRESOLVED_NO_CONTENT,
    build_probe_query,
    canonical_slug,
    canonicalize_profile_url,
    invalid_url_reason,
    match_results,
    sanitize_excerpt,
    source_from_supplied_text,
)
from research.people_leads import extract_leads, name_spellings, same_person_name
from research.providers.base import SearchProvider
from research.providers.router import CoverageAssessment, FamilyOutcome, ProviderFamilyRun, QueryOutcome
from research.sources import (
    SOURCE_TYPE_LINKEDIN_COMPANY,
    SOURCE_TYPE_LINKEDIN_PROFILE,
    Source,
    dedupe_sources,
    score_source,
)

# ---------------------------------------------------------------------------
# Mode, providers and configuration
# ---------------------------------------------------------------------------

PEOPLE_MODE_ENV = "ACCOUNT_RADAR_PEOPLE_MODE"
PEOPLE_MODE_BRAVE_ONLY = "brave_only"
PEOPLE_MODE_BRAVE_TAVILY = "brave_tavily"
PEOPLE_MODES = (PEOPLE_MODE_BRAVE_ONLY, PEOPLE_MODE_BRAVE_TAVILY)

# brave_tavily order (maximum-coverage mode); Serper is always fallback-only.
PEOPLE_PROVIDER_PRIORITY = ("tavily", "brave", "serper")
PEOPLE_FINAL_FALLBACK_PROVIDERS = ("serper",)
_EXCLUDED_IN_BRAVE_ONLY = ("tavily",)

# Default soft search budget (requests per provider per account run). A
# budget of SEARCH REQUESTS -- never a limit on the number of people.
PEOPLE_QUERY_BUDGET = 30

GENERAL_DIMENSION = "general_workforce"
LEAD_DIMENSION = "lead_followup"
RESTRICTED_PROFILE_SITE = "site:linkedin.com/in"
DIALECT_RESTRICTED = "restricted"
DIALECT_PLAIN = "plain"


def people_mode() -> str:
    """The People provider mode from ACCOUNT_RADAR_PEOPLE_MODE; anything
    unset or unrecognised is brave_only."""
    value = (os.getenv(PEOPLE_MODE_ENV) or "").strip().lower()
    return value if value in PEOPLE_MODES else PEOPLE_MODE_BRAVE_ONLY


@dataclass
class PeopleDiscoveryConfig:
    """Every People-discovery threshold in one place (all configurable)."""

    mode: str = field(default_factory=people_mode)
    # search budget (requests per provider)
    soft_budget: int = PEOPLE_QUERY_BUDGET
    extension_step: int = 10
    hard_ceiling: int = 45
    extension_min_weighted_per_request: float = 1.0
    # organization-wide search paging
    org_page2_min_new_associated: int = 3
    org_page2_min_profiles: int = 5
    org_deeper_min_new_associated: int = 3
    org_max_pages: int = 5
    # family paging
    family_page2_min_new_associated: int = 3
    family_page3_min_new_associated: int = 2
    family_max_pages: int = 3
    later_term_page2_min_weighted: float = 3.0
    # yield / stopping
    family_zero_stop: int = 2
    saturation_window: int = 6
    saturation_min_weighted: float = 1.5
    # wording fallback, errors, Serper
    restricted_zero_switch: int = 2
    error_circuit: int = 2
    serper_thin_threshold: int = 5
    serper_max_requests: int = 10
    num_results: int = 20
    # Executive title queries (the first `executive_terms` terms of a family):
    # plain-wording fallback only when the restricted query returned ZERO
    # LinkedIn profile results; at most this many per run. Counted in the budget.
    executive_fallback_max: int = 2
    # Lead follow-up: a named senior executive in a third-party appointment
    # snippet earns a profile-restricted search for their own profile.
    # At most `lead_followup_max` leads per provider run, each with at most
    # `lead_variant_max` requests (one per spelling: 'Al-Name' / 'Al Name'; separate
    # queries, because Brave OR-groups are unreliable). Every request is counted in
    # the same budget (soft 30 / hard ceiling 45).
    lead_followup_max: int = 2
    lead_variant_max: int = 2
    # Known profile URL (identity lead): at most `known_profile_probe_max` slug probe per URL and
    # `known_profile_max` probes per run; each probe is counted in the same budget and runs BEFORE
    # the name-variant follow-up. A URL never creates a person by itself.
    known_profile_probe_max: int = 1
    known_profile_max: int = 3


# Discovery-yield weight of one new associated person, by PEOPLE group
# (enterprise-technology-sales value). Other = associated but unclassified.
_YIELD_WEIGHTS = {
    "Technology Leadership": 1.0,
    "Cybersecurity": 0.9,
    "Infrastructure / Cloud": 0.9,
    "Digital / Data": 0.9,
    "Procurement": 0.8,
    "Other": 0.25,
}


def person_yield_weight(groups: list[str]) -> float:
    return max((_YIELD_WEIGHTS.get(g, 0.25) for g in groups), default=0.25)


# ---------------------------------------------------------------------------
# Query families and wording
# ---------------------------------------------------------------------------

def _taxonomy() -> dict:
    from commercial.people_taxonomy import get_taxonomy  # lazy: avoids an import cycle
    return get_taxonomy()


def query_families() -> list[dict]:
    """People query families from the taxonomy, in priority order."""
    families = (_taxonomy().get("people_discovery") or {}).get("query_families") or []
    return [dict(f, terms=list(f.get("terms") or []), weight=float(f.get("weight", 0.5)))
            for f in families if f.get("terms")]


def plain_term(term: str) -> str:
    """A taxonomy term without Brave operators, for the plain wording:
    `("Chief Technology Officer" OR CIO)` -> `Chief Technology Officer CIO`."""
    text = re.sub(r"[()\"]", " ", term)
    text = re.sub(r"\bOR\b", " ", text)
    return " ".join(text.split())


def build_people_query(dialect: str, organization: str, term: str | None = None, country: str = "") -> str:
    """One People query. Restricted (Brave): public profile pages only, the
    organization name as an exact phrase. Plain (other providers): no
    operators (Phase 1C-H4: operator-heavy queries come back empty on some
    providers); country only on the organization-wide query, and only when
    the organization name does not already contain it."""
    if dialect == DIALECT_RESTRICTED:
        return f'{RESTRICTED_PROFILE_SITE} "{organization}"' + (f" {term}" if term else "")
    if term:
        return f"{organization} {plain_term(term)} linkedin"
    query = f"{organization} linkedin"
    if country and country.lower() not in organization.lower():
        query = f"{query} {country}"
    return query


def build_lead_query(dialect: str, organization: str, name: str) -> str:
    """The follow-up search for a named lead's OWN profile at the resolved
    organisation. Restricted: `site:linkedin.com/in "<Name>" "<Org>"`."""
    if dialect == DIALECT_RESTRICTED:
        return f'{RESTRICTED_PROFILE_SITE} "{name}" "{organization}"'
    return f"{name} {organization} linkedin"


def _title_name(title: str) -> str:
    """The person's name as a profile title gives it: the text before the first ' - ' / ' | '."""
    return re.split(r"\s+[-–—|]\s+", (title or "").strip(), maxsplit=1)[0].strip()


def _person_queries(company: str, country: str) -> list[tuple[str, str]]:
    """[(dimension, plain query), ...] for the whole People query plan, in
    priority order (organization-wide search, then every family term)."""
    out = [(GENERAL_DIMENSION, build_people_query(DIALECT_PLAIN, company, None, country))]
    for family in query_families():
        out += [(f"family:{family['id']}", build_people_query(DIALECT_PLAIN, company, term))
                for term in family["terms"]]
    return out


def _linkedin_coverage_assessment(outcomes: list[QueryOutcome]) -> CoverageAssessment:
    """LinkedIn-specific coverage: sparse if the primary provider errored
    broadly, OR if essentially nothing that looks like a real /in/ or
    /company/ URL came back across the whole dimension set.
    """
    total = len(outcomes)
    errored = sum(1 for o in outcomes if not o.ok)
    profile_urls = set()
    for o in outcomes:
        for r in o.results:
            path = urlparse(r.url).path.lower()
            host = urlparse(r.url).netloc.lower()
            if "linkedin.com" in host and ("/in/" in path or "/company/" in path):
                profile_urls.add(r.url.rstrip("/"))
    metrics = {"queries_run": total, "queries_errored": errored, "linkedin_urls_found": len(profile_urls)}
    if total == 0:
        return CoverageAssessment(True, "no LinkedIn queries were run", metrics)
    if errored == total:
        return CoverageAssessment(True, "primary provider errored on every LinkedIn query", metrics)
    if errored / total >= 0.6:
        return CoverageAssessment(True, f"primary provider errored on {errored}/{total} LinkedIn queries", metrics)
    if len(profile_urls) == 0:
        return CoverageAssessment(True, "zero linkedin.com/in or /company URLs found across all dimensions", metrics)
    return CoverageAssessment(False, "LinkedIn discovery coverage looks healthy", metrics)


class LinkedInDiscoveryResult:
    def __init__(
        self,
        company_page: Source | None,
        profile_candidates: list[Source],
        family_outcome: FamilyOutcome,
        dimension_counts: dict,
    ):
        self.company_page = company_page
        self.profile_candidates = profile_candidates
        self.family_outcome = family_outcome
        self.dimension_counts = dimension_counts

    @property
    def all_sources(self) -> list[Source]:
        out = list(self.profile_candidates)
        if self.company_page:
            out = [self.company_page] + out
        return out


class LinkedInDiscoveryResult:
    def __init__(
        self,
        company_page: Source | None,
        profile_candidates: list[Source],
        family_outcome: FamilyOutcome,
        dimension_counts: dict,
    ):
        self.company_page = company_page
        self.profile_candidates = profile_candidates
        self.family_outcome = family_outcome
        self.dimension_counts = dimension_counts

    @property
    def all_sources(self) -> list[Source]:
        out = list(self.profile_candidates)
        if self.company_page:
            out = [self.company_page] + out
        return out


def order_people_providers(providers: list[SearchProvider]) -> list[SearchProvider]:
    """brave_tavily order: PEOPLE_PROVIDER_PRIORITY first (Tavily, Brave,
    Serper), any other provider after them in its original order."""
    rank = {name: i for i, name in enumerate(PEOPLE_PROVIDER_PRIORITY)}
    indexed = list(enumerate(providers))
    indexed.sort(key=lambda ip: (rank.get(getattr(ip[1], "name", ""), len(rank)), ip[0]))
    return [p for _, p in indexed]


def people_provider_plan(providers: list[SearchProvider], mode: str) -> tuple[list[SearchProvider], list[SearchProvider]]:
    """(People providers in order, controlled fallbacks). In brave_only mode
    Tavily is removed by NAME, so no method of it is ever called."""
    def name(p) -> str:
        return getattr(p, "name", "")

    fallbacks = [p for p in providers if name(p) in PEOPLE_FINAL_FALLBACK_PROVIDERS]
    if mode == PEOPLE_MODE_BRAVE_TAVILY:
        primaries = [p for p in order_people_providers(providers) if name(p) not in PEOPLE_FINAL_FALLBACK_PROVIDERS]
    else:
        primaries = [p for p in providers
                     if name(p) not in PEOPLE_FINAL_FALLBACK_PROVIDERS and name(p) not in _EXCLUDED_IN_BRAVE_ONLY]
        primaries.sort(key=lambda p: 0 if name(p) == "brave" else 1)
    return primaries, fallbacks


def _profile_key(url: str) -> str:
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) >= 2 and parts[0].lower() == "in":
        return parts[1].lower()
    return url.rstrip("/").lower()


def _assess(title: str, snippet: str, organization: str, country: str | None = None) -> tuple[bool, list[str]]:
    """(associated?, PEOPLE groups) with the SAME rules commercial.people
    applies -- discovery yield only counts someone the People list would
    keep as a CONFIRMED current employee. Only the result's own title and
    snippet are used: supplemental / extra snippets never count."""
    from commercial.people import classify_person_text, people_groups  # lazy
    from commercial.people_association import CONFIRMED_CURRENT, assess_employment
    a = assess_employment(title, snippet, organization, organization_country=country)
    if a.status != CONFIRMED_CURRENT:
        return False, []
    c = classify_person_text(a.basis_text)
    return True, people_groups({"seniority": c["seniority"], "functions": c["functions"],
                                "technology_areas": c["technology_areas"], "role_id": c["role_id"]})


# ---------------------------------------------------------------------------
# The adaptive discovery loop
# ---------------------------------------------------------------------------

@dataclass
class _Result:
    profiles: int = 0
    new_associated: int = 0
    weighted: float = 0.0
    more: bool | None = None


@dataclass
class _FamilyState:
    family: dict
    next_term: int = 0
    zero_streak: int = 0
    actions: int = 0
    weighted: float = 0.0
    pending_page: tuple[int, int] | None = None
    stopped: str | None = None

    @property
    def dimension(self) -> str:
        return f"family:{self.family['id']}"

    def next_action(self) -> tuple[str, int, int] | None:
        if self.stopped:
            return None
        if self.pending_page:
            return ("page", *self.pending_page)
        if self.next_term < len(self.family["terms"]):
            return ("term", self.next_term, 0)
        return None

    def priority(self, kind: str) -> float:
        value = self.family["weight"] * (self.weighted / max(self.actions, 1))
        return value * 1.25 if kind == "page" else value

    @property
    def promising(self) -> bool:
        return self.zero_streak == 0 and self.weighted > 0 and self.next_action() is not None


class _PeopleDiscovery:
    def __init__(self, org: ResolvedOrganization, config: PeopleDiscoveryConfig, company_source: Source | None,
                 known_urls=(), known_text=None):
        self.organization = org.name
        self.country = org.country or ""
        self.cfg = config
        self.company_source = company_source
        self.families = query_families()
        self.by_key: dict[str, Source] = {}
        self.associated: set[str] = set()
        self.dimension_counts: dict[str, int] = {GENERAL_DIMENSION: 0}
        self.dimension_counts.update({f"family:{f['id']}": 0 for f in self.families})
        self.family_metrics = {f["id"]: {"requests": 0, "new_associated": 0, "weighted_yield": 0.0, "stop_reason": None}
                               for f in self.families}
        self.provider_metrics: dict[str, dict] = {}
        self.request_log: list[dict] = []
        self.leads: dict[str, dict] = {}
        self.runs: list[ProviderFamilyRun] = []
        self.queries: list[str] = []
        self.known: list[dict] = []                   # identity leads (known profile URLs); never people
        self._known_text: dict[str, str] = {}         # slug -> user-supplied text; this run's memory only
        self._known_attempted: set[str] = set()
        self._known_probes = 0
        self._init_known(known_urls, known_text)

    # -- known profile URLs (identity leads) -------------------------------------

    def _init_known(self, urls, text) -> None:
        seen: set[str] = set()
        if isinstance(urls, str):
            urls = [urls]
        for raw_url in list(urls or []):
            canon = canonicalize_profile_url(raw_url)
            if canon is None:
                self.known.append({"state": INVALID_URL, "reason": invalid_url_reason(raw_url), "slug": None,
                                   "canonical_url": None, "requests": 0, "exact_match_returned": False,
                                   "probe_failed": False, "resolved_by": None, "provenance": None, "excerpt": "",
                                   "query": None})
                continue
            if canon.slug in seen:
                continue
            seen.add(canon.slug)
            self.known.append({"state": "pending", "reason": None, "slug": canon.slug, "canonical_url": canon.url,
                               "requests": 0, "exact_match_returned": False, "probe_failed": False,
                               "resolved_by": None, "provenance": None, "excerpt": "", "query": None})
        if isinstance(text, str):
            if len(seen) == 1:                       # one string is unambiguous only for exactly one URL
                self._known_text[next(iter(seen))] = text
        elif isinstance(text, dict):
            for raw_url, value in text.items():
                canon = canonicalize_profile_url(raw_url)
                if canon is not None and canon.slug in seen and isinstance(value, str):
                    self._known_text[canon.slug] = value

    def _known_present(self, slug: str) -> bool:
        return any(canonical_slug(k) == slug for k in self.by_key)

    def _known_apply_text(self, rec: dict) -> None:
        """Supplied visible text -> a labelled Source for the exact canonical URL. It is only
        a candidate: the unchanged association gate decides its status."""
        slug = rec["slug"]
        text = self._known_text.pop(slug, None)
        if not text or self._known_present(slug):
            return
        canon = canonicalize_profile_url(rec["canonical_url"])
        src = source_from_supplied_text(canon, text)
        if src is None:
            return
        self.by_key[_profile_key(src.url)] = src
        rec.update(state=RESOLVED_VIA_SUPPLIED_TEXT, resolved_by="supplied_text", provenance=ORIGIN_SUPPLIED_TEXT,
                   excerpt=sanitize_excerpt(f"{src.title}\n{src.snippet}"))

    def _finalize_known(self) -> None:
        for rec in self.known:
            if rec["state"] != "pending":
                continue
            if self._known_present(rec["slug"]):
                rec.update(state=RESOLVED_VIA_SEARCH, resolved_by=rec["resolved_by"] or "later_discovery")
                continue
            self._known_apply_text(rec)
            if rec["state"] == "pending":
                rec["state"] = UNRESOLVED_NO_CONTENT

    # -- results -------------------------------------------------------------

    def _absorb(self, outcome: QueryOutcome, provider: str, dimension: str, page: int) -> _Result:
        res = _Result(more=outcome.more_results_available)
        for r in outcome.results:
            parsed = urlparse(r.url)
            host, path = parsed.netloc.lower(), parsed.path.lower()
            if "linkedin.com" not in host:
                continue
            if "/company/" in path:
                if self.company_source is None:
                    self.company_source = Source.from_raw(r, source_type=SOURCE_TYPE_LINKEDIN_COMPANY,
                                                          family="linkedin_company")
                    self.company_source.extra["linkedin_kind"] = "company"
                continue
            if "/in/" not in path:
                continue
            res.profiles += 1
            key = _profile_key(r.url)
            hit = {"provider": provider, "dimension": dimension, "page": page, "query": outcome.query}
            source = self.by_key.get(key)
            if source is None:
                source = Source.from_raw(r, source_type=SOURCE_TYPE_LINKEDIN_PROFILE, family="linkedin_people")
                source.extra.update({"linkedin_kind": "in", "discovery_dimension": dimension,
                                     "discovery_providers": [provider], "supplemental_snippets": [],
                                     "extra_snippets": [], "discovery_hits": [hit]})
                self.by_key[key] = source
                self.dimension_counts[dimension] = self.dimension_counts.get(dimension, 0) + 1
            else:
                source.extra["discovery_hits"].append(hit)
                if provider not in source.extra["discovery_providers"]:
                    source.extra["discovery_providers"].append(provider)
                snippet = (r.snippet or "").strip()
                extras = source.extra["supplemental_snippets"]
                if snippet and snippet != source.snippet and snippet not in extras:
                    if len(snippet) > len(source.snippet or ""):
                        if source.snippet:
                            extras.append(source.snippet)
                        source.snippet = snippet
                    else:
                        extras.append(snippet)
            # Provider extra excerpts: retained as context, never as evidence.
            for extra in getattr(r, "extra_snippets", None) or []:
                if extra and extra not in source.extra["extra_snippets"] and extra != source.snippet:
                    source.extra["extra_snippets"].append(extra)
            self._collect_leads(r, source)
            if key not in self.associated:
                associated, groups = _assess(source.title or "", source.snippet or "", self.organization, self.country)
                if associated:
                    self.associated.add(key)
                    res.new_associated += 1
                    res.weighted += person_yield_weight(groups)
        res.weighted = round(res.weighted, 4)
        return res

    # -- leads (third-party appointment mentions) -----------------------------

    def _collect_leads(self, raw, source: Source) -> None:
        """A mention is only a pointer. Nothing here creates a Source/person."""
        own = _title_name(source.title or "")
        for text in [raw.snippet or "", *(getattr(raw, "extra_snippets", None) or [])]:
            for lead in extract_leads(text, organization=self.organization, own_name=own):
                if lead.key not in self.leads:
                    self.leads[lead.key] = {
                        "name": lead.name, "title_phrase": lead.title_phrase, "status": "pending",
                        "name_variants": name_spellings(lead.name)[:max(1, self.cfg.lead_variant_max)],
                        "queries": [], "requests": 0, "stop_reason": None,
                        "source_profile_url": raw.url, "source_query": raw.query}

    def _already_discovered(self, name: str) -> bool:
        return any(same_person_name(name, _title_name(s.title or "")) for s in self.by_key.values())

    def next_lead(self) -> dict | None:
        for lead in self.leads.values():
            if lead["status"] != "pending":
                continue
            if self._already_discovered(lead["name"]):
                lead["status"] = "skipped_already_discovered"
                continue
            return lead
        return None

    # -- one provider ----------------------------------------------------------

    def run_provider(self, provider: SearchProvider, *, budget: int, hard_ceiling: int, extend: bool) -> None:
        cfg = self.cfg
        name = provider.name
        pm = {"requests": 0, "pages_beyond_first": 0, "new_associated": 0, "weighted_yield": 0.0,
              "people_per_request": 0.0, "dialect": None, "budget": budget, "stop_reason": None}
        self.provider_metrics[name] = pm
        if not provider.is_configured():
            pm["stop_reason"] = "not configured"
            self.runs.append(ProviderFamilyRun(provider=name, outcomes=[], used=False))
            return

        paging = bool(getattr(provider, "supports_pagination", False))
        rich = bool(getattr(provider, "supports_rich_snippets", False))
        state = {"dialect": DIALECT_RESTRICTED if name == "brave" else DIALECT_PLAIN, "errors": 0,
                 "restricted_zero": 0, "restricted_hits": 0, "plain_productive": 0, "executive_fallbacks": 0, "lead_queries": 0}
        pm["dialect"] = state["dialect"]
        outcomes: list[QueryOutcome] = []
        window: deque[float] = deque(maxlen=max(1, cfg.saturation_window))

        def search(query: str, page: int) -> QueryOutcome:
            kwargs: dict = {"num_results": cfg.num_results}
            if rich:
                kwargs["rich_snippets"] = True
            if paging:
                kwargs["page"] = page
            try:
                outcome = provider.search(query, **kwargs)
            except Exception as exc:  # noqa: BLE001 - provider misbehaved beyond its contract
                outcome = QueryOutcome(query=query, provider=name, error=f"unexpected error: {exc}")
            outcomes.append(outcome)
            self.queries.append(query)
            pm["requests"] += 1
            if page:
                pm["pages_beyond_first"] += 1
            return outcome

        def record(outcome: QueryOutcome, dimension: str, page: int, res: _Result | None,
                   fallback: str | None = None, windowed: bool = True) -> None:
            entry = {
                "provider": name, "query": outcome.query, "page": page, "dimension": dimension,
                "profiles": res.profiles if res else 0, "new_associated": res.new_associated if res else 0,
                "weighted_yield": res.weighted if res else 0.0, "error": outcome.error}
            if fallback:
                entry["fallback"] = fallback
            self.request_log.append(entry)
            if windowed:
                window.append(res.weighted if res else 0.0)
            if res:
                pm["new_associated"] += res.new_associated
                pm["weighted_yield"] = round(pm["weighted_yield"] + res.weighted, 4)

        def request(term: str | None, page: int, dimension: str, executive: bool = False) -> _Result | None:
            outcome = search(build_people_query(state["dialect"], self.organization, term, self.country), page)
            if not outcome.ok:
                state["errors"] += 1
                record(outcome, dimension, page, None)
                return None
            state["errors"] = 0
            res = self._absorb(outcome, name, dimension, page)
            record(outcome, dimension, page, res)
            if state["dialect"] != DIALECT_RESTRICTED:
                return res
            # Executive title query that found no LinkedIn profile at all: one
            # plain-wording retry (capped per run, counted in the budget). The
            # restricted wording stays the default for everything else.
            if (executive and page == 0 and res.profiles == 0 and state["executive_fallbacks"] < cfg.executive_fallback_max
                    and pm["requests"] < budget):
                state["executive_fallbacks"] += 1
                plain = search(build_people_query(DIALECT_PLAIN, self.organization, term, self.country), 0)
                state["restricted_zero"] += 1          # zero restricted results is evidence about the wording
                if plain.ok:
                    state["errors"] = 0
                    plain_res = self._absorb(plain, name, dimension, 0)
                    record(plain, dimension, 0, plain_res, fallback="executive")
                    if plain_res.profiles:
                        state["plain_productive"] += 1
                    if state["restricted_zero"] >= cfg.restricted_zero_switch and state["plain_productive"]:
                        state["dialect"] = DIALECT_PLAIN
                        pm["dialect"] = DIALECT_PLAIN
                    return _Result(profiles=res.profiles + plain_res.profiles,
                                   new_associated=res.new_associated + plain_res.new_associated,
                                   weighted=round(res.weighted + plain_res.weighted, 4), more=plain_res.more)
                state["errors"] += 1
                record(plain, dimension, 0, None, fallback="executive")
                return res
            if outcome.results:
                state["restricted_hits"] += 1
                return res
            # Restricted wording returned nothing: try the plain wording, but only
            # while restricted wording is unproven for this account.
            if (not executive and page == 0 and state["restricted_hits"] < 3
                    and state["restricted_zero"] < cfg.restricted_zero_switch and pm["requests"] < budget):
                state["restricted_zero"] += 1
                plain = search(build_people_query(DIALECT_PLAIN, self.organization, term, self.country), 0)
                if plain.ok:
                    state["errors"] = 0
                    plain_res = self._absorb(plain, name, dimension, 0)
                    record(plain, dimension, 0, plain_res)
                    if plain_res.profiles:
                        state["plain_productive"] += 1
                    res = _Result(profiles=res.profiles + plain_res.profiles,
                                  new_associated=res.new_associated + plain_res.new_associated,
                                  weighted=round(res.weighted + plain_res.weighted, 4), more=plain_res.more)
                else:
                    state["errors"] += 1
                    record(plain, dimension, 0, None)
                if state["restricted_zero"] >= cfg.restricted_zero_switch and state["plain_productive"]:
                    state["dialect"] = DIALECT_PLAIN
                    pm["dialect"] = DIALECT_PLAIN
            return res

        def circuit_open() -> bool:
            return state["errors"] >= cfg.error_circuit

        def known_step() -> bool:
            """Known profile URLs: reuse existing results (free), else ONE slug probe per URL
            (budgeted, capped, before any name follow-up), else user-supplied text. Returns True
            when a request was made."""
            for rec in self.known:
                if rec["state"] != "pending":
                    continue
                slug = rec["slug"]
                if self._known_present(slug):
                    rec.update(state=ALREADY_DISCOVERED, resolved_by="existing_results")
                    continue
                if slug in self._known_attempted:
                    continue
                self._known_attempted.add(slug)
                capped = self._known_probes >= cfg.known_profile_max or cfg.known_profile_probe_max < 1
                if capped or pm["requests"] >= budget or circuit_open():
                    rec["reason"] = "probe_cap" if capped else ("budget" if pm["requests"] >= budget else "provider_errors")
                    self._known_apply_text(rec)
                    continue
                self._known_probes += 1
                query = build_probe_query(state["dialect"] == DIALECT_RESTRICTED, slug)
                outcome = search(query, 0)
                rec.update(requests=1, query=query)
                if not outcome.ok:
                    state["errors"] += 1
                    rec["probe_failed"] = True               # the fact of failure only, never the provider text
                    record(outcome, KNOWN_DIMENSION, 0, None, windowed=False)
                else:
                    state["errors"] = 0
                    res = self._absorb(outcome, name, KNOWN_DIMENSION, 0)
                    record(outcome, KNOWN_DIMENSION, 0, res, windowed=False)
                    if match_results(slug, outcome.results):  # exact canonical slug only
                        rec.update(state=RESOLVED_VIA_SEARCH, exact_match_returned=True, resolved_by="probe")
                if rec["state"] == "pending":
                    self._known_apply_text(rec)
                return True
            return False

        def lead_step() -> bool:
            """One follow-up search for a named lead's own profile (budgeted, capped).

            A lead may use one request per spelling of its name (at most `lead_variant_max`,
            as separate queries): it stops as soon as a profile titled with that name is
            known, on an error, or when the budget or the circuit stops it. Each request is
            counted in `pm["requests"]` like any other."""
            if pm["requests"] >= budget or circuit_open():
                return False
            lead = next((v for v in self.leads.values()
                         if v["status"] == "in_progress" and v.get("provider") == name), None)
            if lead is not None and self._already_discovered(lead["name"]):   # found meanwhile
                lead.update(status="queried", stop_reason="profile_found")
                lead = None
            if lead is None:
                if state["lead_queries"] >= cfg.lead_followup_max:
                    return False
                lead = self.next_lead()
                if lead is None:
                    return False
                state["lead_queries"] += 1
                lead.update(status="in_progress", provider=name, profiles_returned=0)
            spelling = lead["name_variants"][lead["requests"]]
            query = build_lead_query(state["dialect"], self.organization, spelling)
            outcome = search(query, 0)
            lead["queries"].append(query)
            lead["requests"] += 1
            lead["query"] = lead["queries"][0]
            if not outcome.ok:
                state["errors"] += 1
                lead.update(status="queried", stop_reason="error", error=outcome.error)
                record(outcome, LEAD_DIMENSION, 0, None, windowed=False)
                return True
            state["errors"] = 0
            res = self._absorb(outcome, name, LEAD_DIMENSION, 0)
            record(outcome, LEAD_DIMENSION, 0, res, windowed=False)
            lead["profiles_returned"] += res.profiles
            if self._already_discovered(lead["name"]):
                lead.update(status="queried", stop_reason="profile_found")
            elif lead["requests"] >= len(lead["name_variants"]):
                lead.update(status="queried", stop_reason="variants_exhausted")
            return True

        families = [_FamilyState(f) for f in self.families]

        def family_step(fs: _FamilyState, kind: str, term_index: int, page: int, *, floor_probe: bool = False) -> None:
            before = pm["requests"]
            if kind == "term":
                fs.next_term += 1
            fs.pending_page = None
            is_exec = kind == "term" and term_index < int(fs.family.get("executive_terms") or 0)
            res = request(fs.family["terms"][term_index], page, fs.dimension, executive=is_exec)
            fm = self.family_metrics[fs.family["id"]]
            fm["requests"] += pm["requests"] - before
            if res is None:
                return  # provider error: not held against the family
            fs.actions += 1
            fs.weighted += res.weighted
            fm["new_associated"] += res.new_associated
            fm["weighted_yield"] = round(fm["weighted_yield"] + res.weighted, 4)
            # A guaranteed floor probe that finds nothing new must not make a
            # family that has already produced people look exhausted (its
            # productive first term could not page otherwise). A family that
            # has produced nothing counts every zero, so it still stops after two.
            hold = floor_probe and fs.weighted > 0
            fs.zero_streak = 0 if res.new_associated else (fs.zero_streak if hold else fs.zero_streak + 1)
            if fs.zero_streak >= cfg.family_zero_stop:
                fs.stopped = "zero yield"
                fm["stop_reason"] = "zero yield"
                return
            if paging and res.more is not False:
                if term_index == 0:
                    threshold = cfg.family_page2_min_new_associated if page == 0 else cfg.family_page3_min_new_associated
                    if res.new_associated >= threshold and page + 1 < cfg.family_max_pages:
                        fs.pending_page = (term_index, page + 1)
                elif page == 0 and res.weighted >= cfg.later_term_page2_min_weighted:
                    fs.pending_page = (term_index, 1)

        def saturated() -> bool:
            if len(window) < cfg.saturation_window or sum(window) >= cfg.saturation_min_weighted:
                return False
            return not any(fs.promising for fs in families)

        def finish(reason: str) -> None:
            pm["stop_reason"] = reason
            pm["budget"] = budget
            pm["people_per_request"] = round(pm["new_associated"] / pm["requests"], 2) if pm["requests"] else 0.0
            for fs in families:
                fm = self.family_metrics[fs.family["id"]]
                if fm["stop_reason"] is None and fs.stopped:
                    fm["stop_reason"] = fs.stopped
            self.runs.append(ProviderFamilyRun(provider=name, outcomes=outcomes, used=bool(outcomes)))

        # 1. Organization-wide search, paged while productive.
        page = 0
        res = request(None, 0, GENERAL_DIMENSION)
        while res is not None and paging and not circuit_open():
            if page == 0:
                productive = (res.new_associated >= cfg.org_page2_min_new_associated
                              or res.profiles >= cfg.org_page2_min_profiles)
            else:
                productive = res.new_associated >= cfg.org_deeper_min_new_associated
            if not productive or res.more is False or page + 1 >= cfg.org_max_pages:
                break
            if pm["requests"] + len(families) >= budget:  # keep room for the floor
                break
            page += 1
            res = request(None, page, GENERAL_DIMENSION)

        # 2. Floor: the first term of every family.
        for fs in families:
            if circuit_open():
                return finish("provider errors")
            if pm["requests"] >= budget:
                break
            family_step(fs, "term", 0, 0)

        # 2b. Executive floor: the remaining executive title groups of a family
        # always run, whatever the generic queries yielded (the adaptive
        # scheduler would otherwise starve them after one zero-yield query).
        for fs in families:
            for _ in range(max(0, int(fs.family.get("executive_terms") or 0) - 1)):
                if circuit_open():
                    return finish("provider errors")
                if pm["requests"] >= budget or fs.next_term >= len(fs.family["terms"]):
                    break
                saved_page = fs.pending_page
                family_step(fs, "term", fs.next_term, 0, floor_probe=True)
                if fs.pending_page is None and saved_page and not fs.stopped:
                    fs.pending_page = saved_page   # keep the first term's paging opportunity

        # 2c. Known profile URLs first (identity leads), then lead follow-up: names announced
        # in third-party snippets so far. A resolved profile makes its name follow-up unnecessary.
        while known_step():
            pass
        while lead_step():
            pass

        # 3. Adaptive expansion.
        while True:
            if circuit_open():
                return finish("provider errors")
            if known_step():
                continue
            if lead_step():             # leads found by later results
                continue
            if saturated():
                return finish("saturated")
            if pm["requests"] >= budget:
                recent = list(window)
                rate = sum(recent) / len(recent) if recent else 0.0
                if extend and budget < hard_ceiling and rate >= cfg.extension_min_weighted_per_request:
                    budget = min(budget + cfg.extension_step, hard_ceiling)
                    continue
                return finish("hard ceiling" if budget >= hard_ceiling else "budget reached")
            best: tuple[float, _FamilyState, tuple] | None = None
            for fs in families:
                action = fs.next_action()
                if action is None:
                    continue
                score = fs.priority(action[0])
                if best is None or score > best[0]:
                    best = (score, fs, action)
            if best is None:
                return finish("query plan exhausted")
            _, fs, (kind, term_index, page) = best
            family_step(fs, kind, term_index, page)

    # -- assembly --------------------------------------------------------------

    def result(self, *, mode: str, primary_names: list[str], max_candidates: int | None) -> LinkedInDiscoveryResult:
        self._finalize_known()
        for lead in self.leads.values():       # final status of leads never reached / cut short
            if lead["status"] == "in_progress":
                lead.update(status="queried", stop_reason="budget" if not lead.get("stop_reason") else lead["stop_reason"])
            elif lead["status"] == "pending" and self._already_discovered(lead["name"]):
                lead["status"] = "skipped_already_discovered"
        candidates = list(self.by_key.values())
        for s in candidates:
            s.source_quality = score_source(s)
        candidates = dedupe_sources(candidates)
        candidates.sort(key=lambda s: -s.source_quality)
        if max_candidates is not None:
            candidates = candidates[:max_candidates]

        used = [r.provider for r in self.runs if r.used]
        primary = next((n for n in primary_names if n in used), None)
        supplemental = [n for n in used if n != primary]
        requests = sum(m["requests"] for m in self.provider_metrics.values())
        coverage = CoverageAssessment(
            sparse=not self.associated,
            reason=f"{len(self.associated)} organization-associated profiles from {', '.join(used) or 'no provider'}",
            metrics={
                "people_mode": mode,
                "queries_planned": len(_person_queries(self.organization, self.country)),
                "requests": requests,
                "linkedin_profiles": len(candidates),
                "associated_profiles": len(self.associated),
                "people_per_request": round(len(self.associated) / requests, 2) if requests else 0.0,
                "tavily_called": bool(self.provider_metrics.get("tavily", {}).get("requests")),
                "providers": self.provider_metrics,
                "families": self.family_metrics,
                "provider_notes": {n: m["stop_reason"] for n, m in self.provider_metrics.items()},
                "request_log": self.request_log,
                "lead_followups": [dict(v) for v in self.leads.values()],
                "known_profile_leads": [dict(v) for v in self.known],
            },
        )
        family_outcome = FamilyOutcome(
            family="linkedin_people",
            queries=self.queries,
            runs=self.runs,
            fallback_triggered=bool(supplemental),
            fallback_reason=(f"people discovery also used {', '.join(supplemental)}" if supplemental else None),
            coverage=coverage,
        )
        return LinkedInDiscoveryResult(company_page=self.company_source, profile_candidates=candidates,
                                       family_outcome=family_outcome, dimension_counts=self.dimension_counts)


def discover_linkedin(
    org: ResolvedOrganization,
    providers: list[SearchProvider],
    *,
    max_candidates: int | None = None,
    query_budget: int | None = None,
    config: PeopleDiscoveryConfig | None = None,
    known_profile_urls=(),
    known_profile_text=None,
) -> LinkedInDiscoveryResult:
    """The single LinkedIn acquisition pipeline (see module docstring).

    `query_budget` overrides the soft search budget per provider.
    `max_candidates` is kept for backward compatibility only and is ignored
    unless a caller passes it: there is no cap on candidates or people.

    `known_profile_urls` / `known_profile_text` (both optional, no UI) supply KNOWN LinkedIn profile
    URLs as identity leads (see research.known_profiles). A URL never creates a person: it is reused
    from existing results at no cost, else resolved by at most one slug probe (counted in the budget,
    run before the name follow-up), else by user-supplied visible text ({url: text}, or one string
    for exactly one URL) which goes through the unchanged association gate. The outcome is reported
    in metrics["known_profile_leads"].
    """
    cfg = config or PeopleDiscoveryConfig()
    company = org.name

    company_source: Source | None = None
    if org.linkedin_company_url:
        company_source = Source(
            url=org.linkedin_company_url.rstrip("/"),
            domain="linkedin.com",
            title=f"{company} — LinkedIn Company Page",
            source_type=SOURCE_TYPE_LINKEDIN_COMPANY,
            snippet="LinkedIn company page." + (" (explicitly supplied)" if org.linkedin_was_supplied else " (discovered)"),
            discovered_via_query="(organization resolution)",
            discovery_provider="resolved",
            family="linkedin_company",
            extra={"linkedin_kind": "company"},
        )
        company_source.source_quality = score_source(company_source)

    soft = query_budget if query_budget is not None else cfg.soft_budget
    hard = max(cfg.hard_ceiling, soft)
    discovery = _PeopleDiscovery(org, cfg, company_source, known_profile_urls, known_profile_text)
    primaries, fallbacks = people_provider_plan(providers, cfg.mode)

    for provider in primaries:
        discovery.run_provider(provider, budget=soft, hard_ceiling=hard, extend=True)

    primaries_worked = any(r.used and any(o.ok for o in r.outcomes) for r in discovery.runs)
    if fallbacks and (not primaries_worked or len(discovery.associated) < cfg.serper_thin_threshold):
        for provider in fallbacks:
            budget = min(cfg.serper_max_requests, soft)
            discovery.run_provider(provider, budget=budget, hard_ceiling=budget, extend=False)
    else:
        for provider in fallbacks:
            discovery.provider_metrics[provider.name] = {
                "requests": 0, "pages_beyond_first": 0, "new_associated": 0, "weighted_yield": 0.0,
                "people_per_request": 0.0, "dialect": None, "budget": 0,
                "stop_reason": f"not needed ({len(discovery.associated)} associated profiles)"}
            discovery.runs.append(ProviderFamilyRun(provider=provider.name, outcomes=[], used=False))

    return discovery.result(mode=cfg.mode, primary_names=[getattr(p, "name", "") for p in primaries],
                            max_candidates=max_candidates)

"""People / Stakeholder Intelligence -- deterministic classification layer
built entirely on top of the existing LinkedIn acquisition pipeline
(research.linkedin.discover_linkedin / research.acquisition.AcquisitionRun).

This module does not perform any acquisition of its own. It consumes the
Source objects discover_linkedin() already produced (company page +
profile_candidates), classifies each candidate deterministically from its
own title/snippet text against the generic people_taxonomy.json catalogue,
and produces a ranked, evidence-backed list of Person records.

Design constraints (see people_taxonomy.py's own docstring for the parallel
constraint on the taxonomy file itself):

  - No organization, sector, or country name is ever hardcoded here.
  - `discovery_dimension` (which query surfaced a candidate) is NEVER used
    as the primary basis for a role/function/technology chip -- it is at
    most a small corroborating factor in the relevance score, exactly as
    research.linkedin's own docstring says it must be treated ("role/
    function classification is a later AI-layer stage, not here" -- this
    module IS that later stage, and it classifies from title/snippet text,
    not from which search query happened to surface the candidate).
  - No AI/LLM call is used for v1 classification -- purely deterministic
    keyword matching against the taxonomy, mirroring the same
    evidence-first philosophy commercial/evidence_mapping.py already uses
    for capability mapping (a term must actually appear in the evidence
    text; nothing is inferred from context alone).
  - This module never imports from, or modifies the behavior of,
    commercial.evidence_mapping, commercial.opportunities,
    commercial.signals, or research.analysis.qualification -- it is purely
    additive and reads only Source objects and plain organization data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from commercial.people_association import (
    CONFIRMED_CURRENT,
    NEEDS_VERIFICATION,
    Assessment,
    assess_results,
    fallback_current_title,
    split_blob,
    title_has_usable_role,
    title_segments,
)
from commercial.people_taxonomy import get_taxonomy
from research.sources import Source

# ---------------------------------------------------------------------------
# Person model
# ---------------------------------------------------------------------------


@dataclass
class Person:
    person_id: str
    name: str
    current_title: str
    organization_name: str
    # "confirmed" (== confirmed_current: an explicit CURRENT employer claim for
    # the target entity) | "needs_verification" | "unconfirmed" (rejected).
    # Legacy "weak" is still accepted by the scoring helpers.
    organization_association: str
    linkedin_url: str
    source_urls: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)
    location: str | None = None

    seniority: str | None = None  # "c_level" | "director" | "head" | "manager" | None
    functions: list[str] = field(default_factory=list)  # function ids, e.g. ["cybersecurity", "it"]
    technology_areas: list[str] = field(default_factory=list)  # technology-area ids
    influence_type: str | None = None  # see _INFLUENCE_* constants below
    role_id: str | None = None  # matched catalogue role (people_taxonomy.json), if any
    role_label: str | None = None

    relevance_score: float = 0.0
    confidence: float = 0.0

    evidence: list[str] = field(default_factory=list)  # human-readable evidence excerpts
    recent_signals: list[str] = field(default_factory=list)  # never auto-populated in v1 (see build_people)
    chips: list[dict] = field(default_factory=list)  # [{"category": ..., "label": ..., "evidence": ...}, ...]
    score_factors: dict = field(default_factory=dict)  # transparent, debuggable scoring breakdown

    # Provenance (guardrail: retain everything that fed classification)
    discovery_dimensions: list[str] = field(default_factory=list)
    discovery_providers: list[str] = field(default_factory=list)  # e.g. ["brave", "tavily"]
    discovery_hit_count: int = 0  # distinct (provider, query, page) results that surfaced this person
    classification_text: str = ""
    classification_matched_terms: list[str] = field(default_factory=list)
    organization_association_evidence: list[str] = field(default_factory=list)
    # Why a needs_verification candidate is not a confirmed stakeholder
    # (empty for confirmed people).
    verification_reasons: list[str] = field(default_factory=list)
    # Supplemental text (Brave extra_snippets / other results' snippets /
    # related-profile blocks): kept for context, never used as evidence.
    context_snippets: list[str] = field(default_factory=list)

    discovered_at: str | None = None
    last_verified_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "person_id": self.person_id,
            "name": self.name,
            "current_title": self.current_title,
            "organization_name": self.organization_name,
            "organization_association": self.organization_association,
            "linkedin_url": self.linkedin_url,
            "source_urls": self.source_urls,
            "source_types": self.source_types,
            "location": self.location,
            "seniority": self.seniority,
            "functions": self.functions,
            "technology_areas": self.technology_areas,
            "influence_type": self.influence_type,
            "role_id": self.role_id,
            "role_label": self.role_label,
            "people_groups": people_groups(self.to_group_input()),
            "relevance_score": self.relevance_score,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "recent_signals": self.recent_signals,
            "chips": self.chips,
            "score_factors": self.score_factors,
            "discovery_dimensions": self.discovery_dimensions,
            "discovery_providers": self.discovery_providers,
            "discovery_hit_count": self.discovery_hit_count,
            "sales_priority_tier": sales_priority(self.to_group_input())[0],
            "sales_priority_label": sales_priority(self.to_group_input())[1],
            "classification_text": self.classification_text,
            "classification_matched_terms": self.classification_matched_terms,
            "organization_association_evidence": self.organization_association_evidence,
            "verification_reasons": self.verification_reasons,
            "context_snippets": self.context_snippets,
            "discovered_at": self.discovered_at,
            "last_verified_at": self.last_verified_at,
        }

    def to_group_input(self) -> dict:
        return {"seniority": self.seniority, "functions": self.functions,
                "technology_areas": self.technology_areas, "role_id": self.role_id}


# ---------------------------------------------------------------------------
# Influence-type labels -- see module docstring / guardrail #5: a strict
# hierarchy, never presented as established fact, always evidence-gated.
# ---------------------------------------------------------------------------

INFLUENCE_DECISION_MAKER = "decision_maker"
INFLUENCE_POTENTIAL_DECISION_MAKER = "potential_decision_maker"
INFLUENCE_TECHNICAL_STAKEHOLDER = "technical_stakeholder"
INFLUENCE_LIKELY_TECHNICAL_STAKEHOLDER = "likely_technical_stakeholder"
INFLUENCE_PROCUREMENT_STAKEHOLDER = "procurement_stakeholder"
INFLUENCE_BUSINESS_STAKEHOLDER = "business_stakeholder"

_INFLUENCE_DISPLAY_LABELS = {
    INFLUENCE_DECISION_MAKER: "Decision Maker",
    INFLUENCE_POTENTIAL_DECISION_MAKER: "Potential Decision Maker",
    INFLUENCE_TECHNICAL_STAKEHOLDER: "Technical Stakeholder",
    INFLUENCE_LIKELY_TECHNICAL_STAKEHOLDER: "Likely Technical Stakeholder",
    INFLUENCE_PROCUREMENT_STAKEHOLDER: "Procurement Stakeholder",
    INFLUENCE_BUSINESS_STAKEHOLDER: "Business Stakeholder",
}

_SENIORITY_RANK = {"c_level": 3, "director": 2, "head": 2, "manager": 1}

# Function ids that count as "technology/decision-relevant" for the
# decision-maker gate -- deliberately excludes plain "applications"/
# "enterprise_architecture" on their own from the STRONG decision-maker
# tier (still count toward technical-stakeholder / potential tiers).
_DECISION_RELEVANT_FUNCTIONS = {"it", "cybersecurity", "cloud", "infrastructure", "digital_transformation", "data"}


# ---------------------------------------------------------------------------
# Term matching -- deterministic, word-boundary regex, mirrors the pattern
# commercial/evidence_mapping.py already uses for capability-term matching
# (a fresh, local implementation -- this module does not import that
# module's private helpers, to keep the two layers fully independent).
# ---------------------------------------------------------------------------


def _term_pattern(term: str, *, case_sensitive: bool = False) -> re.Pattern:
    flags = 0 if case_sensitive else re.I
    return re.compile(r"\b" + re.escape(term) + r"\b", flags)


def _match_category_terms(text: str, entries: list[dict]) -> dict[str, list[str]]:
    """For a taxonomy category (roles / functions / technology_areas),
    returns {entry_id: [matched literal terms]} for every entry that has
    at least one term present in `text`. Short, ambiguous acronyms (IT, AI,
    AWS, ERP) are matched case-SENSITIVELY as their own separate term list
    per entry (`case_sensitive_short_terms`) so the common English word
    "it"/"ai" is never mistaken for the acronym -- job-title text
    capitalizes these consistently ("IT Manager"), ordinary prose does not.
    """
    out: dict[str, list[str]] = {}
    for entry in entries:
        entry_text = text
        # `ignore_phrases`: boilerplate that must not count as evidence for
        # this entry (e.g. "professional network" is not a Network role).
        for phrase in entry.get("ignore_phrases", []):
            entry_text = _term_pattern(phrase).sub(" ", entry_text)
        matched: list[str] = []
        for term in entry.get("terms", []):
            if _term_pattern(term).search(entry_text):
                matched.append(term)
        for term in entry.get("case_sensitive_short_terms", []):
            if _term_pattern(term, case_sensitive=True).search(entry_text):
                matched.append(term)
        # `context_exclude` + `strong_terms`: when the text shows a
        # different domain (e.g. physical security), keep the entry only if
        # an unambiguous term matched (e.g. "information security").
        if matched and entry.get("context_exclude"):
            if any(_term_pattern(t).search(entry_text) for t in entry["context_exclude"]):
                strong = {t.lower() for t in entry.get("strong_terms", [])}
                matched = [m for m in matched if m.lower() in strong]
        if matched:
            out[entry["id"]] = matched
    return out


_TITLE_DASH_RE = re.compile(r"\s+[-\u2013\u2014]\s+")
_AMPERSAND_RE = re.compile(r"\s*&\s*")


def _normalize_title_text(text: str) -> str:
    """Classification-only normalisation of title punctuation: "IT & Digital"
    -> "IT and Digital", "General Manager - IT" -> "General Manager IT".
    (Association uses the original text.)"""
    return _TITLE_DASH_RE.sub(" ", _AMPERSAND_RE.sub(" and ", text or ""))


def classify_person_text(text: str) -> dict:
    """Deterministic classification of one block of text (title + snippet)
    against the taxonomy. Returns a dict with the best-matching specific
    role (if any), its seniority, every matched function id, every matched
    technology-area id, and the flat list of literal matched terms (for
    provenance/evidence display).
    """
    taxonomy = get_taxonomy()
    text = _normalize_title_text(text)

    role_matches = _match_category_terms(text, taxonomy["roles"])
    function_matches = _match_category_terms(text, taxonomy["functions"])
    tech_matches = _match_category_terms(text, taxonomy["technology_areas"])

    best_role = None
    best_role_terms: list[str] = []
    if role_matches:
        # Prefer the role whose matched term is LONGEST (most specific --
        # "chief information security officer" over a shorter coincidental
        # partial match), then by catalogue order for determinism.
        role_by_id = {r["id"]: r for r in taxonomy["roles"]}
        best_id = max(
            role_matches,
            key=lambda rid: (max(len(t) for t in role_matches[rid]),),
        )
        best_role = role_by_id[best_id]
        best_role_terms = role_matches[best_id]

    seniority = best_role["seniority"] if best_role else _fallback_seniority(text)

    all_matched_terms = list(best_role_terms)
    for terms in function_matches.values():
        all_matched_terms.extend(terms)
    for terms in tech_matches.values():
        all_matched_terms.extend(terms)

    return {
        "role_id": best_role["id"] if best_role else None,
        "role_label": best_role["label"] if best_role else None,
        "seniority": seniority,
        "functions": sorted(function_matches.keys()),
        "technology_areas": sorted(tech_matches.keys()),
        "matched_terms": sorted(set(all_matched_terms), key=str.lower),
    }


# Fallback seniority signal used ONLY for the scoring factor when no
# specific catalogued role matched -- never used to manufacture a Role
# chip (a Role chip requires an actual taxonomy role match).
_GENERIC_SENIORITY_RE = re.compile(
    r"\b(chief \w+ officer|vice president|vp|director|head of|senior manager|manager|lead)\b", re.I
)


# Gulf-region title conventions (generic, not organization-specific):
# GM / DGM / AGM and (deputy/assistant) general manager lead a function
# (director level); a section or unit head runs a team (manager level).
_GULF_DIRECTOR_RE = re.compile(r"\b(?:deputy general manager|assistant general manager|general manager)\b", re.I)
_GULF_DIRECTOR_ABBR_RE = re.compile(r"\b(?:GM|DGM|AGM)\b")
_SECTION_HEAD_RE = re.compile(r"\b(?:section head|unit head|head of section|head of unit)\b", re.I)
_CHIEF_COMPOUND_RE = re.compile(r"\bchief [a-z]+(?: (?:and|&) [a-z]+)? officer\b", re.I)


def _fallback_seniority(text: str) -> str | None:
    # The title conventions below are read from the headline zone only (the
    # profile title comes first), so a PAST "General Manager at ..." in a
    # long snippet never promotes someone's current seniority.
    zone = (text or "").split("\n", 1)[0][:200]
    if _CHIEF_COMPOUND_RE.search(zone):
        return "c_level"
    if _GULF_DIRECTOR_RE.search(zone) or _GULF_DIRECTOR_ABBR_RE.search(zone):
        return "director"
    if _SECTION_HEAD_RE.search(zone):
        return "manager"
    m = _GENERIC_SENIORITY_RE.search(text)
    if not m:
        return None
    token = m.group(0).lower()
    if "chief" in token:
        return "c_level"
    if "vice president" in token or token == "vp":
        return "director"
    if "director" in token:
        return "director"
    if "head of" in token:
        return "head"
    return "manager"


# ---------------------------------------------------------------------------
# Organization association -- independent, local implementation (does not
# import commercial.evidence_mapping's private org-association helpers, to
# keep this module fully decoupled). Deliberately simple and explainable:
# literal organization-name substring match = "confirmed"; a majority of
# the organization's own distinct name tokens present = "weak"; otherwise
# "unconfirmed".
# ---------------------------------------------------------------------------


def _org_core_tokens(organization_name: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9]+", organization_name.lower())
    return [t for t in tokens if len(t) >= 3]


def assess_organization_association(
    text: str, organization_name: str, *, organization_country: str | None = None
) -> tuple[str, list[str]]:
    """Returns (association_level, evidence) for ONE block of profile text
    (title, then snippet). association_level is "confirmed" (confirmed_current),
    "needs_verification" or "unconfirmed" (rejected).

    Only an explicit CURRENT-employer claim for the target entity confirms;
    see commercial.people_association for the full rules (field provenance,
    current vs historical, target vs bare brand vs sibling entity, conflicts,
    location). For structured input use `assess_employment` /
    `build_people`, which keep the title and snippet apart.
    """
    if not organization_name or not (text or "").strip():
        return "unconfirmed", []
    title, snippet = split_blob(text)
    a = assess_results([(title or text, snippet)], organization_name, organization_country=organization_country)
    return a.level, list(a.evidence)


# ---------------------------------------------------------------------------
# Chip generation -- every chip must be traceable to an actual match found
# above. No chip is ever generated from discovery_dimension alone.
# ---------------------------------------------------------------------------


def generate_chips(
    *,
    role_label: str | None,
    functions: list[str],
    technology_areas: list[str],
    influence_type: str | None,
    organization_association: str,
    source_types: list[str],
) -> list[dict]:
    taxonomy = get_taxonomy()
    function_labels = {f["id"]: f["label"] for f in taxonomy["functions"]}
    tech_labels = {t["id"]: t["label"] for t in taxonomy["technology_areas"]}

    chips: list[dict] = []
    if role_label:
        chips.append({"category": "role", "label": role_label, "evidence": "matched role term in title/snippet"})
    for fid in functions:
        chips.append(
            {"category": "function", "label": function_labels.get(fid, fid), "evidence": "matched function term in title/snippet"}
        )
    for tid in technology_areas:
        chips.append(
            {"category": "technology", "label": tech_labels.get(tid, tid), "evidence": "matched technology term in title/snippet"}
        )
    if influence_type:
        chips.append(
            {
                "category": "influence",
                "label": _INFLUENCE_DISPLAY_LABELS[influence_type],
                "evidence": "derived from seniority + function/technology evidence",
            }
        )
    if "linkedin_profile" in source_types or "linkedin_company" in source_types:
        chips.append({"category": "evidence", "label": "LinkedIn", "evidence": "sourced from a LinkedIn profile/company page"})
    if organization_association != "confirmed":
        chips.append(
            {"category": "evidence", "label": "Needs Verification", "evidence": f"organization association is {organization_association!r}, not confirmed"}
        )
    return chips


# ---------------------------------------------------------------------------
# Influence-type derivation -- strict hierarchy per guardrail #5. Never
# "Decision Maker" from seniority alone.
# ---------------------------------------------------------------------------


def derive_influence_type(*, seniority: str | None, functions: list[str], technology_areas: list[str], organization_association: str) -> str | None:
    if organization_association == "unconfirmed":
        return None
    has_decision_relevant_function = bool(set(functions) & _DECISION_RELEVANT_FUNCTIONS)
    has_procurement = "procurement" in functions
    has_tech_evidence = bool(technology_areas) or bool(functions)

    if seniority == "c_level" and has_decision_relevant_function:
        return INFLUENCE_DECISION_MAKER
    if seniority in ("director", "head") and has_decision_relevant_function:
        return INFLUENCE_POTENTIAL_DECISION_MAKER
    if seniority == "c_level" and not has_decision_relevant_function:
        # Senior, but no function/technology evidence of remit -- do not
        # overclaim; still worth surfacing as a potential decision maker,
        # qualified.
        return INFLUENCE_POTENTIAL_DECISION_MAKER
    if has_procurement:
        return INFLUENCE_PROCUREMENT_STAKEHOLDER
    if technology_areas and seniority in ("manager", None):
        return INFLUENCE_LIKELY_TECHNICAL_STAKEHOLDER if organization_association in ("weak", "needs_verification") else INFLUENCE_TECHNICAL_STAKEHOLDER
    if has_tech_evidence:
        return INFLUENCE_LIKELY_TECHNICAL_STAKEHOLDER
    if organization_association == "confirmed":
        return INFLUENCE_BUSINESS_STAKEHOLDER
    return None


# ---------------------------------------------------------------------------
# Relevance scoring -- deterministic, explainable, factor-based (guardrail
# #6). Never an opaque single AI-produced number.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PEOPLE groups -- the six summary groups on the PEOPLE page. Data-driven:
# every role / function / technology entry in people_taxonomy.json carries
# the group it belongs to, so a recognised technical or procurement signal
# can never silently fall into "Other".
# ---------------------------------------------------------------------------

GROUP_TECH_LEADERSHIP = "Technology Leadership"
GROUP_CYBERSECURITY = "Cybersecurity"
GROUP_INFRA_CLOUD = "Infrastructure / Cloud"
GROUP_DIGITAL_DATA = "Digital / Data"
GROUP_PROCUREMENT = "Procurement"
GROUP_OTHER = "Other"
PEOPLE_GROUP_ORDER = (GROUP_TECH_LEADERSHIP, GROUP_CYBERSECURITY, GROUP_INFRA_CLOUD,
                      GROUP_DIGITAL_DATA, GROUP_PROCUREMENT, GROUP_OTHER)
_GROUP_KEYS = {"infrastructure_cloud": GROUP_INFRA_CLOUD, "cybersecurity": GROUP_CYBERSECURITY,
               "digital_data": GROUP_DIGITAL_DATA, "procurement": GROUP_PROCUREMENT}
_TECHNOLOGY_GROUPS = {GROUP_INFRA_CLOUD, GROUP_CYBERSECURITY, GROUP_DIGITAL_DATA}
_LEADERSHIP_SENIORITY = {"c_level", "director", "head"}


def taxonomy_group_maps(taxonomy: dict | None = None) -> dict[str, dict[str, str]]:
    """{"roles": {id: group}, "functions": {...}, "technology_areas": {...}}."""
    taxonomy = taxonomy or get_taxonomy()
    return {
        category: {e["id"]: _GROUP_KEYS[e["group"]] for e in taxonomy[category] if e.get("group") in _GROUP_KEYS}
        for category in ("roles", "functions", "technology_areas")
    }


def _non_leadership_ids(taxonomy: dict | None = None) -> set[tuple[str, str]]:
    """(category, id) of taxonomy entries flagged technology_leadership_signal:
    false (telecom vocabulary): they place a person in their group but never,
    on their own, make the person Technology Leadership."""
    taxonomy = taxonomy or get_taxonomy()
    return {(c, e["id"]) for c in ("roles", "functions", "technology_areas") for e in taxonomy[c]
            if e.get("technology_leadership_signal") is False}


def people_groups(person: dict) -> list[str]:
    """The PEOPLE groups one person belongs to (a person can be in several).

    * Specialist groups come from the person's matched role, functions and
      technology areas (see the "group" field in people_taxonomy.json).
    * Technology Leadership requires BOTH leadership seniority (C-level,
      director, head) AND technology relevance -- a CFO, HR or procurement
      director is not Technology Leadership.
    * Telecom vocabulary (technology_leadership_signal: false) places a
      person in Infrastructure / Cloud but never by itself makes them
      Technology Leadership.
    * Other is residual: only people with no recognised signal at all.
    """
    maps = taxonomy_group_maps()
    exempt = _non_leadership_ids()
    groups: set[str] = set()
    leadership_relevant: set[str] = set()
    signals = [("roles", person.get("role_id"))]
    signals += [("functions", f) for f in person.get("functions") or []]
    signals += [("technology_areas", t) for t in person.get("technology_areas") or []]
    for category, entry_id in signals:
        group = maps[category].get(entry_id or "")
        if group:
            groups.add(group)
            if (category, entry_id) not in exempt:
                leadership_relevant.add(group)
    if person.get("seniority") in _LEADERSHIP_SENIORITY and leadership_relevant & _TECHNOLOGY_GROUPS:
        groups.add(GROUP_TECH_LEADERSHIP)
    if not groups:
        groups.add(GROUP_OTHER)
    return [g for g in PEOPLE_GROUP_ORDER if g in groups]


def group_members(people: list[dict]) -> dict[str, set[str]]:
    """Members (person_ids) of each PEOPLE group, in display order."""
    members: dict[str, set[str]] = {g: set() for g in PEOPLE_GROUP_ORDER}
    for p in people:
        for g in people_groups(p):
            members[g].add(p.get("person_id"))
    return members


# ---------------------------------------------------------------------------
# Sales-priority tier -- how useful a stakeholder is for enterprise
# technology sales. Used ONLY to order the PEOPLE page; deliberately kept
# separate from relevance_score, which other Account Radar sections read.
# ---------------------------------------------------------------------------

SALES_PRIORITY_TIERS = {
    1: "Technology decision maker",
    2: "Senior technology leader",
    3: "Technology manager",
    4: "Procurement leader",
    5: "Technical / procurement stakeholder",
    6: "Business leadership",
    7: "Other stakeholder",
}


def sales_priority(person: dict) -> tuple[int, str]:
    """(tier, label) for one person (dict with seniority / functions /
    technology_areas / role_id)."""
    groups = set(people_groups(person))
    seniority = person.get("seniority")
    technical = bool(groups & _TECHNOLOGY_GROUPS)
    if GROUP_TECH_LEADERSHIP in groups:
        tier = 1 if seniority == "c_level" else 2
    elif technical and seniority:
        tier = 3
    elif GROUP_PROCUREMENT in groups and seniority in _LEADERSHIP_SENIORITY:
        tier = 4
    elif technical or GROUP_PROCUREMENT in groups:
        tier = 5
    elif seniority in _LEADERSHIP_SENIORITY:
        tier = 6
    else:
        tier = 7
    return tier, SALES_PRIORITY_TIERS[tier]


def sort_people_for_sales(people: list[dict]) -> list[dict]:
    """PEOPLE page order: sales-priority tier, then the evidence-based
    relevance score, then how often discovery surfaced the person."""
    return sorted(people, key=lambda p: (sales_priority(p)[0], -(p.get("relevance_score") or 0),
                                         -(p.get("discovery_hit_count") or 0), str(p.get("name") or "").lower()))


def score_person(
    *,
    seniority: str | None,
    functions: list[str],
    technology_areas: list[str],
    organization_association: str,
    source_url_count: int,
    discovery_dimensions: list[str],
    role_id: str | None = None,
) -> tuple[float, dict]:
    taxonomy = get_taxonomy()
    function_ids_by_dimension = _dimension_function_overlap_map(taxonomy)

    factors: dict[str, float] = {}

    seniority_points = {"c_level": 25, "director": 20, "head": 18, "manager": 10}.get(seniority or "", 0)
    factors["seniority"] = seniority_points

    function_points = min(15, 5 * len(functions))
    factors["function_relevance"] = function_points

    tech_points = min(20, 5 * len(technology_areas))
    factors["technology_relevance"] = tech_points

    org_points = {"confirmed": 15, "needs_verification": 8, "weak": 8, "unconfirmed": 0}[organization_association]
    factors["organization_association"] = org_points

    multi_source_points = 5 if source_url_count >= 2 else 0
    factors["multiple_evidence_sources"] = multi_source_points

    corroboration_points = 0
    matched_ids = set(functions) | set(technology_areas)
    for dim in discovery_dimensions:
        if function_ids_by_dimension.get(dim, set()) & matched_ids:
            corroboration_points = 2
            break
    factors["discovery_dimension_corroboration"] = corroboration_points

    # role_id is accepted for compatibility; the sales-priority tier
    # (sales_priority) is deliberately NOT part of relevance_score.

    total = round(min(100.0, sum(factors.values())), 1)
    return total, factors


# A small, generic mapping from LinkedIn discovery dimension -> which
# taxonomy function/technology ids it would plausibly corroborate, used
# ONLY as a minor scoring bonus (never as classification evidence itself
# -- see module docstring). People v2 query dimensions are taxonomy-driven
# ("function:<id>", "technology:<id>", "role:<id>", "technology_leadership");
# the two legacy dimension names are kept for older stored runs.
_DIMENSION_CORROBORATION_HINTS = {
    "technology_digital_infrastructure": {"it", "infrastructure", "cloud", "digital_transformation", "network", "data_center"},
    "procurement_commercial": {"procurement"},
}


def _dimension_function_overlap_map(taxonomy: dict) -> dict[str, set[str]]:
    hints = {k: set(v) for k, v in _DIMENSION_CORROBORATION_HINTS.items()}
    ids_by_group: dict[str, set[str]] = {}
    for category, prefix in (("functions", "function"), ("technology_areas", "technology")):
        for entry in taxonomy.get(category, []):
            hints[f"{prefix}:{entry['id']}"] = {entry["id"]}
            ids_by_group.setdefault(entry.get("group") or "", set()).add(entry["id"])
    hints["technology_leadership"] = set().union(
        *(ids_by_group.get(g, set()) for g in ("infrastructure_cloud", "cybersecurity", "digital_data")))
    for role in taxonomy.get("roles", []):
        hints[f"role:{role['id']}"] = set(ids_by_group.get(role.get("group") or "", set()))
    for family in (taxonomy.get("people_discovery") or {}).get("query_families", []):
        group = family.get("group")
        hints[f"family:{family['id']}"] = (set(hints["technology_leadership"]) if group == "technology"
                                           else set(ids_by_group.get(group or "", set())))
    return hints


# ---------------------------------------------------------------------------
# Deduplication -- by stable LinkedIn profile slug (the /in/<slug> path
# segment), independent of subdomain (om.linkedin.com vs www.linkedin.com
# vs au.linkedin.com for the same person) or tracking query params, which
# plain URL-string dedup (already applied upstream in research.sources.
# dedupe_sources) does not catch.
# ---------------------------------------------------------------------------


def _profile_slug(url: str) -> str | None:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "in":
        return parts[1].lower()
    return None


def deduplicate_profile_sources(sources: list[Source]) -> list[list[Source]]:
    """Groups Source objects that represent the SAME LinkedIn profile
    (same /in/<slug>) together. Sources with no recognizable slug are each
    kept as their own singleton group rather than silently dropped.
    """
    groups: dict[str, list[Source]] = {}
    singles: list[list[Source]] = []
    order: list[str] = []
    for s in sources:
        slug = _profile_slug(s.url)
        if slug is None:
            singles.append([s])
            continue
        if slug not in groups:
            groups[slug] = []
            order.append(slug)
        groups[slug].append(s)
    return [groups[slug] for slug in order] + singles


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

# No stakeholder cap: every candidate that passes deduplication and the
# organization-association check is kept (ranked). Volume is governed only
# by the search budget in research.linkedin, never by truncating people.


@dataclass
class PeopleResult:
    """Outcome of People assessment. Only `confirmed` are stakeholders;
    `unverified` are clearly-labelled candidates that must never be counted
    as confirmed stakeholders; `rejected` is diagnostics only."""

    confirmed: list[Person] = field(default_factory=list)
    unverified: list[Person] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


def assess_people(
    profile_candidates: list[Source],
    *,
    organization_name: str,
    organization_country: str | None = None,
    discovered_at: str | None = None,
) -> PeopleResult:
    """Assess every deduplicated LinkedIn profile candidate.

    * confirmed_current -> `confirmed` (the normal People list)
    * needs_verification -> `unverified`, each with explicit reasons
    * rejected -> `rejected` (name + reasons, diagnostics only)

    Employment is judged ONLY from each result's title and primary snippet
    (see commercial.people_association). Brave extra_snippets, other results'
    snippets for the same URL and related-profile text are kept on the
    person as `context_snippets` but never establish employment nor supply
    the person's title, seniority or functions. Nothing is capped or padded.
    """
    result = PeopleResult()
    for group in deduplicate_profile_sources(profile_candidates):
        primary = max(group, key=lambda s: s.source_quality)
        candidate_name = _name_from_title(primary.title)

        # Zones A + B: every result's own title/snippet for this profile URL.
        zone_ab = [(s.title or "", s.snippet or "") for s in sorted(group, key=lambda s: s is not primary)]
        # Zone C: context only.
        context: list[str] = []
        for s in group:
            for snip in [*(s.extra.get("supplemental_snippets") or []), *(s.extra.get("extra_snippets") or [])]:
                if snip and snip not in context and snip != s.snippet:
                    context.append(snip)

        if not any(t or sn for t, sn in zone_ab):
            continue
        assessment = assess_results(
            zone_ab, organization_name, organization_country=organization_country,
            candidate_name=candidate_name or None,
        )
        if assessment.status not in (CONFIRMED_CURRENT, NEEDS_VERIFICATION):
            result.rejected.append({"name": candidate_name, "url": primary.url, "reasons": assessment.reasons})
            continue
        association = assessment.level

        providers: set[str] = set()
        hit_keys: set[tuple] = set()
        for s in group:
            for hit in s.extra.get("discovery_hits") or []:
                hit_keys.add((hit.get("provider"), hit.get("query"), hit.get("page")))
                if hit.get("provider"):
                    providers.add(hit["provider"])
            providers |= set(s.extra.get("discovery_providers") or [])
            if s.discovery_provider and s.discovery_provider not in ("resolved", "direct_crawl"):
                providers.add(s.discovery_provider)

        # Title / seniority / functions come ONLY from the evidence-zone text
        # (headline + current snippet clauses), never from context text and
        # never from historical or other-person clauses.
        classification = classify_person_text(assessment.basis_text)

        influence_type = derive_influence_type(
            seniority=classification["seniority"],
            functions=classification["functions"],
            technology_areas=classification["technology_areas"],
            organization_association=association,
        )

        source_urls = sorted({s.url for s in group})
        source_types = sorted({s.source_type for s in group})
        discovery_dimensions = sorted({s.extra.get("discovery_dimension") for s in group if s.extra.get("discovery_dimension")})

        score, factors = score_person(
            seniority=classification["seniority"],
            functions=classification["functions"],
            technology_areas=classification["technology_areas"],
            organization_association=association,
            source_url_count=len(source_urls),
            discovery_dimensions=discovery_dimensions,
            role_id=classification["role_id"],
        )

        chips = generate_chips(
            role_label=classification["role_label"],
            functions=classification["functions"],
            technology_areas=classification["technology_areas"],
            influence_type=influence_type,
            organization_association=association,
            source_types=source_types,
        )

        confidence = 40.0
        if association == "confirmed":
            confidence += 30
        elif association == "needs_verification":
            confidence += 15
        if classification["role_id"]:
            confidence += 20
        if len(source_urls) >= 2:
            confidence += 10
        confidence = round(min(100.0, confidence), 1)

        # The result title is kept as-is when it carries a role. Otherwise fall
        # back to the person's own explicit current-role / headline field.
        current_title = primary.title
        if association == "confirmed" and not title_has_usable_role(primary.title, candidate_name, organization_name):
            fb = fallback_current_title([sn for _t, sn in zone_ab], candidate_name, organization_name)
            if fb:
                current_title = f"{candidate_name} - {fb[0]}" if candidate_name else fb[0]

        slug = _profile_slug(primary.url)
        person = Person(
            person_id=slug or primary.url,
            name=candidate_name,
            current_title=current_title,
            organization_name=organization_name,
            organization_association=association,
            linkedin_url=primary.url,
            source_urls=source_urls,
            source_types=source_types,
            location=assessment.location,
            seniority=classification["seniority"],
            functions=classification["functions"],
            technology_areas=classification["technology_areas"],
            influence_type=influence_type,
            role_id=classification["role_id"],
            role_label=classification["role_label"],
            relevance_score=score,
            confidence=confidence,
            evidence=[primary.snippet] if primary.snippet else [],
            chips=chips,
            score_factors=factors,
            discovery_dimensions=discovery_dimensions,
            discovery_providers=sorted(providers),
            discovery_hit_count=len(hit_keys) or len(group),
            # Everything retained for context/provenance (all zones); only
            # `classification_matched_terms` + the chips reflect what was
            # actually allowed to classify the person.
            classification_text=" ".join(
                [primary.title, *[sn for _, sn in zone_ab if sn], *context]).strip(),
            classification_matched_terms=classification["matched_terms"],
            organization_association_evidence=assessment.evidence,
            verification_reasons=list(assessment.reasons) if association != "confirmed" else [],
            context_snippets=context,
            discovered_at=discovered_at,
        )
        (result.confirmed if association == "confirmed" else result.unverified).append(person)

    for bucket in (result.confirmed, result.unverified):
        bucket.sort(key=lambda p: (-p.relevance_score, -p.confidence, p.name.lower()))
    return result


def build_people(
    profile_candidates: list[Source],
    *,
    organization_name: str,
    organization_country: str | None = None,
    discovered_at: str | None = None,
) -> list[Person]:
    """The normal People list: only candidates with confirmed CURRENT
    employment at the target organization (see assess_people). Never padded
    and never truncated. `needs_verification` candidates are NOT returned
    here; call assess_people to obtain them separately."""
    return assess_people(
        profile_candidates, organization_name=organization_name,
        organization_country=organization_country, discovered_at=discovered_at,
    ).confirmed


def _name_from_title(title: str) -> str:
    """LinkedIn search-result titles are conventionally "Name - Headline -
    Company | LinkedIn" or "Name | LinkedIn". Take the text before the
    first separator as the name; fall back to the whole title if no
    separator is found rather than fabricating a name.
    """
    if not title:
        return ""
    for sep in (" - ", " | ", " — "):
        if sep in title:
            return title.split(sep, 1)[0].strip()
    return title.strip()


def summarize_by_function(people: list[Person]) -> dict[str, int]:
    """Derived counts for the People-tab summary header (guardrail #12) --
    computed from the actual classified people, never hardcoded.
    """
    counts: dict[str, int] = {}
    for p in people:
        for fid in p.functions:
            counts[fid] = counts.get(fid, 0) + 1
    return counts

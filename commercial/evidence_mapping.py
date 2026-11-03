"""Evidence-first capability mapping engine.

Replaces the naive "keyword found anywhere -> capability opportunity"
pattern with an explicit evidence-level classification, per the chain:

    Customer Signal -> Business Impact -> Technology Requirement
        -> Potential Otech Capability

Every CapabilityMapping this module produces carries WHY it was classified
the way it was (evidence_level + matched_terms + source), never a single
opaque score. This module is 100% deterministic: regex/keyword matching and
transparent arithmetic only. No LLM, no organization/sector-specific
branching -- every function takes a Source (already sector-agnostic) and
the capability catalogue (already sector-agnostic data) as its only inputs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace as _dataclass_replace
from urllib.parse import urlparse

from commercial.capabilities import get_catalog
from commercial.incumbents import _KNOWN_VENDOR_TERMS, detect_incumbent_evidence
from commercial.models import CapabilityMapping, EvidenceCategory, EvidenceLevel, Signal, epistemic_status_for
from commercial.signals import extract_signals
from commercial.text_heuristics import (
    _is_collective_scope_span,
    executive_bio_marker_count,
    is_low_trust_span,
    looks_like_customer_product_page,
    looks_like_customer_requirement_span,
    looks_like_vendor_case_study_span,
    sentence_spans,
    span_containing,
)
from research.sources import SOURCE_TYPE_LINKEDIN_PROFILE, SOURCE_TYPE_OFFICIAL, Source

_HIGH_TRUST_SOURCE_TYPES = {"official", "technology", "procurement"}
_MEDIUM_TRUST_SOURCE_TYPES = {"linkedin_company", "business", "news"}

# Phase 1C-H3.1: a personal LinkedIn ("/in/...") URL, detected directly
# from the URL itself -- independent of `source.source_type`.
#
# WHY THIS EXISTS (the H3.1 regression, explained): the H2 LinkedIn cap
# ("personal profiles are never more than weak_relevance") was enforced by
# branching on `source.source_type == SOURCE_TYPE_LINKEDIN_PROFILE`.
# `source_type` is set once, upstream, at ACQUISITION time, based on which
# discovery family/provider found the URL (research/linkedin.py tags
# results from the dedicated LinkedIn-people family as
# SOURCE_TYPE_LINKEDIN_PROFILE) -- it is not re-derived from the URL
# itself anywhere downstream. A personal LinkedIn URL that instead surfaces
# as an organic hit from a DIFFERENT discovery family (e.g. a generic
# "business"/"news"/"technology" query template that happens to return a
# linkedin.com/in/... result, or a localized host like om.linkedin.com the
# upstream classifier didn't anticipate) is tagged with THAT family's
# source_type instead -- "business", "news", "technology", etc. -- and the
# evidence-mapping engine had no independent way to know it was actually
# looking at a personal profile. H3's organization-association gate then
# had no reason to block it (the profile's own title/content legitimately
# named the organization right next to the matched terms), so a
# misclassified personal profile could clear the same bar as a genuine
# official/technology source.
#
# The fix is defense in depth, not a single conditional: (1) profile
# detection below no longer trusts source_type alone -- it also inspects
# the URL directly, so a misclassified source is still recognized; and
# (2) the cap is additionally enforced as a hard clamp over this
# function's OUTPUT, applied after every promotion rule has run, so no
# future rule (association, signals, term-count, trust tier) can bypass
# it by construction -- the invariant "personal LinkedIn profile evidence
# never exceeds weak_relevance" no longer depends on any single upstream
# flag being correct.
_LINKEDIN_PERSONAL_PROFILE_PATH_RE = re.compile(r"^/in/", re.I)


def _is_linkedin_personal_profile_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = parsed.netloc.lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return False
    # Explicitly distinct from a company page (item 3): only a "/in/..."
    # path counts as a personal profile. "/company/..." (and anything
    # else under the linkedin.com domain family) is untouched by this cap.
    return bool(_LINKEDIN_PERSONAL_PROFILE_PATH_RE.match(parsed.path or ""))

# Signal types that plausibly imply a technology requirement even without a
# direct capability keyword -- deliberately excludes signal types (e.g.
# "financial_change", "leadership_change") that don't obviously imply new
# technology need on their own. Used for STRONG_INFERENCE (generic-trigger)
# promotion -- a generic business event like "expansion" or "partnership"
# is a real customer signal and a defensible basis for INFERRING a
# technology requirement, but per Phase 1C-H2 it must never, by itself,
# stand in for direct technology evidence (see _TECH_EVENT_SIGNAL_TYPES).
_QUALIFYING_SIGNAL_TYPES = {
    "expansion", "project", "investment", "partnership", "contract_award",
    "technology_initiative", "capacity_change", "procurement_activity",
}

# Phase 1C-H2: a strictly narrower subset of signal types that describe an
# EXPLICIT, CONCRETE technology event at the organization itself --
# a technology initiative/upgrade/renewal, a procurement action, or a
# capacity change -- as opposed to a general business event (expansion,
# partnership, investment, contract award, leadership change) that merely
# CORRELATES with technology need without describing one directly.
#
# This is the fix for the reported false-positive pattern: a partnership/
# MOU/cooperation article is a real customer/account signal (retained via
# commercial.signals independently of capability mapping), but it must not
# be treated as evidence that the organization itself has an explicit
# technology requirement just because a generic technology word also
# appears in the same article and the source happens to be high-trust.
# Deliberately excludes "expansion", "project", "investment", "partnership"
# and "contract_award" -- an MOU, alliance, or business expansion does not
# by itself describe a technology deployment, upgrade, renewal or
# procurement action, however business-significant it is.
_TECH_EVENT_SIGNAL_TYPES = {
    "technology_initiative", "procurement_activity", "capacity_change",
}

# Phase 2 hardening (item 2): the signal types allowed to promote a GENERIC
# TRIGGER (as opposed to a specific capability keyword) to STRONG_INFERENCE.
# Deliberately narrower than _QUALIFYING_SIGNAL_TYPES above: "partnership",
# "investment", and "contract_award" are real, valuable customer signals
# (retained via commercial.signals regardless), but per the approved
# hardening direction they must NEVER, by themselves, promote a generic
# trigger like "new facility" or "acquisition" to a technology-requirement
# hypothesis -- an MOU, an investment round, or a contract award does not by
# itself describe a change in the organization's technology footprint the
# way an expansion, a concrete project, a technology initiative, a capacity
# change, or a procurement action does. This set is used ONLY by Rule 4
# (generic-trigger inference) below; it does not affect Rule 2/3's use of
# the broader _QUALIFYING_SIGNAL_TYPES for specific-keyword promotion --
# that path is about a named, capability-specific technology term, not a
# generic word shared across the whole catalogue, so the fan-out risk this
# narrows does not apply there the same way.
_INFERENCE_QUALIFYING_SIGNAL_TYPES = {
    "expansion", "project", "technology_initiative", "capacity_change", "procurement_activity",
}

# False-positive-class fix (generic-strategy-article finding): the subset of
# the qualifying signal types above that CORRELATES with technology need but
# does NOT, by itself, describe a concrete technology event -- i.e. exactly
# _INFERENCE_QUALIFYING_SIGNAL_TYPES minus _TECH_EVENT_SIGNAL_TYPES. Computed
# from the two sets already defined above rather than hardcoded, so it always
# tracks whichever signal types those two sets actually contain. A signal of
# one of these types is not by itself sufficient to promote a generic trigger
# (Rule 4) or a lone specific keyword (Rule 2/3's single-hit fallback) to
# STRONG_INFERENCE -- see `_correlating_signal_has_tech_context` below, which
# additionally requires local technology-context corroboration for exactly
# this subset. tech_event_locally_grounded (both rules) is entirely
# unaffected: technology_initiative/procurement_activity/capacity_change
# remain independently qualifying, exactly as before.
_CORRELATING_ONLY_SIGNAL_TYPES = _INFERENCE_QUALIFYING_SIGNAL_TYPES - _TECH_EVENT_SIGNAL_TYPES

# Phase 2 hardening (item 1): how many sentences away a qualifying signal's
# OWN match may be from the matched GENERIC TRIGGER term and still count as
# "locally associated" with it. Mirrors _SENTENCE_ASSOCIATION_RADIUS's
# reasoning -- normal narrative structure ("Org X is opening a new facility.
# This expansion will require additional network capacity.") puts the signal
# language one sentence away from the trigger word itself; a signal several
# sentences away, or in an unconnected part of the article, is not "this
# same event," and must not be allowed to ground an inference for a
# completely different generic trigger mentioned elsewhere in the source.
_SIGNAL_TRIGGER_ASSOCIATION_RADIUS = 1

# Phase 1C-H3: sentence-level "local context" radius. A matched technology
# term is accepted as being ABOUT the target organization only when a
# mention of that organization falls in the SAME sentence, or a sentence
# immediately adjacent to it (radius 1) -- not merely somewhere else in the
# same paragraph or article. This deliberately mirrors ordinary narrative
# structure ("Org X announced Y. This upgrade will do Z." -- Y and Z are
# both clearly about Org X) while refusing a match separated by two or more
# sentences from any organization mention (a government/MOU/roundup
# article naming the organization in one place and describing an unrelated
# national programme, partner country, or other company two-plus sentences
# later or earlier).
_SENTENCE_ASSOCIATION_RADIUS = 1

# Organization-name tokens shorter than this are treated as too generic to
# count as association evidence on their own (avoids e.g. a 2-letter
# fragment matching incidentally).
_MIN_ORG_TOKEN_LEN = 3

# Organization-identity hardening: generic corporate-form / organizational
# words that appear in many unrelated organizations' names and in ordinary
# prose ("the company's management team"). Like a country token, they do not
# independently identify WHICH organization a sentence is about, so they are
# excluded from token-fallback association. Closed, sector-agnostic list --
# no organization, sector, or country name.
_GENERIC_ORG_NAME_TOKENS = frozenset({
    "company", "group", "holding", "holdings", "corporation", "limited",
    "llc", "saoc", "saog", "management", "services", "international",
})

def _term_pattern(term: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(term) + r"\b", re.I)


def _match_terms(text: str, terms: list[str]) -> list[str]:
    if not text:
        return []
    found = []
    for term in terms:
        if _term_pattern(term).search(text):
            found.append(term)
    return found


def _first_match_context(text: str, terms: list[str], pad: int = 90) -> str:
    for term in terms:
        m = _term_pattern(term).search(text)
        if m:
            return text[max(0, m.start() - pad): min(len(text), m.end() + pad)].strip()
    return ""


def _org_core_tokens(organization_name: str, country: str | None = None) -> list[str]:
    """The organization-name tokens used for association matching.

    Evidence-relevance hardening (recommendation #4, aggregator/regional/
    government pages): a token that is also a token of the resolved
    COUNTRY name is excluded -- "Oman" appearing near "management" on a
    country-level government/ministry page is a country-level reference,
    not evidence that the page is actually about "Oman Airports
    Management Company." `country` is always the dynamically resolved
    country for whatever account this run is for (never hardcoded), and
    is opt-in: omitting it reproduces the prior (unfiltered) token set, so
    callers that don't thread it through are unaffected.

    Defensive fallback: if excluding country tokens would leave NO tokens
    at all (an org name that is ~entirely the country name), the
    unfiltered token list is used instead -- this can only make the
    association check as permissive as before, never MORE permissive, so
    it cannot introduce a new false positive.
    """
    tokens = re.findall(r"[A-Za-z0-9]+", organization_name.lower())
    tokens = [t for t in tokens if len(t) >= _MIN_ORG_TOKEN_LEN]
    if country:
        country_tokens = {t for t in re.findall(r"[A-Za-z0-9]+", country.lower()) if len(t) >= _MIN_ORG_TOKEN_LEN}
        stripped = [t for t in tokens if t not in country_tokens]
        if stripped:
            tokens = stripped
    # Organization-identity hardening: same principle as country stripping
    # above, with the same fallback -- if removing generic corporate words
    # would leave nothing, keep the previous list (never more permissive).
    distinctive = [t for t in tokens if t not in _GENERIC_ORG_NAME_TOKENS]
    if distinctive:
        tokens = distinctive
    return tokens


def _org_short_name(organization_name: str, country: str | None = None) -> str | None:
    """The organization name with trailing generic corporate words removed
    (e.g. "Oman Airports Management Company" -> "Oman Airports"), usable as
    an additional LITERAL, whole-phrase identity match.

    Returns None unless the short form (a) differs from the full name,
    (b) still has at least two words -- never a one-word match -- and
    (c) still contains at least one distinctive token that is neither a
    generic corporate word nor a country token.
    """
    words = organization_name.split()
    while words and words[-1].lower().strip(".,") in _GENERIC_ORG_NAME_TOKENS:
        words.pop()
    if len(words) < 2:
        return None
    short = " ".join(words)
    if short.lower() == organization_name.strip().lower():
        return None
    country_tokens = (
        {t for t in re.findall(r"[a-z0-9]+", country.lower())} if country else set()
    )
    distinctive = [
        t for t in re.findall(r"[a-z0-9]+", short.lower())
        if len(t) >= _MIN_ORG_TOKEN_LEN and t not in _GENERIC_ORG_NAME_TOKENS and t not in country_tokens
    ]
    if not distinctive:
        return None
    return short


def _required_org_token_hits(token_count: int) -> int:
    """Evidence-relevance hardening (recommendation #4): how many DISTINCT
    organization-name tokens must co-occur in a segment for it to count as
    "mentions the organization" via the token-fallback path (the literal
    full-name substring check above it is unaffected and remains the
    strongest signal). Previously a flat "1 if a single token, else 2"
    regardless of how many tokens the name actually has -- weak for
    longer names, where any 2 of e.g. 6 tokens (which might include only
    generic/incidental words) could accidentally co-occur on an unrelated
    page. Now scales with the name: at least half the tokens (rounded up),
    with a floor of 2 once there is more than one token.
    """
    if token_count <= 1:
        return 1
    return max(2, -(-token_count // 2))  # ceil(token_count / 2), minimum 2


# Evidence-intelligence fix (exemplar mentions): a generic industry/vendor
# sentence that names the account only as an EXAMPLE of a wider class --
# "Airports such as Oman Airports are deploying a security operations
# center", "operators like X", "including X", "X and other airports" -- is a
# claim about the class, not about the account. Such a mention must not make
# the sentence count as being ABOUT the organization. Generic English
# enumeration frames only; no organization, sector or vendor names. The
# vendor-specific "customers such as X" / "trusted by X" frames remain in
# text_heuristics.looks_like_vendor_case_study_span.
_EXEMPLAR_PREFIX_RE = re.compile(
    r"(?:\bsuch\s+as|\blike|\bincluding|\bincludes?|\be\.g\.,?|\bfor\s+example,?|\bfor\s+instance,?|\bnotably)"
    r"\s+(?:the\s+)?$",
    re.I,
)
_EXEMPLAR_SUFFIX_RE = re.compile(
    r"^\s*,?\s*(?:(?:and|or)\s+(?:other|many|several|most|some|similar)\b|among\s+others\b|and\s+the\s+like\b)",
    re.I,
)
_EXEMPLAR_PREFIX_WINDOW = 24


def _literal_org_occurrences(seg_lower: str, organization_name: str, short_name: str | None) -> list[tuple[int, int]]:
    """Start/end offsets of every literal full-name or short-name mention in
    `seg_lower`. A short-name hit lying inside a full-name hit is the same
    mention and is not counted twice."""
    full = [m.span() for m in re.finditer(re.escape(organization_name.lower()), seg_lower)]
    out = list(full)
    if short_name:
        for m in re.finditer(r"\b" + re.escape(short_name.lower()) + r"\b", seg_lower):
            if not any(s <= m.start() < e for s, e in full):
                out.append(m.span())
    return out


def _is_exemplar_occurrence(seg_lower: str, start: int, end: int) -> bool:
    before = seg_lower[max(0, start - _EXEMPLAR_PREFIX_WINDOW):start]
    after = seg_lower[end:end + 40]
    return bool(_EXEMPLAR_PREFIX_RE.search(before) or _EXEMPLAR_SUFFIX_RE.search(after))


# Signal Intelligence refinement -- deterministic account aliases.
#
# Besides the full legal name and the multi-word short name, an account is
# commonly referred to by the initials of its name ("Oman Airports
# Management Company" -> "OAMC"). The acronym is DERIVED from the name
# itself (never a lookup table, never fuzzy): the first letter of every word,
# skipping connective words. It is matched only as an exact, upper-case,
# whole word, and only when it has at least four letters -- two- and
# three-letter initialisms collide with too many unrelated organizations to
# identify an account on their own. Exemplar framing ("such as OAMC") is
# rejected exactly as for the full name.
_ACRONYM_SKIP_WORDS = frozenset({"and", "of", "the", "for", "&", "de", "du", "la", "le", "al"})
_MIN_ACRONYM_LETTERS = 4


def _org_acronym(organization_name: str) -> str | None:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", organization_name or "") if w.lower() not in _ACRONYM_SKIP_WORDS]
    acronym = "".join(w[0] for w in words).upper()
    return acronym if len(acronym) >= _MIN_ACRONYM_LETTERS else None


def organization_aliases(organization_name: str, country: str | None = None) -> list[str]:
    """Every deterministic name form that identifies the account: the full
    name, the multi-word short name, and the derived acronym."""
    aliases = [organization_name.strip()]
    short_name = _org_short_name(organization_name, country=country)
    if short_name:
        aliases.append(short_name)
    acronym = _org_acronym(organization_name)
    if acronym:
        aliases.append(acronym)
    return aliases


def _mentions_acronym(segment: str, organization_name: str) -> bool:
    acronym = _org_acronym(organization_name)
    if not acronym:
        return False
    hits = [m.span() for m in re.finditer(r"(?<![A-Za-z0-9])" + re.escape(acronym) + r"(?![A-Za-z0-9])", segment)]
    seg_lower = segment.lower()
    return any(not _is_exemplar_occurrence(seg_lower, s, e) for s, e in hits)


# UNDERSTAND v2 -- deterministic hashtag / compound aliases.
#
# Social posts write a multi-word name as one CamelCase token, usually as a
# hashtag: "Oman Airports" -> "#OmanAirports" / "OmanAirports". The compound
# is DERIVED from the account's own multi-word aliases (full name and short
# name) by removing the spaces -- never a lookup table and never fuzzy. It
# is matched case-sensitively, exactly as written in the name, as a whole
# token: not inside a longer word, not as part of a domain name
# ("omanairports.co.om" does not match), and not as an exemplar.
_MIN_COMPOUND_CHARS = 8


def compound_aliases(organization_name: str, country: str | None = None) -> list[str]:
    out: list[str] = []
    for alias in (organization_name.strip(), _org_short_name(organization_name, country=country)):
        if not alias:
            continue
        words = re.findall(r"[A-Za-z0-9]+", alias)
        compound = "".join(words)
        if len(words) >= 2 and len(compound) >= _MIN_COMPOUND_CHARS and compound not in out:
            out.append(compound)
    return out


def _mentions_compound_alias(segment: str, organization_name: str, country: str | None = None) -> bool:
    seg_lower = segment.lower()
    for compound in compound_aliases(organization_name, country=country):
        pattern = r"(?<![A-Za-z0-9./@_-])#?" + re.escape(compound) + r"(?![A-Za-z0-9_]|\.[A-Za-z])"
        for m in re.finditer(pattern, segment):
            if not _is_exemplar_occurrence(seg_lower, m.start(), m.end()):
                return True
    return False


def _mentions_organization(
    segment: str, organization_name: str, tokens: list[str], *, country: str | None = None
) -> bool:
    if _mentions_acronym(segment, organization_name):
        return True
    if _mentions_compound_alias(segment, organization_name, country=country):
        return True
    seg_lower = segment.lower()
    # Organization-identity hardening: the multi-word short form of the name
    # (trailing generic corporate words removed, see `_org_short_name`) is
    # accepted as a literal whole-phrase match -- "Oman Airports" for
    # "Oman Airports Management Company". Never a single word.
    short_name = _org_short_name(organization_name, country=country)
    literal = _literal_org_occurrences(seg_lower, organization_name, short_name)
    if literal:
        # Evidence-intelligence fix: the account is named here, but if EVERY
        # mention is an exemplar ("such as X", "X and other ...") the
        # sentence is about the wider class, not the account -- and the
        # weaker token fallback below must not re-admit it.
        return any(not _is_exemplar_occurrence(seg_lower, s, e) for s, e in literal)
    if len(tokens) <= 1:
        # Fix B (confirmed live false-positive class, Oman Air investigation):
        # a single remaining core token -- typically because country-
        # stripping in _org_core_tokens left only one word -- is too weak to
        # trust alone: it is frequently a short, common word purely by
        # coincidence (e.g. "air" from "Oman Air", "health" from "Star
        # Health"). Word-boundary matching alone is not sufficient here --
        # "air traffic" is a genuine whole-word match of "air" but is not
        # evidence of "Oman Air" -- so when only one core token remains, only
        # the literal full organization name (checked above) can establish
        # association. Generic -- no organization name is referenced here,
        # and this applies identically regardless of which word the single
        # token happens to be. A naturally single-word organization name is
        # unaffected: its one token IS the full name, so the check above
        # already covers it.
        return False
    required_token_hits = _required_org_token_hits(len(tokens))
    # Fix B: word-boundary matching, not substring containment -- a
    # multi-token name's individual tokens could otherwise coincidentally
    # match inside an unrelated longer word (the same class of bug the
    # single-token early return above addresses, at the multi-token level).
    return sum(
        1 for t in tokens if re.search(r"\b" + re.escape(t) + r"\b", seg_lower)
    ) >= required_token_hits


def _span_disqualifies_evidence(segment: str) -> bool:
    """Evidence-relevance hardening, follow-up pass (Area 3 -- organization
    vs. customer requirements), extended by the false-positive-class fix
    (Oman Airports live-validation finding, 2026-09) with a third,
    equally generic reason: a local (roughly one-sentence) span that must
    NOT be treated as genuine first-party organizational narrative for the
    purpose of grounding a matched term or signal. Three independent
    reasons disqualify a span, and this is simply their union:

      - it reads as non-narrative chrome (`is_low_trust_span`, pre-existing:
        a category-label list/infographic caption, or page navigation/
        aggregator boilerplate); or
      - it uses generic customer-facing language describing what a THIRD
        PARTY -- the organization's own customer -- needs, has, or requires
        at their own site (`looks_like_customer_requirement_span`, new in
        the follow-up pass) -- e.g. "close to gas, water and fiber"
        describing PROSPECTIVE CUSTOMERS' data-centre siting criteria is
        not evidence of the PUBLISHING organization's own connectivity
        requirement; or
      - it describes a collective/aggregate scope -- an entire government,
        sector, or industry -- rather than the researched organization
        specifically (`_is_collective_scope_span`, new in this pass) --
        e.g. "Oman Airports is among the government entities progressing
        digital transformation..." names Oman Airports only as one member
        of a government-wide collective; the claim is about the
        collective, not an organization-specific event.

    All three checks are pure source-shape/language heuristics -- no
    organization, sector, company, or geography name is ever involved --
    and applying the union everywhere a term/signal match's local sentence
    context is checked (both in `_is_organization_associated` and in
    `_signal_locally_associated_with_trigger`, plus the locality helpers
    below) means a collective-scope span is disqualified from grounding
    organization-association in exactly the same structural way a
    buzzword list or a customer-requirement span already is, without
    weaving a third, differently-shaped conditional through every call
    site. Concretely: when the ONLY sentence(s) mentioning the researched
    organization read as collective-scope, `_is_organization_associated`
    finds no usable org-association sentence at all (its `org_sentence_idxs`
    becomes empty) and Rule 4's `generic_org_associated` is False -- so a
    stale, government-wide "digital transformation" article can no longer
    independently promote unrelated capability vocabulary elsewhere in the
    same article to STRONG_INFERENCE on the strength of that one collective
    mention, while an organization-specific claim made in a DIFFERENT,
    non-collective-scope sentence of the same article is untouched.
    """
    return (
        is_low_trust_span(segment)
        or looks_like_customer_requirement_span(segment)
        or _is_collective_scope_span(segment)
        or looks_like_vendor_case_study_span(segment)
    )


def _is_organization_associated(
    text: str, terms: list[str], organization_name: str | None, *, radius: int = _SENTENCE_ASSOCIATION_RADIUS,
    country: str | None = None,
) -> bool:
    """Item 1/3/6 (Phase 1C-H3): for a direct_evidence candidate to hold,
    at least one occurrence of the matched technology term(s) must fall in
    the same sentence as -- or a sentence immediately adjacent to -- a
    mention of the TARGET organization (see _SENTENCE_ASSOCIATION_RADIUS).
    Merely having the organization mentioned somewhere else in a longer
    article is NOT sufficient (item 6): a government/MOU/partnership/
    roundup piece may legitimately name the target organization in one
    sentence and describe an unrelated national programme, a partner
    country, or another company entirely two-plus sentences away.

    Deliberately generic: this only ever uses the organization_name that
    was actually resolved for THIS research run (never a hardcoded name),
    and it requires the organization's own name/tokens to co-occur, in
    local narrative context, with the technology evidence itself (the
    matched term's sentence) -- not just presence anywhere in the source.

    When organization_name is not supplied, this check is a deliberate
    no-op (returns True) -- it is opt-in pipeline context threaded in by
    map_all_sources/build_report from the resolved organization, not
    assumed. Unit tests exercising evidence_mapping.py directly (without
    organization context) are unaffected by this gate, preserving all
    Phase 1C-H2 behavior for those callers.
    """
    if not organization_name:
        return True
    tokens = _org_core_tokens(organization_name, country=country)
    if not tokens:
        return True

    spans = sentence_spans(text)
    if not spans:
        return False

    # Evidence-relevance hardening (recommendations #3 + #4): a span that
    # reads as a category-label list/infographic caption, or as page
    # navigation/aggregator chrome, is not treated as genuine narrative --
    # neither an organization mention nor a matched term found ONLY inside
    # such a span can ground association. A "Read More ... Org X ... Read
    # More" navigation concatenation, or a buzzword list that happens to
    # contain both the organization's name and a technology term, is
    # exactly the false-positive shape this excludes.
    org_sentence_idxs = {
        i for i, (s, e) in enumerate(spans)
        if not _span_disqualifies_evidence(text[s:e])
        and _mentions_organization(text[s:e], organization_name, tokens, country=country)
    }
    if not org_sentence_idxs:
        return False

    for term in terms:
        for match in _term_pattern(term).finditer(text):
            for i, (s, e) in enumerate(spans):
                if s <= match.start() < e:
                    if _span_disqualifies_evidence(text[s:e]):
                        break
                    if any(abs(j - i) <= radius for j in org_sentence_idxs):
                        return True
                    break
    return False


def _signal_locally_associated_with_trigger(
    text: str, trigger_terms: list[str], signal: Signal | None, *, radius: int = _SIGNAL_TRIGGER_ASSOCIATION_RADIUS
) -> bool:
    """Phase 2 hardening (item 1) -- the core fan-out fix.

    A generic trigger being present near the organization's own name
    (`_is_organization_associated`) is not, by itself, proof that a
    genuine business EVENT grounds it -- the trigger word could just be
    generic filler language. This function additionally requires the
    QUALIFYING SIGNAL that is about to promote the trigger to
    STRONG_INFERENCE to have its OWN regex match fall in the same
    sentence as -- or a sentence immediately adjacent to -- an occurrence
    of the matched trigger term itself. This is what distinguishes:

        GOOD: "Org X is opening a new facility. This expansion will
        require additional network capacity." -- the "expansion" signal
        and the "new facility" trigger are one sentence apart, describing
        the SAME event.

        BAD: "Org X signed a strategic partnership with a regional
        operator. ... [several unrelated paragraphs later] ... Org X's
        planned acquisition is expected to close next quarter." -- the
        "partnership" signal (even if it were in the qualifying set) and
        the "acquisition" trigger are nowhere near each other; they are
        two separate facts, not one event implying a technology need.

    A signal with no known match position (`match_start < 0` -- e.g. a
    hand-built Signal in a test/caller that didn't supply one) is treated
    as NOT locally verifiable and therefore not locally associated. This
    is a conservative default: it can only make the gate stricter (fewer
    false STRONG_INFERENCE promotions), never looser.
    """
    if signal is None or signal.match_start is None or signal.match_start < 0:
        return False

    spans = sentence_spans(text)
    if not spans:
        return False

    # Evidence-relevance hardening (recommendation #3): a signal's own
    # match position is, by construction, never inside a low-trust span
    # (commercial.signals.extract_signals already skips those) -- but the
    # TRIGGER term's own occurrence must be checked the same way here, so
    # a trigger word that only appears inside a buzzword list/navigation
    # span can't be treated as "the same event" as a genuine signal found
    # elsewhere in the source.
    signal_sentence_idxs = {
        i for i, (s, e) in enumerate(spans)
        if s <= signal.match_start < e and not _span_disqualifies_evidence(text[s:e])
    }
    if not signal_sentence_idxs:
        return False

    for term in trigger_terms:
        for match in _term_pattern(term).finditer(text):
            for i, (s, e) in enumerate(spans):
                if s <= match.start() < e:
                    if _span_disqualifies_evidence(text[s:e]):
                        break
                    if any(abs(j - i) <= radius for j in signal_sentence_idxs):
                        return True
                    break
    return False


def _direct_evidence_locally_grounded(
    text: str, terms: list[str], organization_name: str | None, *, country: str | None = None,
    radius: int = _SENTENCE_ASSOCIATION_RADIUS,
) -> int:
    """Evidence-relevance hardening, follow-up pass (Area 2 -- Rule 2/3
    locality gap, branch 1): how many DISTINCT matched terms are each,
    individually, locally associated with a mention of the target
    organization.

    `_is_organization_associated` answers a yes/no question ("is AT LEAST
    ONE occurrence of ANY of these terms near an organization mention?"),
    which is the right question for branches 2/3 (a single specific term
    plus a signal). Branch 1 of Rule 2/3, however, promotes to
    DIRECT_EVIDENCE on "two or more DISTINCT specific capability terms
    co-occurring" -- and the follow-up audit found that check was
    satisfiable by ONE term sitting right next to the organization's name
    while a SECOND, unrelated term (needed only to clear the "two terms"
    threshold) occurred somewhere else entirely in a long source. This
    function instead counts how many distinct terms individually clear the
    same locality bar, so the caller can require at least two of them to
    do so -- closing that gap while reusing the exact same association
    machinery (`_org_core_tokens`, `_mentions_organization`,
    `_span_disqualifies_evidence`, sentence-span radius) as everywhere
    else, rather than inventing a new mechanism.

    When no organization_name is supplied (direct callers/tests that
    exercise this module without pipeline org context), this is a
    deliberate no-op that returns the count of distinct terms unfiltered --
    identical to the prior unconditional `len(direct_hits) >= 2` check --
    so existing org-context-free tests are unaffected.
    """
    distinct_terms = {t.lower() for t in terms}
    if not organization_name:
        return len(distinct_terms)
    tokens = _org_core_tokens(organization_name, country=country)
    if not tokens:
        return len(distinct_terms)

    spans = sentence_spans(text)
    if not spans:
        return 0

    org_sentence_idxs = {
        i for i, (s, e) in enumerate(spans)
        if not _span_disqualifies_evidence(text[s:e])
        and _mentions_organization(text[s:e], organization_name, tokens, country=country)
    }
    if not org_sentence_idxs:
        return 0

    grounded_terms: set[str] = set()
    for term in terms:
        if term.lower() in grounded_terms:
            continue
        for match in _term_pattern(term).finditer(text):
            for i, (s, e) in enumerate(spans):
                if s <= match.start() < e:
                    if _span_disqualifies_evidence(text[s:e]):
                        break
                    if any(abs(j - i) <= radius for j in org_sentence_idxs):
                        grounded_terms.add(term.lower())
                    break
    return len(grounded_terms)


def _has_customer_requirement_language_near_terms(text: str, terms: list[str]) -> bool:
    """Evidence-relevance hardening, follow-up pass (Area 3): does at least
    one occurrence of one of `terms` fall in a local span that reads as
    customer/third-party-requirement language? Used purely for LABELING
    (which EvidenceCategory a non-promoted mapping is given), never for
    promotion itself -- promotion is already blocked for such occurrences
    via `_span_disqualifies_evidence` inside the association/locality
    checks above. This lets the epistemic-rule requirement ("classify
    unsupported evidence as non-promotable/contextual, not silently as a
    generic weak mention") apply specifically: a mapping that exists only
    because of customer-requirement language is tagged
    CUSTOMER_REQUIREMENT_CONTEXT instead of the less informative
    INCIDENTAL_MENTION.
    """
    spans = sentence_spans(text)
    if not spans:
        return False
    for term in terms:
        for match in _term_pattern(term).finditer(text):
            segment = span_containing(text, spans, match.start())
            if looks_like_customer_requirement_span(segment):
                return True
    return False


def _cluster_id(*parts: str) -> str:
    """Phase 2 hardening (item 4): a short, deterministic id shared by
    every CapabilityMapping that traces back to the SAME underlying
    source/event, so a consumer (opportunity scoring, the UI) can tell
    "four mappings from one business event" apart from "four
    independently-corroborating pieces of evidence." Pure hash of its
    inputs -- no randomness, no organization/sector-specific logic.
    """
    key = "|".join(p or "" for p in parts)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# Phase 1E: source provenance is separate from discovery family/source_type.
# Search-family labels such as "technology" describe WHY a result was found;
# they do not prove that the publisher is an authoritative technology source.
#
# Third-party technographics / technology-stack databases are useful current-
# state and incumbent context, but they are not evidence of a customer buying
# requirement. Detect them generically from URL/title/content shape rather than
# hard-coding any particular vendor, organization, sector, or country.
_TECHNOGRAPHICS_MARKERS = (
    "technographics",
    "technology-stack",
    "technology stack",
    "customers-database",
    "customers database",
    "software customers",
)
_LINKEDIN_JOB_PATH_RE = re.compile(r"^/jobs/", re.I)


# Signal Intelligence refinement: third-party company-DIRECTORY / data-
# provider profile pages ("<Org> Employee Directory, Headcount & Staff",
# "<Org> - Products, Competitors, Financials, Employees ...", "Top <Org>
# Alternatives, Competitors") describe an organization from the outside in
# templated boilerplate -- the same class as technographics: useful
# current-state context, never evidence that the organization is buying,
# implementing or requiring anything. Detected from URL and TITLE only
# (never body text), so a genuine article that mentions competitors or
# investors in passing is unaffected. Generic -- no provider or organization
# is named.
_DIRECTORY_PROFILE_RE = re.compile(
    r"\b(employee directory|headcount|org chart|products,? competitors|competitors,? financials|"
    r"alternatives,? competitors|alternatives (and|&) competitors|competitors (and|&) alternatives|"
    r"company profile and (employees|financials)|financials,? employees)\b",
    re.I,
)


def _is_directory_profile_page(source: Source) -> bool:
    haystack = " ".join(part for part in (source.url or "", source.title or "") if part)
    return bool(_DIRECTORY_PROFILE_RE.search(_URL_TITLE_SEPARATORS_RE.sub(" ", haystack)))


def _is_third_party_technographics(source: Source, text: str) -> bool:
    haystack = " ".join(
        part for part in (
            source.url or "",
            source.title or "",
            text[:2500] if text else "",
        ) if part
    ).lower()
    return any(marker in haystack for marker in _TECHNOGRAPHICS_MARKERS)


# Generic third-party industry / trend / best-practice / buyer's-guide pages
# (e.g. a vendor's "airport technology trends" article) can establish topical
# relevance, but they are not account-specific evidence that the target
# organization is buying, implementing, planning, procuring or requiring
# anything. Detected from the source's own URL slug and TITLE only -- never
# from body text -- so a genuine news report or official release that merely
# mentions "industry trends" in passing is unaffected. Closed, sector-agnostic
# phrase set; no organization, vendor, sector or country names.
_GENERIC_INDUSTRY_CONTENT_RE = re.compile(
    r"\b(?:"
    r"(?:technology|tech|industry|market|digital|innovation)\s+trends?"
    r"|trends?\s+(?:to\s+watch|(?:(?:for|in)\s+)?(?:19|20)\d{2})"
    r"|(?:19|20)\d{2}\s+(?:\w+\s+){0,2}trends"
    r"|top\s+\d+\s+(?:\w+\s+){0,3}trends"
    r"|(?:technology|industry|market)\s+(?:outlook|landscape)"
    r"|best\s+practices"
    r"|buyers?\s+guide"
    r")\b",
    re.I,
)
_URL_TITLE_SEPARATORS_RE = re.compile(r"[-_/.+%:|'’]+")


# Evidence-intelligence fix: a second, broader set of URL/title shapes that
# mark generic explainer / guidance / thought-leadership content ("Why OT
# cyber resilience matters for aviation", "How to secure airport networks",
# "5 ways to ...", "The future of ...", whitepapers, webinars). Unlike the
# trend/outlook set above, these phrasings are also used by genuine account
# stories ("How <Org> cut downtime with ..."), so this set is EXEMPTED when
# the title or URL slug itself names the researched account as its subject
# (see `_names_account_in_title_or_url`). Closed, sector-agnostic phrases.
_GENERIC_EXPLAINER_CONTENT_RE = re.compile(
    r"\b(?:"
    r"how\s+to|how\s+can|how\s+do"
    r"|why\s+(?:\w+\s+){0,6}matters?"
    r"|what\s+is|what\s+are"
    r"|(?:ultimate|complete|definitive|essential|beginner'?s?|practical)\s+guide|guide\s+to"
    r"|checklist|tips|\d+\s+(?:ways|steps|reasons|things)|ways\s+to"
    r"|the\s+future\s+of|the\s+rise\s+of|explained|explainer"
    r"|white\s?paper|e-?book|webinar|thought\s+leadership|best\s+practice"
    r"|challenges\s+(?:facing|for|of|in)"
    r")\b",
    re.I,
)


def _names_account_in_title_or_url(source: Source, organization_name: str | None, *, country: str | None = None) -> bool:
    """True when the source's own title or URL path names the researched
    account as its subject (never as an exemplar). A publisher titling or
    slugging a page after the account is the clearest third-party signal
    that the page is ABOUT the account."""
    if not organization_name:
        return False
    tokens = _org_core_tokens(organization_name, country=country)
    candidates = [source.title or ""]
    try:
        path = urlparse(source.url or "").path
    except ValueError:
        path = ""
    if path:
        candidates.append(_URL_TITLE_SEPARATORS_RE.sub(" ", path))
    return any(c and _mentions_organization(c, organization_name, tokens, country=country) for c in candidates)


def _is_generic_industry_content(
    source: Source, *, organization_name: str | None = None, country: str | None = None
) -> bool:
    haystack = _URL_TITLE_SEPARATORS_RE.sub(
        " ", " ".join(part for part in (source.url or "", source.title or "") if part)
    )
    if _GENERIC_INDUSTRY_CONTENT_RE.search(haystack):
        return True
    if _GENERIC_EXPLAINER_CONTENT_RE.search(haystack):
        return not _names_account_in_title_or_url(source, organization_name, country=country)
    return False


# Evidence-intelligence fix (account-centrality gate for direct evidence).
#
# ROOT CAUSE of the Oman Airports "airport OT cybersecurity" false positive:
# DIRECT_EVIDENCE was granted to ANY source in which >=1-2 capability terms
# sat within one sentence of an account mention and a technology-event word
# ("deploying", "upgrading") appeared nearby. Nothing asked whether the
# SOURCE was about the account at all. Three things combined:
#   1. the open-web "technology" discovery family labels every result
#      source_type="technology", which `_source_trust_tier` treats as HIGH
#      trust -- the same as the account's own website -- so a vendor blog
#      found by that query earned a high-trust bonus (70 + 10 = 80%);
#   2. association used a one-sentence radius, so a generic sentence
#      ("A zero trust architecture limits lateral movement.") next to a single
#      passing account sentence borrowed the account mention;
#   3. an account named only as an example ("airports such as X") still
#      counted as an account mention (fixed in `_mentions_organization`).
# epistemic_status is a pure function of evidence_level, so every DIRECT
# mapping became confirmed_evidence, and opportunities.py labels an
# opportunity confirmed when its best mapping is DIRECT.
#
# A third-party source may therefore only yield DIRECT_EVIDENCE when it is
# ACCOUNT-CENTRED -- decided from content + account association + source
# context, never from the publisher's domain alone:
#   - first-party: source_type "official" (the account's own site/domain); or
#   - the title or URL slug names the account as its subject; or
#   - the account is the subject of the lead (one of the first two narrative
#     sentences) -- the standard news/announcement shape; or
#   - the account is named in at least two distinct narrative sentences.
# Otherwise the source is CONTEXT that mentions the account in passing: its
# matched terms must sit in the SAME sentence as the account mention (no
# adjacent-sentence borrowing), and the best it can reach is
# STRONG_INFERENCE (an inference_hypothesis), never DIRECT/confirmed.
_ACCOUNT_LEAD_SENTENCES = 2
_ACCOUNT_MIN_NARRATIVE_MENTIONS = 2


def _source_is_account_centred(
    source: Source, text: str, organization_name: str | None, *, country: str | None = None
) -> bool:
    if not organization_name:
        return True  # opt-in pipeline context, like every other association gate here
    tokens = _org_core_tokens(organization_name, country=country)
    if not tokens:
        return True
    if source.source_type == SOURCE_TYPE_OFFICIAL:
        return True
    if _names_account_in_title_or_url(source, organization_name, country=country):
        return True
    spans = sentence_spans(text)
    narrative = [i for i, (s, e) in enumerate(spans) if not _span_disqualifies_evidence(text[s:e])]
    account_idxs = [
        i for i in narrative
        if _mentions_organization(text[spans[i][0]:spans[i][1]], organization_name, tokens, country=country)
    ]
    if len(account_idxs) >= _ACCOUNT_MIN_NARRATIVE_MENTIONS:
        return True
    lead = set(narrative[:_ACCOUNT_LEAD_SENTENCES])
    return any(i in lead for i in account_idxs)


# Signal Intelligence refinement -- account context established by a page
# TITLE.
#
# A page whose title names the account as its subject (e.g. "<Account>
# issues tender for airport systems") may state the event itself without
# repeating the account name ("The tender covers LiDAR-based boarding
# supervision ..."). The title may then stand in for the account mention,
# but ONLY when all of the following hold (the caller additionally requires
# specific catalogue terms with a locally grounded technology/procurement
# event, and caps the result at STRONG_INFERENCE -- a title can never
# CONFIRM anything):
#   - the title names the account (full name, short name or acronym; never
#     as an exemplar);
#   - the title is a single-item headline, not a listing/category page
#     ("Latest <Account> Tenders", "<Account> Contracts", "<Account> Jobs"):
#     a listing names the account as a TOPIC, and its entries can belong to
#     anyone -- the motivating real case was an aggregator page titled after
#     the account whose LiDAR/boarding-gate tender belonged to a different
#     airport;
#   - the event text is not one row of a multi-entry listing ("View Detail",
#     several publication/closing dates side by side).
# Generic: no organization, sector or country is named.
_LISTING_TITLE_RE = re.compile(
    r"\b(latest|tenders|contracts|bids|opportunities|jobs|vacancies|careers|directory|listings?|companies|"
    r"results|archive|news|updates|press releases|notices|announcements|events|projects|suppliers|vendors)\b",
    re.I,
)
_LISTING_ROW_RE = re.compile(r"\bview details?\b|\bclosing date\b|\bbid deadline\b", re.I)
_DATE_STAMP_RE = re.compile(
    r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}\b", re.I
)
_TITLE_EVENT_WINDOW = 300


def _headline_part(title: str) -> str:
    """The headline part of a page title, without a trailing " | Site"
    publisher suffix (which often contains words like "News")."""
    return re.split(r"\s+[|\u2014\u2013]\s+", title or "", maxsplit=1)[0]


def looks_like_listing_row(text: str) -> bool:
    return bool(_LISTING_ROW_RE.search(text or "")) or len(_DATE_STAMP_RE.findall(text or "")) >= 2


def title_establishes_account_context(
    title: str, event_text: str, organization_name: str | None, *, country: str | None = None
) -> bool:
    if not organization_name or not title:
        return False
    tokens = _org_core_tokens(organization_name, country=country)
    if not _mentions_organization(title, organization_name, tokens, country=country):
        return False
    if _LISTING_TITLE_RE.search(_headline_part(title)):
        return False
    return not looks_like_listing_row(event_text)


def _is_linkedin_job_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = parsed.netloc.lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return False
    return bool(_LINKEDIN_JOB_PATH_RE.match(parsed.path or ""))


# Phase 2 evidence-relevance hardening (recommendation #2): a vendor's own
# web property (a case study, product page, or press release hosted on the
# VENDOR's domain, e.g. "paloaltonetworks.com/customers/...") describing a
# customer's use of THAT vendor's product is evidence of the customer's
# EXISTING technology stack/vendor relationship, not an unmet Otech
# opportunity -- however many Otech capability keywords the vendor's own
# marketing copy happens to use. Detected generically from the source's own
# domain against the same horizontal vendor lexicon already used for
# incumbent detection (commercial/incumbents.py) -- no organization,
# sector, or country name is involved, and no NEW vendor list is
# introduced.
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def _normalize_domain_label(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value.lower())


def _vendor_term_for_source_domain(source: Source) -> str | None:
    domain = (source.domain or "").lower()
    if not domain:
        try:
            domain = (urlparse(source.url or "").netloc or "").lower()
        except ValueError:
            domain = ""
    if not domain:
        return None
    labels = [_normalize_domain_label(part) for part in domain.split(".") if part]
    if not labels:
        return None
    for term in _KNOWN_VENDOR_TERMS:
        normalized_term = _normalize_domain_label(term)
        if normalized_term and normalized_term in labels:
            return term
    return None


# Phase 2 evidence-relevance hardening (recommendation #6): how many
# DISTINCT generic executive-biography/interview marker phrases must be
# found in a source's full text before the whole source is treated as
# profile/interview content (the same stakeholder_context treatment
# already applied to a personal LinkedIn profile), rather than a genuine
# business-event article that happens to quote or describe an executive
# once. A single incidental marker (e.g. one "is responsible for..."
# clause inside an otherwise-substantive announcement) is deliberately
# NOT enough -- this only fires for content that reads, on the whole, like
# a biography or interview profile.
_MIN_EXECUTIVE_BIO_MARKERS = 2


def _source_trust_tier(source: Source) -> str:
    if source.source_type in _HIGH_TRUST_SOURCE_TYPES:
        return "high"
    if source.source_type in _MEDIUM_TRUST_SOURCE_TYPES:
        return "medium"
    return "low"  # linkedin_profile and anything else


def _confidence_for(
    evidence_level: EvidenceLevel, *, trust_tier: str, direct_count: int, weak_or_generic_count: int, signal_backed: bool
) -> float:
    trust_bonus = {"high": 10, "medium": 5, "low": 0}[trust_tier]
    if evidence_level == EvidenceLevel.DIRECT_EVIDENCE:
        score = 70 + trust_bonus + min(20, 5 * max(0, direct_count - 1))
        return float(max(60, min(96, score)))
    if evidence_level == EvidenceLevel.STRONG_INFERENCE:
        score = 45 + trust_bonus + (5 if signal_backed else 0)
        return float(max(35, min(70, score)))
    # WEAK_RELEVANCE
    score = 15 + trust_bonus + min(10, 2 * weak_or_generic_count)
    return float(max(5, min(35, score)))


# Evidence-relevance hardening, follow-up pass (Area 4 -- generic
# capacity-expansion semantic ambiguity), generalized by the Current/Change/
# Requirement Signal Gate to cover generic-trigger fan-out.
#
# A generic trigger is "ambiguous" -- unable, on its own, to say WHICH
# capability domain it concerns -- in two independent, equally generic
# ways, and this is the union of both:
#
#   (a) it is built around the word "capacity" (e.g. "capacity expansion"):
#       manufacturing capacity, renewable-generation capacity, logistics
#       capacity, physical production capacity, and IT/data-centre capacity
#       are all equally plausible readings of the bare phrase; or
#
#   (b) it is SHARED -- listed as a generic_trigger by more than one
#       capability in the loaded catalogue (e.g. "digital transformation"
#       appears for cloud_ai_infrastructure, cybersecurity_resilience, AND
#       enterprise_managed_services). A trigger word shared across several
#       otherwise-unrelated capabilities says even less about which
#       specific capability domain the source is actually about than a
#       capability-exclusive trigger does -- the exact shape of the
#       generic-trigger fan-out problem (one "digital transformation"
#       mention independently producing three STRONG_INFERENCE hypotheses).
#
# (b) is computed PURELY from `get_catalog()` at call time -- no trigger
# string, capability id, industry, or sector is ever hardcoded here. If the
# catalogue changes (a trigger is added to/removed from a capability), this
# recomputes automatically the next time it runs; get_catalog() itself is
# already cached, so recomputing the shared-trigger set on every call is
# cheap (iterating a handful of capabilities' short trigger lists) and
# always reflects whatever catalogue is currently loaded -- important for
# tests that call `get_catalog(force_reload=True)` with a different
# catalogue fixture.
_AMBIGUOUS_GENERIC_TRIGGER_RE = re.compile(r"\bcapacity\b", re.I)


def _shared_generic_triggers() -> frozenset[str]:
    counts: dict[str, int] = {}
    for capability in get_catalog():
        for term in {t.strip().lower() for t in capability.get("generic_triggers", [])}:
            counts[term] = counts.get(term, 0) + 1
    return frozenset(term for term, n in counts.items() if n > 1)


def _generic_trigger_is_ambiguous(trigger_term: str) -> bool:
    return bool(_AMBIGUOUS_GENERIC_TRIGGER_RE.search(trigger_term)) or trigger_term.strip().lower() in _shared_generic_triggers()


def _locally_supported_by_context_terms(
    text: str, trigger_terms: list[str], context_terms: list[str], *, radius: int = _SENTENCE_ASSOCIATION_RADIUS,
    also_exclude_terms: tuple[str, ...] = (),
) -> bool:
    """Evidence-relevance hardening, follow-up pass (Area 4): does at least
    one occurrence of one of `trigger_terms` have one of `context_terms`
    locally associated with it (same sentence, or an immediately adjacent
    one)?

    Used to require an AMBIGUOUS generic trigger (see
    `_generic_trigger_is_ambiguous`) to be locally corroborated by actual
    IT/infrastructure-specific vocabulary before it can ground a
    capability's promotion -- e.g. "The company announced a major capacity
    expansion, adding new server racks and doubling its data centre
    footprint" locally associates "capacity expansion" with "server rack"
    and "data centre" and remains promotable, while "The plant's production
    capacity expansion will add 200 jobs" does not.

    `context_terms` is always the CAPABILITY'S OWN catalogue keywords/
    weak-signal vocabulary (see call site) -- already sector-agnostic
    reference data describing what IT/infrastructure language looks like
    for that specific capability -- never a new hardcoded list of
    industries, companies, or sectors to exclude.

    `also_exclude_terms` (F2b): additional term(s), typically the specific
    keyword(s) that already qualified the calling promotion branch, whose
    own matched text is excluded from the context search exactly like the
    trigger's own matched text already is -- see `_correlating_signal_has_
    tech_context`'s docstring for why this matters (a multi-word keyword
    can otherwise contain one of the capability's own context words and
    trivially "corroborate" itself).
    """
    if not context_terms:
        return False
    spans = sentence_spans(text)
    if not spans:
        return False

    trigger_sentence_idxs: set[int] = set()
    # Current/Change/Requirement Signal Gate refinement: also record the
    # trigger's own matched character spans. A shared generic trigger phrase
    # can trivially CONTAIN one of its own qualifying capabilities' context
    # words (e.g. the trigger "digital transformation" contains the word
    # "transformation", which is itself enterprise_managed_services' own
    # weak_signal_term) -- that is not independent corroboration, it is the
    # same three words counted twice. Only a context-term occurrence OUTSIDE
    # the trigger's own matched text counts as genuine local support.
    trigger_match_spans: list[tuple[int, int]] = []
    for term in trigger_terms:
        for match in _term_pattern(term).finditer(text):
            for i, (s, e) in enumerate(spans):
                if s <= match.start() < e:
                    if not _span_disqualifies_evidence(text[s:e]):
                        trigger_sentence_idxs.add(i)
                        trigger_match_spans.append((match.start(), match.end()))
                    break
    if not trigger_sentence_idxs:
        return False

    # F2b (Golden Case G3 -- self-corroboration hole, generic): a caller may
    # pass terms that are already "spent" -- typically the SAME term(s) that
    # qualified the promotion branch calling this function -- so that an
    # occurrence of a context term overlapping one of THOSE terms' own
    # matched text is excluded too, exactly like a trigger's own matched
    # text is excluded above. Without this, a multi-word capability keyword
    # that itself CONTAINS one of the capability's own context words (e.g.
    # the keyword "transport network" contains network_connectivity's own
    # weak-signal term "network") can "corroborate" itself: the same three
    # words are being counted twice, not two independent pieces of
    # evidence. This is the general form of the same principle already
    # applied to `trigger_terms` above -- no organization, sector, or
    # specific phrase pair is hardcoded; any caller-supplied exclusion term
    # is treated identically.
    for _excl_term in also_exclude_terms:
        for _excl_match in _term_pattern(_excl_term).finditer(text):
            trigger_match_spans.append((_excl_match.start(), _excl_match.end()))

    for term in context_terms:
        for match in _term_pattern(term).finditer(text):
            if any(match.start() < te and ts < match.end() for ts, te in trigger_match_spans):
                continue  # overlaps the trigger's own matched text -- not independent
            for i, (s, e) in enumerate(spans):
                if s <= match.start() < e:
                    if _span_disqualifies_evidence(text[s:e]):
                        break
                    if any(abs(i - j) <= radius for j in trigger_sentence_idxs):
                        return True
                    break
    return False


def _usable_generic_hits(text: str, generic_hits: list[str], capability: dict) -> list[str]:
    """Evidence-relevance hardening, follow-up pass (Area 4): filters
    `generic_hits` down to the ones usable for Rule 4 PROMOTION decisions --
    every non-ambiguous trigger passes through unchanged, and an ambiguous
    one (see `_generic_trigger_is_ambiguous`) is kept only when it is
    locally supported by this capability's own IT/infrastructure-specific
    keyword vocabulary (see `_locally_supported_by_context_terms`).

    Deliberately does NOT touch `matched_terms` on the resulting
    CapabilityMapping -- the caller still records the FULL `generic_hits`
    list there for transparency (an unsupported ambiguous trigger is still
    disclosed evidence, just not promotion-eligible on its own); only the
    promotion decision itself is narrowed.
    """
    context_terms = list(capability["keywords"]) + list(capability.get("weak_signal_terms", []))
    usable = []
    for term in generic_hits:
        if not _generic_trigger_is_ambiguous(term):
            usable.append(term)
        elif _locally_supported_by_context_terms(text, [term], context_terms):
            usable.append(term)
    return usable


# F2c -- closed-class, domain-independent prepositions used to distinguish a
# keyword occurrence functioning as the direct grammatical OBJECT of an
# action ("investment IN X", "contract awarded FOR X") from one functioning
# as a SCOPE/coverage modifier ("collaboration ACROSS X", "partnership
# COVERING X"). This is ordinary English function-word grammar -- the same
# fixed list applies to every organization, sector, and capability; it is
# never a rule about any specific word, phrase, or industry. Unknown/
# unlisted prepositions fail closed (treated as neither target nor scope,
# so the fallback simply does not apply -- never as an implicit target).
_TARGET_PREPOSITIONS = frozenset({"in", "into", "toward", "towards", "for", "on"})
_SCOPE_PREPOSITIONS = frozenset({"across", "covering", "spanning", "throughout", "over", "within"})
# Simple determiners/modifiers skipped when walking back from the keyword's
# own match to find the preposition governing it -- e.g. "investment in
# ADDITIONAL compute capacity" must still see "in", not "additional".
_SKIPPABLE_MODIFIERS = frozenset({
    "a", "an", "the", "its", "their", "his", "her", "our", "your",
    "additional", "new", "major", "further", "own", "planned", "recent",
})
_SKIP_WALK_LIMIT = 3


def _keyword_is_object_of_target_preposition(text: str, keyword_match_start: int) -> bool:
    """True if the word immediately governing the keyword occurrence at
    `keyword_match_start` (skipping up to `_SKIP_WALK_LIMIT` simple
    determiners/modifiers) is a target preposition, and False if it is a
    scope preposition or anything else (fail-closed for unrecognized
    words -- this never treats silence as a target).
    """
    preceding_words = re.findall(r"[A-Za-z]+", text[:keyword_match_start])
    i = len(preceding_words) - 1
    skipped = 0
    while i >= 0 and preceding_words[i].lower() in _SKIPPABLE_MODIFIERS and skipped < _SKIP_WALK_LIMIT:
        i -= 1
        skipped += 1
    if i < 0:
        return False
    governing_word = preceding_words[i].lower()
    if governing_word in _SCOPE_PREPOSITIONS:
        return False
    return governing_word in _TARGET_PREPOSITIONS


def _correlating_signal_has_tech_context(
    text: str, signal: Signal, capability: dict, *, exclude_terms: list[str]
) -> bool:
    """False-positive-class fix (generic-strategy-article finding, extended
    by F2 for Golden Case G3): a signal that merely CORRELATES with
    technology need -- as opposed to describing one directly
    (`_TECH_EVENT_SIGNAL_TYPES`) -- may still ground a promotion, but only
    when this capability's own context vocabulary (keywords +
    weak_signal_terms) is independently, locally present near the SIGNAL's
    own matched text: "This expansion will require additional network
    capacity" locally corroborates an otherwise-generic "expansion" with
    network_connectivity's own weak-signal term "network". Only a genuine
    technology-EVENT signal (`_TECH_EVENT_SIGNAL_TYPES`) is entirely
    unaffected by this check and always returns True -- every other signal
    type requires corroboration, fail-closed, whether or not it is also a
    member of the narrower `_CORRELATING_ONLY_SIGNAL_TYPES` set (which
    exists only for Rule 4's separate ambiguous-generic-trigger path, not
    this one).

    `exclude_terms` -- the trigger/keyword term(s) that already qualified
    this capability for the promotion branch being evaluated (`usable_
    generic_hits` for Rule 4, `direct_hits` for Rule 2/3's single-hit
    fallback) -- must be excluded from the context search. Without this,
    the check is vacuous for Rule 2/3's single-keyword path: the very
    keyword that produced `direct_hits` (e.g. "platform migration") is
    ITSELF one of `capability["keywords"]`, so an unfiltered search would
    always trivially find it sitting next to the signal, defeating the
    corroboration requirement entirely.

    F2c (Golden Case regression fix -- "investment in additional compute
    capacity" must still promote): the independent-context search above can
    find nothing when the ONLY candidate context term is a substring of the
    qualifying keyword's own text -- e.g. "compute capacity" contains
    data_centre_infrastructure's own weak_signal_term "compute", so F2b's
    overlap exclusion correctly removes it as self-corroboration. This is
    not a rare edge case: most multi-word catalogue keywords are built from
    [a modifier] + [the capability's own weak_signal_term] (compute
    capacity, cloud migration, server infrastructure, it outsourcing...),
    so an unqualified signal paired with a single such keyword can never
    satisfy the independent-context check on its own, regardless of
    whether the keyword genuinely describes the organization's own
    technology decision or is an unrelated homonym (network_connectivity's
    "transport network" -- also transport-and-logistics network, not IT
    networking -- has the identical shape and must NOT promote).

    Lexical vocabulary cannot tell these two apart -- "compute capacity"
    and "transport network" are equally specific catalogue keywords, at
    the same word-distance from their signal. What DOES distinguish them
    is closed-class English grammar: "investment IN compute capacity"
    names the keyword as the direct grammatical OBJECT of the signal via a
    TARGET preposition, while "collaboration ACROSS their transport
    network" names it as a SCOPE/coverage modifier via a different,
    equally closed-class preposition. `_keyword_is_object_of_target_
    preposition` checks only this: a small, fixed, domain-independent set
    of prepositions, identical for every organization, sector, and
    capability -- never a rule about a specific word, phrase, or industry.
    Falls back to this ONLY when the independent-context check above found
    nothing, and ONLY for terms that are genuine catalogue `keywords` --
    never for a `generic_triggers` term (Rule 4's `usable_generic_hits`),
    which is deliberately ambiguous by design and must keep requiring
    independent corroboration; loosening it here would reopen exactly the
    class of false positive that gate exists to prevent.

    Reuses the exact mechanism already built for ambiguous generic triggers
    (`_locally_supported_by_context_terms`) -- no new taxonomy, no new
    signal vocabulary, no organization/sector/country branching.
    """
    # F2 (Golden Case G3 -- fail-closed signal gating, generic): the prior
    # condition ("not in _CORRELATING_ONLY_SIGNAL_TYPES") meant any signal
    # type NOT EXPLICITLY in the narrow {expansion, project} correlating
    # set was trusted with no corroboration check at all -- including
    # `partnership`, `investment`, and `contract_award` (all members of
    # `_QUALIFYING_SIGNAL_TYPES`, used by this same single-hit fallback,
    # but never added to `_CORRELATING_ONLY_SIGNAL_TYPES`). A partnership/
    # MOU announcement is exactly as generic as a bare "expansion" mention
    # -- neither one, by itself, says anything about technology -- so it
    # deserves the identical local-corroboration requirement. Gating is now
    # fail-closed: only a genuine, specific technology EVENT signal
    # (`_TECH_EVENT_SIGNAL_TYPES`) is trusted without a corroboration
    # check; every other signal type -- present today or added later --
    # must independently demonstrate local technology context. This is a
    # generic signal-type check, not a rule about any specific signal,
    # organization, or phrase.
    if signal.signal_type in _TECH_EVENT_SIGNAL_TYPES:
        return True
    exclude = {t.strip().lower() for t in exclude_terms}
    context_terms = [
        t for t in (list(capability["keywords"]) + list(capability.get("weak_signal_terms", [])))
        if t.strip().lower() not in exclude
    ]
    if _locally_supported_by_context_terms(
        text, signal.matched_terms, context_terms, also_exclude_terms=tuple(exclude_terms)
    ):
        return True

    # F2c fallback (see docstring above): only reached when no independent
    # context term exists. Scoped to genuine catalogue keywords only -- a
    # generic_triggers term (Rule 4's usable_generic_hits) never qualifies.
    catalogue_keywords = {k.strip().lower() for k in capability.get("keywords", [])}
    for term in exclude_terms:
        if term.strip().lower() not in catalogue_keywords:
            continue
        for match in _term_pattern(term).finditer(text):
            if _keyword_is_object_of_target_preposition(text, match.start()):
                return True
    return False


def map_source_to_capabilities(
    source: Source, *, organization_name: str | None = None, country: str | None = None
) -> list[CapabilityMapping]:
    """Classify one source against the full capability catalogue.

    Returns zero or more CapabilityMapping entries -- most (source,
    capability) pairs will produce nothing at all, which is correct: most
    sources are not evidence for most capabilities.

    `organization_name` (Phase 1C-H3, optional): when supplied, gates
    direct_evidence promotion on the matched technology term(s) actually
    occurring near a mention of THIS organization in the source text (see
    `_is_organization_associated`) -- the resolved organization name for
    whatever account this research run is for, never a hardcoded value.
    Omitted by default so existing direct callers/tests that exercise this
    function in isolation are unaffected; the real pipeline
    (`map_all_sources` as called from `research.analysis.report`) always
    supplies it.

    `country` (evidence-relevance hardening, recommendation #4, optional):
    the dynamically resolved country for the same account, used only to
    exclude country-name tokens from organization-association matching
    (see `_org_core_tokens`) -- never a hardcoded value, and never used
    for anything else.
    """
    if not source.url:
        # No traceability possible -- refuse to produce an unsupported claim.
        return []

    text = (source.content or "").strip() or f"{source.title} {source.snippet}".strip()
    if not text:
        return []

    trust_tier = _source_trust_tier(source)
    # Phase 1E: distinguish third-party current-state technographics from
    # genuine organizational technology/project evidence. The former remains
    # useful evidence, but can never be promoted to a sales-requirement tier.
    is_technographics = _is_third_party_technographics(source, text) or _is_directory_profile_page(source)
    is_linkedin_job = _is_linkedin_job_url(source.url)
    # Robust to upstream misclassification (see the H3.1 explanation above
    # `_is_linkedin_personal_profile_url`): a personal profile is a
    # personal profile whether or not acquisition tagged it that way.
    is_linkedin_profile = (
        source.source_type == SOURCE_TYPE_LINKEDIN_PROFILE or _is_linkedin_personal_profile_url(source.url)
    )
    # Generic third-party industry/trend content (URL/title only -- see
    # `_is_generic_industry_content`) is context, never account-specific
    # buying evidence: routed through Rule 0 below. LinkedIn sources keep
    # their own Rule 1 treatment.
    is_generic_industry_content = (
        not is_linkedin_job and not is_linkedin_profile
        and _is_generic_industry_content(source, organization_name=organization_name, country=country)
    )
    # Evidence-intelligence fix: is this source ABOUT the account, or context
    # that mentions it in passing? See `_source_is_account_centred`.
    account_centred = _source_is_account_centred(source, text, organization_name, country=country)
    association_radius = _SENTENCE_ASSOCIATION_RADIUS if account_centred else 0

    # Evidence-relevance hardening (recommendation #6): generic executive
    # biography/interview content gets the same stakeholder_context
    # treatment as a personal LinkedIn profile, regardless of publisher --
    # a vague "is responsible for digital transformation" profile blurb is
    # about a PERSON's remit, not a confirmed organizational requirement,
    # whether it lives on LinkedIn or a third-party interview/"leadership"
    # page.
    is_executive_bio_source = executive_bio_marker_count(text) >= _MIN_EXECUTIVE_BIO_MARKERS

    # Evidence-relevance hardening (recommendation #5): a retail/product/
    # pricing page's own marketing copy ("Growth Plan," "Expansion
    # Finance," "increase your capacity to save") must not be read as the
    # ORGANIZATION's own internal expansion/technology signal merely
    # because it shares vocabulary with the capability catalogue's generic
    # triggers. Detected from source/path/content shape only (see
    # `looks_like_customer_product_page`) -- no company name involved.
    is_customer_product_page = looks_like_customer_product_page(source.url, text)

    # Evidence-relevance hardening (recommendation #2): a source published
    # BY a known technology vendor's own domain, describing a customer's
    # use of THAT vendor's product, is current-state/incumbent context --
    # not evidence of an unmet Otech opportunity -- when that same vendor
    # is independently detected as incumbent evidence in this same source.
    vendor_domain_term = _vendor_term_for_source_domain(source)

    if is_linkedin_profile or is_executive_bio_source:
        trust_tier = "low"

    # F3 (Golden Case G3): pass this module's own span disqualifier so a
    # signal type's first-in-document match is never taken from a span
    # (vendor-case-study, customer-requirement, collective-scope, or
    # non-narrative chrome) that this module would itself reject as not
    # describing the organization's own activity -- see extract_signals'
    # docstring for why this matters.
    signals = extract_signals(source, extra_span_filter=_span_disqualifies_evidence)
    qualifying_signal = next((s for s in signals if s.signal_type in _QUALIFYING_SIGNAL_TYPES), None)
    # The narrower "did something concrete actually happen with technology
    # here" signal, used to gate direct_evidence (item 2/3 of Phase 1C-H2).
    tech_event_signal = next((s for s in signals if s.signal_type in _TECH_EVENT_SIGNAL_TYPES), None)

    incumbent_hits = detect_incumbent_evidence(source)
    incumbent_terms = [i.vendor_or_partner_term for i in incumbent_hits]

    is_vendor_case_study = bool(
        vendor_domain_term and any(t.lower() == vendor_domain_term.lower() for t in incumbent_terms)
    )

    out: list[CapabilityMapping] = []

    for capability in get_catalog():
        direct_hits = _match_terms(text, capability["keywords"])
        weak_hits = _match_terms(text, capability.get("weak_signal_terms", []))
        generic_hits = _match_terms(text, capability["generic_triggers"])

        if not direct_hits and not weak_hits and not generic_hits:
            continue  # no relationship at all between this source and this capability

        # --- Rule 0: third-party technographics/current-state sources, and
        # (recommendation #2) a vendor's own case-study/product page about
        # ITS OWN product, are never sales-requirement evidence. They may
        # describe an existing technology stack (and incumbent vendors),
        # which is valuable for competitive context, but the source does
        # not establish a customer buying requirement. Keep the match as
        # weak_relevance so it remains traceable without feeding the AI
        # "opportunity" path.
        if is_technographics or is_vendor_case_study or is_generic_industry_content:
            matched = direct_hits + weak_hits + generic_hits
            if not matched:
                continue
            level = EvidenceLevel.WEAK_RELEVANCE
            confidence = _confidence_for(
                level, trust_tier="low", direct_count=0,
                weak_or_generic_count=len(matched), signal_backed=False,
            )
            cluster_reason = (
                "technographics" if is_technographics
                else "vendor_case_study" if is_vendor_case_study
                else "industry_context"
            )
            rule0_category = (
                EvidenceCategory.INDUSTRY_CONTEXT
                if cluster_reason == "industry_context"
                else EvidenceCategory.CURRENT_STATE_TECHNOLOGY
            )
            out.append(
                CapabilityMapping(
                    capability_id=capability["id"],
                    capability_name=capability["name"],
                    evidence_level=level,
                    matched_terms=matched,
                    source_url=source.url,
                    source_title=source.title,
                    confidence=confidence,
                    supporting_context=_first_match_context(text, matched),
                    business_impact=capability["why"],
                    technology_requirement=capability["capability"],
                    signal_type=None,
                    incumbent_context=incumbent_terms,
                    evidence_category=rule0_category.value,
                    inference_cluster_id=_cluster_id(source.url, capability["id"], cluster_reason),
                    epistemic_status=epistemic_status_for(level).value,
                )
            )
            continue

        # --- Rule 1: LinkedIn personal-profile mentions -- and (recommendation
        # #6) generic executive-biography/interview content, wherever
        # published -- are never more than weak relevance, regardless of how
        # many terms matched. A person's bio/title/profile says something
        # about THAT PERSON's background or remit, not a confirmed
        # organizational requirement.
        if is_linkedin_profile or is_linkedin_job or is_executive_bio_source:
            matched = direct_hits + weak_hits + generic_hits
            if not matched:
                continue
            level = EvidenceLevel.WEAK_RELEVANCE
            confidence = _confidence_for(level, trust_tier="low", direct_count=0, weak_or_generic_count=len(matched), signal_backed=False)
            category = EvidenceCategory.HIRING_INTENT_CONTEXT if is_linkedin_job else EvidenceCategory.STAKEHOLDER_CONTEXT
            cluster_reason = "linkedin" if (is_linkedin_profile or is_linkedin_job) else "executive_bio"
            out.append(
                CapabilityMapping(
                    capability_id=capability["id"],
                    capability_name=capability["name"],
                    evidence_level=level,
                    matched_terms=matched,
                    source_url=source.url,
                    source_title=source.title,
                    confidence=confidence,
                    supporting_context=_first_match_context(text, matched),
                    business_impact=capability["why"],
                    technology_requirement=capability["capability"],
                    signal_type=None,
                    incumbent_context=incumbent_terms,
                    evidence_category=category.value,
                    inference_cluster_id=_cluster_id(source.url, capability["id"], cluster_reason),
                    epistemic_status=epistemic_status_for(level).value,
                )
            )
            continue

        # --- Rule 2/3: direct keyword evidence.
        #
        # Phase 1C-H2: two or more DISTINCT specific capability terms
        # co-occurring in the same source remains sufficient on its own --
        # that kind of coincidence is unlikely regardless of source type or
        # backing signal. But a SINGLE specific term is no longer promoted
        # to direct_evidence by trust tier alone: a high-trust source
        # (official/technology/procurement) that merely name-drops one
        # technology term in passing -- e.g. an MOU, partnership, or
        # regional-cooperation article that happens to mention "network" --
        # is not, by itself, evidence that the organization has an
        # explicit technology requirement. Promoting a lone term to direct
        # evidence now additionally requires a genuine technology-event
        # signal (technology_initiative / procurement_activity /
        # capacity_change) extracted from that SAME source -- i.e. the
        # source must actually describe a concrete technology event
        # (an initiative, upgrade, renewal, deployment, or procurement
        # action), not just contain the word.
        #
        # Phase 1C-H3: neither path above is sufficient on its own if the
        # matched term(s) aren't actually IN CONTEXT with a mention of the
        # target organization (item 1/3/6) -- a government/MOU/partnership/
        # roundup article can legitimately contain both a real organization
        # mention AND a real technology-programme phrase without the two
        # being connected (e.g. "Oman Airports and Algeria signed an MOU
        # ... the National Digital Transformation Programme has 66
        # participating companies" -- the org is mentioned, the technology
        # programme is mentioned, but not about each other). When
        # organization_name is supplied and no matched term sits near a
        # mention of it, direct_evidence is refused even though the
        # keyword-count/signal conditions would otherwise allow it.
        #
        # Everything that doesn't clear these bars is retained as
        # strong_inference (if there's a real signal backing it) or
        # weak_relevance -- never silently dropped (item 4/7).
        #
        # Evidence-relevance hardening (recommendation #4): the fallback
        # STRONG_INFERENCE branch below previously required NO organization
        # association at all -- a signal-backed term match anywhere in the
        # source, about anyone, was enough. That is exactly the gap that
        # let a government/aggregator/regional page's technology-programme
        # language promote a capability for an organization the matched
        # term was never actually about. It now requires the same
        # `org_associated` gate as the direct_evidence branches above.
        #
        # Evidence-relevance hardening (recommendation #5): additionally,
        # neither promotion path fires on a customer-facing retail/product/
        # pricing page (`is_customer_product_page`) -- a signal extracted
        # from that page's own marketing copy is far more likely to be
        # product-name wordplay ("Growth Plan," "Expansion Finance") than a
        # genuine organizational technology event.
        #
        # Evidence-relevance hardening, follow-up pass (Area 2 -- Rule 2/3
        # locality gap): the follow-up audit found two related gaps in the
        # branches below. First, branch 1 ("two or more distinct terms")
        # only required org-association from AT LEAST ONE of the matched
        # terms -- a second, unrelated term occurring anywhere else in a
        # long source was enough to clear "two distinct terms" without
        # itself being anywhere near the organization. `_direct_evidence_
        # locally_grounded` fixes this by counting how many terms are
        # EACH individually locally grounded, and requiring at least two.
        # Second, branches 2/3 treated `tech_event_signal`/`qualifying_
        # signal` as usable simply because ONE was extracted from the
        # source -- with no check that the signal's own match was anywhere
        # near the matched capability term(s) it was supposedly backing
        # (this is exactly Rule 4's item-1 fix, now applied to Rule 2/3's
        # direct_hits the same way it was already applied to Rule 4's
        # generic_hits). A signal several paragraphs away describes a
        # DIFFERENT fact, not evidence that this particular term reflects a
        # real technology event.
        if direct_hits:
            org_associated = _is_organization_associated(
                text, direct_hits, organization_name, radius=association_radius, country=country
            )
            grounded_term_count = _direct_evidence_locally_grounded(
                text, direct_hits, organization_name, country=country, radius=association_radius
            )
            tech_event_locally_grounded = tech_event_signal is not None and _signal_locally_associated_with_trigger(
                text, direct_hits, tech_event_signal
            )
            qualifying_signal_locally_grounded = qualifying_signal is not None and _signal_locally_associated_with_trigger(
                text, direct_hits, qualifying_signal
            )
            # Scope correction: the corroboration requirement from
            # `_correlating_signal_has_tech_context` applies ONLY to the
            # single-direct-hit STRONG_INFERENCE fallback below (branch 4) --
            # NOT to the established two-or-more-grounded-term DIRECT_EVIDENCE
            # branch (branch 1) immediately below, whose use of the broader
            # qualifying-signal set is pre-existing, already-tested design
            # that must be preserved exactly. A second, single-purpose
            # variable keeps that scope explicit and prevents this narrower
            # gate from silently leaking into the two-term branch again.
            qualifying_signal_single_hit_corroborated = (
                qualifying_signal_locally_grounded
                and _correlating_signal_has_tech_context(text, qualifying_signal, capability, exclude_terms=direct_hits)
            )
            # Current/Change/Requirement Signal Gate (approved design): two
            # or more DISTINCT specific catalogue terms co-occurring, each
            # individually locally grounded to the organization, remains
            # sufficient for DIRECT_EVIDENCE only when a locally-grounded
            # change/requirement signal (technology_initiative/procurement_
            # activity/capacity_change, or the broader qualifying set --
            # exactly the same signals branches 2/3 below already require)
            # is ALSO present. Without one, this is real evidence that the
            # organization HAS or DESCRIBES this capability area -- not that
            # anything is currently changing -- so it is downgraded to
            # STRONG_INFERENCE / EXISTING_CAPABILITY_CONTEXT (see category
            # assignment below) rather than dropped. This is the fix for the
            # reported false-positive pattern: "Oman Airports is equipped
            # with modern systems including a security operations center
            # and zero trust architecture" previously cleared this branch on
            # keyword-count alone.
            existing_capability_only = False
            if grounded_term_count >= 2 and (tech_event_locally_grounded or qualifying_signal_locally_grounded):
                level = EvidenceLevel.DIRECT_EVIDENCE
            elif grounded_term_count >= 2:
                level = EvidenceLevel.STRONG_INFERENCE
                existing_capability_only = True
            elif org_associated and trust_tier == "high" and tech_event_locally_grounded:
                level = EvidenceLevel.DIRECT_EVIDENCE
            elif (
                org_associated and not is_customer_product_page
                and (tech_event_locally_grounded or qualifying_signal_single_hit_corroborated)
            ):
                level = EvidenceLevel.STRONG_INFERENCE
            else:
                level = EvidenceLevel.WEAK_RELEVANCE
            # Signal Intelligence refinement: a single-item page titled
            # after the account, describing a specific technology/
            # procurement event that does not repeat the account name. See
            # `title_establishes_account_context`. Never above
            # STRONG_INFERENCE: the account link rests on the title.
            title_associated = False
            if (
                level == EvidenceLevel.WEAK_RELEVANCE and not org_associated and not is_customer_product_page
                and tech_event_locally_grounded
            ):
                window = text[
                    max(0, tech_event_signal.match_start - _TITLE_EVENT_WINDOW):
                    tech_event_signal.match_end + _TITLE_EVENT_WINDOW
                ]
                if title_establishes_account_context(source.title or "", window, organization_name, country=country):
                    level = EvidenceLevel.STRONG_INFERENCE
                    title_associated = True
            # Evidence-intelligence fix: only an account-centred source can
            # CONFIRM anything. A context source that names the account in
            # passing keeps its (same-sentence, signal-backed) mapping as an
            # inference to validate -- it is reclassified, not dropped.
            if level == EvidenceLevel.DIRECT_EVIDENCE and not account_centred:
                level = EvidenceLevel.STRONG_INFERENCE
            confidence = _confidence_for(
                level, trust_tier=trust_tier, direct_count=len(direct_hits), weak_or_generic_count=len(direct_hits),
                signal_backed=tech_event_locally_grounded or qualifying_signal_locally_grounded,
            )
            # The chain's signal_type prefers the concrete technology-event
            # signal when one grounds this mapping; falling back to the
            # broader qualifying (account/business) signal only when no
            # technology event was found -- and, per the Area 2 fix above,
            # only ever a signal that is actually LOCALLY ASSOCIATED with
            # the matched term(s), never merely present somewhere in the
            # source -- so the chain still shows what customer signal
            # accompanied the evidence even at strong_inference level.
            grounding_signal = (
                tech_event_signal if tech_event_locally_grounded
                else (qualifying_signal if qualifying_signal_locally_grounded else None)
            )
            if level == EvidenceLevel.DIRECT_EVIDENCE:
                category = (
                    EvidenceCategory.PROCUREMENT_SIGNAL
                    if grounding_signal and grounding_signal.signal_type == "procurement_activity"
                    else EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT
                )
            elif level == EvidenceLevel.STRONG_INFERENCE:
                # Current/Change/Requirement Signal Gate: distinguish "two
                # specific terms, no signal" (EXISTING_CAPABILITY_CONTEXT --
                # see commercial/opportunities.py, never independently
                # promotable) from the pre-existing "signal-backed inference"
                # meaning of BUSINESS_SIGNAL_INFERENCE.
                category = (
                    EvidenceCategory.EXISTING_CAPABILITY_CONTEXT if existing_capability_only
                    else EvidenceCategory.PROCUREMENT_SIGNAL if title_associated and (
                        tech_event_signal.signal_type == "procurement_activity" or source.source_type == "procurement"
                    )
                    else EvidenceCategory.BUSINESS_SIGNAL_INFERENCE
                )
            else:
                # Evidence-relevance hardening, follow-up pass (Area 3): a
                # non-promoted direct-hit mapping whose matched term(s) sit
                # in customer/third-party-requirement language is labeled
                # accordingly rather than as a generic incidental mention --
                # the epistemic rule requires this evidence be classified
                # as non-promotable/contextual, never silently folded into
                # an undifferentiated weak bucket.
                category = (
                    EvidenceCategory.CUSTOMER_REQUIREMENT_CONTEXT
                    if _has_customer_requirement_language_near_terms(text, direct_hits)
                    else EvidenceCategory.INCIDENTAL_MENTION
                )
            cluster_id = (
                _cluster_id(source.url, grounding_signal.signal_type, str(grounding_signal.match_start))
                if grounding_signal
                else _cluster_id(source.url, capability["id"], "existing_capability" if existing_capability_only else "direct")
            )
            out.append(
                CapabilityMapping(
                    capability_id=capability["id"],
                    capability_name=capability["name"],
                    evidence_level=level,
                    matched_terms=direct_hits,
                    source_url=source.url,
                    source_title=source.title,
                    confidence=confidence,
                    supporting_context=_first_match_context(text, direct_hits),
                    business_impact=capability["why"],
                    technology_requirement=capability["capability"],
                    signal_type=grounding_signal.signal_type if grounding_signal else None,
                    incumbent_context=incumbent_terms,
                    evidence_category=category.value,
                    inference_cluster_id=cluster_id,
                    epistemic_status=epistemic_status_for(level).value,
                )
            )
            continue

        # --- Rule 4: business-signal-backed generic trigger -> strong
        # inference, but ONLY when:
        #   (a) the generic trigger term itself sits near a mention of the
        #       target organization (unchanged from Phase 1C-H3), AND
        #   (b) a genuinely QUALIFYING signal (Phase 2 hardening item 2: a
        #       narrower set than Rule 2/3's -- "partnership", "investment"
        #       and "contract_award" are explicitly excluded here, since
        #       none of them describe a technology-relevant event on their
        #       own) was extracted from this source, AND
        #   (c) that signal's own match is LOCALLY ASSOCIATED with the
        #       matched trigger term (Phase 2 hardening item 1) -- same
        #       sentence or immediately adjacent, not merely "somewhere
        #       else in the same source."
        #
        # (b) and (c) together are the fix for the reported over-inference
        # pattern: a generic trigger (e.g. "acquisition", shared by every
        # capability in the catalogue) co-occurring with an UNRELATED
        # signal elsewhere in the article (e.g. "partnership" in an
        # earlier, disconnected paragraph) can no longer promote every
        # capability that happens to list that trigger to strong_inference.
        # A genuine, locally-described event (e.g. "Org X is opening a new
        # facility. This expansion will require additional network
        # capacity...") still qualifies exactly as before -- the point is
        # to require the evidence to actually be ABOUT the trigger, not to
        # reduce how many legitimate multi-capability inferences a real
        # event can produce.
        # Evidence-relevance hardening (recommendation #5): a customer-
        # facing retail/product/pricing page is excluded from this rule
        # entirely -- see the note above Rule 2/3 for why.
        #
        # Evidence-relevance hardening, follow-up pass (Area 4 -- generic
        # capacity-expansion semantic ambiguity): an AMBIGUOUS generic
        # trigger (a phrase built around the word "capacity" -- see
        # `_generic_trigger_is_ambiguous`) cannot, on its own, ground
        # promotion for a capability -- "capacity expansion" is equally
        # consistent with manufacturing, renewable-generation, logistics,
        # or IT/data-centre capacity, and only the last of those is
        # actually relevant to this capability. `_usable_generic_hits`
        # keeps every non-ambiguous trigger unchanged and keeps an
        # ambiguous one only when this capability's OWN keyword/weak-signal
        # vocabulary is locally associated with it -- i.e. the surrounding
        # text actually uses IT/infrastructure-specific language, not a
        # hardcoded industry exclusion. `matched_terms` below still records
        # the FULL `generic_hits` for transparency; only the promotion
        # decision is narrowed to the usable subset.
        usable_generic_hits = _usable_generic_hits(text, generic_hits, capability)
        generic_org_associated = _is_organization_associated(
            text, usable_generic_hits, organization_name, radius=0, country=country
        )
        inference_signal = next(
            (
                s for s in signals
                if s.signal_type in _INFERENCE_QUALIFYING_SIGNAL_TYPES
                and _signal_locally_associated_with_trigger(text, usable_generic_hits, s)
                and _correlating_signal_has_tech_context(text, s, capability, exclude_terms=usable_generic_hits)
            ),
            None,
        )
        if (
            usable_generic_hits and inference_signal is not None and trust_tier in ("high", "medium")
            and generic_org_associated and not is_customer_product_page
        ):
            level = EvidenceLevel.STRONG_INFERENCE
            confidence = _confidence_for(
                level, trust_tier=trust_tier, direct_count=0, weak_or_generic_count=len(generic_hits), signal_backed=True
            )
            out.append(
                CapabilityMapping(
                    capability_id=capability["id"],
                    capability_name=capability["name"],
                    evidence_level=level,
                    matched_terms=generic_hits,
                    source_url=source.url,
                    source_title=source.title,
                    confidence=confidence,
                    supporting_context=inference_signal.text or _first_match_context(text, generic_hits),
                    business_impact=capability["why"],
                    technology_requirement=capability["capability"],
                    signal_type=inference_signal.signal_type,
                    incumbent_context=incumbent_terms,
                    evidence_category=EvidenceCategory.BUSINESS_SIGNAL_INFERENCE.value,
                    inference_cluster_id=_cluster_id(source.url, inference_signal.signal_type, str(inference_signal.match_start)),
                    epistemic_status=epistemic_status_for(level).value,
                )
            )
            continue

        # --- Rule 5: everything else that matched something (a bare weak
        # single-word term, or a generic trigger with no corroborating
        # signal) is weak relevance -- retained as evidence, never
        # presented as a sales-ready requirement.
        #
        # Evidence-relevance hardening, follow-up pass (Area 3): as in
        # Rule 2/3's non-promoted branch, a fallthrough mapping whose
        # matched term(s) sit in customer/third-party-requirement language
        # is labeled CUSTOMER_REQUIREMENT_CONTEXT instead of the generic
        # INCIDENTAL_MENTION -- the epistemic rule requires evidence that
        # cannot establish the organization's OWN requirement to be
        # classified as such, not silently folded into an undifferentiated
        # weak bucket.
        matched = weak_hits + generic_hits
        if matched:
            level = EvidenceLevel.WEAK_RELEVANCE
            confidence = _confidence_for(
                level, trust_tier=trust_tier, direct_count=0, weak_or_generic_count=len(matched), signal_backed=False
            )
            category = (
                EvidenceCategory.CUSTOMER_REQUIREMENT_CONTEXT
                if _has_customer_requirement_language_near_terms(text, matched)
                else EvidenceCategory.INCIDENTAL_MENTION
            )
            out.append(
                CapabilityMapping(
                    capability_id=capability["id"],
                    capability_name=capability["name"],
                    evidence_level=level,
                    matched_terms=matched,
                    source_url=source.url,
                    source_title=source.title,
                    confidence=confidence,
                    supporting_context=_first_match_context(text, matched),
                    business_impact=capability["why"],
                    technology_requirement=capability["capability"],
                    signal_type=None,
                    incumbent_context=incumbent_terms,
                    evidence_category=category.value,
                    inference_cluster_id=_cluster_id(source.url, capability["id"], "weak"),
                    epistemic_status=epistemic_status_for(level).value,
                )
            )

    # --- Final hard cap (item 1/2, Phase 1C-H3.1; extended by evidence-
    # relevance hardening recommendation #6): personal LinkedIn profile
    # evidence -- and now generic executive-biography/interview evidence --
    # can never leave this function above weak_relevance, full stop. This
    # is deliberately a clamp over the completed output, not another
    # conditional woven into the rules above -- it runs AFTER every
    # promotion path (organization association, technology-event signals,
    # multi-term co-occurrence, source trust) has already had its say, so
    # none of them can produce a direct_evidence/strong_inference mapping
    # for a profile/bio source that survives to the caller, regardless of
    # how that mapping was reached. The `is_linkedin_profile`/
    # `is_executive_bio_source` checks above already prevent Rule 2/3/4
    # from running for such sources (matched_terms/level get set correctly
    # the first time), so in practice this loop is a no-op confirming that
    # invariant -- its value is as a structural guarantee against a future
    # rule change accidentally reopening the bypass, not as the primary
    # mechanism. Nothing is dropped here (item 4): only evidence_level/
    # confidence are clamped, every matched term/source/context is
    # preserved as-is.
    if is_linkedin_profile or is_executive_bio_source:
        out = [
            m if m.evidence_level == EvidenceLevel.WEAK_RELEVANCE else _dataclass_replace(
                m, evidence_level=EvidenceLevel.WEAK_RELEVANCE, confidence=min(m.confidence, 35.0),
                epistemic_status=epistemic_status_for(EvidenceLevel.WEAK_RELEVANCE).value,
            )
            for m in out
        ]

    return out


def map_all_sources(
    sources: list[Source], *, organization_name: str | None = None, country: str | None = None
) -> list[CapabilityMapping]:
    out: list[CapabilityMapping] = []
    for s in sources:
        out.extend(map_source_to_capabilities(s, organization_name=organization_name, country=country))
    return out


def summarize_by_capability(mappings: list[CapabilityMapping]) -> dict:
    """Group mappings by capability, separated by evidence level -- a
    consumer of this output (the future AI layer, or a UI) can see at a
    glance how much DIRECT evidence exists vs. inference vs. weak noise,
    rather than one collapsed opportunity score.
    """
    by_capability: dict[str, dict] = {}
    for m in mappings:
        bucket = by_capability.setdefault(
            m.capability_id,
            {"capability_name": m.capability_name, "direct_evidence": [], "strong_inference": [], "weak_relevance": []},
        )
        bucket[m.evidence_level.value].append(m.to_dict())
    return by_capability

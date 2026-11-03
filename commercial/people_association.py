"""People organization-association engine (identity & association hardening).

Decides whether a LinkedIn search result shows that a person CURRENTLY works
for the target organization. Deliberately evidence-zoned and conservative:

  Zone A  the result title        "Name - Headline - Company | LinkedIn"
  Zone B  the primary snippet     profile text: "Headline: ...", "Location: ...",
                                  an "Experience" list with dates, a profile
                                  card, or plain prose
  Zone C  supplemental text       Brave extra_snippets, other results' snippets
                                  for the same URL, related-profile blocks

Only zones A and B may establish employment, or supply the person's title,
seniority and functions. Zone C is retained for context and discovery only.

Each employment CLAIM carries its provenance (which field it came from), the
entity class it matched, and whether it is current or historical:

  entity class  target      the full target organization name
                bare_brand  only part of it ("<Brand>" for "<Brand> <Country>")
                sibling     same leading brand, different entity ("<Brand> Group")
                other       an unrelated company
  temporal      current | historical (ex-/former/past/was/until/ended range)
  provenance    structured: title company segment, dated Experience entry,
                            Experience field entry, profile card
                free text:  "<role> at <company>" in a headline / statement

Outcome (status):
  confirmed_current   an explicit current claim for the TARGET entity and no
                      conflicting evidence. A conflicting displayed location
                      additionally requires STRUCTURED evidence.
  needs_verification  bare-brand claims; conflicting current claims; a target
                      claim contradicted by dated experience or by location
                      without structured proof.
  rejected            nothing current for the target (historical only, a
                      sibling/other company, unrelated mention, no evidence).

No organization, sector or country name appears in this module: the target
and its country are plain data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CONFIRMED_CURRENT = "confirmed_current"
NEEDS_VERIFICATION = "needs_verification"
REJECTED = "rejected"

# Stored Person.organization_association values.
LEVEL_CONFIRMED = "confirmed"            # == confirmed_current
LEVEL_NEEDS_VERIFICATION = "needs_verification"
LEVEL_UNCONFIRMED = "unconfirmed"        # rejected

_STATUS_TO_LEVEL = {CONFIRMED_CURRENT: LEVEL_CONFIRMED, NEEDS_VERIFICATION: LEVEL_NEEDS_VERIFICATION,
                    REJECTED: LEVEL_UNCONFIRMED}

# entity classes
TARGET, BARE_BRAND, SIBLING, OTHER = "target", "bare_brand", "sibling", "other"
# provenance
P_TITLE_COMPANY = "title company segment"
P_EXPERIENCE = "Experience field"
P_EXPERIENCE_LIST = "Experience section"
P_PROFILE_CARD = "profile card"
P_HEADLINE_FIELD = "Headline field"
P_TITLE_ROLE_AT = "title headline"
P_SNIPPET_ROLE_AT = "snippet statement"
P_FIRST_PERSON_JOIN = "first-person join post"   # a claimed employment EVENT, never confirmation on its own
_STRUCTURED = {P_TITLE_COMPANY, P_EXPERIENCE, P_EXPERIENCE_LIST, P_PROFILE_CARD}
# Explicit current-role evidence that can outweigh ended dated entries; a bare profile card cannot.
_RESOLVES_ENDED = {P_TITLE_COMPANY, P_EXPERIENCE, P_EXPERIENCE_LIST}
# a competing CURRENT employer in any of these contradicts a target claim
_SELF_DESCRIBED = _STRUCTURED | {P_HEADLINE_FIELD, P_TITLE_ROLE_AT}

# Legal-form / filler words only. "group" is deliberately NOT here: it is what
# distinguishes e.g. "<Brand> Group" from "<Brand> <Country>".
_STOPWORDS = {"the", "of", "and", "for", "co", "company", "ltd", "llc", "saog", "saoc", "sa", "plc", "inc",
              "limited", "pjsc", "psc", "fze", "fzco"}

_TOKEN_RE = re.compile(r"[a-z0-9؀-ۿ]+")


def _norm_token(t: str) -> str:
    # light plural normalisation so "Airports" == "Airport" (but "Air" != "Airport")
    return t[:-1] if len(t) > 4 and t.endswith("s") else t


def _tokens(text: str) -> list[str]:
    return [_norm_token(t) for t in _TOKEN_RE.findall((text or "").lower())
            if t not in _STOPWORDS and len(t) >= 2]


@dataclass
class Claim:
    mention: str
    entity: str            # TARGET | BARE_BRAND | SIBLING | OTHER
    temporal: str          # "current" | "historical"
    provenance: str
    clause: str = ""
    role: str = ""         # role text of a dated Experience entry ("<role> at <company> (dates)")

    @property
    def structured(self) -> bool:
        return self.provenance in _STRUCTURED

    def describe(self) -> str:
        return f"{self.provenance}: '{self.mention.strip()}' ({self.entity}, {self.temporal})"

    def evidence_line(self) -> str:
        label = {P_TITLE_COMPANY: "current employer (LinkedIn profile title)",
                 P_EXPERIENCE: "current employer (LinkedIn Experience field)",
                 P_EXPERIENCE_LIST: "current employer (dated Experience entry)",
                 P_PROFILE_CARD: "current employer (profile card)"}.get(
                     self.provenance, f"current-employer statement ({self.provenance})")
        return f"{label}: '{self.mention.strip()}'"


@dataclass
class Assessment:
    status: str = REJECTED
    reasons: list[str] = field(default_factory=list)       # why this status
    evidence: list[str] = field(default_factory=list)       # supporting claims (provenance)
    claims: list[Claim] = field(default_factory=list)
    location: str | None = None
    location_conflict: bool = False
    basis_text: str = ""                                     # text allowed to drive title/seniority/function

    @property
    def level(self) -> str:
        return _STATUS_TO_LEVEL[self.status]


# ---------------------------------------------------------------------------
# Entity matching
# ---------------------------------------------------------------------------

def classify_entity(mention: str, organization_name: str) -> str:
    """How a company mention relates to the target organization."""
    target = _tokens(organization_name)
    words = _tokens(mention)
    if not target or not words:
        return OTHER
    n = len(target)
    for i in range(len(words) - n + 1):
        if words[i:i + n] == target:
            return TARGET
    if words[0] == target[0]:
        # begins with the target's leading (brand) token: part of the name, or a
        # different entity of the same brand
        if set(words) <= set(target):
            return BARE_BRAND
        return SIBLING
    return OTHER


# ---------------------------------------------------------------------------
# Text structure helpers
# ---------------------------------------------------------------------------

_LINKEDIN_SUFFIX_RE = re.compile(r"\s*[|\-–—]\s*linkedin\b.*$", re.I)
_DASH_RE = re.compile(r"\s+[-–—]\s+")
_CLAUSE_SPLIT_RE = re.compile(r"[.|;\n·]")
_BIDI_RE = re.compile(r"[‎‏‪-‮⁦-⁩]")
_RELATED_RE = re.compile(
    r"\b(?:people also viewed|others also viewed|similar profiles?|related profiles?|people you may know|"
    r"more profiles|view (?:similar|more) profiles|profiles you may like|also viewed)\b.*$", re.I | re.S)
_TRUNCATED_RE = re.compile(r"(?:\.\.\.|…)\s*$")

_HISTORICAL_RE = re.compile(
    r"(?:\b(?:ex|former|formerly|previous|previously|past|prior|retired|alumnus|alumna|alumni|alumnae|"
    r"worked|was|were|until|till|left|resigned|spent|used to|earlier)\b|\bex-)", re.I)
_RANGE_RE = re.compile(
    r"(?P<start>(?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2})\s*(?:-|–|—|to)\s*"
    r"(?P<end>present|current|now|today|ongoing|(?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2})", re.I)
_PRESENT = {"present", "current", "now", "today", "ongoing"}

_ROLE_AT_RE = re.compile(
    r"(?:(?:\bat\b|@)|\b(?:works?|working|employed|serving|serves)\s+(?:for|with|by)\b|\bjoined\b)\s+"
    r"(?P<co>[^.|;,·\n()]{2,80})", re.I)
_CO_CUT_RE = re.compile(r"\s+(?:since|from|in|as|where|which|and\s+(?:previously|formerly)|-\s)\b.*$", re.I)
_ELLIDED_CO_RE = re.compile(r"^[\s.…]*$")
_MAX_COMPANY_WORDS = 8     # longer "company" text is prose, not a company name
_MAX_STATEMENT_LEN = 200     # free-text "<role> at <company>" must be a short statement, not a post/paragraph

_EXP_FIELD_RE = re.compile(
    r"\bexperience\s*:\s*(?P<body>.*?)(?=\b(?:education|location|skills|connections|about|linkedin)\s*:|\n|\.\s+[A-Z]|$)",
    re.I | re.S)
_EXP_SECTION_RE = re.compile(r"#{1,3}\s*experience\b(?P<body>.*?)(?=#{1,3}\s|\[\.\.\.\]|$)", re.I | re.S)
_DATED_ENTRY_RE = re.compile(r"\b(?:at|@)\s+(?P<co>[^()]{2,100}?)\s*\((?P<dates>[^)]*\b(?:19|20)\d{2}[^)]*)\)", re.I)
_HEADLINE_FIELD_RE = re.compile(
    r"\bheadline\s*:\s*(?P<h>.*?)(?=\bcurrent role\s*:|\bseniority\s*:|\blocation\s*:|\bexperience\s*:|"
    r"\blinkedin\s*:|\n|$)", re.I | re.S)
_CARD_RE = re.compile(r"#\s*(?P<head>[^#]{2,240}?)\s{2,}(?:\d[\d,]*\+?\s+(?:connections|followers))", re.I)
_LOC_FIELD_RE = re.compile(r"\blocation\s*:\s*(?P<loc>[^·|\n]*?)(?=\s+(?:experience|linkedin|contact|other|current|"
                           r"seniority|headline)\b\s*:|\s{3,}|·|\||\n|$)", re.I)
_LOC_LEAD_RE = re.compile(r"^\s*(?P<loc>[A-Z][\w '\-]+(?:,\s*[A-Z][\w '\-]+)+)\s*(?:[.·|]|$)")
_NOT_A_LOCATION_RE = re.compile(
    r"\b(?:at|of|for|and|the|manager|director|engineer|officer|head|lead|architect|analyst|consultant|"
    r"specialist|president|chief|executive|advisor|administrator)\b|\d", re.I)
_DURATION_ONLY_RE = re.compile(r"^\s*(?:\d+\s*(?:yrs?|years?|mos?|months?)\s*)+$", re.I)
_DURATION_RE = re.compile(r"^\s*\d+\s*(?:yrs?|years?|mos?|months?)\b", re.I)
_LOCATION_CHAIN_RE = re.compile(r"[A-Z][\w'ţÀ-ɏ]+,\s")

_FOREIGN_SUBJECT_RE = re.compile(
    r"^\s*(?P<name>[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){1,3})\s+"
    r"(?:is|was|has been|serves|served|joined|works|leads|heads|became|has joined|currently)\b")
_FOREIGN_DASH_RE = re.compile(
    r"^\s*(?P<name>[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){1,3})\s+[-–—]\s+\S")


def _strip_bidi(text: str) -> str:
    return _BIDI_RE.sub("", text or "")


def clean_title(title: str) -> str:
    return _LINKEDIN_SUFFIX_RE.sub("", _strip_bidi(title)).strip()


def title_segments(title: str) -> list[str]:
    return [s.strip() for s in _DASH_RE.split(clean_title(title)) if s.strip()]


def split_blob(text: str) -> tuple[str, str]:
    """For callers holding one string: the part up to and including
    '| LinkedIn' is the title (zone A); the rest is the snippet (zone B)."""
    m = re.search(r"\|\s*linkedin\b", text or "", re.I)
    if not m:
        return "", text or ""
    return text[:m.end()], text[m.end():]


def _name_key(name: str) -> set[str]:
    return set(_tokens(_strip_bidi(name)))


_LEAD_PREFIX_RE = re.compile(r"^[\s\-\u2022*\u2013\u2014]*(?:(?:reposted|re-posted|shared|liked|commented|posted)\s*:?\s*)?", re.I)
_FOREIGN_COLON_RE = re.compile(
    r"^(?P<name>[A-Z][\w'\u2019\-]+(?:\s+[A-Z][\w'\u2019\-]+){1,3})\s*(?::|\u2022)\s*\S")


def _is_foreign_subject(clause: str, candidate_name: str | None) -> bool:
    """True when the clause is led by a DIFFERENT person's name:
    'Name is/was/...', 'Name - ...', 'Name: ...', 'Name \u2022 ...' (also behind a
    bullet or a 'Reposted:' prefix)."""
    if not candidate_name:
        return False
    stripped = _LEAD_PREFIX_RE.sub("", clause, count=1)
    for text, rx in ((clause, _FOREIGN_SUBJECT_RE), (clause, _FOREIGN_DASH_RE),
                     (stripped, _FOREIGN_SUBJECT_RE), (stripped, _FOREIGN_DASH_RE), (stripped, _FOREIGN_COLON_RE)):
        m = rx.match(text)
        if m and not _NOT_A_LOCATION_RE.search(m.group("name")):
            subject = _name_key(m.group("name"))
            mine = _name_key(candidate_name)
            if subject and mine and not (subject & mine):
                return True
    return False


def _clean_company(raw: str) -> str:
    return _CO_CUT_RE.sub("", raw).strip(" ,:-")


def _range_temporal(text: str) -> str | None:
    """'current' / 'historical' from a date range in `text`, or None if none."""
    m = _RANGE_RE.search(text or "")
    if not m:
        return None
    return "current" if m.group("end").lower() in _PRESENT else "historical"


def _marker_temporal(text: str) -> str:
    return "historical" if _HISTORICAL_RE.search(text or "") else "current"


def _temporal(clause: str) -> str:
    return _range_temporal(clause) or _marker_temporal(clause)


def _match_temporal(clause: str, m: re.Match) -> str:
    """Temporal state of ONE role-at match inside a clause: a date range right
    after (or inside) the company decides; otherwise historical markers in the
    same sub-phrase BEFORE the match; otherwise current."""
    window = clause[m.start("co"): min(len(clause), m.end("co") + 60)]
    by_range = _range_temporal(window)
    if by_range:
        return by_range
    head = clause[:m.start()]
    cut = max(head.rfind(" - "), head.rfind(" – "), head.rfind(" — "))
    return _marker_temporal(head[cut + 3:] if cut >= 0 else head)


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

def extract_location(snippet: str) -> str | None:
    m = _LOC_FIELD_RE.search(snippet or "")
    if m and m.group("loc").strip():
        return m.group("loc").strip(" .")
    m = _LOC_LEAD_RE.match(snippet or "")
    if m and not _NOT_A_LOCATION_RE.search(m.group("loc")):
        return m.group("loc").strip(" .")
    return None


def location_conflicts(location: str | None, organization_country: str | None) -> bool:
    """A displayed location conflicts only when it names >= 2 comma-separated
    parts and none of them is the organization's country. A bare city or
    region, or no country on file, is never treated as a conflict."""
    if not location or not organization_country:
        return False
    parts = [p.strip().lower() for p in location.split(",") if p.strip()]
    if len(parts) < 2:
        return False
    country = organization_country.strip().lower()
    return not any(country == p or country in p for p in parts)


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

def _role_at_claims(text: str, organization_name: str, provenance: str, *, whole_text_temporal: str | None = None,
                    skip_join: bool = False) -> list[Claim]:
    claims: list[Claim] = []
    if len(text) > _MAX_STATEMENT_LEN:
        return claims
    for m in _ROLE_AT_RE.finditer(text):
        if skip_join and m.group(0).lstrip().lower().startswith("joined"):
            continue    # handled as a first-person join EVENT, not a statement of present employment
        co = _clean_company(m.group("co"))
        if not co or _ELLIDED_CO_RE.match(co) or len(co.split()) > _MAX_COMPANY_WORDS:
            continue   # empty / elided ("at ...") / a sentence rather than a company name
        temporal = whole_text_temporal or _match_temporal(text, m)
        claims.append(Claim(co, classify_entity(co, organization_name), temporal, provenance, text))
    return claims


def _claims_from_title(title: str, organization_name: str) -> tuple[list[Claim], str | None]:
    segs = title_segments(title)
    claims: list[Claim] = []
    name = segs[0] if len(segs) >= 2 else None
    for seg in segs[1:]:
        claims.extend(_role_at_claims(seg, organization_name, P_TITLE_ROLE_AT))
    if len(segs) >= 3:
        last = segs[-1]
        if not _ROLE_AT_RE.search(last) and not _TRUNCATED_RE.search(last):
            claims.append(Claim(last, classify_entity(last, organization_name), "current", P_TITLE_COMPANY, last))
    elif len(segs) == 2 and not _ROLE_AT_RE.search(segs[1]) and not _TRUNCATED_RE.search(segs[1]):
        ent = classify_entity(segs[1], organization_name)
        if ent in (TARGET, BARE_BRAND, SIBLING):
            claims.append(Claim(segs[1], ent, _temporal(segs[1]), P_TITLE_ROLE_AT, segs[1]))
    return claims, name


def _experience_field_claims(body: str, organization_name: str) -> list[Claim]:
    """'Experience: <company> · <dates>' -- only the FIRST entry is the current
    position; a bare tenure ('11 years 4 months') is not an employer."""
    parts = [p.strip() for p in re.split(r"·", body) if p.strip()]
    if not parts or _DURATION_ONLY_RE.match(parts[0]):
        return []
    extent = [parts[0]]
    for p in parts[1:]:
        if _RANGE_RE.search(p) or _DURATION_RE.match(p):
            extent.append(p)
        else:
            break
    entry = " ".join(extent)
    company = _clean_company(_RANGE_RE.sub("", parts[0]).strip(" ,-"))
    if not company:
        return []
    return [Claim(company, classify_entity(company, organization_name), _temporal(entry), P_EXPERIENCE, entry)]


def _experience_section_claims(body: str, organization_name: str) -> list[Claim]:
    """Dated list entries: '<role> at <company> (<start> to <end|present>)'."""
    out: list[Claim] = []
    prev_end = 0
    for m in _DATED_ENTRY_RE.finditer(body):
        temporal = _range_temporal(m.group("dates"))
        co = _clean_company(m.group("co"))
        role = body[prev_end:m.start()].strip(" -\n\t")
        prev_end = m.end()
        if temporal and co:
            out.append(Claim(co, classify_entity(co, organization_name), temporal, P_EXPERIENCE_LIST,
                             f"{co} ({m.group('dates').strip()})", role=role))
    return out


def _card_claims(snippet: str, candidate_name: str | None, organization_name: str) -> list[Claim]:
    """'# Name <company or headline> <location> N connections' profile card."""
    m = _CARD_RE.search(snippet or "")
    if not m:
        return []
    head = _strip_bidi(m.group("head")).strip()
    if candidate_name:
        nm = re.escape(_strip_bidi(candidate_name).strip())
        head = re.sub(rf"^{nm}\s*", "", head, flags=re.I).strip()
    loc = _LOCATION_CHAIN_RE.search(head)
    if loc:
        head = head[:loc.start()].strip()
    if not head:
        return []
    inner = _role_at_claims(head, organization_name, P_PROFILE_CARD)
    if inner:
        return inner
    return [Claim(head, classify_entity(head, organization_name), "current", P_PROFILE_CARD, head)]


_NOISE_RE = re.compile(
    r"congratulat|\bwelcom|\b(?:we(?:[\u2019']re| are)|is|are|now)\s+hiring\b|\bhiring\s+(?:a|an|for|now)\b|"
    r"\bjoin\s+(?:us|our)\b|\bapply\s+(?:now|today|here)\b|\bvacanc|\bjob\s+opening|\bnow\s+recruiting|"
    r"\blooking\s+(?:to|for)\b|\bdream\b|\b(?:hope|want|wish|aim|plan|aspire)s?\s+to\b|\baspir|"
    r"\b(?:would|\u2019d|'d)\s+love\b|\bwho\s+(?:has\s+|have\s+)?joined\b|\bnew\s+colleague\b|"
    r"\bon\s+your\s+new\b|\breposted\s+from\b|\bshared\s+by\b|\bthank", re.I)
_PRONOUN_LEAD_RE = re.compile(r"^\W*(?:he|she|they|his|her|their|him|them)\b", re.I)
_FIRST_PERSON_RE = re.compile(r"\bI\b")
_JOIN_RE = re.compile(r"\b(?:joined|joining|join)\s+(?P<co>[^.|;,\u00b7\n()]{2,80})", re.I)

_OK, _DROP, _PRONOUN = "ok", "drop", "pronoun"


def _attribution(clause: str) -> str:
    """Can this free-text clause be attributed to the candidate as a statement
    of their own present employment?  DROP: congratulations, hiring ads,
    welcomes, reposts, aspirations and similar are not employment evidence.
    PRONOUN: led by a bare third-person pronoun with no explicit antecedent."""
    stripped = _LEAD_PREFIX_RE.sub("", clause, count=1)
    if _NOISE_RE.search(clause):
        return _DROP
    if _PRONOUN_LEAD_RE.match(stripped):
        return _PRONOUN
    return _OK


def _first_person_join_claims(clause: str, organization_name: str) -> list[Claim]:
    """'I've joined <company> ...': a claimed employment event, undated."""
    if not _FIRST_PERSON_RE.search(clause):
        return []
    out: list[Claim] = []
    for m in _JOIN_RE.finditer(clause):
        if _marker_temporal(clause[:m.start()]) == "historical":
            continue
        co = _clean_company(m.group("co"))
        if not co or _ELLIDED_CO_RE.match(co) or len(co.split()) > _MAX_COMPANY_WORDS:
            continue
        out.append(Claim(co, classify_entity(co, organization_name), "current", P_FIRST_PERSON_JOIN, clause))
    return out


def usable_snippet_parts(snippet: str, candidate_name: str | None, organization_name: str
                         ) -> tuple[list[Claim], list[str]]:
    """Zone-B claims and the residual clauses (used for classification).
    Related-profile tails and clauses whose subject is a DIFFERENT person are
    dropped; structured profile fields are parsed before free text."""
    text = _strip_bidi(_RELATED_RE.sub("", snippet or ""))
    claims: list[Claim] = []

    claims.extend(_card_claims(text, candidate_name, organization_name))

    for sec in _EXP_SECTION_RE.finditer(text):
        claims.extend(_experience_section_claims(sec.group("body"), organization_name))
    text = _EXP_SECTION_RE.sub(" ", text)

    for h in _HEADLINE_FIELD_RE.finditer(text):
        for clause in re.split(r"[|;]", h.group("h")):
            claims.extend(_role_at_claims(clause.strip(), organization_name, P_HEADLINE_FIELD))

    for f in _EXP_FIELD_RE.finditer(text):
        claims.extend(_experience_field_claims(f.group("body"), organization_name))
    text = _EXP_FIELD_RE.sub(" ", text)

    clauses: list[str] = []
    for clause in _CLAUSE_SPLIT_RE.split(text):
        clause = clause.strip()
        if not clause or _is_foreign_subject(clause, candidate_name):
            continue
        verdict = _attribution(clause)
        if verdict == _DROP:
            continue
        if verdict == _PRONOUN:
            # a bare "He/She/They ..." has no explicit candidate attribution: it cannot establish
            # employment (and a role/join statement in it is dropped from classification too)
            if not (_ROLE_AT_RE.search(clause) or _JOIN_RE.search(clause)):
                clauses.append(clause)
            continue
        clauses.append(clause)
        if not _HEADLINE_FIELD_RE.search(clause):
            events = _first_person_join_claims(clause, organization_name)
            claims.extend(events)
            claims.extend(_role_at_claims(clause, organization_name, P_SNIPPET_ROLE_AT, skip_join=bool(events)))
    return claims, clauses


# ---------------------------------------------------------------------------
# Classification evidence (what may drive title / seniority / function / technology)
# ---------------------------------------------------------------------------

_FIELD_LINE_RE = re.compile(r"^\s*(?:headline|current\s+role|seniority)\s*:", re.I)
_SENIORITY_FIELD_RE = re.compile(
    r"\bseniority\s*:\s*(?P<s>.*?)(?=\blocation\s*:|\bexperience\s*:|\blinkedin\s*:|\bheadline\s*:|"
    r"\bcurrent\s+role\s*:|\n|$)", re.I | re.S)
_AS_ROLE_RE = re.compile(r"\bas\s+(?:an?\s+|the\s+)?(?P<r>[^,;()]{2,80})", re.I)


def structured_role_fields(snippet: str, candidate_name: str | None) -> list[str]:
    """The candidate's own `Headline:` (the WHOLE field, pipe-separated segments
    included), `Current role:` and `Seniority:` fields. Related-profile tails and
    lines led by another person's name are excluded; a historical-looking field is skipped."""
    out: list[str] = []
    for passage in _own_passages(snippet, candidate_name):
        for rx, grp in ((_HEADLINE_FIELD_RE, "h"), (_CURRENT_ROLE_FIELD_RE, "r"), (_SENIORITY_FIELD_RE, "s")):
            for m in rx.finditer(passage):
                text = re.sub(r"\s+", " ", m.group(grp)).strip(" -\u2013\u2014|,:;")
                if text and _marker_temporal(text) != "historical":
                    out.append(text)
    return out


def own_role_phrases(clause: str, organization_name: str) -> list[str]:
    """The parts of one residual clause that may classify the candidate: the ROLE
    phrase of the candidate's own current-employer statement at the target
    ("<role> at <target>", "... at <target> as <role>", "joined <target> as <role>").
    Structured field lines are handled by structured_role_fields; everything else
    (About text, posts, other employers' roles, other people) is not classified."""
    if _FIELD_LINE_RE.match(clause):
        return []
    out: list[str] = []
    for rx in (_ROLE_AT_RE, _JOIN_RE):
        for m in rx.finditer(clause):
            co = _clean_company(m.group("co"))
            if not co or classify_entity(co, organization_name) != TARGET:
                continue
            if rx is _ROLE_AT_RE and _match_temporal(clause, m) != "current":
                continue
            out.append(re.split(r"[!?]", clause[:m.start()])[-1])
            after = clause[m.start("co") + clause[m.start("co"):].find(co) + len(co):]
            am = _AS_ROLE_RE.match(after.lstrip())
            if am:
                out.append(am.group("r"))
    return [o for o in out if o.strip()]


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------

def assess_employment(
    title: str,
    snippet: str,
    organization_name: str,
    *,
    organization_country: str | None = None,
    candidate_name: str | None = None,
) -> Assessment:
    """Assess one result's zones A + B. See the module docstring."""
    return assess_results([(title, snippet)], organization_name,
                          organization_country=organization_country, candidate_name=candidate_name)


def assess_results(
    results: list[tuple[str, str]],
    organization_name: str,
    *,
    organization_country: str | None = None,
    candidate_name: str | None = None,
) -> Assessment:
    """Assess every (title, snippet) pair that belongs to the SAME profile."""
    out = Assessment()
    if not organization_name or not any((t or s) for t, s in results):
        out.reasons.append("no profile text")
        return out

    basis: list[str] = []
    for title, snippet in results:
        t_claims, name = _claims_from_title(title, organization_name)
        who = candidate_name or name
        s_claims, clauses = usable_snippet_parts(snippet, who, organization_name)
        out.claims.extend(t_claims)
        out.claims.extend(s_claims)
        if out.location is None:
            out.location = extract_location(_strip_bidi(snippet))
        segs = title_segments(title)
        if len(segs) >= 3 and not _ROLE_AT_RE.search(segs[-1]) and not _TRUNCATED_RE.search(segs[-1]):
            basis.extend(segs[1:-1])
        else:
            basis.extend(segs[1:] if len(segs) >= 2 else segs)
        # Classification evidence is built ONLY from the candidate's own attributable fields and
        # statements (see classification_phrases); About text, posts, other employers' roles and
        # other people's text are never classified.
        for c in clauses:
            if _LOC_FIELD_RE.search(c) or (out.location and out.location in c):
                continue
            if _temporal(c) == "historical":
                continue
            basis.extend(own_role_phrases(c, organization_name))
        basis.extend(structured_role_fields(snippet, who))
    # The role of a CURRENT dated Experience entry at the target is current-title evidence.
    basis.extend(c.role for c in out.claims
                 if c.provenance == P_EXPERIENCE_LIST and c.temporal == "current" and c.entity == TARGET and c.role)
    out.basis_text = " . ".join(b for b in basis if b)
    out.location_conflict = location_conflicts(out.location, organization_country)

    cur = [c for c in out.claims if c.temporal == "current" and c.provenance != P_FIRST_PERSON_JOIN]
    # An undated first-person "I've joined <org>" post is a claimed employment EVENT: it can
    # corroborate but never independently confirm present employment.
    events = [c for c in out.claims if c.provenance == P_FIRST_PERSON_JOIN and c.temporal == "current"
              and c.entity == TARGET]
    target = [c for c in cur if c.entity == TARGET]
    bare = [c for c in cur if c.entity == BARE_BRAND]
    sibling = [c for c in cur if c.entity == SIBLING]
    competing = [c for c in cur if c.entity == OTHER and c.provenance in _SELF_DESCRIBED]
    ended_structured = [c for c in out.claims if c.entity == TARGET and c.temporal == "historical"
                        and c.provenance == P_EXPERIENCE_LIST]

    if target:
        conflicts = sibling + competing
        if conflicts:
            out.status = NEEDS_VERIFICATION
            out.reasons.append("conflicting current-employer evidence: " + "; ".join(c.describe() for c in conflicts))
            out.evidence.extend(c.evidence_line() for c in target)
        elif ended_structured and not any(c.provenance in _RESOLVES_ENDED for c in target):
            # Only a profile card or free text says "current"; dated Experience says it ended. A newer
            # explicit current role (title company segment, Experience field, current dated entry) would
            # resolve this -- none is present, so the card/free text must not silently override history.
            out.status = NEEDS_VERIFICATION
            source = ("the profile card names the organization as the current employer"
                      if any(c.provenance == P_PROFILE_CARD for c in target)
                      else "a free-text statement claims current employment")
            out.reasons.append(f"{source} but the dated Experience entries show it ended: "
                               + "; ".join(c.describe() for c in target + ended_structured))
            out.evidence.extend(c.evidence_line() for c in target)
        elif out.location_conflict and not any(c.structured for c in target):
            out.status = NEEDS_VERIFICATION
            out.reasons.append(
                f"displayed location '{out.location}' conflicts with the organization's country and the employer "
                "is stated only in free text (no structured current-employer field)")
            out.evidence.extend(c.evidence_line() for c in target)
        else:
            out.status = CONFIRMED_CURRENT
            out.evidence.extend(c.evidence_line() for c in target)
            if out.location_conflict:
                out.reasons.append(f"location '{out.location}' differs from the organization's country; "
                                   "accepted on structured current-employer evidence")
    elif events:
        out.status = NEEDS_VERIFICATION
        out.reasons.append("only an undated first-person post claims joining the organization; that is a claimed "
                           "employment event, not reliable evidence of current employment: "
                           + "; ".join(c.describe() for c in events))
        conflicts = sibling + competing
        if conflicts:
            out.reasons.append("conflicting current-employer evidence: " + "; ".join(c.describe() for c in conflicts))
    elif sibling:
        out.status = REJECTED
        out.reasons.append("current employer is a different entity: " + "; ".join(c.describe() for c in sibling))
    elif bare:
        out.status = NEEDS_VERIFICATION
        out.reasons.append("employer named only by brand / partial name, cannot identify the operating entity: "
                           + "; ".join(c.describe() for c in bare))
        out.evidence.extend(c.evidence_line() for c in bare)
    else:
        hist = [c for c in out.claims if c.entity in (TARGET, BARE_BRAND, SIBLING) and c.temporal == "historical"]
        if hist:
            out.reasons.append("only historical employment at the organization: "
                               + "; ".join(c.describe() for c in hist))
        elif cur:
            out.reasons.append("current employer is an unrelated company: " + "; ".join(c.describe() for c in cur))
        else:
            out.reasons.append("no current-employer statement for the organization in the profile title/snippet")
        out.status = REJECTED
    return out


# ---------------------------------------------------------------------------
# Current-title fallback
# ---------------------------------------------------------------------------

_CURRENT_ROLE_FIELD_RE = re.compile(
    r"\bcurrent\s+role\s*:\s*(?P<r>.*?)(?=\bseniority\s*:|\blocation\s*:|\bexperience\s*:|\bheadline\s*:|"
    r"\blinkedin\s*:|\n|$)", re.I | re.S)
_HEADLINE_ROLE_CO_RE = re.compile(r"^(?P<role>.+?)\s+(?:at|@)\s+(?P<co>[^()|]{2,80})$", re.I | re.S)
_MAX_TITLE_LEN = 200


def _only_organization(segment: str, organization_name: str) -> bool:
    toks = set(_tokens(segment))
    return bool(toks) and toks <= set(_tokens(organization_name))


def title_has_usable_role(title: str, candidate_name: str | None, organization_name: str) -> bool:
    """True when the result title carries a role of its own: a segment that is
    not the person's name, not only the organization's name and not a bare
    LinkedIn/ellipsis stub (a truncated "<role> at ..." still counts)."""
    segs = title_segments(title)
    name_toks = _name_key(candidate_name or "")
    for i, seg in enumerate(segs):
        seg = re.sub(r"\s+(?:at|@)\s*(?:\.\.\.|…)\s*$", "", seg)
        seg = _TRUNCATED_RE.sub("", seg).strip()
        if not seg:
            continue
        if i == 0 and (not name_toks or (_name_key(seg) and _name_key(seg) <= name_toks)):
            continue    # the person's name
        if _only_organization(seg, organization_name):
            continue
        return True
    return False


def _own_passages(snippet: str, candidate_name: str | None) -> list[str]:
    """Passages of the snippet that can describe the candidate: related-profile
    tails removed and any line led by a different person's name dropped."""
    text = _strip_bidi(_RELATED_RE.sub("", snippet or ""))
    out: list[str] = []
    for passage in text.split("[...]"):
        lines = [ln for ln in passage.split("\n") if ln.strip() and not _is_foreign_subject(ln.strip(), candidate_name)]
        if lines:
            out.append("\n".join(lines))
    return out


def fallback_current_title(snippets: list[str], candidate_name: str | None, organization_name: str
                           ) -> tuple[str, str] | None:
    """The candidate's current role when the result title has none.

    Order: an explicit structured `Current role:` field, then the `Headline:`
    field. Both come from the candidate's own primary-snippet passages only
    (never extra/supplemental snippets, related-profile text or another
    person's lines). A headline is used only when it is not historical and its
    employer, if it names one, is the target organization (or its bare brand).
    Conflicting values for the same field are ambiguous and give no title.
    """
    passages = [p for s in snippets for p in _own_passages(s, candidate_name)]
    roles = {re.sub(r"\s+", " ", m.group("r")).strip(" -–—|,:;")
             for p in passages for m in _CURRENT_ROLE_FIELD_RE.finditer(p)}
    roles.discard("")
    if len(roles) == 1:
        role = next(iter(roles))
        if len(role) <= _MAX_TITLE_LEN and not _HISTORICAL_RE.search(role):
            return role, "Current role field"
    if roles:
        return None   # conflicting current-role values: do not guess

    heads: set[str] = set()
    for p in passages:
        for m in _HEADLINE_FIELD_RE.finditer(p):
            head = re.sub(r"\s+", " ", m.group("h")).strip(" -–—|,:;")
            if not head or _HISTORICAL_RE.search(head):
                continue
            rc = _HEADLINE_ROLE_CO_RE.match(head)
            if rc:
                if classify_entity(rc.group("co"), organization_name) not in (TARGET, BARE_BRAND):
                    continue
                head = rc.group("role").strip(" -–—|,:;")
            if head and len(head) <= _MAX_TITLE_LEN:
                heads.add(head)
    if len(heads) == 1:
        return next(iter(heads)), "Headline field"
    return None

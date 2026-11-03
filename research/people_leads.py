"""Discovery LEADS from third-party appointment / leadership announcements.

A snippet such as "<Name> on her appointment as Chief Technology Officer" is
only a pointer: it tells People discovery whom to look for. A lead

  * is NOT a person record and carries no employment claim of any kind;
  * is only ever used to build one profile-restricted search for that
    person's OWN profile at the resolved organisation;
  * whatever that search returns is assessed by the unchanged association
    rules (commercial.people_association) like any other result.

The extractor is generic: it contains no personal names and recognises only
announcement phrasing plus a senior, technology/digital/security/network/
procurement leadership title (judged by the existing title classifier).
Vacancies and hiring adverts, non-technology titles, organisation names and
text that is not a proper name never produce a lead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HONORIFICS = {"dr", "eng", "engr", "prof", "mr", "mrs", "ms", "miss", "sheikh", "sheikha", "he", "h.e"}
# capitalised words that can sit right before a name in a greeting / headline
_LEADING_NOISE = {
    "congratulations", "congratulation", "congrats", "congratulate", "welcome", "welcoming", "dear", "hearty",
    "heartiest", "warm", "warmest", "many", "big", "huge", "special", "best", "proud", "thank", "thanks",
    "our", "new", "introducing", "meet", "announcing", "announce", "please", "join", "the", "to",
}
_PARTICLES = r"(?:bin|bint|ben|ibn|abu|al|el|de|van|von)"
_TOKEN = r"[A-Z][A-Za-z'’]+(?:-[A-Za-z][A-Za-z'’]*)*"
_HON = r"(?:(?:Dr|Eng|Engr|Prof|Mr|Mrs|Ms|Miss|Sheikh|Sheikha|H\.?E)\.?\s+)"
_NAME = rf"(?:{_HON})?{_TOKEN}(?:\s+(?:{_PARTICLES}\s+)?{_TOKEN}){{1,3}}"
_THE = r"(?:(?:the|its|our|new|a)\s+)*"

_PATTERNS = [
    # "<Name> on her appointment as <title>"
    re.compile(rf"(?P<name>{_NAME})\s+(?i:on\s+(?:his|her|their)\s+(?:recent\s+|new\s+)?"
               rf"(?:appointment|promotion|elevation|new role|new position|new post)\s+(?:as|to)\s+{_THE})(?P<rest>.+)"),
    # "the appointment of <Name> as <title>"
    re.compile(rf"(?i:(?:appointment|promotion|elevation)\s+of\s+)(?P<name>{_NAME})\s+(?i:(?:as|to)\s+{_THE})(?P<rest>.+)"),
    # "<Name> has been appointed as <title>"
    re.compile(rf"(?P<name>{_NAME})\s+(?i:(?:has\s+been|was|is)\s+(?:appointed|named|promoted|elevated)\s+(?:as|to)\s+{_THE})(?P<rest>.+)"),
    # "Congratulations to <Name> on his new role as <title>" / "Welcome <Name> as <title>"
    re.compile(rf"(?i:(?:congratulat\w*|congrats|welcom\w*)\s+(?:to\s+)?)(?P<name>{_NAME})\s+"
               rf"(?i:(?:on\s+(?:his|her|their|your)\s+(?:new\s+role|appointment|promotion|new position)\s+as|as|to)\s+{_THE})(?P<rest>.+)"),
]
_TITLE_END = re.compile(r"\s+(?:at|in|with|for|and\s+we|and\s+wish|and\s+the|and\s+look)\b|[.,;:!?|…\n]|\s+[—–-]\s+")
_HIRING = re.compile(r"\b(?:hiring|we\s+are\s+looking|looking\s+for|vacanc|apply|job\s+opening|seeking|recruit|"
                     r"join\s+our\s+team|open\s+position)\b", re.I)
_LEADERSHIP_START = re.compile(r"^(?:chief|head|vice|vp|svp|evp|senior|executive|general|group|director|deputy|acting|c[a-z]o\b)", re.I)


@dataclass(frozen=True)
class Lead:
    """A name worth searching for. Deliberately carries NO employment status."""

    name: str
    title_phrase: str
    key: str


def name_tokens(name: str) -> list[str]:
    """Lower-case name tokens without honorifics and punctuation: 'Dr. A Al-B' == 'A Al B'."""
    words = re.sub(r"[^A-Za-z\s']", " ", name.replace("-", " ")).split()
    return [w.lower().strip("'’") for w in words if w.lower().strip("'’") not in _HONORIFICS and w.strip("'’")]


_HYPHEN_PARTICLES = {"al", "el", "abu"}     # written 'Al-Name' and 'Al Name' interchangeably


def name_spellings(name: str) -> list[str]:
    """The spellings worth one profile search each: the name as written, then (when the
    name has an Al/El/Abu particle) the other hyphen/space form. At most two, no repeats,
    and never a token the original did not contain. Brave's OR syntax is not reliable, so
    callers issue these as separate queries."""
    words = (name or "").split()
    if not words:
        return []
    written = " ".join(words)
    hyphenated = any("-" in w and w.split("-", 1)[0].lower() in _HYPHEN_PARTICLES for w in words)
    out: list[str] = []
    if hyphenated:                                   # 'Al-Name' -> 'Al Name'
        for w in words:
            head, _, tail = w.partition("-")
            out.extend([head, tail] if tail and head.lower() in _HYPHEN_PARTICLES else [w])
    else:                                            # 'Al Name' -> 'Al-Name'
        i = 0
        while i < len(words):
            nxt = words[i + 1] if i + 1 < len(words) else None
            if words[i].lower() in _HYPHEN_PARTICLES and nxt and nxt.lower() not in _HYPHEN_PARTICLES:
                out.append(f"{words[i]}-{nxt}")
                i += 2
            else:
                out.append(words[i])
                i += 1
    alternative = " ".join(out)
    return [written] if alternative.lower() == written.lower() else [written, alternative]


def lead_key(name: str) -> str:
    return " ".join(name_tokens(name))


def same_person_name(a: str, b: str) -> bool:
    """Equal token sets, or one a subset of the other with at least two tokens."""
    ta, tb = set(name_tokens(a)), set(name_tokens(b))
    if not ta or not tb:
        return False
    return ta == tb or (min(len(ta), len(tb)) >= 2 and (ta <= tb or tb <= ta))


def _clean_name(raw: str, organization_tokens: set[str]) -> str | None:
    words = raw.split()
    while words and words[0].lower().strip(".'’") in _LEADING_NOISE:
        words.pop(0)
    while words and words[-1].lower() in _LEADING_NOISE:
        words.pop()
    plain = [w for w in words if w.lower().strip(".") not in _HONORIFICS]
    if len(plain) < 2 or len(plain) > 4:
        return None
    if any(w.lower().strip("'’") in organization_tokens for w in plain):
        return None                                           # an organisation, not a person
    if not all(w[0].isupper() or w.lower() in {"bin", "bint", "ben", "ibn", "abu", "al", "el", "de", "van", "von"}
               for w in plain):
        return None
    return " ".join(plain)                                   # honorifics are not part of the searched name


def _title(rest: str) -> str | None:
    m = _TITLE_END.search(rest)
    title = (rest[:m.start()] if m else rest).strip(" \"'“”‘’")
    title = re.sub(r"\s+", " ", title)
    if not title or len(title) > 80 or not _LEADERSHIP_START.match(title):
        return None
    return title


def _credible_title(title: str) -> bool:
    """Senior (c-level / head / director) AND a technology / digital / security /
    network / data / procurement group -- judged by the existing classifier."""
    from commercial.people import classify_person_text, people_groups  # lazy: avoids an import cycle
    c = classify_person_text(title)
    if c["seniority"] not in {"c_level", "head", "director"}:
        return False
    groups = people_groups({"seniority": c["seniority"], "functions": c["functions"],
                            "technology_areas": c["technology_areas"], "role_id": c["role_id"]})
    return bool(groups) and set(groups) != {"Other"}


def extract_leads(text: str, *, organization: str = "", own_name: str | None = None) -> list[Lead]:
    """Leads (named senior technology executives) announced in `text`.

    `own_name` is the name of the profile the text came from: a person's own
    description is not a third-party mention, so it never yields a lead for
    that same person.
    """
    if not text:
        return []
    org_tokens = {t for t in re.split(r"\W+", organization.lower()) if t}
    out: list[Lead] = []
    seen: set[str] = set()
    for pattern in _PATTERNS:
        for m in pattern.finditer(text):
            window = text[max(0, m.start() - 100): m.end() + 40]
            if _HIRING.search(window):
                continue
            name = _clean_name(m.group("name"), org_tokens)
            title = _title(m.group("rest"))
            if not name or not title or not _credible_title(title):
                continue
            key = lead_key(name)
            if not key or key in seen:
                continue
            if own_name and same_person_name(name, own_name):
                continue
            seen.add(key)
            out.append(Lead(name=name, title_phrase=title, key=key))
    return out

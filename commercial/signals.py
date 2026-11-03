"""Generic customer-signal extraction -- link 1 of the chain:

    Customer Signal -> Business Impact -> Technology Requirement -> Capability

A signal is something a source actually says is happening (an expansion, a
new project, an investment, a partnership, a leadership change, a
technology initiative) independent of whether it maps to any Otech
capability. Extracting signals as their own first-class thing -- rather
than jumping straight from a capability keyword to a capability match -- is
what keeps the chain from being skipped.

Signal types are GENERIC event categories (the same list applies to an
airport operator, a bank, or a manufacturer); no organization, sector or
country name appears in this file.
"""

from __future__ import annotations

import re
from typing import Callable

from commercial.models import Signal
from commercial.text_heuristics import is_low_trust_span, sentence_spans, span_containing
from research.sources import Source

# Generic event-type signal patterns. Each is a (signal_type, regex) pair;
# regexes are deliberately simple keyword/phrase alternations, not full NLP,
# matching the "deterministic, no LLM" constraint for this phase.
_SIGNAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("expansion", re.compile(r"\b(expansion|expanding|new facility|new site|new location|new terminal|new branch|new plant)\b", re.I)),
    ("project", re.compile(r"\b(new project|major project|programme|program|launch|pilot project|rollout)\b", re.I)),
    ("investment", re.compile(r"\b(invest(ment|ing|ed)?|capital expenditure|capex|funding round)\b", re.I)),
    ("partnership", re.compile(r"\b(partnership|strategic alliance|joint venture|collaborat(ion|e|ing))\b", re.I)),
    ("contract_award", re.compile(r"\b(contract award(ed)?|awarded the contract|signs? (a |an )?(deal|contract)|wins? (a |an )?contract)\b", re.I)),
    ("leadership_change", re.compile(r"\b(appoint(s|ed|ment)?|new (ceo|cio|cto|cfo|coo|chief|director|head of)|steps down|joins as)\b", re.I)),
    (
        "technology_initiative",
        re.compile(
            r"\b(digital transformation|technology (upgrade|initiative|programme|program)|moderni[sz]ation (initiative|programme|program|project|plan|drive)|"
            r"rollout of|migration (initiative|programme|program|project|plan)|deploy(s|ed|ing)?|deployment of|"
            r"implement(s|ed|ing)?|implementation of|"
            r"upgrad(e|es|ed|ing)|install(s|ed|ing)?|installation of|commission(s|ed|ing)?|announc(?:ed|es|ing)\s+(?:a\s+)?(?:new\s+)?(?:[\w-]+\s+){0,2}(?:technology|platform|system|solution|service|architecture|infrastructure)|"
            r"renew(s|ed|al)? (its |the |a |an )?(network|infrastructure|system|platform|contract|agreement)|"
            r"go(es|ing)? live|migrat(e|es|ed|ing) to a new)\b",
            re.I,
        ),
    ),
    ("procurement_activity", re.compile(r"\b(tender|rfp|rfq|request for proposal|request for quotation|procurement process|invitation to bid)\b", re.I)),
    ("capacity_change", re.compile(r"\b(capacity (increase|expansion)|scal(e|ing) up|increased capacity|throughput increase)\b", re.I)),
    ("financial_change", re.compile(r"\b(revenue (growth|increase)|profit (growth|increase)|financial results|quarterly results|annual results)\b", re.I)),
]


def extract_signals(
    source: Source, *, max_signals: int = 6, extra_span_filter: Callable[[str], bool] | None = None
) -> list[Signal]:
    """Scan one source's available text (content, else snippet+title) for
    generic event-type language. Purely pattern-based; no organization
    name is ever part of the pattern -- only the source's own text is
    scanned.

    Evidence-relevance hardening (recommendation #3, buzzword/list-like
    text): a signal pattern can match structurally inside a span of text
    that is not actually narrative prose about an organizational event --
    e.g. an infographic/category-label list ("IIOT Analytics Microchip
    Sensors Connectivity Cybersecurity 5G...") or page navigation/
    aggregator chrome ("Read More ... Read More ..."). Such a match does
    not describe a genuine business signal, so it must never be allowed to
    become a promotable Signal. Rather than taking the FIRST regex match
    per type (`pattern.search`), every candidate match is scanned in order
    (`pattern.finditer`) and the first one whose containing span is NOT
    low-trust is used; a signal type with only low-trust-span matches
    yields no Signal at all for this source, same as if it hadn't matched.

    F3 (Golden Case G3, generic): `extra_span_filter`, when given, is an
    additional per-span rejection check applied the same way as the
    built-in `is_low_trust_span` check above -- a candidate match whose
    containing span this callable flags is skipped, same as a low-trust
    span is skipped. Optional and `None` by default, so every existing
    caller (and `extract_all_signals`) is completely unaffected. This
    exists because "first regex match per type" is a source-wide search:
    without it, a signal type's first-in-document occurrence could sit in
    a span that a caller's own evidence-relevance disqualifiers (vendor-
    case-study language, a collective/government-wide claim, and so on)
    would reject as not describing this organization's own activity --
    permanently shadowing a later, genuine occurrence of the SAME signal
    type in the organization's own, undisqualified sentence, which would
    otherwise never be considered at all. The filter itself is supplied by
    the caller (see commercial.evidence_mapping's call site) -- this
    module defines no disqualification rules of its own and remains
    organization- and sector-agnostic.
    """
    text = (source.content or "").strip() or f"{source.title} {source.snippet}".strip()
    if not text:
        return []

    spans = sentence_spans(text)

    out: list[Signal] = []
    seen_types: set[str] = set()
    for signal_type, pattern in _SIGNAL_PATTERNS:
        if signal_type in seen_types:
            continue
        match = None
        for candidate in pattern.finditer(text):
            segment = span_containing(text, spans, candidate.start())
            if is_low_trust_span(segment):
                continue
            if extra_span_filter is not None and extra_span_filter(segment):
                continue
            match = candidate
            break
        if not match:
            continue

        # A generic "programme/program" mention in awards/recognition
        # context is not an organizational project signal.
        if signal_type == "project" and match.group(0).lower() in {"programme", "program"}:
            sentence = span_containing(text, spans, match.start()) or ""
            if re.search(
                r"\b(award|awards|recognition|recognized|recognised|achievement|achievements|accolade)\b",
                sentence,
                re.I,
            ):
                continue
        seen_types.add(signal_type)
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 80)
        context = text[start:end].strip()
        out.append(
            Signal(
                signal_type=signal_type,
                text=context,
                source_url=source.url,
                source_title=source.title,
                snippet=source.snippet,
                matched_terms=[match.group(0)],
                match_start=match.start(),
                match_end=match.end(),
            )
        )
        if len(out) >= max_signals:
            break
    return out


def extract_all_signals(sources: list[Source]) -> list[Signal]:
    out: list[Signal] = []
    for s in sources:
        out.extend(extract_signals(s))
    return out



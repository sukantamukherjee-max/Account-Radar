"""Otech capability catalogue loader -- the fixed commercial anchor (spec §11).

The catalogue itself lives in `commercial/capabilities.json` as DATA, not
code: no organization, sector, or country name appears anywhere in either
file. The same five entries apply identically whether the target
organization turns out to be an airport operator, a bank, or a
manufacturer -- sector context is discovered from evidence, never assumed
from this catalogue.

Per Phase 0 §11 / §G ("do not scatter offering names through business
logic... should eventually come from Otech's portfolio source of truth"),
the catalogue is now externally editable JSON rather than a Python literal
-- someone maintaining Otech's offering list can edit capabilities.json
directly without touching any code. This is a data-loading change only, not
an architecture change: research/ still never imports or references this
module or its content.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

_CATALOG_PATH = Path(__file__).parent / "capabilities.json"
_lock = Lock()
_cached_catalog: list[dict] | None = None


def get_catalog(*, force_reload: bool = False) -> list[dict]:
    """Load (and cache) the capability catalogue from capabilities.json.

    `force_reload=True` re-reads the file -- useful for tests, or an
    operator who just edited the catalogue and wants it picked up without
    restarting the process.
    """
    global _cached_catalog
    with _lock:
        if _cached_catalog is None or force_reload:
            with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
                catalog = json.load(f)
            _validate_catalog(catalog)
            _cached_catalog = catalog
        return _cached_catalog


def _validate_catalog(catalog: list[dict]) -> None:
    required_fields = {"id", "name", "capability", "keywords", "generic_triggers", "why", "who", "conversation", "next_step", "fit"}
    seen_ids = set()
    for entry in catalog:
        missing = required_fields - set(entry.keys())
        if missing:
            raise ValueError(f"capabilities.json entry {entry.get('id', '?')!r} is missing fields: {missing}")
        if entry["id"] in seen_ids:
            raise ValueError(f"capabilities.json has a duplicate id: {entry['id']!r}")
        seen_ids.add(entry["id"])

        # Phase 1C-H2: a term must live in exactly one specificity bucket.
        # `keywords` is read by the evidence-mapping engine as "specific
        # enough to anchor direct_evidence"; `weak_signal_terms` is read as
        # "too generic/ambiguous to ever be more than weak_relevance on its
        # own". A term in BOTH buckets is a catalogue authoring bug that
        # silently lets a generic word (e.g. "network") earn direct-evidence
        # treatment -- this is exactly the false-positive pattern the
        # Phase 1C-H2 hardening pass exists to prevent, so it is enforced
        # here rather than left to code review.
        keyword_set = {k.strip().lower() for k in entry["keywords"]}
        weak_set = {w.strip().lower() for w in entry.get("weak_signal_terms", [])}
        overlap = keyword_set & weak_set
        if overlap:
            raise ValueError(
                f"capabilities.json entry {entry['id']!r} has term(s) in both 'keywords' and "
                f"'weak_signal_terms': {sorted(overlap)} -- move each term to exactly one bucket"
            )

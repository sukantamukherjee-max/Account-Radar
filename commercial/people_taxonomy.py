"""People/stakeholder taxonomy loader.

Mirrors commercial/capabilities.py's pattern exactly: the taxonomy lives in
people_taxonomy.json as DATA, not code -- no organization, sector, or
country name appears anywhere in either file. The same taxonomy applies
identically whether the target organization is an airport operator, a
bank, or a manufacturer.

This is a generic, technology-stakeholder-focused baseline (Phase 1 /
v1) -- roles, functions, and technology areas, not a sector playbook.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

_TAXONOMY_PATH = Path(__file__).parent / "people_taxonomy.json"
_lock = Lock()
_cached_taxonomy: dict | None = None


def get_taxonomy(*, force_reload: bool = False) -> dict:
    """Load (and cache) the people taxonomy from people_taxonomy.json."""
    global _cached_taxonomy
    with _lock:
        if _cached_taxonomy is None or force_reload:
            with open(_TAXONOMY_PATH, "r", encoding="utf-8") as f:
                taxonomy = json.load(f)
            _validate_taxonomy(taxonomy)
            _cached_taxonomy = taxonomy
        return _cached_taxonomy


_REQUIRED_TOP_LEVEL = {"roles", "functions", "technology_areas"}
_ROLE_REQUIRED_FIELDS = {"id", "label", "seniority", "terms"}
_CATEGORY_REQUIRED_FIELDS = {"id", "label", "terms"}
_VALID_SENIORITY = {"c_level", "director", "head", "manager"}


def _validate_taxonomy(taxonomy: dict) -> None:
    missing_top = _REQUIRED_TOP_LEVEL - set(taxonomy.keys())
    if missing_top:
        raise ValueError(f"people_taxonomy.json is missing top-level keys: {missing_top}")

    seen_role_ids: set[str] = set()
    for entry in taxonomy["roles"]:
        missing = _ROLE_REQUIRED_FIELDS - set(entry.keys())
        if missing:
            raise ValueError(f"people_taxonomy.json role entry {entry.get('id', '?')!r} missing fields: {missing}")
        if entry["id"] in seen_role_ids:
            raise ValueError(f"people_taxonomy.json has a duplicate role id: {entry['id']!r}")
        seen_role_ids.add(entry["id"])
        if entry["seniority"] not in _VALID_SENIORITY:
            raise ValueError(
                f"people_taxonomy.json role {entry['id']!r} has invalid seniority {entry['seniority']!r}; "
                f"must be one of {_VALID_SENIORITY}"
            )

    for category_name in ("functions", "technology_areas"):
        seen_ids: set[str] = set()
        for entry in taxonomy[category_name]:
            missing = _CATEGORY_REQUIRED_FIELDS - set(entry.keys())
            if missing:
                raise ValueError(
                    f"people_taxonomy.json {category_name} entry {entry.get('id', '?')!r} missing fields: {missing}"
                )
            if entry["id"] in seen_ids:
                raise ValueError(f"people_taxonomy.json has a duplicate {category_name} id: {entry['id']!r}")
            seen_ids.add(entry["id"])

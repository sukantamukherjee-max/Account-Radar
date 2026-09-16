"""NOT used by the application. Exists only to reproduce, for the Phase
1C-H before/after demonstration, the naive keyword-matching behavior this
hardening pass replaces (equivalent in spirit to app_v1_36_0.py's
_build_sales_radar_base: any keyword hit anywhere, from any source type,
becomes an "opportunity" with no evidence-level distinction).

Do not import this from application code. It is intentionally the "bad"
baseline the acceptance report's before/after section compares against.
"""

from __future__ import annotations

import re

from commercial.capabilities import get_catalog
from research.sources import Source


def naive_keyword_opportunities(sources: list[Source]) -> list[dict]:
    """Old-style behavior: ANY keyword or generic-trigger occurrence,
    anywhere, from any source (including a LinkedIn person's profile
    snippet), becomes an undifferentiated "opportunity" hit. No evidence
    level, no source-type trust distinction, no signal-chain requirement.
    """
    out = []
    for source in sources:
        text = (source.content or "").strip() or f"{source.title} {source.snippet}".strip()
        if not text:
            continue
        low = text.lower()
        for capability in get_catalog():
            all_terms = capability["keywords"] + capability.get("weak_signal_terms", []) + capability["generic_triggers"]
            hits = [t for t in all_terms if re.search(r"\b" + re.escape(t) + r"\b", low)]
            if hits:
                out.append(
                    {
                        "capability_name": capability["name"],
                        "matched_terms": hits,
                        "source_url": source.url,
                        "source_type": source.source_type,
                        "sales_hypothesis": f"Potential {capability['name']} opportunity based on evidence.",
                    }
                )
    return out

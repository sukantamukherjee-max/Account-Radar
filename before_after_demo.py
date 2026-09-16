#!/usr/bin/env python3
"""Phase 1C-H before/after demonstration.

Uses the SAME test organization from Phase 1B's acquisition acceptance run
(Oman Airports Management Company -- a synthetic/mock test fixture, not a
real research result; see PHASE_1B_ACCEPTANCE_REPORT.md's honesty
disclosure). Rather than the thin synthetic MockSearchProvider content
(which has no realistic body text to run capability mapping against), this
script builds a small set of HAND-AUTHORED, CLEARLY-LABELED-SYNTHETIC
evidence sources shaped like what real acquisition would produce for an
organization of this kind, so the before/after difference in mapping
BEHAVIOR (not acquisition behavior, which is unchanged from Phase 1B) is
visible and concrete.

No organization-specific logic exists anywhere in commercial/ -- this
script only supplies organization-flavored TEXT as test input, the same
way any other organization's real research text would flow through the
same code.
"""

from __future__ import annotations

import json

from commercial.evidence_mapping import map_all_sources
from commercial.incumbents import detect_all_incumbent_evidence
from commercial.legacy_naive_mapping_for_comparison import naive_keyword_opportunities
from research.sources import AccessStatus, Source

ORG_NAME = "Oman Airports Management Company"

# Hand-authored, clearly-synthetic evidence sources shaped like a plausible
# real corpus for this kind of organization -- an official technology page,
# a news article, a procurement notice, and two LinkedIn profile snippets.
# This is fixture data for this demo script only, not a code hardcode: the
# mapping engine below never branches on "Oman Airports" or "airport".
SOURCES = [
    Source(
        url="https://www.example-airports.test/news/digital-transformation",
        domain="example-airports.test",
        title=f"{ORG_NAME} announces digital transformation programme",
        source_type="technology",
        snippet="",
        discovered_via_query="site:example-airports.test technology digital IT infrastructure cloud cybersecurity",
        discovery_provider="serper",
        family="official_domain_restricted",
        content=(
            f"{ORG_NAME} today announced a major digital transformation programme as part of a new terminal "
            "expansion project. The programme includes a cloud migration initiative, moving core operational "
            "workloads to a new cloud platform, alongside a cybersecurity programme covering zero trust "
            "architecture and a new security operations center. The organization is expanding into a new "
            "facility to support the terminal expansion."
        ),
        access_status=AccessStatus.FETCHED,
    ),
    Source(
        url="https://www.example-news.test/business/airport-cisco-network-renewal",
        domain="example-news.test",
        title=f"{ORG_NAME} renews network partnership with Cisco",
        source_type="news",
        snippet="",
        discovered_via_query='"Oman Airports Management Company" Oman leadership CEO CIO CTO CFO appointment executive',
        discovery_provider="serper",
        family="generic_news",
        content=(
            f"{ORG_NAME} has renewed its long-standing partnership with Cisco for its network infrastructure, "
            "the incumbent vendor for the organization's core network. In separate industry commentary, "
            "analysts discussed general trends in cloud, storage and security software adoption across the "
            "aviation sector without detailing specific plans."
        ),
        access_status=AccessStatus.FETCHED,
    ),
    Source(
        url="https://www.example-tenders.test/notices/rfp-backup-dr",
        domain="example-tenders.test",
        title="RFP: Data centre backup and disaster recovery services",
        source_type="procurement",
        snippet="",
        discovered_via_query='"Oman Airports Management Company" Oman tender procurement RFP RFQ award contract',
        discovery_provider="serper",
        family="generic_procurement",
        content=(
            f"{ORG_NAME} has issued a request for proposal (RFP) for data centre backup and disaster recovery "
            "services, seeking storage infrastructure and backup and recovery capability to support expanded "
            "compute capacity at its data centre."
        ),
        access_status=AccessStatus.FETCHED,
    ),
    Source(
        url="https://www.linkedin.com/in/synthetic-person-1",
        domain="linkedin.com",
        title="Synthetic IT Manager profile",
        source_type="linkedin_profile",
        snippet=(
            "IT Manager with a background in cloud platforms, storage infrastructure and network operations. "
            "Interests: cloud, AI, storage."
        ),
        discovered_via_query='site:linkedin.com/in "Oman Airports Management Company" technology OR IT OR digital',
        discovery_provider="serper",
        family="linkedin_people",
        content=None,
        access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
    ),
    Source(
        url="https://www.linkedin.com/in/synthetic-person-2",
        domain="linkedin.com",
        title="Synthetic Marketing Coordinator profile",
        source_type="linkedin_profile",
        snippet="Marketing Coordinator. Enjoys hiking, photography and cloud watching on weekends.",
        discovered_via_query='site:linkedin.com/in "Oman Airports Management Company"',
        discovery_provider="serper",
        family="linkedin_people",
        content=None,
        access_status=AccessStatus.INDEXED_SNIPPET_ONLY,
    ),
]


def main():
    print("=" * 100)
    print(f"BEFORE / AFTER -- capability mapping for {ORG_NAME} (synthetic demo evidence, see docstring)")
    print("=" * 100)

    print("\n--- BEFORE (naive keyword-only mapping, app_v1_36_0.py-equivalent behavior) ---")
    before = naive_keyword_opportunities(SOURCES)
    print(f"Total undifferentiated 'opportunity' hits: {len(before)}")
    for hit in before:
        print(f"  [{hit['source_type']:<17}] {hit['capability_name']:<28} matched={hit['matched_terms']}  <- {hit['source_url']}")

    print("\n--- AFTER (evidence-first mapping) ---")
    # Phase 1C-H3: pass organization_name, exactly as research.analysis.report
    # does in the real pipeline, so this demo reflects production behavior
    # (including the H3 organization-association gate) rather than the
    # gate's opt-out no-op path.
    after = map_all_sources(SOURCES, organization_name=ORG_NAME)
    for level in ("direct_evidence", "strong_inference", "weak_relevance"):
        level_hits = [m for m in after if m.evidence_level.value == level]
        print(f"\n  {level.upper()}  ({len(level_hits)})")
        for m in level_hits:
            print(
                f"    {m.capability_name:<28} confidence={m.confidence:<5} matched={m.matched_terms} "
                f"signal={m.signal_type}  source_type={[s.source_type for s in SOURCES if s.url == m.source_url][0]}"
            )
            if m.incumbent_context:
                print(f"        incumbent_context: {m.incumbent_context}")

    incumbents = detect_all_incumbent_evidence(SOURCES)
    print(f"\n  INCUMBENT/VENDOR EVIDENCE (retained separately, {len(incumbents)} item(s), never a capability mapping)")
    for i in incumbents:
        print(f"    [{i.category}] '{i.vendor_or_partner_term}'  <- {i.source_url}")

    print("\n" + "=" * 100)
    summary = {
        "before_total_undifferentiated_hits": len(before),
        "after_direct_evidence": len([m for m in after if m.evidence_level.value == "direct_evidence"]),
        "after_strong_inference": len([m for m in after if m.evidence_level.value == "strong_inference"]),
        "after_weak_relevance": len([m for m in after if m.evidence_level.value == "weak_relevance"]),
        "incumbent_evidence_items": len(incumbents),
        "after_mappings": [m.to_dict() for m in after],
        "incumbent_evidence": [
            {"term": i.vendor_or_partner_term, "category": i.category, "source_url": i.source_url} for i in incumbents
        ],
    }
    with open("before_after_report.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("Full JSON written to before_after_report.json")


if __name__ == "__main__":
    main()

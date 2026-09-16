"""Evidence-relevance hardening tests (recommendations #1-#6 of
PHASE_2_EVIDENCE_RELEVANCE_AUDIT.md).

Product principle under test throughout, mirroring tests/test_phase2_inference_
hardening.py's own framing: FEWER FALSELY PROMOTED MAPPINGS/INFLATED SCORES,
while PRESERVING LEGITIMATE, ORGANIZATION-SPECIFIC TECHNOLOGY EVIDENCE. Every
false-positive-pattern test below has a paired positive-preservation test (in
this file or in the dedicated "Positive preservation" section at the bottom)
proving the fix does not over-suppress genuine evidence.

Fixtures are deliberately fictional and structurally modeled on the patterns
described in the audit (buzzword/label-list text, vendor case studies,
aggregator/government pages, retail product pages, executive bios) -- no real
organization, sector, or country is referenced anywhere in this file, and no
company/industry-specific branching exists in the production code these tests
exercise (see commercial/text_heuristics.py, commercial/evidence_mapping.py,
commercial/signals.py, commercial/opportunities.py).
"""

from __future__ import annotations

from commercial.evidence_mapping import map_all_sources, map_source_to_capabilities
from commercial.models import EvidenceCategory, EvidenceLevel
from commercial.opportunities import build_opportunity_hypotheses
from commercial.signals import extract_signals
from research.sources import AccessStatus, Source

ORG_NAME = "Verimark National Airports Authority"
COUNTRY = "Verimark"

BANK_ORG_NAME = "Castellane Bank"
INDUSTRIAL_ORG_NAME = "Oakridge Industrial AG"


def _source(
    *, url: str = "https://example.com/a", content: str, source_type: str = "official",
) -> Source:
    return Source(
        url=url, domain=url.split("/")[2], title="", source_type=source_type, snippet="",
        discovered_via_query="q", discovery_provider="test", family="generic",
        content=content, access_status=AccessStatus.FETCHED,
    )


def _promoted(mappings, capability_id):
    return [
        m for m in mappings
        if m.capability_id == capability_id and m.evidence_level != EvidenceLevel.WEAK_RELEVANCE
    ]


# ---------------------------------------------------------------------------
# Recommendation #1 -- evidence diversity scoring
# (the domain-diversity-gating fix itself, plus its two paired tests, lives
# in tests/test_phase2_inference_hardening.py: test_opportunity_score_rewards_
# genuine_domain_diversity [updated] and test_business_signal_inference_
# diversity_does_not_inflate_score [new]. This file adds one more angle:
# procurement_signal is bonus-eligible exactly like organizational_technology_
# event, not just the latter.)
# ---------------------------------------------------------------------------

def test_procurement_signal_category_is_bonus_eligible_like_organizational_technology_event():
    content = (
        f"{ORG_NAME} today issued a tender for a new storage infrastructure and server "
        "infrastructure procurement process across its facilities."
    )
    s1 = _source(url="https://procure-example.com/tender-a", content=content, source_type="procurement")
    s2 = _source(url="https://another-outlet-example.com/tender-b", content=content, source_type="procurement")
    mappings_same_domain = map_all_sources([s1, s1], organization_name=ORG_NAME, country=COUNTRY)
    mappings_diff_domain = map_all_sources([s1, s2], organization_name=ORG_NAME, country=COUNTRY)
    opp_same = {o.capability_id: o for o in build_opportunity_hypotheses(mappings_same_domain)}["data_centre_infrastructure"]
    opp_diff = {o.capability_id: o for o in build_opportunity_hypotheses(mappings_diff_domain)}["data_centre_infrastructure"]
    assert opp_diff.evidence_level == "direct_evidence"
    assert opp_diff.score > opp_same.score


# ---------------------------------------------------------------------------
# Recommendation #2 -- vendor case studies
# ---------------------------------------------------------------------------

def test_vendor_case_study_on_vendors_own_domain_is_not_promoted():
    """The Zscaler x Siemens audit finding, generalized: a case study
    published on the VENDOR's own domain, describing the vendor's own
    product deployed at the organization, must not promote a competing
    Otech capability as direct_evidence/organizational_technology_event --
    it is current-state/incumbent context.
    """
    content = (
        f"Zscaler Resilience is an integrated suite of disaster recovery and backup and recovery "
        f"features built to protect {INDUSTRIAL_ORG_NAME}'s critical workloads. "
        f"{INDUSTRIAL_ORG_NAME} relies on Zscaler's platform for business continuity plan "
        "execution across its global operations."
    )
    s = _source(url="https://www.zscaler.com/customers/oakridge-case-study", content=content, source_type="technology")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    dc = [m for m in mappings if m.capability_id == "data_centre_infrastructure"]
    assert dc, "expected the source to at least match data_centre_infrastructure terms"
    assert not _promoted(mappings, "data_centre_infrastructure")
    assert all(m.evidence_category == EvidenceCategory.CURRENT_STATE_TECHNOLOGY.value for m in dc)
    assert "Zscaler" in dc[0].incumbent_context


def test_same_vendor_mentioned_on_non_vendor_domain_is_unaffected_by_case_study_rule():
    """Control: recommendation #2 is scoped to the SOURCE's own domain
    matching the vendor -- a third-party news article that happens to
    mention the same vendor by name is not, by itself, downgraded by this
    rule (the pre-existing Rule 2/3 promotion logic still applies
    normally).
    """
    content = (
        f"{INDUSTRIAL_ORG_NAME} today announced a disaster recovery and backup and recovery "
        "modernization initiative, working with several technology partners including Zscaler."
    )
    s = _source(url="https://industry-news-example.com/oakridge-dr-upgrade", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    assert _promoted(mappings, "data_centre_infrastructure")


# ---------------------------------------------------------------------------
# Recommendation #3 -- buzzword/list-like text
# ---------------------------------------------------------------------------

def test_signal_pattern_inside_label_list_span_is_not_extracted_as_a_signal():
    label_list_text = (
        "ENABLING TECHNOLOGIES: Digital Transformation IIoT Analytics Microchip Sensors "
        "Connectivity Cybersecurity 5G Digital Twin Simulation Additive Manufacturing "
        "Augmented Reality Edge Computing"
    )
    content = (
        f"{ORG_NAME} exhibited at the trade show. {label_list_text}. Separately, in an unrelated "
        f"announcement, {ORG_NAME} confirmed a new investment in its logistics network, part of a "
        "broader capital expenditure programme."
    )
    s = _source(content=content)
    signals = extract_signals(s)
    types = {sig.signal_type for sig in signals}
    # The ONLY place "digital transformation" (a technology_initiative
    # trigger phrase) appears is inside the buzzword list -- it must not
    # be extracted as a signal from there.
    assert "technology_initiative" not in types
    # The genuine, separately-stated investment signal elsewhere in the
    # SAME source must still be extracted -- this is not a blanket
    # suppression of the whole source.
    assert "investment" in types


def test_buzzword_list_with_two_direct_terms_does_not_promote_direct_evidence():
    """The Siemens "5G buzzword list" audit finding, generalized: two
    distinct direct capability keywords co-occurring inside a category-
    label list (even alongside the organization's own name in that same
    list) must not satisfy the "two co-occurring specific terms" direct_
    evidence path -- a label list is not a sentence describing what the
    organization is doing.
    """
    content = (
        "ENABLING TECHNOLOGIES: " + ORG_NAME + " IIoT Analytics SD-WAN Connectivity IPC "
        "Cybersecurity 5G Digital Twin Simulation Edge Computing."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert not _promoted(mappings, "network_connectivity")


def test_same_two_direct_terms_in_a_genuine_sentence_still_promote_direct_evidence():
    """Positive preservation for recommendation #3: the same two direct
    terms ("5G", "SD-WAN"), when they appear in an ordinary descriptive
    sentence (with a verb) about the organization rather than a label
    list, must still promote normally -- the fix targets the LIST SHAPE,
    not the terms themselves.
    """
    content = f"{ORG_NAME} is deploying a private 5G network alongside a new SD-WAN backbone to modernize its operations."
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert _promoted(mappings, "network_connectivity")
    net = next(m for m in mappings if m.capability_id == "network_connectivity" and m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE)
    assert net.evidence_category == EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT.value


# ---------------------------------------------------------------------------
# Recommendation #4 -- aggregator / regional / government pages
# ---------------------------------------------------------------------------

def test_country_level_reference_does_not_satisfy_organization_association():
    """The Oman MTCIT audit finding, generalized: an organization whose
    name contains its country's name (e.g. "Verimark National Airports
    Authority" / country "Verimark") must not be treated as "associated"
    with a technology mention merely because the COUNTRY name and one
    generic word ("National") co-occur near it on a government/ministry
    page -- the country token is excluded from association matching, so a
    real organization-specific token must actually be present.
    """
    content = (
        "Verimark today reaffirmed its commitment to the National Digital Transformation "
        "Programme, which brings together dozens of participating government agencies and "
        "private companies across multiple sectors. The management of the programme falls "
        "under the Ministry of Technology and Communications."
    )
    s = _source(url="https://mtic.example.gov/national-programme", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert not _promoted(mappings, "enterprise_managed_services")


def test_country_level_reference_is_looser_only_when_country_not_supplied():
    """Confirms the mechanism directly: the SAME source, mapped WITHOUT a
    country supplied, falls back to the prior (unfiltered) token set --
    country-exclusion is opt-in context threaded by the real pipeline
    (research/analysis/report.py always supplies it), not a change to
    map_source_to_capabilities' default, backward-compatible behavior.
    """
    content = (
        "Verimark today reaffirmed its commitment to the National Digital Transformation "
        "Programme, which brings together dozens of participating government agencies and "
        "private companies across multiple sectors."
    )
    s = _source(url="https://mtic.example.gov/national-programme", content=content, source_type="official")
    with_country = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    without_country = map_source_to_capabilities(s, organization_name=ORG_NAME)
    assert not _promoted(with_country, "enterprise_managed_services")
    assert _promoted(without_country, "enterprise_managed_services")


def test_read_more_navigation_concatenation_is_treated_as_lower_trust():
    content = (
        f"Read More: {ORG_NAME} is part of a regional infrastructure network that saw new "
        "facility openings across several operators, Read More, according to industry sources."
    )
    s = _source(url="https://regional-roundup-example.com/news-listing", content=content, source_type="business")
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert not _promoted(mappings, "network_connectivity")
    assert not _promoted(mappings, "data_centre_infrastructure")


def test_rule_2_3_strong_inference_fallback_now_requires_organization_association():
    """A previously-unguarded gap: the direct_hits STRONG_INFERENCE
    fallback branch (a signal-backed lone term, below the direct_evidence
    bar) required NO organization association at all. A term that is
    signal-backed but never actually co-occurs with the target
    organization anywhere in the source must not be promoted.
    """
    content = (
        "A regional operator announced a major cloud migration initiative this week, as part of "
        "a broader modernization drive across the sector. Industry watchers noted broader "
        f"adoption trends throughout the region. Separately, {ORG_NAME} opened a new office "
        "nearby."
    )
    s = _source(content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert not _promoted(mappings, "cloud_ai_infrastructure")


# ---------------------------------------------------------------------------
# Recommendation #5 -- customer-facing product pages
# ---------------------------------------------------------------------------

def test_retail_product_page_expansion_wording_does_not_promote_internal_signal():
    """The sc.bank.in "Business Instalment Loan" audit finding,
    generalized: a retail/product/pricing page's own marketing copy must
    not be read as the organization's own internal expansion/capacity
    signal merely because the product name/description shares vocabulary
    with the capability catalogue's generic triggers.
    """
    content = (
        f"{BANK_ORG_NAME} customers can apply now for our Business Expansion Loan, offering "
        "capacity expansion for growing businesses. Interest rate starting at 5.5% APR, terms "
        "and conditions apply, minimum balance requirements apply."
    )
    s = _source(
        url="https://castellanebank.example.com/personal/loans/business-expansion-loan",
        content=content, source_type="business",
    )
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME)
    assert not _promoted(mappings, "data_centre_infrastructure")
    assert not _promoted(mappings, "enterprise_managed_services")


def test_same_wording_on_a_non_product_page_still_promotes_normally():
    """Positive preservation for recommendation #5: identical "expansion"/
    "capacity" wording, on a page that does NOT carry retail/product-page
    path or content markers (e.g. a corporate news/investor page), is
    unaffected -- the fix is scoped to the page SHAPE, not the words.
    """
    content = (
        f"{BANK_ORG_NAME} today announced a major capacity expansion and business expansion "
        "programme for its own data centre infrastructure, backed by a new investment round."
    )
    s = _source(url="https://castellanebank.example.com/news/2026-expansion", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME)
    assert _promoted(mappings, "data_centre_infrastructure")


# ---------------------------------------------------------------------------
# Recommendation #6 -- third-party executive biographies/interviews
# ---------------------------------------------------------------------------

def test_generic_executive_bio_content_is_treated_as_stakeholder_context():
    """The thekeyexecutives.com CIO-bio audit finding, generalized: a
    third-party biography/interview article about an individual executive
    must get the same stakeholder_context treatment as a LinkedIn personal
    profile, even though it is not on LinkedIn -- a vague "responsible for
    digital transformation" remit statement must not fan out into several
    promoted capability hypotheses.
    """
    content = (
        f"Jane Doe is responsible for {INDUSTRIAL_ORG_NAME}'s digital transformation strategy. "
        f"Before joining {INDUSTRIAL_ORG_NAME}, she held senior technology roles at several "
        "multinational firms. In her role as Chief Information Officer, she believes IT is not "
        "just a support function but a driver of business value."
    )
    s = _source(url="https://industry-interviews-example.com/profiles/jane-doe", content=content, source_type="business")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    assert len(mappings) >= 2, "the vague 'digital transformation' phrase should still match several capabilities"
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in mappings)
    assert all(m.evidence_category == EvidenceCategory.STAKEHOLDER_CONTEXT.value for m in mappings)


def test_single_incidental_bio_phrase_does_not_suppress_a_genuine_announcement():
    """Positive preservation for recommendation #6: a genuine, concrete
    technology-event announcement that happens to include ONE incidental
    biographical clause about an executive must not be reclassified --
    the marker-count threshold (>= 2 distinct markers) exists precisely so
    a real announcement isn't caught by this rule.
    """
    content = (
        f"{INDUSTRIAL_ORG_NAME} today announced a network modernization initiative, deploying a "
        "new SD-WAN and completing a bandwidth upgrade across its facilities. In her role as CIO, "
        "Jane Doe confirmed the rollout."
    )
    s = _source(content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    assert _promoted(mappings, "network_connectivity")


# ---------------------------------------------------------------------------
# Positive preservation -- genuine organizational technology events, modeled
# on the audit's own SUPPORTED rows, must still map correctly after all six
# recommendations are applied together.
# ---------------------------------------------------------------------------

def test_preserved_cloud_first_bank_partnership_announcement():
    """Mirrors the audit's SUPPORTED "SC x Microsoft cloud-first bank"
    finding: a genuine, organization-specific press statement about a
    named technology partnership must still promote strong_inference for
    the relevant capability.
    """
    content = (
        f"{BANK_ORG_NAME} today confirmed a strategic partnership to advance its digital "
        "transformation and become a cloud-first organization, reshaping how it delivers "
        "services to customers."
    )
    s = _source(url="https://castellanebank.example.com/press/cloud-first-bank", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME)
    promoted = _promoted(mappings, "cloud_ai_infrastructure")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.STRONG_INFERENCE


def test_preserved_named_technology_partner_announcement():
    """Mirrors the audit's SUPPORTED "Siemens x NVIDIA CES 2026" finding:
    a genuine partnership-expansion announcement naming a technology
    partner (a direct catalogue keyword: "nvidia") must still promote,
    proving the new org_associated requirement on the Rule 2/3
    strong_inference fallback (recommendation #4) does not break a
    genuinely organization-associated case.
    """
    content = (
        f"CES 2026: NVIDIA and {INDUSTRIAL_ORG_NAME} today expanded their strategic partnership "
        "to build the Industrial AI Operating System, accelerating industrial AI innovation "
        "together."
    )
    s = _source(url="https://press.example-industrial.com/global/en/press/ces-2026", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    promoted = _promoted(mappings, "cloud_ai_infrastructure")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert promoted[0].evidence_category == EvidenceCategory.BUSINESS_SIGNAL_INFERENCE.value


def test_preserved_named_partner_soc_direct_evidence():
    """Mirrors the audit's SUPPORTED "UAE Cyber Security Council x
    Siemens" finding: a concrete, organization-attributed security
    operations center expansion, described in a genuine sentence (not a
    label list, not a vendor's own case study), must still promote
    direct_evidence.
    """
    content = (
        f"The National Cyber Security Council and {INDUSTRIAL_ORG_NAME} today announced a phased "
        "expansion of the organization's security operations center capabilities, strengthening "
        "its business continuity plan across the region."
    )
    s = _source(url="https://news.example-industrial.com/cyber-council-partnership", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    promoted = _promoted(mappings, "cybersecurity_resilience")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.DIRECT_EVIDENCE
    assert promoted[0].evidence_category == EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT.value


# ===========================================================================
# Follow-up hardening pass (PHASE_2_EVIDENCE_RELEVANCE_FOLLOWUP_AUDIT.md):
# four additional, generic gaps found in a live run AFTER recommendations
# #1-#6 above were already in place. As above: every false-positive
# regression has a paired positive-preservation test, and no organization,
# industry, sector, subsidiary, vendor-to-capability, or geography-specific
# branching is introduced anywhere in the production code these tests
# exercise (commercial/text_heuristics.py, commercial/evidence_mapping.py).
# ===========================================================================

# ---------------------------------------------------------------------------
# Area 1 -- hyphenated product-slug detection (closing the recommendation #5
# gap the follow-up audit found: the sc.bank.in "business-instalment-loan"
# false positive, caused by the original detector requiring an EXACT path
# segment like "/loans/" rather than a marker word appearing as a token
# inside a longer hyphenated slug).
# ---------------------------------------------------------------------------

def test_hyphenated_product_slug_false_positive_is_now_detected():
    """Reproduces the exact reported gap: a real hyphenated retail-banking
    URL encodes the product name as a single slug
    ("/business-expansion/business-instalment-loan"), not a standalone
    "/loans/" directory level. Before this fix, `looks_like_customer_
    product_page` missed this shape entirely, so the page's own product
    marketing copy ("business expansion" as part of a loan product name)
    was read as the ORGANIZATION's own internal expansion signal.
    """
    content = (
        f"{BANK_ORG_NAME} customers can apply now for our Business Instalment Loan, ideal for "
        "business expansion needs. Interest rate starting at 4.9% APR, terms and conditions "
        "apply, minimum balance requirements apply."
    )
    s = _source(
        url="https://castellanebank.example.com/business-expansion/business-instalment-loan",
        content=content, source_type="business",
    )
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME)
    assert not _promoted(mappings, "data_centre_infrastructure")
    assert not _promoted(mappings, "enterprise_managed_services")
    assert not _promoted(mappings, "cybersecurity_resilience")


def test_similar_wording_on_a_genuine_organizational_announcement_still_promotes():
    """Positive preservation for the Area 1 fix: a genuine, non-product
    organizational announcement using very similar vocabulary ("business
    expansion", "instalment") -- but with neither a product-page URL nor
    product/pricing content markers -- must still promote normally. The fix
    targets the PAGE SHAPE (path token + >=2 content markers), never the
    words themselves; a single keyword must never be sufficient by itself.

    Current/Change/Requirement Signal Gate update: "business expansion" is a
    generic_trigger SHARED by data_centre_infrastructure AND enterprise_
    managed_services, now requiring local, capability-specific corroboration
    under the approved catalogue-derived rule. "server infrastructure" and
    "storage capacity" (data_centre_infrastructure's own vocabulary) are
    added so this remains a genuine data-centre inference, independent of
    the Area-1 page-shape fix this test actually exercises.
    """
    content = (
        f"{BANK_ORG_NAME} today announced a major business expansion programme, including the "
        "instalment of new server infrastructure and storage capacity across its branches as part "
        "of a broader business expansion and modernization drive, backed by a new investment round."
    )
    s = _source(
        url="https://castellanebank.example.com/news/2026-business-expansion-announcement",
        content=content, source_type="official",
    )
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME)
    assert _promoted(mappings, "data_centre_infrastructure")


# ---------------------------------------------------------------------------
# Area 2 -- Rule 2/3 locality + event gaps (bringing the direct_hits
# promotion branches into alignment with the locality principle Rule 4
# already had: a grounding signal must be positionally associated with the
# matched capability term, not merely present anywhere in the source).
# ---------------------------------------------------------------------------

def test_unrelated_technology_signal_elsewhere_in_article_does_not_ground_direct_term():
    """A single direct capability term ("cloud migration") sits right next
    to the organization's name -- satisfying the pre-existing org-
    association gate -- but the only technology-event signal in the source
    ("upgrading its billing systems") describes a COMPLETELY UNRELATED fact
    (a different, unnamed utility provider) two sentences away. Before this
    fix, `tech_event_backed` was true simply because SOME technology-event
    signal was extracted from the source, regardless of where -- letting a
    high-trust source promote a lone term to DIRECT_EVIDENCE (Rule 2/3
    branch 2) on the strength of a signal that has nothing to do with it.
    """
    content = (
        f"{ORG_NAME} today discussed its longstanding cloud migration strategy during a routine "
        "industry panel, without announcing any new initiative. "
        "The panel also covered several unrelated sector-wide trends over the course of the "
        "afternoon session. "
        "In a completely separate development, a regional utility provider confirmed it is "
        "upgrading its billing systems this quarter."
    )
    s = _source(content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert not _promoted(mappings, "cloud_ai_infrastructure")


def test_generic_cybersecurity_marketing_copy_with_two_keywords_does_not_promote():
    """The Siemens Energy `cybersecurity.html` audit finding, generalized:
    two distinct direct capability keywords ("ransomware", "cyber risk")
    co-occurring near the organization's own name is not, by itself,
    evidence of an organizational technology EVENT when the surrounding
    text is generic marketing/awareness copy describing what the
    organization offers to ITS OWN CUSTOMERS ("helps its customers reduce
    exposure...") rather than an event at the organization itself. This
    reuses the same generic customer-facing-language detector as Area 3
    (a third-party-requirement span is exactly as disqualifying here as a
    label list/navigation span already was) -- not a new marketing-copy
    classifier.
    """
    content = (
        f"{INDUSTRIAL_ORG_NAME} helps its customers reduce exposure to ransomware and manage "
        "cyber risk across their critical infrastructure environments, drawing on decades of "
        "engineering experience."
    )
    s = _source(url="https://www.example-industrial.com/solutions/cybersecurity", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    assert not _promoted(mappings, "cybersecurity_resilience")


def test_siemens_5g_buzzword_list_case_does_not_promote_via_unrelated_signal():
    """The exact root-caused audit finding: a "5G" term sits near the
    organization's name (outside any label list), a category-label/
    buzzword list of unrelated technology terms follows, and then a
    completely unrelated technology_initiative signal ("employees are
    upgrading their skills") appears two sentences later. Before this fix,
    Rule 2/3's signal-backed STRONG_INFERENCE fallback branch had no
    positional check on the signal at all, so this unrelated signal
    "grounded" the "5G" term even though it describes an unconnected
    internal training matter, not a technology event involving 5G.
    """
    label_list = (
        "ENABLING TECHNOLOGIES: Digital Transformation IIoT Analytics Microchip Sensors "
        "Connectivity Cybersecurity Digital Twin Simulation Additive Manufacturing AR VR"
    )
    content = (
        f"{ORG_NAME} exhibited at the trade show, showcasing its 5G capabilities to visitors. "
        f"{label_list}. "
        "Employees are upgrading their skills through a new training programme launched this "
        "quarter, part of an unrelated internal initiative."
    )
    s = _source(url="https://industry-news-example.com/trade-show-roundup", content=content, source_type="business")
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert not _promoted(mappings, "network_connectivity")


def test_genuine_locally_associated_technology_announcement_still_promotes():
    """Positive preservation for Area 2: when the grounding signal IS
    positionally associated with the matched term (one sentence apart,
    describing the same event), the same high-trust single-term path still
    promotes to DIRECT_EVIDENCE exactly as before -- the fix requires
    locality, not the absence of a signal.
    """
    content = (
        f"{ORG_NAME} today confirmed a major cloud migration initiative. This technology upgrade "
        "will modernize its core operational systems over the next 18 months."
    )
    s = _source(content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    promoted = _promoted(mappings, "cloud_ai_infrastructure")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.DIRECT_EVIDENCE


# ---------------------------------------------------------------------------
# Area 3 -- organization requirements vs. customer requirements (the
# Siemens Energy/Eaton "close to gas, water and fiber" audit finding: a
# vendor describing what ITS PROSPECTIVE CUSTOMERS need at their own sites
# is not evidence of the PUBLISHING organization's own requirement).
# ---------------------------------------------------------------------------

def test_customer_siting_criteria_language_is_not_promoted_as_organizations_own_requirement():
    content = (
        f"{INDUSTRIAL_ORG_NAME} recommends that prospective customers site their data centres "
        "close to gas, water and fiber connections for optimal performance."
    )
    s = _source(url="https://press.example-industrial.com/data-centre-siting-guidance", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    assert not _promoted(mappings, "network_connectivity")
    fiber_mappings = [m for m in mappings if m.capability_id == "network_connectivity"]
    assert fiber_mappings, "the term match itself should still be retained as (non-promoted) evidence"
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in fiber_mappings)
    assert all(m.evidence_category == EvidenceCategory.CUSTOMER_REQUIREMENT_CONTEXT.value for m in fiber_mappings)


def test_organizations_own_site_requirement_language_still_promotes():
    """Positive preservation for Area 3: the SAME underlying siting
    language ("close to gas, water and fiber connections"), when it
    describes the ORGANIZATION's own new facility rather than a customer's
    site, is unaffected by the customer-requirement detector and still
    promotes normally.
    """
    content = (
        f"{INDUSTRIAL_ORG_NAME} is building a new facility that must be close to gas, water and "
        "fiber connections to support its own operations, as part of a major expansion project that "
        "will also increase the facility's network bandwidth capacity."
    )
    s = _source(url="https://press.example-industrial.com/new-facility-announcement", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    promoted = _promoted(mappings, "network_connectivity")
    assert promoted
    assert promoted[0].evidence_category == EvidenceCategory.BUSINESS_SIGNAL_INFERENCE.value


# ---------------------------------------------------------------------------
# Area 4 -- generic capacity-expansion semantic ambiguity (a bare "capacity
# expansion" trigger is equally consistent with manufacturing, renewable-
# generation, logistics, or IT/data-centre capacity; only the last of those
# is actually relevant to Data Centre & Infrastructure).
# ---------------------------------------------------------------------------

def test_manufacturing_capacity_expansion_does_not_promote_data_centre_infrastructure():
    content = (
        f"{INDUSTRIAL_ORG_NAME} announced a major capacity expansion at its manufacturing plant, "
        "adding new production lines to meet growing demand for industrial equipment."
    )
    s = _source(url="https://press.example-industrial.com/manufacturing-capacity-expansion", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    assert not _promoted(mappings, "data_centre_infrastructure")


def test_renewable_energy_capacity_expansion_does_not_promote_data_centre_infrastructure():
    content = (
        f"{INDUSTRIAL_ORG_NAME} confirmed a significant capacity expansion of its solar power "
        "generation facility, increasing renewable energy output for the region."
    )
    s = _source(url="https://press.example-industrial.com/solar-capacity-expansion", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    assert not _promoted(mappings, "data_centre_infrastructure")


def test_generic_business_capacity_expansion_does_not_promote_data_centre_infrastructure():
    content = (
        f"{INDUSTRIAL_ORG_NAME} today announced a capacity expansion as part of its broader "
        "business growth strategy, though no further details were provided."
    )
    s = _source(url="https://press.example-industrial.com/capacity-expansion-announcement", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    assert not _promoted(mappings, "data_centre_infrastructure")
    dc_mappings = [m for m in mappings if m.capability_id == "data_centre_infrastructure"]
    assert dc_mappings
    assert all(m.evidence_level == EvidenceLevel.WEAK_RELEVANCE for m in dc_mappings)


def test_explicit_data_centre_capacity_expansion_still_promotes():
    """Positive preservation: the same ambiguous "capacity expansion"
    trigger, locally accompanied by clearly IT/data-centre-specific
    vocabulary (this capability's own catalogue terms), still promotes --
    the fix requires additional local IT context, it does not suppress
    genuine IT/data-centre expansion announcements.
    """
    content = (
        f"{INDUSTRIAL_ORG_NAME} announced a major capacity expansion of its data centre, adding "
        "new storage and compute resources to support growing digital workloads."
    )
    s = _source(url="https://press.example-industrial.com/data-centre-capacity-expansion", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    promoted = _promoted(mappings, "data_centre_infrastructure")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.STRONG_INFERENCE


def test_infrastructure_capacity_expansion_with_clear_it_context_still_promotes():
    content = (
        f"{INDUSTRIAL_ORG_NAME} confirmed a major capacity expansion of its IT infrastructure, "
        "upgrading server and storage systems across its facilities."
    )
    s = _source(url="https://press.example-industrial.com/it-infrastructure-capacity-expansion", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    promoted = _promoted(mappings, "data_centre_infrastructure")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.STRONG_INFERENCE


def test_preserved_own_domain_partner_technology_direct_evidence():
    """Mirrors the audit's SUPPORTED "Siemens delivers ... Private 5G with
    Palo Alto Networks" finding: published on the ORGANIZATION's own
    domain (not the partner vendor's), this is genuine, specific evidence
    of the organization's own infrastructure -- the named partner is
    context, not disqualifying. Proves recommendation #2's vendor-case-
    study rule is correctly scoped to the SOURCE's own domain, not to
    every source that happens to mention a known vendor by name.
    """
    content = (
        f"{INDUSTRIAL_ORG_NAME} delivers a verified AI-driven cybersecurity solution for "
        "Industrial 5G, in collaboration with Palo Alto Networks, as part of its own private 5G "
        "and SD-WAN infrastructure rollout."
    )
    s = _source(url="https://press.example-industrial.com/global/en/press/private-5g", content=content, source_type="official")
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    promoted = _promoted(mappings, "network_connectivity")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.DIRECT_EVIDENCE
    assert promoted[0].evidence_category == EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT.value
    assert "Palo Alto Networks" in promoted[0].incumbent_context


# ---------------------------------------------------------------------------
# False-positive-class fix (Oman Airports live-validation finding, 2026-09):
# the collective/aggregate-scope span disqualifier ("4a" in
# PHASE_2_FALSE_POSITIVE_DESIGN_REPORT.md).
#
# Product principle under test: an organization can be genuinely, locally
# co-located with capability-specific vocabulary and a qualifying business
# signal, and still not be the actual SUBJECT of that claim -- it can be
# named only as one member of a collective (an entire government, sector,
# or industry). `commercial.text_heuristics._is_collective_scope_span`,
# composed into `_span_disqualifies_evidence` (used by
# `_is_organization_associated`, `_signal_locally_associated_with_trigger`,
# and `_direct_evidence_locally_grounded`), disqualifies exactly that
# SPAN -- not the whole source -- from grounding organization-association,
# so an organization-specific claim made in a different, non-collective-
# scope sentence of the same article remains fully eligible.
#
# Every "must reject" test below has a paired "must remain eligible" test
# proving the fix does not over-suppress genuine, organization-specific
# evidence -- same discipline as the rest of this file.
# ---------------------------------------------------------------------------

# --- must reject / must not independently qualify --------------------------

def test_government_wide_collective_statement_in_single_sentence_does_not_promote():
    """Item 1: the org is named only as one member of a government-wide
    collective, in the same sentence as the trigger and an implicit
    'programme' signal -- must not promote. Mirrors the real Oman
    Airports excerpt's own retained language ("...across the whole of
    government...").
    """
    content = (
        f"{ORG_NAME} is among the government entities progressing a digital transformation "
        "programme across the whole of government."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    for capability_id in ("cloud_ai_infrastructure", "cybersecurity_resilience", "enterprise_managed_services"):
        assert not _promoted(mappings, capability_id), capability_id


def test_among_government_entities_with_capability_vocabulary_does_not_promote():
    """Item 2: explicit "among the government entities" enumeration, with
    capability vocabulary (cloud/cybersecurity/managed services) applying
    to the COLLECTIVE, not the researched organization specifically.
    """
    content = (
        f"{ORG_NAME} is among the government entities progressing digital transformation, which "
        "spans cloud, cybersecurity and managed services investments across the sector."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    for capability_id in ("cloud_ai_infrastructure", "cybersecurity_resilience", "enterprise_managed_services"):
        assert not _promoted(mappings, capability_id), capability_id


def test_sector_wide_statement_does_not_promote():
    """Item 3: "across the banking sector" -- the researched organization
    is merely one member of the sector, not the subject of the claim.
    """
    content = (
        f"{BANK_ORG_NAME} is one of several lenders adopting digital transformation practices that "
        "are spreading across the banking sector."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME)
    for capability_id in ("cloud_ai_infrastructure", "cybersecurity_resilience", "enterprise_managed_services"):
        assert not _promoted(mappings, capability_id), capability_id


def test_industry_wide_statement_does_not_promote():
    """Item 4: "industry-wide" -- the organization is not the specific
    subject of the claim.
    """
    content = (
        f"{ORG_NAME} is participating in an industry-wide digital transformation push affecting "
        "the aviation sector broadly."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    for capability_id in ("cloud_ai_infrastructure", "cybersecurity_resilience", "enterprise_managed_services"):
        assert not _promoted(mappings, capability_id), capability_id


def test_several_other_government_entities_does_not_promote():
    """Item 5: "several other government entities" -- a collective
    initiative, not an organization-specific one.
    """
    content = (
        f"{ORG_NAME} and several other government entities are advancing the digital "
        "transformation programme as part of a national strategy."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    for capability_id in ("cloud_ai_infrastructure", "cybersecurity_resilience", "enterprise_managed_services"):
        assert not _promoted(mappings, capability_id), capability_id


# --- must remain eligible ---------------------------------------------------

def test_organization_specific_current_claim_still_promotes():
    """Item 6: no collective-scope language at all -- a plain
    organization-specific current claim must be unaffected.
    """
    content = (
        f"{ORG_NAME} is pursuing a digital transformation programme and is upgrading its security "
        "infrastructure this quarter."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert _promoted(mappings, "cybersecurity_resilience")


def test_organization_named_as_specific_responsible_party_still_promotes():
    """Item 7: the organization is explicitly singled out by name for a
    specific role ("named ... as the lead agency"), not merely enumerated
    among many -- the collective-scope marker list must not match this
    shape (explicit naming, not aggregate enumeration).
    """
    content = (
        f"The Ministry named {ORG_NAME} as the lead agency for the new cloud migration programme, "
        "part of the country's digital transformation strategy."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    promoted = _promoted(mappings, "cloud_ai_infrastructure")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.DIRECT_EVIDENCE


def test_organization_with_peers_but_own_specific_claim_still_promotes():
    """Item 8 -- the most important false-negative guard named in the
    design brief: "Standard Chartered, along with several other banks,
    also announced its own new AI-driven fraud detection platform" must
    remain potentially qualifying because the organization has its own
    specific claim. "along with several other banks" names no
    government/state/sector/industry-wide scope, so the collective-scope
    marker list must not match it.
    """
    content = (
        f"{BANK_ORG_NAME}, along with several other banks, is pursuing its own digital "
        "transformation programme and also announced its own new AI-driven fraud detection "
        "platform."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=BANK_ORG_NAME)
    assert _promoted(mappings, "cloud_ai_infrastructure")


def test_organization_specific_claim_survives_unrelated_government_wide_language_elsewhere():
    """Item 9: a current, organization-specific claim embedded in a
    broader article that ALSO contains government-wide language elsewhere
    -- only the collective-scope SPAN is disqualified, not the whole
    source, so the organization's own, separate claim remains eligible.
    """
    content = (
        "Government agencies across the whole of government are pursuing a national digital "
        f"transformation programme together. Separately, {ORG_NAME} said it is expanding its own "
        "cybersecurity capabilities as part of a digital transformation initiative, deploying a "
        "new cloud platform to support its own terminal operations this quarter."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert _promoted(mappings, "cloud_ai_infrastructure")
    assert _promoted(mappings, "cybersecurity_resilience")


def test_siemens_style_organization_specific_cybersecurity_evidence_still_promotes():
    """Item 10: positive control mirroring the confirmed-good live-crawl
    Siemens Cybersecurity & Resilience pattern -- organization-specific,
    capability-specific, current. Must be unaffected by this fix.
    """
    content = (
        f"{INDUSTRIAL_ORG_NAME} is pursuing a digital transformation programme and is expanding "
        "its cybersecurity platform this quarter, strengthening governance across its own "
        "operations."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=INDUSTRIAL_ORG_NAME)
    promoted = _promoted(mappings, "cybersecurity_resilience")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.STRONG_INFERENCE


# --- item 7 (of the user's numbered instruction list): dedicated,
# end-to-end reproduction of the live failure mechanism ---------------------

def test_live_failure_mechanism_synthetic_reproduction_creates_no_opportunity():
    """The most important new regression test (per instruction): reproduce
    the Oman Airports live-validation mechanism as closely as possible in
    one synthetic article -- an organization mention co-located with the
    shared trigger AND collective-scope language in one sentence, several
    unrelated filler paragraphs, then a SEPARATE, disconnected occurrence
    of the same trigger elsewhere in the article with genuine cloud,
    cybersecurity, and managed-services vocabulary plus a qualifying
    signal -- and confirm the government-wide collective statement does
    NOT independently create Cloud & AI Infrastructure, Cybersecurity &
    Resilience, or Enterprise Managed Services opportunities.
    """
    sentence_a = (
        f"{ORG_NAME} and several other state entities are advancing the digital transformation "
        "programme as part of the government's national strategy."
    )
    filler = (
        "The National Museum unveiled a new exhibition celebrating national heritage and "
        "craftsmanship. Visitors can explore artefacts spanning centuries of maritime trade. "
        "Elsewhere, the Ministry of Tourism reported a record number of international visitors "
        "this quarter, citing new direct flight routes. The agriculture sector also saw growth, "
        "with date exports rising compared to the previous year."
    )
    sentence_b = (
        "Separately, the wider digital transformation initiative is being expanded to include new "
        "cloud infrastructure, enhanced cybersecurity measures, and additional managed services "
        "capacity across government agencies."
    )
    content = f"{sentence_a} {filler} {sentence_b}"
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    for capability_id in ("cloud_ai_infrastructure", "cybersecurity_resilience", "enterprise_managed_services"):
        assert not _promoted(mappings, capability_id), capability_id
    opportunities = build_opportunity_hypotheses(mappings)
    opportunity_ids = {o.capability_id for o in opportunities}
    assert "cloud_ai_infrastructure" not in opportunity_ids
    assert "cybersecurity_resilience" not in opportunity_ids
    assert "enterprise_managed_services" not in opportunity_ids


# --- critical regression tests ----------------------------------------------

def test_direct_evidence_unaffected_by_unrelated_collective_scope_sentence_elsewhere():
    """Item 11/12: an organization-specific direct-evidence claim (the
    organization explicitly named as the responsible party) must be
    unaffected by an unrelated collective-scope sentence elsewhere in the
    same article -- the new disqualifier is span-local, not source-wide.
    """
    content = (
        "Government agencies across the whole of government held a joint conference on public "
        f"administration this month. Separately, the Ministry named {ORG_NAME} as the lead agency "
        "for the new cloud migration programme, part of the country's digital transformation "
        "strategy."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    promoted = _promoted(mappings, "cloud_ai_infrastructure")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.DIRECT_EVIDENCE
    assert promoted[0].evidence_category == EvidenceCategory.ORGANIZATIONAL_TECHNOLOGY_EVENT.value


def test_customer_requirement_disqualification_still_applies_alongside_new_branch():
    """Item 13: the pre-existing customer-requirement span disqualifier
    (a separate OR branch in `_span_disqualifies_evidence`) must continue
    to function correctly now that a third branch has been added.
    """
    content = (
        f"{ORG_NAME}'s data centre colocation service is available to customers requiring space "
        "close to gas, water and fiber for their own facilities."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    dc = [m for m in mappings if m.capability_id == "data_centre_infrastructure"]
    assert dc
    assert not _promoted(mappings, "data_centre_infrastructure")
    assert all(m.evidence_category == EvidenceCategory.CUSTOMER_REQUIREMENT_CONTEXT.value for m in dc)


def test_multi_account_runs_are_independent_and_unaffected_by_each_others_context():
    """Item 14: running the SAME collective-scope article shape for two
    DIFFERENT organizations produces independently correct results -- the
    evidence-mapping functions are pure/stateless per call, so there is no
    cross-account contamination introduced by the new disqualifier.
    """
    content_template = (
        "{org} is among the government entities progressing a digital transformation programme "
        "across the whole of government."
    )
    s1 = _source(url="https://example.com/multi-account-org1", content=content_template.format(org=ORG_NAME))
    s2 = _source(url="https://example.com/multi-account-org2", content=content_template.format(org=BANK_ORG_NAME))
    mappings1 = map_source_to_capabilities(s1, organization_name=ORG_NAME, country=COUNTRY)
    mappings2 = map_source_to_capabilities(s2, organization_name=BANK_ORG_NAME)
    for mappings in (mappings1, mappings2):
        for capability_id in ("cloud_ai_infrastructure", "cybersecurity_resilience", "enterprise_managed_services"):
            assert not _promoted(mappings, capability_id), capability_id


def test_generic_trigger_fan_out_gate_remains_intact_bare_mention_no_signal():
    """Item 15: the pre-existing Current/Change/Requirement Signal Gate
    (a bare capability mention with NO qualifying signal anywhere in the
    text must not promote) is a different, earlier mechanism than this
    fix and must remain untouched. ("new digital platform" is a generic
    trigger for cloud_ai_infrastructure that is deliberately NOT itself a
    signal-pattern phrase, unlike "digital transformation" -- so this
    fixture contains no signal-triggering language at all.)
    """
    content = (
        f"{ORG_NAME} has a new digital platform, a cloud platform, and a cybersecurity capability "
        "across its operations, according to a company profile page."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert not _promoted(mappings, "cloud_ai_infrastructure")
    assert not _promoted(mappings, "cybersecurity_resilience")


def test_existing_capability_context_guard_remains_intact():
    """Item 16: a stale/current-state-only capability description (no
    change/current signal) must still classify as EXISTING_CAPABILITY_
    CONTEXT and must still not, by itself, create an opportunity --
    unaffected by this fix.
    """
    content = (
        f"{ORG_NAME} is equipped with modern systems including a security operations center and "
        "zero trust architecture across its facilities."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    m = next(mp for mp in mappings if mp.capability_id == "cybersecurity_resilience")
    assert m.evidence_category == EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value
    opportunities = build_opportunity_hypotheses(mappings)
    assert "cybersecurity_resilience" not in {o.capability_id for o in opportunities}


def test_capability_only_evidence_with_government_wide_language_creates_no_opportunity():
    """Item 17: the Oman Airports scenario end-to-end -- capability-only
    evidence, organization named only within a government-wide collective
    statement, still cannot independently create an opportunity for any
    of the three capabilities that fanned out in the live crawl.
    """
    content = (
        f"{ORG_NAME} is among the government entities progressing a digital transformation "
        "programme across the whole of government, spanning cloud, cybersecurity and managed "
        "services investments."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    opportunities = build_opportunity_hypotheses(mappings)
    opportunity_ids = {o.capability_id for o in opportunities}
    assert "cloud_ai_infrastructure" not in opportunity_ids
    assert "cybersecurity_resilience" not in opportunity_ids
    assert "enterprise_managed_services" not in opportunity_ids
def test_existing_capability_context_does_not_inflate_opportunity_diversity():
    """Existing-capability evidence must not inflate displayed opportunity corroboration."""
    current_content = (
        f"{ORG_NAME} is pursuing a digital transformation programme and is expanding "
        "its cybersecurity platform this quarter, strengthening governance across its "
        "own operations."
    )
    existing_content = (
        f"{ORG_NAME} is equipped with modern systems including a security operations "
        "center and zero trust architecture across its facilities."
    )

    current = _source(
        url="https://current-signal-example.com/cybersecurity",
        content=current_content,
    )
    existing = _source(
        url="https://existing-capability-example.com/security",
        content=existing_content,
    )

    mappings = map_all_sources(
        [current, existing],
        organization_name=ORG_NAME,
        country=COUNTRY,
    )

    opportunities = build_opportunity_hypotheses(mappings)
    cybersecurity = {
        o.capability_id: o for o in opportunities
    }["cybersecurity_resilience"]

    assert cybersecurity.independent_domain_count == 1
    assert cybersecurity.inference_cluster_count == 1
def test_bare_awards_programme_does_not_create_technology_signal():
    """A programme mention in an awards/recognition context is not a technology event."""
    content = (
        f"{ORG_NAME} received recognition in the World Travel Awards Middle East "
        "programme for its achievements and service quality."
    )

    source = _source(content=content)
    signals = extract_signals(source)

    assert not any(
        signal.signal_type in {"project", "technology_initiative"}
        for signal in signals
    )


# ---------------------------------------------------------------------------
# Oman Airports live-finding follow-up -- correlating-only signal types
# (expansion/project) must not independently ground Rule 4 (generic-trigger)
# or Rule 2/3's single-direct-hit fallback without local, capability-specific
# technology-context corroboration. Proposed fix under investigation; these
# tests are written against CURRENT production code and are expected to
# document the bug (tests 1 and 5 below) until the corroboration-based fix
# lands.
# ---------------------------------------------------------------------------

def test_generic_strategy_article_with_uncorroborated_expansion_signal_does_not_promote_enterprise_managed_services():
    """Confirmed Oman Airports mechanism: generic trigger + expansion,
    with NO capability-specific technology vocabulary anywhere nearby.
    Must not promote, must not produce an opportunity.
    """
    content = (
        f"{ORG_NAME}'s strategic plan calls for full implementation across its regional network, "
        "as part of broader economic growth. This expansion effort will support jobs and "
        "connectivity across the country."
    )
    s = _source(url="https://example-news.com/strategy-piece", content=content, source_type="linkedin_company")
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert not _promoted(mappings, "enterprise_managed_services")
    opportunities = build_opportunity_hypotheses(mappings)
    assert "enterprise_managed_services" not in {o.capability_id for o in opportunities}


def test_genuine_technology_initiative_signal_still_promotes_enterprise_managed_services():
    content = (
        f"{ORG_NAME} is beginning implementation this quarter, while separately upgrading its core "
        "network infrastructure as part of ongoing operations."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    promoted = _promoted(mappings, "enterprise_managed_services")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert promoted[0].evidence_category == EvidenceCategory.BUSINESS_SIGNAL_INFERENCE.value


def test_genuine_procurement_signal_still_promotes_enterprise_managed_services():
    content = f"{ORG_NAME} issued a tender covering implementation across its facilities."
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    promoted = _promoted(mappings, "enterprise_managed_services")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert promoted[0].evidence_category == EvidenceCategory.BUSINESS_SIGNAL_INFERENCE.value


def test_genuine_capacity_change_signal_still_promotes_enterprise_managed_services():
    content = (
        f"{ORG_NAME} is scaling up its operations, with implementation continuing across its "
        "network this quarter."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    promoted = _promoted(mappings, "enterprise_managed_services")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert promoted[0].evidence_category == EvidenceCategory.BUSINESS_SIGNAL_INFERENCE.value


def test_single_keyword_with_uncorroborated_expansion_signal_cannot_reach_strong_inference():
    """Rule 2/3's identical exposure: one specific keyword + expansion,
    with no OTHER capability-specific term nearby (the keyword that got us
    here does not count as its own corroboration).
    """
    content = (
        f"{ORG_NAME} completed a platform migration this year, ahead of planned network "
        "expansion across its facilities."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    assert not _promoted(mappings, "enterprise_managed_services")


def test_expansion_signal_with_explicit_technology_context_still_promotes():
    """The module's own canonical documented example
    (_signal_locally_associated_with_trigger's docstring). Proves the
    proposed corroboration-based fix should not over-suppress: 'network'
    (network_connectivity's own weak-signal term) sitting next to the
    expansion signal is exactly the corroboration the fix is designed to
    accept.
    """
    content = f"{ORG_NAME} is opening a new facility. This expansion will require additional network capacity."
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    promoted = _promoted(mappings, "network_connectivity")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert promoted[0].evidence_category == EvidenceCategory.BUSINESS_SIGNAL_INFERENCE.value


def test_existing_capability_evidence_alone_still_cannot_create_opportunity():
    content = (
        f"{ORG_NAME} has systems integration and managed operations capabilities across its "
        "enterprise IT estate."
    )
    s = _source(content=content)
    mappings = map_source_to_capabilities(s, organization_name=ORG_NAME, country=COUNTRY)
    promoted = _promoted(mappings, "enterprise_managed_services")
    assert promoted
    assert promoted[0].evidence_level == EvidenceLevel.STRONG_INFERENCE
    assert promoted[0].evidence_category == EvidenceCategory.EXISTING_CAPABILITY_CONTEXT.value
    opportunities = build_opportunity_hypotheses(mappings)
    assert "enterprise_managed_services" not in {o.capability_id for o in opportunities}

"""Golden Case Validation Set -- the objective acceptance test for the
evidence -> opportunity pipeline.

Every case asserts the ACTUAL opportunity list produced by
`build_opportunity_hypotheses` (what a user sees), not merely the absence
of DIRECT_EVIDENCE at mapping level. Each case also asserts that generic
or vendor-authored attribution can never produce a confirmed customer
requirement, and that any opportunity that does appear carries the
"sales hypothesis, not confirmation" presentation.

Two kinds of fixtures are used for the real-world cases (1-3):

  * REPLICAS (tests/fixtures/golden/replicas/*.txt) -- original-wording
    reproductions of the structural features of each real page that drive
    the pipeline (literal customer mention in a case-study sentence,
    punctuation-sparse checklists, an unpunctuated heading that merges with
    the next sentence, trigger and signal phrases, trap words). They are NOT
    copies of the third-party text. They always run.
  * SNAPSHOTS (tests/fixtures/golden/snapshots/*.txt) -- the exact text the
    pipeline's own research.fetcher.fetch_page() returns for the real URL,
    captured once by tests/fixtures/golden/capture_snapshots.py so tests
    never touch the live web. Tests that use them are skipped until the
    snapshots have been captured.

KNOWN-GAP TESTS: the G1 and G3 tests encode the required product behavior
and currently FAIL on purpose -- they are the acceptance gate for the
evidence-attribution fix and must not be weakened to pass. See README.md.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from commercial.evidence_mapping import map_source_to_capabilities
from commercial.models import EvidenceLevel
from commercial.opportunities import build_opportunity_hypotheses
from research.sources import AccessStatus, Source

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "golden"
REPLICA_DIR = GOLDEN_DIR / "replicas"
SNAPSHOT_DIR = GOLDEN_DIR / "snapshots"

TARGET_ORG = "Oman Air"
TARGET_COUNTRY = "Oman"
GENERIC_ORG = "Verimark National Airports Authority"
GENERIC_COUNTRY = "Verimark"

DISCLAIMER = "not confirmation of a buying requirement"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    # Normalise newlines so results are identical on LF and CRLF checkouts.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()


def _snapshot(name: str) -> str:
    path = SNAPSHOT_DIR / f"{name}.txt"
    if not path.exists():
        pytest.skip(
            f"real-page snapshot {name!r} has not been captured yet -- run: "
            "py tests/fixtures/golden/capture_snapshots.py"
        )
    return _read(path)


def _evaluate(text: str, *, source_type: str, org: str, country: str):
    source = Source(
        url="https://golden.example/source",
        domain="golden.example",
        title="",
        source_type=source_type,
        snippet="",
        discovered_via_query="golden",
        discovery_provider="golden",
        family="golden",
        content=text,
        access_status=AccessStatus.FETCHED,
    )
    mappings = map_source_to_capabilities(source, organization_name=org, country=country)
    return mappings, build_opportunity_hypotheses(mappings)


def _signature(opps) -> list[tuple]:
    return [(o.capability_id, o.priority, o.score, o.evidence_level, o.epistemic_status) for o in opps]


def _assert_presentation(opps) -> None:
    for o in opps:
        assert DISCLAIMER in o.hypothesis, f"{o.capability_id}: hypothesis must state it is not a confirmed requirement"


def _assert_no_confirmed_customer_requirement(mappings, opps) -> None:
    direct = [(m.capability_id, m.matched_terms) for m in mappings if m.evidence_level == EvidenceLevel.DIRECT_EVIDENCE]
    assert not direct, f"generic/vendor attribution produced DIRECT_EVIDENCE: {direct}"
    confirmed = [o.capability_id for o in opps if o.epistemic_status == "confirmed_evidence"]
    assert not confirmed, f"generic/vendor attribution produced confirmed_evidence opportunities: {confirmed}"


def _assert_zero_opportunities(mappings, opps) -> None:
    assert opps == [], f"expected ZERO commercial opportunities, got {_signature(opps)}"
    _assert_no_confirmed_customer_requirement(mappings, opps)


def _assert_only_watch_inference_ems(mappings, opps) -> None:
    """Case 3: Enterprise Managed Services MAY appear, but only as a WATCH
    inference hypothesis -- never confirmed evidence, never anything else.
    """
    _assert_no_confirmed_customer_requirement(mappings, opps)
    assert len(opps) <= 1, f"expected at most one opportunity, got {_signature(opps)}"
    for o in opps:
        assert (o.capability_id, o.priority, o.evidence_level, o.epistemic_status) == (
            "enterprise_managed_services", "watch", "strong_inference", "inference_hypothesis",
        ), f"unexpected opportunity for a fleet-expansion source: {_signature([o])}"
    _assert_presentation(opps)


# ---------------------------------------------------------------------------
# Case 1 -- generic vendor/marketing article (real Aufait RFP article)
# Expected: ZERO commercial opportunities for the target organization.
# ---------------------------------------------------------------------------

def test_golden_case_1_aufait_replica_yields_zero_opportunities():
    """Replica keeps the worst-case structure: the vendor's case-study
    sentence names the organization literally and sits directly next to the
    generic compliance checklist; a 'fair' trap word is present too.
    """
    mappings, opps = _evaluate(
        _read(REPLICA_DIR / "aufait_rfp_article.txt"),
        source_type="technology", org=TARGET_ORG, country=TARGET_COUNTRY,
    )
    _assert_zero_opportunities(mappings, opps)


def test_golden_case_1_aufait_real_snapshot_yields_zero_opportunities():
    mappings, opps = _evaluate(
        _snapshot("aufait_rfp_article"),
        source_type="technology", org=TARGET_ORG, country=TARGET_COUNTRY,
    )
    _assert_zero_opportunities(mappings, opps)


# ---------------------------------------------------------------------------
# Case 2 -- vendor/partner press release unrelated to the target customer
# (real Zawya Otech/Kaspersky release). Expected: ZERO opportunities.
# ---------------------------------------------------------------------------

def test_golden_case_2_zawya_replica_yields_zero_opportunities():
    mappings, opps = _evaluate(
        _read(REPLICA_DIR / "zawya_otech_kaspersky.txt"),
        source_type="technology", org=TARGET_ORG, country=TARGET_COUNTRY,
    )
    _assert_zero_opportunities(mappings, opps)


def test_golden_case_2_zawya_real_snapshot_yields_zero_opportunities():
    mappings, opps = _evaluate(
        _snapshot("zawya_otech_kaspersky"),
        source_type="technology", org=TARGET_ORG, country=TARGET_COUNTRY,
    )
    _assert_zero_opportunities(mappings, opps)


# ---------------------------------------------------------------------------
# Case 3 -- genuine customer capacity/expansion event (real Oman Air fleet
# expansion article). EMS may appear ONLY as a WATCH inference.
# ---------------------------------------------------------------------------

def test_golden_case_3_fleet_expansion_replica_yields_only_a_watch_inference_hypothesis():
    """Frozen exactly: on the replica the Enterprise Managed Services WATCH is
    produced by the unique trigger 'modernization' plus a capacity_change
    signal ('increased capacity', meaning aircraft seats) at medium trust:
    45 base + 5 trust + 5 signal = 55 confidence -> 38.5 score. This equals
    the result reported for the live article.
    """
    mappings, opps = _evaluate(
        _read(REPLICA_DIR / "oman_air_fleet_expansion.txt"),
        source_type="news", org=TARGET_ORG, country=TARGET_COUNTRY,
    )
    assert _signature(opps) == [
        ("enterprise_managed_services", "watch", 38.5, "strong_inference", "inference_hypothesis"),
    ]
    _assert_only_watch_inference_ems(mappings, opps)


def test_golden_case_3_fleet_expansion_real_snapshot_yields_only_a_watch_inference_hypothesis():
    mappings, opps = _evaluate(
        _snapshot("oman_air_fleet_expansion"),
        source_type="news", org=TARGET_ORG, country=TARGET_COUNTRY,
    )
    _assert_only_watch_inference_ems(mappings, opps)


# ---------------------------------------------------------------------------
# Case 4 -- genuine customer-specific technology initiative.
# Strongest existing fixture: the 'locally associated technology
# announcement' text. DIRECT_EVIDENCE is permitted because attribution and
# local technology evidence are both satisfied.
# ---------------------------------------------------------------------------

_TECH_INITIATIVE = (
    "{org} today confirmed a major cloud migration initiative. This technology upgrade "
    "will modernize its core operational systems over the next 18 months."
)


def test_golden_case_4_customer_technology_initiative_opportunity_list():
    mappings, opps = _evaluate(
        _TECH_INITIATIVE.format(org=GENERIC_ORG),
        source_type="official", org=GENERIC_ORG, country=GENERIC_COUNTRY,
    )
    assert _signature(opps) == [
        ("cloud_ai_infrastructure", "medium", 61.0, "direct_evidence", "confirmed_evidence"),
        ("enterprise_managed_services", "watch", 42.0, "strong_inference", "inference_hypothesis"),
    ]
    _assert_presentation(opps)


def test_golden_case_4_attribution_twin_another_organizations_initiative_yields_zero():
    """Identical wording, but the initiative belongs to a different
    organization: attribution is not satisfied, so nothing may be promoted.
    """
    mappings, opps = _evaluate(
        _TECH_INITIATIVE.format(org="Castellane Bank"),
        source_type="official", org=GENERIC_ORG, country=GENERIC_COUNTRY,
    )
    _assert_zero_opportunities(mappings, opps)


# ---------------------------------------------------------------------------
# Case 5 -- genuine procurement/RFP requirement.
# Strongest existing fixture: the RFP-for-storage-infrastructure text, here
# with the organization named (the original fixture ran without one).
# ---------------------------------------------------------------------------

_RFP = (
    "{org} has issued a request for proposal (RFP) for storage infrastructure "
    "to support expanded compute capacity."
)


def test_golden_case_5_customer_procurement_rfp_opportunity_list():
    mappings, opps = _evaluate(
        _RFP.format(org=GENERIC_ORG),
        source_type="procurement", org=GENERIC_ORG, country=GENERIC_COUNTRY,
    )
    assert _signature(opps) == [
        ("data_centre_infrastructure", "medium", 64.5, "direct_evidence", "confirmed_evidence"),
    ]
    _assert_presentation(opps)


def test_golden_case_5_attribution_twin_another_organizations_rfp_yields_zero():
    mappings, opps = _evaluate(
        _RFP.format(org="Castellane Bank"),
        source_type="procurement", org=GENERIC_ORG, country=GENERIC_COUNTRY,
    )
    _assert_zero_opportunities(mappings, opps)


# ---------------------------------------------------------------------------
# G1 (KNOWN GAP -- currently FAILS) -- vendor page listing the organization
# as a customer, followed by generic cybersecurity/compliance language.
# Expected: ZERO commercial opportunities. The phrase is not covered by any
# vendor-case-study marker, so today the page is promoted to
# cybersecurity_resilience DIRECT_EVIDENCE (85 confidence, score 64.5,
# confirmed_evidence) -- the original Aufait false-positive signature.
# ---------------------------------------------------------------------------

_G1_COMPLIANCE_TAIL = (
    "Regulatory compliance tracking (ISO, SOC 2, GDPR, etc.) Implements role-based "
    "access controls and audit trails for every approval."
)


@pytest.mark.parametrize(
    ("org", "country"),
    [(TARGET_ORG, TARGET_COUNTRY), (GENERIC_ORG, GENERIC_COUNTRY)],
    ids=["single-token-org-after-country-strip", "multi-token-org"],
)
def test_golden_g1_customers_such_as_phrase_on_vendor_page_yields_zero_opportunities(org, country):
    text = f"Trusted by customers such as {org}. {_G1_COMPLIANCE_TAIL}"
    mappings, opps = _evaluate(text, source_type="technology", org=org, country=country)
    _assert_zero_opportunities(mappings, opps)


# ---------------------------------------------------------------------------
# G3 (KNOWN GAP -- currently FAILS) -- product decision: a partnership/MOU
# in which the organization IS genuinely named, whose only technology
# association is the homonym keyword 'transport network' (a route network,
# not a telecom transport network), is retained as WATCH/context only and
# must NOT create a commercial opportunity. Today it creates a
# network_connectivity WATCH opportunity (score 42.0) via a partnership
# signal, which the corroboration gate does not cover.
# ---------------------------------------------------------------------------

_G3_MOU = (
    "Oman Air and a regional operator signed a Memorandum of Understanding to strengthen "
    "cooperation in transport and airport management, covering knowledge sharing, joint "
    "working groups and future collaboration across their transport network of operations."
)


def test_golden_g3_partnership_mou_with_homonym_keyword_creates_no_opportunity():
    mappings, opps = _evaluate(_G3_MOU, source_type="official", org=TARGET_ORG, country=TARGET_COUNTRY)
    _assert_zero_opportunities(mappings, opps)


def test_golden_g3_partnership_mou_with_homonym_keyword_is_retained_as_weak_context():
    """'Retain as context' means the keyword evidence is still recorded, but
    only at weak_relevance -- never strong_inference or direct_evidence.
    """
    mappings, _ = _evaluate(_G3_MOU, source_type="official", org=TARGET_ORG, country=TARGET_COUNTRY)
    network = [m for m in mappings if m.capability_id == "network_connectivity"]
    assert network, "the homonym keyword evidence should be retained as context, not dropped"
    assert [m.evidence_level for m in network] == [EvidenceLevel.WEAK_RELEVANCE] * len(network), (
        f"context-only evidence was promoted: {[(m.evidence_level.value, m.signal_type) for m in network]}"
    )


# ---------------------------------------------------------------------------
# Fixture integrity -- guards that keep the replicas structurally faithful
# to the real pages they stand in for, so an edit cannot silently defeat a
# case's purpose.
# ---------------------------------------------------------------------------

def test_golden_replicas_preserve_their_documented_structure():
    aufait = _read(REPLICA_DIR / "aufait_rfp_article.txt").lower()
    assert "we helped oman air" in aufait          # literal customer mention in a case-study sentence
    assert "regulatory compliance" in aufait and "soc 2" in aufait   # the two generic keywords
    assert "implements role-based access controls" in aufait          # technology_initiative signal wording
    assert " fair " in aufait                       # substring-trap word for the single-token org name

    zawya = _read(REPLICA_DIR / "zawya_otech_kaspersky.txt").lower()
    assert "oman air" not in zawya                  # the page never names the target organization
    assert "expanding" in zawya and "chairman" in zawya

    fleet = _read(REPLICA_DIR / "oman_air_fleet_expansion.txt").lower()
    assert "oman air" in fleet                      # the target organization is named
    assert "modernization" in fleet                 # EMS-unique generic trigger
    assert "increased capacity" in fleet            # capacity_change signal phrase (aircraft seats)


def _load_capture_module():
    spec = importlib.util.spec_from_file_location("golden_capture_snapshots", GOLDEN_DIR / "capture_snapshots.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_capture_tool_is_deterministic_and_never_overwrites(tmp_path):
    capture = _load_capture_module()
    calls: list[str] = []

    def fake_fetch(url):
        calls.append(url)
        return f"page text for {url}", AccessStatus.FETCHED

    targets = {"one": "https://example.test/one"}
    first = capture.capture(fetch=fake_fetch, out_dir=tmp_path, targets=targets)
    assert first == {"one": "captured"}
    assert (tmp_path / "one.txt").read_text(encoding="utf-8") == "page text for https://example.test/one"
    meta = json.loads((tmp_path / "one.meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == "https://example.test/one"
    assert meta["length"] == len("page text for https://example.test/one")
    assert len(meta["sha256"]) == 64

    # A second run must neither refetch nor overwrite (fixtures stay frozen).
    second = capture.capture(fetch=fake_fetch, out_dir=tmp_path, targets=targets)
    assert second == {"one": "skipped_existing"}
    assert calls == ["https://example.test/one"]

    # A failed fetch must never write a snapshot.
    bad_dir = tmp_path / "bad"
    result = capture.capture(fetch=lambda url: ("", AccessStatus.UNREACHABLE), out_dir=bad_dir, targets=targets)
    assert result["one"].startswith("failed")
    assert not (bad_dir / "one.txt").exists()

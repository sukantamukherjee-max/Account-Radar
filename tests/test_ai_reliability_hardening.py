"""AI synthesis reliability hardening (approved fix, following the
PHASE_2_AI_SYNTHESIS_DIAGNOSTIC_REPORT.md diagnosis).

Covers, all without any live network/LLM call:
  - the evidence-block prompt no longer grows without bound and clearly
    separates citable ("ELIGIBLE TO CITE") from context-only
    ("CONTEXT ONLY -- DO NOT CITE") evidence, while every eligible
    (direct_evidence/strong_inference) mapping remains fully represented
  - max_tokens scales with prompt/corpus size within a fixed floor/ceiling
  - AIResponse.ok requires a JSON object, so a valid-but-wrong-shaped
    response from the primary model is not silently accepted and the
    existing primary -> fallback model mechanism actually runs
  - synthesize_account's bounded (exactly-one) schema-repair retry
  - grounding is completely unaffected by any of the above: it still
    validates every citation against the COMPLETE deterministic report,
    not merely what is shown in the (now-capped) prompt

No organization/sector/country-specific logic anywhere in this file or in
the code it exercises -- ORG_NAME is a placeholder used purely as test
data, mirroring tests/test_ai_synthesis.py's ORG_NAME convention.
"""

from __future__ import annotations

from ai.client import (
    _BASE_MAX_TOKENS,
    _MAX_TOKENS_CEILING,
    _estimate_max_tokens,
    AIResponse,
    OpenRouterAIClient,
)
from ai.models import SynthesisStatus
from ai.prompt import _MAX_CONTEXT_ONLY_LINES_PER_CAPABILITY, build_synthesis_prompt
from ai.synthesis import synthesize_account
from commercial.models import CapabilityMapping, EvidenceLevel, epistemic_status_for
from research.analysis.qualification import QualificationResult
from research.analysis.report import AccountRadarReport
from tests.test_ai_synthesis import (
    FakeAIClient,
    _qualified_report,
    _real_direct_evidence_capability_and_source,
)

ORG_NAME = "Synthetic Test Holdings"


# ---------------------------------------------------------------------------
# Synthetic large-corpus report builder (no acquisition/search pipeline
# needed -- CapabilityMapping objects are constructed directly, the same
# way commercial/evidence_mapping.py itself would have produced them).
# ---------------------------------------------------------------------------

_EVIDENCE_CATEGORY_BY_LEVEL = {
    EvidenceLevel.DIRECT_EVIDENCE: "organizational_technology_event",
    EvidenceLevel.STRONG_INFERENCE: "business_signal_inference",
    EvidenceLevel.WEAK_RELEVANCE: "incidental_mention",
}


def _mapping(capability_id: str, capability_name: str, level: EvidenceLevel, url: str) -> CapabilityMapping:
    return CapabilityMapping(
        capability_id=capability_id,
        capability_name=capability_name,
        evidence_level=level,
        matched_terms=["synthetic term"],
        source_url=url,
        source_title="Synthetic source",
        confidence=70.0,
        supporting_context="Synthetic supporting context sentence used for reliability-hardening tests.",
        business_impact="Synthetic business impact.",
        technology_requirement="Synthetic technology requirement.",
        evidence_category=_EVIDENCE_CATEGORY_BY_LEVEL[level],
        inference_cluster_id=f"cluster-{url}",
        epistemic_status=epistemic_status_for(level).value,
    )


def _large_report(
    *, capabilities: int = 5, direct_per_cap: int = 3, strong_per_cap: int = 2, weak_per_cap: int = 9
) -> AccountRadarReport:
    mappings: list[CapabilityMapping] = []
    for c in range(capabilities):
        cap_id = f"capability_{c}"
        cap_name = f"Capability {c}"
        for i in range(direct_per_cap):
            mappings.append(_mapping(cap_id, cap_name, EvidenceLevel.DIRECT_EVIDENCE, f"https://example.test/cap{c}/direct/{i}"))
        for i in range(strong_per_cap):
            mappings.append(_mapping(cap_id, cap_name, EvidenceLevel.STRONG_INFERENCE, f"https://example.test/cap{c}/strong/{i}"))
        for i in range(weak_per_cap):
            mappings.append(_mapping(cap_id, cap_name, EvidenceLevel.WEAK_RELEVANCE, f"https://example.test/cap{c}/weak/{i}"))
    return AccountRadarReport(
        organization_name=ORG_NAME,
        country="Nordholm",
        website="https://example.test",
        linkedin_company_url=None,
        qualification=QualificationResult(),  # defaults to corpus_status="qualified"
        capability_mappings=mappings,
    )


# ---------------------------------------------------------------------------
# 1. Prompt: eligible evidence uncapped, weak evidence capped + separated
# ---------------------------------------------------------------------------

def test_capability_evidence_block_separates_eligible_and_context_only():
    report = _large_report(capabilities=1, direct_per_cap=2, strong_per_cap=1, weak_per_cap=20)
    prompt = build_synthesis_prompt(report)

    assert "ELIGIBLE TO CITE" in prompt
    assert "CONTEXT ONLY -- DO NOT CITE" in prompt

    for m in report.capability_mappings:
        if m.evidence_level in (EvidenceLevel.DIRECT_EVIDENCE, EvidenceLevel.STRONG_INFERENCE):
            assert m.source_url in prompt, "eligible evidence must never be capped out of the prompt"

    weak_urls = [m.source_url for m in report.capability_mappings if m.evidence_level == EvidenceLevel.WEAK_RELEVANCE]
    shown_weak = [u for u in weak_urls if u in prompt]
    assert len(shown_weak) == _MAX_CONTEXT_ONLY_LINES_PER_CAPABILITY
    assert "more weak_relevance item(s) not shown" in prompt


def test_all_eligible_evidence_remains_represented_at_large_account_scale():
    """Standard-Chartered-like scale: 5 capabilities x (3 direct + 2 strong
    + 9 weak) = 70 mappings. Every direct_evidence/strong_inference mapping
    must still appear verbatim, regardless of corpus size.
    """
    report = _large_report(capabilities=5, direct_per_cap=3, strong_per_cap=2, weak_per_cap=9)
    prompt = build_synthesis_prompt(report)
    eligible = [m for m in report.capability_mappings if m.evidence_level in (EvidenceLevel.DIRECT_EVIDENCE, EvidenceLevel.STRONG_INFERENCE)]
    assert len(eligible) == 5 * (3 + 2)
    for m in eligible:
        assert m.source_url in prompt


def test_prompt_size_stays_bounded_as_weak_evidence_volume_grows():
    modest = _large_report(capabilities=5, direct_per_cap=3, strong_per_cap=2, weak_per_cap=9)
    huge = _large_report(capabilities=5, direct_per_cap=3, strong_per_cap=2, weak_per_cap=200)
    prompt_modest = build_synthesis_prompt(modest)
    prompt_huge = build_synthesis_prompt(huge)
    # weak_relevance volume grew >20x; the prompt must not grow anywhere
    # near proportionally, because it is capped per capability.
    assert len(prompt_huge) < len(prompt_modest) * 1.5


# ---------------------------------------------------------------------------
# 2. max_tokens scales with corpus size, within a fixed floor/ceiling
# ---------------------------------------------------------------------------

def test_estimate_max_tokens_scales_within_bounds():
    assert _estimate_max_tokens("") == _BASE_MAX_TOKENS

    small_prompt = build_synthesis_prompt(_large_report(capabilities=1, direct_per_cap=1, strong_per_cap=0, weak_per_cap=1))
    large_prompt = build_synthesis_prompt(_large_report(capabilities=5, direct_per_cap=3, strong_per_cap=2, weak_per_cap=9))
    enormous_prompt = build_synthesis_prompt(_large_report(capabilities=20, direct_per_cap=10, strong_per_cap=10, weak_per_cap=50))

    small_budget = _estimate_max_tokens(small_prompt)
    large_budget = _estimate_max_tokens(large_prompt)
    enormous_budget = _estimate_max_tokens(enormous_prompt)

    assert _BASE_MAX_TOKENS <= small_budget <= _MAX_TOKENS_CEILING
    assert _BASE_MAX_TOKENS <= large_budget <= _MAX_TOKENS_CEILING
    assert _BASE_MAX_TOKENS <= enormous_budget <= _MAX_TOKENS_CEILING
    assert small_budget < large_budget
    assert enormous_budget <= _MAX_TOKENS_CEILING  # bounded, never unlimited growth


class _FakeChatCompletions:
    def __init__(self, capture: list[dict]):
        self._capture = capture

    def create(self, **kwargs):
        self._capture.append(kwargs)

        class _Msg:
            content = (
                '{"account_narrative": "x", "capability_narratives": [], "next_best_actions": []}'
            )

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]
            usage = None

        return _Resp()


class _FakeChat:
    def __init__(self, capture: list[dict]):
        self.completions = _FakeChatCompletions(capture)


class _FakeOpenAI:
    def __init__(self, capture: list[dict]):
        self.chat = _FakeChat(capture)


def test_openrouter_client_sends_prompt_size_scaled_max_tokens_to_the_api():
    """Captures the actual kwargs sent to the (faked) OpenAI-compatible
    client and proves max_tokens scales with prompt size, entirely without
    a real network call.
    """
    capture: list[dict] = []
    client = OpenRouterAIClient(api_key="test-key", fallback_model=None)
    client._client = _FakeOpenAI(capture)  # bypass real network/transport entirely

    small_prompt = "a short synthetic prompt with minimal evidence"
    large_report = _large_report(capabilities=5, direct_per_cap=3, strong_per_cap=2, weak_per_cap=9)
    large_prompt = build_synthesis_prompt(large_report)

    client.complete_json(small_prompt, schema_name="synthesis_v1")
    client.complete_json(large_prompt, schema_name="synthesis_v1")

    assert len(capture) == 2
    assert capture[0]["max_tokens"] < capture[1]["max_tokens"]
    assert all(_BASE_MAX_TOKENS <= c["max_tokens"] <= _MAX_TOKENS_CEILING for c in capture)


# ---------------------------------------------------------------------------
# 3. AIResponse.ok requires a JSON object; fallback model actually runs
# ---------------------------------------------------------------------------

def test_ai_response_ok_requires_dict_parsed_json():
    assert AIResponse(parsed_json={"a": 1}).ok is True
    assert AIResponse(parsed_json=["a", "b"]).ok is False
    assert AIResponse(parsed_json="a bare string").ok is False
    assert AIResponse(parsed_json=123).ok is False
    assert AIResponse(parsed_json=True).ok is False
    assert AIResponse(parsed_json=None).ok is False
    assert AIResponse(parsed_json={"a": 1}, error="transport error").ok is False


class _ScriptedClient(OpenRouterAIClient):
    """OpenRouterAIClient subclass with _try_model scripted per-model, so
    the primary -> fallback loop in complete_json() can be exercised
    without any real network call.
    """

    def __init__(self, responses_by_model: dict[str, AIResponse], **kwargs):
        super().__init__(api_key="test-key", **kwargs)
        self._responses_by_model = responses_by_model
        self.calls: list[str] = []

    def _try_model(self, model: str, prompt: str) -> AIResponse:  # noqa: D401 - override
        self.calls.append(model)
        return self._responses_by_model[model]


def test_primary_model_non_dict_response_triggers_fallback_model():
    primary, fallback = "primary-model", "fallback-model"
    valid_payload = {"account_narrative": "x", "capability_narratives": [], "next_best_actions": []}
    client = _ScriptedClient(
        {
            primary: AIResponse(model=primary, parsed_json=["not", "a", "dict"]),
            fallback: AIResponse(model=fallback, parsed_json=valid_payload),
        },
        model=primary,
        fallback_model=fallback,
    )
    result = client.complete_json("prompt", schema_name="synthesis_v1")
    assert result.ok is True
    assert result.model == fallback
    assert client.calls == [primary, fallback]


def test_both_models_returning_non_dict_json_leaves_response_not_ok():
    primary, fallback = "primary-model", "fallback-model"
    client = _ScriptedClient(
        {
            primary: AIResponse(model=primary, parsed_json=["not", "a", "dict"]),
            fallback: AIResponse(model=fallback, parsed_json="also not a dict"),
        },
        model=primary,
        fallback_model=fallback,
    )
    result = client.complete_json("prompt", schema_name="synthesis_v1")
    assert result.ok is False
    assert client.calls == [primary, fallback]


# ---------------------------------------------------------------------------
# 4. Bounded (exactly-one) schema-repair retry
# ---------------------------------------------------------------------------

def test_schema_repair_retry_succeeds_on_second_attempt():
    report = _qualified_report()
    cap_id, url = _real_direct_evidence_capability_and_source(report)
    bad_first = AIResponse(parsed_json=["not", "an", "object"])
    good_second = AIResponse(
        parsed_json={
            "account_narrative": "Recovered after one bounded repair retry.",
            "capability_narratives": [{"capability_id": cap_id, "narrative": "n", "cited_source_urls": [url]}],
            "next_best_actions": [],
        },
        model="repair-model",
    )
    client = FakeAIClient(responses=[bad_first, good_second])
    result = synthesize_account(report, client)

    assert client.call_count == 2
    assert result.status in (SynthesisStatus.OK, SynthesisStatus.DEGRADED)
    assert result.capability_narratives
    assert result.capability_narratives[0].cited_source_urls == [url]
    assert any("schema-repair retry" in n for n in result.notes)
    assert "CORRECTION REQUIRED" in client.prompts[1]


def test_schema_repair_retry_fails_both_attempts_remains_ai_failed():
    report = _qualified_report()
    client = FakeAIClient(responses=[AIResponse(parsed_json=["bad", "1"]), AIResponse(parsed_json=["still", "bad"])])
    result = synthesize_account(report, client)

    assert client.call_count == 2
    assert result.status == SynthesisStatus.AI_FAILED
    assert result.capability_narratives == []
    assert result.next_best_actions == []


def test_schema_repair_retry_is_attempted_exactly_once():
    """A third, GOOD canned response is queued after two bad ones -- it
    must never be reached, proving the repair retry is bounded to exactly
    one attempt and the pipeline never keeps retrying until it gets a
    passable response.
    """
    report = _qualified_report()
    client = FakeAIClient(
        responses=[
            AIResponse(parsed_json=["bad", "1"]),
            AIResponse(parsed_json=["bad", "2"]),
            AIResponse(parsed_json={"account_narrative": "x", "capability_narratives": [], "next_best_actions": []}),
        ]
    )
    result = synthesize_account(report, client)

    assert client.call_count == 2  # the third canned (good) response is never consumed
    assert result.status == SynthesisStatus.AI_FAILED


def test_schema_repair_retry_not_attempted_for_transport_or_parse_failure():
    """A genuine transport/parse failure (error set, no parsed JSON at all)
    has nothing shape-related to correct -- it must go straight to
    ai_failed without spending the one bounded repair retry.
    """
    report = _qualified_report()
    client = FakeAIClient(responses=[AIResponse(error="simulated transport error")])
    result = synthesize_account(report, client)

    assert client.call_count == 1
    assert result.status == SynthesisStatus.AI_FAILED


# ---------------------------------------------------------------------------
# 5. Grounding is unaffected: still validates against the COMPLETE report,
#    never merely what is shown in the (now-capped) prompt
# ---------------------------------------------------------------------------

def test_weak_only_evidence_url_is_still_rejected_by_grounding_after_prompt_capping():
    report = _qualified_report()
    weak_only_ids = {
        m.capability_id for m in report.capability_mappings if m.evidence_level.value == "weak_relevance"
    } - {
        m.capability_id for m in report.capability_mappings if m.evidence_level.value in ("direct_evidence", "strong_inference")
    }
    assert weak_only_ids, "fixture must contain at least one weak-only capability"
    weak_cap_id = sorted(weak_only_ids)[0]
    weak_url = next(m.source_url for m in report.capability_mappings if m.capability_id == weak_cap_id)

    payload = {
        "account_narrative": "Summary.",
        "capability_narratives": [
            {"capability_id": weak_cap_id, "narrative": "Should be rejected.", "cited_source_urls": [weak_url]},
        ],
        "next_best_actions": [],
    }
    client = FakeAIClient(responses=[AIResponse(parsed_json=payload)])
    result = synthesize_account(report, client)

    assert result.capability_narratives == []
    assert any("no cited source survived grounding" in n for n in result.notes)


def test_grounding_rejects_weak_citation_even_when_omitted_from_the_capped_prompt():
    """The prompt now shows only a capped sample of weak_relevance evidence
    per capability. Grounding must still check citations against the FULL
    deterministic report.capability_mappings -- not just what happened to
    be shown in the prompt -- so a weak URL that was capped OUT of the
    prompt text entirely is still correctly rejected if cited.
    """
    report = _large_report(capabilities=1, direct_per_cap=1, strong_per_cap=0, weak_per_cap=20)
    prompt = build_synthesis_prompt(report)
    weak_mappings = [m for m in report.capability_mappings if m.evidence_level == EvidenceLevel.WEAK_RELEVANCE]
    omitted_weak_url = next(m.source_url for m in weak_mappings if m.source_url not in prompt)
    cap_id = report.capability_mappings[0].capability_id

    payload = {
        "account_narrative": "Summary.",
        "capability_narratives": [
            {"capability_id": cap_id, "narrative": "n", "cited_source_urls": [omitted_weak_url]},
        ],
        "next_best_actions": [],
    }
    client = FakeAIClient(responses=[AIResponse(parsed_json=payload)])
    result = synthesize_account(report, client)

    assert result.capability_narratives == []
    assert any("no cited source survived grounding" in n for n in result.notes)


def test_hallucinated_source_url_still_stripped_after_prompt_capping():
    """Regression guard mirroring tests/test_ai_synthesis.py's equivalent
    test: a mix of one real, eligible citation and one fabricated one must
    still have only the real one survive grounding.
    """
    report = _qualified_report()
    cap_id, real_url = _real_direct_evidence_capability_and_source(report)
    fake_url = "https://not-in-the-evidence-corpus.example/made-up-page"
    payload = {
        "account_narrative": "Summary.",
        "capability_narratives": [
            {"capability_id": cap_id, "narrative": "Mixed real and fake citation.", "cited_source_urls": [real_url, fake_url]},
        ],
        "next_best_actions": [],
    }
    client = FakeAIClient(responses=[AIResponse(parsed_json=payload)])
    result = synthesize_account(report, client)

    assert result.status == SynthesisStatus.DEGRADED
    assert result.capability_narratives[0].cited_source_urls == [real_url]
    assert fake_url not in result.capability_narratives[0].cited_source_urls

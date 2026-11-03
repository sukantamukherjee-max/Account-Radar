"""Offline tests for the standalone People funnel diagnostic
(_diagnostics/people_funnel_diagnostic.py).

Synthetic data only: in-memory fake providers, no network, no real API keys
(the 'secrets' below are fake sentinel strings used to prove they never reach
the output). The diagnostic is imported from its file path; production code
is untouched.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from research.linkedin import PEOPLE_MODE_ENV, PeopleDiscoveryConfig
from research.organization import ResolvedOrganization
from research.providers.base import QueryOutcome, RawResult

import streamlit as _REAL_STREAMLIT  # noqa: E402,F401  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("people_funnel_diagnostic", ROOT / "_diagnostics" / "people_funnel_diagnostic.py")
pfd = importlib.util.module_from_spec(_SPEC)
sys.modules["people_funnel_diagnostic"] = pfd
_SPEC.loader.exec_module(pfd)

ORG = "Ooredoo Oman"
COUNTRY = "Oman"
JIHAD_URL = "https://om.linkedin.com/in/dr-jihad-al-ansari-60b7a23b"
JIHAD_URL_WWW = "https://www.linkedin.com/in/dr-jihad-al-ansari-60b7a23b"
FAKE_BRAVE_KEY = "BRAVE-SENTINEL-KEY-0001"
FAKE_TAVILY_KEY = "TAVILY-SENTINEL-KEY-0002"


def _org():
    return ResolvedOrganization(name=ORG, country=COUNTRY, website=None, linkedin_company_url=None,
                                website_was_supplied=False, linkedin_was_supplied=False)


class FakeBrave:
    name = "brave"
    supports_pagination = True
    supports_rich_snippets = True

    def __init__(self, first_call_rows=(), *, every_call_rows=None):
        self.first = list(first_call_rows)
        self.every = every_call_rows
        self.calls: list[tuple[str, int]] = []

    def is_configured(self):
        return True

    def search(self, query, *, num_results=10, rich_snippets=False, page=0):
        self.calls.append((query, page))
        n = len(self.calls)
        if self.every is not None:
            rows = self.every(n)
        else:
            rows = self.first if n == 1 else []
        return QueryOutcome(query=query, provider=self.name, page=page, more_results_available=bool(self.every),
                            results=[RawResult(url=u, title=t, snippet=s, query=query, provider=self.name, rank=i)
                                     for i, (u, t, s) in enumerate(rows)])


class SpyTavily:
    """Any use at all is recorded. In brave_only nothing may touch it."""

    name = "tavily"

    def __init__(self):
        self.used: list[str] = []

    def is_configured(self):
        self.used.append("is_configured")
        return True

    def search(self, query, *, num_results=10, **kw):
        self.used.append(query)
        return QueryOutcome(query=query, provider=self.name)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(PEOPLE_MODE_ENV, raising=False)


CONFIRMED_ROW = (JIHAD_URL, "Dr. Jihad Al-Ansari - Ooredoo Oman",
                 "Chief Information & Technology Officer at Ooredoo Oman. Muscat, Oman")
CONFIRMED_ROW_DUP = (JIHAD_URL_WWW, "Dr. Jihad Al-Ansari - Ooredoo Oman | LinkedIn",
                     "Dr. Jihad Al-Ansari. CITO at Ooredoo Oman. Oman")
OTHER_CONFIRMED = ("https://www.linkedin.com/in/a-b", "A B - Network Engineer - Ooredoo Oman | LinkedIn",
                   "Network Engineer at Ooredoo Oman")
REJECTED_ROW = ("https://www.linkedin.com/in/c-d", "C D - Manager - Other Co | LinkedIn", "Manager at Other Co")
NON_PROFILE = ("https://example.com/news", "Ooredoo news", "A news item")


def _run(rows, **kw):
    return pfd.run_funnel(_org(), [FakeBrave(rows)], watch=["jihad"], **kw)


# ---------------------------------------------------------------------------
# Funnel: raw -> dedup -> confirmed / unverified / rejected -> final list
# ---------------------------------------------------------------------------

def test_funnel_counts_and_dedup_with_jihad_like_profile_confirmed():
    r = _run([CONFIRMED_ROW, CONFIRMED_ROW_DUP, OTHER_CONFIRMED, REJECTED_ROW, NON_PROFILE])
    assert r["funnel_counts"]["raw_results_all"] == 5
    assert r["funnel_counts"]["raw_profile_results"] == 4        # non-LinkedIn row excluded
    assert r["dedup"]["unique_profile_slugs"] == 3
    assert r["dedup"]["deduplicated_candidates"] == 3
    assert r["dedup"]["duplicate_results_collapsed"] == 1
    assert r["dedup"]["slugs_seen_more_than_once"] == {"dr-jihad-al-ansari-60b7a23b": 2}
    assert r["funnel_counts"]["confirmed"] == 2
    assert r["funnel_counts"]["rejected"] == 1
    assert r["funnel_counts"]["final_people_list"] == r["funnel_counts"]["confirmed"]
    assert JIHAD_URL in r["raw_profile_urls"] and JIHAD_URL_WWW in r["raw_profile_urls"]
    w = r["watch"]["jihad"]
    assert w["stage"] == "present in final People list"
    assert w["profiles"][0]["association_status"] == "confirmed_current"
    assert w["profiles"][0]["in_final_people_list"] is True
    assert len(w["raw_results"]) == 2


def test_jihad_like_profile_bare_brand_is_unverified_and_not_in_final_list():
    row = (JIHAD_URL, "Dr. Jihad Al-Ansari - Ooredoo", "Chief Information & Technology Officer at Ooredoo. Oman")
    r = _run([row, OTHER_CONFIRMED])
    p = next(p for p in r["profiles"] if p["slug"].startswith("dr-jihad"))
    assert p["association_status"] == "needs_verification"
    assert p["in_final_people_list"] is False
    assert p["final_people_list"].startswith("unverified")
    assert any("brand" in reason for reason in p["reasons"])
    assert r["funnel_counts"]["unverified"] == 1 and r["funnel_counts"]["final_people_list"] == 1
    assert r["watch"]["jihad"]["stage"].startswith("deduplicated candidate but NOT in final People list")
    assert "needs_verification" in r["watch"]["jihad"]["stage"]


def test_jihad_like_profile_sibling_entity_is_rejected_with_reason():
    row = (JIHAD_URL, "Dr. Jihad Al-Ansari - Ooredoo Group", "CITO at Ooredoo Group. Qatar")
    r = _run([row, OTHER_CONFIRMED])
    p = next(p for p in r["profiles"] if p["slug"].startswith("dr-jihad"))
    assert p["association_status"] == "rejected"
    assert p["in_final_people_list"] is False
    assert any("different entity" in reason for reason in p["reasons"])
    assert r["funnel_counts"]["rejected"] == 1
    assert [x["url"] for x in r["rejected"]] == [JIHAD_URL]


def test_jihad_like_profile_missing_from_provider_results_is_reported_as_discovery_miss():
    r = _run([OTHER_CONFIRMED, REJECTED_ROW])
    assert r["watch"]["jihad"]["stage"].startswith("NOT in raw provider results")
    assert r["watch"]["jihad"]["raw_results"] == [] and r["watch"]["jihad"]["profiles"] == []


def test_every_profile_records_status_reasons_queries_and_final_inclusion():
    r = _run([CONFIRMED_ROW, REJECTED_ROW])
    for p in r["profiles"]:
        assert {"association_status", "reasons", "queries_that_found_it", "final_people_list",
                "in_final_people_list", "providers"} <= set(p)
        assert p["queries_that_found_it"] and p["providers"] == ["brave"]
    assert r["queries_run"] and all({"seq", "provider", "query", "page", "results", "ok"} <= set(q)
                                    for q in r["queries_run"])
    assert r["provider_requests"]["brave"] == len(r["queries_run"])
    assert r["discovery_request_log"]


def test_final_list_matches_assess_people_confirmed_exactly():
    from commercial.people import assess_people
    brave = FakeBrave([CONFIRMED_ROW, OTHER_CONFIRMED, REJECTED_ROW])
    r = pfd.run_funnel(_org(), [brave])
    in_final = {p["url"] for p in r["profiles"] if p["in_final_people_list"]}
    assert in_final == {JIHAD_URL, OTHER_CONFIRMED[0]}


# ---------------------------------------------------------------------------
# brave_only excludes Tavily even when TAVILY_API_KEY is configured
# ---------------------------------------------------------------------------

def test_brave_only_excludes_tavily_even_when_key_configured(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", FAKE_TAVILY_KEY)
    spy = SpyTavily()
    brave = FakeBrave([CONFIRMED_ROW])
    r = pfd.run_funnel(_org(), [spy, brave], watch=["jihad"])
    assert r["effective_people_mode"] == "brave_only"
    assert r["tavily_key_configured"] is True
    assert r["tavily_called"] is False
    assert spy.used == []                                  # not even is_configured()
    assert r["provider_requests"]["tavily"] == 0
    assert r["provider_requests"]["brave"] == len(brave.calls) > 0
    assert all(q["provider"] == "brave" for q in r["queries_run"])


def test_non_brave_only_mode_is_visible_not_silent(monkeypatch):
    monkeypatch.setenv(PEOPLE_MODE_ENV, "brave_tavily")
    spy = SpyTavily()
    r = pfd.run_funnel(_org(), [spy, FakeBrave([CONFIRMED_ROW])], config=PeopleDiscoveryConfig())
    assert r["effective_people_mode"] == "brave_tavily"
    assert spy.used                                         # diagnostic reports it truthfully


# ---------------------------------------------------------------------------
# Request ceilings
# ---------------------------------------------------------------------------

def test_max_request_limits_match_production_config():
    limits = pfd.max_provider_requests()
    assert limits["soft_budget"] == 30 and limits["hard_ceiling"] == 45
    assert limits["max_brave_requests"] == 45


def test_never_exceeds_45_requests_even_when_every_call_is_productive():
    counter = {"n": 0}

    def rows(n):
        counter["n"] += 1
        return [(f"https://www.linkedin.com/in/person-{n}-{k}", f"P{n}{k} - Network Engineer - Ooredoo Oman | LinkedIn",
                 "Network Engineer at Ooredoo Oman") for k in range(5)]

    brave = FakeBrave(every_call_rows=rows)
    r = pfd.run_funnel(_org(), [brave])
    assert len(brave.calls) <= 45
    assert r["provider_requests"]["brave"] == len(brave.calls) <= 45


def test_wrapper_hard_cap_blocks_requests_beyond_cap_independently_of_discovery():
    inner = FakeBrave(every_call_rows=lambda n: [])
    rec = pfd.RecordingProvider(inner, max_requests=3)
    for _ in range(5):
        rec.search("q", num_results=20, rich_snippets=True, page=0)
    assert len(inner.calls) == 3
    assert [x.get("blocked_by_hard_cap", False) for x in rec.requests] == [False] * 3 + [True] * 2


# ---------------------------------------------------------------------------
# Output hygiene: no secrets, timestamped, never overwritten
# ---------------------------------------------------------------------------

def test_output_contains_no_keys_or_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", FAKE_BRAVE_KEY)
    monkeypatch.setenv("TAVILY_API_KEY", FAKE_TAVILY_KEY)
    monkeypatch.setenv("SERPER_API_KEY", "SERPER-SENTINEL-0003")
    r = pfd.run_funnel(_org(), [SpyTavily(), FakeBrave([CONFIRMED_ROW])], watch=["jihad"])
    paths = pfd.write_outputs(r, tmp_path)
    text = "\n".join(p.read_text(encoding="utf-8") for p in paths)
    for secret in (FAKE_BRAVE_KEY, FAKE_TAVILY_KEY, "SERPER-SENTINEL-0003", "API_KEY"):
        assert secret not in text
    assert "environ" not in json.dumps(r).lower()
    assert set(r) >= {"effective_people_mode", "tavily_key_configured", "tavily_called", "queries_run",
                      "raw_profile_urls", "dedup", "profiles", "funnel_counts", "watch"}


def test_outputs_are_timestamped_and_never_overwrite(tmp_path):
    r = pfd.run_funnel(_org(), [FakeBrave([CONFIRMED_ROW])])
    first = pfd.write_outputs(r, tmp_path)
    marker = first[0].read_text(encoding="utf-8")
    second = pfd.write_outputs(r, tmp_path)                # same second -> must not clobber
    assert set(first).isdisjoint(second)
    assert first[0].read_text(encoding="utf-8") == marker
    assert all(__import__("re").search(r"people_funnel_ooredoo_oman_\d{8}T\d{6}Z", p.name) for p in first + second)
    assert json.loads(first[0].read_text(encoding="utf-8"))["organization"]["name"] == ORG


# ---------------------------------------------------------------------------
# CLI safety: dry run by default, live needs both flags
# ---------------------------------------------------------------------------

@pytest.fixture
def forbid_brave(monkeypatch):
    import research.providers.brave as brave_module

    def boom(*a, **k):
        raise AssertionError("BraveSearchProvider must not be constructed")
    monkeypatch.setattr(brave_module, "BraveSearchProvider", boom)


@pytest.mark.parametrize("flags", [[], ["--live"], ["--confirm-live-brave"]])
def test_cli_makes_no_requests_without_both_live_flags(flags, forbid_brave, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(pfd, "OUT_DIR", tmp_path)
    assert pfd.main(["--org", ORG, "--country", COUNTRY, *flags]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "Maximum Brave requests: 45" in out
    assert list(tmp_path.iterdir()) == []


def test_cli_refuses_live_when_mode_is_not_brave_only(monkeypatch, forbid_brave, capsys):
    monkeypatch.setenv(PEOPLE_MODE_ENV, "brave_tavily")
    assert pfd.main(["--org", ORG, "--live", "--confirm-live-brave"]) == 2
    assert "brave_only" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Brave key handling: the diagnostic uses the app's variable, BRAVE_SEARCH_API_KEY
# ---------------------------------------------------------------------------

class _FakeResp:
    status_code = 200
    headers: dict = {}
    text = ""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _FakeSession:
    """Stands in for requests.Session: no network. Serves Jihad once, then nothing."""

    instances: list = []

    def __init__(self):
        self.calls = []
        _FakeSession.instances.append(self)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "sent_token": (headers or {}).get("X-Subscription-Token")})
        results = []
        if len(self.calls) == 1:
            results = [{"url": JIHAD_URL, "title": "Dr. Jihad Al-Ansari - Ooredoo Oman",
                        "description": "Chief Information & Technology Officer at Ooredoo Oman. Muscat, Oman"}]
        return _FakeResp({"web": {"results": results}})


@pytest.fixture
def fake_http(monkeypatch):
    import research.providers.brave as brave_module
    _FakeSession.instances = []
    monkeypatch.setattr(brave_module.requests, "Session", _FakeSession)
    monkeypatch.setattr(brave_module.time, "sleep", lambda s: None)
    return _FakeSession


def test_diagnostic_reads_the_same_key_variable_as_the_brave_provider():
    from research.providers.brave import BRAVE_KEY_ENV
    assert BRAVE_KEY_ENV == "BRAVE_SEARCH_API_KEY"
    source = (ROOT / "_diagnostics" / "people_funnel_diagnostic.py").read_text(encoding="utf-8")
    assert "os.getenv(\"BRAVE" not in source and "environ[\"BRAVE" not in source  # key is the provider's business


def test_live_path_works_with_brave_search_api_key_and_leaks_nothing(monkeypatch, fake_http, tmp_path, capsys):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", FAKE_BRAVE_KEY)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", FAKE_TAVILY_KEY)
    monkeypatch.setattr(pfd, "OUT_DIR", tmp_path)
    assert pfd.main(["--org", ORG, "--country", COUNTRY, "--watch", "jihad", "--live", "--confirm-live-brave"]) == 0
    stdout = capsys.readouterr().out
    # the (fake) key reached only the provider's request header, never the output
    assert fake_http.instances and fake_http.instances[0].calls[0]["sent_token"] == FAKE_BRAVE_KEY
    assert len(fake_http.instances[0].calls) <= 45
    files = sorted(tmp_path.iterdir())
    assert len(files) == 2
    text = stdout + "\n".join(f.read_text(encoding="utf-8") for f in files)
    for secret in (FAKE_BRAVE_KEY, FAKE_TAVILY_KEY, "API_KEY"):
        assert secret not in text
    data = json.loads(next(f for f in files if f.suffix == ".json").read_text(encoding="utf-8"))
    assert data["watch"]["jihad"]["stage"] == "present in final People list"
    assert data["tavily_called"] is False and data["effective_people_mode"] == "brave_only"
    assert set(data["provider_requests"]) == {"brave"}


def test_live_path_with_only_the_wrong_variable_makes_no_request_and_names_the_right_one(monkeypatch, fake_http, tmp_path, capsys):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("BRAVE_API_KEY", FAKE_BRAVE_KEY)          # the variable the app does NOT use
    monkeypatch.setattr(pfd, "OUT_DIR", tmp_path)
    assert pfd.main(["--org", ORG, "--live", "--confirm-live-brave"]) == 2
    out = capsys.readouterr().out
    assert "BRAVE_SEARCH_API_KEY" in out and FAKE_BRAVE_KEY not in out
    assert all(not i.calls for i in fake_http.instances)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Watch attribution: own profile only; third-party mentions reported separately
# ---------------------------------------------------------------------------

HOST_ROW = ("https://www.linkedin.com/in/mohammed-al-hadhrami-3025028b/", "Mohammed Al Hadhrami - Ooredoo Oman | LinkedIn",
            "Jihad Al Ansari on his appointment as Chief Technology and Information Officer, and we are proud of the "
            "Omani leaders. Ooredoo Oman launched SD-WAN with Fortinet.")


def test_name_only_in_a_third_partys_snippet_is_a_mention_not_a_dedup_loss():
    r = _run([HOST_ROW, OTHER_CONFIRMED])
    w = r["watch"]["jihad"]
    assert w["stage_code"] == "mentioned_only_in_third_party_snippets"
    assert w["stage"].startswith("mentioned_only_in_third_party_snippets")
    assert HOST_ROW[0] in w["stage"]
    assert "dedup" not in w["stage"].lower()
    assert w["own_profile_raw_results"] == [] and w["profiles"] == []
    assert [m["source_profile_url"] for m in w["third_party_mentions"]] == [HOST_ROW[0]]
    assert w["third_party_mentions"][0]["source_profile_title"].startswith("Mohammed Al Hadhrami")
    # the host person is an ordinary profile; the mention did not create another one
    assert all("jihad" not in p["url"] and "jihad" not in p["title"].lower() for p in r["profiles"])


def test_own_profile_wins_over_mentions_and_mentions_are_still_listed():
    r = _run([HOST_ROW, CONFIRMED_ROW, CONFIRMED_ROW_DUP])
    w = r["watch"]["jihad"]
    assert w["stage_code"] == "present_in_final_people_list"
    assert len(w["own_profile_raw_results"]) == 2
    assert [m["source_profile_url"] for m in w["third_party_mentions"]] == [HOST_ROW[0]]


def test_name_in_neither_profile_nor_any_snippet_is_a_discovery_miss():
    r = _run([OTHER_CONFIRMED])
    assert r["watch"]["jihad"]["stage_code"] == "not_in_raw_results"
    assert r["watch"]["jihad"]["third_party_mentions"] == []


def test_watch_can_use_the_exact_profile_slug():
    r = pfd.run_funnel(_org(), [FakeBrave([HOST_ROW, CONFIRMED_ROW])], watch=["dr-jihad-al-ansari-60b7a23b"])
    assert r["watch"]["dr-jihad-al-ansari-60b7a23b"]["stage_code"] == "present_in_final_people_list"


def test_mentions_are_rendered_in_the_markdown_as_not_a_profile(tmp_path):
    r = _run([HOST_ROW])
    md = pfd.render_markdown(r)
    assert "third-party mention (NOT their profile)" in md and "mentioned_only_in_third_party_snippets" in md


# ---------------------------------------------------------------------------
# Lead follow-ups are reported (a mention is a lead, never a person)
# ---------------------------------------------------------------------------

class _LeadBrave:
    name = "brave"
    supports_pagination = True
    supports_rich_snippets = True

    def __init__(self):
        self.calls = []

    def is_configured(self):
        return True

    def search(self, query, *, num_results=10, rich_snippets=False, page=0):
        self.calls.append(query)
        if '"Layla Al-Example"' in query:
            rows = [("https://om.linkedin.com/in/layla-al-example-1a2b3c4d",
                     "Layla Al-Example - Chief Technology and Information Officer - Ooredoo Oman | LinkedIn",
                     "Chief Technology and Information Officer at Ooredoo Oman. Oman")]
        elif len(self.calls) == 1:
            rows = [HOST_ROW[:2] + ("Layla Al-Example on her appointment as Chief Technology and Information Officer, "
                                    "and we are proud of the Omani leaders.",)]
        else:
            rows = []
        return QueryOutcome(query=query, provider=self.name, page=page,
                            results=[RawResult(url=u, title=t, snippet=s, query=query, provider=self.name, rank=i)
                                     for i, (u, t, s) in enumerate(rows)])


def test_lead_followups_and_their_requests_are_reported_and_leak_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", FAKE_BRAVE_KEY)
    r = pfd.run_funnel(_org(), [_LeadBrave()], watch=["layla"])
    [lead] = r["lead_followups"]
    assert lead["name"] == "Layla Al-Example" and lead["status"] == "queried" and lead["profiles_returned"] == 1
    assert lead["source_profile_url"] == HOST_ROW[0]
    assert r["watch"]["layla"]["stage_code"] == "present_in_final_people_list"   # via her OWN profile
    assert any('"Layla Al-Example"' in q["query"] for q in r["queries_run"])
    assert r["provider_requests"]["brave"] == len(r["queries_run"]) <= 45
    paths = pfd.write_outputs(r, tmp_path)
    text = "\n".join(p.read_text(encoding="utf-8") for p in paths)
    assert "Lead follow-ups" in text and FAKE_BRAVE_KEY not in text and "API_KEY" not in text


# ---------------------------------------------------------------------------
# Lead follow-up TRACE: the individual results each follow-up query returned
# ---------------------------------------------------------------------------

LEAD_SLUG = "layla-al-example-1a2b3c4d"
LEAD_URL = f"https://om.linkedin.com/in/{LEAD_SLUG}"
LEAD_MENTION = ("Layla Al-Example on her appointment as Chief Technology and Information Officer, "
                "and we are proud of the Omani leaders.")
LEAD_OWN_ROW = (LEAD_URL, "Layla Al-Example - Chief Technology and Information Officer - Ooredoo Oman | LinkedIn",
                "Chief Technology and Information Officer at Ooredoo Oman. Muscat, Oman. " + "x" * 1000)


class _ScriptedBrave:
    """First request returns a host profile whose snippet announces the lead; the
    exact-name follow-up returns `lead_rows` (or an error)."""

    name = "brave"
    supports_pagination = True
    supports_rich_snippets = True

    def __init__(self, lead_rows, lead_error=None):
        self.lead_rows, self.lead_error, self.calls = lead_rows, lead_error, []

    def is_configured(self):
        return True

    def search(self, query, *, num_results=10, rich_snippets=False, page=0):
        self.calls.append(query)
        if '"Layla Al-Example"' in query:
            if self.lead_error:
                return QueryOutcome(query=query, provider=self.name, page=page, error=self.lead_error)
            rows = list(self.lead_rows)
        elif len(self.calls) == 1:
            rows = [HOST_ROW[:2] + (LEAD_MENTION,)]
        else:
            rows = []
        return QueryOutcome(query=query, provider=self.name, page=page,
                            results=[RawResult(url=u, title=t, snippet=s, query=query, provider=self.name, rank=i)
                                     for i, (u, t, s) in enumerate(rows)])


def _trace(rows, *, lead_error=None, watch=("layla",), target_slugs=(LEAD_SLUG,), **kw):
    r = pfd.run_funnel(_org(), [_ScriptedBrave(rows, lead_error)], watch=list(watch),
                       target_slugs=list(target_slugs), **kw)
    [lead] = r["lead_followups"]
    return r, lead


def test_trace_records_each_returned_result_for_a_matching_target_profile():
    r, lead = _trace([LEAD_OWN_ROW])
    [res] = lead["returned_results"]
    assert res["url"] == LEAD_URL and res["title"].startswith("Layla Al-Example - Chief Technology")
    assert res["query"] == lead["query"] == 'site:linkedin.com/in "Layla Al-Example" "Ooredoo Oman"'
    assert res["provider"] == "brave" and res["rank"] == 0
    assert res["is_linkedin_profile"] is True and res["profile_slug"] == LEAD_SLUG
    assert res["matches_target_slug"] is True and lead["target_slug_returned"] is True
    assert len(res["snippet"]) <= 400 + 1 and res["snippet"].startswith("Chief Technology and Information Officer")  # safely truncated
    # the existing association assessment, not a new rule
    assert res["association_status"] == "confirmed_current" and res["association_reasons"] == []
    assert res["candidate_association_status"] == "confirmed_current" and res["in_final_people_list"] is True
    # compatibility: the existing count is untouched
    assert lead["profiles_returned"] == 1 and lead["status"] == "queried"
    assert r["target_slugs"] == [LEAD_SLUG]


def test_trace_marks_unrelated_profiles_and_non_profile_pages_as_not_the_target():
    rows = [("https://www.linkedin.com/in/unrelated-person", "Unrelated Person - Manager - Other Co | LinkedIn", "Manager at Other Co"),
            ("https://www.linkedin.com/in/another-one", "Another One - Ooredoo", "Network Engineer at Ooredoo. Oman"),
            ("https://example.com/news/appointment", "Appointment news", "Ooredoo Oman appoints a Chief Technology Officer")]
    r, lead = _trace(rows)
    res = lead["returned_results"]
    assert [x["matches_target_slug"] for x in res] == [False, False, False] and lead["target_slug_returned"] is False
    assert [x["is_linkedin_profile"] for x in res] == [True, True, False]
    assert res[2]["profile_slug"] is None and res[2]["association_status"] is None and res[2]["in_final_people_list"] is False
    assert res[0]["association_status"] == "rejected" and res[0]["in_final_people_list"] is False
    assert res[1]["association_status"] == "needs_verification" and res[1]["in_final_people_list"] is False
    assert lead["profiles_returned"] == 2                                     # existing count: LinkedIn /in/ results
    assert r["watch"]["layla"]["stage_code"] == "mentioned_only_in_third_party_snippets"   # still only a mention


def test_trace_keeps_duplicate_results_but_flags_them_and_counts_unique_urls():
    www = (f"https://www.linkedin.com/in/{LEAD_SLUG}", LEAD_OWN_ROW[1], LEAD_OWN_ROW[2])
    r, lead = _trace([LEAD_OWN_ROW, www, LEAD_OWN_ROW])
    res = lead["returned_results"]
    assert len(res) == 3 and [x["rank"] for x in res] == [0, 1, 2]
    assert [x["duplicate_of_earlier_result"] for x in res] == [False, True, True]
    assert lead["returned_result_count"] == 3 and lead["unique_profiles_returned"] == 1
    assert all(x["matches_target_slug"] for x in res)
    people = [p for p in r["profiles"] if p["slug"] == LEAD_SLUG]
    assert len(people) == 1 and r["funnel_counts"]["final_people_list"] >= 1     # still ONE person
    assert lead["profiles_returned"] == 3                                        # existing count preserved as-is


def test_trace_target_slug_matching_is_exact_not_a_substring_or_name_guess():
    near = ("https://www.linkedin.com/in/layla-al-example-1a2b3c4d-other", "Layla Al-Example - Ooredoo Oman | LinkedIn",
            "Network Engineer at Ooredoo Oman")
    r, lead = _trace([near])
    assert lead["returned_results"][0]["matches_target_slug"] is False and lead["target_slug_returned"] is False


def test_trace_with_an_exact_slug_given_as_a_watch_term_counts_as_a_target_slug():
    r = pfd.run_funnel(_org(), [_ScriptedBrave([LEAD_OWN_ROW])], watch=[LEAD_SLUG])
    assert r["target_slugs"] == [LEAD_SLUG]
    assert r["lead_followups"][0]["target_slug_returned"] is True


def test_trace_without_any_target_slug_reports_none_rather_than_guessing():
    r = pfd.run_funnel(_org(), [_ScriptedBrave([LEAD_OWN_ROW])], watch=["layla"])
    assert r["target_slugs"] == []
    assert r["lead_followups"][0]["target_slug_returned"] is None
    assert r["lead_followups"][0]["returned_results"][0]["matches_target_slug"] is None


def test_trace_output_has_no_keys_env_values_or_raw_error_messages(monkeypatch, tmp_path):
    secret_err = "HTTP 401: invalid token BRAVE-SENTINEL-KEY-0001 for subscription"
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", FAKE_BRAVE_KEY)
    monkeypatch.setenv("TAVILY_API_KEY", FAKE_TAVILY_KEY)
    monkeypatch.setenv("SOME_OTHER_ENV", "ENV-VALUE-SENTINEL-77")
    r, lead = _trace([], lead_error=secret_err)
    assert lead["errored"] is True and "error" not in lead and lead["returned_results"] == []
    ok, ok_lead = _trace([LEAD_OWN_ROW])
    assert ok_lead["errored"] is False and "error" not in ok_lead
    paths = pfd.write_outputs(r, tmp_path) + pfd.write_outputs(ok, tmp_path)
    text = "\n".join(p.read_text(encoding="utf-8") for p in paths)
    for secret in (FAKE_BRAVE_KEY, FAKE_TAVILY_KEY, "ENV-VALUE-SENTINEL-77", secret_err, "invalid token", "API_KEY"):
        assert secret not in text
    assert "returned results" in text.lower()


def test_trace_runs_offline_and_a_second_run_never_overwrites_the_first(tmp_path):
    r, _ = _trace([LEAD_OWN_ROW])
    first = pfd.write_outputs(r, tmp_path)
    before = first[0].read_text(encoding="utf-8")
    second = pfd.write_outputs(r, tmp_path)
    assert set(first).isdisjoint(second) and first[0].read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Lead trace with BOTH name spellings (separate queries, one trace)
# ---------------------------------------------------------------------------

class _TwoSpellingBrave(_ScriptedBrave):
    def __init__(self, hyphen_rows=(), spaced_rows=()):
        super().__init__([])
        self.hyphen_rows, self.spaced_rows = list(hyphen_rows), list(spaced_rows)

    def search(self, query, *, num_results=10, rich_snippets=False, page=0):
        if '"Layla Al Example"' in query or '"Layla Al-Example"' in query:
            self.calls.append(query)
            rows = self.spaced_rows if '"Layla Al Example"' in query else self.hyphen_rows
            return QueryOutcome(query=query, provider=self.name, page=page,
                                results=[RawResult(url=u, title=t, snippet=s, query=query, provider=self.name, rank=i)
                                         for i, (u, t, s) in enumerate(rows)])
        return super().search(query, num_results=num_results, rich_snippets=rich_snippets, page=page)


def _trace2(hyphen_rows=(), spaced_rows=()):
    r = pfd.run_funnel(_org(), [_TwoSpellingBrave(hyphen_rows, spaced_rows)], watch=["layla"], target_slugs=[LEAD_SLUG])
    [lead] = r["lead_followups"]
    return r, lead


def test_trace_aggregates_results_of_both_spelling_queries_and_finds_the_target_in_the_second():
    other = ("https://www.linkedin.com/in/unrelated-person", "Unrelated Person - Manager - Other Co | LinkedIn", "Manager at Other Co")
    r, lead = _trace2(hyphen_rows=[other], spaced_rows=[LEAD_OWN_ROW])
    assert lead["queries"] == ['site:linkedin.com/in "Layla Al-Example" "Ooredoo Oman"',
                               'site:linkedin.com/in "Layla Al Example" "Ooredoo Oman"']
    assert [x["query"] for x in lead["returned_results"]] == lead["queries"]
    assert [x["matches_target_slug"] for x in lead["returned_results"]] == [False, True]
    assert lead["target_slug_returned"] is True and lead["returned_result_count"] == 2
    assert lead["requests"] == 2 and lead["profiles_returned"] == 2


def test_trace_flags_duplicates_across_the_two_queries_and_keeps_one_person():
    www = (f"https://www.linkedin.com/in/{LEAD_SLUG.upper()}/", LEAD_OWN_ROW[1], LEAD_OWN_ROW[2])
    other = ("https://www.linkedin.com/in/unrelated-person", "Unrelated Person - Manager - Other Co | LinkedIn", "Manager at Other Co")
    r, lead = _trace2(hyphen_rows=[other], spaced_rows=[LEAD_OWN_ROW, www, other])
    assert [x["duplicate_of_earlier_result"] for x in lead["returned_results"]] == [False, False, True, True]
    assert lead["unique_profiles_returned"] == 2 and lead["returned_result_count"] == 4
    assert len([p for p in r["profiles"] if p["slug"] == LEAD_SLUG]) == 1


def test_trace_with_zero_results_for_both_spellings_reports_two_requests_and_no_target():
    r, lead = _trace2()
    assert lead["requests"] == 2 and lead["stop_reason"] == "variants_exhausted"
    assert lead["returned_results"] == [] and lead["target_slug_returned"] is False
    assert r["watch"]["layla"]["stage_code"] == "mentioned_only_in_third_party_snippets"


def test_trace_first_spelling_hit_means_one_query(tmp_path):
    r, lead = _trace2(hyphen_rows=[LEAD_OWN_ROW])
    assert lead["requests"] == 1 and len(lead["queries"]) == 1 and lead["stop_reason"] == "profile_found"
    text = "\n".join(p.read_text(encoding="utf-8") for p in pfd.write_outputs(r, tmp_path))
    assert "Layla Al-Example" in text and FAKE_BRAVE_KEY not in text


# ---------------------------------------------------------------------------
# Known profile URL leads: trace, redaction, CLI
# ---------------------------------------------------------------------------

KP_SLUG = "layla-al-example-1a2b3c4d"
KP_URL = f"https://om.linkedin.com/in/{KP_SLUG}/?trk=public_profile&utm_source=share#top"
KP_ROW = (f"https://www.linkedin.com/in/{KP_SLUG}", "Layla Al-Example - Chief Technology and Information Officer - Ooredoo Oman | LinkedIn",
          "Chief Technology and Information Officer at Ooredoo Oman. Muscat, Oman.")
KP_OTHER = ("https://www.linkedin.com/in/layla-al-example-ffffffff", "Layla Al-Example - Marketing - Other Co | LinkedIn", "Marketing at Other Co")
KP_MARKER = "ZZ-UNIQUE-TAIL-MARKER-9917"
KP_TEXT = ("Layla Al-Example\nChief Technology and Information Officer at Ooredoo Oman\nMuscat, Oman\n"
           + ("filler sentence about nothing. " * 60) + KP_MARKER + " api_key=SUPERSECRET123 https://x.example/p?trk=abc")


class _ProbeBrave:
    name = "brave"
    supports_pagination = True
    supports_rich_snippets = True

    def __init__(self, probe_rows=()):
        self.probe_rows, self.calls = list(probe_rows), []

    def is_configured(self):
        return True

    def search(self, query, *, num_results=10, rich_snippets=False, page=0):
        self.calls.append(query)
        rows = self.probe_rows if query.startswith("site:linkedin.com/in/") else []
        return QueryOutcome(query=query, provider=self.name, page=page,
                            results=[RawResult(url=u, title=t, snippet=s, query=query, provider=self.name, rank=i)
                                     for i, (u, t, s) in enumerate(rows)])


def _kp(probe_rows=(), *, text=None, urls=(KP_URL,), **kw):
    return pfd.run_funnel(_org(), [_ProbeBrave(probe_rows)], known_urls=list(urls), known_text=text, **kw)


def test_known_profile_trace_records_state_slug_request_and_exact_match():
    r = _kp([KP_OTHER, KP_ROW])
    [k] = r["known_profile_leads"]
    assert k["state"] == "resolved_via_search" and k["slug"] == KP_SLUG and k["requests"] == 1
    assert k["query"] == f"site:linkedin.com/in/{KP_SLUG}" and k["exact_match_returned"] is True
    res = k["returned_results"]
    assert [x["matches_known_slug"] for x in res] == [False, True]
    assert res[1]["association_status"] == "confirmed_current" and res[1]["in_final_people_list"] is True
    assert res[0]["in_final_people_list"] is False                       # a namesake is not credited
    assert k["returned_result_count"] == 2


def test_known_profile_unresolved_trace_has_no_person_and_no_matching_result():
    r = _kp([KP_OTHER])
    [k] = r["known_profile_leads"]
    assert k["state"] == "unresolved_no_content" and k["exact_match_returned"] is False
    assert not any(p["slug"] == KP_SLUG for p in r["profiles"])


def test_known_profile_trace_never_stores_the_input_url_query_or_fragment():
    r = _kp([])
    text = json.dumps(r["known_profile_leads"])
    assert "trk=" not in text and "utm_source" not in text and "#top" not in text and "om.linkedin.com" not in text


def test_supplied_text_is_bounded_sanitised_and_labelled_in_the_report(tmp_path):
    r = _kp([], text={KP_URL: KP_TEXT})
    [k] = r["known_profile_leads"]
    assert k["state"] == "resolved_via_supplied_text" and k["provenance"] == "user_supplied_text"
    [p] = [p for p in r["profiles"] if p["slug"] == KP_SLUG]
    assert p["origin"] == "user_supplied_text" and p["association_status"] == "confirmed_current"
    assert len(p["snippet"]) <= 400 and len(k["excerpt"]) <= 400
    paths = pfd.write_outputs(r, tmp_path)
    blob = json.dumps(r) + "\n".join(x.read_text(encoding="utf-8") for x in paths)
    for gone in (KP_MARKER, "SUPERSECRET123", "trk=abc"):
        assert gone not in blob
    assert "user_supplied_text" in blob


def test_supplied_text_evidence_and_rejection_reasons_are_also_bounded():
    r = _kp([], text={KP_URL: KP_TEXT.replace("Ooredoo Oman", "Omantel")})
    [p] = [p for p in r["profiles"] if p["slug"] == KP_SLUG]
    assert p["origin"] == "user_supplied_text"
    assert sum(len(e) for e in p["evidence"]) <= 400 and all(len(x) <= 400 for x in p["reasons"])
    for rej in r["rejected"]:
        if KP_SLUG in rej["url"]:
            assert all(len(x) <= 400 for x in rej["reasons"])


def test_provider_profiles_are_labelled_as_provider_search():
    r = _kp([KP_ROW])
    [p] = [p for p in r["profiles"] if p["slug"] == KP_SLUG]
    assert p["origin"] == "provider_search"


def test_known_profile_requests_are_in_the_log_and_the_hard_cap_still_applies():
    r = _kp([])
    assert any(e["dimension"] == "known_profile_probe" for e in r["discovery_request_log"])
    assert len(r["queries_run"]) <= 45
    capped = _kp([], max_requests=5)
    assert len([q for q in capped["queries_run"] if not q.get("blocked_by_hard_cap")]) <= 5


def test_known_profile_secret_redaction_across_outputs(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", FAKE_BRAVE_KEY)
    monkeypatch.setenv("TAVILY_API_KEY", FAKE_TAVILY_KEY)
    r = _kp([KP_ROW], text={KP_URL: KP_TEXT})
    paths = pfd.write_outputs(r, tmp_path)
    blob = json.dumps(r) + "\n".join(x.read_text(encoding="utf-8") for x in paths)
    for secret in (FAKE_BRAVE_KEY, FAKE_TAVILY_KEY, "API_KEY"):
        assert secret not in blob


def test_cli_dry_run_with_known_url_makes_no_request_and_does_not_read_the_text_file(forbid_brave, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(pfd, "OUT_DIR", tmp_path)
    assert pfd.main(["--org", ORG, "--known-url", KP_URL, "--known-profile-text-file", str(tmp_path / "missing.txt")]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "Known profile URLs: 1 valid, 0 invalid" in out
    assert "trk=" not in out and "Maximum Brave requests: 45" in out
    assert list(tmp_path.iterdir()) == []


def test_cli_rejects_a_text_file_unless_exactly_one_known_url_is_given(forbid_brave, capsys, tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("x", encoding="utf-8")
    assert pfd.main(["--org", ORG, "--known-profile-text-file", str(f)]) == 2
    assert pfd.main(["--org", ORG, "--known-url", KP_URL, "--known-url", KP_URL.replace("1a2b3c4d", "2b3c4d5e"),
                     "--known-profile-text-file", str(f)]) == 2


def test_cli_counts_invalid_known_urls_without_echoing_them(forbid_brave, capsys):
    assert pfd.main(["--org", ORG, "--known-url", "https://example.com/in/x?token=SECRET-QUERY-VALUE"]) == 0
    out = capsys.readouterr().out
    assert "0 valid, 1 invalid" in out and "SECRET-QUERY-VALUE" not in out


def test_cli_live_with_known_url_and_text_file_leaks_nothing(monkeypatch, fake_http, tmp_path, capsys):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", FAKE_BRAVE_KEY)
    monkeypatch.setenv("TAVILY_API_KEY", FAKE_TAVILY_KEY)
    monkeypatch.setattr(pfd, "OUT_DIR", tmp_path / "out")
    tf = tmp_path / "profile.txt"
    tf.write_text(KP_TEXT, encoding="utf-8")
    assert pfd.main(["--org", ORG, "--country", COUNTRY, "--known-url", KP_URL, "--known-profile-text-file", str(tf),
                     "--live", "--confirm-live-brave"]) == 0
    stdout = capsys.readouterr().out
    files = sorted((tmp_path / "out").iterdir())
    blob = stdout + "\n".join(f.read_text(encoding="utf-8") for f in files)
    for secret in (FAKE_BRAVE_KEY, FAKE_TAVILY_KEY, "API_KEY", KP_MARKER, "SUPERSECRET123", "trk=abc", str(tf)):
        assert secret not in blob
    data = json.loads(next(f for f in files if f.suffix == ".json").read_text(encoding="utf-8"))
    assert data["known_profile_leads"][0]["slug"] == KP_SLUG and len(fake_http.instances[0].calls) <= 45

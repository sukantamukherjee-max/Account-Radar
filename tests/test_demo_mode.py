"""Live Demo Mode -- one product, one live research engine, one launcher.

Demo Mode (demo/demo_app.py -> app.run_demo(), started by run_demo.bat) is
the normal Account Radar app with a presentation layer (ui/demo_mode.py).
These tests pin that it:

  * runs the REAL live research path (app._run_live -> run_acquisition ->
    build_report -> ... -> build_opportunity_hypotheses), always fresh;
  * never loads a frozen dataset or replays an earlier result -- including
    after a failed run;
  * accepts any organization/country (Oman Airports is only a pre-fill);
  * never starts research on its own, never shows API keys or tracebacks;
  * leaves the normal `streamlit run app.py` experience unchanged.

No test makes a network call: the live research function is replaced by a
recording fake where a full run is needed.
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_decide_view import INFRA_LEAD, LEADERSHIP, _report

import streamlit as _REAL_STREAMLIT  # noqa: E402  (see tests/test_theme_engine.py)

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
DEMO_DIR = ROOT / "demo"
DEMO_APP = DEMO_DIR / "demo_app.py"
DEMO_MODULE = ROOT / "ui" / "demo_mode.py"
LAUNCHER = ROOT / "run_demo.bat"
SECRET = "tvly-DEMO-SECRET-0000"


def _app_module():
    sys.modules["streamlit"] = _REAL_STREAMLIT
    import app
    return app


def _demo_mode():
    from ui import demo_mode
    return demo_mode


def _apptest(path: Path):
    sys.modules["streamlit"] = _REAL_STREAMLIT
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(path), default_timeout=30)


def _html(at) -> str:
    return " ".join(m.value for m in at.markdown)


def _all_text(at) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    parts += [str(t.value) for t in at.text_input] + [b.label for b in at.button]
    parts += [e.value for e in at.error] + [str(s.label) for s in at.get("expandable")]
    return " ".join(parts)


def _live_entry(org="Example Org", country="Testland", *, results=12, errors=None):
    report = _report(organization_name=org, country=country, signals=LEADERSHIP, people=[INFRA_LEAD],
                     acquisition_diagnostics={"total_queries": 6, "total_results_returned": results,
                                              "errors": list(errors or [])})
    return {"report": report, "ai_synthesis": {}, "opportunity_hypotheses": [],
            "researched_at": _demo_mode().researched_at_iso()}


class FakeLive:
    """Records every live-research call made by the app (no network)."""

    def __init__(self, *outcomes):
        self.calls = []
        self.outcomes = list(outcomes)

    def __call__(self, name, country, *, progress=None, reuse_verified_identity=True):
        self.calls.append({"name": name, "country": country, "progress": progress,
                           "reuse_verified_identity": reuse_verified_identity})
        if progress:
            progress("Searching current public sources\u2026")
        outcome = self.outcomes.pop(0) if self.outcomes else _live_entry(name, country)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def fake_live(monkeypatch):
    app = _app_module()
    fake = FakeLive()
    monkeypatch.setattr(app, "_run_live", fake)
    return fake


def _analyze(at, org=None, country=None):
    if org is not None:
        at.sidebar.text_input[0].set_value(org)
    if country is not None:
        at.sidebar.text_input[1].set_value(country)
    at.sidebar.button[0].click().run()
    return at


# ---------------------------------------------------------------------------
# 1. Launcher / configuration exist and are thin
# ---------------------------------------------------------------------------

def test_demo_launcher_and_configuration_exist():
    assert DEMO_APP.exists() and DEMO_MODULE.exists() and LAUNCHER.exists()
    assert (DEMO_DIR / "README.md").exists()
    bat = LAUNCHER.read_text(encoding="utf-8")
    assert "streamlit run demo\\demo_app.py" in bat and "--server.port 8502" in bat
    assert '%LOCALAPPDATA%\\Programs\\Python\\Launcher\\py.exe' in bat and 'set "PYLAUNCHER=py"' in bat
    assert 'cd /d "%~dp0"' in bat                      # project root, no user-specific absolute path
    assert "C:\\Users" not in bat and "smukherjee" not in bat
    assert "--client.showErrorDetails none" in bat     # no raw tracebacks for the audience
    # The key check matches the live chain (Brave -> Tavily); Serper is not part of it.
    assert 'if "%BRAVE_SEARCH_API_KEY%%TAVILY_API_KEY%"==""' in bat and "SERPER" not in bat.upper()
    assert _demo_mode().DEMO.port == 8502


def test_demo_entry_point_is_a_thin_launcher_without_business_logic():
    tree = ast.parse(DEMO_APP.read_text(encoding="utf-8"))
    imported = {n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imported <= {"__future__", "sys", "pathlib", "app"}
    assert not [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    assert "app.run_demo()" in DEMO_APP.read_text(encoding="utf-8")


def test_no_duplicated_codebase_for_the_demo():
    names = sorted(p.name for p in DEMO_DIR.iterdir() if p.name != "__pycache__")
    assert names == ["README.md", "demo_app.py"]
    for duplicate in ("app_demo.py", "research_demo", "commercial_demo", "ui_demo"):
        assert not (ROOT / duplicate).exists(), duplicate
    # The presentation module holds no research / intelligence imports.
    tree = ast.parse(DEMO_MODULE.read_text(encoding="utf-8"))
    modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    for forbidden in ("research", "commercial", "ai", "ui.decide_view", "ui.act_view", "requests"):
        assert not any(m == forbidden or m.startswith(forbidden + ".") for m in modules), forbidden


# ---------------------------------------------------------------------------
# 2-3. Real pipeline, never a frozen result
# ---------------------------------------------------------------------------

def test_demo_uses_the_real_research_pipeline_functions():
    app = _app_module()
    import commercial.opportunities
    import research.acquisition
    import research.analysis.report
    import research.providers.tavily
    assert app.run_acquisition is research.acquisition.run_acquisition
    assert app.build_report is research.analysis.report.build_report
    assert app.build_opportunity_hypotheses is commercial.opportunities.build_opportunity_hypotheses
    assert app.TavilyProvider is research.providers.tavily.TavilyProvider
    text = APP.read_text(encoding="utf-8")
    # One research call site, shared by normal and demo mode.
    assert text.count("_run_live(") == 2 and text.count("_analyze_account(") == 2


def test_demo_does_not_load_any_frozen_or_saved_result():
    for path in (DEMO_APP, DEMO_MODULE):
        source = path.read_text(encoding="utf-8")
        for fragment in ("json.load", "evidence_corpus", "oman_entry", "vis_entry", ".json", "open(", "pickle"):
            assert fragment not in source, (path.name, fragment)
    assert not list(DEMO_DIR.glob("*.json"))


def test_opening_demo_mode_starts_no_research(fake_live):
    at = _apptest(DEMO_APP).run()
    assert not at.exception
    assert fake_live.calls == []
    assert "entry" not in at.session_state


# ---------------------------------------------------------------------------
# 4-6. Default example, arbitrary accounts, fresh research
# ---------------------------------------------------------------------------

def test_oman_airports_is_only_the_prefilled_example(fake_live):
    at = _apptest(DEMO_APP).run()
    assert [t.value for t in at.sidebar.text_input] == ["Oman Airports Management Company", "Oman"]
    _analyze(at)
    assert not at.exception
    assert fake_live.calls[0]["name"] == "Oman Airports Management Company"
    assert fake_live.calls[0]["country"] == "Oman"


@pytest.mark.parametrize("org,country", [("Ooredoo", "Oman"), ("Vodafone Oman", "Oman"), ("Siemens", "Germany")])
def test_demo_accepts_any_organization_and_country(fake_live, org, country):
    at = _analyze(_apptest(DEMO_APP).run(), org, country)
    assert not at.exception
    assert fake_live.calls[-1]["name"] == org and fake_live.calls[-1]["country"] == country
    assert at.session_state["entry"]["report"]["organization_name"] == org


def test_demo_requests_fresh_research_with_progress(fake_live):
    at = _analyze(_apptest(DEMO_APP).run())
    call = fake_live.calls[0]
    assert call["reuse_verified_identity"] is False      # fresh: no session identity re-use
    assert callable(call["progress"])                    # visible research progress
    assert _demo_mode().DEMO.fresh_research is True


def test_every_analyze_is_a_new_live_run_and_replaces_the_previous_result(fake_live):
    at = _analyze(_apptest(DEMO_APP).run(), "First Org", "Oman")
    _analyze(at, "Second Org", "Oman")
    assert [c["name"] for c in fake_live.calls] == ["First Org", "Second Org"]
    assert at.session_state["entry"]["report"]["organization_name"] == "Second Org"


def test_fresh_run_does_not_reuse_a_session_verified_website(monkeypatch):
    app = _app_module()
    seen = {}

    class Stop(Exception):
        pass

    def spy(org, providers):
        seen["website"] = org.website
        seen["providers"] = [type(p).__name__ for p in providers]
        raise Stop

    monkeypatch.setenv("TAVILY_API_KEY", SECRET)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(app, "run_acquisition", spy)
    monkeypatch.setattr(app.st, "session_state", {"verified_identities": {("acme", "oman"): "https://acme.example"}})
    messages = []
    with pytest.raises(Stop):
        app._run_live("Acme", "Oman", progress=messages.append, reuse_verified_identity=False)
    assert seen["website"] is None and seen["providers"] == ["TavilyProvider"]
    assert messages and messages[0].startswith("Searching current public sources")
    with pytest.raises(Stop):
        app._run_live("Acme", "Oman")                                  # normal mode: unchanged behaviour
    assert seen["website"] == "https://acme.example"


# ---------------------------------------------------------------------------
# 7. Failure never falls back to stale data
# ---------------------------------------------------------------------------

def test_failed_run_after_a_success_shows_no_old_result(fake_live):
    at = _analyze(_apptest(DEMO_APP).run(), "Old Org", "Oman")
    assert at.session_state["entry"]["report"]["organization_name"] == "Old Org"
    fake_live.outcomes.append(RuntimeError(f"HTTP 500 boom {SECRET} Traceback (most recent call last)"))
    _analyze(at, "New Org", "Oman")
    assert not at.exception
    assert "entry" not in at.session_state                      # the old result was cleared, not shown
    html = _html(at)
    assert "Live research could not be completed. No current account result is being displayed." in html
    assert "Old Org" not in html
    assert SECRET not in _all_text(at) and "Traceback" not in _all_text(at)
    assert [b.label for b in at.main.button] == ["Retry research"]


def test_retry_runs_fresh_research_for_the_same_account(fake_live):
    fake_live.outcomes.append(TimeoutError("research timeout"))
    at = _analyze(_apptest(DEMO_APP).run(), "Retry Org", "Oman")
    assert "The research run timed out." in _html(at)
    at.button(key="demo_retry_button").click().run()
    assert not at.exception
    assert [c["name"] for c in fake_live.calls] == ["Retry Org", "Retry Org"]
    assert at.session_state["entry"]["report"]["organization_name"] == "Retry Org"


def test_a_run_that_retrieved_nothing_is_a_failure_not_an_empty_result(fake_live):
    fake_live.outcomes.append(_live_entry("Empty Org", results=0, errors=["[official] HTTP 401: invalid key"]))
    at = _analyze(_apptest(DEMO_APP).run(), "Empty Org", "Oman")
    assert "entry" not in at.session_state
    assert "The research provider rejected the API key." in _html(at)


@pytest.mark.parametrize("message,cause", [
    ("TAVILY_API_KEY is not configured in the environment.", "API key is not configured"),
    ("Read timed out", "timed out"),
    ("ConnectionError: failed to resolve host", "network problem"),
    ("something odd", "stopped unexpectedly"),
])
def test_exception_causes_are_plain_language(message, cause):
    failure = _demo_mode().failure_from_exception(RuntimeError(message))
    assert cause in failure.cause
    assert message not in failure.cause + failure.hint


@pytest.mark.parametrize("errors,cause", [
    (["[a] TAVILY_API_KEY not configured"], "not configured"),
    (["[a] HTTP 429: too many"], "usage limit"),
    (["[a] timeout"], "did not respond in time"),
    (["[a] transport error: x"], "network problem"),
    ([], "returned no results"),
])
def test_zero_result_runs_are_classified(errors, cause):
    report = {"acquisition_diagnostics": {"total_results_returned": 0, "errors": errors}}
    assert cause in _demo_mode().failure_from_report(report).cause


def test_a_thin_but_real_corpus_is_a_result_not_a_failure():
    report = {"acquisition_diagnostics": {"total_results_returned": 3, "errors": ["[a] timeout"]},
              "status": "insufficient_evidence"}
    assert _demo_mode().failure_from_report(report) is None


# ---------------------------------------------------------------------------
# 8. Timestamp is runtime-generated
# ---------------------------------------------------------------------------

def test_research_timestamp_is_generated_at_runtime():
    dm = _demo_mode()
    stamp = datetime.fromisoformat(dm.researched_at_iso())
    assert abs(stamp - datetime.now(timezone.utc)) < timedelta(minutes=1)
    assert dm.format_researched_at("2026-10-05T10:32:00+00:00") == "05 Oct 2026 \u00b7 14:32 GST"
    assert dm.format_researched_at("2027-01-31T21:05:00+00:00") == "01 Feb 2027 \u00b7 01:05 GST"
    assert dm.format_researched_at(None) == "" and dm.format_researched_at("not a date") == ""
    live = APP.read_text(encoding="utf-8")
    body = live[live.index("def _run_live("):live.index("def _render_metric_row(")]
    assert '"researched_at": demo_mode.researched_at_iso()' in body


def test_status_strip_shows_the_current_runs_values(fake_live):
    at = _analyze(_apptest(DEMO_APP).run(), "Status Org", "Oman")
    html = _html(at)
    when = _demo_mode().format_researched_at(at.session_state["entry"]["researched_at"])
    assert "LIVE RESEARCH" in html and when in html
    for label in ("Qualified sources", "Account-specific developments", "Opportunities", "Decision"):
        assert label in html
    # Zero is a real value and must be displayed, not blanked.
    assert '<div class="ar-demo-fact-label">Opportunities</div><div class="ar-demo-fact-value">0</div>' in html


# ---------------------------------------------------------------------------
# 9-14. Intelligence code is shared, not modified for the demo
# ---------------------------------------------------------------------------

def test_demo_layer_does_not_touch_intelligence_modules():
    for package in ("research", "commercial", "ai"):
        for path in (ROOT / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "demo_mode" not in text and "run_demo" not in text, path
    for module in ("account_signals", "understand_view", "decide_view", "act_view", "executive_summary"):
        assert "demo" not in (ROOT / "ui" / f"{module}.py").read_text(encoding="utf-8").lower(), module


def test_demo_result_pages_are_the_real_product_pages(fake_live):
    at = _analyze(_apptest(DEMO_APP).run(), "Pages Org", "Oman")
    assert not at.exception
    html = _html(at)
    for page in ("What is happening in this account?", "What does this mean?", "Where should Otech focus?",
                 "What do I do next?", "Who should we engage?"):
        assert page in html, page


# ---------------------------------------------------------------------------
# 15-17. Normal mode unchanged; no keys; LIVE indicator
# ---------------------------------------------------------------------------

# `streamlit run app.py` executes app.py as its own script, so normal-mode
# tests patch the research layer it imports rather than app._run_live.

def test_normal_app_is_unchanged_and_shows_no_demo_layer(monkeypatch):
    import research.acquisition

    def forbidden(*a, **k):
        raise AssertionError("opening the app must not start research")

    monkeypatch.setattr(research.acquisition, "run_acquisition", forbidden)
    at = _apptest(APP).run()
    assert not at.exception
    assert [t.value for t in at.sidebar.text_input] == ["", ""]
    assert "LIVE DEMO MODE" not in _html(at) and "LIVE RESEARCH" not in _html(at)
    assert "ar_demo" not in at.session_state or not at.session_state["ar_demo"]


def test_normal_mode_failure_behaviour_is_unchanged(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    at = _analyze(_apptest(APP).run(), "Normal Org", "Oman")
    assert not at.exception
    assert [e.value for e in at.sidebar.error] == [
        "No search provider API key is configured: BRAVE_SEARCH_API_KEY and TAVILY_API_KEY "
        "are not configured in the environment."
    ]
    assert "Live research could not be completed" not in _html(at)


def test_demo_never_displays_api_keys(fake_live, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", SECRET)
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET + "-OR")
    at = _apptest(DEMO_APP).run()
    assert SECRET not in _all_text(at)
    _analyze(at)
    assert SECRET not in _all_text(at)
    assert "TAVILY_API_KEY=" not in LAUNCHER.read_text(encoding="utf-8")


def test_demo_shows_live_indicator_and_presenter_friendly_chrome(fake_live):
    at = _apptest(DEMO_APP).run()
    html = _html(at)
    assert "LIVE DEMO MODE" in html and "Current public-domain research" in html and "Fresh analysis" in html
    captions = " ".join(c.value for c in at.sidebar.caption)
    assert "fresh research" in captions and "API keys" not in captions

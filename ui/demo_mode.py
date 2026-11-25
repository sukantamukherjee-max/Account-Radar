"""Demo Mode -- a presentation layer for LIVE management demonstrations.

Demo Mode is not a second product and holds no intelligence logic. It is the
same Account Radar app (app.main) launched through app.run_demo() by
demo/demo_app.py (see run_demo.bat). Research, evidence, capability mapping,
opportunity qualification, WATCH / UNDERSTAND / DECIDE / PEOPLE / ACT are the
exact same code paths as normal live use.

What this module adds, presentation only:

  * the demo defaults (an example account the presenter may replace);
  * the "fresh research" policy: every Analyze runs the real research
    pipeline; the previous result is cleared first and is never shown again
    if the new run fails;
  * a runtime "researched at" timestamp (never hard-coded);
  * classification of a failed live run into a plain-language cause, with no
    stack trace, no raw provider payload and no API key;
  * the small LIVE DEMO banner and status strip.

Nothing here reads or stores data from disk, and nothing here replays an old
result.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# Gulf Standard Time (Oman, UTC+4, no daylight saving). A fixed offset needs
# no tz database, so it works on Windows without the tzdata package.
GST = timezone(timedelta(hours=4), "GST")


@dataclass(frozen=True)
class DemoConfig:
    """Demo presentation defaults. The example account only pre-fills the
    sidebar inputs; the research pipeline is organization-agnostic and the
    presenter can replace both values with any account."""

    default_organization: str = "Oman Airports Management Company"
    default_country: str = "Oman"
    fresh_research: bool = True        # every Analyze is a new live run
    port: int = 8502                   # see run_demo.bat
    timezone_label: str = "GST"


DEMO = DemoConfig()


# ---------------------------------------------------------------------------
# Timestamp
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def researched_at_iso(moment: datetime | None = None) -> str:
    """ISO-8601 UTC timestamp for the moment a live run completed."""
    return (moment or now_utc()).astimezone(timezone.utc).isoformat(timespec="seconds")


def format_researched_at(iso: str | None) -> str:
    """'05 Oct 2026 · 14:32 GST' from the stored runtime timestamp, or ''."""
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(GST)
    return f"{local:%d %b %Y} · {local:%H:%M} {DEMO.timezone_label}"


# ---------------------------------------------------------------------------
# Live-run assessment
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResearchFailure:
    title: str
    cause: str
    hint: str


FAILURE_TITLE = "Live research could not be completed. No current account result is being displayed."

# Local copy of the live provider chain (research/providers/registry.py:
# SEARCH_PROVIDER_ORDER / SEARCH_PROVIDER_KEYS). This presentation module may
# not import research code, so the values are mirrored here and
# tests/test_demo_mode_failures.py fails if they ever drift from the registry.
SEARCH_PROVIDER_ORDER = ("brave", "tavily")
SEARCH_PROVIDER_KEYS = ("BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY")
_PROVIDER_KEYS: dict[str, str] = dict(zip(SEARCH_PROVIDER_ORDER, SEARCH_PROVIDER_KEYS))
_PROVIDER_LABELS = {"brave": "Brave Search", "tavily": "Tavily"}

# Failure categories, most serious first (the order used to choose the hint).
_CONFIG, _AUTH, _QUOTA, _RATE, _TIMEOUT, _NETWORK, _SERVER, _MALFORMED, _OTHER = (
    "config", "auth", "quota", "rate_limit", "timeout", "network", "server", "malformed", "other")
_SEVERITY = {_CONFIG: 0, _AUTH: 1, _QUOTA: 2, _RATE: 3, _TIMEOUT: 4, _NETWORK: 4, _SERVER: 5, _MALFORMED: 6, _OTHER: 6}

# What each category means, said about one named provider.
_PROVIDER_SENTENCE = {
    _CONFIG: "{label} is not configured",
    _AUTH: "{label} rejected the API key",
    _QUOTA: "{label} reached its usage or plan limit",
    _RATE: "{label} hit its rate limit",
    _TIMEOUT: "{label} did not respond in time",
    _NETWORK: "{label} could not be reached (network problem)",
    _SERVER: "{label} was temporarily unavailable",
    _MALFORMED: "{label} returned an unexpected response",
    _OTHER: "{label} returned an error",
}

# Real report line: "[family] provider: 'query' -> error" (router.FamilyOutcome.errors
# with the family prefix added by acquisition). The query is a Python repr and is
# never interpreted: only the error part is classified.
_REPORT_LINE_RE = re.compile(
    r"""^\[(?P<family>[^\]]*)\]\s+(?P<provider>[A-Za-z][A-Za-z0-9_]*):\s+"""
    r"""(?P<query>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")\s+->\s+(?P<error>.*)$""",
    re.DOTALL,
)
_LEGACY_LINE_RE = re.compile(r"^\[[^\]]*\]\s+(?P<error>.*)$", re.DOTALL)
_HTTP_STATUS_RE = re.compile(r"^HTTP (\d{3})\b")
_SKIP_REASON_RE = re.compile(r"^(?P<provider>[A-Za-z][A-Za-z0-9_]*) skipped: unavailable earlier in this run \((?P<reason>[^)]*)\)$")


def _no_key_hint() -> str:
    keys = list(_PROVIDER_KEYS.values())
    if not keys:
        return "Configure a research provider API key, then restart Demo Mode."
    named = [f"{keys[0]} (primary)"] + keys[1:]
    listed = named[0] if len(named) == 1 else ", ".join(named[:-1]) + " or " + named[-1]
    return f"Set at least one of {listed} in the environment, then restart Demo Mode."


def _category_for_status(status: int) -> str:
    if status in (401, 403):
        return _AUTH
    if status in (402, 432, 433):
        return _QUOTA
    if status == 429:
        return _RATE
    if 500 <= status <= 599:
        return _SERVER
    return _OTHER


def _categorize(error: str) -> tuple[str, int | None]:
    """Category (and HTTP status, if any) of ONE provider error string. Only
    the error text is looked at -- never the query or organization name."""
    text = (error or "").strip()
    match = _HTTP_STATUS_RE.match(text)
    if match:
        status = int(match.group(1))
        return _category_for_status(status), status
    lowered = text.lower()
    if lowered.endswith("not configured"):
        return _CONFIG, None
    if lowered.startswith("timeout") or "timed out" in lowered:
        return _TIMEOUT, None
    if lowered.startswith("transport error"):
        return _NETWORK, None
    if lowered.startswith("malformed response") or lowered.startswith("unexpected error"):
        return _MALFORMED, None
    skipped = re.match(r"^skipped: unavailable earlier in this run \((.*)\)$", text)
    if skipped:
        return _categorize(skipped.group(1))
    return _OTHER, None


def _label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, "Another research provider")


def _chain_position(provider: str) -> tuple[int, str]:
    order = list(_PROVIDER_KEYS)
    return (order.index(provider), provider) if provider in order else (len(order), provider)


def failure_from_exception(exc: BaseException) -> ResearchFailure:
    """Plain-language cause for an exception raised by the live run. The raw
    exception text is never shown (it can contain provider payloads)."""
    text = str(exc or "")
    lowered = text.lower()
    if "api_key" in lowered and ("not configured" in lowered or "missing" in lowered):
        return ResearchFailure(FAILURE_TITLE, "The research provider API key is not configured.", _no_key_hint())
    if "timeout" in lowered or "timed out" in lowered:
        return ResearchFailure(FAILURE_TITLE, "The research run timed out.", "Check the connection and retry.")
    if any(k in lowered for k in ("connection", "network", "resolve", "unreachable", "ssl", "proxy")):
        return ResearchFailure(FAILURE_TITLE, "A network problem prevented live research.", "Check the connection and retry.")
    return ResearchFailure(
        FAILURE_TITLE, "The research pipeline stopped unexpectedly.",
        "Retry the research. If it fails again, run Account Radar normally to investigate.",
    )


def _diag(report: dict[str, Any]) -> dict[str, Any]:
    return report.get("acquisition_diagnostics") or {}


def _provider_failures(diag: dict[str, Any]) -> tuple[dict[str, tuple[str, int | None]], list[tuple[str, int | None]]]:
    """Worst failure per named provider, from the error lines, the B5.3
    skip markers (fallback_reason) and skipped_providers; plus the failures
    of older-format lines that name no provider."""
    per_provider: dict[str, tuple[str, int | None]] = {}
    unattributed: list[tuple[str, int | None]] = []

    def note(provider: str, failure: tuple[str, int | None]) -> None:
        current = per_provider.get(provider)
        if current is None or _SEVERITY[failure[0]] < _SEVERITY[current[0]]:
            per_provider[provider] = failure

    for line in diag.get("errors") or []:
        text = str(line)
        match = _REPORT_LINE_RE.match(text)
        if match:
            note(match.group("provider"), _categorize(match.group("error")))
            continue
        legacy = _LEGACY_LINE_RE.match(text)
        unattributed.append(_categorize(legacy.group("error") if legacy else text))

    for family in diag.get("families") or []:
        if not isinstance(family, dict):
            continue
        skipped = family.get("skipped_providers")
        if isinstance(skipped, dict):
            for provider, reason in skipped.items():
                note(str(provider), _categorize(str(reason)))
        match = _SKIP_REASON_RE.match(str(family.get("fallback_reason") or ""))
        if match:
            note(match.group("provider"), _categorize(match.group("reason")))
    return per_provider, unattributed


def _legacy_failure(failures: list[tuple[str, int | None]]) -> ResearchFailure:
    """Older reports (no provider names in the error lines): the original
    single-cause wording, picked by the same severity order."""
    category = min((c for c, _ in failures), key=_SEVERITY.__getitem__)
    if category == _CONFIG:
        cause, hint = "The research provider API key is not configured.", _no_key_hint()
    elif category == _AUTH:
        cause, hint = "The research provider rejected the API key.", "Check the configured key, then restart Demo Mode."
    elif category in (_QUOTA, _RATE):
        cause, hint = "The research provider's usage limit was reached.", "Wait a moment or check the provider plan, then retry."
    elif category == _TIMEOUT:
        cause, hint = "The research provider did not respond in time.", "Check the connection and retry."
    elif category == _NETWORK:
        cause, hint = "A network problem prevented live research.", "Check the connection and retry."
    else:
        cause, hint = "The research provider returned errors.", "Retry the research."
    return ResearchFailure(FAILURE_TITLE, cause, hint)


def _hint(category: str, providers: list[str]) -> str:
    keys = [_PROVIDER_KEYS[p] for p in providers if p in _PROVIDER_KEYS]
    keys_text = " and ".join(keys)
    if category == _CONFIG:
        return _no_key_hint()
    if category == _AUTH:
        return f"Check {keys_text}, then restart Demo Mode." if keys else "Check the configured API key, then restart Demo Mode."
    if category == _QUOTA:
        names = " and ".join(_label(p) for p in providers)
        suffix = f" ({keys_text})" if keys else ""
        return f"Check the {names} plan or remaining usage{suffix}, then retry."
    if category == _RATE:
        return "Wait a moment, then retry."
    if category in (_TIMEOUT, _NETWORK):
        return "Check the connection and retry."
    if category == _SERVER:
        return "Wait a moment, then retry."
    return "Retry the research."


def failure_from_report(report: dict[str, Any]) -> ResearchFailure | None:
    """A completed run that retrieved NOTHING from the providers is a failed
    live run, not an account result (providers record errors instead of
    raising). A run that retrieved results -- even a thin, insufficient
    corpus -- is a real current result and is shown as such.

    The explanation names each failed provider in chain order with its
    configuration variable where relevant. It is built only from provider
    names, failure categories and HTTP status codes: never raw error text,
    provider payloads, search queries or API keys."""
    diag = _diag(report)
    returned = diag.get("total_results_returned")
    if returned is None or returned > 0:
        return None
    per_provider, unattributed = _provider_failures(diag)
    if not per_provider:
        if unattributed:
            return _legacy_failure(unattributed)
        return ResearchFailure(
            FAILURE_TITLE, "The research providers returned no results for this organization.",
            "Check the organization name and country, then retry.",
        )

    ordered = sorted(per_provider, key=_chain_position)
    sentences = []
    for provider in ordered:
        category, status = per_provider[provider]
        sentence = _PROVIDER_SENTENCE[category].format(label=_label(provider))
        sentences.append(sentence + (f" (HTTP {status})." if status else "."))
    worst = min((per_provider[p][0] for p in ordered), key=_SEVERITY.__getitem__)
    worst_providers = [p for p in ordered if _SEVERITY[per_provider[p][0]] == _SEVERITY[worst]]
    return ResearchFailure(FAILURE_TITLE, " ".join(sentences), _hint(worst, worst_providers))


# ---------------------------------------------------------------------------
# Partial provider failure (a run that DID retrieve results)
# ---------------------------------------------------------------------------

PARTIAL_COVERAGE_TITLE = "Research coverage may be incomplete."
PARTIAL_COVERAGE_FOOTER = "Results shown come from the providers that responded."

# Short noun phrase for each category, used inside parentheses.
_CATEGORY_NOUN = {
    _AUTH: "API key rejected",
    _QUOTA: "usage or plan limit reached",
    _RATE: "rate limit",
    _TIMEOUT: "timed out",
    _NETWORK: "network problem",
    _SERVER: "service temporarily unavailable",
    _MALFORMED: "unexpected response",
    _OTHER: "provider error",
}


@dataclass(frozen=True)
class PartialCoverage:
    """A completed run with results in which at least one provider failed
    or was skipped. `message` is safe to show: provider names, categories,
    HTTP status codes and counts only."""

    message: str
    failed_providers: tuple[str, ...]
    skipped_providers: tuple[str, ...]


def _describe(failure: tuple[str, int | None]) -> str:
    category, status = failure
    return _CATEGORY_NOUN.get(category, _CATEGORY_NOUN[_OTHER]) + (f", HTTP {status}" if status else "")


def _searches(failed: int, sent: Any) -> str:
    if isinstance(sent, int) and not isinstance(sent, bool) and sent >= failed:
        return f"{failed} of {sent} searches failed"
    return f"{failed} search failed" if failed == 1 else f"{failed} searches failed"


def partial_coverage_from_report(report: dict[str, Any]) -> PartialCoverage | None:
    """Warning for a run that retrieved results although at least one
    provider failed or was skipped by run-level provider health. Returns None
    for a fully healthy run, for a run that retrieved nothing (that is a total
    failure, handled by failure_from_report and unchanged) and for reports
    without the diagnostics to tell. A provider that answered with zero
    results and no error is never treated as failed. "not configured" lines
    (older reports) are not coverage failures."""
    diag = _diag(report)
    returned = diag.get("total_results_returned")
    if not isinstance(returned, (int, float)) or isinstance(returned, bool) or returned <= 0:
        return None

    failures: dict[str, list[tuple[str, int | None]]] = {}
    unattributed = 0
    for line in diag.get("errors") or []:
        text = str(line)
        match = _REPORT_LINE_RE.match(text)
        if match:
            failure = _categorize(match.group("error"))
            if failure[0] != _CONFIG:
                failures.setdefault(match.group("provider"), []).append(failure)
            continue
        legacy = _LEGACY_LINE_RE.match(text)
        if _categorize(legacy.group("error") if legacy else text)[0] != _CONFIG:
            unattributed += 1

    skips: dict[str, tuple[str, int | None]] = {}

    def note_skip(provider: str, failure: tuple[str, int | None]) -> None:
        current = skips.get(provider)
        if current is None or _SEVERITY[failure[0]] < _SEVERITY[current[0]]:
            skips[provider] = failure

    for family in diag.get("families") or []:
        if not isinstance(family, dict):
            continue
        skipped = family.get("skipped_providers")
        if isinstance(skipped, dict):
            for provider, reason in skipped.items():
                note_skip(str(provider), _categorize(str(reason)))
        match = _SKIP_REASON_RE.match(str(family.get("fallback_reason") or ""))
        if match:
            note_skip(match.group("provider"), _categorize(match.group("reason")))

    if not failures and not skips and not unattributed:
        return None

    requests = diag.get("provider_requests")
    requests = requests if isinstance(requests, dict) else {}
    sentences: list[str] = []
    for provider in sorted(set(failures) | set(skips), key=_chain_position):
        label = _label(provider)
        provider_failures = failures.get(provider) or []
        skip = skips.get(provider)
        if provider_failures:
            worst = min(provider_failures, key=lambda f: _SEVERITY[f[0]])
            mixed = len({f[0] for f in provider_failures}) > 1
            detail = ("most serious: " if mixed else "") + _describe(worst)
            sentence = f"{label}: {_searches(len(provider_failures), requests.get(provider))} ({detail})"
            if skip is not None:
                sentence += " and it was then skipped for the rest of the run"
                if _describe(skip) != _describe(worst):
                    sentence += f" ({_describe(skip)})"
            sentences.append(sentence + ".")
        else:
            sentences.append(f"{label} was skipped for the rest of the run ({_describe(skip)}).")
    if unattributed:
        sentences.append(f"Some searches returned errors ({unattributed}).")

    message = " ".join([PARTIAL_COVERAGE_TITLE, *sentences, PARTIAL_COVERAGE_FOOTER])
    return PartialCoverage(
        message=message,
        failed_providers=tuple(p for p in sorted(failures, key=_chain_position)),
        skipped_providers=tuple(p for p in sorted(skips, key=_chain_position)),
    )


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def banner_html() -> str:
    return (
        '<div class="ar-demo-banner" role="status">'
        '<span class="ar-demo-live">LIVE DEMO MODE</span>'
        '<span class="ar-demo-banner-text">Current public-domain research &middot; Fresh analysis</span>'
        '</div>'
    )


def status_html(*, researched_at: str | None, sources: int, developments: int,
                opportunities: int, decision: str, confidence: str) -> str:
    """Compact strip shown after a completed live run. Every value is passed
    in from the current result; nothing is hard-coded."""
    when = format_researched_at(researched_at)
    facts = [
        ("Analyzed", when or "–"),
        ("Qualified sources", sources),
        ("Account-specific developments", developments),
        ("Opportunities", opportunities),
        ("Decision", f"{decision} · {confidence}" if confidence else decision),
    ]
    cells = "".join(
        f'<div class="ar-demo-fact"><div class="ar-demo-fact-label">{_e(k)}</div>'
        f'<div class="ar-demo-fact-value">{_e(v)}</div></div>'
        for k, v in facts
    )
    return (
        '<div class="ar-demo-status">'
        '<div class="ar-demo-status-head"><span class="ar-demo-live">LIVE RESEARCH</span>'
        '<span class="ar-demo-banner-text">Research complete &middot; results reflect public information at the time of analysis</span></div>'
        f'<div class="ar-demo-facts">{cells}</div>'
        '</div>'
    )


def failure_html(failure: ResearchFailure, organization: str, country: str) -> str:
    target = organization + (f", {country}" if country else "")
    return (
        '<div class="ar-panel ar-demo-fail" role="alert">'
        f'<div class="ar-demo-fail-title">{_e(failure.title)}</div>'
        f'<div class="ar-demo-fail-text"><strong>Likely cause:</strong> {_e(failure.cause)}</div>'
        f'<div class="ar-demo-fail-text">{_e(failure.hint)}</div>'
        f'<div class="ar-demo-fail-meta">Account: {_e(target)}</div>'
        '</div>'
    )

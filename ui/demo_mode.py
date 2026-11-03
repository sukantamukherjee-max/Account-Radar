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


def failure_from_exception(exc: BaseException) -> ResearchFailure:
    """Plain-language cause for an exception raised by the live run. The raw
    exception text is never shown (it can contain provider payloads)."""
    text = str(exc or "")
    lowered = text.lower()
    if "api_key" in lowered and ("not configured" in lowered or "missing" in lowered):
        return ResearchFailure(
            FAILURE_TITLE, "The research provider API key is not configured.",
            "Set TAVILY_API_KEY in the environment, then restart Demo Mode.",
        )
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


def failure_from_report(report: dict[str, Any]) -> ResearchFailure | None:
    """A completed run that retrieved NOTHING from the providers is a failed
    live run, not an account result (providers record errors instead of
    raising). A run that retrieved results -- even a thin, insufficient
    corpus -- is a real current result and is shown as such."""
    diag = _diag(report)
    returned = diag.get("total_results_returned")
    if returned is None or returned > 0:
        return None
    errors = " ".join(str(e) for e in diag.get("errors") or []).lower()
    if not errors:
        return ResearchFailure(
            FAILURE_TITLE, "The research providers returned no results for this organization.",
            "Check the organization name and country, then retry.",
        )
    if "not configured" in errors:
        cause, hint = "The research provider API key is not configured.", "Set TAVILY_API_KEY in the environment, then restart Demo Mode."
    elif "http 401" in errors or "http 403" in errors:
        cause, hint = "The research provider rejected the API key.", "Check the configured key, then restart Demo Mode."
    elif "http 429" in errors or "http 432" in errors or "http 433" in errors:
        cause, hint = "The research provider's usage limit was reached.", "Wait a moment or check the provider plan, then retry."
    elif "timeout" in errors:
        cause, hint = "The research provider did not respond in time.", "Check the connection and retry."
    elif "transport error" in errors:
        cause, hint = "A network problem prevented live research.", "Check the connection and retry."
    else:
        cause, hint = "The research provider returned errors.", "Retry the research."
    return ResearchFailure(FAILURE_TITLE, cause, hint)


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

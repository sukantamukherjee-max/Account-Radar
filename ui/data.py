from __future__ import annotations

from typing import Any


def extract_organizations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Multi-account selector support: like `normalize_entry`, but returns
    EVERY organization entry present in a runner-shape payload (not only
    the first) -- the data source the account selector is populated from.
    A single-entry payload (already `{"report": ..., "ai_synthesis": ...}`
    shaped, e.g. straight from a live research run) still comes back as a
    one-item list, so every caller can treat both shapes uniformly instead
    of special-casing "was this a multi-account report or not".

    Purely structural: no organization name, count, or shape is assumed
    beyond what the payload itself contains.
    """
    if "organizations" in payload:
        orgs = payload.get("organizations") or []
        if not orgs:
            raise ValueError("The report contains no organizations.")
        return list(orgs)
    if "report" in payload and "ai_synthesis" in payload:
        return [payload]
    raise ValueError("Unsupported report format.")


def normalize_entry(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept a Phase 1D/1F full report or a single organization entry.

    Unchanged behavior (including both error messages): this is now a thin
    wrapper around `extract_organizations`, kept for existing callers that
    only ever want "the first/only account" -- e.g. a live research result,
    which is always single-account.
    """
    return extract_organizations(payload)[0]


def account_label(entry: dict[str, Any], index: int) -> str:
    """The display name for one account entry: whatever
    `organization_name` its own report carries, or a generic positional
    fallback ("Account N") only when that's missing/blank. Never a
    hardcoded organization name -- driven entirely by the loaded JSON."""
    name = ((entry or {}).get("report") or {}).get("organization_name")
    name = (name or "").strip()
    return name or f"Account {index + 1}"


def account_labels(accounts: list[dict[str, Any]]) -> list[str]:
    """Human-readable, guaranteed-unique display labels for a list of
    account entries, in the same order -- the option list a multi-account
    selector renders. Built purely from `account_label` above; duplicate
    organization names (two accounts that happen to share one) are
    disambiguated with a "(2)"-style suffix so a selector built from this
    list can always map a chosen label back to exactly one account.
    """
    raw = [account_label(a, i) for i, a in enumerate(accounts)]
    seen: dict[str, int] = {}
    labels: list[str] = []
    for name in raw:
        seen[name] = seen.get(name, 0) + 1
        labels.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return labels


def source_is_linkedin(url: str) -> bool:
    u = (url or "").lower()
    return "linkedin.com/in/" in u or "linkedin.com/company/" in u or "linkedin.com/jobs/" in u


def evidence_level_label(value: str) -> str:
    return {
        "direct_evidence": "Direct evidence",
        "strong_inference": "Strong inference",
        "weak_relevance": "Weak relevance",
    }.get(value, value.replace("_", " ").title())


def priority_label(value: str) -> str:
    return {"high": "HIGH", "medium": "MEDIUM", "watch": "WATCH"}.get(value, value.upper())


def epistemic_status_label(value: str | None) -> str:
    return {
        "confirmed_evidence": "Confirmed evidence",
        "inference_hypothesis": "Inference hypothesis",
    }.get(value or "", (value or "").replace("_", " ").title() or "Unknown")


def evidence_category_label(value: str | None) -> str:
    return {
        "organizational_technology_event": "Organizational technology event",
        "procurement_signal": "Procurement signal",
        "business_signal_inference": "Business-signal inference",
        "current_state_technology": "Current-state technology",
        "stakeholder_context": "Stakeholder context",
        "hiring_intent_context": "Hiring-intent context",
        "incidental_mention": "Incidental mention",
        "customer_requirement_context": "Customer requirement context",
        "existing_capability_context": "Existing capability (no current signal)",
    }.get(value or "", (value or "").replace("_", " ").title() or "Unknown")


def capability_summary_rows(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Phase 2 hardening (fixes the pre-existing UNDERSTAND rendering bug):
    `report["capability_summary"]` is a nested structure --
    {capability_id: {capability_name, direct_evidence: [...], strong_inference: [...], weak_relevance: [...]}}
    -- with a full mapping dict per evidence item, not something safe to
    `str()` directly into the UI. This flattens it into one row per
    capability with simple counts a template can render.
    """
    rows: list[dict[str, Any]] = []
    for cap_id, bucket in (summary or {}).items():
        bucket = bucket or {}
        rows.append(
            {
                "capability_id": cap_id,
                "capability_name": bucket.get("capability_name", cap_id),
                "direct_count": len(bucket.get("direct_evidence") or []),
                "strong_count": len(bucket.get("strong_inference") or []),
                "weak_count": len(bucket.get("weak_relevance") or []),
            }
        )
    return rows



def _parse_research_date(value: Any) -> "datetime | None":
    """Parse common ISO/date values without failing the report."""
    if not value:
        return None
    try:
        from datetime import datetime
        text = str(value).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


def research_recency(published_at: Any, *, now: "datetime | None" = None) -> dict[str, Any]:
    """Return transparent freshness metadata for research evidence.

    Freshness is a relevance modifier, not a replacement for evidence quality.
    The score is intentionally conservative: information older than six months
    loses prevalence, and information older than a year is materially down-ranked.
    """
    from datetime import datetime, timezone
    published = _parse_research_date(published_at)
    if published is None:
        return {
            "age_days": None,
            "age_label": "Date unavailable",
            "freshness_factor": 0.75,
            "freshness_band": "date_unavailable",
        }

    reference = now or datetime.now(timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    age_days = max(0, (reference - published).days)

    if age_days <= 30:
        factor, band = 1.00, "very_fresh"
    elif age_days <= 90:
        factor, band = 0.95, "fresh"
    elif age_days <= 180:
        factor, band = 0.90, "recent"
    elif age_days <= 365:
        factor, band = 0.70, "aging"
    else:
        factor, band = 0.45, "old"

    if age_days < 60:
        label = f"{age_days} days old"
    elif age_days < 365:
        label = f"{age_days // 30} months old"
    else:
        label = f"{age_days // 365} year(s) old"

    return {
        "age_days": age_days,
        "age_label": label,
        "freshness_factor": factor,
        "freshness_band": band,
    }

def research_items(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten retained public research into editable intelligence items.

    Chain mappings are the primary evidence objects because they already carry
    the signal -> business impact -> technology requirement traceability.
    Standalone signals are also surfaced when they do not have a matching
    source URL already represented by a chain item. This is intentionally
    structural: no customer, industry, vendor, or capability is hardcoded.
    """
    report = (entry or {}).get("report", {})
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for row in report.get("chain", []) or []:
        url = row.get("source_url") or ""
        title = row.get("source_title") or url or "Research evidence"
        key = ("chain", url, title)
        if key in seen:
            continue
        seen.add(key)
        # Prefer the exact signal text retained on the chain. If the chain
        # does not retain it, only recover a signal from the same source when
        # its matched terms overlap the chain mapping. Never substitute an
        # unrelated signal from the same page just because the URL matches.
        text = row.get("customer_signal", {}).get("text") or row.get("signal_text") or ""
        source_context = row.get("supporting_context") or ""
        evidence_excerpt = row.get("evidence_excerpt") or ""
        if not text:
            chain_terms = {str(t).strip().lower() for t in (row.get("matched_terms") or []) if str(t).strip()}
            candidates = []
            for candidate in report.get("signals", []) or []:
                if (candidate.get("source_url") or "") != url:
                    continue
                candidate_terms = {str(t).strip().lower() for t in (candidate.get("matched_terms") or []) if str(t).strip()}
                if chain_terms and candidate_terms.intersection(chain_terms):
                    candidates.append(candidate)
            if candidates:
                text = candidates[0].get("text") or candidates[0].get("snippet") or ""
        items.append({
            "id": f"chain:{len(items)}",
            "kind": "evidence",
            "title": title,
            "source_url": url,
            "source": "Public research",
            "published_at": row.get("published_at") or row.get("publication_date") or row.get("date"),
            "text": evidence_excerpt if "evidence_excerpt" in row else (text or ""),
            "source_context": source_context,
            "why_relevant": row.get("business_impact") or row.get("technology_requirement") or "",
            "evidence_level": row.get("evidence_level", ""),
            "evidence_category": row.get("evidence_category", ""),
            "epistemic_status": row.get("epistemic_status", ""),
            "confidence": row.get("confidence"),
            "capability": (row.get("potential_otech_capability") or {}).get("capability_name", ""),
            "matched_terms": row.get("matched_terms", []) or [],
            "original": row,
        })

    for signal in report.get("signals", []) or []:
        url = signal.get("source_url") or ""
        title = signal.get("source_title") or url or "Research signal"
        key = ("signal", url, title)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "id": f"signal:{len(items)}",
            "kind": "signal",
            "title": title,
            "source_url": url,
            "source": "Public research",
            "published_at": signal.get("published_at") or signal.get("publication_date") or signal.get("date"),
            "text": signal.get("text") or signal.get("snippet") or "",
            "why_relevant": signal.get("signal_type", "").replace("_", " ").title(),
            "evidence_level": signal.get("evidence_level", ""),
            "evidence_category": signal.get("evidence_category", ""),
            "epistemic_status": signal.get("epistemic_status", ""),
            "confidence": signal.get("confidence"),
            "capability": "",
            "matched_terms": signal.get("matched_terms", []) or [],
            "original": signal,
        })
    for item in items:
        item["recency"] = research_recency(item.get("published_at"))
    return items

def weak_evidence_total(summary: dict[str, Any] | None) -> int:
    return sum(len((bucket or {}).get("weak_relevance") or []) for bucket in (summary or {}).values())

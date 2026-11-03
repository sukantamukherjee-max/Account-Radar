"""Capture the real pages used by the Golden Case Validation Set.

Run ONCE from the repository root, on a machine with network access:

    py tests/fixtures/golden/capture_snapshots.py

For each target this stores exactly what the pipeline itself would store --
the text returned by research.fetcher.fetch_page() (whitespace-collapsed,
boilerplate-stripped, capped at MAX_CONTENT_CHARS) -- in snapshots/<name>.txt,
plus snapshots/<name>.meta.json with the URL, fetch status, length, SHA-256
and capture time. After that the golden tests never need the live web.

Design rules:
  * Existing snapshots are NEVER overwritten (fixtures must stay frozen);
    pass --force to deliberately re-capture, then review the git diff.
  * A failed or empty fetch never writes anything.
  * The fetch function is injectable, so the tool itself is unit-tested
    without network access (see tests/test_golden_cases.py).

Note: snapshots contain third-party page text. Keep the repository private
or confirm the sources' terms before publishing it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.sources import AccessStatus  # noqa: E402

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"

TARGETS: dict[str, str] = {
    "aufait_rfp_article": "https://aufaittechnologies.com/blog/rfp-management-software",
    "zawya_otech_kaspersky": (
        "https://www.zawya.com/en/press-release/companies-news/"
        "otech-strengthens-omans-locally-hosted-cybersecurity-capabilities-through-new-collaboration-with-kaspersky-1021726"
    ),
    "oman_air_fleet_expansion": (
        "https://www.eplaneai.com/it/news/oman-air-plans-to-add-six-widebody-aircraft-by-2028-to-expand-global-network"
    ),
}


def _default_fetch(url: str):
    from research.fetcher import fetch_page

    return fetch_page(url)


def capture(*, fetch=None, out_dir: Path = SNAPSHOT_DIR, targets: dict[str, str] = TARGETS, force: bool = False) -> dict[str, str]:
    """Capture every target; returns {name: 'captured' | 'skipped_existing' | 'failed: <reason>'}."""
    fetch = fetch or _default_fetch
    out_dir = Path(out_dir)
    results: dict[str, str] = {}
    for name, url in targets.items():
        text_path = out_dir / f"{name}.txt"
        meta_path = out_dir / f"{name}.meta.json"
        if text_path.exists() and not force:
            results[name] = "skipped_existing"
            continue
        content, status = fetch(url)
        if status != AccessStatus.FETCHED or not (content or "").strip():
            results[name] = f"failed: status={getattr(status, 'value', status)} empty={not (content or '').strip()}"
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        text_path.write_text(content, encoding="utf-8", newline="\n")
        meta = {
            "url": url,
            "status": getattr(status, "value", str(status)),
            "length": len(content),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
        results[name] = "captured"
    return results


def main(argv: list[str]) -> int:
    force = "--force" in argv
    results = capture(force=force)
    width = max(len(n) for n in results)
    for name, outcome in results.items():
        print(f"{name:<{width}}  {outcome}")
    return 1 if any(o.startswith("failed") for o in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

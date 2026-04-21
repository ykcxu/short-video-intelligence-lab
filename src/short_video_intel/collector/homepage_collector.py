from __future__ import annotations

"""Homepage collection entrypoint with minimal HTML-based video extraction."""

from datetime import datetime, timezone
from typing import Any

from ..browser.page_runtime import (
    detect_playwright,
    extract_video_candidates_with_diagnostics,
    run_playwright_homepage_probe,
)


def collect_homepage_videos(config: Any, homepage_url: str, max_items: int = 50) -> dict[str, Any]:
    """Collect a homepage video listing with a graceful Playwright fallback.

    Parameters
    ----------
    config:
        Application config object. Only the browser settings needed for the
        minimal probe are accessed.
    homepage_url:
        The homepage URL to collect.
    max_items:
        Reserved for the future full collector. The current skeleton keeps the
        parameter so later implementations can reuse the same API.

    Returns
    -------
    dict[str, Any]
        A stable payload with the following top-level keys:

        - ``backend``: identifies the collection backend used
        - ``homepage_url``: normalized homepage URL
        - ``scanned_at``: UTC ISO-8601 timestamp
        - ``videos``: list of discovered video URL candidates
        - ``warnings``: human-readable notes and recoverable issues
    """

    normalized_homepage_url = _normalize_homepage_url(homepage_url)
    scanned_at = _now_iso()

    if max_items <= 0:
        return {
            "backend": "invalid-argument",
            "homepage_url": normalized_homepage_url,
            "scanned_at": scanned_at,
            "videos": [],
            "diagnostics": _empty_diagnostics(normalized_homepage_url),
            "extraction_version": "homepage-extract.v2",
            "warnings": ["max_items must be greater than 0"],
        }

    if not detect_playwright():
        return {
            "backend": "stub/no_playwright",
            "homepage_url": normalized_homepage_url,
            "scanned_at": scanned_at,
            "videos": [],
            "diagnostics": _empty_diagnostics(normalized_homepage_url),
            "extraction_version": "homepage-extract.v2",
            "warnings": [
                "playwright is not installed; returning a structured stub result",
                f"max_items={max_items}",
            ],
        }

    try:
        probe = run_playwright_homepage_probe(config, normalized_homepage_url)
    except Exception as exc:  # pragma: no cover - runtime fallback path
        return {
            "backend": "playwright/error",
            "homepage_url": normalized_homepage_url,
            "scanned_at": scanned_at,
            "videos": [],
            "diagnostics": _empty_diagnostics(normalized_homepage_url),
            "extraction_version": "homepage-extract.v2",
            "warnings": [
                f"playwright probe failed: {exc!r}",
                f"max_items={max_items}",
            ],
        }

    extraction = extract_video_candidates_with_diagnostics(
        probe.get("page_html", ""),
        probe.get("final_url", normalized_homepage_url),
        max_items=max_items,
    )
    videos = extraction["videos"]
    diagnostics = extraction["diagnostics"]
    warnings = [
        "homepage html scanned with regex-based video URL extraction",
        f"aweme_id_regex_matches={diagnostics.get('aweme_id_matches', 0)}",
        f"aweme_id_resolution={'hit' if diagnostics.get('aweme_id_matches', 0) else 'miss'}",
        f"page_title={probe.get('title')!r}",
        f"final_url={probe.get('final_url')!r}",
        f"http_status={probe.get('http_status')!r}",
        f"extracted_count={len(videos)}",
        f"max_items={max_items}",
    ]
    return {
        "backend": "playwright/html-regex",
        "homepage_url": normalized_homepage_url,
        "scanned_at": scanned_at,
        "videos": videos,
        "diagnostics": diagnostics,
        "extraction_version": diagnostics.get("extraction_version", "homepage-extract.v2"),
        "warnings": warnings,
    }


def _normalize_homepage_url(homepage_url: str) -> str:
    normalized = str(homepage_url).strip()
    if not normalized:
        raise ValueError("homepage_url must not be empty")
    return normalized


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_diagnostics(homepage_url: str) -> dict[str, Any]:
    return {
        "extraction_version": "homepage-extract.v2",
        "total_matches": 0,
        "unique_video_ids": 0,
        "aweme_id_matches": 0,
        "merged_unique_video_ids": 0,
        "invalid_candidates": 0,
        "duplicate_candidates": 0,
        "homepage_origin": homepage_url,
    }

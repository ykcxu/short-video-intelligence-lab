from __future__ import annotations

"""
Homepage collection skeleton.

This module provides the public collection entrypoint for homepage video
discovery. The current implementation is intentionally conservative:

- No hard dependency on Playwright.
- No complex selectors or full scraping logic yet.
- Stable return structure for downstream storage / analysis code.

The return payload is designed to remain compatible when the real collector is
implemented later, so callers can already build storage, retries, and analysis
around it.
"""

from datetime import datetime, timezone
from typing import Any

from ..browser.page_runtime import detect_playwright, run_playwright_homepage_probe


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
        - ``videos``: list of discovered videos, currently empty
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
            "warnings": ["max_items must be greater than 0"],
        }

    if not detect_playwright():
        return {
            "backend": "stub/no_playwright",
            "homepage_url": normalized_homepage_url,
            "scanned_at": scanned_at,
            "videos": [],
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
            "warnings": [
                f"playwright probe failed: {exc!r}",
                f"max_items={max_items}",
            ],
        }

    warnings = [
        "minimal homepage probe only; video extraction is intentionally disabled in this skeleton",
        f"page_title={probe.get('title')!r}",
        f"final_url={probe.get('final_url')!r}",
        f"http_status={probe.get('http_status')!r}",
        f"max_items={max_items}",
    ]
    return {
        "backend": probe.get("backend", "playwright/minimal"),
        "homepage_url": normalized_homepage_url,
        "scanned_at": scanned_at,
        "videos": [],
        "warnings": warnings,
    }


def _normalize_homepage_url(homepage_url: str) -> str:
    normalized = str(homepage_url).strip()
    if not normalized:
        raise ValueError("homepage_url must not be empty")
    return normalized


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

from __future__ import annotations

from datetime import datetime, timezone
from importlib.util import find_spec
from typing import Any

from ..config import AppConfig


def collect_video_comments(
    config: AppConfig,
    video_url: str,
    max_pages: int = 3,
) -> dict[str, Any]:
    """
    Collect a minimal, stable comment scan payload for a video.

    The implementation favors predictable structure over completeness:
    - no Playwright: deterministic stub with is_incomplete=True
    - Playwright present: visit the page once, return empty comment data

    Parameters
    ----------
    config:
        Application configuration.
    video_url:
        Target video URL.
    max_pages:
        Requested pagination depth cap for future expansion.

    Returns
    -------
    dict[str, Any]
        Normalized scan payload with the following keys:
        - video_url
        - collected_at
        - comments
        - replies
        - scan_meta
    """

    collected_at = _now_iso()

    if not _has_playwright():
        return {
            "video_url": video_url,
            "collected_at": collected_at,
            "comments": [],
            "replies": [],
            "scan_meta": {
                "pagination_depth": 0,
                "is_incomplete": True,
                "stop_reason": "no_playwright",
                "backend": "stub:no_playwright",
            },
        }

    return _collect_with_playwright_comments(
        config=config,
        video_url=video_url,
        max_pages=max_pages,
        collected_at=collected_at,
    )


def _collect_with_playwright_comments(
    config: AppConfig,
    video_url: str,
    max_pages: int,
    collected_at: str,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception:
        return {
            "video_url": video_url,
            "collected_at": collected_at,
            "comments": [],
            "replies": [],
            "scan_meta": {
                "pagination_depth": 0,
                "is_incomplete": True,
                "stop_reason": "playwright_import_failed",
                "backend": "stub:playwright_import_failed",
            },
        }

    timeout_ms = int(getattr(config.browser, "timeout_ms", 30_000))
    headless = bool(getattr(config.browser, "headless", True))
    user_agent = getattr(config.browser, "user_agent", None)
    storage_state = getattr(config.browser, "storage_state", None)

    warnings: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context_kwargs: dict[str, Any] = {"locale": getattr(config.browser, "locale", "zh-CN")}
            if user_agent:
                context_kwargs["user_agent"] = user_agent
            if storage_state:
                context_kwargs["storage_state"] = str(storage_state)

            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            try:
                page.goto(video_url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
                except PlaywrightTimeoutError:
                    warnings.append("networkidle timeout while probing comment page")
            finally:
                context.close()
                browser.close()
    except Exception as exc:  # pragma: no cover - runtime dependent
        warnings.append(f"playwright placeholder comment scan failed: {exc!s}")
        return {
            "video_url": video_url,
            "collected_at": collected_at,
            "comments": [],
            "replies": [],
            "scan_meta": {
                "pagination_depth": 0,
                "is_incomplete": True,
                "stop_reason": "playwright_placeholder_error",
                "backend": "playwright:placeholder-error",
                "warnings": warnings,
            },
        }

    return {
        "video_url": video_url,
        "collected_at": collected_at,
        "comments": [],
        "replies": [],
        "scan_meta": {
            "pagination_depth": min(max(0, int(max_pages)), 1),
            "is_incomplete": True,
            "stop_reason": "placeholder_only",
            "backend": "playwright:placeholder",
            "warnings": warnings,
        },
    }


def _has_playwright() -> bool:
    return find_spec("playwright") is not None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

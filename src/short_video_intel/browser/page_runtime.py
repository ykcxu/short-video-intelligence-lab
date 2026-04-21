from __future__ import annotations

"""
Browser page runtime helpers for homepage collection.

This module intentionally keeps the implementation small and replaceable:

- It does not require Playwright at import time.
- It exposes a single minimal probe helper that can be swapped out later.
- It returns plain dictionaries so downstream collectors can evolve without
  coupling to browser-specific objects.

The current role of this module is to provide the thinnest possible browser
runtime bridge for homepage collection experiments.
"""

from datetime import datetime, timezone
from importlib.util import find_spec
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from typing import Any


_VIDEO_URL_RE = re.compile(
    r'(?P<url>(?:(?:https?:)?//[^"\'\s<>]+)?(?P<path>/video/(?P<video_id>[A-Za-z0-9_-]+)(?:\?[^"\'\s<>]*)?))'
)


def detect_playwright() -> bool:
    """Return ``True`` when Playwright is importable in the current environment."""

    return find_spec("playwright") is not None


def run_playwright_homepage_probe(config: Any, homepage_url: str) -> dict[str, Any]:
    """Open a homepage with Playwright and return minimal page metadata.

    The function intentionally avoids any complicated selectors or scraping
    logic. It only verifies that the page can be opened and records a few
    lightweight metadata fields that are useful for later collector stages.

    Parameters
    ----------
    config:
        Application config object. Only ``config.browser`` fields are used.
    homepage_url:
        The homepage URL to visit.

    Returns
    -------
    dict[str, Any]
        A metadata payload containing the final URL, page title, HTTP status,
        the page HTML, and the browser settings used for the probe.
    """

    if not detect_playwright():
        raise RuntimeError("playwright is not installed")

    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

    browser_cfg = getattr(config, "browser", None)
    headless = bool(getattr(browser_cfg, "headless", True))
    timeout_ms = int(getattr(browser_cfg, "timeout_ms", 30_000))
    locale = getattr(browser_cfg, "locale", "zh-CN")
    user_agent = getattr(browser_cfg, "user_agent", None)
    engine = str(getattr(browser_cfg, "engine", "playwright")).lower()
    if engine not in {"chromium", "firefox", "webkit"}:
        engine = "chromium"

    launched_browser = None
    context = None
    page = None
    try:
        with sync_playwright() as playwright:
            browser_type = getattr(playwright, engine)
            launched_browser = browser_type.launch(headless=headless)
            context_kwargs: dict[str, Any] = {"locale": locale}
            if user_agent:
                context_kwargs["user_agent"] = user_agent
            context = launched_browser.new_context(**context_kwargs)
            page = context.new_page()
            response = page.goto(homepage_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page_html = page.content()

            return {
                "homepage_url": homepage_url,
                "final_url": page.url,
                "title": page.title(),
                "http_status": response.status if response is not None else None,
                "page_html": page_html,
                "backend": "playwright/minimal",
                "engine": engine,
                "headless": headless,
                "timeout_ms": timeout_ms,
                "scanned_at": _now_iso(),
            }
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if launched_browser is not None:
            try:
                launched_browser.close()
            except Exception:
                pass


def extract_video_candidates_from_html(html: str, homepage_url: str, max_items: int) -> list[dict[str, Any]]:
    """Extract stable video URL candidates from homepage HTML."""

    if max_items <= 0:
        return []

    search_space = str(html or "").replace(r"\/", "/")
    homepage_origin = _homepage_origin(homepage_url)
    seen_video_ids: set[str] = set()
    videos: list[dict[str, Any]] = []

    for match in _VIDEO_URL_RE.finditer(search_space):
        video_id = match.group("video_id")
        if not video_id or video_id in seen_video_ids:
            continue

        raw_url = match.group("url")
        if not raw_url:
            continue

        normalized_url = _normalize_video_url(raw_url, homepage_origin, video_id)
        seen_video_ids.add(video_id)
        videos.append(
            {
                "video_url": normalized_url,
                "video_id": video_id,
                "title": None,
                "publish_at": None,
            }
        )

        if len(videos) >= max_items:
            break

    return videos


def _normalize_video_url(raw_url: str, homepage_origin: str, video_id: str) -> str:
    joined = urljoin(homepage_origin, raw_url)
    parsed = urlsplit(joined)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/video/{video_id}", "", ""))


def _homepage_origin(homepage_url: str) -> str:
    parsed = urlsplit(str(homepage_url).strip())
    if not parsed.scheme or not parsed.netloc:
        return str(homepage_url).strip()
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

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
_ID_KEY_RE = re.compile(
    r'(?:(?:["\']?(?:aweme_id|modal_id|item_id|group_id)["\']?\s*[:=]\s*["\'](?P<id_quoted>[A-Za-z0-9_-]{6,128})["\'])|(?:["\']?(?:aweme_id|modal_id|item_id|group_id)["\']?\s*[:=]\s*(?P<id_raw>[A-Za-z0-9_-]{6,128})))'
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
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8_000))
            except Exception:
                pass
            # Best-effort: switch to "作品" tab so video anchors are rendered.
            for selector in (
                "text=作品",
                "text=全部作品",
                "[data-e2e='user-post-tab']",
                "[class*='post-tab']",
            ):
                try:
                    target = page.locator(selector).first
                    if target.count() > 0:
                        target.click(timeout=1_500)
                        page.wait_for_timeout(600)
                        break
                except Exception:
                    continue
            for _ in range(3):
                try:
                    page.mouse.wheel(0, 1800)
                    page.wait_for_timeout(400)
                except Exception:
                    break
            page_html = page.content()
            try:
                dom_hrefs = page.evaluate(
                    """() => {
                        const out = [];
                        for (const a of Array.from(document.querySelectorAll('a[href]'))) {
                            const href = (a.getAttribute('href') || '').trim();
                            if (!href) continue;
                            out.push(href);
                        }
                        return out.slice(0, 4000);
                    }"""
                )
            except Exception:
                dom_hrefs = []

            return {
                "homepage_url": homepage_url,
                "final_url": page.url,
                "title": page.title(),
                "http_status": response.status if response is not None else None,
                "page_html": page_html,
                "dom_hrefs": dom_hrefs,
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

    return extract_video_candidates_with_diagnostics(html, homepage_url, max_items)["videos"]


def extract_video_candidates_with_diagnostics(
    html: str,
    homepage_url: str,
    max_items: int,
) -> dict[str, Any]:
    """Extract video candidates and return extraction diagnostics."""

    if max_items <= 0:
        return {
            "videos": [],
            "diagnostics": {
                "extraction_version": "homepage-extract.v2",
                "total_matches": 0,
                "unique_video_ids": 0,
                "aweme_id_matches": 0,
                "merged_unique_video_ids": 0,
                "invalid_candidates": 0,
                "duplicate_candidates": 0,
                "homepage_origin": _homepage_origin(homepage_url),
            },
        }

    search_space = str(html or "").replace(r"\/", "/")
    homepage_origin = _homepage_origin(homepage_url)
    seen_video_ids: set[str] = set()
    url_unique_video_ids = 0
    videos: list[dict[str, Any]] = []
    total_matches = 0
    aweme_id_matches = 0
    id_key_matches = 0
    invalid_candidates = 0
    duplicate_candidates = 0

    for match in _VIDEO_URL_RE.finditer(search_space):
        total_matches += 1
        video_id = match.group("video_id")
        if not _is_valid_video_id(video_id):
            invalid_candidates += 1
            continue

        if video_id in seen_video_ids:
            duplicate_candidates += 1
            continue

        raw_url = match.group("url")
        if not raw_url:
            invalid_candidates += 1
            continue

        normalized_url = _normalize_video_url(raw_url, homepage_origin, video_id)
        seen_video_ids.add(video_id)
        url_unique_video_ids = len(seen_video_ids)
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

    if len(videos) < max_items:
        for aweme_id in _extract_aweme_id_candidates(search_space):
            aweme_id_matches += 1
            if not _is_valid_video_id(aweme_id):
                invalid_candidates += 1
                continue

            if aweme_id in seen_video_ids:
                duplicate_candidates += 1
                continue

            normalized_url = _normalize_video_url(f"/video/{aweme_id}", homepage_origin, aweme_id)
            seen_video_ids.add(aweme_id)
            videos.append(
                {
                    "video_url": normalized_url,
                    "video_id": aweme_id,
                    "title": None,
                    "publish_at": None,
                }
            )

            if len(videos) >= max_items:
                break

    if len(videos) < max_items:
        for key_id in _extract_keyed_video_id_candidates(search_space):
            id_key_matches += 1
            if not _is_valid_video_id(key_id):
                invalid_candidates += 1
                continue
            if key_id in seen_video_ids:
                duplicate_candidates += 1
                continue
            normalized_url = _normalize_video_url(f"/video/{key_id}", homepage_origin, key_id)
            seen_video_ids.add(key_id)
            videos.append(
                {
                    "video_url": normalized_url,
                    "video_id": key_id,
                    "title": None,
                    "publish_at": None,
                }
            )
            if len(videos) >= max_items:
                break

    return {
        "videos": videos,
        "diagnostics": {
            "extraction_version": "homepage-extract.v2",
            "total_matches": total_matches,
            "unique_video_ids": url_unique_video_ids,
            "aweme_id_matches": aweme_id_matches,
            "id_key_matches": id_key_matches,
            "merged_unique_video_ids": len(seen_video_ids),
            "invalid_candidates": invalid_candidates,
            "duplicate_candidates": duplicate_candidates,
            "homepage_origin": homepage_origin,
        },
    }


def _normalize_video_url(raw_url: str, homepage_origin: str, video_id: str) -> str:
    joined = urljoin(homepage_origin, raw_url)
    parsed = urlsplit(joined)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/video/{video_id}", "", ""))


def _homepage_origin(homepage_url: str) -> str:
    parsed = urlsplit(str(homepage_url).strip())
    if not parsed.scheme or not parsed.netloc:
        return str(homepage_url).strip()
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _extract_aweme_id_candidates(search_space: str) -> list[str]:
    return _extract_keyed_video_id_candidates(search_space, only_aweme=True)


def _extract_keyed_video_id_candidates(search_space: str, only_aweme: bool = False) -> list[str]:
    candidates: list[str] = []
    if only_aweme:
        aweme_only = re.compile(
            r'(?:(?:["\']?aweme_id["\']?\s*[:=]\s*["\'](?P<id_quoted>[A-Za-z0-9_-]{6,128})["\'])|(?:["\']?aweme_id["\']?\s*[:=]\s*(?P<id_raw>[A-Za-z0-9_-]{6,128})))'
        )
        pattern = aweme_only
    else:
        pattern = _ID_KEY_RE
    for match in pattern.finditer(search_space):
        candidate_id = match.group("id_quoted") or match.group("id_raw")
        if candidate_id:
            candidates.append(candidate_id)
    return candidates


def _is_valid_video_id(video_id: Any) -> bool:
    if not isinstance(video_id, str):
        return False

    candidate = video_id.strip()
    if len(candidate) < 6 or len(candidate) > 128:
        return False
    if candidate.lower() in {"undefined", "null", "none", "nan", "false", "true"}:
        return False

    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", candidate))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

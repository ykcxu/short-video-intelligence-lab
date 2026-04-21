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
    requested_pages = _normalize_requested_pages(max_pages)

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
                "requested_pages": requested_pages,
                "warnings": ["playwright is not installed"],
                "backend_version": "comment-collector.v2",
            },
        }

    return _collect_with_playwright_comments(
        config=config,
        video_url=video_url,
        max_pages=requested_pages,
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
                "stop_reason": "playwright_placeholder_error",
                "stop_reason_detail": "playwright import failed",
                "backend": "playwright:placeholder-error",
                "requested_pages": max_pages,
                "warnings": ["playwright import failed"],
                "backend_version": "comment-collector.v2",
            },
        }

    timeout_ms = int(getattr(config.browser, "timeout_ms", 30_000))
    headless = bool(getattr(config.browser, "headless", True))
    user_agent = getattr(config.browser, "user_agent", None)
    storage_state = getattr(config.browser, "storage_state", None)

    warnings: list[str] = []
    pagination_depth = 0
    stop_reason = "placeholder_only"
    stop_reason_detail: str | None = None
    dom_extract_attempted = False
    dom_items_seen = 0
    comments: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = []

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

                for page_idx in range(max_pages):
                    try:
                        page.mouse.wheel(0, 1200)
                        page.wait_for_timeout(350)
                        pagination_depth = page_idx + 1
                        dom_comments, dom_replies, dom_meta = _extract_comments_from_dom(page)
                        comments.extend(dom_comments)
                        replies.extend(dom_replies)
                        dom_extract_attempted = dom_extract_attempted or bool(
                            dom_meta.get("dom_extract_attempted", False)
                        )
                        dom_items_seen += int(dom_meta.get("dom_items_seen", 0) or 0)
                        helper_stop_reason = dom_meta.get("stop_reason")
                        helper_stop_detail = dom_meta.get("stop_reason_detail")
                        helper_warnings = dom_meta.get("warnings") or []
                        if helper_warnings:
                            warnings.extend(str(item) for item in helper_warnings)
                        if helper_stop_reason == "dom_extract_error":
                            stop_reason = "dom_extract_error"
                            stop_reason_detail = (
                                str(helper_stop_detail)
                                if helper_stop_detail is not None
                                else "comment DOM extraction failed"
                            )
                            break
                    except Exception as exc:  # pragma: no cover - runtime dependent
                        warnings.append(f"pagination probe failed on page {page_idx + 1}: {exc!s}")
                        stop_reason = "pagination_probe_failed"
                        stop_reason_detail = str(exc)
                        break
            finally:
                context.close()
                browser.close()
    except Exception as exc:  # pragma: no cover - runtime dependent
        warnings.append(f"playwright placeholder comment scan failed: {exc!s}")
        return {
            "video_url": video_url,
            "collected_at": collected_at,
            "comments": comments,
            "replies": replies,
            "scan_meta": {
                "pagination_depth": pagination_depth,
                "is_incomplete": True,
                "stop_reason": "playwright_placeholder_error",
                "stop_reason_detail": str(exc),
                "backend": "playwright:placeholder-error",
                "warnings": warnings,
                "requested_pages": max_pages,
                "dom_extract_attempted": dom_extract_attempted,
                "dom_items_seen": dom_items_seen,
                "backend_version": "comment-collector.v2",
            },
        }

    return {
        "video_url": video_url,
        "collected_at": collected_at,
        "comments": comments,
        "replies": replies,
        "scan_meta": {
            "pagination_depth": pagination_depth,
            "is_incomplete": True,
            "stop_reason": stop_reason,
            "stop_reason_detail": stop_reason_detail,
            "backend": "playwright:placeholder",
            "warnings": warnings,
            "requested_pages": max_pages,
            "dom_extract_attempted": dom_extract_attempted,
            "dom_items_seen": dom_items_seen,
            "backend_version": "comment-collector.v2",
        },
    }


def _normalize_requested_pages(max_pages: int) -> int:
    try:
        requested = int(max_pages)
    except Exception:
        return 0
    return max(0, requested)


def _comment_item_template() -> dict[str, Any]:
    return {
        "comment_id": "",
        "video_url": "",
        "author_id": "",
        "author_name": "",
        "content": "",
        "like_count": 0,
        "reply_count": 0,
        "created_at": "",
        "updated_at": "",
        "raw": {},
    }


def _reply_item_template() -> dict[str, Any]:
    return {
        "reply_id": "",
        "comment_id": "",
        "video_url": "",
        "author_id": "",
        "author_name": "",
        "content": "",
        "like_count": 0,
        "created_at": "",
        "updated_at": "",
        "raw": {},
    }


def _extract_comments_from_dom(page) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """
    Placeholder DOM extraction hook for comment scanning.

    TODO points for future integration:
    - locate the primary comment container
    - map comment node text/author/like metadata into `_comment_item_template()`
    - locate nested reply containers and map them into `_reply_item_template()`
    - detect load-more / expand-reply controls for deeper pagination
    - extract stable timestamps and raw payload fragments when available
    """

    meta: dict[str, Any] = {
        "dom_extract_attempted": True,
        "dom_items_seen": 0,
        "stop_reason": None,
        "stop_reason_detail": None,
        "warnings": [],
    }

    comments: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = []

    try:
        # NOTE: this read keeps the hook grounded in the live DOM without
        # committing to any platform-specific selector yet.
        _ = page.content()

        # TODO: replace the empty payload with normalized comment nodes.
        # comments.append(_comment_item_template())
        # replies.append(_reply_item_template())

        meta["dom_items_seen"] = len(comments) + len(replies)
        return comments, replies, meta
    except Exception as exc:  # pragma: no cover - runtime dependent
        meta["stop_reason"] = "dom_extract_error"
        meta["stop_reason_detail"] = str(exc)
        meta["warnings"].append(f"dom extraction failed: {exc!s}")
        return [], [], meta


def _has_playwright() -> bool:
    return find_spec("playwright") is not None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.util import find_spec
from typing import Any

from ..config import AppConfig


@dataclass(slots=True)
class VideoMetrics:
    """Normalized video engagement metrics."""

    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-serializable representation."""
        return {
            "view_count": self.view_count,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "share_count": self.share_count,
        }


def collect_video_detail(config: AppConfig, video_url: str) -> dict[str, Any]:
    """
    Collect a minimal, stable video detail payload.

    The current implementation is intentionally conservative:
    - without Playwright: return a deterministic stub
    - with Playwright: open the page once and return a placeholder payload

    Parameters
    ----------
    config:
        Application configuration.
    video_url:
        Target video URL.

    Returns
    -------
    dict[str, Any]
        Normalized detail payload with the following keys:
        - video_url
        - collected_at
        - metrics
        - raw
        - backend
        - warnings
    """

    collected_at = _now_iso()
    warnings: list[str] = []

    if not _has_playwright():
        warnings.append("playwright not installed; returning stub video detail")
        return {
            "video_url": video_url,
            "collected_at": collected_at,
            "metrics": VideoMetrics().to_dict(),
            "raw": {},
            "backend": "stub:no_playwright",
            "warnings": warnings,
        }

    result = _collect_with_playwright_detail(config=config, video_url=video_url)
    result["collected_at"] = collected_at
    return result


def _collect_with_playwright_detail(config: AppConfig, video_url: str) -> dict[str, Any]:
    warnings: list[str] = []
    raw: dict[str, Any] = {}

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - import resolution varies by env
        warnings.append(f"playwright import failed: {exc!s}")
        return {
            "video_url": video_url,
            "collected_at": _now_iso(),
            "metrics": VideoMetrics().to_dict(),
            "raw": raw,
            "backend": "stub:playwright_import_failed",
            "warnings": warnings,
        }

    timeout_ms = int(getattr(config.browser, "timeout_ms", 30_000))
    headless = bool(getattr(config.browser, "headless", True))
    user_agent = getattr(config.browser, "user_agent", None)
    storage_state = getattr(config.browser, "storage_state", None)

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
                    warnings.append("networkidle timeout while probing video detail page")
                raw = {
                    "url": page.url,
                    "title": _safe_text(page.title()),
                }
            finally:
                context.close()
                browser.close()
    except Exception as exc:  # pragma: no cover - runtime dependent
        warnings.append(f"playwright placeholder collection failed: {exc!s}")
        return {
            "video_url": video_url,
            "collected_at": _now_iso(),
            "metrics": VideoMetrics().to_dict(),
            "raw": raw,
            "backend": "playwright:placeholder-error",
            "warnings": warnings,
        }

    return {
        "video_url": video_url,
        "metrics": VideoMetrics().to_dict(),
        "raw": raw,
        "backend": "playwright:placeholder",
        "warnings": warnings,
    }


def _has_playwright() -> bool:
    return find_spec("playwright") is not None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()

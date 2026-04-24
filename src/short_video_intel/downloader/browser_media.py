from __future__ import annotations

from datetime import datetime, timezone
from importlib.util import find_spec
import re
from typing import Any

from ..config import AppConfig


_MEDIA_URL_RE = re.compile(
    r"""https?://[^\s"'<>\\]+?(?:\.mp4(?:\?[^\s"'<>\\]*)?|\.m3u8(?:\?[^\s"'<>\\]*)?|video_mp4[^\s"'<>\\]*|playwm[^\s"'<>\\]*|playAddr[^\s"'<>\\]*)""",
    re.IGNORECASE,
)


_MEDIA_EXCLUDE_TOKENS = (
    "douyin_pc_client.mp4",
    "bytednsdoc.com",
    "/download/",
    "client/download",
)


def discover_media_urls(
    config: AppConfig,
    video_url: str,
    *,
    expected_video_id: str | None = None,
) -> dict[str, Any]:
    if find_spec("playwright") is None:
        return {
            "ok": False,
            "backend": "browser_media:no_playwright",
            "video_url": video_url,
            "candidates": [],
            "warnings": ["playwright is not installed"],
            "probed_at": _now_iso(),
        }

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        return {
            "ok": False,
            "backend": "browser_media:playwright_import_failed",
            "video_url": video_url,
            "candidates": [],
            "warnings": [f"playwright import failed: {exc!s}"],
            "probed_at": _now_iso(),
        }

    browser_cfg = getattr(config, "browser", None)
    headless = bool(getattr(browser_cfg, "headless", True))
    timeout_ms = int(getattr(browser_cfg, "timeout_ms", 30_000))
    locale = getattr(browser_cfg, "locale", "zh-CN")
    user_agent = getattr(browser_cfg, "user_agent", None)
    storage_state = getattr(browser_cfg, "storage_state", None)

    warnings: list[str] = []
    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    response_count = 0

    normalized_expected_video_id = str(expected_video_id or "").strip()

    def add_candidate(url: str, *, source: str, mime_type: str = "", note: str = "") -> None:
        normalized = str(url or "").strip()
        if not normalized or normalized in seen_urls:
            return
        if not _looks_like_media_url(normalized):
            return
        seen_urls.add(normalized)
        candidates.append(
            {
                "url": normalized,
                "source": source,
                "mime_type": mime_type,
                "note": note,
                "excluded": _is_excluded_media_url(normalized),
                "expected_video_id_match": (
                    normalized_expected_video_id
                    and normalized_expected_video_id.lower() in normalized.lower()
                ),
            }
        )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context_kwargs: dict[str, Any] = {"locale": locale}
            if user_agent:
                context_kwargs["user_agent"] = user_agent
            if storage_state:
                context_kwargs["storage_state"] = str(storage_state)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()

            def handle_response(response: Any) -> None:
                nonlocal response_count
                response_count += 1
                try:
                    url = str(response.url or "")
                    headers = response.headers or {}
                    mime_type = str(headers.get("content-type") or headers.get("Content-Type") or "")
                    if "video/" in mime_type.lower() or _looks_like_media_url(url):
                        add_candidate(url, source="network_response", mime_type=mime_type)
                except Exception:
                    return

            page.on("response", handle_response)
            try:
                probe_urls = [video_url]
                share_url = _build_share_video_url(video_url, expected_video_id=normalized_expected_video_id)
                if share_url and share_url not in probe_urls:
                    probe_urls.append(share_url)

                for probe_index, probe_url in enumerate(probe_urls):
                    phase_label = "primary" if probe_index == 0 else "share_fallback"
                    _probe_single_page(
                        page,
                        probe_url=probe_url,
                        timeout_ms=timeout_ms,
                        warnings=warnings,
                        add_candidate=add_candidate,
                        phase_label=phase_label,
                    )
                    if _has_usable_candidate(candidates):
                        break
            finally:
                context.close()
                browser.close()
    except Exception as exc:  # pragma: no cover
        warnings.append(f"browser media probe failed: {exc!s}")

    return {
        "ok": bool(candidates),
        "backend": "browser_media:playwright-v1",
        "video_url": video_url,
        "candidates": sorted(
            candidates,
            key=lambda item: _candidate_sort_key(item, expected_video_id=normalized_expected_video_id),
        ),
        "response_count": response_count,
        "warnings": warnings,
        "probed_at": _now_iso(),
    }


def _looks_like_media_url(url: str) -> bool:
    lowered = str(url or "").lower()
    if not lowered.startswith(("http://", "https://")):
        return False
    return any(
        token in lowered
        for token in (
            ".mp4",
            ".m3u8",
            "video_mp4",
            "playwm",
            "playaddr",
            "aweme/v1/play",
            "/play/",
            "/video/tos/",
            "douyinvod.com",
        )
    )


def _is_excluded_media_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return any(token in lowered for token in _MEDIA_EXCLUDE_TOKENS)


def _has_usable_candidate(candidates: list[dict[str, Any]]) -> bool:
    return any(isinstance(item, dict) and not bool(item.get("excluded")) for item in candidates)


def _collect_page_candidates(page: Any, add_candidate: Any) -> None:
    try:
        perf_entries = page.evaluate(
            """() => performance.getEntriesByType('resource').map(item => ({
                name: item.name || '',
                initiatorType: item.initiatorType || ''
            }))"""
        )
    except Exception:
        perf_entries = []
    for entry in perf_entries or []:
        if not isinstance(entry, dict):
            continue
        add_candidate(
            str(entry.get("name") or ""),
            source="performance_resource",
            note=str(entry.get("initiatorType") or ""),
        )

    try:
        dom_video_urls = page.evaluate(
            """() => {
                const out = [];
                for (const el of Array.from(document.querySelectorAll('video, source'))) {
                    const src = el.currentSrc || el.src || el.getAttribute('src') || '';
                    if (src) out.push(src);
                }
                return out;
            }"""
        )
    except Exception:
        dom_video_urls = []
    for url in dom_video_urls or []:
        add_candidate(str(url), source="dom_media_element")

    try:
        html = page.content()
    except Exception:
        html = ""
    for match in _MEDIA_URL_RE.findall(html):
        add_candidate(match, source="html_regex")


def _probe_single_page(
    page: Any,
    *,
    probe_url: str,
    timeout_ms: int,
    warnings: list[str],
    add_candidate: Any,
    phase_label: str,
) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        page.goto(probe_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        warnings.append(f"{phase_label}: goto timeout while probing browser media")
        return

    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10_000))
    except PlaywrightTimeoutError:
        warnings.append(f"{phase_label}: networkidle timeout while probing browser media")

    for _ in range(4):
        try:
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(1200)
        except Exception:
            break
    _collect_page_candidates(page, add_candidate)

    if _has_usable_candidate_from_phase(add_candidate, phase_label):  # pragma: no cover - marker hook
        return

    warnings.append(f"{phase_label}: no usable media candidate after initial probe; attempting video activation retry")
    _activate_video(page)
    for _ in range(3):
        try:
            page.wait_for_timeout(1500)
        except Exception:
            break
        _collect_page_candidates(page, add_candidate)


def _activate_video(page: Any) -> None:
    try:
        page.locator("video").first.click(timeout=2_000)
    except Exception:
        pass
    try:
        page.keyboard.press("Space")
    except Exception:
        pass
    try:
        page.evaluate(
            """() => {
                const selectors = ['video', '[data-e2e="feed-active-video"] video', 'xg-video-container video'];
                for (const selector of selectors) {
                    const video = document.querySelector(selector);
                    if (!video) continue;
                    video.muted = true;
                    video.setAttribute('muted', 'muted');
                    const maybePromise = video.play?.();
                    return Boolean(maybePromise || true);
                }
                return false;
            }"""
        )
    except Exception:
        pass


def _build_share_video_url(video_url: str, *, expected_video_id: str) -> str | None:
    video_id = expected_video_id or _extract_video_id(video_url)
    if not video_id:
        return None
    return f"https://www.iesdouyin.com/share/video/{video_id}/"


def _extract_video_id(video_url: str) -> str:
    match = re.search(r"/video/(?P<video_id>\d+)", str(video_url or ""))
    return str(match.group("video_id")) if match else ""


def _has_usable_candidate_from_phase(add_candidate: Any, phase_label: str) -> bool:
    # lightweight hook kept as a function to avoid threading more mutable state;
    # currently always continue to the activation retry path.
    _ = add_candidate
    _ = phase_label
    return False


def _candidate_sort_key(
    item: dict[str, Any],
    *,
    expected_video_id: str = "",
) -> tuple[int, int, int, int, int, str]:
    url = str(item.get("url") or "").lower()
    mime_type = str(item.get("mime_type") or "").lower()
    source = str(item.get("source") or "").lower()
    note = str(item.get("note") or "").lower()
    expected_video_id = str(expected_video_id or "").lower()

    excluded_score = 1 if _is_excluded_media_url(url) else 0
    video_id_score = (
        0
        if expected_video_id and expected_video_id in url
        else 1
    )
    video_mime_score = 0 if "video/" in mime_type else 1
    explicit_video_score = 0 if any(token in url for token in ("video_mp4", ".mp4", ".m3u8", "aweme/v1/play", "douyinvod.com")) else 1
    preferred_host_score = 0 if "douyinvod.com" in url else 1
    source_score = 0 if source in {"dom_media_element", "network_response"} else 1
    note_score = 0 if any(token in note for token in ("video", "media")) else 1
    return (
        excluded_score,
        video_id_score,
        video_mime_score,
        preferred_host_score,
        explicit_video_score,
        source_score,
        note_score,
        url,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

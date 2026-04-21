from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.util import find_spec
import re
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
                page_html = page.content()
                metrics, extraction_diagnostics, extraction_warnings = _extract_metrics_from_html(page_html)
                warnings.extend(extraction_warnings)
                raw = {
                    "url": page.url,
                    "title": _safe_text(page.title()),
                    "page_content_length": len(page_html),
                    "extraction_diagnostics": extraction_diagnostics,
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
            "backend": "playwright:metrics-v1",
            "warnings": warnings,
        }

    return {
        "video_url": video_url,
        "metrics": metrics.to_dict(),
        "raw": raw,
        "backend": "playwright:metrics-v1",
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


_METRIC_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "view_count": ("view_count", "play_count", "playCnt", "playcnt", "views"),
    "like_count": ("like_count", "digg_count", "favorite_count", "fav_count"),
    "comment_count": ("comment_count", "comments_count", "reply_count"),
    "share_count": ("share_count", "forward_count", "repost_count"),
}

_COUNT_VALUE_PATTERN = r"-?\d+(?:\.\d+)?(?:[,_]\d{3})*(?:\.\d+)?(?:\s*[万亿])?|-?\d+(?:\.\d+)?[万亿]"


def _extract_metrics_from_html(html: str) -> tuple[VideoMetrics, dict[str, Any], list[str]]:
    warnings: list[str] = []
    diagnostics: dict[str, Any] = {
        "source_count": 0,
        "sources": [],
        "metrics": {},
    }

    script_blocks = _extract_script_blocks(html)
    sources: list[tuple[str, str, int]] = []
    for index, script_text in enumerate(script_blocks):
        if _looks_relevant(script_text):
            sources.append((f"script[{index}]", script_text, 0))
    if _looks_relevant(html):
        sources.append(("page_html", html, 1))

    diagnostics["source_count"] = len(sources)
    metrics = VideoMetrics()

    for metric_name, aliases in _METRIC_KEY_ALIASES.items():
        candidates: list[dict[str, Any]] = []
        for source_name, source_text, source_rank in sources:
            for alias in aliases:
                for value_text, span in _find_key_value_candidates(source_text, alias):
                    parsed_value = _parse_count_value(value_text)
                    if parsed_value is None:
                        continue
                    candidates.append(
                        {
                            "metric": metric_name,
                            "alias": alias,
                            "source": source_name,
                            "source_rank": source_rank,
                            "raw_value": value_text,
                            "parsed_value": parsed_value,
                            "span": span,
                        }
                    )

        chosen_candidate = _choose_best_candidate(candidates)
        if chosen_candidate is not None:
            setattr(metrics, metric_name, int(chosen_candidate["parsed_value"]))

        diagnostics["metrics"][metric_name] = {
            "candidate_count": len(candidates),
            "selected_value": int(chosen_candidate["parsed_value"]) if chosen_candidate else 0,
            "selected_source": chosen_candidate["source"] if chosen_candidate else None,
            "selected_alias": chosen_candidate["alias"] if chosen_candidate else None,
            "source_breakdown": _build_source_breakdown(candidates),
        }

    diagnostics["sources"] = [
        {
            "name": source_name,
            "length": len(source_text),
            "rank": source_rank,
        }
        for source_name, source_text, source_rank in sources
    ]

    return metrics, diagnostics, warnings


def _extract_script_blocks(html: str) -> list[str]:
    matches = re.findall(r"<script\b[^>]*>(.*?)</script>", html, flags=re.IGNORECASE | re.DOTALL)
    if matches:
        return [block.strip() for block in matches if block and block.strip()]
    return []


def _looks_relevant(text: str) -> bool:
    lowered = text.lower()
    alias_set = {alias.lower() for aliases in _METRIC_KEY_ALIASES.values() for alias in aliases}
    return any(alias in lowered for alias in alias_set)


def _find_key_value_candidates(text: str, alias: str) -> list[tuple[str, tuple[int, int]]]:
    alias_pattern = re.escape(alias)
    patterns = [
        rf'["\']?{alias_pattern}["\']?\s*[:=]\s*["\']?(?P<value>{_COUNT_VALUE_PATTERN})["\']?',
        rf'(?P<value>{alias_pattern}\s*[:=]\s*["\']?{_COUNT_VALUE_PATTERN}["\']?)',
    ]
    found: list[tuple[str, tuple[int, int]]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.VERBOSE | re.DOTALL):
            value = match.groupdict().get("value") or match.group(0)
            if value.lower().startswith(alias.lower()):
                value = value.split(":", 1)[-1] if ":" in value else value.split("=", 1)[-1]
            found.append((_strip_non_numeric_wrappers(value), match.span()))
    return found


def _strip_non_numeric_wrappers(value: str) -> str:
    return value.strip().strip("\"'` ,;:]})")


def _parse_count_value(value: str) -> int | None:
    normalized = _strip_non_numeric_wrappers(value)
    if not normalized:
        return None

    normalized = normalized.replace(",", "").replace("_", "").replace(" ", "")
    multiplier = 1
    if normalized.endswith("万"):
        multiplier = 10_000
        normalized = normalized[:-1]
    elif normalized.endswith("亿"):
        multiplier = 100_000_000
        normalized = normalized[:-1]

    normalized = re.sub(r"[^0-9.\-]", "", normalized)
    if not normalized or normalized in {"-", ".", "-."}:
        return None

    try:
        parsed = float(normalized)
    except ValueError:
        return None

    return max(0, int(round(parsed * multiplier)))


def _choose_best_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item.get("source_rank", 99),
            item.get("parsed_value", 0),
            -item.get("span", (0, 0))[0],
        ),
        reverse=False,
    )
    best_rank = candidates[0].get("source_rank", 99)
    same_rank_candidates = [item for item in candidates if item.get("source_rank", 99) == best_rank]
    return max(same_rank_candidates, key=lambda item: (item.get("parsed_value", 0), -item.get("span", (0, 0))[0]))


def _build_source_breakdown(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    breakdown: dict[str, Any] = {}
    for candidate in candidates:
        source = candidate.get("source") or "unknown"
        item = breakdown.setdefault(
            source,
            {
                "candidate_count": 0,
                "max_value": 0,
                "aliases": [],
            },
        )
        item["candidate_count"] += 1
        item["max_value"] = max(item["max_value"], int(candidate.get("parsed_value", 0)))
        alias = candidate.get("alias")
        if alias and alias not in item["aliases"]:
            item["aliases"].append(alias)
    return breakdown

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
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8_000))
                except PlaywrightTimeoutError:
                    warnings.append("networkidle timeout while probing video detail page")

                page_html, body_text, wait_diagnostics = _wait_for_video_payload(
                    page,
                    timeout_ms=timeout_ms,
                )
                metrics, extraction_diagnostics, extraction_warnings = _extract_metrics_from_payload(
                    page_html,
                    body_text,
                )
                warnings.extend(extraction_warnings)
                raw = {
                    "url": page.url,
                    "title": _safe_text(page.title()),
                    "page_content_length": len(page_html),
                    "body_text_length": len(body_text),
                    "body_text_preview": body_text[:1000],
                    "wait_diagnostics": wait_diagnostics,
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
_BODY_COUNT_TOKEN_PATTERN = re.compile(r"(?<![\d:])\d+(?:\.\d+)?(?:万|亿)?(?![\d:])")
_BODY_METRIC_WINDOW_CHARS = 260
_BODY_METRIC_MARKER_TAIL_CHARS = 40


def _extract_metrics_from_payload(html: str, body_text: str) -> tuple[VideoMetrics, dict[str, Any], list[str]]:
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
    if body_text:
        sources.append(("body_text", body_text, 2))

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

    text_metrics, text_diagnostics = _extract_metrics_from_body_text(body_text)
    diagnostics["text_body"] = text_diagnostics
    if text_diagnostics.get("matched"):
        for metric_name in ("like_count", "comment_count", "share_count"):
            if text_metrics.get(metric_name, 0) > 0:
                setattr(metrics, metric_name, int(text_metrics[metric_name]))
                metric_diag = diagnostics["metrics"].setdefault(metric_name, {})
                metric_diag["selected_value"] = int(text_metrics[metric_name])
                metric_diag["selected_source"] = "body_text_sequence"
                metric_diag["selected_alias"] = "body_text_sequence"
        if text_metrics.get("comment_count", 0) == 0:
            metrics.comment_count = 0
            metric_diag = diagnostics["metrics"].setdefault("comment_count", {})
            metric_diag["selected_value"] = 0
            metric_diag["selected_source"] = "body_text_sequence"
            metric_diag["selected_alias"] = "body_text_sequence"
        if not text_metrics.get("view_count", 0):
            metrics.view_count = 0
            metric_diag = diagnostics["metrics"].setdefault("view_count", {})
            metric_diag["selected_value"] = 0
            metric_diag["selected_source"] = "not_exposed_in_body_text"
            metric_diag["selected_alias"] = "not_exposed_in_body_text"
        if text_metrics.get("like_count", 0) > 0:
            _zero_out_candidate_metric(diagnostics, "like_count")
        if text_metrics.get("comment_count", 0) >= 0:
            _zero_out_candidate_metric(diagnostics, "comment_count")
        if text_metrics.get("share_count", 0) > 0:
            _zero_out_candidate_metric(diagnostics, "share_count")
    if metrics.view_count <= 0 and text_metrics.get("view_count", 0) > 0:
        metrics.view_count = int(text_metrics["view_count"])
        metric_diag = diagnostics["metrics"].setdefault("view_count", {})
        metric_diag["selected_value"] = int(text_metrics["view_count"])
        metric_diag["selected_source"] = "body_text_sequence"
        metric_diag["selected_alias"] = "body_text_sequence"

    return metrics, diagnostics, warnings


def _wait_for_video_payload(page: Any, *, timeout_ms: int) -> tuple[str, str, dict[str, Any]]:
    poll_ms = 4_000
    max_rounds = max(8, min(18, max(1, timeout_ms // poll_ms)))
    best_html = ""
    best_body = ""
    best_score = -10**9
    round_details: list[dict[str, Any]] = []

    for round_index in range(max_rounds):
        if round_index > 0:
            try:
                page.wait_for_timeout(poll_ms)
            except Exception:
                break
        try:
            current_html = page.content()
        except Exception:
            current_html = best_html
        try:
            current_body = page.inner_text("body")
        except Exception:
            current_body = best_body

        score = _video_payload_score(current_html, current_body)
        round_details.append(
            {
                "round": round_index,
                "html_length": len(current_html),
                "body_length": len(current_body),
                "score": score,
                "has_loading_text": ("视频数据加载中" in current_body) or ("加载中" in current_body),
                "has_comment_block": "全部评论" in current_body,
                "has_publish_time": "发布时间：" in current_body,
            }
        )

        if score >= best_score:
            best_html = current_html
            best_body = current_body
            best_score = score

        if (
            len(current_body) >= 1200
            and ("全部评论" in current_body or "留下你的精彩评论吧" in current_body)
            and "发布时间：" in current_body
        ):
            break

    return best_html, best_body, {"rounds": round_details, "best_score": best_score}


def _video_payload_score(html: str, body_text: str) -> int:
    score = len(body_text)
    if "全部评论" in body_text:
        score += 1200
    if "发布时间：" in body_text:
        score += 900
    if "举报" in body_text:
        score += 400
    if ("视频数据加载中" in body_text) or ("加载中" in body_text):
        score -= 500
    score += min(500, html.lower().count("comment"))
    return score


def _extract_metrics_from_body_text(body_text: str) -> tuple[dict[str, int], dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "count_tokens": [],
        "window_preview": "",
        "tail_preview": "",
        "has_first_comment_marker": False,
        "matched": False,
    }
    metrics = {
        "view_count": 0,
        "like_count": 0,
        "comment_count": 0,
        "share_count": 0,
    }
    text = _safe_text(body_text)
    if not text:
        return metrics, diagnostics

    normalized = re.sub(r"\s+", " ", text)
    if len(normalized) < 300 and not _BODY_COUNT_TOKEN_PATTERN.search(normalized):
        diagnostics["window_preview"] = normalized[:300]
        return metrics, diagnostics

    section = _extract_body_metric_window(text)
    if not section:
        diagnostics["window_preview"] = normalized[:300]
        return metrics, diagnostics

    diagnostics["window_preview"] = re.sub(r"\s+", " ", section)[:300]
    lines = [token.strip() for token in re.split(r"[\s\r\n]+", section) if token and token.strip()]
    diagnostics["tail_preview"] = " | ".join(lines[-12:])
    diagnostics["has_first_comment_marker"] = any("抢首评" in line for line in lines)
    numeric_lines = [
        line for line in lines
        if re.fullmatch(r"\d+(?:\.\d+)?(?:万|亿)?", line)
    ]
    diagnostics["count_tokens"] = numeric_lines[-8:]

    parsed = [_parse_count_value(token) for token in numeric_lines[-4:]]
    parsed = [value for value in parsed if value is not None and value < 100_000_000]
    if parsed and _looks_like_reasonable_metric_block(lines):
        diagnostics["matched"] = True
        metrics["like_count"] = parsed[0]
        if diagnostics["has_first_comment_marker"]:
            metrics["comment_count"] = 0
            metrics["share_count"] = parsed[-1] if len(parsed) >= 2 else 0
        else:
            metrics["comment_count"] = parsed[1] if len(parsed) >= 2 else 0
            metrics["share_count"] = parsed[-1] if len(parsed) >= 3 else 0
    return metrics, diagnostics


def _extract_body_metric_window(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""

    if "发布时间：" in raw:
        publish_idx = raw.find("发布时间：")
        return raw[max(0, publish_idx - _BODY_METRIC_WINDOW_CHARS) : publish_idx + _BODY_METRIC_MARKER_TAIL_CHARS]
    if "举报" in raw:
        report_idx = raw.find("举报")
        return raw[max(0, report_idx - _BODY_METRIC_WINDOW_CHARS) : report_idx + _BODY_METRIC_MARKER_TAIL_CHARS]
    normalized = re.sub(r"\s+", " ", raw)
    match = re.search(r"连播\s*(?P<section>.+?)(?:举报|发布时间：)", normalized)
    if match:
        return match.group("section").strip()
    return normalized[-_BODY_METRIC_WINDOW_CHARS:]


def _looks_like_reasonable_metric_block(lines: list[str]) -> bool:
    if not lines:
        return False
    joined = " ".join(lines[-12:])
    noisy_tokens = ("获赞", "关注", "推荐视频", "下载客户端", "子琦老师讲语文")
    if any(token in joined for token in noisy_tokens):
        return False
    if "发布时间" in joined:
        return True
    return "举报" in joined or "抢首评" in joined


def _zero_out_candidate_metric(diagnostics: dict[str, Any], metric_name: str) -> None:
    metric_diag = diagnostics.get("metrics", {}).get(metric_name)
    if not isinstance(metric_diag, dict):
        return
    source_breakdown = metric_diag.get("source_breakdown")
    if isinstance(source_breakdown, dict):
        for source_item in source_breakdown.values():
            if isinstance(source_item, dict):
                source_item["max_value"] = 0


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

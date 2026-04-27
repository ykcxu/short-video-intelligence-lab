from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from importlib.util import find_spec
import re
from html import unescape
from typing import Any

from ..config import AppConfig

EMPTY_COMMENT_MARKERS = ("暂无评论", "抢首评", "还没有评论")
COMMENT_TEXT_KEYS = ("text", "content", "comment", "comment_text", "commenttext", "comment_content", "commentcontent")
COMMENT_ID_KEYS = ("comment_id", "cid", "id")
COMMENT_CONTAINER_KEYS = (
    "comment",
    "comments",
    "comment_list",
    "commentlist",
    "list",
    "data",
    "items",
    "replies",
    "reply_comment",
    "reply_comments",
)
COMMENT_LIKE_KEYS = ("like_count", "digg_count", "likes", "like", "upvote_count", "vote_count")
COMMENT_REPLY_KEYS = ("reply_count", "reply_cnt", "reply_num", "replynum", "sub_comment_count", "children_count")
COMMENT_TIME_KEYS = ("create_time", "created_at", "update_time", "updated_at", "publish_time")
COMMENT_AUTHOR_ID_KEYS = ("author_id", "uid", "user_id", "sec_uid", "sec_user_id")
REAL_COMMENT_RESPONSE_PATTERNS = (
    "/aweme/v1/web/comment/list",
    "/aweme/v1/web/comment/publish",
    "/aweme/v1/web/comment/list/reply",
)


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
                "extraction_diagnostics": _new_extraction_diagnostics(),
                "backend_version": "comment-collector.v2",
            },
        }

    return _collect_with_playwright_comments(
        config=config,
        video_url=video_url,
        max_pages=requested_pages,
        collected_at=collected_at,
    )


def _new_extraction_diagnostics() -> dict[str, Any]:
    """统一生成诊断结构，避免不同提取链路字段漂移。"""
    return {
        "source_hits": {
            "regex_fragments": 0,
            "script_tags": 0,
            "jsonish_strings": 0,
            "json_objects": 0,
            "response_payloads": 0,
            "response_objects": 0,
        },
        "parse_failures": 0,
        "final_deduped_count": 0,
        "response_listener_failures": 0,
    }


def _merge_extraction_diagnostics(target: dict[str, Any], source: dict[str, Any] | None) -> None:
    """按计数语义合并诊断数据，保证 warnings 之外还能回看提取来源。"""
    if not isinstance(source, dict):
        return
    target_hits = target.setdefault("source_hits", {})
    source_hits = source.get("source_hits") or {}
    for key in ("regex_fragments", "script_tags", "jsonish_strings", "json_objects", "response_payloads", "response_objects"):
        target_hits[key] = int(target_hits.get(key) or 0) + int(source_hits.get(key) or 0)
    target["parse_failures"] = int(target.get("parse_failures") or 0) + int(source.get("parse_failures") or 0)
    target["response_listener_failures"] = int(target.get("response_listener_failures") or 0) + int(
        source.get("response_listener_failures") or 0
    )
    target["final_deduped_count"] = int(source.get("final_deduped_count") or target.get("final_deduped_count") or 0)


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
                "extraction_diagnostics": _new_extraction_diagnostics(),
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
    extraction_diagnostics = _new_extraction_diagnostics()
    comments: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = []
    payload_diagnostics: dict[str, Any] = {"rounds": [], "best_body_length": 0}
    latest_body_snapshot = ""
    response_comments: list[dict[str, Any]] = []

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

            def on_response(response: Any) -> None:
                """实时监听评论接口响应，单条失败只记诊断不打断主流程。"""
                try:
                    response_items, response_warnings, response_diag = _extract_comments_from_response(
                        response=response,
                        video_url=video_url,
                    )
                    if response_items:
                        response_comments.extend(response_items)
                    if response_warnings:
                        warnings.extend(response_warnings)
                    _merge_extraction_diagnostics(extraction_diagnostics, response_diag)
                except Exception as exc:
                    extraction_diagnostics["parse_failures"] += 1
                    extraction_diagnostics["response_listener_failures"] += 1
                    warnings.append(f"response listener failed: {exc!s}")

            page.on("response", on_response)
            try:
                page.goto(video_url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
                except PlaywrightTimeoutError:
                    warnings.append("networkidle timeout while probing comment page")

                activation_warnings = _activate_comment_panel(page)
                if activation_warnings:
                    warnings.extend(activation_warnings)

                page_html, body_text, payload_diagnostics = _wait_for_comment_payload(page, timeout_ms=timeout_ms)
                text_comments, text_warnings, text_diag = _extract_comments_from_body_text(body_text, video_url)
                if text_warnings:
                    warnings.extend(text_warnings)
                if text_comments:
                    comments.extend(text_comments)
                    extraction_diagnostics["source_hits"]["regex_fragments"] += int(
                        text_diag.get("source_hits", {}).get("body_text_blocks") or 0
                    )
                    extraction_diagnostics["final_deduped_count"] = len(_dedupe_comment_items(comments))

                for page_idx in range(max_pages):
                    try:
                        page.mouse.wheel(0, 1200)
                        page.wait_for_timeout(350)
                        pagination_depth = page_idx + 1
                        latest_html = page.content()
                        latest_body = page.inner_text("body")
                        latest_body_snapshot = latest_body
                        text_comments, text_warnings, text_diag = _extract_comments_from_body_text(
                            latest_body,
                            video_url,
                        )
                        if text_warnings:
                            warnings.extend(text_warnings)
                        if text_comments:
                            comments.extend(text_comments)
                            extraction_diagnostics["source_hits"]["regex_fragments"] += int(
                                text_diag.get("source_hits", {}).get("body_text_blocks") or 0
                            )

                        dom_comments, dom_replies, dom_meta = _extract_comments_from_dom(
                            page,
                            html=latest_html,
                            body_text=latest_body,
                        )
                        comments.extend(dom_comments)
                        replies.extend(dom_replies)
                        dom_extract_attempted = dom_extract_attempted or bool(
                            dom_meta.get("dom_extract_attempted", False)
                        )
                        dom_items_seen += int(dom_meta.get("dom_items_seen", 0) or 0)
                        dom_diag = dom_meta.get("extraction_diagnostics") or {}
                        _merge_extraction_diagnostics(extraction_diagnostics, dom_diag)
                        extraction_diagnostics["final_deduped_count"] = len(_dedupe_comment_items(comments))
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
                if response_comments:
                    comments.extend(response_comments)
                try:
                    page.remove_listener("response", on_response)
                except Exception:
                    pass
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
                "payload_diagnostics": payload_diagnostics,
                "extraction_diagnostics": extraction_diagnostics,
                "backend_version": "comment-collector.v2",
            },
        }

    comments = _filter_real_comment_items(_dedupe_comment_items(comments))[:20]
    extraction_diagnostics["final_deduped_count"] = len(comments)
    if comments and stop_reason == "placeholder_only":
        comment_sources = {
            str((item.get("raw") or {}).get("source") or "")
            for item in comments
            if isinstance(item, dict)
        }
        if "network_response_json" in comment_sources:
            stop_reason = "network_response_comments_captured"
        elif any(isinstance(item, dict) and bool((item.get("raw") or {}).get("stub")) for item in comments):
            stop_reason = "body_text_comment_stubs_captured"
        else:
            stop_reason = "body_text_comments_captured"
    elif stop_reason == "placeholder_only" and _has_empty_comment_state(latest_body_snapshot or body_text):
        stop_reason = "empty_comment_state"
        stop_reason_detail = "评论区显示暂无评论或抢首评"

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
            "backend": _build_comment_backend(comments=comments, stop_reason=stop_reason),
            "warnings": warnings,
            "requested_pages": max_pages,
            "dom_extract_attempted": dom_extract_attempted,
            "dom_items_seen": dom_items_seen,
            "payload_diagnostics": payload_diagnostics,
            "extraction_diagnostics": extraction_diagnostics,
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


def _build_comment_item(
    content: str,
    video_url: str,
    source: str,
    content_hash: str,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = _comment_item_template()
    item["comment_id"] = f"cmt_{content_hash[:16]}"
    item["video_url"] = video_url
    item["content"] = content
    item["raw"] = {
        "source": source,
        "content_hash": content_hash,
        **(raw or {}),
    }
    return item


def _to_count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value or "").strip().lower()
    if not text:
        return 0
    cleaned = text.replace(",", "")
    multiplier = 1.0
    if cleaned.endswith(("w", "万")):
        multiplier = 10_000.0
        cleaned = cleaned[:-1]
    try:
        return max(0, int(float(cleaned) * multiplier))
    except Exception:
        return 0


def _extract_lightweight_comment_signals(text: str) -> dict[str, Any]:
    normalized = _normalize_comment_content(text)
    signals: dict[str, Any] = {"author_name": "", "like_count": 0, "reply_count": 0}
    if not normalized:
        return signals

    author_match = re.search(r"(?:作者|author)\s*[:：]\s*([^\s，,。:：]{1,32})", normalized, re.IGNORECASE)
    if author_match:
        signals["author_name"] = _normalize_comment_content(author_match.group(1))

    like_match = re.search(r"([0-9]+(?:\.[0-9]+)?(?:w|万)?)\s*赞", normalized, re.IGNORECASE)
    if like_match:
        signals["like_count"] = _to_count(like_match.group(1))

    reply_match = re.search(
        r"(?:回复\s*([0-9]+(?:\.[0-9]+)?(?:w|万)?)|([0-9]+(?:\.[0-9]+)?(?:w|万)?)\s*(?:条)?回复)",
        normalized,
        re.IGNORECASE,
    )
    if reply_match:
        reply_value = reply_match.group(1) or reply_match.group(2) or ""
        signals["reply_count"] = _to_count(reply_value)

    return signals


def _dedupe_comment_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        raw = item.get("raw") if isinstance(item, dict) else {}
        content_hash = ""
        if isinstance(raw, dict):
            content_hash = str(raw.get("content_hash") or "")
        if not content_hash:
            content_hash = _hash_content(str(item.get("content", "") or ""))
        if content_hash in seen:
            continue
        seen.add(content_hash)
        deduped.append(item)
    return deduped


def _filter_real_comment_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """保留真实评论，过滤直播配置、无障碍文案等泛 JSON 噪声。"""
    return [item for item in items if _is_real_comment_item(item)]


def _is_real_comment_item(item: dict[str, Any]) -> bool:
    """判断评论是否来自评论接口或具备真实评论作者信息。"""
    if not isinstance(item, dict):
        return False
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    response_url = str(raw.get("response_url") or "").lower()
    if any(pattern in response_url for pattern in REAL_COMMENT_RESPONSE_PATTERNS):
        return True
    if str(item.get("author_id") or "").strip():
        return True
    author_name = str(item.get("author_name") or "").strip()
    return bool(author_name and not raw.get("stub"))


def _normalize_comment_content(text: str) -> str:
    cleaned = unescape(str(text or ""))
    cleaned = cleaned.replace("\u200b", " ").replace("\ufeff", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _normalize_extracted_text(text: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _looks_like_comment_text(text: str) -> bool:
    if not text:
        return False
    if len(text) < 2:
        return False
    if len(text) > 500:
        return False
    if text.lower() in {"comment", "reply", "content", "text"}:
        return False
    if re.fullmatch(r"[\W_]+", text):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return False
    return True


def _hash_content(text: str) -> str:
    normalized = _normalize_comment_content(text)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_comment_content(value)
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _unescape_jsonish_string(text: str) -> str:
    try:
        return bytes(text, "utf-8").decode("unicode_escape")
    except Exception:
        return text


def _extract_comment_like_strings_from_jsonish_text(text: str) -> list[str]:
    candidates: list[str] = []

    double_quote_pattern = re.compile(
        r'(?is)"(?:text|content|comment)"\s*:\s*"(?P<value>(?:\\.|[^"\\]){2,500}?)"'
    )
    single_quote_pattern = re.compile(
        r"(?is)'(?:text|content|comment)'\s*:\s*'(?P<value>(?:\\.|[^'\\]){2,500}?)'"
    )
    for pattern in (double_quote_pattern, single_quote_pattern):
        for match in pattern.finditer(text):
            candidates.append(_unescape_jsonish_string(match.group("value") or ""))

    fallback_double = re.compile(r'(?is)\b(?:text|content|comment)\b\s*:\s*"(?P<value>(?:\\.|[^"\\]){2,500}?)"')
    fallback_single = re.compile(r"(?is)\b(?:text|content|comment)\b\s*:\s*'(?P<value>(?:\\.|[^'\\]){2,500}?)'")
    for pattern in (fallback_double, fallback_single):
        for match in pattern.finditer(text):
            candidates.append(_unescape_jsonish_string(match.group("value") or ""))

    return _dedupe_strings(candidates)


def _extract_comment_like_strings_from_parsed_json(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except Exception:
        return []

    candidates: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_lower = str(key).lower()
                if key_lower in {"text", "content", "comment", "commenttext", "comment_content", "commentcontent"}:
                    if isinstance(value, str):
                        candidates.append(value)
                    elif isinstance(value, (int, float)):
                        candidates.append(str(value))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return _dedupe_strings(candidates)


def _extract_author_name_from_node(node: Any) -> str:
    if not isinstance(node, dict):
        return ""

    direct_keys = ("author_name", "nickname", "nick_name", "user_name", "screen_name", "name")
    for key in direct_keys:
        value = node.get(key)
        if isinstance(value, str):
            normalized = _normalize_comment_content(value)
            if normalized:
                return normalized

    author_value = node.get("author")
    if isinstance(author_value, str):
        normalized = _normalize_comment_content(author_value)
        if normalized:
            return normalized
    if isinstance(author_value, dict):
        nested = _extract_author_name_from_node(author_value)
        if nested:
            return nested

    for key in ("user", "user_info", "author_info", "owner"):
        value = node.get(key)
        if isinstance(value, dict):
            nested = _extract_author_name_from_node(value)
            if nested:
                return nested

    return ""


def _extract_scalar_field(node: dict[str, Any], keys: tuple[str, ...]) -> str:
    """抽取常见标量字段，供评论 id、作者 id、时间等映射复用。"""
    for key in keys:
        value = node.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            normalized = _normalize_comment_content(str(value))
            if normalized:
                return normalized
    return ""


def _extract_author_id_from_node(node: dict[str, Any]) -> str:
    """兼容 user/author 等嵌套结构中的作者标识。"""
    direct_value = _extract_scalar_field(node, COMMENT_AUTHOR_ID_KEYS)
    if direct_value:
        return direct_value
    for key in ("user", "user_info", "author", "author_info", "owner"):
        value = node.get(key)
        if isinstance(value, dict):
            nested_value = _extract_scalar_field(value, COMMENT_AUTHOR_ID_KEYS)
            if nested_value:
                return nested_value
    return ""


def _extract_comment_text_from_node(node: dict[str, Any]) -> str:
    """兼容 text/content/comment 等不同评论正文字段。"""
    for key in COMMENT_TEXT_KEYS:
        value = node.get(key)
        if isinstance(value, str):
            normalized = _normalize_comment_content(value)
            if normalized:
                return normalized
        if isinstance(value, dict):
            nested = _extract_scalar_field(value, ("text", "content", "value"))
            if nested:
                return nested
    return ""


def _map_comment_payload_node(
    node: dict[str, Any],
    video_url: str,
    response_url: str,
    source: str,
) -> dict[str, Any] | None:
    """把评论接口中的常见字段映射到现有 comment item 结构。"""
    content = _extract_comment_text_from_node(node)
    if not _looks_like_comment_text(content):
        return None
    content_hash = _hash_content(content)
    item = _build_comment_item(
        content=content,
        video_url=video_url,
        source=source,
        content_hash=content_hash,
        raw={"source": source, "response_url": response_url, "node_keys": sorted(str(key) for key in node.keys())[:20]},
    )
    item["comment_id"] = _extract_scalar_field(node, COMMENT_ID_KEYS) or item["comment_id"]
    item["author_id"] = _extract_author_id_from_node(node)
    item["author_name"] = _extract_author_name_from_node(node)
    item["like_count"] = _to_count(_extract_scalar_field(node, COMMENT_LIKE_KEYS))
    item["reply_count"] = _to_count(_extract_scalar_field(node, COMMENT_REPLY_KEYS))
    item["created_at"] = _extract_scalar_field(node, COMMENT_TIME_KEYS)
    item["updated_at"] = _extract_scalar_field(node, ("update_time", "updated_at"))
    return item


def _looks_like_comment_response_url(url: str) -> bool:
    normalized_url = str(url or "").lower()
    return bool(re.search(r"(?:comment|reply|comment_list|commentlist|aweme/v\d+/.*comment)", normalized_url))


def _looks_like_real_comment_response_url(url: str) -> bool:
    """只允许真实评论接口进入 JSON 递归解析，避免配置接口噪声污染。"""
    normalized_url = str(url or "").lower()
    return any(pattern in normalized_url for pattern in REAL_COMMENT_RESPONSE_PATTERNS)


def _payload_contains_comment_hints(payload: Any) -> bool:
    """只做轻量启发式判断，避免为无关 JSON 做过深解析。"""
    queue: list[Any] = [payload]
    inspected = 0
    while queue and inspected < 200:
        current = queue.pop(0)
        inspected += 1
        if isinstance(current, dict):
            keys = {str(key).lower() for key in current.keys()}
            if keys.intersection(COMMENT_TEXT_KEYS) and (
                "nickname" in keys or "digg_count" in keys or "user" in keys or "author" in keys
            ):
                return True
            if keys.intersection(COMMENT_CONTAINER_KEYS):
                return True
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current[:20])
    return False


def _extract_comments_from_response_payload(
    payload: Any,
    video_url: str,
    response_url: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """从评论接口 JSON 中递归提取评论对象，供 response 监听与单测复用。"""
    diagnostics = _new_extraction_diagnostics()
    comments: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    queue: list[Any] = [payload]

    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            item = _map_comment_payload_node(current, video_url, response_url, "network_response_json")
            if item:
                content_hash = str((item.get("raw") or {}).get("content_hash") or "")
                if content_hash and content_hash not in seen_hashes:
                    seen_hashes.add(content_hash)
                    comments.append(item)
                    diagnostics["source_hits"]["response_objects"] += 1
            for key, value in current.items():
                key_lower = str(key).lower()
                if key_lower in COMMENT_CONTAINER_KEYS:
                    diagnostics["source_hits"]["response_payloads"] += 1
                if isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(current, list):
            queue.extend(current)

    diagnostics["final_deduped_count"] = len(comments)
    return comments[:20], diagnostics


def _extract_comment_like_objects_from_parsed_json(
    text: str,
) -> tuple[list[dict[str, Any]], int]:
    try:
        data = json.loads(text)
    except Exception:
        return [], 1

    objects: list[dict[str, Any]] = []
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            collected_text = _extract_comment_text_from_node(node)
            if collected_text:
                objects.append(
                    {
                        "content": collected_text,
                        "author_name": _extract_author_name_from_node(node),
                        "like_count": _to_count(_extract_scalar_field(node, COMMENT_LIKE_KEYS)),
                        "reply_count": _to_count(_extract_scalar_field(node, COMMENT_REPLY_KEYS)),
                    }
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return objects, 0


def _extract_comment_candidates_via_regex(
    html: str,
    video_url: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    warnings: list[str] = []
    comments: list[dict[str, Any]] = []
    seen_content_hashes: set[str] = set()
    diagnostics: dict[str, Any] = {
        "source_hits": {"regex_fragments": 0},
        "parse_failures": 0,
        "deduped_count": 0,
    }

    try:
        fragment_pattern = re.compile(
            r"(?is)<(?P<tag>div|span|p|li|a|article|section)(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>"
        )
        for match in fragment_pattern.finditer(html):
            attrs = match.group("attrs") or ""
            if not re.search(
                r"(comment|reply|aweme-comment|feed-comment|comment-item|reply-item|comment_list|commentList)",
                attrs,
                re.IGNORECASE,
            ):
                continue
            diagnostics["source_hits"]["regex_fragments"] += 1
            text = _normalize_extracted_text(match.group("body"))
            if not _looks_like_comment_text(text):
                continue
            normalized = _normalize_comment_content(text)
            content_hash = _hash_content(normalized)
            if content_hash in seen_content_hashes:
                continue
            seen_content_hashes.add(content_hash)
            signals = _extract_lightweight_comment_signals(normalized)
            item = _build_comment_item(
                content=normalized,
                video_url=video_url,
                source="dom_regex",
                content_hash=content_hash,
                raw={"source": "dom_regex", "match": "comment-like fragment"},
            )
            item["author_name"] = signals.get("author_name", "") or ""
            item["like_count"] = _to_count(signals.get("like_count"))
            item["reply_count"] = _to_count(signals.get("reply_count"))
            comments.append(
                item
            )
            if len(comments) >= 20:
                break
        if comments:
            warnings.append("dom_regex_extraction_used")
    except Exception as exc:  # pragma: no cover - runtime dependent
        warnings.append(f"dom regex extraction failed: {exc!s}")
        diagnostics["parse_failures"] += 1

    diagnostics["deduped_count"] = len(comments)
    return comments, warnings, diagnostics


def _extract_comment_candidates_via_json(
    html: str,
    video_url: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    warnings: list[str] = []
    comments: list[dict[str, Any]] = []
    seen_content_hashes: set[str] = set()
    diagnostics: dict[str, Any] = {
        "source_hits": {"script_tags": 0, "jsonish_strings": 0, "json_objects": 0},
        "parse_failures": 0,
        "deduped_count": 0,
    }

    try:
        script_pattern = re.compile(r"(?is)<script\b([^>]*)>(.*?)</script>")
        for attrs, script_body in script_pattern.findall(html):
            attrs_lower = (attrs or "").lower()
            script_text = unescape(script_body or "").strip()
            if not script_text:
                continue

            hint = (
                "application/json" in attrs_lower
                or "ld+json" in attrs_lower
                or "__next_data__" in attrs_lower
                or "comment" in attrs_lower
                or "content" in attrs_lower
                or "comment" in script_text.lower()
            )
            if not hint:
                continue
            diagnostics["source_hits"]["script_tags"] += 1

            extracted_texts = _extract_comment_like_strings_from_jsonish_text(script_text)
            if extracted_texts:
                diagnostics["source_hits"]["jsonish_strings"] += len(extracted_texts)
            if not extracted_texts and script_text[:1] in "{[":
                extracted_texts = _extract_comment_like_strings_from_parsed_json(script_text)
                if not extracted_texts:
                    diagnostics["parse_failures"] += 1

            extracted_objects: list[dict[str, Any]] = []
            if script_text[:1] in "{[":
                parsed_objects, parse_failures = _extract_comment_like_objects_from_parsed_json(script_text)
                diagnostics["parse_failures"] += parse_failures
                extracted_objects = parsed_objects
                if extracted_objects:
                    diagnostics["source_hits"]["json_objects"] += len(extracted_objects)

            for text in extracted_texts:
                normalized = _normalize_comment_content(text)
                if not _looks_like_comment_text(normalized):
                    continue
                content_hash = _hash_content(normalized)
                if content_hash in seen_content_hashes:
                    continue
                seen_content_hashes.add(content_hash)
                comments.append(
                    _build_comment_item(
                        content=normalized,
                        video_url=video_url,
                        source="dom_json",
                        content_hash=content_hash,
                        raw={"source": "dom_json", "script_hint": True},
                    )
                )
                if len(comments) >= 20:
                    break

            for obj in extracted_objects:
                normalized = _normalize_comment_content(str(obj.get("content") or ""))
                if not _looks_like_comment_text(normalized):
                    continue
                content_hash = _hash_content(normalized)
                if content_hash in seen_content_hashes:
                    continue
                seen_content_hashes.add(content_hash)
                item = _build_comment_item(
                    content=normalized,
                    video_url=video_url,
                    source="dom_json",
                    content_hash=content_hash,
                    raw={"source": "dom_json", "script_hint": True, "object_extracted": True},
                )
                item["author_name"] = _normalize_comment_content(str(obj.get("author_name") or ""))
                item["like_count"] = _to_count(obj.get("like_count"))
                item["reply_count"] = _to_count(obj.get("reply_count"))
                if not item["author_name"] or not item["like_count"] or not item["reply_count"]:
                    fallback_signals = _extract_lightweight_comment_signals(normalized)
                    item["author_name"] = item["author_name"] or fallback_signals.get("author_name", "") or ""
                    item["like_count"] = item["like_count"] or _to_count(fallback_signals.get("like_count"))
                    item["reply_count"] = item["reply_count"] or _to_count(fallback_signals.get("reply_count"))
                comments.append(item)
                if len(comments) >= 20:
                    break
            if len(comments) >= 20:
                break
        if comments:
            warnings.append("dom_json_extraction_used")
    except Exception as exc:  # pragma: no cover - runtime dependent
        warnings.append(f"dom json extraction failed: {exc!s}")
        diagnostics["parse_failures"] += 1

    diagnostics["deduped_count"] = len(comments)
    return comments, warnings, diagnostics


def _extract_comments_from_response(
    response: Any,
    video_url: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """解析 Playwright response；失败只记 warning 和 diagnostics，不中断采集。"""
    diagnostics = _new_extraction_diagnostics()
    warnings: list[str] = []
    response_url = str(getattr(response, "url", "") or "")
    content_type = ""

    try:
        header_value = getattr(response, "header_value", None)
        if callable(header_value):
            content_type = str(header_value("content-type") or "").lower()
    except Exception:
        content_type = ""

    if not _looks_like_real_comment_response_url(response_url):
        return [], warnings, diagnostics

    try:
        payload = response.json()
    except Exception as exc:
        diagnostics["parse_failures"] += 1
        diagnostics["response_listener_failures"] += 1
        warnings.append(f"comment response parse failed: {response_url or '<unknown>'}: {exc!s}")
        return [], warnings, diagnostics

    if not _payload_contains_comment_hints(payload):
        return [], warnings, diagnostics

    comments, payload_diag = _extract_comments_from_response_payload(payload, video_url, response_url)
    _merge_extraction_diagnostics(diagnostics, payload_diag)
    if comments:
        warnings.append("network_response_comment_extraction_used")
    return comments, warnings, diagnostics


def _wait_for_comment_payload(page: Any, *, timeout_ms: int) -> tuple[str, str, dict[str, Any]]:
    poll_ms = 3_000
    max_rounds = max(6, min(16, max(1, timeout_ms // poll_ms)))
    best_html = ""
    best_body = ""
    best_score = -10**9
    rounds: list[dict[str, Any]] = []

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

        score = len(current_body)
        if "全部评论" in current_body:
            score += 1000
        if "留下你的精彩评论吧" in current_body:
            score += 300
        if _has_empty_comment_state(current_body):
            score += 650
        if "分享" in current_body and "回复" in current_body:
            score += 800
        if "展开" in current_body and "条回复" in current_body:
            score += 500
        rounds.append(
            {
                "round": round_index,
                "html_length": len(current_html),
                "body_length": len(current_body),
                "score": score,
                "has_comment_header": "全部评论" in current_body,
                "has_reply_actions": "分享" in current_body and "回复" in current_body,
                "has_comment_placeholder": "留下你的精彩评论吧" in current_body,
                "has_empty_comment_state": _has_empty_comment_state(current_body),
            }
        )
        if score >= best_score:
            best_score = score
            best_html = current_html
            best_body = current_body
        if ("全部评论" in current_body or "留下你的精彩评论吧" in current_body) and (
            ("分享" in current_body and "回复" in current_body)
            or "暂时没有更多评论" in current_body
            or _has_empty_comment_state(current_body)
        ):
            break

    return best_html, best_body, {"rounds": rounds, "best_body_length": len(best_body), "best_score": best_score}


def _activate_comment_panel(page: Any) -> list[str]:
    warnings: list[str] = []
    selectors = [
        "text=全部评论",
        "text=留下你的精彩评论吧",
        "text=抢首评",
        "text=/展开\\d+条回复/",
        "[data-e2e*='comment']",
        "[class*='comment']",
        "[id*='comment']",
        "textarea",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector).first
            count = locator.count()
            if count <= 0:
                continue
            locator.click(timeout=1_500)
            page.wait_for_timeout(600)
            if _body_looks_like_comment_ready(page):
                warnings.append(f"comment_panel_activation={selector}")
                return warnings
        except Exception:
            continue

    try:
        page.keyboard.press("End")
        page.wait_for_timeout(600)
    except Exception:
        pass
    try:
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(800)
    except Exception:
        pass

    if _body_looks_like_comment_ready(page):
        warnings.append("comment_panel_activation=keyboard_end")
    else:
        warnings.append("comment_panel_activation_not_confirmed")
    return warnings


def _body_looks_like_comment_ready(page: Any) -> bool:
    try:
        body = page.inner_text("body")
    except Exception:
        return False
    return any(
        marker in body
        for marker in (
            "全部评论",
            "留下你的精彩评论吧",
            "暂时没有更多评论",
            "暂无评论",
            "抢首评",
            "分享",
        )
    )


def _has_empty_comment_state(text: str) -> bool:
    """识别平台明确展示的空评论态，避免把真实空态误判为采集失败。"""
    return any(marker in str(text or "") for marker in EMPTY_COMMENT_MARKERS)


def _build_comment_backend(*, comments: list[dict[str, Any]], stop_reason: str) -> str:
    """按最终状态标记后端来源，方便后续统计空态与失败态。"""
    if comments:
        if any(str((item.get("raw") or {}).get("source") or "") == "network_response_json" for item in comments):
            return "playwright:network-response-v1"
        return "playwright:body_text-v2"
    if stop_reason == "empty_comment_state":
        return "playwright:empty-state"
    return "playwright:placeholder"


def _extract_comments_from_body_text(
    body_text: str,
    video_url: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    diagnostics: dict[str, Any] = {
        "source_hits": {"body_text_blocks": 0, "body_text_stub_blocks": 0},
        "parse_failures": 0,
        "window_preview": "",
    }
    warnings: list[str] = []
    text = str(body_text or "")
    if not text or "全部评论" not in text:
        return [], warnings, diagnostics

    section = _extract_comment_section(text)
    if not section:
        start_index = text.find("全部评论")
        diagnostics["window_preview"] = _normalize_comment_content(text[start_index : start_index + 400])
        return [], warnings, diagnostics

    diagnostics["window_preview"] = _normalize_comment_content(section[:400])
    comments: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    block_pattern = re.compile(
        r"(?P<author>(?!\.\.\.$)[^\n]{1,80})\n(?:\.\.\.\n)?(?P<content>[^\n]{2,200})\n(?P<meta>\d+[^\n]{0,40})\n(?P<like>\d+(?:\.\d+)?(?:w|万)?)\n分享\n回复(?:\n展开(?P<reply>\d+)条回复)?",
        flags=re.MULTILINE,
    )

    for match in block_pattern.finditer(section):
        author_name = _normalize_comment_content(match.group("author"))
        content = _normalize_comment_content(match.group("content"))
        meta_line = _normalize_comment_content(match.group("meta"))
        if not _looks_like_comment_text(content):
            continue
        if author_name in {"...", "留下你的精彩评论吧", "大家都在搜："}:
            continue
        content_hash = _hash_content(content)
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        item = _build_comment_item(
            content=content,
            video_url=video_url,
            source="body_text_block",
            content_hash=content_hash,
            raw={
                "source": "body_text_block",
                "meta_line": meta_line,
                "window": "全部评论->推荐视频",
            },
        )
        item["author_name"] = author_name
        item["like_count"] = _to_count(match.group("like"))
        item["reply_count"] = _to_count(match.group("reply"))
        comments.append(item)
        diagnostics["source_hits"]["body_text_blocks"] += 1
        if len(comments) >= 20:
            break

    if comments:
        warnings.append("body_text_comment_extraction_used")
        return comments, warnings, diagnostics

    try:
        line_comments = _extract_comments_from_body_lines(section, video_url)
        comments.extend(line_comments)
        diagnostics["source_hits"]["body_text_blocks"] += len(line_comments)
        if line_comments:
            warnings.append("body_text_line_extraction_used")
    except Exception as exc:
        diagnostics["parse_failures"] += 1
        warnings.append(f"body_text_line_extraction_failed: {exc!s}")

    if comments:
        return comments[:20], warnings, diagnostics

    try:
        stub_comments = _extract_comment_stubs_from_body_lines(section, video_url)
        comments.extend(stub_comments)
        diagnostics["source_hits"]["body_text_stub_blocks"] += len(stub_comments)
        if stub_comments:
            warnings.append("body_text_stub_extraction_used")
    except Exception as exc:
        diagnostics["parse_failures"] += 1
        warnings.append(f"body_text_stub_extraction_failed: {exc!s}")

    return comments[:20], warnings, diagnostics


def _extract_comments_from_body_lines(section: str, video_url: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in section.splitlines() if line and line.strip()]
    comments: list[dict[str, Any]] = []
    idx = 0
    while idx < len(lines):
        author = lines[idx]
        if not _looks_like_comment_author(author):
            idx += 1
            continue

        cursor = idx + 1
        while cursor < len(lines) and _is_comment_badge_line(lines[cursor]):
            cursor += 1
        if cursor >= len(lines):
            break
        if lines[cursor] == "...":
            cursor += 1
        if cursor >= len(lines):
            break

        content = lines[cursor]
        if (
            not _looks_like_comment_text(content)
            or _looks_like_comment_meta_line(content)
            or _is_comment_control_line(content)
        ):
            idx += 1
            continue

        cursor += 1
        while cursor < len(lines) and _is_comment_badge_line(lines[cursor]):
            cursor += 1
        if cursor >= len(lines) or not _looks_like_comment_meta_line(lines[cursor]):
            idx += 1
            continue
        meta_idx = cursor

        like_idx = meta_idx + 1
        if like_idx >= len(lines) or not re.fullmatch(r"\d+(?:\.\d+)?(?:w|万)?", lines[like_idx]):
            idx += 1
            continue

        reply_count = 0
        cursor = like_idx + 1
        while cursor < len(lines) and _is_comment_control_line(lines[cursor]):
            reply_match = re.fullmatch(r"展开(\d+)条回复", lines[cursor])
            if reply_match:
                reply_count = _to_count(reply_match.group(1))
            cursor += 1
        if not _looks_like_comment_text(content):
            idx = max(cursor, idx + 1)
            continue
        content_hash = _hash_content(content)
        item = _build_comment_item(
            content=content,
            video_url=video_url,
            source="body_text_lines",
            content_hash=content_hash,
            raw={"source": "body_text_lines", "meta_line": lines[meta_idx]},
        )
        item["author_name"] = _normalize_comment_content(author)
        item["like_count"] = _to_count(lines[like_idx])
        item["reply_count"] = reply_count
        comments.append(item)
        if len(comments) >= 20:
            break
        idx = max(cursor, idx + 1)
    return _dedupe_comment_items(comments)


def _extract_comment_stubs_from_body_lines(section: str, video_url: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in section.splitlines() if line and line.strip()]
    comments: list[dict[str, Any]] = []
    idx = 0

    while idx < len(lines):
        author = lines[idx]
        if not _looks_like_comment_author(author):
            idx += 1
            continue

        cursor = idx + 1
        while cursor < len(lines) and _is_comment_badge_line(lines[cursor]):
            cursor += 1
        if cursor >= len(lines):
            break

        meta_line = lines[cursor]
        if not _looks_like_comment_meta_line(meta_line):
            idx += 1
            continue

        like_idx = cursor + 1
        if like_idx >= len(lines) or not re.fullmatch(r"\d+(?:\.\d+)?(?:w|万)?", lines[like_idx]):
            idx += 1
            continue

        cursor = like_idx + 1
        saw_share = False
        saw_reply = False
        reply_count = 0
        while cursor < len(lines):
            current = lines[cursor]
            if current == "分享":
                saw_share = True
                cursor += 1
                continue
            if current == "回复":
                saw_reply = True
                cursor += 1
                continue
            reply_match = re.fullmatch(r"展开(\d+)条回复", current)
            if reply_match:
                reply_count = _to_count(reply_match.group(1))
                cursor += 1
                continue
            break

        if not (saw_share and saw_reply):
            idx += 1
            continue

        author_name = _normalize_comment_content(author)
        normalized_meta = _normalize_comment_content(meta_line)
        stub_seed = f"{author_name}|{normalized_meta}|{lines[like_idx]}"
        content = f"[评论正文未渲染] {author_name} {normalized_meta}"
        content_hash = _hash_content(stub_seed)
        item = _build_comment_item(
            content=content,
            video_url=video_url,
            source="body_text_stub",
            content_hash=content_hash,
            raw={
                "source": "body_text_stub",
                "meta_line": normalized_meta,
                "window": "全部评论->推荐视频",
                "stub": True,
            },
        )
        item["author_name"] = author_name
        item["like_count"] = _to_count(lines[like_idx])
        item["reply_count"] = reply_count
        comments.append(item)
        if len(comments) >= 20:
            break
        idx = max(cursor, idx + 1)

    return _dedupe_comment_items(comments)


def _looks_like_comment_author(text: str) -> bool:
    normalized = _normalize_comment_content(text)
    if not normalized:
        return False
    if normalized in {"...", "留下你的精彩评论吧", "大家都在搜：", "加载中", "暂时没有更多评论"}:
        return False
    if len(normalized) > 80:
        return False
    if _looks_like_comment_meta_line(normalized):
        return False
    if _is_comment_control_line(normalized):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?(?:w|万)?", normalized):
        return False
    return True


def _looks_like_comment_meta_line(text: str) -> bool:
    normalized = _normalize_comment_content(text)
    if not normalized:
        return False
    return bool(
        re.search(
            r"(?:刚刚|昨天|前天|\d{1,2}[/-]\d{1,2}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d+\s*(?:分钟|小时|天|周|月|年)前)"
            r"(?:\s+\d{1,2}:\d{2})?(?:·[^\n]{1,40})?$",
            normalized,
        )
    )


def _is_comment_control_line(text: str) -> bool:
    normalized = _normalize_comment_content(text)
    if not normalized:
        return False
    if normalized in {"分享", "回复", "加载中", "暂时没有更多评论"}:
        return True
    if normalized.startswith("展开") and normalized.endswith("条回复"):
        return True
    return False


def _extract_comment_section(text: str) -> str:
    start_index = text.find("全部评论")
    if start_index < 0:
        return ""

    section = text[start_index + len("全部评论") :]
    end_markers = (
        "加载中",
        "下载客户端，桌面快捷访问",
        "推荐视频",
        "广告投放",
        "暂时没有更多评论",
        "大家都在搜",
        "相关推荐",
    )
    empty_index = _find_first_empty_comment_marker(section)
    end_indexes = [section.find(marker) for marker in end_markers if section.find(marker) >= 0]
    if end_indexes:
        end_index = min(end_indexes)
        if empty_index >= 0 and end_index < empty_index:
            # 空评论态经常跟在“大家都在搜”后面，截断太早会丢失关键状态。
            section = section[: empty_index + len(_empty_comment_marker_at(section, empty_index))]
        else:
            section = section[:end_index]
    elif len(section) > 4_000:
        section = section[:4_000]
    return section.strip()


def _find_first_empty_comment_marker(text: str) -> int:
    """返回空评论态标记的最早位置，未命中返回 -1。"""
    indexes = [str(text or "").find(marker) for marker in EMPTY_COMMENT_MARKERS]
    valid_indexes = [index for index in indexes if index >= 0]
    return min(valid_indexes) if valid_indexes else -1


def _empty_comment_marker_at(text: str, index: int) -> str:
    """找出指定位置对应的空评论态标记，用于保留完整窗口。"""
    for marker in EMPTY_COMMENT_MARKERS:
        if str(text or "").startswith(marker, index):
            return marker
    return ""


def _is_comment_badge_line(text: str) -> bool:
    normalized = _normalize_comment_content(text)
    if not normalized:
        return False
    if normalized == "...":
        return True
    if normalized in {"作者", "置顶"}:
        return True
    if normalized.endswith("赞过"):
        return True
    return False


def _extract_comments_from_dom(page, *, html: str | None = None, body_text: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
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
        "extraction_diagnostics": _new_extraction_diagnostics(),
    }

    comments: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = []

    try:
        # NOTE: this read keeps the hook grounded in the live DOM without
        # committing to any platform-specific selector yet.
        html = html if html is not None else page.content()
        page_url = str(getattr(page, "url", "") or "")
        body_text = body_text if body_text is not None else page.inner_text("body")

        live_dom_comments, live_dom_warnings, live_dom_diag = _extract_comment_candidates_via_live_dom(page, page_url)
        meta["warnings"].extend(live_dom_warnings)
        live_hits = live_dom_diag.get("source_hits") or {}
        meta["extraction_diagnostics"]["source_hits"]["json_objects"] += int(live_hits.get("dom_nodes") or 0)
        meta["extraction_diagnostics"]["parse_failures"] += int(live_dom_diag.get("parse_failures") or 0)
        if live_dom_comments:
            comments.extend(live_dom_comments)

        regex_comments, regex_warnings, regex_diag = _extract_comment_candidates_via_regex(html, page_url)
        meta["warnings"].extend(regex_warnings)
        meta["extraction_diagnostics"]["source_hits"]["regex_fragments"] += int(
            (regex_diag.get("source_hits") or {}).get("regex_fragments") or 0
        )
        meta["extraction_diagnostics"]["parse_failures"] += int(regex_diag.get("parse_failures") or 0)
        if regex_comments:
            comments.extend(regex_comments)

        json_comments, json_warnings, json_diag = _extract_comment_candidates_via_json(html, page_url)
        meta["warnings"].append("dom_json_extraction_used")
        meta["warnings"].extend(json_warnings)
        json_hits = json_diag.get("source_hits") or {}
        meta["extraction_diagnostics"]["source_hits"]["script_tags"] += int(json_hits.get("script_tags") or 0)
        meta["extraction_diagnostics"]["source_hits"]["jsonish_strings"] += int(
            json_hits.get("jsonish_strings") or 0
        )
        meta["extraction_diagnostics"]["source_hits"]["json_objects"] += int(json_hits.get("json_objects") or 0)
        meta["extraction_diagnostics"]["parse_failures"] += int(json_diag.get("parse_failures") or 0)
        if json_comments:
            comments.extend(json_comments)

        body_comments, body_warnings, body_diag = _extract_comments_from_body_text(body_text, page_url)
        meta["warnings"].extend(body_warnings)
        meta["extraction_diagnostics"]["source_hits"]["regex_fragments"] += int(
            (body_diag.get("source_hits") or {}).get("body_text_blocks") or 0
        )
        meta["extraction_diagnostics"]["parse_failures"] += int(body_diag.get("parse_failures") or 0)
        if body_comments:
            comments.extend(body_comments)

        helper_failure_warnings = [
            warning
            for warning in (regex_warnings + json_warnings)
            if "failed" in warning.lower()
        ]
        if helper_failure_warnings and not meta["stop_reason_detail"]:
            meta["stop_reason_detail"] = "; ".join(helper_failure_warnings[:3])

        comments = _dedupe_comment_items(comments)[:20]
        meta["extraction_diagnostics"]["final_deduped_count"] = len(comments)
        meta["dom_items_seen"] = len(comments) + len(replies)
        return comments, replies, meta
    except Exception as exc:  # pragma: no cover - runtime dependent
        meta["stop_reason"] = "dom_extract_error"
        meta["stop_reason_detail"] = str(exc)
        meta["warnings"].append(f"dom extraction failed: {exc!s}")
        return [], [], meta


def _extract_comment_candidates_via_live_dom(
    page: Any,
    video_url: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    warnings: list[str] = []
    diagnostics: dict[str, Any] = {
        "source_hits": {"dom_nodes": 0},
        "parse_failures": 0,
        "deduped_count": 0,
    }
    comments: list[dict[str, Any]] = []

    script = """
() => {
  const textOf = (node) => ((node && (node.innerText || node.textContent)) || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (node) => {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (!style) return true;
    return style.display !== 'none' && style.visibility !== 'hidden';
  };
  const candidates = [];
  const seen = new Set();
  const nodes = Array.from(document.querySelectorAll('div, li, article, section'));
  for (const node of nodes) {
    if (!isVisible(node)) continue;
    const text = textOf(node);
    if (!text || text.length < 8 || text.length > 500) continue;
    if (!(text.includes('分享') && text.includes('回复'))) continue;
    const lines = text.split(/\\n+/).map(x => x.trim()).filter(Boolean);
    if (lines.length < 4) continue;
    let author = '';
    let content = '';
    let meta = '';
    let like = '';
    for (let i = 0; i < Math.min(lines.length, 12); i++) {
      const line = lines[i];
      if (!author && line.length <= 40 && !/^(分享|回复|展开\\d+条回复|\\d+(?:\\.\\d+)?(?:w|万)?)$/.test(line)) {
        author = line;
        continue;
      }
      if (!content && author && line !== author && !/(刚刚|分钟前|小时前|天前|周前|月前|年前)/.test(line) && line !== '...' && !/^(分享|回复)$/.test(line)) {
        content = line;
        continue;
      }
      if (!meta && /(刚刚|\\d+\\s*(?:分钟|小时|天|周|月|年)前)/.test(line)) {
        meta = line;
        continue;
      }
      if (!like && /^\\d+(?:\\.\\d+)?(?:w|万)?$/.test(line)) {
        like = line;
      }
    }
    if (!author || (!content && !meta)) continue;
    const sig = [author, content, meta, like].join('|');
    if (seen.has(sig)) continue;
    seen.add(sig);
    candidates.push({author_name: author, content: content, meta_line: meta, like_text: like, raw_text: text.slice(0, 500)});
    if (candidates.length >= 30) break;
  }
  return candidates;
}
"""

    try:
        nodes = page.evaluate(script)
    except Exception as exc:
        warnings.append(f"live_dom_extraction_failed: {exc!s}")
        diagnostics["parse_failures"] += 1
        return comments, warnings, diagnostics

    if not isinstance(nodes, list):
        return comments, warnings, diagnostics

    seen_hashes: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        author_name = _normalize_comment_content(node.get("author_name"))
        content = _normalize_comment_content(node.get("content"))
        meta_line = _normalize_comment_content(node.get("meta_line"))
        if not content and meta_line:
            content = f"[评论正文未渲染] {author_name} {meta_line}"
        if not _looks_like_comment_author(author_name):
            continue
        if not _looks_like_comment_text(content):
            continue
        content_hash = _hash_content(f"{author_name}|{content}|{meta_line}")
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        item = _build_comment_item(
            content=content,
            video_url=video_url,
            source="live_dom",
            content_hash=content_hash,
            raw={
                "source": "live_dom",
                "meta_line": meta_line,
                "raw_text": _normalize_comment_content(node.get("raw_text")),
                "stub": content.startswith("[评论正文未渲染]"),
            },
        )
        item["author_name"] = author_name
        item["like_count"] = _to_count(node.get("like_text"))
        comments.append(item)
        diagnostics["source_hits"]["dom_nodes"] += 1
        if len(comments) >= 20:
            break

    diagnostics["deduped_count"] = len(comments)
    if comments:
        warnings.append("live_dom_extraction_used")
    return comments, warnings, diagnostics


def _has_playwright() -> bool:
    return find_spec("playwright") is not None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

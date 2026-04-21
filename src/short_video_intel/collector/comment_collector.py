from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from importlib.util import find_spec
import re
from html import unescape
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
                "extraction_diagnostics": {
                    "source_hits": {"regex_fragments": 0, "script_tags": 0, "jsonish_strings": 0, "json_objects": 0},
                    "parse_failures": 0,
                    "final_deduped_count": 0,
                },
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
                "extraction_diagnostics": {
                    "source_hits": {"regex_fragments": 0, "script_tags": 0, "jsonish_strings": 0, "json_objects": 0},
                    "parse_failures": 0,
                    "final_deduped_count": 0,
                },
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
    extraction_diagnostics: dict[str, Any] = {
        "source_hits": {"regex_fragments": 0, "script_tags": 0, "jsonish_strings": 0, "json_objects": 0},
        "parse_failures": 0,
        "final_deduped_count": 0,
    }
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
                        dom_diag = dom_meta.get("extraction_diagnostics") or {}
                        dom_hits = dom_diag.get("source_hits") or {}
                        extraction_diagnostics["source_hits"]["regex_fragments"] += int(
                            dom_hits.get("regex_fragments") or 0
                        )
                        extraction_diagnostics["source_hits"]["script_tags"] += int(dom_hits.get("script_tags") or 0)
                        extraction_diagnostics["source_hits"]["jsonish_strings"] += int(
                            dom_hits.get("jsonish_strings") or 0
                        )
                        extraction_diagnostics["source_hits"]["json_objects"] += int(
                            dom_hits.get("json_objects") or 0
                        )
                        extraction_diagnostics["parse_failures"] += int(dom_diag.get("parse_failures") or 0)
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
                "extraction_diagnostics": extraction_diagnostics,
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


def _extract_comment_like_objects_from_parsed_json(
    text: str,
) -> tuple[list[dict[str, Any]], int]:
    try:
        data = json.loads(text)
    except Exception:
        return [], 1

    objects: list[dict[str, Any]] = []
    text_keys = {"text", "content", "comment", "commenttext", "comment_content", "commentcontent"}
    like_keys = ("like_count", "digg_count", "likes", "like", "upvote_count", "vote_count")
    reply_keys = ("reply_count", "reply_cnt", "reply_num", "replynum", "sub_comment_count", "children_count")

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            collected_text = ""
            for key in text_keys:
                value = node.get(key)
                if isinstance(value, str):
                    collected_text = value
                    break
            if collected_text:
                like_count = 0
                reply_count = 0
                for key in like_keys:
                    if key in node:
                        like_count = _to_count(node.get(key))
                        if like_count:
                            break
                for key in reply_keys:
                    if key in node:
                        reply_count = _to_count(node.get(key))
                        if reply_count:
                            break
                objects.append(
                    {
                        "content": collected_text,
                        "author_name": _extract_author_name_from_node(node),
                        "like_count": like_count,
                        "reply_count": reply_count,
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
        "extraction_diagnostics": {
            "source_hits": {"regex_fragments": 0, "script_tags": 0, "jsonish_strings": 0, "json_objects": 0},
            "parse_failures": 0,
            "final_deduped_count": 0,
        },
    }

    comments: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = []

    try:
        # NOTE: this read keeps the hook grounded in the live DOM without
        # committing to any platform-specific selector yet.
        html = page.content()
        page_url = str(getattr(page, "url", "") or "")

        regex_comments, regex_warnings, regex_diag = _extract_comment_candidates_via_regex(html, page_url)
        meta["warnings"].append("dom_regex_extraction_used")
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


def _has_playwright() -> bool:
    return find_spec("playwright") is not None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

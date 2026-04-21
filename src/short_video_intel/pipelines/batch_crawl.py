from __future__ import annotations

"""Batch execution helpers for the data-collection phase."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from time import monotonic
from typing import Any

from ..collector.comment_collector import collect_video_comments
from ..collector.homepage_collector import collect_homepage_videos
from ..collector.video_collector import collect_video_detail


def run_batch_homepage_crawl(
    config: Any,
    targets: list[dict[str, Any]],
    max_items: int = 50,
    max_workers: int = 1,
) -> dict[str, Any]:
    """Run homepage collection for a batch of targets.

    Parameters
    ----------
    config:
        Application config forwarded to the homepage collector.
    targets:
        A list of target dictionaries. Each item must at least contain
        ``homepage_url`` and may include additional metadata such as
        ``source_name`` or category fields.
    max_items:
        Maximum number of video candidates to request from each homepage
        collection call.
    max_workers:
        ``1`` runs sequentially. Values greater than ``1`` use a
        ``ThreadPoolExecutor`` to collect homepages concurrently.

    Returns
    -------
    dict[str, Any]
        Batch report with timing, counts, per-target results, and failures.
    """

    if max_workers <= 0:
        raise ValueError("max_workers must be greater than 0")

    started_at = _now_iso()
    started_monotonic = monotonic()

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    target_items = list(targets)
    worker = partial(_collect_single_homepage, config=config, max_items=max_items)

    if max_workers == 1 or len(target_items) <= 1:
        for target in target_items:
            outcome = worker(target)
            _record_outcome(outcome, results, failures)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for outcome in executor.map(worker, target_items):
                _record_outcome(outcome, results, failures)

    finished_at = _now_iso()
    duration_sec = round(monotonic() - started_monotonic, 6)

    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": duration_sec,
        "total_targets": len(target_items),
        "success_count": len(results),
        "failed_count": len(failures),
        "results": results,
        "failures": failures,
    }


def run_batch_full_collect(
    config: Any,
    targets: list[dict[str, Any]],
    *,
    with_video_detail: bool = False,
    with_comments: bool = False,
    comment_pages: int = 3,
    max_items: int = 50,
    max_workers: int = 1,
    video_limit_per_target: int | None = None,
    comment_video_limit_per_target: int | None = None,
) -> dict[str, Any]:
    """Run homepage, detail, and comment collection for a batch of targets."""

    if max_workers <= 0:
        raise ValueError("max_workers must be greater than 0")

    started_at = _now_iso()
    started_monotonic = monotonic()
    normalized_comment_pages = max(0, int(comment_pages))

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    target_items = list(targets)
    worker = partial(
        _collect_single_full_target,
        config=config,
        max_items=max_items,
        with_video_detail=with_video_detail,
        with_comments=with_comments,
        comment_pages=normalized_comment_pages,
        video_limit_per_target=video_limit_per_target,
        comment_video_limit_per_target=comment_video_limit_per_target,
    )

    if max_workers == 1 or len(target_items) <= 1:
        for target in target_items:
            outcome = worker(target)
            _record_full_outcome(outcome, results, failures)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for outcome in executor.map(worker, target_items):
                _record_full_outcome(outcome, results, failures)

    finished_at = _now_iso()
    duration_sec = round(monotonic() - started_monotonic, 6)
    summary_block = _build_full_batch_summary(
        results=results,
        failures=failures,
        total_targets=len(target_items),
        with_video_detail=with_video_detail,
        with_comments=with_comments,
        comment_pages=normalized_comment_pages,
        max_items=max_items,
        video_limit_per_target=video_limit_per_target,
        comment_video_limit_per_target=comment_video_limit_per_target,
    )

    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": duration_sec,
        "total_targets": len(target_items),
        "success_count": len(results),
        "failed_count": len(failures),
        "results": results,
        "failures": failures,
        "summary_block": summary_block,
        "with_video_detail": with_video_detail,
        "with_comments": with_comments,
        "comment_pages": normalized_comment_pages,
        "max_items": max_items,
        "video_limit_per_target": video_limit_per_target,
        "comment_video_limit_per_target": comment_video_limit_per_target,
    }


def _collect_single_homepage(
    target: Any,
    *,
    config: Any,
    max_items: int,
) -> dict[str, Any]:
    target_copy: dict[str, Any]
    try:
        target_copy = _normalize_target(target)
        homepage_url = _extract_homepage_url(target_copy)
        crawl_result = collect_homepage_videos(
            config,
            homepage_url,
            max_items=max_items,
        )
        return {
            "ok": True,
            "target": target_copy,
            "crawl_result": crawl_result,
        }
    except Exception as exc:  # pragma: no cover - batch safety fallback
        if isinstance(target, dict):
            target_copy = dict(target)
        else:
            target_copy = {"raw_target": repr(target)}
        return {
            "ok": False,
            "target": target_copy,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _record_outcome(
    outcome: dict[str, Any],
    results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    if outcome.get("ok"):
        results.append(
            {
                "target": outcome["target"],
                "crawl_result": outcome["crawl_result"],
            }
        )
    else:
        failures.append(
            {
                "target": outcome["target"],
                "error": outcome["error"],
            }
        )


def _record_full_outcome(
    outcome: dict[str, Any],
    results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    if outcome.get("ok"):
        results.append(
            {
                "target": outcome["target"],
                "homepage_result": outcome["homepage_result"],
                "video_items": outcome["video_items"],
                "summary": outcome["summary"],
            }
        )
    else:
        failures.append(
            {
                "target": outcome["target"],
                "error": outcome["error"],
            }
        )


def _build_full_batch_summary(
    *,
    results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    total_targets: int,
    with_video_detail: bool,
    with_comments: bool,
    comment_pages: int,
    max_items: int,
    video_limit_per_target: int | None,
    comment_video_limit_per_target: int | None,
) -> dict[str, Any]:
    account_summary: list[dict[str, Any]] = []
    total_videos_seen = 0
    total_detail_success = 0
    total_detail_attempted = 0
    total_comment_success = 0
    total_comment_attempted = 0
    total_comment_items_seen = 0
    total_comment_entries_seen = 0
    total_comment_reply_entries_seen = 0
    total_detail_meaningful = 0
    total_comment_meaningful = 0

    for item in results:
        target = item.get("target") or {}
        homepage_result = item.get("homepage_result") or {}
        video_items = item.get("video_items") or []
        summary = item.get("summary") or {}

        videos_seen = _safe_int(summary.get("homepage_videos_seen"), default=len(video_items))
        detail_success = _safe_int(summary.get("detail_succeeded"))
        detail_attempted = _safe_int(summary.get("detail_attempted"))
        comments_success = _safe_int(summary.get("comments_succeeded"))
        comments_attempted = _safe_int(summary.get("comments_attempted"))
        comment_items_seen = _safe_int(summary.get("comment_items_seen"))
        comment_entries_seen = _safe_int(summary.get("comment_entries_seen"))
        comment_reply_entries_seen = _safe_int(summary.get("comment_reply_entries_seen"))
        detail_meaningful = _safe_int(summary.get("detail_meaningful"))
        comment_meaningful = _safe_int(summary.get("comment_meaningful"))
        warnings_count = _count_full_batch_warnings(item)

        total_videos_seen += videos_seen
        total_detail_success += detail_success
        total_detail_attempted += detail_attempted
        total_comment_success += comments_success
        total_comment_attempted += comments_attempted
        total_comment_items_seen += comment_items_seen
        total_comment_entries_seen += comment_entries_seen
        total_comment_reply_entries_seen += comment_reply_entries_seen
        total_detail_meaningful += detail_meaningful
        total_comment_meaningful += comment_meaningful

        account_summary.append(
            {
                "homepage_url": target.get("homepage_url"),
                "source_name": target.get("source_name"),
                "videos_seen": videos_seen,
                "detail_success": detail_success,
                "comments_success": comments_success,
                "comment_items_seen": comment_items_seen,
                "comment_entries_seen": comment_entries_seen,
                "comment_reply_entries_seen": comment_reply_entries_seen,
                "detail_meaningful": detail_meaningful,
                "comment_meaningful": comment_meaningful,
                "warnings_count": warnings_count,
                "video_limit_per_target": video_limit_per_target,
                "comment_video_limit_per_target": comment_video_limit_per_target,
                "backend": homepage_result.get("backend"),
                "extraction_version": homepage_result.get("extraction_version"),
            }
        )

    detail_success_rate = (
        round(total_detail_success / total_detail_attempted, 6)
        if total_detail_attempted
        else 0.0
    )
    comment_success_rate = (
        round(total_comment_success / total_comment_attempted, 6)
        if total_comment_attempted
        else 0.0
    )

    return {
        "account_summary": account_summary,
        "global_summary": {
            "target_count": total_targets,
            "video_total": total_videos_seen,
            "detail_attempted": total_detail_attempted,
            "detail_success_count": total_detail_success,
            "detail_success_rate": detail_success_rate,
            "comment_attempted": total_comment_attempted,
            "comment_success_count": total_comment_success,
            "comment_success_rate": comment_success_rate,
            "comment_items_seen": total_comment_items_seen,
            "comment_entries_seen": total_comment_entries_seen,
            "comment_reply_entries_seen": total_comment_reply_entries_seen,
            "detail_meaningful_count": total_detail_meaningful,
            "comment_meaningful_count": total_comment_meaningful,
            "failed_count": len(failures),
            "with_video_detail": with_video_detail,
            "with_comments": with_comments,
            "comment_pages": comment_pages,
            "max_items": max_items,
            "video_limit_per_target": video_limit_per_target,
            "comment_video_limit_per_target": comment_video_limit_per_target,
        },
    }


def _collect_single_full_target(
    target: Any,
    *,
    config: Any,
    max_items: int,
    with_video_detail: bool,
    with_comments: bool,
    comment_pages: int,
    video_limit_per_target: int | None,
    comment_video_limit_per_target: int | None,
) -> dict[str, Any]:
    target_copy: dict[str, Any]
    try:
        target_copy = _normalize_target(target)
        homepage_url = _extract_homepage_url(target_copy)
        homepage_result = collect_homepage_videos(
            config,
            homepage_url,
            max_items=max_items,
        )

        videos = list(homepage_result.get("videos") or [])
        video_items: list[dict[str, Any]] = []
        detail_attempted = 0
        detail_succeeded = 0
        detail_failed = 0
        comments_attempted = 0
        comments_succeeded = 0
        comments_failed = 0
        detail_meaningful = 0
        comment_meaningful = 0
        comment_items_seen = 0
        comment_entries_seen = 0
        comment_reply_entries_seen = 0

        normalized_video_limit = _normalize_optional_limit(video_limit_per_target)
        normalized_comment_limit = _normalize_optional_limit(comment_video_limit_per_target)
        detail_budget_remaining = normalized_video_limit
        comment_budget_remaining = normalized_comment_limit

        for candidate in videos:
            candidate_copy = _normalize_video_candidate(candidate)
            video_url = _extract_video_url(candidate_copy)
            item: dict[str, Any] = {"candidate": candidate_copy}

            allow_detail = with_video_detail and video_url and (
                detail_budget_remaining is None or detail_budget_remaining > 0
            )
            if allow_detail:
                detail_attempted += 1
                try:
                    detail_result = collect_video_detail(config, video_url=video_url)
                    item["detail_result"] = detail_result
                    detail_succeeded += 1
                    if _is_meaningful_detail_result(detail_result):
                        detail_meaningful += 1
                except Exception as exc:  # pragma: no cover - runtime safety fallback
                    item["detail_error"] = f"{type(exc).__name__}: {exc}"
                    detail_failed += 1
                finally:
                    if detail_budget_remaining is not None:
                        detail_budget_remaining -= 1
            elif with_video_detail and video_url:
                item["detail_skipped"] = "video_limit_reached"

            allow_comments = with_comments and video_url and (
                comment_budget_remaining is None or comment_budget_remaining > 0
            )
            if allow_comments:
                comments_attempted += 1
                try:
                    comments_result = collect_video_comments(
                        config,
                        video_url=video_url,
                        max_pages=comment_pages,
                    )
                    item["comments_result"] = comments_result
                    comments_succeeded += 1
                    comment_items_seen += 1
                    comment_entries_seen += len(list((comments_result or {}).get("comments") or []))
                    comment_reply_entries_seen += len(list((comments_result or {}).get("replies") or []))
                    if _is_meaningful_comment_result(comments_result):
                        comment_meaningful += 1
                except Exception as exc:  # pragma: no cover - runtime safety fallback
                    item["comments_error"] = f"{type(exc).__name__}: {exc}"
                    comments_failed += 1
                finally:
                    if comment_budget_remaining is not None:
                        comment_budget_remaining -= 1
            elif with_comments and video_url:
                item["comments_skipped"] = "comment_video_limit_reached"

            video_items.append(item)

        return {
            "ok": True,
            "target": target_copy,
            "homepage_result": homepage_result,
            "video_items": video_items,
            "summary": {
                "homepage_videos_seen": len(videos),
                "detail_attempted": detail_attempted,
                "detail_succeeded": detail_succeeded,
                "detail_failed": detail_failed,
                "comments_attempted": comments_attempted,
                "comments_succeeded": comments_succeeded,
                "comments_failed": comments_failed,
                "detail_meaningful": detail_meaningful,
                "comment_meaningful": comment_meaningful,
                "comment_items_seen": comment_items_seen,
                "comment_entries_seen": comment_entries_seen,
                "comment_reply_entries_seen": comment_reply_entries_seen,
                "with_video_detail": with_video_detail,
                "with_comments": with_comments,
                "comment_pages": comment_pages,
                "max_items": max_items,
                "video_limit_per_target": normalized_video_limit,
                "comment_video_limit_per_target": normalized_comment_limit,
            },
        }
    except Exception as exc:  # pragma: no cover - batch safety fallback
        if isinstance(target, dict):
            target_copy = dict(target)
        else:
            target_copy = {"raw_target": repr(target)}
        return {
            "ok": False,
            "target": target_copy,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _normalize_target(target: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(target, dict):
        raise TypeError("each target must be a dict")
    return dict(target)


def _extract_homepage_url(target: dict[str, Any]) -> str:
    homepage_url = str(target.get("homepage_url", "")).strip()
    if not homepage_url:
        raise ValueError("target.homepage_url must not be empty")
    return homepage_url


def _normalize_video_candidate(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        return dict(candidate)
    if hasattr(candidate, "model_dump"):
        return dict(candidate.model_dump())  # type: ignore[call-arg]
    if hasattr(candidate, "__dict__"):
        return {
            key: value
            for key, value in dict(candidate.__dict__).items()
            if not str(key).startswith("_sa_")
        }
    return {"raw_candidate": repr(candidate)}


def _extract_video_url(candidate: dict[str, Any]) -> str:
    video_url = str(candidate.get("video_url", "")).strip()
    if video_url:
        return video_url
    video_id = str(candidate.get("video_id", "")).strip()
    if video_id:
        return f"https://www.douyin.com/video/{video_id}"
    return ""


def _count_full_batch_warnings(item: dict[str, Any]) -> int:
    warning_count = 0

    homepage_result = item.get("homepage_result")
    if isinstance(homepage_result, dict):
        warning_count += len(homepage_result.get("warnings") or [])
        diagnostics = homepage_result.get("diagnostics")
        if isinstance(diagnostics, dict):
            warning_count += len(diagnostics.get("warnings") or [])

    for video_item in item.get("video_items") or []:
        if not isinstance(video_item, dict):
            continue
        detail_result = video_item.get("detail_result")
        if isinstance(detail_result, dict):
            warning_count += len(detail_result.get("warnings") or [])
        elif video_item.get("detail_error"):
            warning_count += 1

        comments_result = video_item.get("comments_result")
        if isinstance(comments_result, dict):
            warning_count += len(comments_result.get("warnings") or [])
            scan_meta = comments_result.get("scan_meta")
            if isinstance(scan_meta, dict):
                warning_count += len(scan_meta.get("warnings") or [])
        elif video_item.get("comments_error"):
            warning_count += 1

    return warning_count


def _is_meaningful_detail_result(detail_result: Any) -> bool:
    if not isinstance(detail_result, dict):
        return False
    metrics = detail_result.get("metrics")
    if isinstance(metrics, dict):
        for key in ("like_count", "comment_count", "share_count", "view_count"):
            value = metrics.get(key)
            if isinstance(value, (int, float)) and int(value) > 0:
                return True
    raw = detail_result.get("raw")
    if isinstance(raw, dict):
        text_diag = raw.get("extraction_diagnostics")
        if isinstance(text_diag, dict):
            text_body = text_diag.get("text_body")
            if isinstance(text_body, dict) and text_body.get("matched"):
                return True
    backend = str(detail_result.get("backend") or "")
    return backend.startswith("playwright:")


def _is_meaningful_comment_result(comments_result: Any) -> bool:
    if not isinstance(comments_result, dict):
        return False
    comments = comments_result.get("comments")
    if isinstance(comments, list) and len(comments) > 0:
        return True
    scan_meta = comments_result.get("scan_meta")
    if isinstance(scan_meta, dict):
        backend = str(scan_meta.get("backend") or "")
        stop_reason = str(scan_meta.get("stop_reason") or "")
        if backend == "playwright:body_text-v1":
            return True
        if stop_reason == "body_text_comments_captured":
            return True
    return False


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_optional_limit(value: Any) -> int | None:
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    if normalized <= 0:
        return None
    return normalized


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

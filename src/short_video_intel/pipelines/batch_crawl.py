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

    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": duration_sec,
        "total_targets": len(target_items),
        "success_count": len(results),
        "failed_count": len(failures),
        "results": results,
        "failures": failures,
        "with_video_detail": with_video_detail,
        "with_comments": with_comments,
        "comment_pages": normalized_comment_pages,
        "max_items": max_items,
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


def _collect_single_full_target(
    target: Any,
    *,
    config: Any,
    max_items: int,
    with_video_detail: bool,
    with_comments: bool,
    comment_pages: int,
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

        for candidate in videos:
            candidate_copy = _normalize_video_candidate(candidate)
            video_url = _extract_video_url(candidate_copy)
            item: dict[str, Any] = {"candidate": candidate_copy}

            if with_video_detail and video_url:
                detail_attempted += 1
                try:
                    detail_result = collect_video_detail(config, video_url=video_url)
                    item["detail_result"] = detail_result
                    detail_succeeded += 1
                except Exception as exc:  # pragma: no cover - runtime safety fallback
                    item["detail_error"] = f"{type(exc).__name__}: {exc}"
                    detail_failed += 1

            if with_comments and video_url:
                comments_attempted += 1
                try:
                    comments_result = collect_video_comments(
                        config,
                        video_url=video_url,
                        max_pages=comment_pages,
                    )
                    item["comments_result"] = comments_result
                    comments_succeeded += 1
                except Exception as exc:  # pragma: no cover - runtime safety fallback
                    item["comments_error"] = f"{type(exc).__name__}: {exc}"
                    comments_failed += 1

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
                "with_video_detail": with_video_detail,
                "with_comments": with_comments,
                "comment_pages": comment_pages,
                "max_items": max_items,
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

from __future__ import annotations

"""Batch execution helpers for the data-collection phase."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from time import monotonic
from typing import Any

from ..collector.homepage_collector import collect_homepage_videos


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


def _normalize_target(target: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(target, dict):
        raise TypeError("each target must be a dict")
    return dict(target)


def _extract_homepage_url(target: dict[str, Any]) -> str:
    homepage_url = str(target.get("homepage_url", "")).strip()
    if not homepage_url:
        raise ValueError("target.homepage_url must not be empty")
    return homepage_url


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

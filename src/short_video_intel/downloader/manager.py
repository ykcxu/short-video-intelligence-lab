from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
import json
from pathlib import Path
from typing import Any, Iterable

from .adapter_ytdlp import detect_ytdlp, run_ytdlp_download
from .browser_media import discover_media_urls
from .direct_http import download_via_http
from .downloader_stub import run_download_job as run_download_job_stub


def run_download_job_with_fallback(job: dict[str, Any]) -> dict[str, Any]:
    app_config = job.get("_app_config") if isinstance(job, dict) else None
    normalized_job = _normalize_job(job)
    if normalized_job is None:
        return _failure_result(
            downloader="manager",
            output_path="",
            error="job must be a dict with non-empty video_url and output_path",
            fallback_used=False,
        )

    ytdlp_result = run_ytdlp_download(
        normalized_job["video_url"],
        normalized_job["output_path"],
        cookies_source=normalized_job.get("cookies_source"),
    )
    if ytdlp_result.get("status") == "success":
        ytdlp_result["fallback_used"] = False
        ytdlp_result["artifact_path"] = _write_download_metadata(normalized_job, ytdlp_result)
        return _strip_runtime_fields(ytdlp_result)

    browser_media_result = _run_browser_media_fallback(normalized_job, config=app_config)
    if browser_media_result.get("status") == "success":
        browser_media_result["fallback_used"] = True
        browser_media_result["ytdlp_error"] = ytdlp_result.get("error")
        browser_media_result["artifact_path"] = _write_download_metadata(normalized_job, browser_media_result)
        return _strip_runtime_fields(browser_media_result)

    stub_job = dict(normalized_job)
    stub_job["downloader"] = "stub"
    stub_job["fallback_used"] = True
    stub_job["ytdlp_error"] = ytdlp_result.get("error")
    if ytdlp_result.get("cookies_file"):
        stub_job["cookies_file"] = ytdlp_result.get("cookies_file")
    if browser_media_result.get("probe"):
        stub_job["browser_media_probe"] = browser_media_result.get("probe")
    if browser_media_result.get("error"):
        stub_job["browser_media_error"] = browser_media_result.get("error")
    try:
        stub_result = run_download_job_stub(stub_job)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _failure_result(
            downloader="stub",
            output_path=normalized_job["output_path"],
            error=str(exc),
            fallback_used=True,
        )
    stub_result["fallback_used"] = True
    return _strip_runtime_fields(stub_result)


def run_download_jobs_with_fallback(
    jobs: Iterable[dict[str, Any]],
    *,
    max_workers: int = 1,
) -> list[dict[str, Any]]:
    normalized_jobs = list(jobs)
    if max_workers <= 1 or len(normalized_jobs) <= 1:
        return [run_download_job_with_fallback(job) for job in normalized_jobs]

    results: list[dict[str, Any] | None] = [None] * len(normalized_jobs)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(run_download_job_with_fallback, job): index
            for index, job in enumerate(normalized_jobs)
        }
        for future in as_completed(future_map):
            index = future_map[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # pragma: no cover - defensive fallback
                normalized = _normalize_job(normalized_jobs[index]) or {}
                results[index] = _failure_result(
                    downloader="manager",
                    output_path=str(normalized.get("output_path") or ""),
                    error=str(exc),
                    fallback_used=False,
                )
    return [result for result in results if isinstance(result, dict)]


def summarize_download_results(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    normalized_results = [result for result in results if isinstance(result, dict)]
    success_count = sum(1 for item in normalized_results if item.get("status") == "success")
    failed_count = len(normalized_results) - success_count
    fallback_count = sum(1 for item in normalized_results if bool(item.get("fallback_used")))
    total_file_size = sum(int(item.get("file_size") or 0) for item in normalized_results)
    ytdlp_error_count = sum(1 for item in normalized_results if str(item.get("ytdlp_error") or "").strip())
    fresh_cookies_required_count = sum(
        1
        for item in normalized_results
        if "fresh cookies" in str(item.get("ytdlp_error") or "").lower()
    )
    downloader_breakdown: dict[str, int] = {}
    error_samples: list[dict[str, Any]] = []
    for item in normalized_results:
        downloader = str(item.get("downloader") or "unknown")
        downloader_breakdown[downloader] = downloader_breakdown.get(downloader, 0) + 1
        error_text = str(item.get("error") or item.get("ytdlp_error") or "").strip()
        if error_text and len(error_samples) < 5:
            error_samples.append(
                {
                    "job_id": item.get("job_id"),
                    "video_url": item.get("video_url"),
                    "downloader": downloader,
                    "error": error_text,
                }
            )
    return {
        "jobs_total": len(normalized_results),
        "success_count": success_count,
        "failed_count": failed_count,
        "fallback_count": fallback_count,
        "total_file_size": total_file_size,
        "ytdlp_error_count": ytdlp_error_count,
        "fresh_cookies_required_count": fresh_cookies_required_count,
        "downloader_breakdown": downloader_breakdown,
        "error_samples": error_samples,
    }


def run_download_jobs_from_file(
    jobs_file: str | Path,
    *,
    max_workers: int = 1,
) -> dict[str, Any]:
    jobs_path = Path(jobs_file)
    payload = json.loads(jobs_path.read_text(encoding="utf-8"))
    jobs = list(payload.get("jobs") or []) if isinstance(payload, dict) else payload
    if not isinstance(jobs, list):
        raise ValueError("jobs_file must contain a JSON array or {'jobs': [...]} payload")

    results = run_download_jobs_with_fallback(jobs, max_workers=max_workers)
    summary = summarize_download_results(results)
    return {
        "jobs_file": str(jobs_path),
        "results": results,
        "summary": summary,
    }


def _write_download_metadata(job: dict[str, Any], result: dict[str, Any]) -> str:
    artifact_path_value = str(job.get("artifact_path") or "").strip()
    if not artifact_path_value:
        artifact_path_value = str(Path(str(job.get("output_path") or "")).with_suffix(".json"))
    artifact_path = Path(artifact_path_value)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "job": job,
        "result": result,
    }
    artifact_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_fallback),
        encoding="utf-8",
    )
    return str(artifact_path)


def _run_browser_media_fallback(job: dict[str, Any], *, config: Any) -> dict[str, Any]:
    if config is None:
        return {
            "status": "failed",
            "downloader": "browser_http",
            "output_path": str(job.get("output_path") or ""),
            "file_size": 0,
            "error": "app config unavailable for browser media fallback",
        }

    probe = discover_media_urls(
        config,
        str(job.get("video_url") or ""),
        expected_video_id=str(job.get("video_id") or ""),
    )
    candidates = [
        candidate
        for candidate in list(probe.get("candidates") or [])
        if isinstance(candidate, dict) and not bool(candidate.get("excluded"))
    ]
    if not candidates:
        return {
            "status": "failed",
            "downloader": "browser_http",
            "output_path": str(job.get("output_path") or ""),
            "file_size": 0,
            "error": "no usable media candidates discovered from browser probe",
            "probe": probe,
        }

    browser_cfg = getattr(config, "browser", None)
    user_agent = getattr(browser_cfg, "user_agent", None)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        media_url = str(candidate.get("url") or "").strip()
        if not media_url:
            continue
        direct_result = download_via_http(
            media_url,
            str(job.get("output_path") or ""),
            referer=str(job.get("video_url") or ""),
            user_agent=user_agent,
        )
        direct_result.update(
            {
                "job_id": job.get("job_id"),
                "video_url": job.get("video_url"),
                "video_id": job.get("video_id"),
                "source_name": job.get("source_name"),
                "homepage_url": job.get("homepage_url"),
                "origin_artifact_path": job.get("origin_artifact_path"),
                "origin_kind": job.get("origin_kind"),
            }
        )
        direct_result["probe"] = probe
        direct_result["media_candidate"] = candidate
        if direct_result.get("status") == "success":
            return direct_result
    return {
        "status": "failed",
        "downloader": "browser_http",
        "output_path": str(job.get("output_path") or ""),
        "file_size": 0,
        "error": "browser media candidates discovered but direct download failed",
        "probe": probe,
    }


def run_download_job(job: dict[str, Any]) -> dict[str, Any]:
    return run_download_job_with_fallback(job)


def run_download_jobs(jobs: Iterable[dict[str, Any]], *, max_workers: int = 1) -> list[dict[str, Any]]:
    return run_download_jobs_with_fallback(jobs, max_workers=max_workers)


def _normalize_job(job: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(job, dict):
        return None
    video_url = _normalize_text(job.get("video_url"))
    output_path = _normalize_text(job.get("output_path"))
    if not video_url or not output_path:
        return None
    normalized = dict(job)
    normalized["video_url"] = video_url
    normalized["output_path"] = str(Path(output_path))
    artifact_path = _normalize_text(job.get("artifact_path"))
    if artifact_path:
        normalized["artifact_path"] = str(Path(artifact_path))
    cookies_source = _normalize_text(job.get("cookies_source"))
    if cookies_source:
        normalized["cookies_source"] = str(Path(cookies_source))
    return normalized


def _failure_result(
    *,
    downloader: str,
    output_path: str,
    error: str,
    fallback_used: bool,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "downloader": downloader,
        "output_path": output_path,
        "file_size": 0,
        "error": error,
        "fallback_used": fallback_used,
    }


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _json_fallback(value: Any) -> str:
    return str(value)


def _strip_runtime_fields(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    sanitized.pop("_app_config", None)
    return sanitized


__all__ = [
    "detect_ytdlp",
    "run_ytdlp_download",
    "run_download_job_with_fallback",
    "run_download_jobs_with_fallback",
    "run_download_jobs_from_file",
    "summarize_download_results",
    "run_download_job",
    "run_download_jobs",
]

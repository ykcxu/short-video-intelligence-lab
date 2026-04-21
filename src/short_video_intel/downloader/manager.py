from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .adapter_ytdlp import detect_ytdlp, run_ytdlp_download
from .downloader_stub import run_download_job as run_download_job_stub
from .downloader_stub import run_download_jobs as run_download_jobs_stub


def run_download_job_with_fallback(job: dict[str, Any]) -> dict[str, Any]:
    normalized_job = _normalize_job(job)
    if normalized_job is None:
        return _failure_result(
            downloader="manager",
            output_path="",
            error="job must be a dict with non-empty video_url and output_path",
            fallback_used=False,
        )

    ytdlp_result = run_ytdlp_download(normalized_job["video_url"], normalized_job["output_path"])
    if ytdlp_result.get("status") == "success":
        ytdlp_result["fallback_used"] = False
        return ytdlp_result

    stub_job = dict(normalized_job)
    stub_job["downloader"] = "stub"
    stub_job["fallback_used"] = True
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
    return stub_result


def run_download_jobs_with_fallback(jobs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for job in jobs:
        results.append(run_download_job_with_fallback(job))
    return results


def run_download_job(job: dict[str, Any]) -> dict[str, Any]:
    return run_download_job_with_fallback(job)


def run_download_jobs(jobs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return run_download_jobs_with_fallback(jobs)


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


__all__ = [
    "detect_ytdlp",
    "run_ytdlp_download",
    "run_download_job_with_fallback",
    "run_download_jobs_with_fallback",
    "run_download_job",
    "run_download_jobs",
]

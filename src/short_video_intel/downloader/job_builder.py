from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_download_jobs(videos: list[dict[str, Any]], output_dir: str | Path) -> list[dict[str, Any]]:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    created_at = _now_iso()
    jobs: list[dict[str, Any]] = []
    for index, video in enumerate(videos):
        if not isinstance(video, dict):
            raise TypeError(f"videos[{index}] must be a dict, got {type(video).__name__}")

        video_url = _normalize_text(video.get("video_url"))
        if not video_url:
            raise ValueError(f"videos[{index}] is missing non-empty video_url")

        job_id = _build_job_id(video_url, created_at, index)
        output_path = output_root / f"{job_id}.json"

        job: dict[str, Any] = {
            "job_id": job_id,
            "video_url": video_url,
            "status": "pending",
            "downloader": "stub",
            "output_path": str(output_path),
            "created_at": created_at,
        }

        if "video_id" in video:
            job["video_id"] = _normalize_text(video.get("video_id"))
        if "title" in video:
            job["title"] = _normalize_text(video.get("title"))

        jobs.append(job)

    return jobs


def _build_job_id(video_url: str, created_at: str, index: int) -> str:
    payload = f"{video_url}|{created_at}|{index}".encode("utf-8")
    digest = hashlib.sha1(payload).hexdigest()[:16]
    return f"dl_{digest}"


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

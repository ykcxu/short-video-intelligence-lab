from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
import re
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
        source_name = _normalize_text(video.get("source_name")) or "unknown_source"
        source_slug = _slugify_filename(source_name)
        source_dir = output_root / source_slug
        source_dir.mkdir(parents=True, exist_ok=True)
        video_id = _normalize_text(video.get("video_id")) or _build_video_token(video_url)
        output_path = source_dir / f"{source_slug}_{video_id}.mp4"
        artifact_path = source_dir / f"{job_id}.json"

        job: dict[str, Any] = {
            "job_id": job_id,
            "video_url": video_url,
            "video_id": video_id,
            "status": "pending",
            "downloader": "stub",
            "output_path": str(output_path),
            "artifact_path": str(artifact_path),
            "source_name": source_name,
            "created_at": created_at,
        }

        if "title" in video:
            job["title"] = _normalize_text(video.get("title"))
        if "homepage_url" in video:
            job["homepage_url"] = _normalize_text(video.get("homepage_url"))
        if "origin_artifact_path" in video:
            job["origin_artifact_path"] = _normalize_text(video.get("origin_artifact_path"))
        if "origin_kind" in video:
            job["origin_kind"] = _normalize_text(video.get("origin_kind"))

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


def _slugify_filename(value: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return "unknown"
    slug = re.sub(r"[\\/:*?\"<>|]+", "_", normalized)
    slug = re.sub(r"\s+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("._ ")
    return slug[:80] or "unknown"


def _build_video_token(video_url: str) -> str:
    return hashlib.sha1(video_url.encode("utf-8")).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

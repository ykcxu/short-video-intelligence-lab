from __future__ import annotations

import ast
import json
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .models import HomepageTarget, Video, VideoSnapshot


def upsert_homepage_target(session: Session, target_dict: dict[str, Any]) -> HomepageTarget:
    """Insert or update a homepage target by homepage_url."""

    raw_target = dict(target_dict or {})
    normalized = _normalize_target_dict(raw_target)
    homepage_url = normalized["homepage_url"]

    target = (
        session.query(HomepageTarget)
        .filter(HomepageTarget.homepage_url == homepage_url)
        .one_or_none()
    )

    if target is None:
        target = HomepageTarget(
            platform=normalized.get("platform") or "douyin",
            homepage_url=homepage_url,
            source_name=normalized.get("source_name") or None,
            category_lv1=normalized.get("category_lv1") or None,
            category_lv2=normalized.get("category_lv2") or None,
            tags_json=normalized.get("tags_json") or [],
            status=normalized.get("status") or "active",
            notes=normalized.get("notes") or None,
        )
        session.add(target)
        session.flush()
        return target

    _assign_if_present(target, "platform", raw_target, default="douyin")
    _assign_if_present(target, "source_name", raw_target, allow_empty=False)
    _assign_if_present(target, "category_lv1", raw_target, allow_empty=False)
    _assign_if_present(target, "category_lv2", raw_target, allow_empty=False)
    if "tags_json" in raw_target:
        target.tags_json = normalized["tags_json"] or []
    if "status" in raw_target:
        _assign_if_present(target, "status", raw_target, default="active")
    if "notes" in raw_target:
        target.notes = _normalize_optional_text(raw_target.get("notes")) or None

    session.flush()
    return target


def upsert_video_from_candidate(
    session: Session,
    target_id: int,
    candidate: dict[str, Any],
    raw_json_path: str | None = None,
) -> Video:
    """Insert or update a video row from a collector candidate."""

    normalized = _normalize_video_candidate(candidate)
    candidate_video_url = normalized.get("video_url")
    candidate_video_id = normalized.get("video_id")

    video_url = candidate_video_url or _build_video_url(candidate_video_id)
    if not video_url:
        raise ValueError("candidate must provide video_url or video_id")

    video_id = candidate_video_id or _extract_video_id(video_url)
    if not video_id:
        raise ValueError("unable to resolve video_id from candidate")

    video = (
        session.query(Video)
        .filter(Video.video_url == video_url)
        .one_or_none()
    )
    if video is None:
        video = (
            session.query(Video)
            .filter(Video.target_id == target_id, Video.video_id == video_id)
            .one_or_none()
        )

    publish_at = _coerce_datetime(normalized.get("publish_at"))
    title = _normalize_optional_text(normalized.get("title")) or None

    if video is None:
        video = Video(
            platform=normalized.get("platform") or "douyin",
            target_id=target_id,
            video_id=video_id,
            video_url=video_url,
            title=title,
            description=_normalize_optional_text(normalized.get("description")) or None,
            publish_at=publish_at,
            author_name=_normalize_optional_text(normalized.get("author_name")) or None,
            cover_url=_normalize_optional_text(normalized.get("cover_url")) or None,
            raw_json_path=raw_json_path or _normalize_optional_text(normalized.get("raw_json_path")) or None,
        )
        session.add(video)
        session.flush()
        return video

    video.platform = normalized.get("platform") or video.platform or "douyin"
    video.target_id = target_id
    video.video_id = video_id
    video.video_url = video_url
    if "title" in normalized:
        video.title = title
    if "description" in normalized:
        video.description = _normalize_optional_text(normalized.get("description")) or None
    if "publish_at" in normalized:
        video.publish_at = publish_at
    if "author_name" in normalized:
        video.author_name = _normalize_optional_text(normalized.get("author_name")) or None
    if "cover_url" in normalized:
        video.cover_url = _normalize_optional_text(normalized.get("cover_url")) or None
    if raw_json_path is not None:
        video.raw_json_path = raw_json_path
    elif "raw_json_path" in normalized:
        video.raw_json_path = _normalize_optional_text(normalized.get("raw_json_path")) or None

    session.flush()
    return video


def insert_video_snapshot(
    session: Session,
    video_id_fk: int,
    metrics: dict[str, Any],
    capture_source: str = "collector_stub",
    raw_json_path: str | None = None,
) -> VideoSnapshot:
    """Insert a new snapshot row for a video."""

    normalized = dict(metrics or {})
    snapshot = VideoSnapshot(
        video_id_fk=video_id_fk,
        view_count=_coerce_int(normalized.get("view_count")),
        like_count=_coerce_int(normalized.get("like_count")),
        comment_count=_coerce_int(normalized.get("comment_count")),
        share_count=_coerce_int(normalized.get("share_count")),
        bookmark_count=_coerce_int(normalized.get("bookmark_count")),
        capture_source=_normalize_optional_text(normalized.get("capture_source")) or capture_source,
        is_estimated=bool(normalized.get("is_estimated", False)),
        raw_json_path=raw_json_path or _normalize_optional_text(normalized.get("raw_json_path")) or None,
    )
    if "snapshot_at" in normalized:
        snapshot.snapshot_at = _coerce_datetime(normalized.get("snapshot_at")) or snapshot.snapshot_at
    session.add(snapshot)
    session.flush()
    return snapshot


def persist_homepage_crawl_result(
    session: Session,
    target_dict: dict[str, Any],
    crawl_result: dict[str, Any],
    raw_json_path: str | None = None,
) -> dict[str, Any]:
    """Persist a homepage crawl result into the current ORM tables."""

    target_lookup_url = _normalize_optional_text((target_dict or {}).get("homepage_url"))
    existing_target = None
    if target_lookup_url:
        existing_target = (
            session.query(HomepageTarget)
            .filter(HomepageTarget.homepage_url == target_lookup_url)
            .one_or_none()
        )
    target = upsert_homepage_target(session, target_dict)
    videos = list((crawl_result or {}).get("videos") or [])

    created_videos = 0
    updated_videos = 0
    snapshot_count = 0
    persisted_video_ids: list[int] = []
    persisted_video_urls: list[str] = []

    for candidate in videos:
        existing_video = _find_video_before_upsert(session, target.id, candidate)
        video = upsert_video_from_candidate(
            session=session,
            target_id=target.id,
            candidate=_candidate_as_dict(candidate),
            raw_json_path=_extract_raw_json_path(candidate, raw_json_path),
        )
        if existing_video is None:
            created_videos += 1
        else:
            updated_videos += 1

        persisted_video_ids.append(video.id)
        persisted_video_urls.append(video.video_url)

        metrics = _extract_metrics(candidate)
        if metrics:
            insert_video_snapshot(
                session=session,
                video_id_fk=video.id,
                metrics=metrics,
                capture_source=_normalize_optional_text(
                    metrics.get("capture_source")
                )
                or _normalize_optional_text((crawl_result or {}).get("backend"))
                or "collector_stub",
                raw_json_path=_extract_raw_json_path(candidate, raw_json_path),
            )
            snapshot_count += 1

    summary = {
        "homepage_target_id": target.id,
        "homepage_url": target.homepage_url,
        "target_created": existing_target is None,
        "target_updated": existing_target is not None,
        "videos_seen": len(videos),
        "videos_created": created_videos,
        "videos_updated": updated_videos,
        "snapshots_inserted": snapshot_count,
        "persisted_video_ids": persisted_video_ids,
        "persisted_video_urls": persisted_video_urls,
        "backend": (crawl_result or {}).get("backend"),
        "scanned_at": (crawl_result or {}).get("scanned_at"),
        "warnings": list((crawl_result or {}).get("warnings") or []),
        "raw_json_path": raw_json_path,
    }
    return summary


def _normalize_target_dict(target_dict: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(target_dict or {})
    homepage_url = _normalize_optional_text(normalized.get("homepage_url"))
    if not homepage_url:
        raise ValueError("target_dict must include homepage_url")
    normalized["homepage_url"] = homepage_url
    normalized["platform"] = _normalize_optional_text(normalized.get("platform")) or "douyin"
    normalized["source_name"] = _normalize_optional_text(normalized.get("source_name")) or None
    normalized["category_lv1"] = _normalize_optional_text(normalized.get("category_lv1")) or None
    normalized["category_lv2"] = _normalize_optional_text(normalized.get("category_lv2")) or None
    normalized["status"] = _normalize_optional_text(normalized.get("status")) or None
    normalized["notes"] = _normalize_optional_text(normalized.get("notes")) or None
    normalized["tags_json"] = _coerce_tags_json(normalized.get("tags_json"), normalized)
    return normalized


def _normalize_video_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(candidate or {})
    if "video_url" in normalized:
        normalized["video_url"] = _normalize_optional_text(normalized.get("video_url")) or None
    if "video_id" in normalized:
        normalized["video_id"] = _normalize_optional_text(normalized.get("video_id")) or None
    if "title" in normalized:
        normalized["title"] = _normalize_optional_text(normalized.get("title")) or None
    if "description" in normalized:
        normalized["description"] = _normalize_optional_text(normalized.get("description")) or None
    if "author_name" in normalized:
        normalized["author_name"] = _normalize_optional_text(normalized.get("author_name")) or None
    if "cover_url" in normalized:
        normalized["cover_url"] = _normalize_optional_text(normalized.get("cover_url")) or None
    if "platform" in normalized:
        normalized["platform"] = _normalize_optional_text(normalized.get("platform")) or None
    if "publish_at" in normalized:
        normalized["publish_at"] = normalized.get("publish_at")
    return normalized


def _assign_if_present(
    target: HomepageTarget,
    field_name: str,
    normalized: dict[str, Any],
    *,
    default: str | None = None,
    allow_empty: bool = True,
) -> None:
    if field_name not in normalized:
        return
    value = _normalize_optional_text(normalized.get(field_name))
    if value:
        setattr(target, field_name, value)
    elif allow_empty:
        setattr(target, field_name, default)


def _find_video_before_upsert(
    session: Session,
    target_id: int,
    candidate: dict[str, Any] | Any,
) -> Video | None:
    normalized = _candidate_as_dict(candidate)
    video_url = _normalize_optional_text(normalized.get("video_url"))
    video_id = _normalize_optional_text(normalized.get("video_id"))

    if video_url:
        existing = session.query(Video).filter(Video.video_url == video_url).one_or_none()
        if existing is not None:
            return existing

    if video_id:
        return (
            session.query(Video)
            .filter(Video.target_id == target_id, Video.video_id == video_id)
            .one_or_none()
        )
    return None


def _extract_metrics(candidate: dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        metrics = candidate.get("metrics")
        if isinstance(metrics, dict):
            return dict(metrics)
        snapshot = candidate.get("snapshot")
        if isinstance(snapshot, dict):
            return dict(snapshot)
        payload = {}
        for key in (
            "view_count",
            "like_count",
            "comment_count",
            "share_count",
            "bookmark_count",
            "capture_source",
            "is_estimated",
            "snapshot_at",
        ):
            if key in candidate:
                payload[key] = candidate.get(key)
        return payload
    return {}


def _candidate_as_dict(candidate: dict[str, Any] | Any) -> dict[str, Any]:
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
    raise TypeError(f"unsupported candidate type: {type(candidate).__name__}")


def _extract_raw_json_path(candidate: dict[str, Any] | Any, fallback: str | None) -> str | None:
    if isinstance(candidate, dict):
        value = candidate.get("raw_json_path")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _normalize_optional_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_tags_json(value: Any, normalized: dict[str, Any]) -> list[str]:
    if value is None:
        return _default_tags_from_target(normalized)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, (tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return _default_tags_from_target(normalized)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                if "," in text:
                    parsed = [part.strip().strip("\"'") for part in text.strip("[]").split(",") if part.strip()]
                else:
                    parsed = [text]
        return _coerce_tags_json(parsed, normalized)
    return _default_tags_from_target(normalized)


def _default_tags_from_target(normalized: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for key in ("platform", "category_lv1", "category_lv2"):
        value = _normalize_optional_text(normalized.get(key))
        if value:
            tags.append(f"{key}:{value}")
    source_name = _normalize_optional_text(normalized.get("source_name"))
    if source_name:
        tags.append(f"source:{source_name}")
    return tags


def _build_video_url(video_id: Any) -> str | None:
    video_id_text = _normalize_optional_text(video_id)
    if not video_id_text:
        return None
    return f"https://www.douyin.com/video/{video_id_text}"


def _extract_video_id(video_url: str) -> str | None:
    marker = "/video/"
    if marker not in video_url:
        return None
    tail = video_url.split(marker, 1)[1]
    if not tail:
        return None
    return tail.split("?", 1)[0].split("/", 1)[0].strip() or None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

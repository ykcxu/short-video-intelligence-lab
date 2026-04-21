from __future__ import annotations

import ast
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .models import Comment, CommentReply, HomepageTarget, Video, VideoSnapshot


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


def upsert_comment(
    session: Session,
    video_id_fk: int,
    comment_dict: dict[str, Any],
    raw_json_path: str | None = None,
) -> Comment:
    """Insert or update a comment row with platform-id-first deduping."""

    source_comment = _candidate_as_dict(comment_dict)
    source_keys = set(source_comment.keys())
    normalized = _normalize_comment_payload(source_comment)
    comment_platform_id = normalized.get("comment_platform_id")
    unique_hash = _build_comment_unique_hash(video_id_fk, normalized)

    comment = None
    if comment_platform_id:
        comment = (
            session.query(Comment)
            .filter(
                Comment.video_id_fk == video_id_fk,
                Comment.comment_platform_id == comment_platform_id,
            )
            .one_or_none()
        )
    if comment is None:
        comment = (
            session.query(Comment)
            .filter(
                Comment.video_id_fk == video_id_fk,
                Comment.unique_hash == unique_hash,
            )
            .one_or_none()
        )

    comment_at = _coerce_datetime(
        normalized.get("comment_at")
        or normalized.get("created_at")
        or normalized.get("updated_at")
    )
    content = _normalize_optional_text(normalized.get("content")) or ""
    like_count = _coerce_int(normalized.get("like_count"))
    reply_count = _coerce_int(normalized.get("reply_count"))
    is_author = _coerce_bool(normalized.get("is_author"))
    comment_raw_json_path = raw_json_path or _normalize_optional_text(normalized.get("raw_json_path")) or None

    if comment is None:
        comment = Comment(
            video_id_fk=video_id_fk,
            comment_platform_id=comment_platform_id or None,
            user_id=_normalize_optional_text(normalized.get("user_id")) or None,
            nickname=_normalize_optional_text(normalized.get("nickname")) or None,
            content=content,
            like_count=like_count,
            reply_count=reply_count,
            comment_at=comment_at,
            is_author=is_author,
            raw_json_path=comment_raw_json_path,
            unique_hash=unique_hash,
        )
        session.add(comment)
        session.flush()
        return comment

    comment.video_id_fk = video_id_fk
    if {"comment_platform_id", "comment_id", "platform_id"} & source_keys:
        comment.comment_platform_id = comment_platform_id or None
    if {"user_id", "author_id"} & source_keys:
        comment.user_id = _normalize_optional_text(normalized.get("user_id")) or None
    if {"nickname", "author_name"} & source_keys:
        comment.nickname = _normalize_optional_text(normalized.get("nickname")) or None
    if {"content", "text"} & source_keys:
        comment.content = content
    if {"like_count", "likes"} & source_keys:
        comment.like_count = like_count
    if {"reply_count", "replies_count"} & source_keys:
        comment.reply_count = reply_count
    if {"comment_at", "created_at", "updated_at"} & source_keys:
        comment.comment_at = comment_at
    if {"is_author", "author"} & source_keys:
        comment.is_author = is_author
    if raw_json_path is not None:
        comment.raw_json_path = raw_json_path
    elif "raw_json_path" in source_keys:
        comment.raw_json_path = _normalize_optional_text(normalized.get("raw_json_path")) or None
    comment.unique_hash = unique_hash

    session.flush()
    return comment


def upsert_comment_reply(
    session: Session,
    comment_id_fk: int,
    reply_dict: dict[str, Any],
    raw_json_path: str | None = None,
) -> CommentReply:
    """Insert or update a comment reply row with platform-id-first deduping."""

    source_reply = _candidate_as_dict(reply_dict)
    source_keys = set(source_reply.keys())
    normalized = _normalize_reply_payload(source_reply)
    reply_platform_id = normalized.get("reply_platform_id")
    unique_hash = _build_reply_unique_hash(comment_id_fk, normalized)

    reply = None
    if reply_platform_id:
        reply = (
            session.query(CommentReply)
            .filter(
                CommentReply.comment_id_fk == comment_id_fk,
                CommentReply.reply_platform_id == reply_platform_id,
            )
            .one_or_none()
        )
    if reply is None:
        reply = (
            session.query(CommentReply)
            .filter(
                CommentReply.comment_id_fk == comment_id_fk,
                CommentReply.unique_hash == unique_hash,
            )
            .one_or_none()
        )

    reply_at = _coerce_datetime(
        normalized.get("reply_at")
        or normalized.get("created_at")
        or normalized.get("updated_at")
    )
    content = _normalize_optional_text(normalized.get("content")) or ""
    like_count = _coerce_int(normalized.get("like_count"))
    reply_raw_json_path = raw_json_path or _normalize_optional_text(normalized.get("raw_json_path")) or None

    if reply is None:
        reply = CommentReply(
            comment_id_fk=comment_id_fk,
            reply_platform_id=reply_platform_id or None,
            user_id=_normalize_optional_text(normalized.get("user_id")) or None,
            nickname=_normalize_optional_text(normalized.get("nickname")) or None,
            content=content,
            like_count=like_count,
            reply_at=reply_at,
            raw_json_path=reply_raw_json_path,
            unique_hash=unique_hash,
        )
        session.add(reply)
        session.flush()
        return reply

    reply.comment_id_fk = comment_id_fk
    if {"reply_platform_id", "reply_id", "platform_id"} & source_keys:
        reply.reply_platform_id = reply_platform_id or None
    if {"user_id", "author_id"} & source_keys:
        reply.user_id = _normalize_optional_text(normalized.get("user_id")) or None
    if {"nickname", "author_name"} & source_keys:
        reply.nickname = _normalize_optional_text(normalized.get("nickname")) or None
    if {"content", "text"} & source_keys:
        reply.content = content
    if {"like_count", "likes"} & source_keys:
        reply.like_count = like_count
    if {"reply_at", "created_at", "updated_at"} & source_keys:
        reply.reply_at = reply_at
    if raw_json_path is not None:
        reply.raw_json_path = raw_json_path
    elif "raw_json_path" in source_keys:
        reply.raw_json_path = _normalize_optional_text(normalized.get("raw_json_path")) or None
    reply.unique_hash = unique_hash

    session.flush()
    return reply


def persist_video_comments_result(
    session: Session,
    video_id_fk: int,
    comments_result: dict[str, Any],
    raw_json_path: str | None = None,
) -> dict[str, Any]:
    """Persist comments and replies for a video into the ORM tables."""

    normalized_result = dict(comments_result or {})
    comments_payload = _extract_comment_records(normalized_result.get("comments"))
    replies_payload = _extract_reply_records(normalized_result.get("replies"))

    # Also accept nested replies under comments for future compatibility.
    for comment_record in comments_payload:
        nested_replies = comment_record.pop("replies", None)
        if isinstance(nested_replies, list):
            replies_payload.extend(_extract_reply_records(nested_replies))

    comments_seen = 0
    comments_created = 0
    comments_updated = 0
    replies_seen = 0
    replies_created = 0
    replies_updated = 0
    persisted_comment_ids: list[int] = []
    persisted_reply_ids: list[int] = []
    warnings = list((normalized_result.get("scan_meta") or {}).get("warnings") or [])

    comment_lookup: dict[str, Comment] = {}

    for comment_record in comments_payload:
        identity = _comment_identity(video_id_fk, comment_record)
        if not identity or identity in comment_lookup:
            continue

        existing_comment = _find_comment_before_upsert(session, video_id_fk, comment_record)
        comment = upsert_comment(
            session=session,
            video_id_fk=video_id_fk,
            comment_dict=comment_record,
            raw_json_path=_extract_raw_json_path(comment_record, raw_json_path),
        )
        comments_seen += 1
        if existing_comment is None:
            comments_created += 1
        else:
            comments_updated += 1

        persisted_comment_ids.append(comment.id)
        _register_comment_lookup(comment_lookup, comment_record, comment)

    for reply_record in replies_payload:
        parent_comment = _resolve_parent_comment(session, video_id_fk, comment_lookup, reply_record)
        if parent_comment is None:
            warnings.append("reply skipped because parent comment could not be resolved")
            continue

        identity = _reply_identity(parent_comment.id, reply_record)
        if not identity:
            continue

        existing_reply = _find_reply_before_upsert(session, parent_comment.id, reply_record)
        reply = upsert_comment_reply(
            session=session,
            comment_id_fk=parent_comment.id,
            reply_dict=reply_record,
            raw_json_path=_extract_raw_json_path(reply_record, raw_json_path),
        )
        replies_seen += 1
        if existing_reply is None:
            replies_created += 1
        else:
            replies_updated += 1

        persisted_reply_ids.append(reply.id)

    scan_meta = dict(normalized_result.get("scan_meta") or {})
    summary = {
        "video_id_fk": video_id_fk,
        "comments_seen": comments_seen,
        "comments_created": comments_created,
        "comments_updated": comments_updated,
        "replies_seen": replies_seen,
        "replies_created": replies_created,
        "replies_updated": replies_updated,
        "persisted_comment_ids": persisted_comment_ids,
        "persisted_reply_ids": persisted_reply_ids,
        "backend": scan_meta.get("backend"),
        "scanned_at": normalized_result.get("collected_at"),
        "is_incomplete": bool(scan_meta.get("is_incomplete", True)),
        "stop_reason": scan_meta.get("stop_reason"),
        "stop_reason_detail": scan_meta.get("stop_reason_detail"),
        "warnings": warnings,
        "raw_json_path": raw_json_path,
        "scan_meta": scan_meta,
    }
    return summary


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


def _extract_comment_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    records: list[dict[str, Any]] = []
    for item in value:
        try:
            records.append(_candidate_as_dict(item))
        except Exception:
            continue
    return records


def _extract_reply_records(value: Any) -> list[dict[str, Any]]:
    return _extract_comment_records(value)


def _normalize_comment_payload(comment_dict: dict[str, Any] | Any) -> dict[str, Any]:
    normalized = _candidate_as_dict(comment_dict)
    if "comment_platform_id" not in normalized:
        normalized["comment_platform_id"] = _normalize_optional_text(
            normalized.get("comment_id") or normalized.get("platform_id")
        )
    else:
        normalized["comment_platform_id"] = _normalize_optional_text(normalized.get("comment_platform_id"))
    if "user_id" not in normalized:
        normalized["user_id"] = _normalize_optional_text(normalized.get("author_id"))
    else:
        normalized["user_id"] = _normalize_optional_text(normalized.get("user_id"))
    if "nickname" not in normalized:
        normalized["nickname"] = _normalize_optional_text(normalized.get("author_name"))
    else:
        normalized["nickname"] = _normalize_optional_text(normalized.get("nickname"))
    if "content" not in normalized:
        normalized["content"] = _normalize_optional_text(normalized.get("text"))
    else:
        normalized["content"] = _normalize_optional_text(normalized.get("content"))
    if "like_count" in normalized:
        normalized["like_count"] = _coerce_int(normalized.get("like_count"))
    elif "likes" in normalized:
        normalized["like_count"] = _coerce_int(normalized.get("likes"))
    if "reply_count" in normalized:
        normalized["reply_count"] = _coerce_int(normalized.get("reply_count"))
    elif "replies_count" in normalized:
        normalized["reply_count"] = _coerce_int(normalized.get("replies_count"))
    if "comment_at" not in normalized:
        normalized["comment_at"] = normalized.get("created_at") or normalized.get("updated_at")
    if "is_author" in normalized:
        normalized["is_author"] = _coerce_bool(normalized.get("is_author"))
    elif "author" in normalized:
        normalized["is_author"] = _coerce_bool(normalized.get("author"))
    if "raw_json_path" in normalized:
        normalized["raw_json_path"] = _normalize_optional_text(normalized.get("raw_json_path"))
    if "unique_hash" in normalized:
        normalized["unique_hash"] = _normalize_optional_text(normalized.get("unique_hash"))
    return normalized


def _normalize_reply_payload(reply_dict: dict[str, Any] | Any) -> dict[str, Any]:
    normalized = _candidate_as_dict(reply_dict)
    if "reply_platform_id" not in normalized:
        normalized["reply_platform_id"] = _normalize_optional_text(
            normalized.get("reply_id") or normalized.get("platform_id")
        )
    else:
        normalized["reply_platform_id"] = _normalize_optional_text(normalized.get("reply_platform_id"))
    if "user_id" not in normalized:
        normalized["user_id"] = _normalize_optional_text(normalized.get("author_id"))
    else:
        normalized["user_id"] = _normalize_optional_text(normalized.get("user_id"))
    if "nickname" not in normalized:
        normalized["nickname"] = _normalize_optional_text(normalized.get("author_name"))
    else:
        normalized["nickname"] = _normalize_optional_text(normalized.get("nickname"))
    if "content" not in normalized:
        normalized["content"] = _normalize_optional_text(normalized.get("text"))
    else:
        normalized["content"] = _normalize_optional_text(normalized.get("content"))
    if "like_count" in normalized:
        normalized["like_count"] = _coerce_int(normalized.get("like_count"))
    elif "likes" in normalized:
        normalized["like_count"] = _coerce_int(normalized.get("likes"))
    if "reply_at" not in normalized:
        normalized["reply_at"] = normalized.get("created_at") or normalized.get("updated_at")
    if "raw_json_path" in normalized:
        normalized["raw_json_path"] = _normalize_optional_text(normalized.get("raw_json_path"))
    if "unique_hash" in normalized:
        normalized["unique_hash"] = _normalize_optional_text(normalized.get("unique_hash"))
    return normalized


def _comment_identity(video_id_fk: int, comment_record: dict[str, Any]) -> str:
    platform_id = _normalize_optional_text(
        comment_record.get("comment_platform_id") or comment_record.get("comment_id")
    )
    if platform_id:
        return f"platform:{platform_id}"
    unique_hash = _normalize_optional_text(comment_record.get("unique_hash"))
    if not unique_hash:
        unique_hash = _build_comment_unique_hash(video_id_fk, _normalize_comment_payload(comment_record))
    return f"hash:{unique_hash}" if unique_hash else ""


def _reply_identity(comment_id_fk: int, reply_record: dict[str, Any]) -> str:
    platform_id = _normalize_optional_text(
        reply_record.get("reply_platform_id") or reply_record.get("reply_id")
    )
    if platform_id:
        return f"platform:{platform_id}"
    unique_hash = _normalize_optional_text(reply_record.get("unique_hash"))
    if not unique_hash:
        unique_hash = _build_reply_unique_hash(comment_id_fk, _normalize_reply_payload(reply_record))
    return f"hash:{unique_hash}" if unique_hash else ""


def _register_comment_lookup(
    comment_lookup: dict[str, Comment],
    comment_record: dict[str, Any],
    comment: Comment,
) -> None:
    for key in {
        _normalize_optional_text(comment.comment_platform_id),
        _normalize_optional_text(comment.unique_hash),
        _normalize_optional_text(comment_record.get("comment_id")),
        _normalize_optional_text(comment_record.get("comment_platform_id")),
        _normalize_optional_text(comment_record.get("unique_hash")),
    }:
        if key:
            comment_lookup[key] = comment


def _resolve_parent_comment(
    session: Session,
    video_id_fk: int,
    comment_lookup: dict[str, Comment],
    reply_record: dict[str, Any],
) -> Comment | None:
    candidate_keys = (
        _normalize_optional_text(reply_record.get("comment_id")),
        _normalize_optional_text(reply_record.get("comment_platform_id")),
        _normalize_optional_text(reply_record.get("parent_comment_id")),
        _normalize_optional_text(reply_record.get("parent_comment_platform_id")),
        _normalize_optional_text(reply_record.get("comment_unique_hash")),
        _normalize_optional_text(reply_record.get("parent_comment_unique_hash")),
    )
    for key in candidate_keys:
        if key and key in comment_lookup:
            return comment_lookup[key]

    platform_id = _normalize_optional_text(
        reply_record.get("comment_platform_id") or reply_record.get("comment_id")
    )
    if platform_id:
        existing = (
            session.query(Comment)
            .filter(
                Comment.video_id_fk == video_id_fk,
                Comment.comment_platform_id == platform_id,
            )
            .one_or_none()
        )
        if existing is not None:
            return existing

    unique_hash = _normalize_optional_text(reply_record.get("comment_unique_hash"))
    if unique_hash:
        existing = (
            session.query(Comment)
            .filter(
                Comment.video_id_fk == video_id_fk,
                Comment.unique_hash == unique_hash,
            )
            .one_or_none()
        )
        if existing is not None:
            return existing

    return None


def _find_comment_before_upsert(
    session: Session,
    video_id_fk: int,
    comment_dict: dict[str, Any] | Any,
) -> Comment | None:
    normalized = _normalize_comment_payload(comment_dict)
    platform_id = normalized.get("comment_platform_id")
    unique_hash = _build_comment_unique_hash(video_id_fk, normalized)

    if platform_id:
        existing = (
            session.query(Comment)
            .filter(
                Comment.video_id_fk == video_id_fk,
                Comment.comment_platform_id == platform_id,
            )
            .one_or_none()
        )
        if existing is not None:
            return existing

    return (
        session.query(Comment)
        .filter(
            Comment.video_id_fk == video_id_fk,
            Comment.unique_hash == unique_hash,
        )
        .one_or_none()
    )


def _find_reply_before_upsert(
    session: Session,
    comment_id_fk: int,
    reply_dict: dict[str, Any] | Any,
) -> CommentReply | None:
    normalized = _normalize_reply_payload(reply_dict)
    platform_id = normalized.get("reply_platform_id")
    unique_hash = _build_reply_unique_hash(comment_id_fk, normalized)

    if platform_id:
        existing = (
            session.query(CommentReply)
            .filter(
                CommentReply.comment_id_fk == comment_id_fk,
                CommentReply.reply_platform_id == platform_id,
            )
            .one_or_none()
        )
        if existing is not None:
            return existing

    return (
        session.query(CommentReply)
        .filter(
            CommentReply.comment_id_fk == comment_id_fk,
            CommentReply.unique_hash == unique_hash,
        )
        .one_or_none()
    )


def _build_comment_unique_hash(video_id_fk: int, normalized: dict[str, Any]) -> str:
    explicit_hash = _normalize_optional_text(normalized.get("unique_hash"))
    if explicit_hash:
        return explicit_hash
    payload = "|".join(
        [
            "comment",
            str(video_id_fk),
            _normalize_optional_text(normalized.get("comment_platform_id")),
            _normalize_optional_text(normalized.get("content")),
            _normalize_optional_text(normalized.get("user_id")),
            _normalize_optional_text(normalized.get("nickname")),
            _datetime_hash_component(normalized.get("comment_at")),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_reply_unique_hash(comment_id_fk: int, normalized: dict[str, Any]) -> str:
    explicit_hash = _normalize_optional_text(normalized.get("unique_hash"))
    if explicit_hash:
        return explicit_hash
    payload = "|".join(
        [
            "reply",
            str(comment_id_fk),
            _normalize_optional_text(normalized.get("reply_platform_id")),
            _normalize_optional_text(normalized.get("content")),
            _normalize_optional_text(normalized.get("user_id")),
            _normalize_optional_text(normalized.get("nickname")),
            _datetime_hash_component(normalized.get("reply_at")),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _datetime_hash_component(value: Any) -> str:
    dt = _coerce_datetime(value)
    return dt.isoformat() if dt is not None else ""


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


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

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .targets_loader import load_targets_from_path as _load_targets_from_path


def load_targets_from_file(
    path: str | Path,
    input_format: str = "auto",
) -> list[dict[str, Any]]:
    """Load homepage targets from a file and normalize them to a shared schema.

    This function is a thin wrapper around the existing ``targets_loader`` module.
    It keeps file-based ingestion compatible with the database-backed ingestion
    path by returning the same core fields for every record.

    Args:
        path: File path to a CSV, TSV, JSON, or JSONL target list.
        input_format: Explicit input format hint. Use ``"auto"`` to infer from
            file suffix.

    Returns:
        A list of normalized target dictionaries.
    """

    records = _load_targets_from_path(path, input_format=input_format)
    return [_normalize_target_record(record, default_status="active") for record in records]


def load_targets_from_db(
    database_url: str,
    status: str = "active",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Load homepage targets from the database and normalize them to a shared schema.

    Args:
        database_url: SQLAlchemy database URL for the existing target store.
        status: Optional status filter; only rows with an exact status match are
            returned.
        limit: Optional maximum number of rows to return. ``None`` means no limit.

    Returns:
        A list of normalized target dictionaries.
    """

    if limit is not None and limit < 0:
        raise ValueError("limit must be None or a non-negative integer")
    if limit == 0:
        return []

    select, get_session, HomepageTarget = _load_db_dependencies()

    stmt = select(HomepageTarget).order_by(HomepageTarget.id.asc())
    if status:
        stmt = stmt.where(HomepageTarget.status == status)
    if limit is not None:
        stmt = stmt.limit(limit)

    with get_session(database_url) as session:
        rows = session.scalars(stmt).all()

    return [_normalize_db_target(row) for row in rows]


def _normalize_db_target(target: HomepageTarget) -> dict[str, Any]:
    """Convert a ``HomepageTarget`` ORM row into the shared target schema."""

    normalized = {
        "homepage_url": _normalize_text(target.homepage_url),
        "source_name": _normalize_text(target.source_name),
        "category_lv1": _normalize_text(target.category_lv1),
        "category_lv2": _normalize_text(target.category_lv2),
        "tags_json": _normalize_tags_json(target.tags_json),
        "status": _normalize_text(target.status) or "active",
        "platform": _normalize_text(target.platform) or "douyin",
    }

    return normalized


def _normalize_target_record(
    record: dict[str, Any],
    *,
    default_status: str = "active",
) -> dict[str, Any]:
    """Normalize an arbitrary target record into the shared target schema."""

    normalized = {
        "homepage_url": _normalize_text(record.get("homepage_url")),
        "source_name": _normalize_text(record.get("source_name")),
        "category_lv1": _normalize_text(record.get("category_lv1")),
        "category_lv2": _normalize_text(record.get("category_lv2")),
        "tags_json": _normalize_tags_json(record.get("tags_json")),
        "status": _normalize_text(record.get("status")) or default_status,
    }

    platform = _normalize_text(record.get("platform"))
    if platform:
        normalized["platform"] = platform

    return normalized


def _normalize_text(value: Any) -> str:
    """Convert any value into a trimmed string."""

    if value is None:
        return ""
    return str(value).strip()


def _normalize_tags_json(value: Any) -> str:
    """Normalize tags into a canonical JSON string representation."""

    if value is None:
        return "[]"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "[]"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return json.dumps([text], ensure_ascii=False)
        return json.dumps(parsed, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _load_db_dependencies() -> tuple[Any, Any, Any]:
    """Import DB dependencies lazily so file-based loading stays usable without SQLAlchemy."""

    try:
        from sqlalchemy import select as sqlalchemy_select
        from ..db.engine import get_session as db_get_session
        from ..db.models import HomepageTarget as db_homepage_target
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised when optional deps are missing
        raise RuntimeError(
            "database target loading requires SQLAlchemy and the DB models to be available"
        ) from exc

    return sqlalchemy_select, db_get_session, db_homepage_target

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DETAIL_GLOB = "video_detail_*.json"
COMMENTS_GLOB = "video_comments_*.json"
MP4_GLOB = "*.mp4"
CSV_NAMES = ("accounts.csv", "videos.csv", "video_metrics.csv", "comments.csv")
TABLE_SCHEMAS: dict[str, str] = {
    "accounts": """CREATE TABLE IF NOT EXISTS accounts (
        account_id TEXT PRIMARY KEY,
        account_name TEXT NOT NULL,
        source TEXT NOT NULL
    )""",
    "videos": """CREATE TABLE IF NOT EXISTS videos (
        video_id TEXT PRIMARY KEY,
        video_url TEXT NOT NULL,
        account_id TEXT,
        title TEXT,
        detail_artifact_path TEXT,
        comments_artifact_path TEXT,
        mp4_path TEXT,
        detail_collected_at TEXT,
        comments_collected_at TEXT
    )""",
    "video_metrics": """CREATE TABLE IF NOT EXISTS video_metrics (
        video_id TEXT PRIMARY KEY,
        view_count INTEGER NOT NULL,
        like_count INTEGER NOT NULL,
        comment_count INTEGER NOT NULL,
        share_count INTEGER NOT NULL,
        metrics_artifact_path TEXT,
        collected_at TEXT
    )""",
    "comments": """CREATE TABLE IF NOT EXISTS comments (
        comment_id TEXT PRIMARY KEY,
        video_id TEXT NOT NULL,
        text TEXT,
        create_time TEXT,
        digg_count INTEGER NOT NULL,
        user_id TEXT,
        user_name TEXT,
        artifact_path TEXT
    )""",
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """统一构建分析数据集，输出 CSV 并按需导出 SQLite。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    output_dir = args.output_dir.resolve()
    detail_root = workspace / "artifacts" / "collector" / "video"
    comments_root = workspace / "artifacts" / "collector" / "comments"
    mp4_root = workspace / "downloads" / "artifact"
    warnings: list[str] = []

    detail_index = _scan_detail_artifacts(detail_root, warnings)
    comments_index, comment_rows_by_video = _scan_comment_artifacts(comments_root, warnings)
    media_index = _scan_mp4_artifacts(mp4_root)
    selected_video_ids = _select_video_ids(detail_index, comments_index, media_index, args.limit)
    account_rows, video_rows, metric_rows, comment_rows = _build_rows(
        selected_video_ids, detail_index, comments_index, comment_rows_by_video, media_index
    )
    _write_csvs(output_dir, account_rows, video_rows, metric_rows, comment_rows)

    sqlite_path = _resolve_sqlite_path(args.sqlite, workspace, output_dir)
    if sqlite_path is not None:
        _write_sqlite(sqlite_path, account_rows, video_rows, metric_rows, comment_rows)

    for warning in warnings:
        _warn(warning)
    payload = {
        "ok": True,
        "workspace": str(workspace),
        "output_dir": str(output_dir),
        "sqlite_path": str(sqlite_path) if sqlite_path else None,
        "videos_scanned": len(selected_video_ids),
        "accounts_count": len(account_rows),
        "videos_count": len(video_rows),
        "video_metrics_count": len(metric_rows),
        "comments_count": len(comment_rows),
        "warning_count": len(warnings),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="构建统一分析数据集（CSV/SQLite）。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "processed",
        help="CSV 输出目录，默认 data/processed。",
    )
    parser.add_argument(
        "--sqlite",
        nargs="?",
        const="analysis_dataset.sqlite",
        default=None,
        help="可选 SQLite 输出文件；不传则不导出，传空值时默认 analysis_dataset.sqlite。",
    )
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少个视频。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _scan_detail_artifacts(detail_root: Path, warnings: list[str]) -> dict[str, dict[str, Any]]:
    """扫描视频详情产物并按视频 ID 建索引。"""
    if not detail_root.exists():
        return {}
    detail_index: dict[str, dict[str, Any]] = {}
    for path in sorted(detail_root.glob(DETAIL_GLOB)):
        payload = _load_json(path, warnings)
        if payload is None:
            continue
        video_id = _extract_video_id(payload, path.stem.removeprefix("video_detail_"))
        if not video_id:
            warnings.append(f"详情文件缺少 video_id，已跳过：{path}")
            continue
        detail_index[video_id] = {"payload": payload, "path": path}
    return detail_index


def _scan_comment_artifacts(
    comments_root: Path,
    warnings: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """扫描评论产物，构建视频级索引和评论明细行。"""
    if not comments_root.exists():
        return {}, {}
    comments_index: dict[str, dict[str, Any]] = {}
    rows_by_video: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(comments_root.glob(COMMENTS_GLOB)):
        payload = _load_json(path, warnings)
        if payload is None:
            continue
        video_id = _extract_video_id(payload, path.stem.removeprefix("video_comments_"))
        if not video_id:
            warnings.append(f"评论文件缺少 video_id，已跳过：{path}")
            continue
        comments_index[video_id] = {"payload": payload, "path": path}
        rows_by_video[video_id] = _extract_comment_rows(video_id, payload, path)
    return comments_index, rows_by_video


def _scan_mp4_artifacts(mp4_root: Path) -> dict[str, dict[str, str]]:
    """扫描下载的 mp4 产物，按视频 ID 关联账号目录与文件路径。"""
    if not mp4_root.exists():
        return {}
    media_index: dict[str, dict[str, str]] = {}
    for path in sorted(mp4_root.rglob(MP4_GLOB)):
        video_id = _extract_video_id_from_text(path.stem)
        if not video_id:
            continue
        media_index[video_id] = {"mp4_path": str(path), "account_name": path.parent.name.strip() or "unknown"}
    return media_index


def _select_video_ids(
    detail_index: dict[str, dict[str, Any]],
    comments_index: dict[str, dict[str, Any]],
    media_index: dict[str, dict[str, str]],
    limit: int | None,
) -> list[str]:
    """合并来源并应用 limit。"""
    merged_ids = sorted(set(detail_index) | set(comments_index) | set(media_index))
    if limit is None:
        return merged_ids
    return merged_ids[: max(0, int(limit))]


def _build_rows(
    selected_video_ids: list[str],
    detail_index: dict[str, dict[str, Any]],
    comments_index: dict[str, dict[str, Any]],
    comment_rows_by_video: dict[str, list[dict[str, Any]]],
    media_index: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """把索引转换为 accounts/videos/video_metrics/comments 四类行。"""
    accounts_map: dict[str, dict[str, str]] = {}
    video_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    comment_rows: list[dict[str, Any]] = []
    for video_id in selected_video_ids:
        detail_entry = detail_index.get(video_id, {})
        comments_entry = comments_index.get(video_id, {})
        media_entry = media_index.get(video_id, {})
        detail_payload = detail_entry.get("payload", {})
        comments_payload = comments_entry.get("payload", {})
        account_name = media_entry.get("account_name", "unknown")
        account_id = _build_account_id(account_name)
        accounts_map.setdefault(account_id, {"account_id": account_id, "account_name": account_name, "source": "mp4"})
        video_rows.append(_build_video_row(video_id, account_id, detail_entry, comments_entry, media_entry))
        metric_rows.append(_build_metric_row(video_id, detail_payload, detail_entry.get("path")))
        comment_rows.extend(comment_rows_by_video.get(video_id, []))
    accounts = sorted(accounts_map.values(), key=lambda item: item["account_id"])
    return accounts, video_rows, metric_rows, comment_rows


def _build_video_row(
    video_id: str,
    account_id: str,
    detail_entry: dict[str, Any],
    comments_entry: dict[str, Any],
    media_entry: dict[str, Any],
) -> dict[str, Any]:
    """构建 videos.csv 的单行。"""
    detail_payload = detail_entry.get("payload", {})
    comments_payload = comments_entry.get("payload", {})
    return {
        "video_id": video_id,
        "video_url": _extract_video_url(detail_payload, comments_payload, video_id),
        "account_id": account_id,
        "title": _extract_title(detail_payload),
        "detail_artifact_path": str(detail_entry.get("path") or ""),
        "comments_artifact_path": str(comments_entry.get("path") or ""),
        "mp4_path": media_entry.get("mp4_path", ""),
        "detail_collected_at": _to_text(detail_payload.get("collected_at")),
        "comments_collected_at": _to_text(comments_payload.get("collected_at")),
    }


def _build_metric_row(video_id: str, detail_payload: dict[str, Any], detail_path: Path | None) -> dict[str, Any]:
    """提取视频指标，统一落入 video_metrics.csv。"""
    return {
        "video_id": video_id,
        "view_count": _extract_metric(detail_payload, ["view_count", "play_count", "viewCount", "playCount"]),
        "like_count": _extract_metric(detail_payload, ["like_count", "digg_count", "likeCount", "diggCount"]),
        "comment_count": _extract_metric(detail_payload, ["comment_count", "commentCount"]),
        "share_count": _extract_metric(detail_payload, ["share_count", "shareCount"]),
        "metrics_artifact_path": str(detail_path or ""),
        "collected_at": _to_text(detail_payload.get("collected_at")),
    }


def _extract_comment_rows(video_id: str, payload: dict[str, Any], artifact_path: Path) -> list[dict[str, Any]]:
    """展开评论列表并做最小字段标准化。"""
    comments = payload.get("comments")
    if not isinstance(comments, list):
        data = payload.get("data")
        comments = data.get("comments") if isinstance(data, dict) else []
    if not isinstance(comments, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(comments):
        if not isinstance(item, dict):
            continue
        comment_id = _to_text(item.get("cid") or item.get("comment_id") or item.get("id")) or f"{video_id}:{index}"
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        rows.append(
            {
                "comment_id": comment_id,
                "video_id": video_id,
                "text": _to_text(item.get("text") or item.get("content") or item.get("desc")),
                "create_time": _to_text(item.get("create_time")),
                "digg_count": _to_int(item.get("digg_count")) or 0,
                "user_id": _to_text(user.get("uid") or user.get("user_id") or item.get("user_id")),
                "user_name": _to_text(user.get("nickname") or item.get("nickname")),
                "artifact_path": str(artifact_path),
            }
        )
    return rows


def _write_csvs(
    output_dir: Path,
    account_rows: list[dict[str, Any]],
    video_rows: list[dict[str, Any]],
    metric_rows: list[dict[str, Any]],
    comment_rows: list[dict[str, Any]],
) -> None:
    """写出四类 CSV 文件。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "accounts.csv", ["account_id", "account_name", "source"], account_rows)
    _write_csv(
        output_dir / "videos.csv",
        [
            "video_id",
            "video_url",
            "account_id",
            "title",
            "detail_artifact_path",
            "comments_artifact_path",
            "mp4_path",
            "detail_collected_at",
            "comments_collected_at",
        ],
        video_rows,
    )
    _write_csv(
        output_dir / "video_metrics.csv",
        ["video_id", "view_count", "like_count", "comment_count", "share_count", "metrics_artifact_path", "collected_at"],
        metric_rows,
    )
    _write_csv(
        output_dir / "comments.csv",
        ["comment_id", "video_id", "text", "create_time", "digg_count", "user_id", "user_name", "artifact_path"],
        comment_rows,
    )


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    """按固定字段顺序写出 UTF-8 CSV。"""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def _resolve_sqlite_path(sqlite_arg: str | None, workspace: Path, output_dir: Path) -> Path | None:
    """解析 SQLite 输出路径；默认文件放输出目录，显式相对路径按 workspace 解析。"""
    if sqlite_arg is None:
        return None
    if sqlite_arg == "analysis_dataset.sqlite":
        return output_dir / sqlite_arg
    path = Path(sqlite_arg)
    return path if path.is_absolute() else workspace / path


def _write_sqlite(
    sqlite_path: Path,
    account_rows: list[dict[str, Any]],
    video_rows: list[dict[str, Any]],
    metric_rows: list[dict[str, Any]],
    comment_rows: list[dict[str, Any]],
) -> None:
    """导出 SQLite 数据集。"""
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sqlite_path)
    try:
        conn.execute(TABLE_SCHEMAS["accounts"])
        conn.execute(TABLE_SCHEMAS["videos"])
        conn.execute(TABLE_SCHEMAS["video_metrics"])
        conn.execute(TABLE_SCHEMAS["comments"])
        _replace_rows(conn, "accounts", account_rows)
        _replace_rows(conn, "videos", video_rows)
        _replace_rows(conn, "video_metrics", metric_rows)
        _replace_rows(conn, "comments", comment_rows)
        conn.commit()
    finally:
        # Windows 会锁住仍打开的 sqlite 文件，显式关闭保证测试和重复生成可清理。
        conn.close()


def _replace_rows(conn: sqlite3.Connection, table_name: str, rows: list[dict[str, Any]]) -> None:
    """使用 REPLACE INTO 写入行，保证重复执行可覆盖更新。"""
    conn.execute(f"DELETE FROM {table_name}")
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ",".join("?" for _ in columns)
    columns_sql = ",".join(columns)
    sql = f"REPLACE INTO {table_name} ({columns_sql}) VALUES ({placeholders})"
    conn.executemany(sql, [[row.get(column) for column in columns] for row in rows])


def _extract_video_id(payload: dict[str, Any], fallback_id: str) -> str:
    """优先从字段读取视频 ID，不存在时再用 URL 或文件名兜底。"""
    direct_id = _to_text(payload.get("video_id"))
    if direct_id:
        return direct_id
    url_id = _extract_video_id_from_text(_to_text(payload.get("video_url")))
    if url_id:
        return url_id
    return _extract_video_id_from_text(fallback_id)


def _extract_video_url(detail_payload: dict[str, Any], comments_payload: dict[str, Any], video_id: str) -> str:
    """统一推断可访问的视频 URL。"""
    for payload in (detail_payload, comments_payload):
        url = _to_text(payload.get("video_url"))
        if url:
            return url
    return f"https://www.douyin.com/video/{video_id}" if video_id else ""


def _extract_title(detail_payload: dict[str, Any]) -> str:
    """提取标题字段，兼容 raw.title 与 title。"""
    raw = detail_payload.get("raw")
    if isinstance(raw, dict):
        title = _to_text(raw.get("title"))
        if title:
            return title
    return _to_text(detail_payload.get("title"))


def _extract_metric(payload: dict[str, Any], keys: list[str]) -> int:
    """在多层统计容器中提取指标值。"""
    containers = [payload]
    for key in ("metrics", "statistics", "stats", "aweme_statistics", "stat"):
        value = payload.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for key in keys:
            value = _to_int(container.get(key))
            if value is not None:
                return value
    return 0


def _extract_video_id_from_text(text: str) -> str:
    """从 URL 或文件名文本中提取最长数字串作为视频 ID。"""
    digits: list[str] = []
    current = []
    for char in text:
        if char.isdigit():
            current.append(char)
            continue
        if current:
            digits.append("".join(current))
            current = []
    if current:
        digits.append("".join(current))
    candidates = [item for item in digits if len(item) >= 10]
    return max(candidates, key=len) if candidates else ""


def _build_account_id(account_name: str) -> str:
    """按账号名构建稳定 account_id。"""
    normalized = account_name.strip() or "unknown"
    parsed = urlparse(normalized)
    if parsed.scheme and parsed.netloc:
        normalized = parsed.path.strip("/") or parsed.netloc
    return normalized.replace(" ", "_")


def _load_json(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    """读取 JSON，坏文件返回 None 并累积 warning。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"JSON 解析失败：{path}，错误：{exc}")
        return None
    if isinstance(data, dict):
        return data
    warnings.append(f"JSON 顶层不是对象，已跳过：{path}")
    return None


def _to_text(value: Any) -> str:
    """把任意值归一化为字符串。"""
    return str(value).strip() if value is not None else ""


def _to_int(value: Any) -> int | None:
    """把数字或数字字符串转换成整数。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
    return None


def _warn(message: str) -> None:
    """统一 warning 输出。"""
    print(f"[WARNING] {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DETAIL_GLOB = "video_detail_*.json"
COMMENTS_GLOB = "video_comments_*.json"
REAL_COMMENT_RESPONSE_PATTERNS = (
    "/aweme/v1/web/comment/list",
    "/aweme/v1/web/comment/publish",
    "/aweme/v1/web/comment/list/reply",
)
VIDEO_URL_TEMPLATE = "https://www.douyin.com/video/{video_id}"
VIDEO_ID_IN_URL = re.compile(r"/video/([^/?#]+)")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """构建评论补抓目标列表，并输出 JSON/TXT 目标文件。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    detail_root = workspace / "artifacts" / "collector" / "video"
    comments_root = workspace / "artifacts" / "collector" / "comments"
    output_json = (workspace / args.output_json).resolve()
    output_txt = (workspace / args.output_txt).resolve()

    details = _scan_detail_records(detail_root)
    comment_status = _scan_comment_artifacts(comments_root)
    targets = _build_targets(details, comment_status)
    planned_targets = _apply_limit(targets, args.limit)

    payload = {
        "ok": True,
        "workspace": str(workspace),
        "detail_count": len(details),
        "comment_video_count": len(comment_status),
        "target_count": len(targets),
        "planned_count": len(planned_targets),
        "output_json": str(output_json),
        "output_txt": str(output_txt),
        "targets": planned_targets,
    }
    _write_json(output_json, payload)
    _write_txt(output_txt, planned_targets)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成评论补抓目标清单。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument(
        "--output-json",
        default="artifacts/collector/comment_backfill_targets.json",
        help="JSON 目标文件输出路径（相对 workspace）。",
    )
    parser.add_argument(
        "--output-txt",
        default="artifacts/collector/comment_backfill_targets.txt",
        help="TXT 目标文件输出路径（相对 workspace）。",
    )
    parser.add_argument("--limit", type=int, default=None, help="最多输出多少个目标。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _scan_detail_records(detail_root: Path) -> dict[str, dict[str, Any]]:
    """扫描详情产物，提取视频 ID、URL 及互动指标。"""
    if not detail_root.exists():
        return {}
    details: dict[str, dict[str, Any]] = {}
    for path in sorted(detail_root.glob(DETAIL_GLOB), key=lambda item: item.stat().st_mtime):
        payload = _load_json(path)
        video_id = _extract_video_id(payload, path.stem.removeprefix("video_detail_"))
        video_url = _extract_video_url(payload, video_id)
        if not _is_valid_video_id(video_id) or not video_url:
            continue
        # 同一视频可能被多次补抓；按文件名排序时后面的通常更新，直接覆盖可避免旧空指标污染排序。
        details[video_id] = {
            "video_id": video_id,
            "video_url": video_url,
            "comment_count": _extract_metric(payload, ["comment_count", "commentCount"]),
            "like_count": _extract_metric(payload, ["like_count", "digg_count", "likeCount", "diggCount"]),
            "share_count": _extract_metric(payload, ["share_count", "shareCount"]),
            "view_count": _extract_metric(payload, ["view_count", "play_count", "viewCount", "playCount"]),
            "detail_path": str(path),
        }
    return details


def _scan_comment_artifacts(comments_root: Path) -> dict[str, dict[str, Any]]:
    """扫描评论产物，识别是否存在非空 comments。"""
    if not comments_root.exists():
        return {}
    status: dict[str, dict[str, Any]] = {}
    for path in sorted(comments_root.glob(COMMENTS_GLOB)):
        payload = _load_json(path)
        file_hint_id = path.stem.removeprefix("video_comments_")
        video_id = _extract_video_id(payload, file_hint_id)
        if not _is_valid_video_id(video_id):
            continue
        current = status.setdefault(video_id, {"artifact_count": 0, "has_non_empty_comments": False})
        current["artifact_count"] += 1
        if _has_non_empty_comments(payload):
            current["has_non_empty_comments"] = True
    return status


def _build_targets(
    details: dict[str, dict[str, Any]],
    comment_status: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """优先选择 comment_count>0 且没有非空评论产物的视频。"""
    targets: list[dict[str, Any]] = []
    for video_id, detail in details.items():
        status = comment_status.get(video_id, {"artifact_count": 0, "has_non_empty_comments": False})
        if status["has_non_empty_comments"]:
            continue
        has_expected_comments = detail["comment_count"] > 0
        priority = 1 if has_expected_comments else 0
        targets.append(
            {
                **detail,
                "priority": priority,
                "comment_expected_status": "comment_expected" if has_expected_comments else "no_comment_expected",
                "should_backfill_comment": has_expected_comments,
                "has_comment_artifact": bool(status["artifact_count"]),
                "has_non_empty_comments": False,
            }
        )
    # 业务规则：先补有评论量但未抓到有效评论的数据，剩余目标再按评论量降序排队。
    targets.sort(key=lambda item: (-item["priority"], -item["comment_count"], item["video_id"]))
    return targets


def _apply_limit(items: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    """按 limit 截断输出目标。"""
    if limit is None:
        return list(items)
    return list(items[: max(0, int(limit))])


def _extract_video_id(payload: dict[str, Any], fallback_id: str = "") -> str:
    """优先用 payload 中的视频 ID，缺失时从 URL 或文件名提示兜底。"""
    direct_id = _to_text(payload.get("video_id"))
    if direct_id:
        return direct_id
    from_url = _extract_video_id_from_url(_to_text(payload.get("video_url")))
    if from_url:
        return from_url
    return _to_text(fallback_id)


def _extract_video_url(payload: dict[str, Any], video_id: str) -> str:
    """统一构造可回放的视频 URL。"""
    url = _to_text(payload.get("video_url"))
    if url:
        return url
    return VIDEO_URL_TEMPLATE.format(video_id=video_id) if video_id else ""


def _extract_video_id_from_url(video_url: str) -> str:
    """从视频 URL 提取视频 ID。"""
    matched = VIDEO_ID_IN_URL.search(video_url)
    return matched.group(1).strip() if matched else ""


def _is_valid_video_id(video_id: str) -> bool:
    """过滤 undefined、hash 片段等无效 ID，只保留抖音数字视频 ID。"""
    normalized = _to_text(video_id)
    return normalized.isdigit() and len(normalized) >= 10


def _extract_metric(payload: dict[str, Any], keys: list[str]) -> int:
    """在常见统计结构中提取整型指标。"""
    containers = [
        payload,
        payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
        payload.get("statistics") if isinstance(payload.get("statistics"), dict) else {},
        payload.get("stats") if isinstance(payload.get("stats"), dict) else {},
        payload.get("aweme_statistics") if isinstance(payload.get("aweme_statistics"), dict) else {},
        payload.get("stat") if isinstance(payload.get("stat"), dict) else {},
    ]
    for container in containers:
        for key in keys:
            value = _to_int(container.get(key))
            if value is not None:
                return value
    return 0


def _has_non_empty_comments(payload: dict[str, Any]) -> bool:
    """识别是否已有真实评论，避免平台配置文案误判为已完成。"""
    comments = payload.get("comments")
    if isinstance(comments, list):
        return any(_is_real_comment_item(item) for item in comments)
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("comments"), list):
        return any(_is_real_comment_item(item) for item in data["comments"])
    return False


def _is_real_comment_item(item: Any) -> bool:
    """真实评论需来自评论接口，或至少带有作者标识。"""
    if not isinstance(item, dict):
        return False
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    response_url = str(raw.get("response_url") or "").lower()
    if any(pattern in response_url for pattern in REAL_COMMENT_RESPONSE_PATTERNS):
        return True
    if str(item.get("author_id") or "").strip():
        return True
    return bool(str(item.get("author_name") or "").strip() and not raw.get("stub"))


def _load_json(path: Path) -> dict[str, Any]:
    """安全读取 JSON，坏文件返回空对象。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """写出 JSON 目标文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_txt(path: Path, targets: list[dict[str, Any]]) -> None:
    """写出 URL 文本目标文件，供批处理任务直接读取。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [target["video_url"] for target in targets]
    content = "\n".join(lines)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _to_text(value: Any) -> str:
    """把任意值规范成去空白字符串。"""
    return str(value).strip() if value is not None else ""


def _to_int(value: Any) -> int | None:
    """把常见字符串/数值安全转换为 int。"""
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


if __name__ == "__main__":
    raise SystemExit(main())

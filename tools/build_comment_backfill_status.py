from __future__ import annotations

import argparse
import json
import sys
import re
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
COMMENTS_GLOB = "video_comments_*.json"
RECENT_LIMIT = 20
TARGET_SUMMARY_KEYS = ("detail_count", "target_count", "planned_count", "comment_video_count")
REAL_COMMENT_RESPONSE_PATTERNS = (
    "/aweme/v1/web/comment/list",
    "/aweme/v1/web/comment/publish",
    "/aweme/v1/web/comment/list/reply",
)
VIDEO_ID_IN_URL = re.compile(r"/video/([^/?#]+)")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """构建评论补采状态报告。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    report = build_comment_backfill_status(workspace)
    json_output = _resolve_path(workspace, args.output)
    md_output = _resolve_path(workspace, args.md_output)
    _write_outputs(report, json_output, md_output)
    print(json.dumps({"ok": True, "json_output": str(json_output), "md_output": str(md_output)}, ensure_ascii=False, indent=2))
    return 0


def build_comment_backfill_status(workspace: Path) -> dict[str, Any]:
    """扫描评论产物并合并补采目标摘要。"""
    comments_root = workspace / "artifacts" / "collector" / "comments"
    artifacts = _scan_comment_artifacts(comments_root)
    summary = _summarize_artifacts(artifacts)
    targets = _load_target_summary(workspace / "artifacts" / "collector" / "comment_backfill_targets.json")
    return {
        "ok": True,
        "workspace": str(workspace),
        **summary,
        "comment_backfill_targets": targets,
        "recent_artifacts": _recent_artifacts(artifacts),
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成评论补采状态报告。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--output", default="artifacts/status/comment_backfill_status.json", help="JSON 输出路径。")
    parser.add_argument("--md-output", default="artifacts/status/comment_backfill_status.md", help="Markdown 输出路径。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _scan_comment_artifacts(comments_root: Path) -> list[dict[str, Any]]:
    """读取所有评论产物，保留文件级摘要供最近产物展示。"""
    if not comments_root.exists():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(comments_root.glob(COMMENTS_GLOB), key=lambda item: item.stat().st_mtime):
        payload = _load_json(path)
        comments = _extract_comments(payload)
        real_count = sum(1 for item in comments if _is_real_comment_item(item))
        artifacts.append(_build_artifact_summary(path, payload, comments, real_count))
    return artifacts


def _build_artifact_summary(path: Path, payload: dict[str, Any], comments: list[Any], real_count: int) -> dict[str, Any]:
    """构造单个产物的摘要，避免 Markdown 阶段重复解析。"""
    video_id = _extract_video_id(payload, path.stem.removeprefix("video_comments_"))
    comment_count = len(comments)
    return {
        "path": str(path),
        "name": path.name,
        "mtime": path.stat().st_mtime,
        "video_id": video_id,
        "comment_count": comment_count,
        "real_comment_count": real_count,
        "is_empty": comment_count == 0,
        "is_noise_only": comment_count > 0 and real_count == 0,
        "has_real_comments": real_count > 0,
    }


def _summarize_artifacts(artifacts: list[dict[str, Any]]) -> dict[str, int]:
    """按视频聚合状态指标，同一视频多个产物合并判断。"""
    videos: dict[str, dict[str, int]] = {}
    for artifact in artifacts:
        video_id = artifact["video_id"] or artifact["name"]
        current = videos.setdefault(video_id, {"comments": 0, "real": 0, "artifacts": 0})
        current["comments"] += int(artifact["comment_count"])
        current["real"] += int(artifact["real_comment_count"])
        current["artifacts"] += 1
    return {
        "comment_artifact_count": len(artifacts),
        "comment_video_count": len(videos),
        "real_comment_video_count": sum(1 for item in videos.values() if item["real"] > 0),
        "empty_comment_video_count": sum(1 for item in videos.values() if item["comments"] == 0),
        "noise_only_video_count": sum(1 for item in videos.values() if item["comments"] > 0 and item["real"] == 0),
        "total_comment_count": sum(int(item["comment_count"]) for item in artifacts),
        "total_real_comment_count": sum(int(item["real_comment_count"]) for item in artifacts),
    }


def _recent_artifacts(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按修改时间倒序取最近评论产物。"""
    recent = sorted(artifacts, key=lambda item: item["mtime"], reverse=True)[:RECENT_LIMIT]
    return [{key: value for key, value in item.items() if key != "mtime"} for item in recent]


def _extract_comments(payload: dict[str, Any]) -> list[Any]:
    """兼容顶层 comments 与 data.comments 两种结构。"""
    comments = payload.get("comments")
    if isinstance(comments, list):
        return comments
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("comments"), list):
        return data["comments"]
    return []


def _is_real_comment_item(item: Any) -> bool:
    """真实评论沿用补采目标脚本的接口来源与作者字段判定。"""
    if not isinstance(item, dict):
        return False
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    response_url = str(raw.get("response_url") or "").lower()
    if any(pattern in response_url for pattern in REAL_COMMENT_RESPONSE_PATTERNS):
        return True
    if str(item.get("author_id") or "").strip():
        return True
    return bool(str(item.get("author_name") or "").strip() and not raw.get("stub"))


def _load_target_summary(path: Path) -> dict[str, int]:
    """只合并补采目标报告中的稳定计数字段。"""
    payload = _load_json(path) if path.exists() else {}
    return {key: _to_int(payload.get(key)) for key in TARGET_SUMMARY_KEYS}


def _render_markdown(report: dict[str, Any]) -> str:
    """渲染中文 Markdown 状态报告。"""
    lines = ["# 评论补采状态报告", "", "## 总体指标", ""]
    lines.extend(_metric_lines(report))
    lines.extend(["", "## 补采目标摘要", ""])
    lines.extend(_target_lines(report["comment_backfill_targets"]))
    lines.extend(["", "## 最近 20 个评论产物", "", "| 文件 | 视频 ID | 评论数 | 真实评论数 | 状态 |", "| --- | --- | ---: | ---: | --- |"])
    for item in report["recent_artifacts"]:
        lines.append(_artifact_line(item))
    return "\n".join(lines) + "\n"


def _metric_lines(report: dict[str, Any]) -> list[str]:
    """生成总体指标行，保持 Markdown 文案集中。"""
    labels = {
        "comment_artifact_count": "评论产物数",
        "comment_video_count": "评论视频数",
        "real_comment_video_count": "有真实评论视频数",
        "empty_comment_video_count": "空评论视频数",
        "noise_only_video_count": "仅噪声视频数",
        "total_comment_count": "评论条目总数",
        "total_real_comment_count": "真实评论总数",
    }
    return [f"- {label}：{report[key]}" for key, label in labels.items()]


def _target_lines(targets: dict[str, int]) -> list[str]:
    """生成补采目标摘要行。"""
    labels = {
        "detail_count": "详情产物数",
        "target_count": "目标视频数",
        "planned_count": "计划补采数",
        "comment_video_count": "目标侧评论视频数",
    }
    return [f"- {label}：{targets[key]}" for key, label in labels.items()]


def _artifact_line(item: dict[str, Any]) -> str:
    """生成单个评论产物摘要表格行。"""
    status = "真实评论" if item["has_real_comments"] else "空评论" if item["is_empty"] else "仅噪声"
    return f"| {item['name']} | {item['video_id']} | {item['comment_count']} | {item['real_comment_count']} | {status} |"


def _extract_video_id(payload: dict[str, Any], fallback_id: str = "") -> str:
    """优先使用 payload 视频 ID，其次从视频 URL 抽取，最后回退文件名。"""
    direct_id = str(payload.get("video_id") or "").strip()
    if direct_id:
        return direct_id
    video_url = str(payload.get("video_url") or "").strip()
    match = VIDEO_ID_IN_URL.search(video_url)
    if match:
        return match.group(1)
    return str(fallback_id or "").strip()


def _load_json(path: Path) -> dict[str, Any]:
    """读取 JSON 对象，坏文件按空对象处理。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_outputs(report: dict[str, Any], json_output: Path, md_output: Path) -> None:
    """写出 JSON 和 Markdown 报告。"""
    json_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_output.write_text(_render_markdown(report), encoding="utf-8")


def _resolve_path(workspace: Path, value: str | Path) -> Path:
    """解析 workspace 相对路径。"""
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _to_int(value: Any) -> int:
    """把目标摘要字段规范为整数。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.short_video_intel.analysis.local_video_inputs import (
    _build_content_feature_summary,
    _extract_frame_feature_summary,
    _extract_sample_frames,
    _extract_subtitle_hint_summary,
    _ffprobe_video,
)

DEFAULT_OUTPUT = "artifacts/analysis-inputs/local_video_inputs_strict_second_round.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """基于严格有效池生成第二轮多模态输入。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    rows = _load_rows(workspace)
    selected = _select_rows(rows, args.max_per_account)
    items = [_build_item(workspace, row, args.frames_per_video) for row in selected]
    payload = _build_payload(items, rows, selected, args.frames_per_video)
    output = _resolve_path(workspace, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), "items": len(items), "accounts": _account_counts(items)}, ensure_ascii=False, indent=2))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令参数。"""
    parser = argparse.ArgumentParser(description="生成严格有效池多模态输入。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--max-per-account", type=int, default=5, help="每个账号最多选择多少条。")
    parser.add_argument("--frames-per-video", type=int, default=3, help="每条视频抽帧数量。")
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT), help="输出 JSON。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _load_rows(workspace: Path) -> list[dict[str, Any]]:
    """加载严格有效视频和指标，并计算互动代理分。"""
    videos = _read_csv(workspace / "data" / "processed_strict_valid" / "videos.csv")
    metrics = {row["video_id"]: row for row in _read_csv(workspace / "data" / "processed_strict_valid" / "video_metrics.csv")}
    rows = []
    for video in videos:
        metric = metrics.get(video["video_id"], {})
        merged = {**video, **_metric_values(metric)}
        merged["engagement_score"] = merged["like_count"] + merged["comment_count"] * 3 + merged["share_count"] * 2
        if _usable_video_path(merged):
            rows.append(merged)
    return rows


def _select_rows(rows: list[dict[str, Any]], max_per_account: int) -> list[dict[str, Any]]:
    """每个账号按高、中、低互动分层抽样，减少只看头部视频的偏差。"""
    selected: list[dict[str, Any]] = []
    for account in sorted({row["account_id"] for row in rows}):
        account_rows = sorted([row for row in rows if row["account_id"] == account], key=lambda item: item["engagement_score"], reverse=True)
        selected.extend(_stratified_pick(account_rows, max(1, max_per_account)))
    return selected


def _stratified_pick(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """从账号内高分、中位、低分区间取样。"""
    if len(rows) <= limit:
        return rows
    indexes = _spread_indexes(len(rows), limit)
    return [rows[index] for index in indexes]


def _spread_indexes(total: int, limit: int) -> list[int]:
    """生成覆盖头部、中位、尾部的索引。"""
    if limit <= 1:
        return [0]
    return sorted({round(index * (total - 1) / (limit - 1)) for index in range(limit)})


def _build_item(workspace: Path, row: dict[str, Any], frames_per_video: int) -> dict[str, Any]:
    """为单条视频生成 ffprobe、抽帧和本地视觉摘要。"""
    video_path = Path(row["mp4_path"])
    probe = _ffprobe_video(video_path)
    frame_dir = workspace / "artifacts" / "analysis-inputs" / "frames_second_round" / row["video_id"]
    frames = _extract_sample_frames(video_path=video_path, probe=probe, sample_dir=frame_dir, frames_per_video=frames_per_video)
    frame_summary = _extract_frame_feature_summary(frames)
    subtitle_hints = _extract_subtitle_hint_summary(frames)
    content_features = _build_content_feature_summary(probe=probe, frame_stats=frame_summary)
    return _item_payload(row, video_path, probe, frames, frame_summary, subtitle_hints, content_features)


def _item_payload(
    row: dict[str, Any],
    video_path: Path,
    probe: dict[str, Any],
    frames: list[dict[str, Any]],
    frame_summary: dict[str, Any],
    subtitle_hints: dict[str, Any],
    content_features: dict[str, Any],
) -> dict[str, Any]:
    """组装 run_multimodal_batch 可直接消费的输入项。"""
    return {
        "video_id": row["video_id"],
        "video_url": row["video_url"],
        "source_name": row["account_id"],
        "download_output_path": str(video_path),
        "file_size": video_path.stat().st_size,
        "probe": probe,
        "metrics": {key: row[key] for key in ("like_count", "comment_count", "share_count", "view_count", "engagement_score")},
        "frame_samples": frames,
        "frame_feature_summary": frame_summary,
        "subtitle_hints": subtitle_hints,
        "content_features": content_features,
        "analysis_input": {
            "video_meta": {"video_id": row["video_id"], "source_name": row["account_id"], "video_url": row["video_url"]},
            "video_features": content_features,
            "frame_feature_summary": frame_summary,
            "subtitle_hints": subtitle_hints,
        },
    }


def _build_payload(items: list[dict[str, Any]], rows: list[dict[str, Any]], selected: list[dict[str, Any]], frames_per_video: int) -> dict[str, Any]:
    """生成带抽样说明的输入文件。"""
    return {
        "ok": True,
        "analysis_type": "strict_second_round_local_video_inputs",
        "generated_at": datetime.now().astimezone().isoformat(),
        "strict_video_count": len(rows),
        "selected_count": len(selected),
        "frames_per_video": frames_per_video,
        "accounts": _account_counts(items),
        "items": items,
    }


def _metric_values(metric: dict[str, str]) -> dict[str, int]:
    """提取整数指标。"""
    return {key: _to_int(metric.get(key)) for key in ("view_count", "like_count", "comment_count", "share_count")}


def _usable_video_path(row: dict[str, Any]) -> bool:
    """只保留本地 MP4 存在的视频。"""
    path = Path(str(row.get("mp4_path") or ""))
    return path.exists() and path.is_file()


def _account_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """统计账号样本数。"""
    counts: dict[str, int] = {}
    for item in items:
        account = item.get("source_name") or "unknown"
        counts[account] = counts.get(account, 0) + 1
    return counts


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except ValueError:
        return 0


def _resolve_path(workspace: Path, value: Path) -> Path:
    return value if value.is_absolute() else workspace / value


if __name__ == "__main__":
    raise SystemExit(main())

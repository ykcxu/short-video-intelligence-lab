from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """根据严格池缺口报告生成补采目标清单。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    gap_report = _load_json(_resolve_path(workspace, args.gap_report))
    target_source = _load_json(_resolve_path(workspace, args.target_source))
    targets = build_backfill_targets(gap_report, target_source, min_priority=args.min_priority, min_valid_videos=args.min_valid_videos)
    output = _resolve_path(workspace, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), "target_count": len(targets)}, ensure_ascii=False, indent=2))
    return 0


def build_backfill_targets(
    gap_report: dict[str, Any],
    target_source: dict[str, Any],
    *,
    min_priority: str = "中",
    min_valid_videos: int = 50,
) -> list[dict[str, Any]]:
    """从缺口报告筛选账号，并补齐主页 URL 与分类字段。"""
    source_index = _build_source_index(target_source)
    selected = []
    for row in _gap_rows(gap_report):
        if not _should_select(row, min_priority=min_priority, min_valid_videos=min_valid_videos):
            continue
        source = source_index.get(_normalize_name(row.get("account_id")))
        if not source:
            continue
        selected.append(_build_target(source, row))
    selected.sort(key=lambda item: (PRIORITY_ORDER.get(item["backfill_priority"], 9), item["valid_video_count"], item["source_name"]))
    return selected


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成严格有效池补采目标清单。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--gap-report", default="artifacts/status/strict_pool_gap_report.json", help="严格池缺口报告 JSON。")
    parser.add_argument("--target-source", default="artifacts/analysis/homepage_batch_summary_20260422.json", help="含主页 URL 的目标来源 JSON。")
    parser.add_argument("--output", default="artifacts/analysis/strict_pool_backfill_targets.json", help="输出目标清单 JSON。")
    parser.add_argument("--min-priority", choices=("高", "中", "低"), default="中", help="纳入补采的最低优先级。")
    parser.add_argument("--min-valid-videos", type=int, default=50, help="严格有效视频低于该值时纳入补采。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _build_source_index(target_source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """从主页汇总 rows 构建账号名索引。"""
    rows = target_source.get("rows") if isinstance(target_source.get("rows"), list) else []
    result = {}
    for row in rows:
        if isinstance(row, dict):
            name = _normalize_name(row.get("source_name"))
            if name:
                result[name] = row
    return result


def _gap_rows(gap_report: dict[str, Any]) -> list[dict[str, Any]]:
    """安全读取缺口报告账号行。"""
    rows = gap_report.get("accounts")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _should_select(row: dict[str, Any], *, min_priority: str, min_valid_videos: int) -> bool:
    """按优先级和有效视频数决定是否补采。"""
    priority = str(row.get("priority") or "低")
    valid_count = _to_int(row.get("valid_video_count"))
    priority_hit = PRIORITY_ORDER.get(priority, 9) <= PRIORITY_ORDER[min_priority]
    return priority_hit or valid_count < min_valid_videos


def _build_target(source: dict[str, Any], gap_row: dict[str, Any]) -> dict[str, Any]:
    """合并目标来源与缺口统计，产出可执行补采目标。"""
    return {
        "homepage_url": source.get("homepage_url", ""),
        "source_name": source.get("source_name", gap_row.get("account_id", "")),
        "category_lv1": source.get("category_lv1", ""),
        "category_lv2": source.get("category_lv2", ""),
        "platform": source.get("platform", "抖音"),
        "status": source.get("status", "active"),
        "backfill_priority": gap_row.get("priority", "低"),
        "input_video_count": _to_int(gap_row.get("input_video_count")),
        "valid_video_count": _to_int(gap_row.get("valid_video_count")),
        "filtered_video_count": _to_int(gap_row.get("filtered_video_count")),
        "retention_rate": float(gap_row.get("retention_rate") or 0),
    }


def _load_json(path: Path) -> dict[str, Any]:
    """读取 JSON 对象，结构错误直接报错。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON 顶层不是对象：{path}")
    return data


def _normalize_name(value: Any) -> str:
    """归一化账号名，兼容历史问号占位。"""
    text = str(value or "").strip()
    return "".join(char for char in text if char not in {"?", "？"})


def _to_int(value: Any) -> int:
    """安全转整数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _resolve_path(workspace: Path, value: str | Path) -> Path:
    """解析 workspace 相对路径。"""
    path = Path(value)
    return path if path.is_absolute() else workspace / path


if __name__ == "__main__":
    raise SystemExit(main())

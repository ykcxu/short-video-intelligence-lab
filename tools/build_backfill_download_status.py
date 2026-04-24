from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """汇总补采目标的本地下载覆盖情况。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    targets = _load_json_list(_resolve_path(workspace, args.targets))
    processed_videos = _read_csv_rows(_resolve_path(workspace, args.processed_videos))
    report = build_backfill_download_status(workspace, targets, processed_videos)
    output = _resolve_path(workspace, args.output)
    md_output = _resolve_path(workspace, args.md_output)
    _write_outputs(report, output, md_output)
    print(json.dumps({"ok": True, "json_output": str(output), "md_output": str(md_output)}, ensure_ascii=False, indent=2))
    return 0


def build_backfill_download_status(workspace: Path, targets: list[dict[str, Any]], processed_videos: list[dict[str, str]]) -> dict[str, Any]:
    """按补采账号统计 mp4 和已入库视频覆盖。"""
    processed_counts = _count_processed_by_account(processed_videos)
    accounts = []
    for target in targets:
        name = str(target.get("source_name") or "").strip()
        normalized_name = _normalize_name(name)
        mp4_count = _count_mp4(workspace, normalized_name)
        processed_count = processed_counts.get(normalized_name, 0)
        accounts.append({
            "source_name": name,
            "backfill_priority": target.get("backfill_priority", ""),
            "target_valid_video_count": int(target.get("valid_video_count") or 0),
            "local_mp4_count": mp4_count,
            "processed_video_count": processed_count,
            "needs_dataset_refresh": mp4_count > processed_count,
        })
    return {
        "ok": True,
        "target_count": len(accounts),
        "needs_dataset_refresh_count": sum(1 for item in accounts if item["needs_dataset_refresh"]),
        "accounts": accounts,
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="汇总补采目标下载覆盖情况。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--targets", default="artifacts/analysis/strict_pool_backfill_targets.json", help="补采目标 JSON。")
    parser.add_argument("--processed-videos", default="data/processed/videos.csv", help="当前 processed videos.csv。")
    parser.add_argument("--output", default="artifacts/status/backfill_download_status.json", help="JSON 输出路径。")
    parser.add_argument("--md-output", default="artifacts/status/backfill_download_status.md", help="Markdown 输出路径。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _count_processed_by_account(rows: list[dict[str, str]]) -> dict[str, int]:
    """统计当前 processed 视频行数。"""
    counts: dict[str, int] = {}
    for row in rows:
        account = _normalize_name(row.get("account_id"))
        counts[account] = counts.get(account, 0) + 1
    return counts


def _count_mp4(workspace: Path, source_name: str) -> int:
    """统计账号下载目录下 mp4 文件数。"""
    path = workspace / "downloads" / "artifact" / source_name
    return len(list(path.glob("*.mp4"))) if path.exists() else 0


def _normalize_name(value: Any) -> str:
    """账号名归一化，兼容历史问号占位。"""
    return "".join(char for char in str(value or "").strip() if char not in {"?", "？"})


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    """读取 CSV 行。"""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    """读取 JSON 列表。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"JSON 顶层不是列表：{path}")
    return [item for item in data if isinstance(item, dict)]


def _write_outputs(report: dict[str, Any], json_output: Path, md_output: Path) -> None:
    """写出 JSON 和 Markdown。"""
    json_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_output.write_text(_render_markdown(report), encoding="utf-8")


def _render_markdown(report: dict[str, Any]) -> str:
    """渲染 Markdown 状态表。"""
    lines = [
        "# 补采下载状态",
        "",
        f"- 目标账号数：{report['target_count']}",
        f"- 需刷新数据集账号数：{report['needs_dataset_refresh_count']}",
        "",
        "| 账号 | 优先级 | 目标严格有效 | 本地 MP4 | processed 视频 | 需刷新 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in report["accounts"]:
        lines.append(
            f"| {item['source_name']} | {item['backfill_priority']} | {item['target_valid_video_count']} | {item['local_mp4_count']} | {item['processed_video_count']} | {item['needs_dataset_refresh']} |"
        )
    return "\n".join(lines) + "\n"


def _resolve_path(workspace: Path, value: str | Path) -> Path:
    """解析 workspace 相对路径。"""
    path = Path(value)
    return path if path.is_absolute() else workspace / path


if __name__ == "__main__":
    raise SystemExit(main())

"""构建 artifacts 索引与最近运行历史摘要。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

SCAN_DIRS = (
    "collector/comments",
    "collector/video",
    "analysis",
    "run-logs",
    "status",
    "downloader/results",
)
DEFAULT_RECENT_LIMIT = 20
JSON_NAME = "artifact_index.json"
MARKDOWN_NAME = "artifact_index.md"
ERROR_NAME_PARTS = ("error", "err", "stderr", "failed", "failure")


@dataclass(frozen=True)
class FileEntry:
    """保存单个 artifact 文件的标准化元数据。"""

    path: str
    directory: str
    size: int
    modified_at: str
    modified_ts: float


@dataclass(frozen=True)
class DirectorySummary:
    """保存目录级聚合，便于 JSON 与 Markdown 共用。"""

    path: str
    exists: bool
    count: int
    total_bytes: int
    latest_time: str | None
    latest_file: str | None


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数，测试可通过 argv 注入避免依赖全局状态。"""
    parser = argparse.ArgumentParser(description="构建 artifacts 索引")
    parser.add_argument("--artifacts-dir", default="artifacts", help="artifacts 根目录")
    parser.add_argument("--json-output", help="JSON 输出路径，默认写入 artifacts 根目录")
    parser.add_argument("--markdown-output", help="Markdown 输出路径，默认写入 artifacts 根目录")
    parser.add_argument("--limit", type=int, default=DEFAULT_RECENT_LIMIT, help="最近文件数量")
    return parser.parse_args(argv)


def to_iso(timestamp: float) -> str:
    """统一使用本地时区 ISO 时间，方便人工核对文件时间。"""
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def to_relative(path: Path, base: Path) -> str:
    """输出相对 artifacts 根目录的 POSIX 路径，保证跨平台快照稳定。"""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def iter_files(root: Path) -> Iterable[Path]:
    """递归枚举普通文件，跳过目录和异常文件类型。"""
    if not root.exists():
        return
    for item in root.rglob("*"):
        if item.is_file():
            yield item


def build_file_entry(path: Path, directory: str, artifacts_dir: Path) -> FileEntry:
    """从文件系统 stat 结果生成可序列化条目。"""
    stat = path.stat()
    return FileEntry(
        path=to_relative(path, artifacts_dir),
        directory=directory,
        size=stat.st_size,
        modified_at=to_iso(stat.st_mtime),
        modified_ts=stat.st_mtime,
    )


def scan_directory(artifacts_dir: Path, directory: str) -> tuple[DirectorySummary, list[FileEntry]]:
    """扫描单个目标目录并返回目录聚合与文件列表。"""
    root = artifacts_dir / directory
    entries = [build_file_entry(path, directory, artifacts_dir) for path in iter_files(root)]
    latest = max(entries, key=lambda item: item.modified_ts, default=None)
    summary = DirectorySummary(
        path=directory,
        exists=root.exists(),
        count=len(entries),
        total_bytes=sum(item.size for item in entries),
        latest_time=latest.modified_at if latest else None,
        latest_file=latest.path if latest else None,
    )
    return summary, entries


def looks_like_error_log(entry: FileEntry) -> bool:
    """通过文件名识别错误日志，避免读取大日志造成额外 IO。"""
    name = Path(entry.path).name.lower()
    suffix = Path(name).suffix
    return entry.size > 0 and (suffix in {".err", ".log"}) and any(part in name for part in ERROR_NAME_PARTS)


def build_index(artifacts_dir: Path, limit: int) -> dict:
    """构建完整索引对象，包含最近文件、目录聚合和错误日志提示。"""
    summaries: list[DirectorySummary] = []
    entries: list[FileEntry] = []
    for directory in SCAN_DIRS:
        summary, directory_entries = scan_directory(artifacts_dir, directory)
        summaries.append(summary)
        entries.extend(directory_entries)

    recent = sorted(entries, key=lambda item: item.modified_ts, reverse=True)[: max(limit, 0)]
    latest = recent[0] if recent else None
    error_logs = [entry for entry in recent if looks_like_error_log(entry)]
    run_logs = [entry for entry in recent if entry.directory == "run-logs"]
    return serialize_index(artifacts_dir, summaries, entries, recent, latest, error_logs, run_logs)


def serialize_entry(entry: FileEntry) -> dict:
    """去掉内部排序字段，保持公开 JSON 简洁稳定。"""
    return {
        "path": entry.path,
        "directory": entry.directory,
        "size": entry.size,
        "modified_at": entry.modified_at,
    }


def serialize_index(
    artifacts_dir: Path,
    summaries: list[DirectorySummary],
    entries: list[FileEntry],
    recent: list[FileEntry],
    latest: FileEntry | None,
    error_logs: list[FileEntry],
    run_logs: list[FileEntry],
) -> dict:
    """组装最终结构，所有派生字段集中在这里便于测试。"""
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "artifacts_dir": str(artifacts_dir),
        "scan_dirs": list(SCAN_DIRS),
        "totals": {"file_count": len(entries), "total_bytes": sum(item.size for item in entries)},
        "latest_file": serialize_entry(latest) if latest else None,
        "recent_files": [serialize_entry(item) for item in recent],
        "recent_run_logs": [serialize_entry(item) for item in run_logs],
        "directories": [summary.__dict__ for summary in summaries],
        "error_log_warnings": [serialize_entry(item) for item in error_logs],
    }


def markdown_row(values: Sequence[object]) -> str:
    """生成 Markdown 表格行，统一处理空值显示。"""
    return "| " + " | ".join("-" if value is None else str(value) for value in values) + " |"


def render_markdown(index: dict) -> str:
    """渲染人工可读摘要，便于快速查看最近运行历史。"""
    lines = ["# Artifact 索引", ""]
    lines.append(f"生成时间：{index['generated_at']}")
    lines.append(f"文件总数：{index['totals']['file_count']}")
    lines.append(f"总大小：{index['totals']['total_bytes']} bytes")
    latest = index["latest_file"]
    lines.append(f"最新文件：{latest['path'] if latest else '-'}")
    append_directory_table(lines, index["directories"])
    append_recent_table(lines, "最近文件", index["recent_files"])
    append_recent_table(lines, "最近运行日志", index["recent_run_logs"])
    append_error_warnings(lines, index["error_log_warnings"])
    return "\n".join(lines) + "\n"


def append_directory_table(lines: list[str], directories: list[dict]) -> None:
    """追加按目录聚合表，缺失目录也保留便于排查采集链路。"""
    lines.extend(["", "## 按目录聚合", "", "| 目录 | 存在 | 数量 | 最新时间 | 最新文件 |", "| --- | --- | ---: | --- | --- |"])
    for item in directories:
        lines.append(markdown_row([item["path"], item["exists"], item["count"], item["latest_time"], item["latest_file"]]))


def append_recent_table(lines: list[str], title: str, entries: list[dict]) -> None:
    """追加最近文件表；为空时保留明确提示。"""
    lines.extend(["", f"## {title}", ""])
    if not entries:
        lines.append("暂无记录。")
        return
    lines.extend(["| 路径 | 目录 | 大小 | 修改时间 |", "| --- | --- | ---: | --- |"])
    for item in entries:
        lines.append(markdown_row([item["path"], item["directory"], item["size"], item["modified_at"]]))


def append_error_warnings(lines: list[str], warnings: list[dict]) -> None:
    """追加非空错误日志提示，帮助优先定位失败运行。"""
    lines.extend(["", "## 错误日志提示", ""])
    if not warnings:
        lines.append("未发现非空错误日志。")
        return
    for item in warnings:
        lines.append(f"- 非空错误日志：`{item['path']}`（{item['size']} bytes）")


def write_outputs(index: dict, json_output: Path, markdown_output: Path) -> None:
    """写入 JSON 与 Markdown，父目录不存在时自动创建。"""
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_output.write_text(render_markdown(index), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """命令入口，返回状态码便于 unittest 直接调用。"""
    args = parse_args(argv)
    artifacts_dir = Path(args.artifacts_dir)
    json_output = Path(args.json_output) if args.json_output else artifacts_dir / JSON_NAME
    markdown_output = Path(args.markdown_output) if args.markdown_output else artifacts_dir / MARKDOWN_NAME
    index = build_index(artifacts_dir, args.limit)
    write_outputs(index, json_output, markdown_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

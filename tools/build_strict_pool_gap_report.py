from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """生成严格有效池账号覆盖缺口报告。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    input_videos = _resolve_path(workspace, args.input_videos)
    valid_videos = _resolve_path(workspace, args.valid_videos)
    pipeline_log = _resolve_path(workspace, args.pipeline_log)
    json_output = _resolve_path(workspace, args.json_output)
    md_output = _resolve_path(workspace, args.md_output)
    report = build_gap_report(input_videos, valid_videos, pipeline_log)
    _write_outputs(report, json_output, md_output)
    print(json.dumps({"ok": True, "json_output": str(json_output), "md_output": str(md_output)}, ensure_ascii=False, indent=2))
    return 0


def build_gap_report(input_videos: Path, valid_videos: Path, pipeline_log: Path) -> dict[str, Any]:
    """读取输入池、严格池与流水线日志，构建账号覆盖缺口。"""
    input_counts = _count_by_account(_read_csv_rows(input_videos))
    valid_counts = _count_by_account(_read_csv_rows(valid_videos))
    pipeline_summary = _load_pipeline_summary(pipeline_log)
    strict_step = _find_step_payload(pipeline_log, "valid_account_video_counts")
    reason_counts = strict_step.get("filtered_reason_counts", {}) if isinstance(strict_step, dict) else {}
    accounts = _build_account_rows(input_counts, valid_counts)
    return {
        "ok": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "input_videos": str(input_videos),
        "valid_videos": str(valid_videos),
        "pipeline_log": str(pipeline_log),
        "input_video_count": sum(input_counts.values()),
        "valid_video_count": sum(valid_counts.values()),
        "overall_retention_rate": _ratio(sum(valid_counts.values()), sum(input_counts.values())),
        "filtered_reason_counts": reason_counts,
        "pipeline_return_code": pipeline_summary.get("return_code"),
        "accounts": accounts,
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成严格有效池账号覆盖缺口报告。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--input-videos", default="data/processed/videos.csv", help="原始分析视频表。")
    parser.add_argument("--valid-videos", default="data/processed_strict_valid/videos.csv", help="严格有效池视频表。")
    parser.add_argument("--pipeline-log", default="artifacts/status/phase1_pipeline_last_run.log", help="一期流水线最近运行日志。")
    parser.add_argument("--json-output", default="artifacts/status/strict_pool_gap_report.json", help="JSON 输出路径。")
    parser.add_argument("--md-output", default="artifacts/status/strict_pool_gap_report.md", help="Markdown 输出路径。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    """读取 CSV 行；缺文件直接报错，避免误判为空。"""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _count_by_account(rows: list[dict[str, str]]) -> dict[str, int]:
    """按 account_id 统计视频数量。"""
    counts: dict[str, int] = {}
    for row in rows:
        account = (row.get("account_id") or "unknown").strip() or "unknown"
        counts[account] = counts.get(account, 0) + 1
    return counts


def _build_account_rows(input_counts: dict[str, int], valid_counts: dict[str, int]) -> list[dict[str, Any]]:
    """构建账号覆盖行，并按保留率升序排列。"""
    rows = []
    for account in sorted(set(input_counts) | set(valid_counts)):
        input_count = input_counts.get(account, 0)
        valid_count = valid_counts.get(account, 0)
        rows.append({
            "account_id": account,
            "input_video_count": input_count,
            "valid_video_count": valid_count,
            "filtered_video_count": max(input_count - valid_count, 0),
            "retention_rate": _ratio(valid_count, input_count),
            "priority": _priority(valid_count, input_count),
        })
    rows.sort(key=lambda item: (item["retention_rate"], -item["input_video_count"], item["account_id"]))
    return rows


def _load_pipeline_summary(path: Path) -> dict[str, Any]:
    """从流水线日志尾部解析 summary；失败时返回空对象。"""
    data = _extract_last_json_object(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else None
    return data if isinstance(data, dict) else {}


def _find_step_payload(path: Path, required_key: str) -> dict[str, Any]:
    """在日志中寻找包含指定 key 的步骤输出 JSON。"""
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    for payload in _extract_json_objects(text):
        if isinstance(payload, dict) and required_key in payload:
            return payload
    return {}


def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    """从混合日志中提取顶层 JSON 对象。"""
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        brace = text.find("{", index)
        if brace < 0:
            break
        try:
            payload, end = decoder.raw_decode(text[brace:])
        except json.JSONDecodeError:
            index = brace + 1
            continue
        if isinstance(payload, dict):
            objects.append(payload)
        index = brace + end
    return objects


def _extract_last_json_object(text: str) -> dict[str, Any] | None:
    """返回日志中最后一个 JSON 对象。"""
    objects = _extract_json_objects(text)
    return objects[-1] if objects else None


def _write_outputs(report: dict[str, Any], json_output: Path, md_output: Path) -> None:
    """写出 JSON 与 Markdown 报告。"""
    json_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_output.write_text(_render_markdown(report), encoding="utf-8")


def _render_markdown(report: dict[str, Any]) -> str:
    """渲染账号覆盖缺口 Markdown。"""
    lines = [
        "# 严格有效池覆盖缺口报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 输入视频数：{report['input_video_count']}",
        f"- 严格有效视频数：{report['valid_video_count']}",
        f"- 总体保留率：{report['overall_retention_rate']:.2%}",
        f"- 过滤原因：{_format_reason_counts(report.get('filtered_reason_counts', {}))}",
        "",
        "| 账号 | 输入视频 | 严格有效 | 过滤 | 保留率 | 优先级 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["accounts"]:
        lines.append(
            f"| {row['account_id']} | {row['input_video_count']} | {row['valid_video_count']} | {row['filtered_video_count']} | {row['retention_rate']:.2%} | {row['priority']} |"
        )
    return "\n".join(lines) + "\n"


def _format_reason_counts(reason_counts: Any) -> str:
    """格式化过滤原因统计。"""
    if not isinstance(reason_counts, dict) or not reason_counts:
        return "无"
    return "，".join(f"{key}={value}" for key, value in reason_counts.items())


def _priority(valid_count: int, input_count: int) -> str:
    """根据保留量与保留率给出补采优先级。"""
    if input_count <= 0:
        return "低"
    if valid_count == 0 or valid_count < 20:
        return "高"
    if valid_count < 50:
        return "中"
    return "低"


def _ratio(numerator: int, denominator: int) -> float:
    """安全计算比例。"""
    return round(numerator / denominator, 4) if denominator else 0.0


def _resolve_path(workspace: Path, value: str | Path) -> Path:
    """解析 workspace 相对路径。"""
    path = Path(value)
    return path if path.is_absolute() else workspace / path


if __name__ == "__main__":
    raise SystemExit(main())

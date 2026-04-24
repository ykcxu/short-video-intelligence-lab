from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
STATUS_FILES = {
    "project_progress": "artifacts/status/project_progress.json",
    "data_quality_report": "artifacts/status/data_quality_report.json",
    "positive_factors_report": "artifacts/analysis/positive_factors_report.json",
    "positive_factors_strict_valid_report": "artifacts/analysis/positive_factors_strict_valid_report.json",
    "strict_pool_gap_report": "artifacts/status/strict_pool_gap_report.json",
    "strict_pool_backfill_targets": "artifacts/analysis/strict_pool_backfill_targets.json",
}


def main(argv: Sequence[str] | None = None) -> int:
    # 主流程：解析参数、构建总览并输出 JSON/Markdown。
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    json_output = _resolve_output_path(workspace, args.json_output)
    md_output = _resolve_output_path(workspace, args.md_output)
    log_limit = max(args.log_limit, 1)
    summary = _build_run_summary(workspace, log_limit)
    _write_text(json_output, json.dumps(summary, ensure_ascii=False, indent=2))
    _write_text(md_output, _build_markdown(summary))
    print(json.dumps({"ok": True, "json_output": str(json_output), "md_output": str(md_output)}, ensure_ascii=False, indent=2))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    # 解析命令行参数。
    parser = argparse.ArgumentParser(description="生成运行状态总览。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--json-output", default="artifacts/status/run_summary.json", help="JSON 输出路径（可相对 workspace）。")
    parser.add_argument("--md-output", default="artifacts/status/run_summary.md", help="Markdown 输出路径（可相对 workspace）。")
    parser.add_argument("--log-limit", type=int, default=10, help="扫描最近日志数量上限。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _build_run_summary(workspace: Path, log_limit: int) -> dict[str, Any]:
    # 汇总状态文件与日志扫描结果，并收集 warning。
    warnings: list[str] = []
    status = _load_status_reports(workspace, warnings)
    logs = _scan_recent_logs(workspace, log_limit, warnings)
    return {
        "ok": True,
        "version": "v1",
        "generated_at": _utc_now_iso(),
        "workspace": str(workspace),
        "warnings": warnings,
        "status_reports": status,
        "run_logs": logs,
    }


def _load_status_reports(workspace: Path, warnings: list[str]) -> dict[str, dict[str, Any]]:
    # 读取三个状态文件，文件缺失时写入 warning。
    result: dict[str, dict[str, Any]] = {}
    for report_name, relative_path in STATUS_FILES.items():
        path = workspace / relative_path
        payload = _read_optional_json(path)
        if payload is None:
            warnings.append(f"状态文件缺失：{relative_path}")
            result[report_name] = {"path": str(path), "exists": False, "generated_at": "", "highlights": {}}
            continue
        result[report_name] = {
            "path": str(path),
            "exists": True,
            "generated_at": _as_text(payload.get("generated_at")),
            "highlights": _build_report_highlights(report_name, payload),
        }
    return result


def _build_report_highlights(report_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    # 按报告类型提取关键指标，避免在总览里复制大体量原始数据。
    if report_name == "project_progress":
        return _project_progress_highlights(payload)
    if report_name == "data_quality_report":
        return _data_quality_highlights(payload)
    if report_name in {"positive_factors_report", "positive_factors_strict_valid_report"}:
        return _positive_factors_highlights(payload)
    if report_name == "strict_pool_gap_report":
        return _strict_pool_gap_highlights(payload)
    if report_name == "strict_pool_backfill_targets":
        return _target_list_highlights(payload)
    return {}


def _project_progress_highlights(payload: dict[str, Any]) -> dict[str, Any]:
    # 提取项目进度报告中的核心进度指标。
    progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
    accounts = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
    return {
        "download_goal_progress": progress.get("download_goal_progress"),
        "detail_coverage_progress": progress.get("detail_coverage_progress"),
        "comment_quality_progress": progress.get("comment_quality_progress"),
        "download_goal_downloaded_videos": progress.get("download_goal_downloaded_videos"),
        "download_goal_target_videos": progress.get("download_goal_target_videos"),
        "account_count": len(accounts),
    }


def _data_quality_highlights(payload: dict[str, Any]) -> dict[str, Any]:
    # 提取数据质量报告中的异常与覆盖关键统计。
    invalid = payload.get("invalid_video_id") if isinstance(payload.get("invalid_video_id"), dict) else {}
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    anomaly = payload.get("detail_metric_anomalies") if isinstance(payload.get("detail_metric_anomalies"), dict) else {}
    return {
        "invalid_video_id_count": invalid.get("count"),
        "download_without_detail_count": coverage.get("download_without_detail_count"),
        "detail_without_download_count": coverage.get("detail_without_download_count"),
        "detail_metric_anomalies_count": anomaly.get("count"),
    }


def _positive_factors_highlights(payload: dict[str, Any]) -> dict[str, Any]:
    # 提取正向因素报告中的规模指标。
    account_entries = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
    return {
        "account_count": payload.get("account_count", len(account_entries)),
        "top_n": payload.get("top_n"),
        "dataset_rows": payload.get("dataset_rows", payload.get("sample_count")),
    }


def _strict_pool_gap_highlights(payload: dict[str, Any]) -> dict[str, Any]:
    # 提取严格有效池覆盖缺口中的关键统计。
    accounts = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
    high_priority = [item for item in accounts if isinstance(item, dict) and item.get("priority") == "高"]
    return {
        "input_video_count": payload.get("input_video_count"),
        "valid_video_count": payload.get("valid_video_count"),
        "overall_retention_rate": payload.get("overall_retention_rate"),
        "high_priority_account_count": len(high_priority),
    }


def _target_list_highlights(payload: dict[str, Any]) -> dict[str, Any]:
    # 提取补采目标列表的规模和优先级分布。
    targets = payload.get("_items") if isinstance(payload.get("_items"), list) else []
    high_priority = [item for item in targets if isinstance(item, dict) and item.get("backfill_priority") == "高"]
    return {"target_count": len(targets), "high_priority_target_count": len(high_priority)}


def _scan_recent_logs(workspace: Path, log_limit: int, warnings: list[str]) -> dict[str, Any]:
    # 扫描 run-logs 最新文件，识别 batch 相关日志和非空错误日志。
    log_dir = workspace / "artifacts" / "run-logs"
    if not log_dir.exists():
        warnings.append("日志目录缺失：artifacts/run-logs")
        return _empty_log_summary(log_dir, log_limit)

    files = sorted((item for item in log_dir.iterdir() if item.is_file()), key=lambda item: item.stat().st_mtime, reverse=True)
    latest = files[:log_limit]
    recent_logs: list[dict[str, Any]] = []
    batch_logs: list[str] = []
    non_empty_error_logs: list[str] = []
    error_log_count = 0

    for path in latest:
        entry = _build_log_entry(path, workspace)
        recent_logs.append(entry)
        if entry["is_batch_related"]:
            batch_logs.append(entry["name"])
        if entry["is_error_log"]:
            error_log_count += 1
            if entry["non_empty"]:
                non_empty_error_logs.append(entry["name"])

    return {
        "log_dir": str(log_dir),
        "scanned_file_count": len(files),
        "included_file_count": len(latest),
        "log_limit": log_limit,
        "recent_batch_log_count": len(batch_logs),
        "recent_batch_logs": batch_logs,
        "error_log_count": error_log_count,
        "non_empty_error_log_count": len(non_empty_error_logs),
        "has_non_empty_error_log": len(non_empty_error_logs) > 0,
        "non_empty_error_logs": non_empty_error_logs,
        "recent_logs": recent_logs,
    }


def _build_log_entry(path: Path, workspace: Path) -> dict[str, Any]:
    # 构建单条日志元数据，供 JSON 和 Markdown 共用。
    lower_name = path.name.lower()
    size = path.stat().st_size
    return {
        "name": path.name,
        "relative_path": str(path.relative_to(workspace)),
        "modified_at": _iso_from_timestamp(path.stat().st_mtime),
        "size_bytes": size,
        "non_empty": size > 0,
        "is_error_log": lower_name.endswith(".err.log") or "error" in lower_name,
        "is_batch_related": "batch" in lower_name,
    }


def _build_markdown(summary: dict[str, Any]) -> str:
    # 生成总览 Markdown，提供可快速阅读的人类视图。
    status_reports = summary["status_reports"]
    run_logs = summary["run_logs"]
    lines = [
        "# 运行状态总览",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 工作区：{summary['workspace']}",
        "",
        "## 状态文件",
    ]
    for name in STATUS_FILES:
        item = status_reports[name]
        exists_text = "存在" if item["exists"] else "缺失"
        lines.append(f"- {name}: {exists_text}（{item['path']}）")
        if item["exists"]:
            highlights_text = "，".join(f"{key}={value}" for key, value in item["highlights"].items())
            lines.append(f"  - 指标：{highlights_text if highlights_text else '无'}")

    lines.extend(
        [
            "",
            "## 最近日志",
            f"- 扫描文件总数：{run_logs['scanned_file_count']}（纳入最近 {run_logs['included_file_count']} 条，limit={run_logs['log_limit']}）",
            f"- 最近批处理相关日志数：{run_logs['recent_batch_log_count']}",
            f"- 错误日志数：{run_logs['error_log_count']}（非空 {run_logs['non_empty_error_log_count']}）",
            "",
            "### 最新日志明细",
        ]
    )

    for item in run_logs["recent_logs"]:
        lines.append(
            f"- {item['name']} | non_empty={item['non_empty']} | is_error={item['is_error_log']} | is_batch={item['is_batch_related']} | {item['modified_at']}"
        )

    if not run_logs["recent_logs"]:
        lines.append("- 无")

    lines.extend(["", "## Warnings"])
    if summary["warnings"]:
        for warning in summary["warnings"]:
            lines.append(f"- {warning}")
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def _empty_log_summary(log_dir: Path, log_limit: int) -> dict[str, Any]:
    # 返回空日志摘要结构，确保输出字段稳定。
    return {
        "log_dir": str(log_dir),
        "scanned_file_count": 0,
        "included_file_count": 0,
        "log_limit": log_limit,
        "recent_batch_log_count": 0,
        "recent_batch_logs": [],
        "error_log_count": 0,
        "non_empty_error_log_count": 0,
        "has_non_empty_error_log": False,
        "non_empty_error_logs": [],
        "recent_logs": [],
    }


def _resolve_output_path(workspace: Path, output: str) -> Path:
    # 输出路径支持绝对路径和 workspace 相对路径。
    path = Path(output)
    return path if path.is_absolute() else workspace / path


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    # 读取 JSON 对象，解析失败或结构异常视为缺失。
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(payload, list):
        return {"_items": payload}
    return payload if isinstance(payload, dict) else None


def _write_text(path: Path, content: str) -> None:
    # 写入文本并自动创建父目录。
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _iso_from_timestamp(value: float) -> str:
    # 将文件时间戳统一转为 UTC ISO 字符串。
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _utc_now_iso() -> str:
    # 生成当前 UTC 时间戳。
    return datetime.now(timezone.utc).isoformat()


def _as_text(value: Any) -> str:
    # 将可选字段安全转为文本。
    return str(value).strip() if value is not None else ""


if __name__ == "__main__":
    raise SystemExit(main())

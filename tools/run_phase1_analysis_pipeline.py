from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """顺序执行一期分析流水线，任一步失败即终止。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    steps = _build_steps(workspace, top_n=args.top_n, log_limit=args.log_limit, skip_sqlite=args.skip_sqlite)
    result = _run_steps(steps)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["return_code"])


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="顺序执行一期分析流水线。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--top-n", type=int, default=10, help="正向因素报告的 Top N。")
    parser.add_argument("--log-limit", type=int, default=25, help="运行摘要日志保留条数。")
    parser.add_argument("--skip-sqlite", action="store_true", help="跳过 build_dataset 的 --sqlite 参数。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _build_steps(workspace: Path, top_n: int, log_limit: int, skip_sqlite: bool) -> list[dict[str, object]]:
    """按固定顺序构建一期流水线命令。"""
    tool_dir = workspace / "tools"
    python_cmd = sys.executable

    dataset_cmd = [
        python_cmd,
        str(tool_dir / "build_dataset.py"),
        "--workspace",
        str(workspace),
        "--output-dir",
        "data/processed",
    ]
    if not skip_sqlite:
        dataset_cmd.extend(["--sqlite", "data/processed/analysis_dataset.sqlite"])

    return [
        {"name": "build_dataset", "command": dataset_cmd},
        {
            "name": "build_data_quality_report",
            "command": [
                python_cmd,
                str(tool_dir / "build_data_quality_report.py"),
                "--workspace",
                str(workspace),
                "--json-output",
                "artifacts/status/data_quality_report.json",
                "--md-output",
                "artifacts/status/data_quality_report.md",
            ],
        },
        {
            "name": "build_valid_analysis_pool",
            "command": [
                python_cmd,
                str(tool_dir / "build_valid_analysis_pool.py"),
                "--workspace",
                str(workspace),
                "--input-dir",
                "data/processed",
                "--output-dir",
                "data/processed_strict_valid",
                "--quality-report",
                "artifacts/status/data_quality_report.json",
                "--require-homepage-observed",
                "--require-detail-account-mention",
            ],
        },
        {
            "name": "build_positive_factors_report",
            "command": [
                python_cmd,
                str(tool_dir / "build_positive_factors_report.py"),
                "--workspace",
                str(workspace),
                "--input-dir",
                "data/processed_strict_valid",
                "--json-output",
                "artifacts/analysis/positive_factors_strict_valid_report.json",
                "--md-output",
                "artifacts/analysis/positive_factors_strict_valid_report.md",
                "--top-n",
                str(top_n),
            ],
        },
        {
            "name": "build_run_summary",
            "command": [
                python_cmd,
                str(tool_dir / "build_run_summary.py"),
                "--workspace",
                str(workspace),
                "--json-output",
                "artifacts/status/run_summary.json",
                "--md-output",
                "artifacts/status/run_summary.md",
                "--log-limit",
                str(log_limit),
            ],
        },
    ]


def _run_steps(steps: Sequence[dict[str, object]]) -> dict[str, object]:
    """执行所有步骤并返回汇总结果。"""
    summaries: list[dict[str, object]] = []
    overall_code = 0
    start_all = time.perf_counter()

    for step in steps:
        summary = _run_step(step)
        summaries.append(summary)
        if summary["return_code"] != 0:
            overall_code = int(summary["return_code"])
            break

    return {
        "return_code": overall_code,
        "duration_sec": round(time.perf_counter() - start_all, 3),
        "steps": summaries,
    }


def _run_step(step: dict[str, object]) -> dict[str, object]:
    """执行单个步骤并透传日志。"""
    name = str(step["name"])
    command = list(step["command"])
    command_text = subprocess.list2cmdline(command)
    start = time.perf_counter()
    completed = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True)
    _forward_output(completed)
    duration = round(time.perf_counter() - start, 3)
    return {
        "name": name,
        "return_code": completed.returncode,
        "duration_sec": duration,
        "command": command_text,
    }


def _forward_output(completed: subprocess.CompletedProcess[str]) -> None:
    """将子进程输出转发到当前进程 stderr，避免吞掉错误。"""
    if completed.stdout:
        sys.stderr.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)


if __name__ == "__main__":
    raise SystemExit(main())

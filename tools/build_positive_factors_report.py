from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from short_video_intel.analysis.positive_factors import build_positive_factors_report
from short_video_intel.analysis.positive_factors import write_positive_factors_outputs

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """构建账号正向因素分析报告并写出 JSON/Markdown。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    input_dir = _resolve_path(args.input_dir, workspace)
    json_output = _resolve_path(args.json_output, workspace)
    md_output = _resolve_path(args.md_output, workspace)
    try:
        report = build_positive_factors_report(input_dir, top_n=args.top_n)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    write_positive_factors_outputs(report, json_output=json_output, md_output=md_output)
    print(
        json.dumps(
            {
                "ok": True,
                "input_dir": str(input_dir),
                "json_output": str(json_output),
                "md_output": str(md_output),
                "account_count": report.get("account_count", 0),
                "top_n": report.get("top_n", args.top_n),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成账号正向因素分析报告。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--input-dir", default="data/processed", help="输入 CSV 目录。")
    parser.add_argument("--json-output", default="artifacts/analysis/positive_factors_report.json", help="JSON 输出路径。")
    parser.add_argument("--md-output", default="artifacts/analysis/positive_factors_report.md", help="Markdown 输出路径。")
    parser.add_argument("--top-n", type=int, default=5, help="每个账号输出 top/bottom 视频与关键词数量。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _resolve_path(path_text: str | Path, workspace: Path) -> Path:
    """相对路径基于 workspace 解析，绝对路径保持原值。"""
    path = Path(path_text)
    if path.is_absolute():
        return path.resolve()
    return (workspace / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())

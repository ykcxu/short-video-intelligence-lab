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

from short_video_intel.analysis.valid_pool import build_valid_analysis_pool


def main(argv: Sequence[str] | None = None) -> int:
    """构建有效分析池：过滤视频、指标与评论三类 CSV。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    input_dir = _resolve_path(args.input_dir, workspace)
    output_dir = _resolve_path(args.output_dir, workspace)
    quality_report_path = _resolve_path(args.quality_report, workspace)
    summary = build_valid_analysis_pool(
        workspace=workspace,
        input_dir=input_dir,
        output_dir=output_dir,
        quality_report_path=quality_report_path,
        keep_suspicious=bool(args.keep_suspicious),
        require_homepage_observed=bool(args.require_homepage_observed),
        require_detail_account_mention=bool(args.require_detail_account_mention),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="构建有效分析池（valid CSV 子集）。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--input-dir", default="data/processed", help="输入目录，包含 videos.csv 等文件。")
    parser.add_argument("--output-dir", default="data/processed", help="输出目录，写入 valid_*.csv。")
    parser.add_argument("--quality-report", default="artifacts/status/data_quality_report.json", help="数据质量报告 JSON 路径。")
    parser.add_argument("--keep-suspicious", action="store_true", help="保留质量报告中的可疑指标视频。")
    parser.add_argument("--require-homepage-observed", action="store_true", help="仅保留账号主页采集产物中出现过的视频。")
    parser.add_argument(
        "--require-detail-account-mention",
        action="store_true",
        help="仅保留详情文本里出现归一化账号名的视频。",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _resolve_path(path_text: str | Path, workspace: Path) -> Path:
    """相对路径基于 workspace 解析，绝对路径保持原值。"""
    path = Path(path_text)
    if path.is_absolute():
        return path.resolve()
    return (workspace / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())

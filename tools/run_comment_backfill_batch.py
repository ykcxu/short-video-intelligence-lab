from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = "artifacts/collector/comment_backfill_targets.json"
DEFAULT_LOG_DIR = "artifacts/run-logs"
TAIL_LINES = 20

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """批量执行评论补采，并输出可复核的 JSON 摘要。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    targets_path = _resolve_path(workspace, args.targets)
    log_output = _resolve_log_output(workspace, args.log_output)
    targets = _apply_limit(_load_targets(targets_path), args.limit)

    if args.dry_run:
        payload = _build_dry_run_summary(workspace, targets_path, log_output, targets)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    results = _run_targets(args, workspace, targets, log_output)
    payload = _build_run_summary(workspace, targets_path, log_output, results)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数，保持默认值贴近现有采集产物路径。"""
    parser = argparse.ArgumentParser(description="批量执行评论补采目标。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--targets", type=Path, default=Path(DEFAULT_TARGETS), help="目标 JSON 路径。")
    parser.add_argument("--limit", type=int, default=None, help="最多执行多少个目标。")
    parser.add_argument("--max-pages", type=int, default=3, help="每个视频最多抓取页数。")
    parser.add_argument("--retry-limit", type=int, default=0, help="失败后的最大重试次数。")
    parser.add_argument("--session-name", required=True, help="浏览器会话名。")
    parser.add_argument("--config", type=Path, default=None, help="CLI 配置文件路径。")
    parser.add_argument("--log-output", type=Path, default=None, help="批处理日志输出路径。")
    parser.add_argument("--dry-run", action="store_true", help="只输出计划，不实际调用 CLI。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _run_targets(
    args: argparse.Namespace,
    workspace: Path,
    targets: list[dict[str, Any]],
    log_output: Path,
) -> list[dict[str, Any]]:
    """逐条执行目标，单个失败不阻断后续补采。"""
    results: list[dict[str, Any]] = []
    _append_log(log_output, f"comment backfill batch started: {datetime.now().isoformat()}\n")
    for index, target in enumerate(targets, start=1):
        command = _build_command(args, workspace, target)
        result = _run_one_target(command, workspace, target, args.retry_limit)
        results.append(result)
        _append_log(log_output, _format_log_entry(index, command, result))
    return results


def _run_one_target(
    command: list[str],
    workspace: Path,
    target: dict[str, Any],
    retry_limit: int,
) -> dict[str, Any]:
    """执行单个目标；retry_limit 表示失败后的额外重试次数。"""
    attempts = 0
    completed = None
    max_attempts = max(0, retry_limit) + 1
    while attempts < max_attempts:
        attempts += 1
        completed = subprocess.run(command, cwd=workspace, capture_output=True, text=True)
        if completed.returncode == 0:
            break
    return _build_result(target, completed, attempts)


def _build_command(args: argparse.Namespace, workspace: Path, target: dict[str, Any]) -> list[str]:
    """生成固定 CLI 调用，避免 shell 拼接带来的注入风险。"""
    command = ["py", "-3.11", "-m", "short_video_intel.cli", "--workspace", str(workspace)]
    if args.config is not None:
        command.extend(["--config", str(_resolve_path(workspace, args.config))])
    command.extend(["--session-name", args.session_name])
    command.extend(["crawl-video-comments", "--video-url", str(target["video_url"])])
    command.extend(["--max-pages", str(args.max_pages)])
    return command


def _load_targets(path: Path) -> list[dict[str, Any]]:
    """读取目标文件，兼容顶层 targets 字段或直接列表两种结构。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_targets = payload.get("targets") if isinstance(payload, dict) else payload
    if not isinstance(raw_targets, list):
        raise ValueError(f"目标文件缺少 targets 列表：{path}")
    return [_normalize_target(item) for item in raw_targets]


def _normalize_target(item: Any) -> dict[str, Any]:
    """规范单条目标，确保后续执行至少有 video_url。"""
    if not isinstance(item, dict):
        raise ValueError("目标项必须是对象。")
    video_url = str(item.get("video_url") or "").strip()
    if not video_url:
        raise ValueError("目标项缺少 video_url。")
    video_id = str(item.get("video_id") or _extract_video_id(video_url)).strip()
    return {"video_id": video_id, "video_url": video_url}


def _build_result(target: dict[str, Any], completed: subprocess.CompletedProcess[str] | None, attempts: int) -> dict[str, Any]:
    """把 subprocess 结果压缩成稳定摘要，避免 stdout/stderr 过大。"""
    stdout = completed.stdout if completed is not None else ""
    stderr = completed.stderr if completed is not None else ""
    return {
        "video_id": target["video_id"],
        "video_url": target["video_url"],
        "return_code": completed.returncode if completed is not None else -1,
        "attempts": attempts,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }


def _build_dry_run_summary(workspace: Path, targets_path: Path, log_output: Path, targets: list[dict[str, Any]]) -> dict[str, Any]:
    """生成 dry-run 摘要；不创建日志、不调用子进程。"""
    return {
        "ok": True,
        "dry_run": True,
        "workspace": str(workspace),
        "targets_path": str(targets_path),
        "log_output": str(log_output),
        "planned_count": len(targets),
        "targets": targets,
    }


def _build_run_summary(workspace: Path, targets_path: Path, log_output: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
    """生成实际执行摘要，用 return_code 汇总整体状态。"""
    failed = [item for item in results if item["return_code"] != 0]
    return {
        "ok": not failed,
        "dry_run": False,
        "workspace": str(workspace),
        "targets_path": str(targets_path),
        "log_output": str(log_output),
        "planned_count": len(results),
        "failed_count": len(failed),
        "results": results,
    }


def _format_log_entry(index: int, command: list[str], result: dict[str, Any]) -> str:
    """格式化单条执行日志，便于人工排查失败目标。"""
    return "\n".join(
        [
            f"\n[{index}] video_id={result['video_id']} return_code={result['return_code']}",
            "command=" + json.dumps(command, ensure_ascii=False),
            "stdout_tail:\n" + result["stdout_tail"],
            "stderr_tail:\n" + result["stderr_tail"],
            "",
        ]
    )


def _append_log(path: Path, content: str) -> None:
    """追加写日志，目录不存在时自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(content)


def _resolve_log_output(workspace: Path, value: Path | None) -> Path:
    """解析日志路径；未指定时使用带时间戳的默认文件名。"""
    if value is not None:
        return _resolve_path(workspace, value)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return workspace / DEFAULT_LOG_DIR / f"comment_backfill_batch_{stamp}.log"


def _resolve_path(workspace: Path, value: Path) -> Path:
    """相对路径按 workspace 解析，绝对路径原样保留。"""
    return value if value.is_absolute() else workspace / value


def _apply_limit(items: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    """按 limit 截断目标列表。"""
    if limit is None:
        return list(items)
    return list(items[: max(0, int(limit))])


def _tail(text: str) -> str:
    """只保留最后若干行，控制摘要体积。"""
    lines = text.splitlines()
    return "\n".join(lines[-TAIL_LINES:])


def _extract_video_id(video_url: str) -> str:
    """从常见抖音视频 URL 中提取最后一段路径作为兜底 ID。"""
    marker = "/video/"
    if marker not in video_url:
        return ""
    return video_url.split(marker, 1)[1].split("?", 1)[0].split("#", 1)[0].strip("/")


if __name__ == "__main__":
    raise SystemExit(main())

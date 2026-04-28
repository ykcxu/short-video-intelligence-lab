from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = "artifacts/analysis-inputs/local_video_inputs_strict_all.json"
DEFAULT_STATUS = "artifacts/status/multimodal_full_batch_status.json"
DEFAULT_LOG_DIR = "artifacts/run-logs"
DEFAULT_CHUNK_DIR = "artifacts/analysis-inputs/multimodal_full_chunks"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """分片运行全量多模态分析，支持中断后续跑。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    payload = _load_json(_resolve(workspace, args.inputs))
    items = _extract_items(payload)
    chunks = _write_chunks(workspace, items, args)
    results = _run_chunks(workspace, chunks, args)
    status = _build_status(workspace, items, chunks, results)
    _write_json(_resolve(workspace, args.status), status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["failed_count"] == 0 else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分片执行全量多模态分析。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--inputs", type=Path, default=Path(DEFAULT_INPUT), help="全量多模态输入 JSON。")
    parser.add_argument("--status", type=Path, default=Path(DEFAULT_STATUS), help="状态输出 JSON。")
    parser.add_argument("--chunk-size", type=int, default=16, help="每片视频数。")
    parser.add_argument("--run-id-prefix", default="strict_all_20260428", help="分片 run-id 前缀。")
    parser.add_argument("--model-size", default="tiny", help="ASR 模型大小。")
    parser.add_argument("--chunk-timeout-sec", type=int, default=1800, help="单片最大运行秒数。")
    parser.add_argument("--resume", action="store_true", help="跳过已有成功 fusion 输出的分片。")
    parser.add_argument("--dry-run", action="store_true", help="只生成分片和计划。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _write_chunks(workspace: Path, items: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    """写出稳定分片文件，供 run_multimodal_batch 消费。"""
    chunk_dir = workspace / DEFAULT_CHUNK_DIR
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    size = max(1, args.chunk_size)
    for index, start in enumerate(range(0, len(items), size), start=1):
        chunk_items = items[start : start + size]
        run_id = f"{args.run_id_prefix}_part{index:03d}"
        path = chunk_dir / f"{run_id}.json"
        _write_json(path, {"ok": True, "items": chunk_items, "generated_at": _now(), "run_id": run_id})
        chunks.append({"index": index, "run_id": run_id, "path": path, "count": len(chunk_items)})
    return chunks


def _run_chunks(workspace: Path, chunks: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    """顺序执行分片，每片结束立即刷新状态。"""
    results = []
    for chunk in chunks:
        result = _run_one_chunk(workspace, chunk, args)
        results.append(result)
        partial = _build_status(workspace, [], chunks, results)
        _write_json(_resolve(workspace, args.status), partial)
    return results


def _run_one_chunk(workspace: Path, chunk: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """执行单个分片，带超时和 resume 判断。"""
    output = workspace / "artifacts" / "analysis" / f"multimodal_fusion_batch_{chunk['run_id']}.json"
    if args.resume and output.exists():
        return _result(chunk, 0, "skipped_existing", "", str(output), 0)
    if args.dry_run:
        return _result(chunk, 0, "dry_run", "", str(output), 0)
    command = _chunk_command(workspace, chunk, args)
    start = time.perf_counter()
    try:
        completed = subprocess.run(command, cwd=workspace, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=max(1, args.chunk_timeout_sec))
        return _result(chunk, completed.returncode, completed.stdout, completed.stderr, str(output), time.perf_counter() - start)
    except subprocess.TimeoutExpired as exc:
        return _result(chunk, 124, exc.stdout or "", f"分片超时：{args.chunk_timeout_sec}s", str(output), time.perf_counter() - start)


def _chunk_command(workspace: Path, chunk: dict[str, Any], args: argparse.Namespace) -> list[str]:
    """构造安全的分片命令，避免 shell 字符串拼接。"""
    return [
        sys.executable,
        str(workspace / "tools" / "run_multimodal_batch.py"),
        "--workspace",
        str(workspace),
        "--inputs",
        str(chunk["path"]),
        "--max-per-account",
        "999",
        "--run-id",
        str(chunk["run_id"]),
        "--model-size",
        args.model_size,
    ]


def _result(chunk: dict[str, Any], code: int, stdout: str, stderr: str, output: str, duration: float) -> dict[str, Any]:
    return {
        "index": chunk["index"],
        "run_id": chunk["run_id"],
        "count": chunk["count"],
        "return_code": code,
        "duration_sec": round(duration, 3),
        "fusion_output": output,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }


def _build_status(workspace: Path, items: list[dict[str, Any]], chunks: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    done = [item for item in results if item["return_code"] == 0]
    failed = [item for item in results if item["return_code"] != 0]
    return {
        "ok": not failed,
        "generated_at": _now(),
        "workspace": str(workspace),
        "total_video_count": len(items) if items else sum(item["count"] for item in chunks),
        "chunk_count": len(chunks),
        "completed_chunk_count": len(done),
        "failed_count": len(failed),
        "processed_video_count": sum(item["count"] for item in done),
        "results": results,
    }


def _extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("输入缺少 items 列表")
    return [dict(item) for item in items if isinstance(item, dict)]


def _tail(text: str, lines: int = 20) -> str:
    return "\n".join(str(text or "").splitlines()[-lines:])


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve(workspace: Path, path: Path) -> Path:
    return path if path.is_absolute() else workspace / path


def _now() -> str:
    return datetime.now().astimezone().isoformat()


if __name__ == "__main__":
    raise SystemExit(main())

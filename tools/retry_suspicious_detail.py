from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
VIDEO_ID_PATTERN = re.compile(r"/video/(\d+)")

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from short_video_intel.config import load_config
from short_video_intel.orchestrator import Orchestrator


def main() -> int:
    parser = argparse.ArgumentParser(description="重跑可疑的视频 detail 指标。")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=ROOT / "config.local.yaml")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--status-file",
        type=Path,
        default=ROOT / "artifacts" / "status" / "detail_quality_retry_status.json",
    )
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    suspicious_items = _find_suspicious_detail_items(workspace)
    if args.limit is not None:
        suspicious_items = suspicious_items[: max(0, int(args.limit))]

    status_file = args.status_file if args.status_file.is_absolute() else workspace / args.status_file
    status_file.parent.mkdir(parents=True, exist_ok=True)
    _write_status(status_file, _build_status(workspace, suspicious_items))

    config = load_config(args.config, workspace=workspace)
    orchestrator = Orchestrator(config)
    orchestrator.bootstrap()

    success = 0
    failed = 0
    total = len(suspicious_items)
    print(f"detail quality retry start: total={total}", flush=True)
    for index, item in enumerate(suspicious_items, start=1):
        latest = _retry_one(orchestrator, item)
        if latest.get("ok"):
            success += 1
        else:
            failed += 1
        status = _build_status(workspace, suspicious_items, index, success, failed, latest)
        _write_status(status_file, status)
        print(
            f"[{index:03d}/{total:03d}] success={success} failed={failed} "
            f"video={item['video_id']} old_value={item['old_value']}",
            flush=True,
        )
    print("detail quality retry completed", flush=True)
    return 0


def _retry_one(orchestrator: Orchestrator, item: dict[str, Any]) -> dict[str, Any]:
    # 业务原因：只重跑明确可疑项，避免全量重复访问浪费时间和风险。
    try:
        result = orchestrator.crawl_video_detail(str(item["video_url"]))
    except Exception as exc:  # pragma: no cover - 运行时兜底记录错误，不伪装成功
        return {"ok": False, "video_url": item["video_url"], "error": f"{type(exc).__name__}: {exc}"}
    metrics = result.get("metrics") or {}
    return {
        "ok": bool(result.get("artifact_path")),
        "video_url": item["video_url"],
        "video_id": item["video_id"],
        "old_value": item["old_value"],
        "artifact_path": result.get("artifact_path"),
        "metrics": metrics,
        "still_suspicious": _is_equal_nonzero_metrics(metrics),
        "warnings": result.get("warnings"),
    }


def _find_suspicious_detail_items(workspace: Path) -> list[dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    detail_root = workspace / "artifacts" / "collector" / "video"
    if not detail_root.exists():
        return []
    for path in sorted(detail_root.glob("video_detail_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        payload = _load_json(path)
        video_url = str(payload.get("video_url") or "")
        video_id = _extract_video_id(video_url)
        if not video_id or video_id == "undefined" or video_id in items:
            continue
        metrics = payload.get("metrics") or {}
        if not _is_equal_nonzero_metrics(metrics):
            continue
        items[video_id] = {
            "video_id": video_id,
            "video_url": video_url,
            "old_value": int(metrics.get("like_count") or 0),
            "source_path": str(path),
        }
    return list(items.values())


def _is_equal_nonzero_metrics(metrics: Mapping[str, Any] | dict[str, Any]) -> bool:
    values = [int(metrics.get(key) or 0) for key in ("view_count", "like_count", "comment_count", "share_count")]
    return values[0] > 0 and values[0] == values[1] == values[2] == values[3]


def _extract_video_id(video_url: str) -> str:
    match = VIDEO_ID_PATTERN.search(video_url)
    return match.group(1) if match else ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_status(
    workspace: Path,
    items: list[dict[str, Any]],
    done: int = 0,
    success: int = 0,
    failed: int = 0,
    latest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total = len(items)
    return {
        "ok": True,
        "analysis_type": "detail_quality_retry",
        "workspace": str(workspace),
        "total": total,
        "done": done,
        "success": success,
        "failed": failed,
        "remaining": max(0, total - done),
        "latest": latest,
        "preview": items[:5],
    }


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

import sys

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from short_video_intel.config import load_config
from short_video_intel.orchestrator import Orchestrator


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill video detail coverage from all downloaded videos.")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="Workspace root.")
    parser.add_argument("--config", type=Path, default=ROOT / "config.local.yaml", help="Config file path.")
    parser.add_argument("--status-file", type=Path, default=ROOT / "artifacts" / "status" / "detail_backfill_status.json")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on how many missing videos to process.")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    config = load_config(args.config, workspace=workspace)
    orchestrator = Orchestrator(config)
    orchestrator.bootstrap()

    allowed_sources = _load_target_account_names(workspace / "inputs" / "douyin_homepages_seed.tsv")
    downloaded_videos = _load_downloaded_videos(workspace, allowed_sources=allowed_sources)
    existing_detail_urls = _load_existing_detail_urls(workspace)

    missing = [item for item in downloaded_videos if item["video_url"] not in existing_detail_urls]
    missing.sort(key=lambda item: (item.get("source_name") or "", item.get("video_id") or ""))
    if args.limit is not None:
        missing = missing[: max(0, int(args.limit))]

    status_file: Path = args.status_file if args.status_file.is_absolute() else workspace / args.status_file
    status_file.parent.mkdir(parents=True, exist_ok=True)

    total = len(missing)
    done = 0
    success = 0
    failed = 0
    status_payload: dict[str, Any] = {
        "ok": True,
        "analysis_type": "detail_backfill_all",
        "workspace": str(workspace),
        "total": total,
        "done": done,
        "success": success,
        "failed": failed,
        "remaining": total - done,
        "latest": None,
        "missing_preview": missing[:5],
    }
    _write_status(status_file, status_payload)

    print(f"detail backfill start: total_missing={total}", flush=True)
    for index, item in enumerate(missing, start=1):
        video_url = str(item["video_url"])
        source_name = str(item.get("source_name") or "")
        video_id = str(item.get("video_id") or "")
        try:
            result = orchestrator.crawl_video_detail(video_url)
            artifact_path = result.get("artifact_path")
            ok = bool(artifact_path)
            if ok:
                success += 1
            else:
                failed += 1
            latest = {
                "video_url": video_url,
                "video_id": video_id,
                "source_name": source_name,
                "ok": ok,
                "artifact_path": artifact_path,
                "metrics": result.get("metrics"),
                "warnings": result.get("warnings"),
            }
        except Exception as exc:  # pragma: no cover - runtime safety
            failed += 1
            latest = {
                "video_url": video_url,
                "video_id": video_id,
                "source_name": source_name,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

        done = index
        remaining = total - done
        bar = _render_progress_bar(done / total if total else 1.0)
        print(
            f"[{done:04d}/{total:04d}] {bar} success={success} failed={failed} "
            f"source={source_name} video={video_id}",
            flush=True,
        )
        status_payload.update(
            {
                "done": done,
                "success": success,
                "failed": failed,
                "remaining": remaining,
                "latest": latest,
            }
        )
        _write_status(status_file, status_payload)

    status_payload["ok"] = True
    _write_status(status_file, status_payload)
    print("detail backfill completed", flush=True)
    return 0


def _load_downloaded_videos(workspace: Path, *, allowed_sources: set[str]) -> list[dict[str, Any]]:
    results_root = workspace / "artifacts" / "downloader" / "results"
    items: dict[str, dict[str, Any]] = {}
    if not results_root.exists():
        return []

    for path in sorted(results_root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        results = payload.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            if str(result.get("status") or "").lower() != "success":
                continue
            video_url = str(result.get("video_url") or "").strip()
            if not video_url:
                continue
            video_id = str(result.get("video_id") or "").strip()
            source_name = str(result.get("source_name") or "").strip()
            if allowed_sources and source_name not in allowed_sources:
                continue
            items.setdefault(
                video_url,
                {
                    "video_url": video_url,
                    "video_id": video_id,
                    "source_name": source_name,
                    "output_path": str(result.get("output_path") or ""),
                    "download_result_path": str(path),
                },
            )
    return list(items.values())


def _load_target_account_names(seed_path: Path) -> set[str]:
    if not seed_path.exists():
        return set()
    with seed_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        names: set[str] = set()
        for row in reader:
            if not isinstance(row, dict):
                continue
            name = str(
                row.get("账号名")
                or row.get("source_name")
                or row.get("name")
                or ""
            ).strip()
            if name:
                names.add(name)
        return names


def _load_existing_detail_urls(workspace: Path) -> set[str]:
    detail_root = workspace / "artifacts" / "collector" / "video"
    urls: set[str] = set()
    if not detail_root.exists():
        return urls

    for path in sorted(detail_root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        video_url = str(payload.get("video_url") or "").strip()
        if video_url:
            urls.add(video_url)
    return urls


def _render_progress_bar(progress: float, width: int = 24) -> str:
    bounded = max(0.0, min(1.0, progress))
    filled = int(round(bounded * width))
    filled = min(width, max(0, filled))
    return f"[{'█' * filled}{'░' * (width - filled)}] {bounded * 100:5.1f}%"


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

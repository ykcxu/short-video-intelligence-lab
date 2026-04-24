from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
VIDEO_URL_TEMPLATE = "https://www.douyin.com/video/{video_id}"
VIDEO_ID_IN_URL = re.compile(r"/video/([^/?#]+)")
VIDEO_ID_IN_FILE = re.compile(r"_([^_]+)$")

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from short_video_intel.config import load_config
from short_video_intel.orchestrator import Orchestrator


def main(argv: Sequence[str] | None = None) -> int:
    """扫描本地下载与已有详情，只处理真正缺失的详情数据。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    downloaded_items = _scan_downloaded_videos(workspace / "downloads" / "artifact")
    existing_ids = _scan_existing_detail_ids(workspace / "artifacts" / "collector" / "video")
    missing_items = _find_missing_items(downloaded_items, existing_ids)
    planned_items = _apply_limit(missing_items, args.limit)
    status = _build_status(workspace, downloaded_items, existing_ids, missing_items, planned_items)
    if args.dry_run:
        _print_json(status)
        return 0

    config = load_config(args.config, workspace=workspace)
    orchestrator = Orchestrator(config)
    orchestrator.bootstrap()
    status["results"] = _backfill_missing_details(orchestrator, planned_items)
    status["processed_count"] = len(status["results"])
    _print_json(status)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="只补齐缺失视频详情的工具脚本。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--config", type=Path, default=ROOT / "config.local.yaml", help="配置文件路径。")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少个缺口。")
    parser.add_argument("--dry-run", action="store_true", help="仅输出缺口状态，不实际抓取。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _find_missing_items(
    downloaded_items: list[dict[str, str]],
    existing_ids: set[str],
) -> list[dict[str, str]]:
    """对比下载产物和详情产物，找出缺失项。"""
    missing_items = [item for item in downloaded_items if item["video_id"] not in existing_ids]
    missing_items.sort(key=lambda item: item["video_id"])
    return missing_items


def _scan_downloaded_videos(download_root: Path) -> list[dict[str, str]]:
    """扫描下载目录里的 mp4，并从文件名提取视频 ID。"""
    if not download_root.exists():
        return []
    items: dict[str, dict[str, str]] = {}
    for path in sorted(download_root.rglob("*.mp4")):
        video_id = _extract_video_id_from_file(path)
        if not video_id:
            continue
        items.setdefault(
            video_id,
            {
                "video_id": video_id,
                "video_url": VIDEO_URL_TEMPLATE.format(video_id=video_id),
                "file_path": str(path),
            },
        )
    return list(items.values())


def _scan_existing_detail_ids(detail_root: Path) -> set[str]:
    """扫描已有详情产物，并统一抽取视频 ID。"""
    if not detail_root.exists():
        return set()
    existing_ids: set[str] = set()
    for path in sorted(detail_root.glob("video_detail_*.json")):
        payload = _load_json(path)
        video_id = _extract_video_id_from_payload(payload)
        if video_id:
            existing_ids.add(video_id)
    return existing_ids


def _extract_video_id_from_file(path: Path) -> str:
    """按现有下载命名规则，从文件名末尾提取视频 ID。"""
    stem = path.stem.strip()
    matched = VIDEO_ID_IN_FILE.search(stem)
    return matched.group(1).strip() if matched else ""


def _extract_video_id_from_payload(payload: dict[str, Any]) -> str:
    """优先使用显式 video_id，没有时再从 video_url 兜底提取。"""
    direct_id = _to_text(payload.get("video_id"))
    if direct_id:
        return direct_id
    return _extract_video_id_from_url(_to_text(payload.get("video_url")))


def _extract_video_id_from_url(video_url: str) -> str:
    """从抖音视频 URL 中提取视频 ID。"""
    matched = VIDEO_ID_IN_URL.search(video_url)
    return matched.group(1).strip() if matched else ""


def _load_json(path: Path) -> dict[str, Any]:
    """安全读取 JSON，坏文件直接跳过。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _apply_limit(items: list[dict[str, str]], limit: int | None) -> list[dict[str, str]]:
    """按 limit 截取本次计划处理的缺口。"""
    if limit is None:
        return list(items)
    return list(items[: max(0, int(limit))])


def _build_status(
    workspace: Path,
    downloaded_items: list[dict[str, str]],
    existing_ids: set[str],
    missing_items: list[dict[str, str]],
    planned_items: list[dict[str, str]],
) -> dict[str, Any]:
    """构造统一的 JSON 状态输出。"""
    return {
        "ok": True,
        "workspace": str(workspace),
        "downloaded_count": len(downloaded_items),
        "existing_detail_count": len(existing_ids),
        "missing_count": len(missing_items),
        "planned_count": len(planned_items),
        "missing_items": missing_items,
        "planned_items": planned_items,
    }


def _backfill_missing_details(
    orchestrator: Orchestrator,
    items: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """按计划列表逐个补齐详情。"""
    results: list[dict[str, Any]] = []
    for item in items:
        results.append(_backfill_one(orchestrator, item))
    return results


def _backfill_one(orchestrator: Orchestrator, item: dict[str, str]) -> dict[str, Any]:
    """补齐单个视频详情，并保留可追踪结果。"""
    # 业务原因：只针对缺失详情的视频补齐，避免对已完成项重复抓取。
    try:
        result = orchestrator.crawl_video_detail(item["video_url"])
    except Exception as exc:  # pragma: no cover - 运行时错误需要原样暴露到结果中
        return {
            "ok": False,
            "video_id": item["video_id"],
            "video_url": item["video_url"],
            "file_path": item["file_path"],
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": bool(result.get("artifact_path")),
        "video_id": item["video_id"],
        "video_url": item["video_url"],
        "file_path": item["file_path"],
        "artifact_path": result.get("artifact_path"),
        "warnings": result.get("warnings"),
    }


def _to_text(value: Any) -> str:
    """把任意值规范成去空白字符串。"""
    return str(value).strip() if value is not None else ""


def _print_json(payload: dict[str, Any]) -> None:
    """统一输出 UTF-8 JSON 结果。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())

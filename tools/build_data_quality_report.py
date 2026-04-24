from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DETAIL_GLOB = "video_detail_*.json"
COMMENTS_GLOB = "video_comments_*.json"
DOWNLOAD_GLOB = "*.mp4"
VIDEO_ID_IN_URL = re.compile(r"/video/([^/?#]+)")
VIDEO_ID_IN_FILE = re.compile(r"_([^_]+)$")
HUGE_METRIC_THRESHOLD = 1_000_000_000
METRIC_KEYS = {
    "view_count": ["view_count", "play_count", "viewCount", "playCount"],
    "like_count": ["like_count", "digg_count", "likeCount", "diggCount"],
    "comment_count": ["comment_count", "commentCount"],
    "share_count": ["share_count", "shareCount"],
}


def main(argv: Sequence[str] | None = None) -> int:
    # 主流程：解析参数，构建报表并输出两个文件。
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    json_output = _resolve_output_path(workspace, args.json_output)
    md_output = _resolve_output_path(workspace, args.md_output)
    report = _build_report(workspace)
    _write_text(json_output, json.dumps(report, ensure_ascii=False, indent=2))
    _write_text(md_output, _build_markdown(report))
    print(json.dumps({"ok": True, "json_output": str(json_output), "md_output": str(md_output)}, ensure_ascii=False, indent=2))
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成数据质量报表（v1）。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--json-output", default="artifacts/status/data_quality_report.json", help="JSON 输出路径（可相对 workspace）。")
    parser.add_argument("--md-output", default="artifacts/status/data_quality_report.md", help="Markdown 输出路径（可相对 workspace）。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _build_report(workspace: Path) -> dict[str, Any]:
    detail = _scan_detail_records(workspace / "artifacts" / "collector" / "video")
    comments = _scan_comment_records(workspace / "artifacts" / "collector" / "comments")
    downloads = _scan_download_records(workspace / "downloads" / "artifact")
    detail_ids = set(detail["valid_ids"])
    download_ids = set(downloads["valid_ids"])
    invalid_records = [*detail["invalid_records"], *comments["invalid_records"], *downloads["invalid_records"]]
    download_without_detail = sorted(download_ids - detail_ids)
    detail_without_download = sorted(detail_ids - download_ids)
    return {
        "ok": True,
        "version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(workspace),
        "scanned": {
            "detail_file_count": detail["file_count"],
            "comment_file_count": comments["file_count"],
            "download_file_count": downloads["file_count"],
            "detail_video_count": len(detail_ids),
            "comment_video_count": len(comments["by_video_id"]),
            "download_video_count": len(download_ids),
        },
        "invalid_video_id": {
            "count": len(invalid_records),
            "detail_count": len(detail["invalid_records"]),
            "comment_count": len(comments["invalid_records"]),
            "download_count": len(downloads["invalid_records"]),
            "records": invalid_records,
        },
        "coverage": {
            "download_without_detail_count": len(download_without_detail),
            "detail_without_download_count": len(detail_without_download),
            "download_without_detail_video_ids": download_without_detail,
            "detail_without_download_video_ids": detail_without_download,
        },
        "detail_metric_anomalies": {"count": len(detail["metric_anomalies"]), "records": detail["metric_anomalies"]},
        "comment_quality": {
            "placeholder_count": len(comments["placeholder_records"]),
            "empty_comment_state_count": len(comments["empty_state_records"]),
            "non_empty_comment_video_count": comments["non_empty_video_count"],
            "non_empty_comment_artifact_count": comments["non_empty_artifact_count"],
            "placeholder_records": comments["placeholder_records"],
            "empty_comment_state_records": comments["empty_state_records"],
        },
    }


def _scan_detail_records(detail_root: Path) -> dict[str, Any]:
    if not detail_root.exists():
        return {"file_count": 0, "valid_ids": set(), "invalid_records": [], "metric_anomalies": []}
    valid_ids: set[str] = set()
    invalid_records: list[dict[str, Any]] = []
    metric_anomalies: list[dict[str, Any]] = []
    file_count = 0
    for path in sorted(detail_root.glob(DETAIL_GLOB)):
        file_count += 1
        payload = _load_json(path)
        video_id = _extract_video_id(payload, _extract_file_hint_id(path.stem, "video_detail_"))
        if not _is_valid_video_id(video_id):
            invalid_records.append(_build_invalid_record("detail", path, video_id))
            continue
        valid_ids.add(video_id)
        anomaly_types = _detect_metric_anomaly_types(payload)
        if anomaly_types:
            metric_anomalies.append({"video_id": video_id, "path": str(path), "anomaly_types": anomaly_types, "metrics": _extract_metrics(payload)})
    return {"file_count": file_count, "valid_ids": valid_ids, "invalid_records": invalid_records, "metric_anomalies": metric_anomalies}


def _scan_comment_records(comment_root: Path) -> dict[str, Any]:
    if not comment_root.exists():
        return {"file_count": 0, "by_video_id": {}, "invalid_records": [], "placeholder_records": [], "empty_state_records": [], "non_empty_video_count": 0, "non_empty_artifact_count": 0}
    by_video_id: dict[str, dict[str, Any]] = {}
    invalid_records: list[dict[str, Any]] = []
    placeholder_records: list[dict[str, Any]] = []
    empty_state_records: list[dict[str, Any]] = []
    non_empty_artifact_count = 0
    file_count = 0
    for path in sorted(comment_root.glob(COMMENTS_GLOB)):
        file_count += 1
        payload = _load_json(path)
        video_id = _extract_video_id(payload, _extract_file_hint_id(path.stem, "video_comments_"))
        if not _is_valid_video_id(video_id):
            invalid_records.append(_build_invalid_record("comments", path, video_id))
            continue
        has_non_empty = _has_non_empty_comments(payload)
        has_placeholder = _has_placeholder_comment(payload)
        has_empty_state = _has_empty_comment_state(payload)
        status = by_video_id.setdefault(video_id, {"artifact_count": 0, "has_non_empty_comments": False})
        status["artifact_count"] += 1
        status["has_non_empty_comments"] = bool(status["has_non_empty_comments"] or has_non_empty)
        if has_non_empty:
            non_empty_artifact_count += 1
        if has_placeholder:
            placeholder_records.append({"video_id": video_id, "path": str(path)})
        if has_empty_state:
            empty_state_records.append({"video_id": video_id, "path": str(path)})
    non_empty_video_count = sum(1 for item in by_video_id.values() if item["has_non_empty_comments"])
    return {
        "file_count": file_count,
        "by_video_id": by_video_id,
        "invalid_records": invalid_records,
        "placeholder_records": placeholder_records,
        "empty_state_records": empty_state_records,
        "non_empty_video_count": non_empty_video_count,
        "non_empty_artifact_count": non_empty_artifact_count,
    }


def _scan_download_records(download_root: Path) -> dict[str, Any]:
    if not download_root.exists():
        return {"file_count": 0, "valid_ids": set(), "invalid_records": []}
    valid_ids: set[str] = set()
    invalid_records: list[dict[str, Any]] = []
    file_count = 0
    for path in sorted(download_root.rglob(DOWNLOAD_GLOB)):
        file_count += 1
        video_id = _extract_video_id_from_file(path)
        if not _is_valid_video_id(video_id):
            invalid_records.append(_build_invalid_record("downloads", path, video_id))
            continue
        valid_ids.add(video_id)
    return {"file_count": file_count, "valid_ids": valid_ids, "invalid_records": invalid_records}


def _build_markdown(report: dict[str, Any]) -> str:
    scanned = report["scanned"]
    invalid = report["invalid_video_id"]
    coverage = report["coverage"]
    anomalies = report["detail_metric_anomalies"]
    comments = report["comment_quality"]
    lines = [
        "# 数据质量报表（v1）",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 工作区：{report['workspace']}",
        "",
        "## 扫描范围",
        f"- detail 文件：{scanned['detail_file_count']}（视频 {scanned['detail_video_count']}）",
        f"- comments 文件：{scanned['comment_file_count']}（视频 {scanned['comment_video_count']}）",
        f"- downloads 文件：{scanned['download_file_count']}（视频 {scanned['download_video_count']}）",
        "",
        "## 异常统计",
        f"- 无效 video_id：{invalid['count']}（detail={invalid['detail_count']}，comments={invalid['comment_count']}，downloads={invalid['download_count']}）",
        f"- 下载无 detail：{coverage['download_without_detail_count']}",
        f"- detail 无下载：{coverage['detail_without_download_count']}",
        f"- 指标异常：{anomalies['count']}",
        f"- placeholder 评论：{comments['placeholder_count']}",
        f"- empty_comment_state：{comments['empty_comment_state_count']}",
        f"- 非空评论视频数：{comments['non_empty_comment_video_count']}",
        "",
        "## 覆盖差异（前 20）",
        "- 下载无 detail：" + _join_top(coverage["download_without_detail_video_ids"]),
        "- detail 无下载：" + _join_top(coverage["detail_without_download_video_ids"]),
        "",
        "## 指标异常样例（前 10）",
    ]
    for item in anomalies["records"][:10]:
        lines.append(f"- {item['video_id']}：{'、'.join(item['anomaly_types'])}（{item['path']}）")
    if not anomalies["records"]:
        lines.append("- 无")
    lines.extend(["", "## 无效 video_id 样例（前 10）"])
    for item in invalid["records"][:10]:
        lines.append(f"- [{item['source']}] detected={item['detected_video_id']} path={item['path']}")
    if not invalid["records"]:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def _extract_video_id(payload: dict[str, Any], fallback_id: str = "") -> str:
    direct_id = _to_text(payload.get("video_id"))
    if direct_id:
        return direct_id
    from_url = _extract_video_id_from_url(_to_text(payload.get("video_url")))
    return from_url if from_url else _to_text(fallback_id)


def _extract_file_hint_id(stem: str, prefix: str) -> str:
    return stem.removeprefix(prefix).split("_")[0].strip()


def _extract_video_id_from_url(video_url: str) -> str:
    matched = VIDEO_ID_IN_URL.search(video_url)
    return matched.group(1).strip() if matched else ""


def _extract_video_id_from_file(path: Path) -> str:
    matched = VIDEO_ID_IN_FILE.search(path.stem)
    return matched.group(1).strip() if matched else ""


def _is_valid_video_id(video_id: str) -> bool:
    text = _to_text(video_id)
    return text.isdigit() and len(text) >= 10


def _extract_metrics(payload: dict[str, Any]) -> dict[str, int]:
    return {metric: _extract_metric(payload, keys) for metric, keys in METRIC_KEYS.items()}


def _detect_metric_anomaly_types(payload: dict[str, Any]) -> list[str]:
    # v1 规则：四项全等非零、负数、超大值。
    values = list(_extract_metrics(payload).values())
    anomalies: list[str] = []
    if len(set(values)) == 1 and values[0] != 0:
        anomalies.append("all_equal_non_zero")
    if any(value < 0 for value in values):
        anomalies.append("negative_value")
    if any(value > HUGE_METRIC_THRESHOLD for value in values):
        anomalies.append("huge_value")
    return anomalies


def _extract_metric(payload: dict[str, Any], keys: list[str]) -> int:
    containers = [
        payload,
        payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
        payload.get("statistics") if isinstance(payload.get("statistics"), dict) else {},
        payload.get("stats") if isinstance(payload.get("stats"), dict) else {},
        payload.get("aweme_statistics") if isinstance(payload.get("aweme_statistics"), dict) else {},
        payload.get("stat") if isinstance(payload.get("stat"), dict) else {},
    ]
    for container in containers:
        for key in keys:
            value = _to_int(container.get(key))
            if value is not None:
                return value
    return 0


def _has_non_empty_comments(payload: dict[str, Any]) -> bool:
    comments = payload.get("comments")
    if isinstance(comments, list):
        return len(comments) > 0
    data = payload.get("data")
    return isinstance(data, dict) and isinstance(data.get("comments"), list) and len(data["comments"]) > 0


def _has_placeholder_comment(payload: dict[str, Any]) -> bool:
    scan_meta = payload.get("scan_meta") if isinstance(payload.get("scan_meta"), dict) else {}
    if "placeholder" in _to_text(scan_meta.get("stop_reason")) or "placeholder" in _to_text(scan_meta.get("backend")):
        return True
    return any(bool(item.get("has_comment_placeholder")) for item in _extract_rounds(scan_meta))


def _has_empty_comment_state(payload: dict[str, Any]) -> bool:
    scan_meta = payload.get("scan_meta") if isinstance(payload.get("scan_meta"), dict) else {}
    return any(bool(item.get("has_empty_comment_state")) for item in _extract_rounds(scan_meta))


def _extract_rounds(scan_meta: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics = scan_meta.get("payload_diagnostics")
    if not isinstance(diagnostics, dict):
        return []
    rounds = diagnostics.get("rounds")
    return rounds if isinstance(rounds, list) else []


def _build_invalid_record(source: str, path: Path, video_id: str) -> dict[str, Any]:
    return {"source": source, "path": str(path), "detected_video_id": _to_text(video_id)}


def _resolve_output_path(workspace: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else workspace / path


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _join_top(items: list[str], limit: int = 20) -> str:
    if not items:
        return "无"
    head = "、".join(items[:limit])
    return head if len(items) <= limit else f"{head} …（共 {len(items)} 项）"


def _to_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
    return None


if __name__ == "__main__":
    raise SystemExit(main())

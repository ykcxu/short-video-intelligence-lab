from __future__ import annotations
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse
ROOT = Path(__file__).resolve().parents[1]
COMMENTS_GLOB = "video_comments_*.json"
DEFAULT_JSON_OUTPUT = "artifacts/status/comment_failure_diagnostics.json"
DEFAULT_MD_OUTPUT = "artifacts/status/comment_failure_diagnostics.md"
REAL_COMMENT_RESPONSE_PATTERNS = (
    "/aweme/v1/web/comment/list",
    "/aweme/v1/web/comment/publish",
    "/aweme/v1/web/comment/list/reply",
)
STATUS_ORDER = ("real_comment", "empty_response", "noise_only", "missing_artifact")
VIDEO_ID_IN_URL_PART = "/video/"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def main(argv: Sequence[str] | None = None) -> int:
    """生成评论补采失败命中率诊断报告。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    report = build_comment_failure_diagnostics(workspace)
    json_output = _resolve_path(workspace, args.output)
    md_output = _resolve_path(workspace, args.md_output)
    _write_outputs(report, json_output, md_output)
    print(json.dumps({"ok": True, "json_output": str(json_output), "md_output": str(md_output)}, ensure_ascii=False, indent=2))
    return 0
def build_comment_failure_diagnostics(workspace: Path) -> dict[str, Any]:
    """按视频聚合补采命中与失败原因。"""
    target_path = workspace / "artifacts" / "collector" / "comment_backfill_targets.json"
    comments_root = workspace / "artifacts" / "collector" / "comments"
    targets = _load_targets(target_path)
    artifact_groups = _load_artifact_groups(comments_root)
    video_ids = _select_video_ids(targets, artifact_groups)
    videos = [_classify_video(video_id, targets.get(video_id, {}), artifact_groups.get(video_id, [])) for video_id in video_ids]
    return _build_report(workspace, target_path, videos)
def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成评论补采命中率诊断报告。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--output", default=DEFAULT_JSON_OUTPUT, help="JSON 输出路径。")
    parser.add_argument("--md-output", default=DEFAULT_MD_OUTPUT, help="Markdown 输出路径。")
    return parser.parse_args(list(argv) if argv is not None else None)
def _load_targets(path: Path) -> dict[str, dict[str, Any]]:
    payload = _load_json(path) if path.exists() else {}
    rows = payload.get("targets") if isinstance(payload.get("targets"), list) else []
    targets: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        video_id = _extract_video_id(row, "")
        if video_id:
            targets[video_id] = row
    return targets
def _load_artifact_groups(comments_root: Path) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not comments_root.exists():
        return groups
    for path in sorted(comments_root.glob(COMMENTS_GLOB)):
        payload = _load_json(path)
        video_id = _extract_video_id(payload, path.stem.removeprefix("video_comments_"))
        groups[video_id or path.name].append(_summarize_artifact(path, payload))
    return groups
def _summarize_artifact(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    comments = _extract_comments(payload)
    real_count = sum(1 for item in comments if _is_real_comment_item(item))
    return {
        "path": str(path),
        "name": path.name,
        "collected_at": payload.get("collected_at"),
        "comment_count": len(comments),
        "real_comment_count": real_count,
        "reasons": _extract_reasons(payload, comments),
    }
def _classify_video(video_id: str, target: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    comment_count = sum(int(item["comment_count"]) for item in artifacts)
    real_count = sum(int(item["real_comment_count"]) for item in artifacts)
    status = _resolve_status(artifacts, comment_count, real_count)
    return {
        "video_id": video_id,
        "status": status,
        "artifact_count": len(artifacts),
        "comment_count": comment_count,
        "real_comment_count": real_count,
        "target": _target_snapshot(target),
        "failure_reasons": _merge_reasons(artifacts) if status != "real_comment" else {},
        "artifacts": artifacts,
    }
def _resolve_status(artifacts: list[dict[str, Any]], comment_count: int, real_count: int) -> str:
    if not artifacts:
        return "missing_artifact"
    if real_count > 0:
        return "real_comment"
    return "empty_response" if comment_count == 0 else "noise_only"
def _extract_comments(payload: dict[str, Any]) -> list[Any]:
    comments = payload.get("comments")
    if isinstance(comments, list):
        return comments
    data = payload.get("data")
    return data["comments"] if isinstance(data, dict) and isinstance(data.get("comments"), list) else []
def _is_real_comment_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    response_url = str(raw.get("response_url") or "").lower()
    if any(pattern in response_url for pattern in REAL_COMMENT_RESPONSE_PATTERNS):
        return True
    if str(item.get("author_id") or "").strip():
        return True
    return bool(str(item.get("author_name") or "").strip() and not raw.get("stub"))
def _extract_reasons(payload: dict[str, Any], comments: list[Any]) -> dict[str, int]:
    reasons: Counter[str] = Counter()
    _add_scan_meta_reasons(reasons, payload.get("scan_meta"))
    for item in comments:
        raw = item.get("raw") if isinstance(item, dict) and isinstance(item.get("raw"), dict) else {}
        _add_raw_reasons(reasons, raw)
    return dict(sorted(reasons.items()))
def _add_raw_reasons(reasons: Counter[str], raw: dict[str, Any]) -> None:
    if raw.get("stub"):
        reasons["raw.stub:true"] += 1
    error = _clean_reason(raw.get("error"))
    if error:
        reasons[f"raw.error:{error}"] += 1
    response_url = _normalize_response_url(raw.get("response_url"))
    if response_url:
        reasons[f"raw.response_url:{response_url}"] += 1
def _add_scan_meta_reasons(reasons: Counter[str], scan_meta: Any) -> None:
    if not isinstance(scan_meta, dict):
        return
    for key in ("stop_reason", "stop_reason_detail", "backend"):
        value = _clean_reason(scan_meta.get(key))
        if value:
            reasons[f"scan_meta.{key}:{value}"] += 1
    for warning in scan_meta.get("warnings") or []:
        value = _clean_reason(warning)
        if value:
            reasons[f"scan_meta.warning:{value}"] += 1
def _normalize_response_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    return f"{parsed.netloc}{parsed.path}" if parsed.netloc else text.split("?", 1)[0]
def _clean_reason(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    return text[:160]
def _merge_reasons(artifacts: list[dict[str, Any]]) -> dict[str, int]:
    merged: Counter[str] = Counter()
    for artifact in artifacts:
        merged.update(artifact.get("reasons") or {})
    return dict(merged.most_common())
def _build_report(workspace: Path, target_path: Path, videos: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(item["status"] for item in videos)
    failure_reasons = _aggregate_failure_reasons(videos)
    return {
        "ok": True,
        "workspace": str(workspace),
        "target_path": str(target_path),
        "summary": _build_summary(videos, status_counts),
        "status_counts": {status: status_counts.get(status, 0) for status in STATUS_ORDER},
        "failure_reasons": failure_reasons,
        "videos": videos,
    }
def _build_summary(videos: list[dict[str, Any]], status_counts: Counter[str]) -> dict[str, Any]:
    total = len(videos)
    real_count = status_counts.get("real_comment", 0)
    return {
        "video_count": total,
        "real_comment_video_count": real_count,
        "failure_video_count": total - real_count,
        "hit_rate": round(real_count / total, 6) if total else 0,
    }
def _aggregate_failure_reasons(videos: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counters: dict[str, Counter[str]] = {status: Counter() for status in STATUS_ORDER if status != "real_comment"}
    for video in videos:
        status = video["status"]
        if status in counters:
            counters[status].update(video.get("failure_reasons") or {})
    return {status: dict(counter.most_common()) for status, counter in counters.items()}
def _select_video_ids(targets: dict[str, dict[str, Any]], groups: dict[str, list[dict[str, Any]]]) -> list[str]:
    if targets:
        return sorted(targets)
    return sorted(groups)
def _target_snapshot(target: dict[str, Any]) -> dict[str, Any]:
    keys = ("video_url", "comment_count", "priority", "has_comment_artifact", "has_non_empty_comments")
    return {key: target[key] for key in keys if key in target}
def _render_markdown(report: dict[str, Any]) -> str:
    lines = ["# 评论补采命中率诊断", "", "## 总体指标", ""]
    lines.extend(_summary_lines(report))
    lines.extend(["", "## 视频状态分布", "", "| 状态 | 视频数 |", "| --- | ---: |"])
    lines.extend(f"| {status} | {report['status_counts'][status]} |" for status in STATUS_ORDER)
    lines.extend(["", "## 失败原因聚合", ""])
    lines.extend(_failure_reason_lines(report["failure_reasons"]))
    lines.extend(["", "## 视频明细", "", "| 视频 ID | 状态 | 产物数 | 评论数 | 真实评论数 | 主要失败原因 |", "| --- | --- | ---: | ---: | ---: | --- |"])
    lines.extend(_video_line(video) for video in report["videos"])
    return "\n".join(lines) + "\n"
def _summary_lines(report: dict[str, Any]) -> list[str]:
    summary = report["summary"]
    return [
        f"- 视频总数：{summary['video_count']}",
        f"- 真实评论视频数：{summary['real_comment_video_count']}",
        f"- 失败视频数：{summary['failure_video_count']}",
        f"- 命中率：{summary['hit_rate']:.2%}",
    ]
def _failure_reason_lines(failure_reasons: dict[str, dict[str, int]]) -> list[str]:
    lines: list[str] = []
    for status, reasons in failure_reasons.items():
        lines.append(f"### {status}")
        lines.append("")
        if not reasons:
            lines.append("- 无可聚合原因")
        else:
            lines.extend(f"- `{reason}`：{count}" for reason, count in reasons.items())
        lines.append("")
    return lines
def _video_line(video: dict[str, Any]) -> str:
    reason = next(iter(video.get("failure_reasons") or {"-": 0}))
    return f"| {video['video_id']} | {video['status']} | {video['artifact_count']} | {video['comment_count']} | {video['real_comment_count']} | `{reason}` |"
def _extract_video_id(payload: dict[str, Any], fallback_id: str = "") -> str:
    direct_id = str(payload.get("video_id") or "").strip()
    if direct_id:
        return direct_id
    video_url = str(payload.get("video_url") or "").strip()
    if VIDEO_ID_IN_URL_PART in video_url:
        return video_url.rsplit(VIDEO_ID_IN_URL_PART, 1)[-1].split("?", 1)[0].split("/", 1)[0]
    return str(fallback_id or "").strip()
def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
def _write_outputs(report: dict[str, Any], json_output: Path, md_output: Path) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    md_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_output.write_text(_render_markdown(report), encoding="utf-8")
def _resolve_path(workspace: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else workspace / path
if __name__ == "__main__":
    raise SystemExit(main())

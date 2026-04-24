from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from short_video_intel.analysis.valid_pool_sources import build_homepage_observed_index
from short_video_intel.analysis.valid_pool_sources import is_detail_account_mentioned
from short_video_intel.analysis.valid_pool_sources import is_homepage_observed


VIDEO_ID_MIN_LENGTH = 10
COMMENT_NOISE_MARKERS = (
    "抖音电商直播间带货榜",
    "直播间带货榜当前分为",
    "商品类目榜",
    "抖音旗舰榜",
    "特色主题榜",
    "国家补贴榜",
    "带货力、互动力、吸引力",
    "为保证榜单时效性",
    "交易量等数据指标综合评估直播间主播的带货能力",
    "互动异常",
    "不同的用户看到的标签不尽相同",
)


def build_valid_analysis_pool(
    workspace: Path,
    input_dir: Path,
    output_dir: Path,
    quality_report_path: Path,
    keep_suspicious: bool = False,
    require_homepage_observed: bool = False,
    require_detail_account_mention: bool = False,
) -> dict[str, Any]:
    """构建有效分析池并写出 valid_* 与标准 CSV。"""
    videos_headers, videos_rows = _read_csv_rows(input_dir / "videos.csv")
    metrics_headers, metrics_rows = _read_csv_rows(input_dir / "video_metrics.csv")
    comments_headers, comments_rows = _read_csv_rows(input_dir / "comments.csv")
    quality_report = _load_json(quality_report_path)
    homepage_index = build_homepage_observed_index(workspace) if require_homepage_observed else {}
    filtered_ids = _collect_filtered_video_ids(quality_report, keep_suspicious)
    valid_video_ids, valid_videos, reason_counts = _filter_videos(
        rows=videos_rows,
        filtered_ids=filtered_ids,
        homepage_index=homepage_index,
        workspace=workspace,
        input_dir=input_dir,
        require_detail_account_mention=require_detail_account_mention,
    )
    valid_metrics = _filter_rows_by_video_id(metrics_rows, valid_video_ids)
    valid_comments = _filter_valid_comments(comments_rows, valid_video_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv_rows(output_dir / "valid_videos.csv", videos_headers, valid_videos)
    _write_csv_rows(output_dir / "valid_video_metrics.csv", metrics_headers, valid_metrics)
    _write_csv_rows(output_dir / "valid_comments.csv", comments_headers, valid_comments)
    _write_standard_csv_outputs(output_dir, videos_headers, metrics_headers, comments_headers, valid_videos, valid_metrics, valid_comments)
    return {
        "ok": True,
        "workspace": str(workspace),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "quality_report": str(quality_report_path),
        "keep_suspicious": bool(keep_suspicious),
        "require_homepage_observed": bool(require_homepage_observed),
        "require_detail_account_mention": bool(require_detail_account_mention),
        "filtered_video_id_count": len(filtered_ids),
        "homepage_observed_account_count": len(homepage_index),
        "homepage_observed_video_count": sum(len(video_ids) for video_ids in homepage_index.values()),
        "valid_video_count": len(valid_videos),
        "valid_video_metric_count": len(valid_metrics),
        "valid_comment_count": len(valid_comments),
        "filtered_reason_counts": reason_counts,
        "input_account_video_counts": _count_rows_by_account(videos_rows),
        "valid_account_video_counts": _count_rows_by_account(valid_videos),
    }


def _filter_videos(
    rows: list[dict[str, str]],
    filtered_ids: set[str],
    homepage_index: dict[str, set[str]],
    workspace: Path,
    input_dir: Path,
    require_detail_account_mention: bool,
) -> tuple[set[str], list[dict[str, str]], dict[str, int]]:
    """筛出具备下载+详情且通过所有条件校验的视频。"""
    valid_rows: list[dict[str, str]] = []
    valid_ids: set[str] = set()
    detail_cache: dict[Path, str] = {}
    reason_counts = _new_reason_counts()
    for row in rows:
        video_id = _to_text(row.get("video_id"))
        reason = _get_video_filter_reason(row, video_id, filtered_ids, homepage_index, workspace, input_dir, require_detail_account_mention, detail_cache)
        if reason:
            reason_counts[reason] += 1
            continue
        valid_rows.append(row)
        valid_ids.add(video_id)
    return valid_ids, valid_rows, reason_counts


def _get_video_filter_reason(
    row: dict[str, str],
    video_id: str,
    filtered_ids: set[str],
    homepage_index: dict[str, set[str]],
    workspace: Path,
    input_dir: Path,
    require_detail_account_mention: bool,
    detail_cache: dict[Path, str],
) -> str:
    """返回视频被过滤的原因；空字符串表示保留。"""
    if not _is_valid_video_id(video_id):
        return "invalid_video_id"
    if video_id in filtered_ids:
        return "quality_report_filtered"
    if not _has_required_assets(row):
        return "missing_required_assets"
    if not is_homepage_observed(row, homepage_index):
        return "not_homepage_observed"
    if not is_detail_account_mentioned(row, workspace, input_dir, require_detail_account_mention, detail_cache):
        return "detail_account_not_mentioned"
    return ""


def _new_reason_counts() -> dict[str, int]:
    """构造稳定的过滤原因计数字典，方便下游直接读取。"""
    return {
        "invalid_video_id": 0,
        "quality_report_filtered": 0,
        "missing_required_assets": 0,
        "not_homepage_observed": 0,
        "detail_account_not_mentioned": 0,
    }


def _has_required_assets(row: dict[str, str]) -> bool:
    """判定视频是否同时具备详情和下载路径。"""
    return bool(_to_text(row.get("detail_artifact_path")) and _to_text(row.get("mp4_path")))


def _filter_rows_by_video_id(rows: list[dict[str, str]], valid_video_ids: set[str]) -> list[dict[str, str]]:
    """按视频 ID 子集过滤任意明细表。"""
    return [row for row in rows if _to_text(row.get("video_id")) in valid_video_ids]


def _filter_valid_comments(rows: list[dict[str, str]], valid_video_ids: set[str]) -> list[dict[str, str]]:
    """按视频 ID 与文本噪声规则过滤评论。"""
    filtered = []
    for row in rows:
        if _to_text(row.get("video_id")) not in valid_video_ids:
            continue
        if _is_comment_noise(row):
            continue
        filtered.append(row)
    return filtered


def _is_comment_noise(row: dict[str, str]) -> bool:
    """识别从页面说明区误抽成评论的电商榜单噪声。"""
    text = _to_text(row.get("text"))
    return any(marker in text for marker in COMMENT_NOISE_MARKERS)


def _count_rows_by_account(rows: list[dict[str, str]]) -> dict[str, int]:
    """按账号统计视频行数，便于解释严格过滤后的覆盖变化。"""
    counts: dict[str, int] = {}
    for row in rows:
        account = _to_text(row.get("account_id")) or "unknown"
        counts[account] = counts.get(account, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _collect_filtered_video_ids(report: dict[str, Any], keep_suspicious: bool) -> set[str]:
    """从质量报告提取应过滤的视频 ID 集合。"""
    filtered_ids = _extract_invalid_video_ids(report) | _extract_detail_without_download_ids(report)
    if not keep_suspicious:
        filtered_ids |= _extract_metric_anomaly_ids(report)
    return filtered_ids


def _extract_invalid_video_ids(report: dict[str, Any]) -> set[str]:
    """提取 invalid_video_id.records 里的 video_id。"""
    records = report.get("invalid_video_id", {}).get("records") if isinstance(report.get("invalid_video_id"), dict) else None
    if not isinstance(records, list):
        return set()
    return {video_id for video_id in (_to_text(item.get("detected_video_id")) for item in records if isinstance(item, dict)) if _is_valid_video_id(video_id)}


def _extract_detail_without_download_ids(report: dict[str, Any]) -> set[str]:
    """提取 detail 无下载的视频 ID。"""
    coverage = report.get("coverage")
    video_ids = coverage.get("detail_without_download_video_ids") if isinstance(coverage, dict) else None
    if not isinstance(video_ids, list):
        return set()
    return {video_id for video_id in (_to_text(item) for item in video_ids) if _is_valid_video_id(video_id)}


def _extract_metric_anomaly_ids(report: dict[str, Any]) -> set[str]:
    """提取 detail_metric_anomalies.records 里的异常视频 ID。"""
    anomalies = report.get("detail_metric_anomalies")
    records = anomalies.get("records") if isinstance(anomalies, dict) else None
    if not isinstance(records, list):
        return set()
    return {video_id for video_id in (_to_text(item.get("video_id")) for item in records if isinstance(item, dict)) if _is_valid_video_id(video_id)}


def _read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """读取 CSV 并保留原始表头；文件缺失时报错。"""
    if not path.exists():
        raise FileNotFoundError(f"CSV 文件不存在：{path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def _write_csv_rows(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    """按输入表头写出 UTF-8 CSV。"""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in headers})


def _write_standard_csv_outputs(
    output_dir: Path,
    videos_headers: list[str],
    metrics_headers: list[str],
    comments_headers: list[str],
    videos: list[dict[str, str]],
    metrics: list[dict[str, str]],
    comments: list[dict[str, str]],
) -> None:
    """同步写出标准文件名，方便后续分析工具直接读取有效池目录。"""
    _write_csv_rows(output_dir / "videos.csv", videos_headers, videos)
    _write_csv_rows(output_dir / "video_metrics.csv", metrics_headers, metrics)
    _write_csv_rows(output_dir / "comments.csv", comments_headers, comments)


def _load_json(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；不存在或结构错误时抛错。"""
    if not path.exists():
        raise FileNotFoundError(f"质量报告不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"质量报告顶层不是对象：{path}")
    return data


def _is_valid_video_id(video_id: str) -> bool:
    """视频 ID 有效性规则：纯数字且长度不小于 10。"""
    text = _to_text(video_id)
    return text.isdigit() and len(text) >= VIDEO_ID_MIN_LENGTH


def _to_text(value: Any) -> str:
    """把任意值归一化成去空格字符串。"""
    return str(value).strip() if value is not None else ""

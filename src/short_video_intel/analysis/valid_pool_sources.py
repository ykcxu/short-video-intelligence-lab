from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

VIDEO_ID_MIN_LENGTH = 10
VIDEO_ID_PATTERN = re.compile(r"(\d{10,})")
HOMEPAGE_DIR = Path("artifacts/collector/homepage")
HOMEPAGE_BATCH_DIR = Path("artifacts/collector/batch")
HOMEPAGE_SUMMARY_GLOB = "artifacts/analysis/homepage_batch_summary*.json"


def build_homepage_observed_index(workspace: Path) -> dict[str, set[str]]:
    """汇总主页采集产物，得到账号名到主页视频 ID 集合的映射。"""
    index: dict[str, set[str]] = {}
    url_to_account = _load_homepage_url_account_map(workspace)
    for path in _iter_homepage_source_files(workspace):
        data = _load_json_if_object(path)
        if data:
            _merge_batch_homepage_index(index, data)
            _merge_single_homepage_index(index, data, url_to_account)
    return index


def is_homepage_observed(row: dict[str, str], homepage_index: dict[str, set[str]]) -> bool:
    """启用主页校验时，要求账号名和视频 ID 同时命中主页视频池。"""
    if not homepage_index:
        return True
    account_id = normalize_account_name(row.get("account_id"))
    video_id = _to_text(row.get("video_id"))
    return video_id in homepage_index.get(account_id, set())


def is_detail_account_mentioned(
    row: dict[str, str],
    workspace: Path,
    input_dir: Path,
    enabled: bool,
    detail_cache: dict[Path, str],
) -> bool:
    """启用详情作者校验时，要求详情内容能命中归一化后的账号名。"""
    if not enabled:
        return True
    account_id = normalize_account_name(row.get("account_id"))
    if not account_id:
        return False
    detail_path = _resolve_detail_artifact_path(row.get("detail_artifact_path"), workspace, input_dir)
    if not detail_path:
        return False
    return account_id in _load_normalized_detail_text(detail_path, detail_cache)


def normalize_account_name(value: Any) -> str:
    """账号名归一化，消除历史导入中 emoji/问号丢失造成的轻微差异。"""
    return "".join(char for char in _to_text(value) if char not in {"?", "？"}).strip()


def _load_homepage_url_account_map(workspace: Path) -> dict[str, str]:
    """从主页汇总和批处理产物里恢复 homepage_url 到账号名的映射。"""
    mapping: dict[str, str] = {}
    for path in workspace.glob(HOMEPAGE_SUMMARY_GLOB):
        data = _load_json_if_object(path)
        rows = data.get("rows") if data else None
        if isinstance(rows, list):
            _merge_url_rows(mapping, rows)
    for path in (workspace / HOMEPAGE_BATCH_DIR).glob("batch_homepage_crawl*.json"):
        data = _load_json_if_object(path)
        _merge_url_rows(mapping, [item.get("target", {}) for item in _extract_batch_results(data)])
    return mapping


def _iter_homepage_source_files(workspace: Path) -> list[Path]:
    """列出可用于主页校验的批处理与单主页采集文件。"""
    batch_files = list((workspace / HOMEPAGE_BATCH_DIR).glob("batch_homepage_crawl*.json"))
    homepage_files = list((workspace / HOMEPAGE_DIR).glob("homepage*.json"))
    return batch_files + homepage_files


def _merge_batch_homepage_index(index: dict[str, set[str]], data: dict[str, Any]) -> None:
    """合并批处理主页产物中的 target 与 crawl_result.videos。"""
    for result in _extract_batch_results(data):
        target = result.get("target")
        crawl_result = result.get("crawl_result")
        if isinstance(target, dict) and isinstance(crawl_result, dict):
            account = normalize_account_name(target.get("source_name"))
            _add_homepage_videos(index, account, crawl_result.get("videos"))


def _merge_single_homepage_index(index: dict[str, set[str]], data: dict[str, Any], url_to_account: dict[str, str]) -> None:
    """合并单主页产物；账号名通过 homepage_url 反查。"""
    account = url_to_account.get(_to_text(data.get("homepage_url")))
    if account:
        _add_homepage_videos(index, account, data.get("videos"))


def _extract_batch_results(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """安全提取 batch.results。"""
    if not data or not isinstance(data.get("batch"), dict):
        return []
    results = data["batch"].get("results")
    return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []


def _merge_url_rows(mapping: dict[str, str], rows: list[Any]) -> None:
    """把包含 homepage_url/source_name 的行合并到 URL 映射。"""
    for row in rows:
        if not isinstance(row, dict):
            continue
        homepage_url = _to_text(row.get("homepage_url"))
        source_name = normalize_account_name(row.get("source_name"))
        if homepage_url and source_name:
            mapping[homepage_url] = source_name


def _add_homepage_videos(index: dict[str, set[str]], account: str, videos: Any) -> None:
    """把主页视频列表归并到账号视频池。"""
    if not account or not isinstance(videos, list):
        return
    bucket = index.setdefault(account, set())
    for item in videos:
        video_id = _extract_video_id(item)
        if _is_valid_video_id(video_id):
            bucket.add(video_id)


def _resolve_detail_artifact_path(detail_value: Any, workspace: Path, input_dir: Path) -> Path | None:
    """解析详情产物路径，兼容绝对路径与常见相对路径。"""
    detail_text = _to_text(detail_value)
    if not detail_text:
        return None
    detail_path = Path(detail_text)
    candidates = [detail_path] if detail_path.is_absolute() else [workspace / detail_path, input_dir / detail_path]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def _load_normalized_detail_text(path: Path, detail_cache: dict[Path, str]) -> str:
    """读取详情 JSON 并返回可用于作者命中校验的归一化文本。"""
    if path in detail_cache:
        return detail_cache[path]
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        detail_cache[path] = ""
        return ""
    parts = [normalize_account_name(content)]
    parsed = _parse_json_or_none(content)
    raw = parsed.get("raw") if isinstance(parsed, dict) else None
    if isinstance(raw, dict):
        parts.extend(normalize_account_name(raw.get(key)) for key in ("body_text_preview", "body_text"))
    detail_cache[path] = "\n".join(part for part in parts if part)
    return detail_cache[path]


def _parse_json_or_none(text: str) -> Any:
    """解析 JSON 失败时返回 None，避免中断主流程。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _load_json_if_object(path: Path) -> dict[str, Any] | None:
    """宽松读取 JSON 对象；主页历史产物异常时跳过而不中断主流程。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _extract_video_id(item: Any) -> str:
    """从主页视频项中提取 video_id，兼容字符串 URL 和字典结构。"""
    if isinstance(item, dict):
        return _to_text(item.get("video_id")) or _extract_video_id_from_text(item.get("video_url"))
    return _extract_video_id_from_text(item)


def _extract_video_id_from_text(value: Any) -> str:
    """从 URL 或普通文本里提取候选视频 ID。"""
    matched = VIDEO_ID_PATTERN.search(_to_text(value))
    return matched.group(1) if matched else ""


def _is_valid_video_id(video_id: str) -> bool:
    """视频 ID 有效性规则：纯数字且长度不小于 10。"""
    text = _to_text(video_id)
    return text.isdigit() and len(text) >= VIDEO_ID_MIN_LENGTH


def _to_text(value: Any) -> str:
    """把任意值归一化成去空格字符串。"""
    return str(value).strip() if value is not None else ""

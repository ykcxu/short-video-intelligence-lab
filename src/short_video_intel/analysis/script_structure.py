from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCRIPT_STRUCTURE_VERSION = "script-structure.v1"
HOOK_PATTERNS = ("你知道", "千万别", "很多家长", "一个方法", "三招", "别再", "为什么", "到底")
PAIN_PATTERNS = ("不会", "学不会", "粗心", "丢分", "焦虑", "没思路", "记不住", "跟不上", "错误")
METHOD_PATTERNS = ("方法", "步骤", "先", "再", "最后", "公式", "技巧", "训练", "这样做")
EXAMPLE_PATTERNS = ("比如", "例如", "这道题", "举个例子", "来看", "我们看")
CTA_PATTERNS = ("关注", "收藏", "点赞", "评论", "私信", "领取", "直播间", "主页")
KNOWLEDGE_PATTERNS = ("知识点", "题型", "阅读", "作文", "语法", "单词", "数学", "语文", "英语", "考试")


def analyze_script_structure_item(item: Mapping[str, Any]) -> dict[str, Any]:
    text = _collect_text(item)
    normalized = _normalize_text(text)
    features = _build_structure_features(normalized)
    return {
        "version": SCRIPT_STRUCTURE_VERSION,
        "text_length": len(normalized),
        "source_fields": _source_fields(item),
        "script_structure": features,
        "diagnostics": _build_diagnostics(normalized=normalized, features=features),
    }


def analyze_script_structure_file(
    *,
    workspace: Path,
    artifact: Path,
    output: Path | None = None,
    features_dir: Path | None = None,
) -> dict[str, Any]:
    resolved_artifact = artifact if artifact.is_absolute() else workspace / artifact
    items = _extract_items(json.loads(resolved_artifact.read_text(encoding="utf-8")))
    results = [_build_result_item(item) for item in items]
    result = {
        "ok": True,
        "analysis_type": "script_structure",
        "artifact_path": str(resolved_artifact),
        "result": {"version": SCRIPT_STRUCTURE_VERSION, "total": len(results), "summary": _summarize(results), "results": results},
        "generated_at": _now_iso(),
    }
    _write_feature_files(workspace=workspace, features_dir=features_dir, results=results)
    resolved_output = _resolve_output_path(workspace=workspace, output=output)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(resolved_output)
    return result


def _build_result_item(item: Mapping[str, Any]) -> dict[str, Any]:
    analysis = analyze_script_structure_item(item)
    video_url = _text(item.get("video_url") or item.get("url"))
    return {
        "video_id": _text(item.get("video_id")) or _extract_video_id(video_url),
        "video_url": video_url,
        "source_name": _text(item.get("source_name")),
        **analysis,
    }


def _build_structure_features(text: str) -> dict[str, Any]:
    flags = {
        "has_hook": _contains_any(text, HOOK_PATTERNS, head_chars=80),
        "has_pain_point": _contains_any(text, PAIN_PATTERNS),
        "has_method": _contains_any(text, METHOD_PATTERNS),
        "has_example": _contains_any(text, EXAMPLE_PATTERNS),
        "has_cta": _contains_any(text, CTA_PATTERNS, tail_chars=120),
    }
    density = _knowledge_density(text)
    completeness = sum(1 for value in flags.values() if value) / len(flags)
    return {**flags, "knowledge_density_score": round(density, 3), "structure_completeness_score": round(completeness, 3)}


def _collect_text(item: Mapping[str, Any]) -> str:
    raw = _as_dict(item.get("raw"))
    blocks = [
        _text(item.get("title")),
        _text(item.get("desc")),
        _text(raw.get("title")),
        _text(raw.get("body_text_preview")),
        _text(_as_dict(item.get("asr_speech")).get("transcript")),
        _text(_as_dict(item.get("ocr_subtitle")).get("text")),
        _text(item.get("transcript")),
    ]
    return "\n".join(block for block in blocks if block)


def _build_diagnostics(*, normalized: str, features: Mapping[str, Any]) -> dict[str, Any]:
    missing = [key for key in ("has_hook", "has_pain_point", "has_method", "has_example", "has_cta") if not features.get(key)]
    return {"missing_parts": missing, "preview": normalized[:120]}


def _extract_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
        source_items = payload["items"]
    elif isinstance(payload, Mapping) and isinstance(payload.get("result"), Mapping):
        result = _as_dict(payload.get("result"))
        source_items = result.get("items") or result.get("results") or []
    elif isinstance(payload, list):
        source_items = payload
    else:
        raise ValueError("script structure artifact missing items/results list")
    return [item for item in source_items if isinstance(item, Mapping)]


def _write_feature_files(*, workspace: Path, features_dir: Path | None, results: list[Mapping[str, Any]]) -> None:
    root = _resolve_features_dir(workspace=workspace, features_dir=features_dir)
    root.mkdir(parents=True, exist_ok=True)
    for result in results:
        video_id = _text(result.get("video_id"))
        if not video_id:
            continue
        path = root / f"{video_id}.json"
        existing = _load_existing_feature(path)
        existing["script_structure"] = _as_dict(result.get("script_structure"))
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_existing_feature(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, Mapping) else {}


def _summarize(results: list[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(results)
    if not total:
        return {"average_structure_completeness": 0.0, "average_knowledge_density": 0.0}
    completeness = [_num(_as_dict(item.get("script_structure")).get("structure_completeness_score")) for item in results]
    density = [_num(_as_dict(item.get("script_structure")).get("knowledge_density_score")) for item in results]
    return {"average_structure_completeness": round(sum(completeness) / total, 3), "average_knowledge_density": round(sum(density) / total, 3)}


def _source_fields(item: Mapping[str, Any]) -> list[str]:
    fields = []
    for key in ("title", "desc", "transcript"):
        if _text(item.get(key)):
            fields.append(key)
    if _text(_as_dict(item.get("asr_speech")).get("transcript")):
        fields.append("asr_speech.transcript")
    if _text(_as_dict(item.get("ocr_subtitle")).get("text")):
        fields.append("ocr_subtitle.text")
    return fields


def _extract_video_id(video_url: str) -> str:
    matched = re.search(r"/video/(\d+)", video_url)
    return matched.group(1) if matched else ""


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _contains_any(text: str, patterns: tuple[str, ...], *, head_chars: int = 0, tail_chars: int = 0) -> bool:
    scoped = text[:head_chars] if head_chars else text[-tail_chars:] if tail_chars else text
    return any(pattern in scoped for pattern in patterns)


def _knowledge_density(text: str) -> float:
    if not text:
        return 0.0
    hits = sum(text.count(pattern) for pattern in KNOWLEDGE_PATTERNS)
    return max(0.0, min(1.0, hits / max(3.0, len(text) / 80.0)))


def _resolve_features_dir(*, workspace: Path, features_dir: Path | None) -> Path:
    if features_dir is not None:
        return features_dir if features_dir.is_absolute() else workspace / features_dir
    return workspace / "artifacts" / "multimodal" / "features"


def _resolve_output_path(*, workspace: Path, output: Path | None) -> Path:
    if output is not None:
        return output if output.is_absolute() else workspace / output
    return workspace / "artifacts" / "analysis" / f"script_structure_{_now_token()}.json"


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_token() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

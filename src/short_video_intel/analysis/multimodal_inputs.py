from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MULTIMODAL_INPUTS_VERSION = "multimodal-inputs.v1"
MODALITY_KEYS = {
    "face_quality",
    "pose_quality",
    "person_subject",
    "ocr_subtitle",
    "asr_speech",
    "script_structure",
}


def prepare_multimodal_inputs(
    *,
    workspace: Path,
    local_fit_artifact: Path,
    features_dir: Path | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    resolved_fit = local_fit_artifact if local_fit_artifact.is_absolute() else workspace / local_fit_artifact
    feature_root = _resolve_feature_root(workspace=workspace, features_dir=features_dir)
    local_items = _load_local_fit_items(resolved_fit)
    items = [_merge_feature_item(item=item, feature_root=feature_root) for item in local_items]
    result = {
        "ok": True,
        "analysis_type": "multimodal_inputs",
        "artifact_path": str(resolved_fit),
        "features_dir": str(feature_root) if feature_root else "",
        "result": {"version": MULTIMODAL_INPUTS_VERSION, "total": len(items), "summary": _summarize(items), "items": items},
        "generated_at": _now_iso(),
    }
    resolved_output = _resolve_output_path(workspace=workspace, output=output)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(resolved_output)
    return result


def _load_local_fit_items(path: Path) -> list[Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = _as_dict(payload.get("result")) if isinstance(payload, Mapping) else {}
    source_items = result.get("results") if isinstance(result.get("results"), list) else None
    if source_items is None:
        raise ValueError("local fit artifact missing result.results list")
    return [item for item in source_items if isinstance(item, Mapping)]


def _merge_feature_item(*, item: Mapping[str, Any], feature_root: Path | None) -> dict[str, Any]:
    video_id = _text(item.get("video_id"))
    features = _load_feature_file(feature_root=feature_root, video_id=video_id)
    merged = {
        "video_id": video_id,
        "video_url": _text(item.get("video_url")),
        "source_name": _text(item.get("source_name")),
        "content_features": _as_dict(item.get("content_features")),
        "frame_feature_summary": _as_dict(item.get("frame_feature_summary")),
        "local_video_fit": _as_dict(item.get("fit")),
    }
    # 抽取器产物只允许写入白名单模态，避免把调试字段混入评分输入。
    for key in MODALITY_KEYS:
        if isinstance(features.get(key), Mapping):
            merged[key] = dict(features[key])
    return merged


def _load_feature_file(*, feature_root: Path | None, video_id: str) -> dict[str, Any]:
    if feature_root is None or not video_id:
        return {}
    path = feature_root / f"{video_id}.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, Mapping) else {}


def _summarize(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    modality_coverage = {key: 0 for key in sorted(MODALITY_KEYS)}
    for item in items:
        for key in modality_coverage:
            if isinstance(item.get(key), Mapping):
                modality_coverage[key] += 1
    return {"modality_coverage": modality_coverage}


def _resolve_feature_root(*, workspace: Path, features_dir: Path | None) -> Path | None:
    if features_dir is None:
        default_dir = workspace / "artifacts" / "multimodal" / "features"
        return default_dir if default_dir.exists() else None
    return features_dir if features_dir.is_absolute() else workspace / features_dir


def _resolve_output_path(*, workspace: Path, output: Path | None) -> Path:
    if output is not None:
        return output if output.is_absolute() else workspace / output
    return workspace / "artifacts" / "analysis" / f"multimodal_inputs_{_now_token()}.json"


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_token() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MULTIMODAL_FUSION_VERSION = "multimodal-fusion.v1"

COMPONENT_WEIGHTS = {
    "local_visual": 0.16,
    "face_quality": 0.16,
    "pose_quality": 0.12,
    "person_subject": 0.12,
    "ocr_subtitle": 0.14,
    "asr_speech": 0.14,
    "script_structure": 0.16,
}


def analyze_multimodal_item(item: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(item) if isinstance(item, Mapping) else {}
    # 这里不直接绑定具体模型，先接收各抽取器产出的标准化特征，便于后续并行替换重型实现。
    components = {
        "local_visual": _score_local_visual(normalized),
        "face_quality": _score_face_quality(_as_dict(normalized.get("face_quality"))),
        "pose_quality": _score_pose_quality(_as_dict(normalized.get("pose_quality"))),
        "person_subject": _score_person_subject(_as_dict(normalized.get("person_subject"))),
        "ocr_subtitle": _score_ocr_subtitle(_as_dict(normalized.get("ocr_subtitle"))),
        "asr_speech": _score_asr_speech(_as_dict(normalized.get("asr_speech"))),
        "script_structure": _score_script_structure(_as_dict(normalized.get("script_structure"))),
    }
    fit_score = _weighted_score(components)
    strengths, risks, actions = _collect_explanations(components)
    return {
        "version": MULTIMODAL_FUSION_VERSION,
        "fit_score": fit_score,
        "fit_level": _fit_level(fit_score),
        "diagnostics": components,
        "strengths": strengths,
        "risks": risks,
        "actions": actions,
    }


def analyze_multimodal_inputs_file(
    *,
    workspace: Path,
    artifact: Path,
    output: Path | None = None,
) -> dict[str, Any]:
    resolved_artifact = artifact if artifact.is_absolute() else workspace / artifact
    payload = json.loads(resolved_artifact.read_text(encoding="utf-8"))
    items = _extract_items(payload)
    results = [_build_result_item(index, item) for index, item in enumerate(items)]
    summary = _summarize_results(results)
    result = {
        "ok": True,
        "analysis_type": "multimodal_fusion",
        "artifact_path": str(resolved_artifact),
        "result": {"version": MULTIMODAL_FUSION_VERSION, "total": len(results), "summary": summary, "results": results},
        "generated_at": _now_iso(),
    }
    resolved_output = _resolve_output_path(workspace=workspace, output=output)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(resolved_output)
    return result


def _extract_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
        source_items = payload["items"]
    elif isinstance(payload, Mapping) and isinstance(payload.get("result"), Mapping):
        result = _as_dict(payload["result"])
        source_items = result.get("items") or result.get("results") or []
    elif isinstance(payload, list):
        source_items = payload
    else:
        raise ValueError("multimodal input artifact missing items/results list")
    return [item for item in source_items if isinstance(item, Mapping)]


def _build_result_item(index: int, item: Mapping[str, Any]) -> dict[str, Any]:
    fit = analyze_multimodal_item(item)
    return {
        "index": index,
        "video_id": _text(item.get("video_id")),
        "video_url": _text(item.get("video_url")),
        "source_name": _text(item.get("source_name")),
        "fit": fit,
    }


def _score_local_visual(item: Mapping[str, Any]) -> dict[str, Any]:
    fit = _as_dict(item.get("local_video_fit") or item.get("fit"))
    score = _num(fit.get("fit_score"), default=50.0)
    if not fit:
        return _component(45, [], ["缺少本地画面基础分析结果。"], ["先运行本地视频输入与基础画面分析。"])
    return _component(score, _list_text(fit.get("strengths")), _list_text(fit.get("risks")), _list_text(fit.get("actions")))


def _score_face_quality(data: Mapping[str, Any]) -> dict[str, Any]:
    if not data:
        return _missing("缺少人脸/出镜质量特征。", "补充人脸检测、居中度、清晰度和表情积极度特征。")
    # 不做不可解释的“颜值绝对分”，统一转成可优化的出镜质量信号。
    score = 45 + _num(data.get("center_score"), default=0.5) * 18 + _num(data.get("sharpness_score"), default=0.5) * 17
    score += _num(data.get("expression_positive_score"), default=0.5) * 12
    score += 8 if _truthy(data.get("face_detected")) else -18
    score -= _num(data.get("occlusion_risk"), default=0.0) * 15
    strengths = ["人物出镜清晰且表情状态较好。"] if score >= 70 else []
    risks = [] if _truthy(data.get("face_detected")) else ["未检测到稳定人脸，口播账号适配风险较高。"]
    return _component(score, strengths, risks, ["优先保证人物脸部清晰、居中、少遮挡。"])


def _score_pose_quality(data: Mapping[str, Any]) -> dict[str, Any]:
    if not data:
        return _missing("缺少姿态识别特征。", "补充正脸朝向、上半身可见、手势活跃度和稳定度特征。")
    score = 42 + _num(data.get("facing_camera_score"), 0.5) * 18 + _num(data.get("stability_score"), 0.5) * 16
    score += _num(data.get("gesture_activity_score"), 0.5) * 12
    score += 8 if _truthy(data.get("upper_body_visible")) else -8
    risks = [] if score >= 60 else ["人物姿态对镜头表达支撑不足。"]
    return _component(score, ["姿态对讲解表达有支撑。"] if score >= 70 else [], risks, ["保持正向镜头与稳定上半身构图。"])


def _score_person_subject(data: Mapping[str, Any]) -> dict[str, Any]:
    if not data:
        return _missing("缺少人物主体检测特征。", "补充人物框占比、居中度、人数和背景杂乱度。")
    count = int(_num(data.get("person_count"), 0))
    score = 45 + _num(data.get("subject_ratio"), 0.35) * 18 + _num(data.get("center_score"), 0.5) * 18
    score += 10 if count == 1 else -10 if count > 1 else -16
    score -= _num(data.get("background_clutter_score"), 0.0) * 12
    risks = [] if count == 1 else ["人物主体不唯一或不稳定，容易削弱账号记忆点。"]
    return _component(score, ["人物主体明确，适合IP型讲解。"] if score >= 70 else [], risks, ["保证单一主体突出，减少背景干扰。"])


def _score_ocr_subtitle(data: Mapping[str, Any]) -> dict[str, Any]:
    if not data:
        return _missing("缺少OCR字幕特征。", "补充字幕文本、覆盖率、可读性和关键词密度。")
    score = 40 + _num(data.get("readability_score"), 0.5) * 22 + _num(data.get("keyword_density"), 0.3) * 16
    score += _num(data.get("subtitle_consistency_score"), 0.5) * 12 + _num(data.get("coverage_ratio"), 0.4) * 10
    risks = [] if score >= 60 else ["字幕承接信息不足，可能影响静音观看和知识点理解。"]
    return _component(score, ["字幕信息对知识表达有帮助。"] if score >= 70 else [], risks, ["提高字幕可读性，保留核心知识词。"])


def _score_asr_speech(data: Mapping[str, Any]) -> dict[str, Any]:
    if not data:
        return _missing("缺少ASR语音转文字特征。", "补充转写文本、语速、停顿比例和开场钩子。")
    speech_rate = _num(data.get("speech_rate_cpm"), 260.0)
    rate_score = max(0.0, 1.0 - abs(speech_rate - 260.0) / 220.0)
    score = 45 + rate_score * 22 + _num(data.get("opening_hook_score"), 0.5) * 20
    score -= _num(data.get("pause_ratio"), 0.15) * 25
    risks = [] if score >= 60 else ["语音节奏或开场吸引力偏弱。"]
    return _component(score, ["语音节奏与开场吸引力较好。"] if score >= 70 else [], risks, ["优化前3秒钩子，并控制语速与停顿。"])


def _score_script_structure(data: Mapping[str, Any]) -> dict[str, Any]:
    if not data:
        return _missing("缺少口播话术结构特征。", "补充钩子、痛点、方法、例子、转化动作等结构标签。")
    flags = ["has_hook", "has_pain_point", "has_method", "has_example", "has_cta"]
    structure_score = sum(1 for key in flags if _truthy(data.get(key))) / len(flags)
    score = 35 + structure_score * 45 + _num(data.get("knowledge_density_score"), 0.5) * 20
    missing = [key for key in flags if not _truthy(data.get(key))]
    risks = ["话术结构缺项：" + "、".join(missing)] if missing else []
    return _component(score, ["口播结构完整，适合复盘与迁移。"] if score >= 75 else [], risks, ["按钩子-痛点-方法-例子-行动的顺序补齐话术。"])


def _component(score: float, strengths: list[str], risks: list[str], actions: list[str]) -> dict[str, Any]:
    bounded = max(0, min(100, int(round(score))))
    return {"score": bounded, "level": _fit_level(bounded), "strengths": strengths, "risks": risks, "actions": actions}


def _missing(risk: str, action: str) -> dict[str, Any]:
    return _component(35, [], [risk], [action])


def _weighted_score(components: Mapping[str, Mapping[str, Any]]) -> int:
    total = 0.0
    # 固定权重保证同一批输入可复盘；缺失特征在各组件内显式降分而不是静默忽略。
    for key, weight in COMPONENT_WEIGHTS.items():
        total += _num(_as_dict(components.get(key)).get("score"), 0.0) * weight
    return max(0, min(100, int(round(total))))


def _collect_explanations(components: Mapping[str, Mapping[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    strengths: list[str] = []
    risks: list[str] = []
    actions: list[str] = []
    for component in components.values():
        strengths.extend(_list_text(component.get("strengths")))
        risks.extend(_list_text(component.get("risks")))
        actions.extend(_list_text(component.get("actions")))
    return _dedupe(strengths)[:8], _dedupe(risks)[:10], _dedupe(actions)[:10]


def _summarize_results(results: list[Mapping[str, Any]]) -> dict[str, Any]:
    distribution = {"high": 0, "medium": 0, "low": 0}
    score_sum = 0
    for item in results:
        fit = _as_dict(item.get("fit"))
        level = _text(fit.get("fit_level")) or "low"
        distribution[level] = distribution.get(level, 0) + 1
        score_sum += int(_num(fit.get("fit_score"), 0))
    total = len(results)
    return {"average_fit_score": round(score_sum / total, 2) if total else 0.0, "distribution": distribution}


def _resolve_output_path(*, workspace: Path, output: Path | None) -> Path:
    if output is not None:
        return output if output.is_absolute() else workspace / output
    return workspace / "artifacts" / "analysis" / f"multimodal_fusion_{_now_token()}.json"


def _fit_level(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_text(value: Any) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()] if isinstance(value, list) else []


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _truthy(value: Any) -> bool:
    return bool(value) and str(value).lower() not in {"false", "0", "none", "null"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_token() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

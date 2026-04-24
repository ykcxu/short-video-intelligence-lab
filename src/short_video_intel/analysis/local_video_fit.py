from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


LOCAL_VIDEO_FIT_VERSION = "local-video-fit.v1"


def analyze_local_video_item(item: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(item) if isinstance(item, Mapping) else {}
    features = _as_dict(normalized.get("content_features"))
    probe = _as_dict(normalized.get("probe"))
    frame_summary = _as_dict(_as_dict(normalized.get("frame_feature_summary")).get("summary"))
    subtitle_hints = _as_dict(normalized.get("subtitle_hints"))

    score = 50
    reasons_positive: list[str] = []
    reasons_risk: list[str] = []

    orientation = _safe_text(features.get("orientation"))
    duration_bucket = _safe_text(features.get("duration_bucket"))
    resolution_tier = _safe_text(features.get("resolution_tier"))
    bitrate_tier = _safe_text(features.get("bitrate_tier"))
    visual_tone = _as_dict(features.get("visual_tone"))
    visual_tags = [str(item).strip() for item in list(features.get("visual_tags") or []) if str(item).strip()]
    brightness_level = _safe_text(visual_tone.get("brightness_level"))
    saturation_level = _safe_text(visual_tone.get("saturation_level"))
    contrast_level = _safe_text(visual_tone.get("contrast_level"))
    visual_rhythm_hint = _safe_text(visual_tone.get("visual_rhythm_hint"))
    subtitle_readability = _safe_text(subtitle_hints.get("readability_hint"))
    duration_sec = _safe_float(features.get("duration_sec") or probe.get("duration_sec"))

    if orientation == "portrait":
        score += 12
        reasons_positive.append("竖屏形态符合抖音主流内容消费习惯。")
    else:
        score -= 10
        reasons_risk.append("非竖屏内容在抖音原生分发环境中通常更吃亏。")

    if 15 <= duration_sec <= 50:
        score += 12
        reasons_positive.append("时长处于中短视频较常见的可接受区间。")
    elif duration_sec < 8:
        score -= 12
        reasons_risk.append("视频时长过短，不利于承载完整讲解或转化结构。")
    elif duration_sec > 75:
        score -= 8
        reasons_risk.append("视频时长偏长，需要更强节奏控制才能维持完播。")

    if resolution_tier in {"high", "4k_like"}:
        score += 8
        reasons_positive.append("画面分辨率较高，利于塑造专业感。")
    elif resolution_tier == "low":
        score -= 10
        reasons_risk.append("分辨率偏低，会削弱账号内容质感。")

    if brightness_level == "balanced":
        score += 8
        reasons_positive.append("整体亮度较均衡，适合知识/讲解型内容观看。")
    elif brightness_level == "dark":
        score -= 6
        reasons_risk.append("画面偏暗，容易影响信息识别和停留。")
    elif brightness_level == "bright":
        score += 3
        reasons_positive.append("画面整体较亮，首屏可视性较好。")

    if contrast_level == "high":
        score += 6
        reasons_positive.append("画面对比度较强，有利于突出主体与文字信息。")
    elif contrast_level == "low":
        score -= 5
        reasons_risk.append("画面对比度偏低，主体可能不够突出。")

    if saturation_level == "medium":
        score += 5
        reasons_positive.append("色彩饱和度适中，画面观感相对自然。")
    elif saturation_level == "low":
        score -= 3
        reasons_risk.append("色彩饱和度偏低，视觉记忆点可能不够强。")
    elif saturation_level == "high":
        score += 1
        reasons_positive.append("色彩相对鲜明，可能提升首屏吸引力。")

    if bitrate_tier == "high":
        score += 5
        reasons_positive.append("码率较高，细节保留更充分。")
    elif bitrate_tier == "low":
        score -= 4
        reasons_risk.append("码率偏低，复杂画面时可能影响清晰度。")

    if subtitle_readability == "high":
        score += 5
        reasons_positive.append("字幕区域可读性较好，适合讲解型内容承接信息。")
    elif subtitle_readability == "low":
        score -= 7
        reasons_risk.append("字幕区域可读性偏弱，可能影响知识点传达和停留。")

    if "possible_talking_head" in visual_tags:
        score += 6
        reasons_positive.append("画面节奏较稳定，适合讲解型/口播型内容承载。")
    if "clean_educational_style" in visual_tags:
        score += 5
        reasons_positive.append("整体画面风格干净，较适合知识表达场景。")
    if "dynamic_visual_pacing" in visual_tags:
        score += 3
        reasons_positive.append("画面节奏更活跃，可能更利于首屏吸引。")
    if "low_exposure_risk" in visual_tags:
        score -= 5
        reasons_risk.append("存在低曝光风险，可能削弱人物、字幕或板书可读性。")

    fit_score = max(0, min(100, int(round(score))))
    fit_level = _fit_level(fit_score)
    actions = _build_local_actions(
        fit_level=fit_level,
        brightness_level=brightness_level,
        contrast_level=contrast_level,
        saturation_level=saturation_level,
        orientation=orientation,
        duration_sec=duration_sec,
        visual_tags=visual_tags,
        visual_rhythm_hint=visual_rhythm_hint,
        subtitle_readability=subtitle_readability,
    )

    return {
        "version": LOCAL_VIDEO_FIT_VERSION,
        "fit_score": fit_score,
        "fit_level": fit_level,
        "diagnostics": {
            "duration_sec": round(duration_sec, 3),
            "orientation": orientation,
            "resolution_tier": resolution_tier,
            "bitrate_tier": bitrate_tier,
            "brightness_level": brightness_level,
            "saturation_level": saturation_level,
            "contrast_level": contrast_level,
            "visual_rhythm_hint": visual_rhythm_hint,
            "visual_tags": visual_tags,
            "subtitle_readability": subtitle_readability,
            "avg_brightness": _safe_float(frame_summary.get("avg_brightness")),
            "avg_saturation": _safe_float(frame_summary.get("avg_saturation")),
            "avg_contrast_span": _safe_float(frame_summary.get("avg_contrast_span")),
        },
        "strengths": reasons_positive or ["当前视频暂无明显画面侧强信号。"],
        "risks": reasons_risk or ["当前视频在基础画面侧未发现明显短板。"],
        "actions": actions,
    }


def analyze_local_video_inputs_file(
    *,
    workspace: Path,
    artifact: Path,
    output: Path | None = None,
) -> dict[str, Any]:
    resolved_artifact = artifact if artifact.is_absolute() else (workspace / artifact)
    payload = json.loads(resolved_artifact.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        raise ValueError("local video inputs artifact missing items list")

    results: list[dict[str, Any]] = []
    distribution = {"high": 0, "medium": 0, "low": 0}
    score_sum = 0

    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        fit = analyze_local_video_item(item)
        fit_level = _safe_text(fit.get("fit_level")) or "low"
        distribution[fit_level] = distribution.get(fit_level, 0) + 1
        score_sum += _safe_int(fit.get("fit_score"))
        results.append(
            {
                "index": index,
                "video_id": _safe_text(item.get("video_id")),
                "video_url": _safe_text(item.get("video_url")),
                "source_name": _safe_text(item.get("source_name")),
                "content_features": _as_dict(item.get("content_features")),
                "frame_feature_summary": _as_dict(item.get("frame_feature_summary")),
                "fit": fit,
            }
        )

    total = len(results)
    result = {
        "ok": True,
        "analysis_type": "local_video_fit",
        "artifact_path": str(resolved_artifact),
        "result": {
            "version": LOCAL_VIDEO_FIT_VERSION,
            "total": total,
            "summary": {
                "average_fit_score": round(score_sum / total, 2) if total else 0.0,
                "distribution": distribution,
            },
            "results": results,
        },
        "generated_at": _now_iso(),
    }

    if output is not None:
        resolved_output = output if output.is_absolute() else (workspace / output)
    else:
        resolved_output = workspace / "artifacts" / "analysis" / f"local_video_fit_{_now_token()}.json"
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(resolved_output)
    return result


def _build_local_actions(
    *,
    fit_level: str,
    brightness_level: str,
    contrast_level: str,
    saturation_level: str,
    orientation: str,
    duration_sec: float,
    visual_tags: list[str],
    visual_rhythm_hint: str,
    subtitle_readability: str,
) -> list[str]:
    actions: list[str] = []
    if fit_level == "high":
        actions.append("保留当前拍摄构图与清晰度配置，优先复用到同类选题。")
    elif fit_level == "medium":
        actions.append("保持当前视频结构，但优先微调画面表达与前10秒节奏。")
    else:
        actions.append("先不要放大当前模板，优先修画面表达和基础包装。")

    if orientation != "portrait":
        actions.append("改成 9:16 竖屏构图，减少平台原生分发损耗。")
    if brightness_level == "dark":
        actions.append("提高主光或整体曝光，让人物和字幕更容易识别。")
    if contrast_level == "low":
        actions.append("增强主体与背景分离，提升人物或板书的识别度。")
    if saturation_level == "low":
        actions.append("适当增加色彩层次或封面冲击力，提升首屏吸引力。")
    if duration_sec > 50:
        actions.append("压缩铺垫段落，把核心价值前置到前15秒。")
    if duration_sec < 12:
        actions.append("补足完整讲解链路，避免信息量不足导致转化弱。")
    if "possible_talking_head" in visual_tags:
        actions.append("可以强化老师/人物主体与字幕协同，往稳定讲解型模板沉淀。")
    if "dynamic_visual_pacing" in visual_tags and visual_rhythm_hint == "dynamic":
        actions.append("保留动态切换优势，同时注意不要牺牲知识点清晰度。")
    if subtitle_readability == "low":
        actions.append("提升底部字幕区域明暗对比，避免字幕被背景吞没。")
    elif subtitle_readability == "high":
        actions.append("保留当前字幕可读性配置，后续适合接 OCR/字幕分析链路。")
    return actions


def _fit_level(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_token() -> str:
    token = _now_iso().replace(":", "").replace("-", "").replace(".", "")
    return token.replace("+", "_plus_").replace("Z", "_z_")

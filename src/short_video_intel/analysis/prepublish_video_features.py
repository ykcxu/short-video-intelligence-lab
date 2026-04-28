from __future__ import annotations

from pathlib import Path
from typing import Any

from short_video_intel.analysis.local_video_fit import analyze_local_video_item
from short_video_intel.analysis.local_video_inputs import (
    _build_content_feature_summary,
    _extract_frame_feature_summary,
    _extract_sample_frames,
    _extract_subtitle_hint_summary,
    _ffprobe_video,
)
from short_video_intel.analysis.video_effect_evaluator import CTA_WORDS, HOOK_WORDS

DEFAULT_SPEECH_RATE_CPM = 260.0
DEFAULT_PERSON_COUNT = 1.0
FRAME_SAMPLE_COUNT = 3


def build_pre_publish_row(options: dict[str, Any]) -> dict[str, Any]:
    """把本地上传视频和拟发布文本转换成上架前评分输入。"""
    video_path = Path(str(options.get("video_path") or "")).expanduser()
    work_dir = Path(str(options.get("work_dir") or "output/gui_work")).expanduser()
    planned_title = _clean_text(options.get("planned_title"))
    planned_caption = _clean_text(options.get("planned_caption"))
    script_text = _clean_text(options.get("script_text"))
    video_meta = _analyze_video(video_path=video_path, work_dir=work_dir)
    text_features = _analyze_text_features(
        planned_title=planned_title,
        planned_caption=planned_caption,
        script_text=script_text,
    )
    row = {
        "video_id": video_path.stem,
        "source_name": "本地上传视频",
        "video_url": str(video_path),
        "planned_title": planned_title,
        "planned_caption": planned_caption,
        "script_text": script_text,
        **video_meta["row_features"],
        **text_features,
        "feature_warnings": video_meta["warnings"],
    }
    return row


def _analyze_video(*, video_path: Path, work_dir: Path) -> dict[str, Any]:
    """抽取轻量视频特征；失败时保留明确告警，避免伪装成完整多模态结果。"""
    warnings: list[str] = []
    if not video_path.exists() or not video_path.is_file():
        raise FileNotFoundError(f"视频文件不存在：{video_path}")

    probe = _ffprobe_video(video_path)
    if not probe.get("ok"):
        warnings.append(f"ffprobe 未能解析视频：{probe.get('error') or '未知错误'}")
        return {"row_features": _fallback_video_features(), "warnings": warnings}

    sample_dir = work_dir / "frames" / video_path.stem
    frames = _extract_sample_frames(
        video_path=video_path,
        probe=probe,
        sample_dir=sample_dir,
        frames_per_video=FRAME_SAMPLE_COUNT,
    )
    frame_summary = _extract_frame_feature_summary(frames)
    subtitle_hints = _extract_subtitle_hint_summary(frames)
    content_features = _build_content_feature_summary(probe=probe, frame_stats=frame_summary)
    fit = analyze_local_video_item(
        {
            "probe": probe,
            "content_features": content_features,
            "frame_feature_summary": frame_summary,
            "subtitle_hints": subtitle_hints,
        }
    )
    if not frame_summary.get("ok"):
        warnings.append("未能抽取稳定帧特征，画面质量分会偏保守。")
    warnings.append("当前 GUI 轻量版未启用 ASR/OCR/姿态重模型，字幕、语速、人物项使用保守估计。")
    return {
        "row_features": {
            "fit_score": fit.get("fit_score", 50),
            "ocr_readability": _subtitle_readability_score(subtitle_hints.get("readability_hint")),
            "subtitle_consistency": _subtitle_readability_score(subtitle_hints.get("readability_hint")),
            "face_center_score": 0.65,
            "pose_facing_score": 0.65,
            "speech_rate_cpm": DEFAULT_SPEECH_RATE_CPM,
            "person_count": DEFAULT_PERSON_COUNT,
            "local_video_fit": fit,
            "video_probe": probe,
            "content_features": content_features,
            "frame_feature_summary": frame_summary,
            "subtitle_hints": subtitle_hints,
        },
        "warnings": warnings,
    }


def _analyze_text_features(*, planned_title: str, planned_caption: str, script_text: str) -> dict[str, Any]:
    """用上架前可编辑文本估计结构完整度、知识密度和开头钩子。"""
    text = " ".join(part for part in [planned_title, planned_caption, script_text] if part)
    structure_hits = [
        _has_hook(text),
        _contains_any(text, ["痛点", "不会", "容易错", "为什么", "难", "问题"]),
        _contains_any(text, ["方法", "三步", "技巧", "训练", "公式", "步骤"]),
        _contains_any(text, ["比如", "例如", "例子", "案例", "演示"]),
        _contains_any(text, list(CTA_WORDS)),
    ]
    return {
        "structure_completeness": sum(1 for item in structure_hits if item) / len(structure_hits),
        "knowledge_density": _knowledge_density(text),
        "opening_hook_score": _opening_hook_score(planned_title=planned_title, script_text=script_text),
    }


def _fallback_video_features() -> dict[str, Any]:
    """视频解析失败时给出低置信默认值，让界面仍能展示问题而不是静默失败。"""
    return {
        "fit_score": 45,
        "ocr_readability": 0.5,
        "subtitle_consistency": 0.5,
        "face_center_score": 0.55,
        "pose_facing_score": 0.55,
        "speech_rate_cpm": DEFAULT_SPEECH_RATE_CPM,
        "person_count": DEFAULT_PERSON_COUNT,
        "local_video_fit": {},
        "video_probe": {},
        "content_features": {},
        "frame_feature_summary": {},
        "subtitle_hints": {},
    }


def _subtitle_readability_score(readability_hint: Any) -> float:
    mapping = {"high": 0.85, "medium": 0.65, "low": 0.35}
    return mapping.get(_clean_text(readability_hint), 0.5)


def _opening_hook_score(*, planned_title: str, script_text: str) -> float:
    opening = (planned_title + " " + script_text[:80]).strip()
    if not opening:
        return 0.0
    score = 0.35
    if _has_hook(opening):
        score += 0.35
    if "？" in opening or "?" in opening:
        score += 0.15
    if len(opening) <= 70:
        score += 0.15
    return min(1.0, score)


def _knowledge_density(text: str) -> float:
    if not text:
        return 0.0
    keywords = ["方法", "技巧", "训练", "公式", "步骤", "例子", "题", "阅读", "写作", "语文", "英语"]
    hit_count = sum(1 for keyword in keywords if keyword in text)
    length_bonus = 1 if len(text) >= 80 else 0
    return min(1.0, (hit_count + length_bonus) / 7)


def _has_hook(text: str) -> bool:
    return _contains_any(text, list(HOOK_WORDS)) or "？" in text or "?" in text


def _contains_any(text: str, words: list[str]) -> bool:
    return any(word and word in text for word in words)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()

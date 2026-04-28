from __future__ import annotations

from collections import Counter
from statistics import mean, median
from typing import Any, Iterable

MODEL_VERSION = "video-effect-evaluator.prepublish.v1"
IDEAL_KEYS = [
    "fit_score",
    "ocr_readability",
    "subtitle_consistency",
    "structure_completeness",
    "face_center_score",
    "pose_facing_score",
    "speech_rate_cpm",
    "person_count",
    "opening_hook_score",
]
DEMAND_TOPICS = {"资料领取", "考试报考", "家长咨询", "方法追问", "难度质疑"}
HOOK_WORDS = {"你知道", "为什么", "怎么", "如何", "别再", "一定要", "三步", "方法", "技巧", "避坑"}
CTA_WORDS = {"评论", "留言", "私信", "领取", "资料", "收藏", "关注", "转发"}


def train_effect_model(rows: list[dict[str, Any]], comments: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """用历史可信样本训练理想画像；训练可看历史表现，单视频评分不能看自身表现。"""
    trusted = [row for row in rows if not row.get("metric_suspicious")]
    high_engagement = _top_fraction(trusted, "trusted_engagement_score", 0.25)
    high_comment = _top_fraction(trusted, "comment_count", 0.25)
    ideal_source = _dedupe_rows(high_engagement + high_comment)
    topic_counter = _topic_counter(comments, [row["video_id"] for row in ideal_source])
    return {
        "version": MODEL_VERSION,
        "scoring_mode": "pre_publish_only",
        "trained_video_count": len(trusted),
        "ideal_source_count": len(ideal_source),
        "suspicious_video_count": len(rows) - len(trusted),
        "ideal_profile": {key: _ideal_range(ideal_source, key) for key in IDEAL_KEYS},
        "topic_weights": _topic_weights(topic_counter),
        "weights": {"production_quality": 0.55, "content_structure": 0.25, "comment_potential": 0.20},
        "forbidden_runtime_fields": ["view_count", "like_count", "comment_count", "share_count", "engagement_score", "comments"],
    }


def score_video(row: dict[str, Any], comments: list[dict[str, Any]] | None, model: dict[str, Any]) -> dict[str, Any]:
    """上架前评分：忽略实际互动和真实评论，只用视频内容特征与拟发布文本。"""
    production = _production_score(row, model)
    structure = _content_structure_score(row)
    potential = _comment_potential_score(row, model)
    weights = model.get("weights", {})
    final = production * weights.get("production_quality", 0.55)
    final += structure * weights.get("content_structure", 0.25)
    final += potential * weights.get("comment_potential", 0.20)
    risks = _risks(row)
    return {
        "video_id": row.get("video_id"),
        "source_name": row.get("source_name"),
        "video_url": row.get("video_url"),
        "effect_score": round(final, 2),
        "effect_level": _level(final),
        "production_quality_score": round(production, 2),
        "content_structure_score": round(structure, 2),
        "comment_potential_score": round(potential, 2),
        "planned_topics": _planned_topics(row),
        "ignored_runtime_fields": _present_runtime_fields(row, comments),
        "risks": risks,
        "actions": _actions(row, risks),
    }


def _production_score(row: dict[str, Any], model: dict[str, Any]) -> float:
    """按历史理想画像距离评估画面、出镜、字幕和语速。"""
    profile = model.get("ideal_profile", {})
    parts = [
        _range_score(_num(row.get("fit_score")), profile.get("fit_score"), 100),
        _range_score(_num(row.get("ocr_readability")), profile.get("ocr_readability"), 1),
        _range_score(_num(row.get("subtitle_consistency")), profile.get("subtitle_consistency"), 1),
        _range_score(_num(row.get("face_center_score")), profile.get("face_center_score"), 1),
        _range_score(_num(row.get("pose_facing_score")), profile.get("pose_facing_score"), 1),
        _speech_score(_num(row.get("speech_rate_cpm")), profile.get("speech_rate_cpm")),
        _person_score(_num(row.get("person_count"))),
    ]
    return mean(parts) if parts else 0.0


def _content_structure_score(row: dict[str, Any]) -> float:
    """评估脚本结构，不使用真实互动。"""
    score = 35.0
    score += _num(row.get("structure_completeness")) * 35
    score += _num(row.get("knowledge_density")) * 15
    score += _num(row.get("opening_hook_score")) * 15
    text = _planned_text(row)
    if any(word in text for word in HOOK_WORDS):
        score += 8
    if any(word in text for word in CTA_WORDS):
        score += 7
    return min(100.0, score)


def _comment_potential_score(row: dict[str, Any], model: dict[str, Any]) -> float:
    """用拟发布标题/文案/口播判断评论潜力，不读取真实评论。"""
    topics = _planned_topics(row)
    text = _planned_text(row)
    topic_weights = model.get("topic_weights", {})
    score = 35.0 + min(30.0, sum(topic_weights.get(topic, 4) for topic in topics))
    if "?" in text or "？" in text or "吗" in text:
        score += 12
    if any(word in text for word in CTA_WORDS):
        score += 15
    if any(topic in topics for topic in DEMAND_TOPICS):
        score += 8
    return min(100.0, score)


def _planned_topics(row: dict[str, Any]) -> dict[str, int]:
    """从拟发布文本中提取需求主题。"""
    text = _planned_text(row).lower()
    rules = {
        "资料领取": ["资料", "打印", "领取", "链接", "私信"],
        "考试报考": ["ket", "pet", "报考", "真题", "考试", "听力", "阅读"],
        "家长咨询": ["孩子", "家长", "几年级", "适合", "怎么办"],
        "难度质疑": ["太简单", "太难", "难度", "差距", "避坑"],
        "方法追问": ["怎么", "如何", "方法", "技巧", "训练", "三步"],
    }
    found = [name for name, keys in rules.items() if any(key in text for key in keys)]
    return dict(Counter(found or ["未识别明确评论钩子"]))


def _planned_text(row: dict[str, Any]) -> str:
    """合并上架前可提供的标题、文案、口播和 OCR 文本。"""
    keys = ["planned_title", "planned_caption", "title", "caption", "script_text", "asr_transcript", "ocr_text"]
    return " ".join(str(row.get(key) or "") for key in keys).strip()


def _risks(row: dict[str, Any]) -> list[str]:
    risks = []
    if _num(row.get("structure_completeness")) < 0.3:
        risks.append("口播结构不完整，缺少钩子、方法、例子或行动召唤。")
    if _num(row.get("subtitle_consistency")) < 0.45:
        risks.append("字幕一致性偏弱，静音观看理解成本较高。")
    if _num(row.get("person_count")) > 1.8:
        risks.append("人物主体不稳定，可能削弱老师 IP 记忆点。")
    if not _planned_text(row):
        risks.append("缺少拟发布标题/文案/口播文本，评论潜力只能按结构弱估。")
    if "未识别明确评论钩子" in _planned_topics(row):
        risks.append("未识别到明确评论钩子，上架前建议补充问题或资料入口。")
    return risks


def _actions(row: dict[str, Any], risks: list[str]) -> list[str]:
    actions = []
    topics = _planned_topics(row)
    if "未识别明确评论钩子" in topics:
        actions.append("结尾增加低门槛评论问题，例如年级报数、A/B 选择、是否需要资料。")
    if _num(row.get("structure_completeness")) < 0.3:
        actions.append("按“结论钩子-痛点-方法-例子-评论问题”重写脚本。")
    if _num(row.get("person_count")) > 1.8:
        actions.append("减少多人和复杂背景，保证单一老师或单一视觉焦点。")
    if topics.keys() & {"资料领取", "考试报考", "家长咨询"}:
        actions.append("把标题和结尾导向资料领取、报考咨询或家长问题，提高评论转化潜力。")
    if not actions and not risks:
        actions.append("当前上架前模型表现较稳，优先测试标题钩子和封面首屏。")
    return actions


def _present_runtime_fields(row: dict[str, Any], comments: list[dict[str, Any]] | None) -> list[str]:
    fields = [key for key in ["view_count", "like_count", "comment_count", "share_count", "engagement_score"] if row.get(key) not in (None, "")]
    if comments:
        fields.append("comments")
    return fields


def _ideal_range(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    values = sorted(_num(row.get(key)) for row in rows if row.get(key) is not None)
    if not values:
        return {"low": 0, "mid": 0, "high": 0}
    return {"low": round(_percentile(values, 0.25), 4), "mid": round(median(values), 4), "high": round(_percentile(values, 0.75), 4)}


def _range_score(value: float, target: dict[str, float] | None, scale: float) -> float:
    if not target:
        return 50.0
    low, mid, high = target.get("low", 0), target.get("mid", 0), target.get("high", 0)
    if low <= value <= high:
        return 100.0
    distance = min(abs(value - low), abs(value - high), abs(value - mid))
    return max(0.0, 100.0 - distance / max(scale, 1e-6) * 100)


def _speech_score(value: float, target: dict[str, float] | None) -> float:
    mid = (target or {}).get("mid", 260)
    return max(0.0, 100.0 - abs(value - mid) / 220 * 100)


def _person_score(value: float) -> float:
    if value <= 1.2:
        return 100.0
    if value <= 1.8:
        return 80.0
    return max(20.0, 80.0 - (value - 1.8) * 16)


def _topic_weights(counter: Counter[str]) -> dict[str, int]:
    weights = {topic: 4 for topic in DEMAND_TOPICS}
    for topic, _ in counter.most_common(5):
        weights[topic] = max(weights.get(topic, 3), 6 if topic in DEMAND_TOPICS else 4)
    return weights


def _topic_counter(comments: dict[str, list[dict[str, Any]]], video_ids: Iterable[str]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for vid in video_ids:
        for item in comments.get(vid, []):
            counter.update(item.get("topics") or [])
    return counter


def _top_fraction(rows: list[dict[str, Any]], key: str, fraction: float) -> list[dict[str, Any]]:
    count = max(1, int(len(rows) * fraction)) if rows else 0
    return sorted(rows, key=lambda item: _num(item.get(key)), reverse=True)[:count]


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set(); output = []
    for row in rows:
        vid = row.get("video_id")
        if vid in seen:
            continue
        seen.add(vid); output.append(row)
    return output


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, round((len(values) - 1) * p)))
    return values[index]


def _level(score: float) -> str:
    if score >= 80:
        return "excellent"
    if score >= 65:
        return "good"
    if score >= 50:
        return "medium"
    return "weak"


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

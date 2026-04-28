from __future__ import annotations

from collections import Counter
from statistics import mean, median
from typing import Any, Iterable

MODEL_VERSION = "video-effect-evaluator.v1"
IDEAL_KEYS = [
    "fit_score",
    "ocr_readability",
    "subtitle_consistency",
    "structure_completeness",
    "face_center_score",
    "pose_facing_score",
    "speech_rate_cpm",
    "person_count",
]
DEMAND_TOPICS = {"资料领取", "考试报考", "家长咨询", "方法追问", "难度质疑"}


def train_effect_model(rows: list[dict[str, Any]], comments: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """基于可信互动样本训练一个可解释的理想视频画像。"""
    trusted = [row for row in rows if not row.get("metric_suspicious")]
    high_engagement = _top_fraction(trusted, "trusted_engagement_score", 0.25)
    high_comment = _top_fraction(trusted, "comment_count", 0.25)
    ideal_source = _dedupe_rows(high_engagement + high_comment)
    ideal = {key: _ideal_range(ideal_source, key) for key in IDEAL_KEYS}
    topic_counter = _topic_counter(comments, [row["video_id"] for row in ideal_source])
    return {
        "version": MODEL_VERSION,
        "trained_video_count": len(trusted),
        "ideal_source_count": len(ideal_source),
        "suspicious_video_count": len(rows) - len(trusted),
        "ideal_profile": ideal,
        "topic_weights": _topic_weights(topic_counter),
        "weights": {
            "production_quality": 0.45,
            "interaction_signal": 0.30,
            "comment_conversion": 0.25,
        },
        "quality_gates": {
            "max_person_count": 1.8,
            "min_structure_completeness": 0.3,
            "min_subtitle_consistency": 0.45,
            "suspicious_share_rule": "share_count>1000 and like_count<1000 and comment_count<50",
        },
    }


def score_video(row: dict[str, Any], comments: list[dict[str, Any]], model: dict[str, Any]) -> dict[str, Any]:
    """对单条视频输出效果评分、风险和改进动作。"""
    production = _production_score(row, model)
    interaction = _interaction_score(row)
    conversion = _comment_conversion_score(comments, model)
    weights = model.get("weights", {})
    final = production * weights.get("production_quality", 0.45)
    final += interaction * weights.get("interaction_signal", 0.30)
    final += conversion * weights.get("comment_conversion", 0.25)
    risks = _risks(row, comments)
    return {
        "video_id": row.get("video_id"),
        "source_name": row.get("source_name"),
        "video_url": row.get("video_url"),
        "effect_score": round(final, 2),
        "effect_level": _level(final),
        "production_quality_score": round(production, 2),
        "interaction_signal_score": round(interaction, 2),
        "comment_conversion_score": round(conversion, 2),
        "comment_topics": _comment_topics(comments),
        "risks": risks,
        "actions": _actions(row, comments, risks),
    }


def _production_score(row: dict[str, Any], model: dict[str, Any]) -> float:
    """按理想画像距离计算制作质量分。"""
    profile = model.get("ideal_profile", {})
    parts = [
        _range_score(_num(row.get("fit_score")), profile.get("fit_score"), 100),
        _range_score(_num(row.get("ocr_readability")), profile.get("ocr_readability"), 1),
        _range_score(_num(row.get("subtitle_consistency")), profile.get("subtitle_consistency"), 1),
        _range_score(_num(row.get("structure_completeness")), profile.get("structure_completeness"), 1),
        _range_score(_num(row.get("face_center_score")), profile.get("face_center_score"), 1),
        _range_score(_num(row.get("pose_facing_score")), profile.get("pose_facing_score"), 1),
        _speech_score(_num(row.get("speech_rate_cpm")), profile.get("speech_rate_cpm")),
        _person_score(_num(row.get("person_count"))),
    ]
    return mean(parts) if parts else 0.0


def _interaction_score(row: dict[str, Any]) -> float:
    """用可信互动代理分给潜在传播信号打分。"""
    if row.get("metric_suspicious"):
        return 35.0
    engagement = _num(row.get("trusted_engagement_score") or row.get("engagement_score"))
    comment = _num(row.get("comment_count"))
    score = min(100.0, 35 + engagement / 8)
    return min(100.0, score + min(15.0, comment * 1.2))


def _comment_conversion_score(comments: list[dict[str, Any]], model: dict[str, Any]) -> float:
    """根据评论里的咨询、需求和作者回复评估转化价值。"""
    if not comments:
        return 30.0
    topics = _comment_topics(comments)
    topic_weights = model.get("topic_weights", {})
    topic_score = sum(topic_weights.get(topic, 3) * count for topic, count in topics.items())
    question_count = sum(1 for item in comments if item.get("sentiment") == "咨询")
    author_replies = sum(1 for item in comments if item.get("is_author_reply"))
    score = 35 + min(25, len(comments) * 2) + min(25, topic_score) + min(15, question_count * 3 + author_replies * 2)
    return min(100.0, score)


def _risks(row: dict[str, Any], comments: list[dict[str, Any]]) -> list[str]:
    risks = []
    if row.get("metric_suspicious"):
        risks.append("互动指标疑似低赞低评高转发，需要人工核验。")
    if _num(row.get("structure_completeness")) < 0.3:
        risks.append("口播结构不完整，缺少钩子、方法、例子或行动召唤。")
    if _num(row.get("subtitle_consistency")) < 0.45:
        risks.append("字幕一致性偏弱，静音观看理解成本较高。")
    if _num(row.get("person_count")) > 1.8:
        risks.append("人物主体不稳定，可能削弱老师 IP 记忆点。")
    if comments and not any(item.get("is_author_reply") for item in comments):
        risks.append("评论区已有需求但作者回复不足。")
    return risks


def _actions(row: dict[str, Any], comments: list[dict[str, Any]], risks: list[str]) -> list[str]:
    actions = []
    topics = _comment_topics(comments)
    if topics.keys() & {"资料领取", "考试报考", "家长咨询"}:
        actions.append("把评论区高频问题沉淀成系列选题，并设置资料领取或私信关键词入口。")
    if _num(row.get("structure_completeness")) < 0.3:
        actions.append("按“结论钩子-痛点-方法-例子-评论问题”重写脚本。")
    if _num(row.get("person_count")) > 1.8:
        actions.append("减少多人和复杂背景，保证单一老师或单一视觉焦点。")
    if not comments:
        actions.append("结尾增加低门槛评论问题，例如年级报数、A/B 选择、是否需要资料。")
    if not actions and not risks:
        actions.append("当前模型表现较稳，优先复用选题结构并测试标题钩子。")
    return actions


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


def _comment_topics(comments: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in comments:
        counter.update(item.get("topics") or [])
    return dict(counter.most_common())


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

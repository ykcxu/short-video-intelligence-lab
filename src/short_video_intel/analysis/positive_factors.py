from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

SCORING_VERSION = "positive-factors.v1"
DEFAULT_TOTAL_WEIGHTS = {
    "activity_score": 0.30,
    "execution_score": 0.30,
    "interaction_score": 0.25,
    "stability_score": 0.15,
}


def score_accounts_from_summary(summary_block: dict) -> dict:
    """Score accounts from a batch summary block.

    This is a rule-based, fully offline first-pass scorer.

    Scoring rules:
    - activity_score: favors accounts with more observed videos and more successful detail/comment captures.
      It is normalized against the strongest account in the batch so results stay comparable.
    - execution_score: measures how consistently the pipeline can obtain video detail data from the account.
      Formula is roughly detail_success / videos_seen.
    - interaction_score: measures how much comment data we can obtain relative to the available video detail.
      Formula is roughly comments_success / detail_success.
    - stability_score: penalizes warning-heavy accounts. Fewer warnings means a higher score.
    - total_score: weighted average of the four scores above.

    Notes:
    - This is a rule-based v1 proxy, not an ML model.
    - The function only reads batch summary data; it does not inspect raw video or comment content.
    - The output is designed to be explainable and easy to evolve later.
    """

    payload = _ensure_mapping(summary_block)
    account_summary = _ensure_list(payload.get("account_summary"))
    global_summary = _ensure_mapping(payload.get("global_summary"))

    baselines = _build_baselines(account_summary, global_summary)
    scored_accounts: list[dict[str, Any]] = []

    for index, raw_account in enumerate(account_summary):
        account = _ensure_mapping(raw_account)
        videos_seen = _to_int(account.get("videos_seen"))
        detail_success = _to_int(account.get("detail_success"))
        comments_success = _to_int(account.get("comments_success"))
        warnings_count = _to_int(account.get("warnings_count"))

        activity_score = _score_activity(
            videos_seen=videos_seen,
            detail_success=detail_success,
            comments_success=comments_success,
            baselines=baselines,
        )
        execution_score = _score_rate(detail_success, videos_seen, empty_value=0.0, scale=100.0)
        interaction_score = _score_rate(comments_success, detail_success, empty_value=0.0, scale=100.0)
        stability_score = _score_stability(warnings_count, baselines)

        total_score = _weighted_total(
            activity_score=activity_score,
            execution_score=execution_score,
            interaction_score=interaction_score,
            stability_score=stability_score,
        )

        scored_accounts.append(
            {
                "rank": 0,  # filled after sorting
                "account_index": index,
                "source_name": account.get("source_name") or account.get("homepage_url") or f"account-{index + 1}",
                "homepage_url": account.get("homepage_url", ""),
                "category_lv1": account.get("category_lv1", ""),
                "category_lv2": account.get("category_lv2", ""),
                "platform": account.get("platform", ""),
                "backend": account.get("backend", ""),
                "extraction_version": account.get("extraction_version", ""),
                "videos_seen": videos_seen,
                "detail_success": detail_success,
                "comments_success": comments_success,
                "warnings_count": warnings_count,
                "activity_score": activity_score,
                "execution_score": execution_score,
                "interaction_score": interaction_score,
                "stability_score": stability_score,
                "total_score": total_score,
                "signals": {
                    "detail_success_rate": _percentage(detail_success, videos_seen),
                    "interaction_capture_rate": _percentage(comments_success, detail_success),
                    "warning_penalty": min(100, warnings_count * 12),
                },
            }
        )

    scored_accounts.sort(key=lambda item: (item["total_score"], item["activity_score"]), reverse=True)
    for rank, account in enumerate(scored_accounts, start=1):
        account["rank"] = rank

    return {
        "scoring_version": SCORING_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "global_summary": global_summary,
        "baselines": baselines,
        "weights": dict(DEFAULT_TOTAL_WEIGHTS),
        "accounts": scored_accounts,
    }


def build_recommendations(scored: dict) -> list[dict]:
    """Build simple, explainable recommendations from the scored output."""

    payload = _ensure_mapping(scored)
    accounts = _ensure_list(payload.get("accounts"))
    recommendations: list[dict[str, Any]] = []

    if not accounts:
        recommendations.append(
            {
                "scope": "batch",
                "priority": "high",
                "title": "先补足可评分账号",
                "reason": "当前没有可用的账号评分结果，先确保 summary_block.account_summary 非空。",
                "actions": [
                    "确认 batch summary_block 已正确生成",
                    "检查 full-batch 采集是否返回 account_summary",
                ],
            }
        )
        return recommendations

    for account in accounts:
        activity_score = _to_int(account.get("activity_score"))
        execution_score = _to_int(account.get("execution_score"))
        interaction_score = _to_int(account.get("interaction_score"))
        stability_score = _to_int(account.get("stability_score"))
        total_score = _to_int(account.get("total_score"))

        focus_areas: list[str] = []
        actions: list[str] = []
        reasons: list[str] = []

        if activity_score < 60:
            focus_areas.append("activity")
            reasons.append("视频产出和可见数据量偏弱")
            actions.append("优先保持稳定发布频率，补齐连续视频样本")
        else:
            actions.append("保持当前发布节奏，继续做同主题连续输出")

        if execution_score < 70:
            focus_areas.append("execution")
            reasons.append("视频详情可提取程度还不够稳定")
            actions.append("优先保证视频标题、时长、发布时间等基础字段可稳定获取")

        if interaction_score < 60:
            focus_areas.append("interaction")
            reasons.append("评论侧可观测信号还不够强")
            actions.append("增加评论引导、提问式结尾和可互动话题")

        if stability_score < 70:
            focus_areas.append("stability")
            reasons.append("采集或页面结构波动较多")
            actions.append("先处理 warnings，减少 incomplete 和 placeholder 路径")

        if not reasons:
            reasons.append("该账号当前四项分数整体均衡，适合作为同类账号的参考样本")
            actions.append("继续维持当前结构，并优先沉淀高分视频的共性")

        if total_score >= 85:
            priority = "low"
        elif total_score >= 70:
            priority = "medium"
        else:
            priority = "high"

        recommendations.append(
            {
                "scope": "account",
                "priority": priority,
                "source_name": account.get("source_name", ""),
                "homepage_url": account.get("homepage_url", ""),
                "rank": account.get("rank", 0),
                "total_score": total_score,
                "focus_areas": focus_areas,
                "reason": "；".join(reasons),
                "actions": actions,
            }
        )

    recommendations.sort(key=lambda item: (_priority_rank(item["priority"]), -_to_int(item.get("total_score"))))
    return recommendations


def _build_baselines(accounts: list[dict], global_summary: dict) -> dict:
    videos_seen_values = [_to_int(_ensure_mapping(account).get("videos_seen")) for account in accounts]
    detail_success_values = [_to_int(_ensure_mapping(account).get("detail_success")) for account in accounts]
    comments_success_values = [_to_int(_ensure_mapping(account).get("comments_success")) for account in accounts]
    warnings_values = [_to_int(_ensure_mapping(account).get("warnings_count")) for account in accounts]

    return {
        "account_count": len(accounts),
        "max_videos_seen": max(videos_seen_values, default=0),
        "max_detail_success": max(detail_success_values, default=0),
        "max_comments_success": max(comments_success_values, default=0),
        "max_warnings_count": max(warnings_values, default=0),
        "global_video_total": _to_int(global_summary.get("video_total")),
        "global_detail_success_count": _to_int(global_summary.get("detail_success_count")),
        "global_comment_success_count": _to_int(global_summary.get("comment_success_count")),
        "global_failed_count": _to_int(global_summary.get("failed_count")),
        "global_detail_success_rate": _to_float(global_summary.get("detail_success_rate")),
        "global_comment_success_rate": _to_float(global_summary.get("comment_success_rate")),
    }


def _score_activity(*, videos_seen: int, detail_success: int, comments_success: int, baselines: dict) -> int:
    max_videos_seen = max(1, _to_int(baselines.get("max_videos_seen")))
    max_detail_success = max(1, _to_int(baselines.get("max_detail_success")))
    max_comments_success = max(1, _to_int(baselines.get("max_comments_success")))

    volume_component = _ratio(videos_seen, max_videos_seen)
    detail_component = _ratio(detail_success, max_detail_success)
    comment_component = _ratio(comments_success, max_comments_success)

    score = 100.0 * (
        0.60 * volume_component
        + 0.25 * detail_component
        + 0.15 * comment_component
    )
    return _clamp_int(score)


def _score_rate(numerator: int, denominator: int, *, empty_value: float, scale: float) -> int:
    if denominator <= 0:
        return _clamp_int(empty_value)
    return _clamp_int(scale * numerator / denominator)


def _score_stability(warnings_count: int, baselines: dict) -> int:
    batch_warning_penalty = 0
    if _to_int(baselines.get("global_failed_count")) > 0:
        batch_warning_penalty += 3

    score = 100 - warnings_count * 12 - batch_warning_penalty
    return _clamp_int(score)


def _weighted_total(*, activity_score: int, execution_score: int, interaction_score: int, stability_score: int) -> int:
    total = (
        activity_score * DEFAULT_TOTAL_WEIGHTS["activity_score"]
        + execution_score * DEFAULT_TOTAL_WEIGHTS["execution_score"]
        + interaction_score * DEFAULT_TOTAL_WEIGHTS["interaction_score"]
        + stability_score * DEFAULT_TOTAL_WEIGHTS["stability_score"]
    )
    return _clamp_int(total)


def _priority_rank(priority: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(priority, 3)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))


def _percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, numerator / denominator) * 100.0, 2)


def _clamp_int(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _ensure_mapping(value: Any) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _ensure_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return []


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0

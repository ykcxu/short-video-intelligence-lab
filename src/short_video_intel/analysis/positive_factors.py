from __future__ import annotations

import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
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
        detail_success = _to_int(account.get("detail_meaningful", account.get("detail_success")))
        comments_success = _to_int(account.get("comment_meaningful", account.get("comments_success")))
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
    detail_success_values = [
        _to_int(_ensure_mapping(account).get("detail_meaningful", _ensure_mapping(account).get("detail_success")))
        for account in accounts
    ]
    comments_success_values = [
        _to_int(_ensure_mapping(account).get("comment_meaningful", _ensure_mapping(account).get("comments_success")))
        for account in accounts
    ]
    warnings_values = [_to_int(_ensure_mapping(account).get("warnings_count")) for account in accounts]

    return {
        "account_count": len(accounts),
        "max_videos_seen": max(videos_seen_values, default=0),
        "max_detail_success": max(detail_success_values, default=0),
        "max_comments_success": max(comments_success_values, default=0),
        "max_warnings_count": max(warnings_values, default=0),
        "global_video_total": _to_int(global_summary.get("video_total")),
        "global_detail_success_count": _to_int(global_summary.get("detail_meaningful_count", global_summary.get("detail_success_count"))),
        "global_comment_success_count": _to_int(global_summary.get("comment_meaningful_count", global_summary.get("comment_success_count"))),
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


POSITIVE_FACTORS_VERSION = "account-positive-factors.v1"
DEFAULT_INPUT_FILES = ("videos.csv", "video_metrics.csv", "comments.csv")
DEFAULT_STOPWORDS = {
    "的",
    "了",
    "和",
    "是",
    "在",
    "就",
    "都",
    "很",
    "也",
    "与",
    "及",
    "以及",
    "我们",
    "你们",
    "他们",
    "这个",
    "那个",
    "真的",
    "可以",
    "但是",
    "因为",
    "所以",
    "然后",
    "视频",
    "账号",
    "内容",
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
}
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")
VIDEO_ID_KEYS = ("video_id", "aweme_id", "item_id", "id")
ACCOUNT_KEYS = ("account_id", "source_name", "account_name", "author_id", "uid", "sec_uid", "homepage_url", "account")
TITLE_KEYS = ("title", "video_title", "desc", "caption", "text")
VIEW_KEYS = ("view_count", "play_count", "views", "view")
LIKE_KEYS = ("like_count", "digg_count", "likes", "like")
COMMENT_KEYS = ("comment_count", "comments_count", "reply_count", "comments")
SHARE_KEYS = ("share_count", "shares", "share")
COMMENT_TEXT_KEYS = ("comment_text", "text", "content", "comment")


def build_positive_factors_report(input_dir: Path | str, *, top_n: int = 5) -> dict[str, Any]:
    """读取标准 CSV 并构建账号正向因素分析报告。"""
    normalized_top_n = max(1, _to_int(top_n))
    input_path = Path(input_dir).resolve()
    _ensure_required_files(input_path)
    videos = _read_csv_rows(input_path / "videos.csv")
    metrics = _read_csv_rows(input_path / "video_metrics.csv")
    comments = _read_csv_rows(input_path / "comments.csv")
    merged_videos = _merge_video_rows(videos, metrics)
    score_params = _build_score_params(merged_videos)
    account_groups = _group_videos_by_account(merged_videos)
    accounts = _build_account_summaries(account_groups, comments, score_params, normalized_top_n)
    return {
        "analysis_version": POSITIVE_FACTORS_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "input_dir": str(input_path),
        "account_count": len(accounts),
        "top_n": normalized_top_n,
        "accounts": accounts,
    }


def render_positive_factors_markdown(report: Mapping[str, Any]) -> str:
    """把正向因素报告渲染为 Markdown。"""
    payload = _ensure_mapping(report)
    accounts = _ensure_list(payload.get("accounts"))
    lines = [
        "# 账号正向因素分析 v1",
        "",
        f"- 生成时间：{_stringify(payload.get('generated_at'))}",
        f"- 分析版本：{_stringify(payload.get('analysis_version'))}",
        f"- 账号数量：{_to_int(payload.get('account_count'))}",
        "",
    ]
    if not accounts:
        return "\n".join(lines + ["_暂无可分析账号。_", ""])
    for index, account in enumerate(accounts, start=1):
        lines.extend(_render_account_markdown(index, _ensure_mapping(account)))
    return "\n".join(lines).rstrip() + "\n"


def write_positive_factors_outputs(
    report: Mapping[str, Any],
    *,
    json_output: Path | str,
    md_output: Path | str,
) -> None:
    """写出 JSON 和 Markdown 产物文件。"""
    json_path = Path(json_output).resolve()
    md_path = Path(md_output).resolve()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(_ensure_mapping(report), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_positive_factors_markdown(report), encoding="utf-8")


def _ensure_required_files(input_dir: Path) -> None:
    missing = [name for name in DEFAULT_INPUT_FILES if not (input_dir / name).exists()]
    if missing:
        names = ", ".join(missing)
        raise FileNotFoundError(f"缺少输入文件：{names}。请先运行 build_dataset 生成 data/processed 数据。")


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            normalized = {str(key).strip(): (value or "").strip() for key, value in row.items() if key is not None}
            if normalized:
                rows.append(normalized)
        return rows


def _merge_video_rows(videos: list[dict[str, str]], metrics: list[dict[str, str]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in videos:
        video_id = _pick_field(row, VIDEO_ID_KEYS)
        if not video_id:
            continue
        merged[video_id] = {
            "video_id": video_id,
            "account_id": _pick_account_id(row) or "unknown-account",
            "title": _pick_field(row, TITLE_KEYS),
            "view_count": 0,
            "like_count": 0,
            "comment_count": 0,
            "share_count": 0,
        }
    for row in metrics:
        video_id = _pick_field(row, VIDEO_ID_KEYS)
        if not video_id:
            continue
        current = merged.setdefault(
            video_id,
            {
                "video_id": video_id,
                "account_id": _pick_account_id(row) or "unknown-account",
                "title": _pick_field(row, TITLE_KEYS),
                "view_count": 0,
                "like_count": 0,
                "comment_count": 0,
                "share_count": 0,
            },
        )
        current["account_id"] = current["account_id"] or _pick_account_id(row) or "unknown-account"
        current["title"] = current["title"] or _pick_field(row, TITLE_KEYS)
        current["view_count"] = _extract_metric(row, VIEW_KEYS, current["view_count"])
        current["like_count"] = _extract_metric(row, LIKE_KEYS, current["like_count"])
        current["comment_count"] = _extract_metric(row, COMMENT_KEYS, current["comment_count"])
        current["share_count"] = _extract_metric(row, SHARE_KEYS, current["share_count"])
    return list(merged.values())


def _build_score_params(videos: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    return {metric: _median_and_scale(videos, metric) for metric in ("view_count", "like_count", "comment_count", "share_count")}


def _group_videos_by_account(videos: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in videos:
        groups[_stringify(row.get("account_id")) or "unknown-account"].append(row)
    return dict(groups)


def _build_account_summaries(
    account_groups: dict[str, list[dict[str, Any]]],
    comments: list[dict[str, str]],
    score_params: dict[str, tuple[float, float]],
    top_n: int,
) -> list[dict[str, Any]]:
    comments_by_video = _group_comments_by_video(comments)
    comments_by_account = _group_comments_by_account(comments)
    account_items = []
    for account_id, videos in account_groups.items():
        ranked = _rank_videos(videos, score_params)
        top_videos = ranked[:top_n]
        bottom_videos = list(reversed(ranked[-top_n:]))
        account_comments = _collect_account_comments(account_id, ranked, comments_by_video, comments_by_account)
        account_items.append(
            {
                "account_id": account_id,
                "video_count": len(ranked),
                "engagement_score": _round2(_mean([item["engagement_score"] for item in ranked])),
                "top_videos": [_compact_video(item) for item in top_videos],
                "bottom_videos": [_compact_video(item) for item in bottom_videos],
                "high_performance_title_keywords": _extract_keyword_summary(top_videos, ranked, "title", top_n),
                "comment_keywords": _extract_keyword_summary(account_comments, account_comments, "text", top_n),
            }
        )
    account_items.sort(key=lambda item: (-item["engagement_score"], item["account_id"]))
    return account_items


def _group_comments_by_video(comments: list[dict[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in comments:
        video_id = _pick_field(row, VIDEO_ID_KEYS)
        text = _pick_field(row, COMMENT_TEXT_KEYS)
        if video_id and text:
            grouped[video_id].append(text)
    return dict(grouped)


def _group_comments_by_account(comments: list[dict[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in comments:
        account_id = _pick_account_id(row)
        text = _pick_field(row, COMMENT_TEXT_KEYS)
        if account_id and text:
            grouped[account_id].append(text)
    return dict(grouped)


def _collect_account_comments(
    account_id: str,
    ranked_videos: list[dict[str, Any]],
    comments_by_video: dict[str, list[str]],
    comments_by_account: dict[str, list[str]],
) -> list[dict[str, str]]:
    items = [{"text": text} for text in comments_by_account.get(account_id, [])]
    if items:
        return items
    for video in ranked_videos:
        video_id = _stringify(video.get("video_id"))
        items.extend({"text": text} for text in comments_by_video.get(video_id, []))
    return items


def _rank_videos(videos: list[dict[str, Any]], score_params: dict[str, tuple[float, float]]) -> list[dict[str, Any]]:
    ranked = []
    for row in videos:
        score = _compute_video_score(row, score_params)
        ranked.append({**row, "engagement_score": score})
    ranked.sort(
        key=lambda item: (
            -item["engagement_score"],
            -_to_int(item.get("view_count")),
            _stringify(item.get("video_id")),
        )
    )
    return ranked


def _compute_video_score(row: Mapping[str, Any], score_params: Mapping[str, tuple[float, float]]) -> float:
    weights = {"view_count": 0.20, "like_count": 0.40, "comment_count": 0.25, "share_count": 0.15}
    score = 50.0
    for metric, weight in weights.items():
        median, scale = score_params.get(metric, (0.0, 1.0))
        z_score = (_to_float(row.get(metric)) - median) / scale
        score += max(-4.0, min(4.0, z_score)) * weight * 8.0
    return _round2(max(0.0, min(100.0, score)))


def _median_and_scale(videos: list[dict[str, Any]], metric: str) -> tuple[float, float]:
    values = sorted(_to_float(item.get(metric)) for item in videos)
    if not values:
        return 0.0, 1.0
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations) if deviations else 0.0
    return float(median), max(mad * 1.4826, 1.0)


def _compact_video(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "video_id": _stringify(item.get("video_id")),
        "title": _stringify(item.get("title")),
        "engagement_score": _round2(_to_float(item.get("engagement_score"))),
        "view_count": _to_int(item.get("view_count")),
        "like_count": _to_int(item.get("like_count")),
        "comment_count": _to_int(item.get("comment_count")),
        "share_count": _to_int(item.get("share_count")),
    }


def _extract_keyword_summary(
    focus_rows: list[Mapping[str, Any]],
    all_rows: list[Mapping[str, Any]],
    field_name: str,
    top_n: int,
) -> list[dict[str, Any]]:
    focus_counter = _token_counter(focus_rows, field_name)
    if not focus_counter:
        return []
    all_counter = _token_counter(all_rows, field_name)
    ranked = []
    for token, count in focus_counter.items():
        total = max(1, all_counter.get(token, 0))
        ranked.append(
            {
                "keyword": token,
                "count": count,
                "weight": _round2(count / total),
            }
        )
    ranked.sort(key=lambda item: (-item["weight"], -item["count"], item["keyword"]))
    return ranked[:top_n]


def _token_counter(rows: list[Mapping[str, Any]], field_name: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        text = _stringify(row.get(field_name))
        if not text:
            continue
        for token in _tokenize(text):
            counter[token] += 1
    return counter


def _tokenize(text: str) -> list[str]:
    tokens = []
    for matched in TOKEN_PATTERN.findall(text.lower()):
        token = matched.strip()
        if len(token) < 2 or token in DEFAULT_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _extract_metric(row: Mapping[str, Any], candidates: tuple[str, ...], default: int) -> int:
    value = _to_int(_pick_field(row, candidates))
    return max(value, 0) if value else max(default, 0)


def _pick_account_id(row: Mapping[str, Any]) -> str:
    return _pick_field(row, ACCOUNT_KEYS)


def _pick_field(row: Mapping[str, Any], candidates: tuple[str, ...]) -> str:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for key in candidates:
        value = lowered.get(key.lower())
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _render_account_markdown(index: int, account: Mapping[str, Any]) -> list[str]:
    lines = [
        f"## {index}. {_stringify(account.get('account_id'))}",
        f"- 平均互动得分：{_to_float(account.get('engagement_score')):.2f}",
        f"- 视频数量：{_to_int(account.get('video_count'))}",
        "",
        "### Top 视频",
    ]
    lines.extend(_render_video_table(_ensure_list(account.get("top_videos"))))
    lines.extend(["", "### Bottom 视频"])
    lines.extend(_render_video_table(_ensure_list(account.get("bottom_videos"))))
    lines.extend(["", f"### 高表现标题关键词：{_format_keywords(_ensure_list(account.get('high_performance_title_keywords')))}"])
    lines.extend([f"### 评论关键词：{_format_keywords(_ensure_list(account.get('comment_keywords')))}", ""])
    return lines


def _render_video_table(videos: list[dict[str, Any]]) -> list[str]:
    if not videos:
        return ["_暂无数据_"]
    lines = [
        "| video_id | 标题 | 互动得分 | 播放 | 点赞 | 评论 | 分享 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in videos:
        lines.append(
            "| {video_id} | {title} | {score:.2f} | {view} | {like} | {comment} | {share} |".format(
                video_id=_escape_cell(_stringify(item.get("video_id"))),
                title=_escape_cell(_stringify(item.get("title"))),
                score=_to_float(item.get("engagement_score")),
                view=_to_int(item.get("view_count")),
                like=_to_int(item.get("like_count")),
                comment=_to_int(item.get("comment_count")),
                share=_to_int(item.get("share_count")),
            )
        )
    return lines


def _format_keywords(items: list[dict[str, Any]]) -> str:
    if not items:
        return "无"
    return "、".join(f"{_stringify(item.get('keyword'))}({int(_to_int(item.get('count')))})" for item in items)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _round2(value: float) -> float:
    return round(float(value), 2)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()

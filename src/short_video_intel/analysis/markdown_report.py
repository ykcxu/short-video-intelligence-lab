from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from .positive_factors import build_recommendations, score_accounts_from_summary


def build_markdown_report(
    summary_block: Mapping[str, Any] | None = None,
    scored_result: Mapping[str, Any] | None = None,
    *,
    title: str = "短视频账号分析报告",
    top_n: int | None = None,
) -> str:
    """Render a readable Markdown report from batch summary or scored output.

    Parameters
    ----------
    summary_block:
        Raw batch summary payload. When ``scored_result`` is not provided, this
        function will score the accounts via :func:`score_accounts_from_summary`
        and build recommendations automatically.
    scored_result:
        Already scored payload, typically the return value from
        :func:`score_accounts_from_summary`.
    title:
        Report title inserted as the top-level Markdown heading.
    top_n:
        Optional maximum number of ranked accounts/recommendations to render.
        When ``None``, all items are included.

    Returns
    -------
    str
        A Markdown string containing ranking, summary metrics, and suggestions.

    Notes
    -----
    - The function only depends on the standard library and the local scoring
      helpers already present in the package.
    - If both ``summary_block`` and ``scored_result`` are provided, the scored
      result takes precedence.
    """

    payload = _ensure_mapping(scored_result) if scored_result is not None else {}
    if not payload:
        summary_payload = _ensure_mapping(summary_block)
        if not summary_payload:
            raise ValueError("summary_block or scored_result must be provided")
        if _looks_scored_payload(summary_payload):
            payload = summary_payload
        else:
            payload = score_accounts_from_summary(summary_payload)

    accounts = _ensure_list(payload.get("accounts"))
    recommendations = _ensure_list(payload.get("recommendations"))
    if not recommendations:
        recommendations = build_recommendations(payload)

    if top_n is not None:
        accounts = accounts[: max(0, top_n)]
        recommendations = recommendations[: max(0, top_n)]

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- 生成时间：{_format_datetime(payload.get('generated_at'))}")
    lines.append(f"- 评分版本：{_stringify(payload.get('scoring_version') or 'unknown')}")

    weights = _ensure_mapping(payload.get("weights"))
    if weights:
        lines.append(f"- 权重配置：{_format_weight_summary(weights)}")

    global_summary = _ensure_mapping(payload.get("global_summary"))
    if global_summary:
        lines.append("")
        lines.append("## 全局概览")
        lines.extend(_render_kv_list(_summary_items(global_summary)))

    baselines = _ensure_mapping(payload.get("baselines"))
    if baselines:
        lines.append("")
        lines.append("## 批次基线")
        lines.extend(_render_kv_list(_baseline_items(baselines)))

    lines.append("")
    lines.append("## 账户排名")
    lines.append("")
    lines.extend(_render_account_table(accounts))

    if recommendations:
        lines.append("")
        lines.append("## 建议")
        lines.extend(_render_recommendation_sections(recommendations))

    return "\n".join(lines).rstrip() + "\n"


def _render_account_table(accounts: list[dict[str, Any]]) -> list[str]:
    if not accounts:
        return ["_暂无可展示的账号排名。_"]

    lines = [
        "| 排名 | 账号 | 总分 | 活跃 | 执行 | 互动 | 稳定 | 视频数 | 详情成功 | 评论成功 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for account in accounts:
        lines.append(
            "| {rank} | {name} | {total} | {activity} | {execution} | {interaction} | {stability} | {videos} | {detail} | {comments} |".format(
                rank=_stringify(account.get("rank", "")),
                name=_escape_cell(_account_label(account)),
                total=_to_int(account.get("total_score")),
                activity=_to_int(account.get("activity_score")),
                execution=_to_int(account.get("execution_score")),
                interaction=_to_int(account.get("interaction_score")),
                stability=_to_int(account.get("stability_score")),
                videos=_to_int(account.get("videos_seen")),
                detail=_to_int(account.get("detail_meaningful", account.get("detail_success"))),
                comments=_to_int(account.get("comment_meaningful", account.get("comments_success"))),
            )
        )
    return lines


def _render_recommendation_sections(recommendations: list[dict[str, Any]]) -> list[str]:
    if not recommendations:
        return ["_暂无建议。_"]

    lines: list[str] = []
    for index, item in enumerate(recommendations, start=1):
        if item.get("scope") == "account":
            lines.append(f"### {index}. {_account_label(item)}")
            lines.append(
                f"- 排名：{_stringify(item.get('rank', ''))}｜总分：{_to_int(item.get('total_score'))}｜优先级：{_stringify(item.get('priority', ''))}"
            )
            focus_areas = _ensure_list(item.get("focus_areas"))
            if focus_areas:
                lines.append(f"- 关注点：{', '.join(_stringify(v) for v in focus_areas)}")
            reason = _stringify(item.get("reason"))
            if reason:
                lines.append(f"- 原因：{reason}")
            actions = _ensure_list(item.get("actions"))
            if actions:
                lines.append("- 建议：")
                for action in actions:
                    lines.append(f"  - {_stringify(action)}")
        else:
            lines.append(f"### {index}. {_stringify(item.get('title', '建议'))}")
            reason = _stringify(item.get("reason"))
            if reason:
                lines.append(f"- 原因：{reason}")
            actions = _ensure_list(item.get("actions"))
            if actions:
                lines.append("- 建议：")
                for action in actions:
                    lines.append(f"  - {_stringify(action)}")
        lines.append("")
    return lines[:-1] if lines and lines[-1] == "" else lines


def _summary_items(summary: Mapping[str, Any]) -> list[tuple[str, Any]]:
    keys = [
        ("video_total", "视频总数"),
        ("detail_success_count", "详情成功数"),
        ("comment_success_count", "评论成功数"),
        ("detail_meaningful_count", "有效详情数"),
        ("comment_meaningful_count", "有效评论数"),
        ("failed_count", "失败数"),
        ("detail_success_rate", "详情成功率"),
        ("comment_success_rate", "评论成功率"),
    ]
    return [(label, summary.get(key)) for key, label in keys if key in summary]


def _baseline_items(baselines: Mapping[str, Any]) -> list[tuple[str, Any]]:
    keys = [
        ("account_count", "账号数"),
        ("max_videos_seen", "最多视频数"),
        ("max_detail_success", "最多详情成功数"),
        ("max_comments_success", "最多评论成功数"),
        ("max_warnings_count", "最多警告数"),
        ("global_video_total", "全局视频总数"),
        ("global_failed_count", "全局失败数"),
    ]
    return [(label, baselines.get(key)) for key, label in keys if key in baselines]


def _render_kv_list(items: list[tuple[str, Any]]) -> list[str]:
    lines: list[str] = []
    for label, value in items:
        lines.append(f"- {label}：{_format_value(label, value)}")
    return lines or ["_暂无数据。_"]


def _format_weight_summary(weights: Mapping[str, Any]) -> str:
    ordered_keys = [
        ("activity_score", "活跃"),
        ("execution_score", "执行"),
        ("interaction_score", "互动"),
        ("stability_score", "稳定"),
    ]
    parts = []
    for key, label in ordered_keys:
        if key in weights:
            parts.append(f"{label} {float(weights.get(key, 0.0)):.2f}")
    return "，".join(parts) if parts else "未设置"


def _format_value(label: str, value: Any) -> str:
    if isinstance(value, (int, float)) and "率" in label:
        return f"{float(value):.2f}%"
    if isinstance(value, float):
        return f"{value:.2f}"
    return _stringify(value)


def _account_label(account: Mapping[str, Any]) -> str:
    for key in ("source_name", "homepage_url", "account_name", "name"):
        value = account.get(key)
        if value:
            return _stringify(value)
    rank = account.get("rank")
    if rank:
        return f"account-{rank}"
    return "unknown-account"


def _format_datetime(value: Any) -> str:
    text = _stringify(value)
    if text:
        return text
    return datetime.now(UTC).isoformat()


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _ensure_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _ensure_list(value: Any) -> list[Any]:
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


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _looks_scored_payload(payload: Mapping[str, Any]) -> bool:
    return "accounts" in payload and "scoring_version" in payload

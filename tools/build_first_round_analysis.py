from __future__ import annotations

import csv
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20260428_first_round"
TOP_LIMIT = 12

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """生成第一轮数据分析、报告和交付包。"""
    workspace = ROOT if not argv else Path(argv[0]).resolve()
    dataset = _load_dataset(workspace)
    analysis = _build_analysis(dataset)
    output_dir = workspace / "artifacts" / "analysis" / "first_round"
    deliverable_dir = workspace / "deliverables" / RUN_ID
    _write_outputs(workspace, output_dir, deliverable_dir, dataset, analysis)
    print(json.dumps({"ok": True, "output_dir": str(output_dir), "deliverable_dir": str(deliverable_dir)}, ensure_ascii=False, indent=2))
    return 0


def _load_dataset(workspace: Path) -> dict[str, Any]:
    """读取严格有效池，避免被低可信原始数据污染结论。"""
    base = workspace / "data" / "processed_strict_valid"
    videos = _read_csv(base / "videos.csv")
    metrics = {row["video_id"]: row for row in _read_csv(base / "video_metrics.csv")}
    comments = _group_comments(_read_csv(base / "comments.csv"))
    rows = [_merge_row(item, metrics, comments) for item in videos]
    return {"workspace": workspace, "rows": rows, "comments": comments}


def _merge_row(video: dict[str, str], metrics: dict[str, dict[str, str]], comments: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    """合并视频、指标和评论，并生成标题特征。"""
    video_id = video["video_id"]
    metric = metrics.get(video_id, {})
    title = video.get("title", "")
    row = {**video, **_metric_values(metric)}
    row["collected_comment_count"] = len(comments.get(video_id, []))
    row["engagement_score"] = row["like_count"] + row["comment_count"] * 3 + row["share_count"] * 2
    row["metric_suspicious"] = _is_metric_suspicious(row)
    row.update(_title_features(title))
    return row


def _metric_values(metric: dict[str, str]) -> dict[str, int]:
    """把指标字段安全转为整数。"""
    return {key: _to_int(metric.get(key)) for key in ("view_count", "like_count", "comment_count", "share_count")}


def _is_metric_suspicious(row: dict[str, Any]) -> bool:
    """识别明显疑似指标错位的极端值，避免首轮因素分析被污染。"""
    same_large_like_comment = row["like_count"] == row["comment_count"] and row["comment_count"] >= 100000
    impossible_comment_scale = row["comment_count"] >= 100000
    return bool(same_large_like_comment or impossible_comment_scale)


def _title_features(title: str) -> dict[str, Any]:
    """提取可解释标题特征，用于首轮非模型分析。"""
    clean = title.replace(" - 抖音", "").strip()
    return {
        "title_clean": clean,
        "title_len": len(clean),
        "has_question": int("?" in clean or "？" in clean),
        "has_exclaim": int("!" in clean or "！" in clean),
        "has_grade": int(bool(re.search(r"[一二三四五六七八九1-9]年级|小升初|初中|小学", clean))),
        "has_method": int(any(word in clean for word in ("方法", "技巧", "口诀", "公式", "干货", "避坑", "规划"))),
        "has_material": int(any(word in clean for word in ("资料", "领取", "打印", "练习", "作业", "清单"))),
        "has_parent_child": int(any(word in clean for word in ("孩子", "家长", "妈妈", "爸爸", "学生"))),
        "has_interaction": int(any(word in clean for word in ("评论", "你们", "知道", "留言", "收藏", "转发"))),
        "has_subject": int(any(word in clean for word in ("语文", "英语", "作文", "阅读", "单词", "数学"))),
        "has_persona": int(any(word in clean for word in ("老师", "同学", "学霸", "Chinese"))),
        "hashtag_count": clean.count("#"),
    }


def _build_analysis(dataset: dict[str, Any]) -> dict[str, Any]:
    """生成播放热度、评论热度与账号改进点三类结论。"""
    rows = dataset["rows"]
    analysis_rows = [row for row in rows if not row["metric_suspicious"]]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_summary": _sample_summary(rows, analysis_rows),
        "play_analysis": _factor_analysis(analysis_rows, "engagement_score"),
        "comment_analysis": _factor_analysis(analysis_rows, "comment_count"),
        "top_videos": _top_videos(analysis_rows),
        "account_analysis": _account_analysis(analysis_rows),
        "comment_topic_summary": _comment_topic_summary(dataset["comments"]),
        "data_limitations": _data_limitations(rows, analysis_rows),
    }


def _sample_summary(rows: list[dict[str, Any]], analysis_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总样本规模与指标可用性。"""
    return {
        "strict_valid_video_count": len(rows),
        "analysis_video_count": len(analysis_rows),
        "suspicious_metric_video_count": len(rows) - len(analysis_rows),
        "account_count": len({row["account_id"] for row in rows}),
        "nonzero_view_count": sum(1 for row in rows if row["view_count"] > 0),
        "nonzero_comment_count": sum(1 for row in rows if row["comment_count"] > 0),
        "total_collected_comments": sum(row["collected_comment_count"] for row in rows),
    }


def _factor_analysis(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    """对比高分组与普通组的标题特征差异。"""
    scored = [row for row in rows if row[metric] > 0]
    threshold = _percentile([row[metric] for row in scored], 0.75)
    high = [row for row in scored if row[metric] >= threshold]
    normal = [row for row in scored if row[metric] < threshold]
    return {
        "metric": metric,
        "high_threshold": threshold,
        "high_count": len(high),
        "normal_count": len(normal),
        "feature_lifts": _feature_lifts(high, normal),
        "top_examples": _video_briefs(sorted(scored, key=lambda item: item[metric], reverse=True)[:TOP_LIMIT], metric),
    }


def _feature_lifts(high: list[dict[str, Any]], normal: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """计算高分组中特征占比相对普通组的提升。"""
    features = ["has_question", "has_exclaim", "has_grade", "has_method", "has_material", "has_parent_child", "has_interaction", "has_subject", "has_persona"]
    lifts = [_feature_lift(name, high, normal) for name in features]
    return sorted(lifts, key=lambda item: item["lift"], reverse=True)


def _feature_lift(name: str, high: list[dict[str, Any]], normal: list[dict[str, Any]]) -> dict[str, Any]:
    """计算单个特征的高分占比、普通占比和差值。"""
    high_rate = mean([row[name] for row in high]) if high else 0
    normal_rate = mean([row[name] for row in normal]) if normal else 0
    return {"feature": name, "high_rate": round(high_rate, 4), "normal_rate": round(normal_rate, 4), "lift": round(high_rate - normal_rate, 4)}


def _top_videos(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """列出播放代理与评论表现最好的视频。"""
    return {
        "by_engagement_score": _video_briefs(sorted(rows, key=lambda item: item["engagement_score"], reverse=True)[:TOP_LIMIT], "engagement_score"),
        "by_comment_count": _video_briefs(sorted(rows, key=lambda item: item["comment_count"], reverse=True)[:TOP_LIMIT], "comment_count"),
        "by_share_count": _video_briefs(sorted(rows, key=lambda item: item["share_count"], reverse=True)[:TOP_LIMIT], "share_count"),
    }


def _account_analysis(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按账号统计表现，并生成改进建议。"""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["account_id"]].append(row)
    return [_account_summary(account, items) for account, items in sorted(groups.items())]


def _account_summary(account: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """生成单账号指标、优势特征和改进点。"""
    top = sorted(rows, key=lambda item: item["engagement_score"], reverse=True)[:5]
    weak = sorted(rows, key=lambda item: item["engagement_score"])[:5]
    return {
        "account_id": account,
        "video_count": len(rows),
        "median_engagement_score": _safe_median([row["engagement_score"] for row in rows]),
        "median_comment_count": _safe_median([row["comment_count"] for row in rows]),
        "median_like_count": _safe_median([row["like_count"] for row in rows]),
        "median_share_count": _safe_median([row["share_count"] for row in rows]),
        "top_feature_profile": _profile_features(top),
        "weak_feature_profile": _profile_features(weak),
        "top_examples": _video_briefs(top, "engagement_score"),
        "improvement_points": _improvement_points(rows),
    }


def _profile_features(rows: list[dict[str, Any]]) -> dict[str, float]:
    """统计一组视频的标题特征占比。"""
    features = ["has_grade", "has_method", "has_material", "has_parent_child", "has_interaction", "has_subject", "has_persona"]
    return {name: round(mean([row[name] for row in rows]), 3) if rows else 0 for name in features}


def _improvement_points(rows: list[dict[str, Any]]) -> list[str]:
    """根据账号内高低分差异生成可执行建议。"""
    top_profile = _profile_features(sorted(rows, key=lambda item: item["engagement_score"], reverse=True)[:5])
    weak_profile = _profile_features(sorted(rows, key=lambda item: item["engagement_score"])[:5])
    suggestions = []
    mapping = {
        "has_grade": "标题和开头更明确年级/阶段，降低用户判断成本",
        "has_method": "增加口诀、方法、避坑、规划类表达，强化可收藏价值",
        "has_material": "补充资料/练习/清单类钩子，引导保存和评论索取",
        "has_parent_child": "把痛点从知识点转成家长/孩子场景，提升共鸣",
        "has_interaction": "在标题或结尾加入明确提问，提升评论触发",
    }
    for key, text in mapping.items():
        if top_profile.get(key, 0) - weak_profile.get(key, 0) >= 0.2:
            suggestions.append(text)
    return suggestions[:4] or ["保持高表现视频的选题密度，优先复刻账号内 Top 视频结构"]


def _comment_topic_summary(comments: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    """从评论文本提取高频主题，辅助解释评论动因。"""
    texts = [item.get("text", "") for rows in comments.values() for item in rows if item.get("text")]
    buckets = Counter()
    for text in texts:
        for name, words in _comment_topic_words().items():
            if any(word in text for word in words):
                buckets[name] += 1
    return {"comment_text_count": len(texts), "topic_counts": dict(buckets.most_common())}


def _comment_topic_words() -> dict[str, tuple[str, ...]]:
    """定义首轮评论主题词，不做复杂 NLP，保证可解释。"""
    return {
        "资料/领取": ("资料", "领取", "发", "打印", "求", "链接"),
        "年级/作业": ("年级", "上册", "下册", "作业", "寒假"),
        "认同/夸赞": ("对", "是", "好", "厉害", "优秀", "谢谢"),
        "提问/求解": ("怎么", "为什么", "吗", "？", "?"),
        "争议/反驳": ("不是", "不对", "错", "但是", "可是"),
    }


def _data_limitations(rows: list[dict[str, Any]], analysis_rows: list[dict[str, Any]]) -> list[str]:
    """记录首轮结论使用边界，避免把代理指标误当播放量真值。"""
    nonzero_views = sum(1 for row in rows if row["view_count"] > 0)
    suspicious = len(rows) - len(analysis_rows)
    return [
        f"严格有效池中 view_count 非零视频仅 {nonzero_views}/{len(rows)}，播放量分析以 like/comment/share 构造的 engagement_score 作为代理。",
        f"已从因素分析中剔除 {suspicious} 条疑似指标错位极端值，但仍保留在原始数据和清单中便于复核。",
        "部分账号严格有效样本不足 50，账号级建议优先用于方向判断，不宜直接作为最终投放策略。",
        "评论补采仍在后台继续，后续新增评论会影响评论主题和评论触发结论。",
    ]


def _write_outputs(workspace: Path, output_dir: Path, deliverable_dir: Path, dataset: dict[str, Any], analysis: dict[str, Any]) -> None:
    """写出 JSON、Markdown、数据说明和交付目录。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    deliverable_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "first_round_analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    report = _render_report(analysis)
    usage = _render_usage(workspace, analysis)
    (output_dir / "first_round_analysis_report.md").write_text(report, encoding="utf-8")
    (output_dir / "data_usage_guide.md").write_text(usage, encoding="utf-8")
    _copy_package_files(workspace, output_dir, deliverable_dir, report, usage)
    _write_manifests(workspace, deliverable_dir)


def _copy_package_files(workspace: Path, output_dir: Path, deliverable_dir: Path, report: str, usage: str) -> None:
    """打包轻量数据和报告；视频大文件用清单引用，避免复制 9GB。"""
    (deliverable_dir / "报告_第一轮分析.md").write_text(report, encoding="utf-8")
    (deliverable_dir / "使用方法.md").write_text(usage, encoding="utf-8")
    shutil.copy2(output_dir / "first_round_analysis.json", deliverable_dir / "first_round_analysis.json")
    for rel in ("data/processed", "data/processed_strict_valid", "artifacts/collector", "artifacts/status", "artifacts/analysis/positive_factors_strict_valid_report.md"):
        _copy_path(workspace / rel, deliverable_dir / rel)


def _write_manifests(workspace: Path, deliverable_dir: Path) -> None:
    """生成原始产物和视频文件清单，保证不复制大文件也能定位全部数据。"""
    _write_file_manifest(workspace / "downloads", deliverable_dir / "downloads_manifest.csv")
    _write_file_manifest(workspace / "artifacts" / "collector", deliverable_dir / "collector_artifacts_manifest.csv")
    archive = workspace / "deliverables" / f"{RUN_ID}_analysis_bundle.zip"
    shutil.make_archive(str(archive.with_suffix("")), "zip", deliverable_dir)


def _render_report(analysis: dict[str, Any]) -> str:
    """渲染面向业务阅读的首轮分析报告。"""
    lines = ["# 短视频账号第一轮分析报告", "", "## 1. 数据口径", ""]
    lines.extend(_summary_report_lines(analysis))
    lines.extend(["", "## 2. 什么样的视频更容易带来更好的播放表现", ""])
    lines.extend(_factor_report_lines(analysis["play_analysis"], "播放代理热度"))
    lines.extend(["", "## 3. 什么样的视频更容易引起评论", ""])
    lines.extend(_factor_report_lines(analysis["comment_analysis"], "评论量"))
    lines.extend(["", "## 4. 评论主题洞察", ""])
    lines.extend(f"- {key}：{value}" for key, value in analysis["comment_topic_summary"]["topic_counts"].items())
    lines.extend(["", "## 5. 每个账号的改进点", ""])
    for account in analysis["account_analysis"]:
        lines.extend(_account_report_lines(account))
    lines.extend(["", "## 6. 使用边界", ""])
    lines.extend(f"- {item}" for item in analysis["data_limitations"])
    return "\n".join(lines) + "\n"


def _summary_report_lines(analysis: dict[str, Any]) -> list[str]:
    summary = analysis["sample_summary"]
    return [
        f"- 严格有效视频：{summary['strict_valid_video_count']}",
        f"- 因素分析使用视频：{summary['analysis_video_count']}（剔除疑似指标错位 {summary['suspicious_metric_video_count']} 条）",
        f"- 覆盖账号：{summary['account_count']}",
        f"- 有评论量视频：{summary['nonzero_comment_count']}",
        f"- 已采集评论文本：{summary['total_collected_comments']}",
        "- 播放量字段缺失较多，因此本轮用 `engagement_score = like_count + 3*comment_count + 2*share_count` 作为播放/传播表现代理。",
    ]


def _factor_report_lines(section: dict[str, Any], name: str) -> list[str]:
    lines = [f"- 高{name}阈值：`{section['high_threshold']}`，高表现样本数：`{section['high_count']}`"]
    readable = _feature_names()
    for item in section["feature_lifts"][:6]:
        lines.append(f"- `{readable[item['feature']]}`：高表现占比 {item['high_rate']:.1%}，普通组 {item['normal_rate']:.1%}，差值 {item['lift']:.1%}")
    lines.extend(["", "代表视频："])
    lines.extend(f"- {item['account_id']}｜{item['metric_value']}｜{item['title']}" for item in section["top_examples"][:5])
    return lines


def _account_report_lines(account: dict[str, Any]) -> list[str]:
    lines = [
        f"### {account['account_id']}",
        "",
        f"- 严格有效视频：{account['video_count']}；中位互动热度：{account['median_engagement_score']}；中位评论量：{account['median_comment_count']}",
        "- 改进点：",
    ]
    lines.extend(f"  - {item}" for item in account["improvement_points"])
    lines.append("- 账号内高表现样例：")
    lines.extend(f"  - {item['metric_value']}｜{item['title']}" for item in account["top_examples"][:3])
    lines.append("")
    return lines


def _render_usage(workspace: Path, analysis: dict[str, Any]) -> str:
    """说明数据包结构和复现方式。"""
    return f"""# 数据包使用方法

## 包内核心文件

- `报告_第一轮分析.md`：面向业务阅读的结论报告。
- `first_round_analysis.json`：机器可读分析结果。
- `data/processed/`：原始聚合数据集。
- `data/processed_strict_valid/`：推荐用于分析的严格有效数据集。
- `artifacts/collector/`：主页、详情、评论等原始采集 JSON。
- `artifacts/status/`：数据质量、补采状态、缺口报告。
- `downloads_manifest.csv`：本地视频文件清单；视频原文件体积约 9GB，仍在 `{workspace / 'downloads'}`，没有重复复制进 zip。
- `collector_artifacts_manifest.csv`：采集原始 JSON 产物清单。

## 推荐使用顺序

1. 先读 `报告_第一轮分析.md`。
2. 需要复核结论时查看 `first_round_analysis.json`。
3. 需要重新分析时优先使用 `data/processed_strict_valid/*.csv`。
4. 需要追溯原始页面采集结果时，根据 CSV 中的 artifact 路径或 `collector_artifacts_manifest.csv` 定位 JSON。

## 复现命令

```powershell
py -3.11 tools\\run_phase1_analysis_pipeline.py --workspace .
py -3.11 tools\\build_first_round_analysis.py .
```

## 当前分析样本

- 严格有效视频：{analysis['sample_summary']['strict_valid_video_count']}
- 因素分析使用视频：{analysis['sample_summary']['analysis_video_count']}
- 覆盖账号：{analysis['sample_summary']['account_count']}
- 已采集评论文本：{analysis['sample_summary']['total_collected_comments']}
"""


def _copy_path(src: Path, dst: Path) -> None:
    """复制文件或目录到交付包。"""
    if not src.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _write_file_manifest(root: Path, output: Path) -> None:
    """写出文件清单，避免遗漏大体积原始数据的位置。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["path", "relative_path", "size_bytes", "modified_at"])
        writer.writeheader()
        if not root.exists():
            return
        for item in sorted(root.rglob("*")):
            if item.is_file():
                stat = item.stat()
                writer.writerow({"path": str(item), "relative_path": str(item.relative_to(root)), "size_bytes": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat()})


def _video_briefs(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    """压缩视频样例字段，控制 JSON 和报告大小。"""
    return [{"video_id": row["video_id"], "account_id": row["account_id"], "title": row["title_clean"], "metric": metric, "metric_value": row[metric], "comment_count": row["comment_count"], "like_count": row["like_count"], "share_count": row["share_count"], "video_url": row["video_url"]} for row in rows]


def _feature_names() -> dict[str, str]:
    return {"has_question": "提问式标题", "has_exclaim": "强情绪感叹", "has_grade": "明确年级/阶段", "has_method": "方法/口诀/避坑", "has_material": "资料/练习/清单", "has_parent_child": "家长孩子场景", "has_interaction": "评论/留言互动钩子", "has_subject": "明确学科", "has_persona": "老师/学生/身份感"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _group_comments(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["video_id"]].append(row)
    return grouped


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except ValueError:
        return 0


def _safe_median(values: list[int]) -> int:
    return int(median(values)) if values else 0


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * ratio)))
    return ordered[index]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

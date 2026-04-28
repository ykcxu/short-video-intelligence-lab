from __future__ import annotations

import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20260428_second_round"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    """生成第二轮多模态分析报告和交付包。"""
    workspace = ROOT
    data = _load_second_round(workspace)
    analysis = _build_analysis(data)
    out_dir = workspace / "artifacts" / "analysis" / "second_round"
    deliver_dir = workspace / "deliverables" / RUN_ID
    _write_outputs(workspace, out_dir, deliver_dir, analysis)
    print(json.dumps({"ok": True, "output_dir": str(out_dir), "deliverable_dir": str(deliver_dir)}, ensure_ascii=False, indent=2))
    return 0


def _load_second_round(workspace: Path) -> dict[str, Any]:
    """读取第二轮多模态产物。"""
    names = {
        "inputs": "local_video_inputs_batch_second_round_20260428.json",
        "fusion": "multimodal_fusion_batch_second_round_20260428.json",
        "asr": "asr_features_batch_second_round_20260428.json",
        "ocr": "ocr_features_batch_second_round_20260428.json",
        "person": "person_visual_features_batch_second_round_20260428.json",
        "script": "script_structure_batch_second_round_20260428.json",
    }
    return {key: _load_json(workspace / "artifacts" / "analysis" / name) for key, name in names.items()}


def _build_analysis(data: dict[str, Any]) -> dict[str, Any]:
    """融合各模型输出，形成账号级和总体结论。"""
    rows = _join_rows(data)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_summary": _sample_summary(rows),
        "overall_findings": _overall_findings(rows),
        "account_findings": _account_findings(rows),
        "top_examples": sorted(_brief_rows(rows), key=lambda item: item["fit_score"], reverse=True),
        "limitations": _limitations(rows),
    }


def _join_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """按 video_id 合并输入、ASR、OCR、人物视觉、话术和融合评分。"""
    inputs = {item["video_id"]: item for item in data["inputs"].get("items", [])}
    asr = _index_results(data["asr"])
    ocr = _index_results(data["ocr"])
    person = _index_results(data["person"])
    script = _index_results(data["script"])
    rows = []
    for item in data["fusion"]["result"]["results"]:
        vid = item["video_id"]
        rows.append(_row_payload(item, inputs.get(vid, {}), asr.get(vid, {}), ocr.get(vid, {}), person.get(vid, {}), script.get(vid, {})))
    return rows


def _row_payload(fusion: dict[str, Any], inp: dict[str, Any], asr: dict[str, Any], ocr: dict[str, Any], person: dict[str, Any], script: dict[str, Any]) -> dict[str, Any]:
    """压缩单条视频的核心模型特征。"""
    fit = fusion.get("fit", {})
    metrics = inp.get("metrics", {})
    speech = asr.get("asr_speech", {})
    subtitle = ocr.get("ocr_subtitle", {})
    face = person.get("face_quality", {})
    pose = person.get("pose_quality", {})
    subject = person.get("person_subject", {})
    structure = script.get("script_structure", {})
    return {
        "video_id": fusion.get("video_id"),
        "source_name": fusion.get("source_name"),
        "video_url": fusion.get("video_url"),
        "fit_score": fit.get("fit_score", 0),
        "fit_level": fit.get("fit_level", ""),
        "engagement_score": metrics.get("engagement_score", 0),
        "comment_count": metrics.get("comment_count", 0),
        "speech_rate_cpm": speech.get("speech_rate_cpm", 0),
        "speech_duration_sec": speech.get("duration_sec", 0),
        "ocr_coverage": subtitle.get("coverage_ratio", 0),
        "ocr_readability": subtitle.get("readability_score", 0),
        "subtitle_consistency": subtitle.get("subtitle_consistency_score", 0),
        "face_ratio": face.get("face_ratio", 0),
        "face_center_score": face.get("center_score", 0),
        "pose_facing_score": pose.get("facing_camera_score", 0),
        "gesture_activity_score": pose.get("gesture_activity_score", 0),
        "person_count": subject.get("person_count", 0),
        "subject_ratio": subject.get("subject_ratio", 0),
        "background_clutter_score": subject.get("background_clutter_score", 0),
        "structure_completeness": structure.get("structure_completeness_score", 0),
        "knowledge_density": structure.get("knowledge_density_score", 0),
    }


def _sample_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总第二轮样本和模型命中情况。"""
    return {
        "video_count": len(rows),
        "account_count": len({row["source_name"] for row in rows}),
        "average_fit_score": round(_avg(rows, "fit_score"), 2),
        "average_speech_rate_cpm": round(_avg(rows, "speech_rate_cpm"), 2),
        "average_ocr_coverage": round(_avg(rows, "ocr_coverage"), 3),
        "average_structure_completeness": round(_avg(rows, "structure_completeness"), 3),
    }


def _overall_findings(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """输出模型层面的总体发现。"""
    high = _top_half(rows, "engagement_score")
    low = [row for row in rows if row not in high]
    return {
        "high_vs_low": {
            "fit_score_gap": round(_avg(high, "fit_score") - _avg(low, "fit_score"), 2),
            "ocr_readability_gap": round(_avg(high, "ocr_readability") - _avg(low, "ocr_readability"), 3),
            "face_center_gap": round(_avg(high, "face_center_score") - _avg(low, "face_center_score"), 3),
            "structure_gap": round(_avg(high, "structure_completeness") - _avg(low, "structure_completeness"), 3),
            "speech_rate_gap": round(_avg(high, "speech_rate_cpm") - _avg(low, "speech_rate_cpm"), 2),
        },
        "risk_counts": {
            "multi_person_or_unstable_subject": sum(1 for row in rows if row["person_count"] > 1.5),
            "low_structure": sum(1 for row in rows if row["structure_completeness"] < 0.3),
            "low_subtitle_consistency": sum(1 for row in rows if row["subtitle_consistency"] < 0.45),
        },
    }


def _account_findings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按账号聚合模型建议。"""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["source_name"]].append(row)
    return [_account_summary(name, items) for name, items in sorted(groups.items())]


def _account_summary(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """单账号第二轮模型画像。"""
    return {
        "account": name,
        "sample_count": len(rows),
        "average_fit_score": round(_avg(rows, "fit_score"), 2),
        "average_ocr_readability": round(_avg(rows, "ocr_readability"), 3),
        "average_structure_completeness": round(_avg(rows, "structure_completeness"), 3),
        "average_person_count": round(_avg(rows, "person_count"), 2),
        "recommendations": _recommendations(rows),
    }


def _recommendations(rows: list[dict[str, Any]]) -> list[str]:
    """根据模型短板生成账号建议。"""
    tips = []
    if _avg(rows, "structure_completeness") < 0.3:
        tips.append("强化口播结构：开头钩子、知识点、例子、结尾提问要更完整。")
    if _avg(rows, "subtitle_consistency") < 0.5:
        tips.append("提升字幕一致性：关键概念固定位置、减少跳字和多套字体。")
    if _avg(rows, "person_count") > 1.5:
        tips.append("降低多主体干扰：知识讲解尽量突出单一老师或单一视觉焦点。")
    if _avg(rows, "face_center_score") < 0.75:
        tips.append("优化人物居中和脸部清晰度，增强账号记忆点。")
    return tips or ["当前抽样视频基础画面质量较稳，优先迭代选题和评论互动钩子。"]


def _render_report(analysis: dict[str, Any]) -> str:
    """生成 Markdown 报告。"""
    lines = ["# 第二轮多模态分析报告", "", "## 1. 样本与模型", ""]
    summary = analysis["sample_summary"]
    lines += [
        f"- 抽样视频：{summary['video_count']} 条，覆盖账号：{summary['account_count']} 个。",
        "- 本轮实际跑通：ASR、OCR、人脸质量、人物主体、姿态关键点、话术结构、多模态融合评分。",
        f"- 平均融合评分：{summary['average_fit_score']}；平均 OCR 覆盖：{summary['average_ocr_coverage']:.1%}。",
    ]
    lines += ["", "## 2. 总体结论", ""]
    lines += _overall_lines(analysis["overall_findings"])
    lines += ["", "## 3. 账号改进建议", ""]
    for item in analysis["account_findings"]:
        lines += _account_lines(item)
    lines += ["", "## 4. 高融合评分样例", ""]
    for item in analysis["top_examples"][:8]:
        lines.append(f"- {item['source_name']}｜融合 {item['fit_score']}｜互动 {item['engagement_score']}｜{item['video_url']}")
    lines += ["", "## 5. 使用边界", ""]
    lines += [f"- {item}" for item in analysis["limitations"]]
    return "\n".join(lines) + "\n"


def _overall_lines(findings: dict[str, Any]) -> list[str]:
    gap = findings["high_vs_low"]
    risks = findings["risk_counts"]
    return [
        f"- 高互动样本相对低互动样本的融合评分差值：{gap['fit_score_gap']}。",
        f"- OCR 可读性差值：{gap['ocr_readability_gap']}；人物居中差值：{gap['face_center_gap']}；话术完整度差值：{gap['structure_gap']}。",
        f"- 多主体或主体不稳风险：{risks['multi_person_or_unstable_subject']} 条；话术结构偏弱：{risks['low_structure']} 条；字幕一致性偏弱：{risks['low_subtitle_consistency']} 条。",
        "- 当前 16 条样本没有证明“融合评分越高互动越高”；它更像是质量体检，用来发现可改进短板。",
        "- 主要短板集中在字幕信息组织、单主体稳定度和口播结构完整度，而不是基础清晰度。",
    ]


def _account_lines(item: dict[str, Any]) -> list[str]:
    lines = [
        f"### {item['account']}",
        f"- 样本：{item['sample_count']}；平均融合评分：{item['average_fit_score']}；平均话术完整度：{item['average_structure_completeness']}",
        "- 建议：",
    ]
    lines += [f"  - {tip}" for tip in item["recommendations"]]
    return lines + [""]


def _write_outputs(workspace: Path, out_dir: Path, deliver_dir: Path, analysis: dict[str, Any]) -> None:
    """写报告、JSON 和 zip 包。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    deliver_dir.mkdir(parents=True, exist_ok=True)
    report = _render_report(analysis)
    (out_dir / "second_round_multimodal_analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "second_round_multimodal_report.md").write_text(report, encoding="utf-8")
    (deliver_dir / "第二轮多模态分析报告.md").write_text(report, encoding="utf-8")
    shutil.copy2(out_dir / "second_round_multimodal_analysis.json", deliver_dir / "second_round_multimodal_analysis.json")
    for name in _artifact_names():
        shutil.copy2(workspace / "artifacts" / "analysis" / name, deliver_dir / name)
    shutil.make_archive(str((workspace / "deliverables" / f"{RUN_ID}_multimodal_bundle").with_suffix("")), "zip", deliver_dir)


def _limitations(rows: list[dict[str, Any]]) -> list[str]:
    return [
        f"本轮是每账号 2 条、总计 {len(rows)} 条的分层抽样，不代表全量 362 条视频的最终模型统计。",
        "ASR 使用 tiny CPU 模型，中文口播存在错字，适合做结构和语速参考，不适合直接引用逐字稿。",
        "OCR 和姿态结果基于每条 3 帧抽样，适合判断趋势，不能替代逐帧精细标注。",
    ]


def _brief_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ["video_id", "source_name", "video_url", "fit_score", "engagement_score", "comment_count"]
    return [{key: row.get(key) for key in keys} for row in rows]


def _artifact_names() -> list[str]:
    return [
        "local_video_inputs_batch_second_round_20260428.json",
        "asr_features_batch_second_round_20260428.json",
        "ocr_features_batch_second_round_20260428.json",
        "person_visual_features_batch_second_round_20260428.json",
        "script_structure_batch_second_round_20260428.json",
        "multimodal_fusion_batch_second_round_20260428.json",
    ]


def _index_results(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["video_id"]: item for item in payload.get("result", {}).get("results", [])}


def _top_half(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda item: item[key], reverse=True)[: max(1, len(rows) // 2)]


def _avg(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row.get(key) or 0) for row in rows]
    return mean(values) if values else 0


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())

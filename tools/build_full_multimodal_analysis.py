from __future__ import annotations
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20260428_full_multimodal"
PART_GLOB = "*_batch_strict_all_20260428_part*.json"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def main() -> int:
    data = _load_all_outputs(ROOT)
    rows = _join_rows(data)
    analysis = _build_analysis(rows, data)
    out_dir = ROOT / "artifacts" / "analysis" / "full_multimodal"
    deliver_dir = ROOT / "deliverables" / RUN_ID
    _write_outputs(out_dir, deliver_dir, analysis)
    print(json.dumps({"ok": True, "rows": len(rows), "out_dir": str(out_dir)}, ensure_ascii=False, indent=2))
    return 0
def _load_all_outputs(workspace: Path) -> dict[str, Any]:
    analysis_dir = workspace / "artifacts" / "analysis"
    inputs = _load_json(workspace / "artifacts" / "analysis-inputs" / "local_video_inputs_strict_all.json")
    status = _maybe_load_json(workspace / "artifacts" / "status" / "multimodal_full_batch_status.json")
    return {
        "inputs": inputs,
        "status": status,
        "fusion": _load_parts(analysis_dir, "multimodal_fusion_batch"),
        "asr": _load_parts(analysis_dir, "asr_features_batch"),
        "ocr": _load_parts(analysis_dir, "ocr_features_batch"),
        "person": _load_parts(analysis_dir, "person_visual_features_batch"),
        "script": _load_parts(analysis_dir, "script_structure_batch"),
    }
def _load_parts(analysis_dir: Path, prefix: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(analysis_dir.glob(PART_GLOB)):
        if not path.name.startswith(prefix):
            continue
        payload = _load_json(path)
        rows.extend(payload.get("result", {}).get("results", []))
    return rows
def _join_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = {item["video_id"]: item for item in data["inputs"].get("items", [])}
    asr = _index(data["asr"])
    ocr = _index(data["ocr"])
    person = _index(data["person"])
    script = _index(data["script"])
    rows = []
    for fusion in data["fusion"]:
        vid = fusion["video_id"]
        rows.append(_row_payload(fusion, inputs.get(vid, {}), asr.get(vid, {}), ocr.get(vid, {}), person.get(vid, {}), script.get(vid, {})))
    return rows
def _row_payload(fusion: dict[str, Any], inp: dict[str, Any], asr: dict[str, Any], ocr: dict[str, Any], person: dict[str, Any], script: dict[str, Any]) -> dict[str, Any]:
    fit, metrics = fusion.get("fit", {}), inp.get("metrics", {})
    speech, subtitle = asr.get("asr_speech", {}), ocr.get("ocr_subtitle", {})
    face, pose = person.get("face_quality", {}), person.get("pose_quality", {})
    subject, structure = person.get("person_subject", {}), script.get("script_structure", {})
    return {
        "video_id": fusion.get("video_id"),
        "source_name": fusion.get("source_name"),
        "video_url": fusion.get("video_url"),
        "fit_score": _num(fit.get("fit_score")),
        "fit_level": fit.get("fit_level", ""),
        "engagement_score": _num(metrics.get("engagement_score")),
        "like_count": _num(metrics.get("like_count")),
        "comment_count": _num(metrics.get("comment_count")),
        "share_count": _num(metrics.get("share_count")),
        "speech_rate_cpm": _num(speech.get("speech_rate_cpm")),
        "speech_duration_sec": _num(speech.get("duration_sec")),
        "opening_hook_score": _num(speech.get("opening_hook_score")),
        "ocr_coverage": _num(subtitle.get("coverage_ratio")),
        "ocr_readability": _num(subtitle.get("readability_score")),
        "subtitle_consistency": _num(subtitle.get("subtitle_consistency_score")),
        "face_detected": bool(face.get("face_detected")),
        "face_ratio": _num(face.get("face_ratio")),
        "face_center_score": _num(face.get("center_score")),
        "pose_detected": bool(pose.get("pose_detected")),
        "pose_facing_score": _num(pose.get("facing_camera_score")),
        "gesture_activity_score": _num(pose.get("gesture_activity_score")),
        "person_count": _num(subject.get("person_count")),
        "subject_ratio": _num(subject.get("subject_ratio")),
        "background_clutter_score": _num(subject.get("background_clutter_score")),
        "structure_completeness": _num(structure.get("structure_completeness_score")),
        "knowledge_density": _num(structure.get("knowledge_density_score")),
    }
def _build_analysis(rows: list[dict[str, Any]], data: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": RUN_ID,
        "status": _status_summary(data.get("status", {})),
        "sample_summary": _sample_summary(rows),
        "overall_findings": _overall_findings(rows),
        "account_findings": _account_findings(rows),
        "top_fit_examples": _brief_rows(sorted(rows, key=lambda item: item["fit_score"], reverse=True)[:20]),
        "top_engagement_examples": _brief_rows(sorted(rows, key=lambda item: item["engagement_score"], reverse=True)[:20]),
        "limitations": _limitations(rows),
    }
def _status_summary(status: dict[str, Any]) -> dict[str, Any]:
    keys = ["ok", "total_video_count", "chunk_count", "completed_chunk_count", "failed_count", "processed_video_count"]
    return {key: status.get(key) for key in keys}
def _sample_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "video_count": len(rows),
        "account_count": len({row["source_name"] for row in rows}),
        "average_fit_score": round(_avg(rows, "fit_score"), 2),
        "fit_level_distribution": dict(Counter(row["fit_level"] for row in rows)),
        "average_speech_rate_cpm": round(_avg(rows, "speech_rate_cpm"), 2),
        "average_ocr_coverage": round(_avg(rows, "ocr_coverage"), 3),
        "average_structure_completeness": round(_avg(rows, "structure_completeness"), 3),
        "face_detected_count": sum(1 for row in rows if row["face_detected"]),
        "pose_detected_count": sum(1 for row in rows if row["pose_detected"]),
    }
def _overall_findings(rows: list[dict[str, Any]]) -> dict[str, Any]:
    high = _top_half(rows, "engagement_score")
    low = [row for row in rows if row not in high]
    return {
        "high_vs_low": _gap_summary(high, low),
        "risk_counts": _risk_counts(rows),
        "correlation_hint": _correlation_hint(rows),
    }
def _gap_summary(high: list[dict[str, Any]], low: list[dict[str, Any]]) -> dict[str, float]:
    keys = ["fit_score", "ocr_readability", "face_center_score", "structure_completeness", "speech_rate_cpm", "subtitle_consistency"]
    return {f"{key}_gap": round(_avg(high, key) - _avg(low, key), 3) for key in keys}
def _risk_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "multi_person_or_unstable_subject": sum(1 for row in rows if row["person_count"] > 1.5),
        "low_structure": sum(1 for row in rows if row["structure_completeness"] < 0.3),
        "low_subtitle_consistency": sum(1 for row in rows if row["subtitle_consistency"] < 0.45),
        "face_small_or_offcenter": sum(1 for row in rows if row["face_ratio"] < 0.02 or row["face_center_score"] < 0.7),
        "speech_too_fast": sum(1 for row in rows if row["speech_rate_cpm"] > 360),
    }
def _correlation_hint(rows: list[dict[str, Any]]) -> dict[str, float]:
    keys = ["fit_score", "ocr_readability", "structure_completeness", "face_center_score", "subtitle_consistency"]
    return {key: round(_pearson(rows, key, "engagement_score"), 3) for key in keys}
def _account_findings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["source_name"]].append(row)
    return [_account_summary(name, items) for name, items in sorted(groups.items())]
def _account_summary(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "account": name,
        "video_count": len(rows),
        "average_fit_score": round(_avg(rows, "fit_score"), 2),
        "average_engagement_score": round(_avg(rows, "engagement_score"), 2),
        "average_ocr_readability": round(_avg(rows, "ocr_readability"), 3),
        "average_structure_completeness": round(_avg(rows, "structure_completeness"), 3),
        "average_person_count": round(_avg(rows, "person_count"), 2),
        "risk_counts": _risk_counts(rows),
        "recommendations": _recommendations(rows),
    }
def _recommendations(rows: list[dict[str, Any]]) -> list[str]:
    tips = []
    if _avg(rows, "structure_completeness") < 0.3:
        tips.append("强化口播结构：开头钩子、痛点、方法、例子、结尾提问要更完整。")
    if _avg(rows, "subtitle_consistency") < 0.5:
        tips.append("统一字幕风格：关键词位置、字体层级和节奏保持一致。")
    if _avg(rows, "person_count") > 1.5:
        tips.append("降低多主体干扰：知识讲解尽量突出单一老师或单一视觉焦点。")
    if _avg(rows, "face_center_score") < 0.75:
        tips.append("优化人物居中和脸部清晰度，增强账号记忆点。")
    if _avg(rows, "speech_rate_cpm") > 340:
        tips.append("适当降低语速并增加停顿，方便学生和家长跟上知识点。")
    return tips or ["基础画面质量较稳，优先迭代选题、标题和评论互动钩子。"]
def _render_report(analysis: dict[str, Any]) -> str:
    s, f = analysis["sample_summary"], analysis["overall_findings"]
    lines = ["# 全量视频多模态分析报告", "", "## 1. 覆盖范围", ""]
    lines += _coverage_lines(s, analysis["status"])
    lines += ["", "## 2. 总体结论", ""] + _overall_lines(f)
    lines += ["", "## 3. 账号改进建议", ""]
    for item in analysis["account_findings"]:
        lines += _account_lines(item)
    lines += ["", "## 4. 高融合评分样例", ""] + _example_lines(analysis["top_fit_examples"][:10])
    lines += ["", "## 5. 高互动样例", ""] + _example_lines(analysis["top_engagement_examples"][:10])
    lines += ["", "## 6. 使用边界", ""] + [f"- {item}" for item in analysis["limitations"]]
    return "\n".join(lines) + "\n"
def _coverage_lines(summary: dict[str, Any], status: dict[str, Any]) -> list[str]:
    return [
        f"- 全量严格有效视频：{summary['video_count']} 条，覆盖账号：{summary['account_count']} 个。",
        f"- 分片处理：{status.get('completed_chunk_count')}/{status.get('chunk_count')}；失败：{status.get('failed_count')}；已处理：{status.get('processed_video_count')}。",
        "- 已跑通模型：ASR、OCR、人脸质量、姿态关键点、人物主体检测、口播话术结构、多模态融合评分。",
        f"- 平均融合评分：{summary['average_fit_score']}；评分分布：{summary['fit_level_distribution']}。",
        f"- 人脸命中：{summary['face_detected_count']}；姿态命中：{summary['pose_detected_count']}；平均 OCR 覆盖：{summary['average_ocr_coverage']:.1%}。",
    ]
def _overall_lines(findings: dict[str, Any]) -> list[str]:
    gap, risks, corr = findings["high_vs_low"], findings["risk_counts"], findings["correlation_hint"]
    return [
        f"- 高互动组相对低互动组的融合评分差值：{gap['fit_score_gap']}；字幕一致性差值：{gap['subtitle_consistency_gap']}；话术完整度差值：{gap['structure_completeness_gap']}。",
        f"- 与互动代理分的简单相关：融合评分 {corr['fit_score']}，OCR 可读性 {corr['ocr_readability']}，话术完整度 {corr['structure_completeness']}。",
        f"- 风险规模：多主体/主体不稳 {risks['multi_person_or_unstable_subject']} 条；话术结构弱 {risks['low_structure']} 条；字幕一致性弱 {risks['low_subtitle_consistency']} 条。",
        "- 当前多模态分更适合做制作质量体检；播放/评论提升仍要结合选题、标题、账号类型和发布时间交叉验证。",
    ]
def _account_lines(item: dict[str, Any]) -> list[str]:
    lines = [
        f"### {item['account']}",
        f"- 视频：{item['video_count']}；平均融合评分：{item['average_fit_score']}；平均互动代理分：{item['average_engagement_score']}。",
        f"- 平均话术完整度：{item['average_structure_completeness']}；平均 OCR 可读性：{item['average_ocr_readability']}；平均人物数：{item['average_person_count']}。",
        "- 建议：",
    ]
    lines += [f"  - {tip}" for tip in item["recommendations"]]
    return lines + [""]
def _example_lines(items: list[dict[str, Any]]) -> list[str]:
    return [f"- {item['source_name']}｜融合 {item['fit_score']}｜互动 {item['engagement_score']}｜评论 {item['comment_count']}｜{item['video_url']}" for item in items]
def _write_outputs(out_dir: Path, deliver_dir: Path, analysis: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    deliver_dir.mkdir(parents=True, exist_ok=True)
    report = _render_report(analysis)
    json_path = out_dir / "full_multimodal_analysis.json"
    report_path = out_dir / "full_multimodal_report.md"
    json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(report, encoding="utf-8")
    shutil.copy2(json_path, deliver_dir / json_path.name)
    (deliver_dir / "全量视频多模态分析报告.md").write_text(report, encoding="utf-8")
    shutil.copy2(ROOT / "artifacts" / "status" / "multimodal_full_batch_status.json", deliver_dir / "multimodal_full_batch_status.json")
    shutil.make_archive(str((ROOT / "deliverables" / f"{RUN_ID}_bundle").with_suffix("")), "zip", deliver_dir)
def _limitations(rows: list[dict[str, Any]]) -> list[str]:
    return [
        f"本报告覆盖当前本地严格有效的 {len(rows)} 条已下载视频，不包含后续新增采集的视频。",
        "ASR 使用本地 CPU 模型，中文转写存在错字；适合做结构/语速参考，不适合直接引用逐字稿。",
        "OCR 和姿态基于每条 3 帧抽样，适合批量趋势判断，不能替代逐帧人工标注。",
        "view_count 当前为 0，报告沿用点赞、评论、转发组合的互动代理分。",
    ]
def _brief_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ["video_id", "source_name", "video_url", "fit_score", "engagement_score", "comment_count"]
    return [{key: row.get(key) for key in keys} for row in rows]
def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["video_id"]: item for item in rows}
def _top_half(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda item: item[key], reverse=True)[: max(1, len(rows) // 2)]
def _pearson(rows: list[dict[str, Any]], left: str, right: str) -> float:
    xs, ys = [_num(row.get(left)) for row in rows], [_num(row.get(right)) for row in rows]
    if len(xs) < 2:
        return 0.0
    mx, my = mean(xs), mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / ((vx * vy) ** 0.5) if vx and vy else 0.0
def _avg(rows: list[dict[str, Any]], key: str) -> float:
    values = [_num(row.get(key)) for row in rows]
    return mean(values) if values else 0.0
def _num(value: Any) -> float:
    return float(value or 0)
def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
def _maybe_load_json(path: Path) -> dict[str, Any]:
    return _load_json(path) if path.exists() else {}
if __name__ == "__main__":
    raise SystemExit(main())

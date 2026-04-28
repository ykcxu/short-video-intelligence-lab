from __future__ import annotations
import csv, importlib.util, json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any
import fitz, pdfplumber
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "pdf"
TMP_DIR = ROOT / "tmp" / "pdfs"
PDF_PATH = OUT_DIR / "20260428_短视频账号全量数据分析报告_修正版_低赞低评高转发.pdf"
AUDIT_PATH = OUT_DIR / "20260428_异常互动指标核查.csv"
PREVIEW_PATH = TMP_DIR / "20260428_短视频账号全量数据分析报告_page1.png"
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
METRIC_LIMIT = 1000
COLUMNS = ["序", "账号", "视频ID", "赞", "评", "转", "互动", "指标", "融合", "等级", "语速", "OCR", "字幕", "出镜", "姿态", "主体", "话术", "风险"]
WIDTHS = [8, 30, 32, 13, 12, 13, 16, 15, 13, 12, 13, 12, 12, 12, 12, 12, 12, 31]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def main() -> int:
    _register_fonts()
    rows = _mark_metric_quality(_load_rows())
    analysis = _build_analysis(rows)
    _write_audit(rows)
    _write_pdf(_build_story(analysis, rows))
    preview = _render_preview(PDF_PATH)
    checks = _check_pdf(PDF_PATH)
    print(json.dumps({"ok": True, "pdf": str(PDF_PATH), "audit_csv": str(AUDIT_PATH), "preview": str(preview), **checks}, ensure_ascii=False, indent=2))
    return 0
def _load_full_module():
    spec = importlib.util.spec_from_file_location("full_mm", ROOT / "tools" / "build_full_multimodal_analysis.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module
def _load_rows() -> list[dict[str, Any]]:
    module = _load_full_module()
    return module._join_rows(module._load_all_outputs(ROOT))
def _is_suspicious_metrics(row: dict[str, Any]) -> tuple[bool, str]:
    # 只隔离“点赞/评论很低但转发极高”的疑似解析错位；上万播放/点赞/评论本身不再判错。
    like_count = float(row.get("like_count") or 0)
    comment_count = float(row.get("comment_count") or 0)
    share_count = float(row.get("share_count") or 0)
    if share_count > METRIC_LIMIT and like_count < METRIC_LIMIT and comment_count < 50:
        return True, "低赞低评高转发"
    return False, ""
def _mark_metric_quality(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    marked = []
    for row in rows:
        copied = dict(row)
        suspicious, reason = _is_suspicious_metrics(copied)
        copied["metric_suspicious"] = suspicious
        copied["suspicious_reason"] = reason
        copied["trusted_engagement_score"] = 0 if copied["metric_suspicious"] else copied["engagement_score"]
        marked.append(copied)
    return marked
def _build_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trusted = [row for row in rows if not row["metric_suspicious"]]
    return {"generated_at": datetime.now().isoformat(), "summary": _summary(rows, trusted), "overall": _overall(rows, trusted), "accounts": _accounts(rows, trusted), "top_trusted": sorted(trusted, key=lambda x: x["trusted_engagement_score"], reverse=True)[:20]}
def _summary(rows: list[dict[str, Any]], trusted: list[dict[str, Any]]) -> dict[str, Any]:
    return {"video_count": len(rows), "trusted_metric_count": len(trusted), "suspicious_metric_count": len(rows) - len(trusted), "account_count": len({r["source_name"] for r in rows}), "average_fit_score": round(_avg(rows, "fit_score"), 2), "average_trusted_engagement": round(_avg(trusted, "trusted_engagement_score"), 2)}
def _overall(rows: list[dict[str, Any]], trusted: list[dict[str, Any]]) -> dict[str, Any]:
    high = sorted(trusted, key=lambda x: x["trusted_engagement_score"], reverse=True)[: max(1, len(trusted)//4)]
    low = sorted(trusted, key=lambda x: x["trusted_engagement_score"])[: max(1, len(trusted)//4)]
    return {"risk_counts": _risk_counts(rows), "trusted_gap": {"fit_score_gap": round(_avg(high,"fit_score") - _avg(low,"fit_score"), 2), "comment_gap": round(_avg(high,"comment_count") - _avg(low,"comment_count"), 2), "structure_gap": round(_avg(high,"structure_completeness") - _avg(low,"structure_completeness"), 3)}}
def _accounts(rows: list[dict[str, Any]], trusted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    all_groups, trusted_groups = defaultdict(list), defaultdict(list)
    for row in rows: all_groups[row["source_name"]].append(row)
    for row in trusted: trusted_groups[row["source_name"]].append(row)
    return [_account(name, all_groups[name], trusted_groups.get(name, [])) for name in sorted(all_groups)]
def _account(name: str, rows: list[dict[str, Any]], trusted: list[dict[str, Any]]) -> dict[str, Any]:
    return {"account": name, "video_count": len(rows), "trusted_count": len(trusted), "suspicious_count": len(rows)-len(trusted), "average_fit_score": round(_avg(rows,"fit_score"), 2), "trusted_engagement_avg": round(_avg(trusted,"trusted_engagement_score"), 2), "average_ocr_readability": round(_avg(rows,"ocr_readability"), 3), "average_structure": round(_avg(rows,"structure_completeness"), 3), "average_person_count": round(_avg(rows,"person_count"), 2), "recommendations": _recommendations(rows)}
def _risk_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"主体不稳": sum(1 for r in rows if r["person_count"] > 1.5), "话术弱": sum(1 for r in rows if r["structure_completeness"] < 0.3), "字幕弱": sum(1 for r in rows if r["subtitle_consistency"] < 0.45), "语速快": sum(1 for r in rows if r["speech_rate_cpm"] > 360)}
def _recommendations(rows: list[dict[str, Any]]) -> list[str]:
    tips = []
    if _avg(rows, "structure_completeness") < 0.3: tips.append("补齐钩子-痛点-方法-例子-行动的话术结构。")
    if _avg(rows, "subtitle_consistency") < 0.5: tips.append("统一字幕样式和关键词位置，减少跳字和多字体。")
    if _avg(rows, "person_count") > 1.5: tips.append("减少多人/复杂背景，突出单一老师或单一视觉焦点。")
    if _avg(rows, "face_center_score") < 0.75: tips.append("提高人物居中、脸部清晰度和稳定出镜。")
    return tips or ["画面基础较稳，优先优化选题、标题和评论钩子。"]
def _build_story(analysis: dict[str, Any], rows: list[dict[str, Any]]) -> list[Any]:
    styles = _styles(); story: list[Any] = []
    story += _cover(styles, analysis)
    story += _methodology(styles)
    story += _overall_section(styles, analysis)
    story += _account_section(styles, analysis)
    story += _growth_actions(styles)
    story += _top_section(styles, analysis)
    story += _video_table_section(styles, rows)
    return story
def _cover(styles: dict[str, ParagraphStyle], analysis: dict[str, Any]) -> list[Any]:
    s = analysis["summary"]
    return [Spacer(1, 18*mm), Paragraph("短视频账号全量数据分析报告（修正版）", styles["title"]), Spacer(1, 7*mm), Paragraph("已隔离低赞低评高转发的可疑互动指标", styles["subtitle"]), Spacer(1, 12*mm), _kv_table([["生成时间", datetime.now().strftime("%Y-%m-%d %H:%M")], ["覆盖视频", f"{s['video_count']} 条"], ["可信互动样本", f"{s['trusted_metric_count']} 条"], ["可疑指标样本", f"{s['suspicious_metric_count']} 条"], ["平均融合评分", s["average_fit_score"]]]), Spacer(1, 10*mm), Paragraph("核心结论", styles["h1"]), *_bullet(styles, ["上万播放、点赞、评论本身可能是真实数据，不再一刀切标错。", f"本版采用业务口径：转发超过 {METRIC_LIMIT}，且点赞低于 {METRIC_LIMIT}、评论低于 50 时，才标记为可疑。", "所有视频仍保留多模态评价；涉及播放、评论、点赞、转发的结论只使用可信互动样本。"]), PageBreak()]
def _methodology(styles: dict[str, ParagraphStyle]) -> list[Any]:
    weights = [["组件", "权重", "依据"], ["基础画面", "16%", "竖屏、时长、亮度、对比度、字幕区域"], ["人脸/出镜", "16%", "人脸命中、居中、清晰度、表情、遮挡"], ["姿态", "12%", "正向镜头、上半身、手势、稳定度"], ["人物主体", "12%", "人数、主体占比、居中、背景杂乱度"], ["OCR 字幕", "14%", "字幕覆盖、可读性、关键词、一致性"], ["ASR 语音", "14%", "语速、停顿、开场钩子"], ["口播结构", "16%", "钩子、痛点、方法、例子、行动召唤"]]
    return [Paragraph("1. 我们如何评测视频", styles["h1"]), *_bullet(styles, ["数据来源：已登录浏览器会话采集主页、详情、评论和本地视频，不使用抖音官方接口。", "互动代理分公式仍为 like_count + 3*comment_count + 2*share_count，但只在可信指标样本内使用。", "低赞低评高转发的疑似错位指标不直接删除，而是在明细表标记为“可疑”，便于后续人工复核或重采。", "融合评分 0-100 分：75 以上 high，55-74 medium，55 以下 low。", "不输出不可解释的颜值绝对分，统一转成出镜质量、居中、清晰度和表情积极度。"]), Spacer(1,4*mm), _small_table(weights, [34,18,170]), PageBreak()]
def _overall_section(styles: dict[str, ParagraphStyle], analysis: dict[str, Any]) -> list[Any]:
    s, o = analysis["summary"], analysis["overall"]
    rows = [["指标", "数值"], ["全量视频", s["video_count"]], ["可信互动样本", s["trusted_metric_count"]], ["可疑互动样本", s["suspicious_metric_count"]], ["可信互动均值", s["average_trusted_engagement"]], ["高低互动融合分差", o["trusted_gap"]["fit_score_gap"]], ["高低互动评论差", o["trusted_gap"]["comment_gap"]], ["高低互动话术差", o["trusted_gap"]["structure_gap"]]]
    risks = o["risk_counts"]
    return [Paragraph("2. 全量视频综合评价", styles["h1"]), *_bullet(styles, [f"本次全量多模态覆盖 {s['video_count']} 条视频，其中 {s['trusted_metric_count']} 条互动指标可信，{s['suspicious_metric_count']} 条互动指标需复核。", f"制作侧主要风险：主体不稳 {risks['主体不稳']} 条、话术弱 {risks['话术弱']} 条、字幕弱 {risks['字幕弱']} 条、语速快 {risks['语速快']} 条。", "修正后，上万点赞/评论不再被直接判错；重点隔离低赞低评但转发异常高的样本。"]), Spacer(1,4*mm), _small_table(rows, [55,55]), PageBreak()]
def _account_section(styles: dict[str, ParagraphStyle], analysis: dict[str, Any]) -> list[Any]:
    rows = [["主页", "视频", "可信", "可疑", "融合", "可信互动均值", "话术", "主体", "主要建议"]]
    for item in analysis["accounts"]:
        rows.append([item["account"], item["video_count"], item["trusted_count"], item["suspicious_count"], item["average_fit_score"], item["trusted_engagement_avg"], item["average_structure"], item["average_person_count"], "；".join(item["recommendations"][:2])])
    return [Paragraph("3. 每个主页的综合评价", styles["h1"]), _small_table(rows, [36,13,13,13,16,25,16,16,106]), PageBreak()]
def _growth_actions(styles: dict[str, ParagraphStyle]) -> list[Any]:
    items = ["增粉：固定老师IP + 年级/学科 + 可复制栏目，结尾给明确关注理由。", "增粉：把可信高互动选题做系列化，主页置顶代表作，减少随机选题。", "评论：标题和结尾设置低门槛问题，例如 A/B 选择、年级报数、留言领取资料。", "评论：围绕家长痛点发问，如背了就忘、作文不会开头、三年级阅读理解。", "点赞：前三秒给结果或反常识结论，中段给可截图清单，结尾提示点赞收藏。", "转发：多做资料型、考试节点型、家长群可传播内容。", "制作：全量最明显短板仍是话术结构，统一按钩子-痛点-方法-例子-行动写脚本。", "画面：减少多人、复杂背景和主体漂移，强化单一老师记忆点。"]
    return [Paragraph("4. 增加粉丝、评论、点赞的具体措施", styles["h1"]), *_bullet(styles, items), PageBreak()]
def _top_section(styles: dict[str, ParagraphStyle], analysis: dict[str, Any]) -> list[Any]:
    rows = [["账号", "视频ID", "可信互动", "赞", "评", "转", "融合", "风险"]]
    for row in analysis["top_trusted"][:15]:
        rows.append([_short(row["source_name"],12), row["video_id"], int(row["trusted_engagement_score"]), int(row["like_count"]), int(row["comment_count"]), int(row["share_count"]), int(row["fit_score"]), _risk_text(row)])
    return [Paragraph("5. 可信互动 Top 视频", styles["h1"]), _small_table(rows, [34,42,24,16,16,16,16,70]), PageBreak()]
def _video_table_section(styles: dict[str, ParagraphStyle], rows: list[dict[str, Any]]) -> list[Any]:
    ordered = sorted(rows, key=lambda x: (x["source_name"], x["metric_suspicious"], -x["trusted_engagement_score"]))
    data = [COLUMNS] + [_video_row(i, row) for i, row in enumerate(ordered, 1)]
    table = Table(_paragraphize(data, styles["cell"]), colWidths=[w*mm for w in WIDTHS], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1f4e79")), ("TEXTCOLOR",(0,0),(-1,0),colors.white), ("FONTNAME",(0,0),(-1,-1),"MSYH"), ("FONTSIZE",(0,0),(-1,-1),5.5), ("GRID",(0,0),(-1,-1),0.25,colors.HexColor("#d9e2f3")), ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f7fbff")])]))
    return [Paragraph("6. 所有视频详细评分表", styles["h1"]), Paragraph("说明：指标=可信/可疑；可疑互动值不参与账号均值、Top 视频和增长结论。", styles["body"]), Spacer(1,3*mm), table]
def _video_row(i: int, row: dict[str, Any]) -> list[Any]:
    return [i, _short(row["source_name"],9), row["video_id"], int(row["like_count"]), int(row["comment_count"]), int(row["share_count"]), int(row["engagement_score"]), "可疑" if row["metric_suspicious"] else "可信", int(row["fit_score"]), row["fit_level"], int(row["speech_rate_cpm"]), _fmt(row["ocr_readability"],2), _fmt(row["subtitle_consistency"],2), _fmt(row["face_center_score"],2), _fmt(row["pose_facing_score"],2), _fmt(row["person_count"],1), _fmt(row["structure_completeness"],2), _risk_text(row)]
def _risk_text(row: dict[str, Any]) -> str:
    risks = []
    if row.get("metric_suspicious"): risks.append("指标可疑")
    if row["person_count"] > 1.5: risks.append("主体不稳")
    if row["structure_completeness"] < 0.3: risks.append("话术弱")
    if row["subtitle_consistency"] < 0.45: risks.append("字幕弱")
    if row["speech_rate_cpm"] > 360: risks.append("语速快")
    return "、".join(risks) or "-"
def _write_audit(rows: list[dict[str, Any]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["video_id","source_name","video_url","like_count","comment_count","share_count","view_count","engagement_score","suspicious_reason"]
    with AUDIT_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for row in rows:
            if row["metric_suspicious"]: writer.writerow({k: row.get(k, "") for k in fields})
def _write_pdf(story: list[Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(PDF_PATH), pagesize=landscape(A4), rightMargin=10*mm, leftMargin=10*mm, topMargin=10*mm, bottomMargin=10*mm)
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
def _render_preview(pdf_path: Path) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True); doc = fitz.open(pdf_path); pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.3,1.3), alpha=False); pix.save(PREVIEW_PATH); doc.close(); return PREVIEW_PATH
def _check_pdf(pdf_path: Path) -> dict[str, Any]:
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages[:6])
        return {"pages": len(pdf.pages), "size_bytes": pdf_path.stat().st_size, "has_metric_audit": "可疑互动指标" in text, "has_actions": "增加粉丝" in text}
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {"title": ParagraphStyle("title", parent=base["Title"], fontName="MSYH-Bold", fontSize=26, leading=32, alignment=TA_CENTER, textColor=colors.HexColor("#17365d")), "subtitle": ParagraphStyle("subtitle", fontName="MSYH", fontSize=14, leading=19, alignment=TA_CENTER, textColor=colors.HexColor("#666666")), "h1": ParagraphStyle("h1", fontName="MSYH-Bold", fontSize=15, leading=20, spaceAfter=6, textColor=colors.HexColor("#1f4e79")), "body": ParagraphStyle("body", fontName="MSYH", fontSize=9.3, leading=14), "cell": ParagraphStyle("cell", fontName="MSYH", fontSize=5.4, leading=6.8, alignment=TA_LEFT)}
def _register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("MSYH", str(FONT_PATH))); pdfmetrics.registerFont(TTFont("MSYH-Bold", str(FONT_PATH)))
def _small_table(rows: list[list[Any]], widths: list[int]) -> Table:
    styles = _styles(); table = Table(_paragraphize(rows, styles["body"]), colWidths=[w*mm for w in widths], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#ddebf7")), ("FONTNAME",(0,0),(-1,-1),"MSYH"), ("FONTSIZE",(0,0),(-1,-1),8), ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#b4c6e7")), ("VALIGN",(0,0),(-1,-1),"TOP")]))
    return table
def _kv_table(rows: list[list[Any]]) -> Table: return _small_table([["项目","内容"], *rows], [42,130])
def _paragraphize(rows: list[list[Any]], style: ParagraphStyle) -> list[list[Paragraph]]: return [[Paragraph(_safe(cell), style) for cell in row] for row in rows]
def _bullet(styles: dict[str, ParagraphStyle], items: list[str]) -> list[Any]: return [Paragraph(f"• {item}", styles["body"]) for item in items]
def _page(canvas, doc) -> None:
    canvas.saveState(); canvas.setFont("MSYH",7); canvas.setFillColor(colors.HexColor("#666666")); canvas.drawRightString(287*mm,7*mm,f"第 {doc.page} 页"); canvas.drawString(10*mm,7*mm,"短视频账号全量数据分析报告（修正版）"); canvas.restoreState()
def _avg(rows: list[dict[str, Any]], key: str) -> float:
    vals = [float(row.get(key) or 0) for row in rows]; return mean(vals) if vals else 0.0
def _fmt(value: Any, digits: int = 2) -> str: return f"{float(value or 0):.{digits}f}"
def _short(text: str, limit: int) -> str: return text if len(text) <= limit else text[:limit-1] + "…"
def _safe(value: Any) -> str: return str(value).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
if __name__ == "__main__":
    raise SystemExit(main())


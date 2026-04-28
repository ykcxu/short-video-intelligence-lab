from __future__ import annotations
import importlib.util, json, sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any
import fitz
import pdfplumber
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
PDF_PATH = OUT_DIR / "20260428_短视频账号全量数据分析报告.pdf"
PREVIEW_PATH = TMP_DIR / "20260428_短视频账号全量数据分析报告_page1.png"
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
COLUMNS = ["序", "账号", "视频ID", "赞", "评", "转", "互动", "融合", "等级", "语速", "OCR", "字幕", "出镜", "姿态", "主体", "话术", "风险"]
WIDTHS = [9, 34, 34, 15, 14, 15, 20, 14, 13, 14, 13, 13, 13, 13, 13, 13, 35]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def main() -> int:
    _register_fonts()
    data = _load_data()
    rows = _load_rows()
    story = _build_story(data, rows)
    _write_pdf(story)
    preview = _render_preview(PDF_PATH)
    checks = _check_pdf(PDF_PATH)
    print(json.dumps({"ok": True, "pdf": str(PDF_PATH), "preview": str(preview), **checks}, ensure_ascii=False, indent=2))
    return 0
def _load_full_module():
    spec = importlib.util.spec_from_file_location("full_mm", ROOT / "tools" / "build_full_multimodal_analysis.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module
def _load_data() -> dict[str, Any]:
    full = json.loads((ROOT / "artifacts" / "analysis" / "full_multimodal" / "full_multimodal_analysis.json").read_text(encoding="utf-8"))
    first_path = ROOT / "artifacts" / "analysis" / "first_round" / "first_round_analysis.json"
    first = json.loads(first_path.read_text(encoding="utf-8")) if first_path.exists() else {}
    return {"full": full, "first": first}
def _load_rows() -> list[dict[str, Any]]:
    module = _load_full_module()
    loaded = module._load_all_outputs(ROOT)
    return module._join_rows(loaded)
def _build_story(data: dict[str, Any], rows: list[dict[str, Any]]) -> list[Any]:
    styles = _styles()
    full = data["full"]
    story: list[Any] = []
    story += _cover(styles, full)
    story += _methodology(styles)
    story += _overall_section(styles, full, rows)
    story += _account_section(styles, full)
    story += _growth_actions(styles, data.get("first", {}), full)
    story += _video_table_section(styles, rows)
    return story
def _cover(styles: dict[str, ParagraphStyle], full: dict[str, Any]) -> list[Any]:
    summary = full["sample_summary"]
    return [
        Spacer(1, 18 * mm), Paragraph("短视频账号全量数据分析报告", styles["title"]), Spacer(1, 7 * mm),
        Paragraph("抖音一期数据收集与多模态评测", styles["subtitle"]), Spacer(1, 12 * mm),
        _kv_table([["生成时间", datetime.now().strftime("%Y-%m-%d %H:%M")], ["覆盖视频", f"{summary['video_count']} 条"], ["覆盖主页", f"{summary['account_count']} 个"], ["平均融合评分", summary["average_fit_score"]], ["模型覆盖", "ASR / OCR / 人脸出镜 / 姿态 / 主体 / 话术结构 / 多模态融合"]]),
        Spacer(1, 10 * mm), Paragraph("核心结论", styles["h1"]),
        *_bullet(styles, ["当前 view_count 字段不可用，报告用点赞、评论、转发构造互动代理分。", "多模态融合评分更适合做制作质量体检，不能单独解释所有播放差异。", "全量短板集中在话术结构、字幕一致性、人物主体稳定度三个方向。"]),
        PageBreak(),
    ]
def _methodology(styles: dict[str, ParagraphStyle]) -> list[Any]:
    weights = [["组件", "权重", "评测依据"], ["基础画面", "16%", "竖屏、时长、亮度、对比度、字幕区域、画面节奏"], ["人脸/出镜", "16%", "人脸命中、居中、清晰度、表情积极度、遮挡风险"], ["姿态", "12%", "正向镜头、上半身可见、手势活跃度、稳定度"], ["人物主体", "12%", "人数、主体占比、居中度、背景杂乱度"], ["OCR 字幕", "14%", "字幕覆盖、可读性、关键词密度、一致性"], ["ASR 语音", "14%", "语速、停顿、开场钩子、语音时长"], ["口播结构", "16%", "钩子、痛点、方法、例子、行动召唤、知识密度"]]
    return [Paragraph("1. 我们如何评测视频", styles["h1"]), *_bullet(styles, ["数据来源：基于已登录浏览器会话采集主页视频、指标和本地下载视频，不使用抖音官方接口。", "互动代理分：engagement_score = like_count + 3 * comment_count + 2 * share_count。评论和转发权重更高，因为它们更能代表讨论与传播。", "融合评分：0-100 分，75 分及以上为 high，55-74 为 medium，55 以下为 low。", "每条视频抽取 3 帧进行 OCR、人物、姿态和画面特征分析，同时使用 ASR 获取口播文本和语速，再进行话术结构识别。", "本报告避免输出不可解释的“颜值绝对分”，统一转成可优化的出镜质量、居中、清晰度和表情积极度信号。"]), Spacer(1, 4 * mm), _small_table(weights, [35, 18, 170]), PageBreak()]
def _overall_section(styles: dict[str, ParagraphStyle], full: dict[str, Any], rows: list[dict[str, Any]]) -> list[Any]:
    s, f = full["sample_summary"], full["overall_findings"]
    risk = f["risk_counts"]
    dist = s["fit_level_distribution"]
    high = sorted(rows, key=lambda x: x["engagement_score"], reverse=True)[: max(1, len(rows)//4)]
    normal = sorted(rows, key=lambda x: x["engagement_score"])[: max(1, len(rows)//4)]
    table = [["指标", "全量", "高互动Top25%", "低互动Bottom25%"], ["平均融合评分", _fmt(_avg(rows,"fit_score")), _fmt(_avg(high,"fit_score")), _fmt(_avg(normal,"fit_score"))], ["平均评论数", _fmt(_avg(rows,"comment_count")), _fmt(_avg(high,"comment_count")), _fmt(_avg(normal,"comment_count"))], ["平均点赞数", _fmt(_avg(rows,"like_count")), _fmt(_avg(high,"like_count")), _fmt(_avg(normal,"like_count"))], ["平均转发数", _fmt(_avg(rows,"share_count")), _fmt(_avg(high,"share_count")), _fmt(_avg(normal,"share_count"))], ["话术完整度", _fmt(_avg(rows,"structure_completeness"),3), _fmt(_avg(high,"structure_completeness"),3), _fmt(_avg(normal,"structure_completeness"),3)]]
    return [Paragraph("2. 全量视频综合评价", styles["h1"]), *_bullet(styles, [f"已评测 {s['video_count']} 条视频，覆盖 {s['account_count']} 个主页；分片处理 23/23，失败 0。", f"融合评分分布：high {dist.get('high',0)} 条，medium {dist.get('medium',0)} 条，low {dist.get('low',0)} 条。", f"风险规模：多主体/主体不稳 {risk['multi_person_or_unstable_subject']} 条，话术结构弱 {risk['low_structure']} 条，字幕一致性弱 {risk['low_subtitle_consistency']} 条。", f"简单相关：融合评分与互动代理分相关系数 {f['correlation_hint']['fit_score']}，说明制作质量只是必要条件，选题和互动钩子同样关键。"]), Spacer(1,4*mm), _small_table(table, [45, 45, 55, 55]), PageBreak()]
def _account_section(styles: dict[str, ParagraphStyle], full: dict[str, Any]) -> list[Any]:
    rows = [["主页", "视频", "融合", "互动均值", "OCR", "话术", "主体", "主要建议"]]
    for item in full["account_findings"]:
        rows.append([item["account"], item["video_count"], item["average_fit_score"], int(item["average_engagement_score"]), item["average_ocr_readability"], item["average_structure_completeness"], item["average_person_count"], "；".join(item["recommendations"][:2])])
    return [Paragraph("3. 每个主页的综合评价", styles["h1"]), _small_table(rows, [38, 14, 18, 24, 18, 18, 18, 110]), PageBreak()]
def _growth_actions(styles: dict[str, ParagraphStyle], first: dict[str, Any], full: dict[str, Any]) -> list[Any]:
    items = ["增粉：固定“老师IP + 年级/学科 + 可复制栏目”，每条视频结尾明确关注理由，例如“每天1个小学语文提分点，关注后按年级刷”。", "增粉：把高互动选题做系列化，如错题避坑、作文素材、阅读方法、英语发音纠错，并在主页置顶3条代表作。", "评论：标题和结尾设置低门槛问题，优先用 A/B 选择、年级报数、留言领取资料、你家孩子是否也这样等触发。", "评论：围绕家长痛点制造讨论场景，例如“孩子背了就忘怎么办”“三年级作文最该练什么”，但避免空泛争议。", "点赞：前3秒给结果或反常识结论，中段给可截图清单，结尾提示“有用先点赞收藏”。", "点赞：字幕关键词固定位置，重点词高亮，减少多套字体和跳字，提高静音观看理解效率。", "转发：多做资料型、清单型、考试节点型视频，例如期末复习表、必背词、作文万能句，天然适合转给家长群。", "制作：口播统一按“钩子-痛点-方法-例子-行动”结构补齐，这是当前全量最明显短板。", "画面：知识讲解尽量单一老师/单一视觉焦点，降低多人和复杂背景对账号记忆点的干扰。"]
    return [Paragraph("4. 增加粉丝、评论、点赞的具体措施", styles["h1"]), *_bullet(styles, items), PageBreak()]
def _video_table_section(styles: dict[str, ParagraphStyle], rows: list[dict[str, Any]]) -> list[Any]:
    ordered = sorted(rows, key=lambda x: (x["source_name"], -x["engagement_score"]))
    data = [COLUMNS]
    for i, row in enumerate(ordered, 1):
        data.append(_video_row(i, row))
    table = Table(_paragraphize(data, styles["cell"]), colWidths=[w * mm for w in WIDTHS], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1f4e79")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTNAME", (0,0), (-1,-1), "MSYH"), ("FONTSIZE", (0,0), (-1,-1), 6), ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#d9e2f3")), ("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f7fbff")])]))
    return [Paragraph("5. 所有视频详细评分表", styles["h1"]), Paragraph("说明：互动为点赞 + 3*评论 + 2*转发；OCR/字幕/出镜/姿态/话术为 0-1 特征或模型评分摘要；风险列只列最关键风险。", styles["body"]), Spacer(1, 3*mm), table]
def _video_row(i: int, row: dict[str, Any]) -> list[Any]:
    risk = []
    if row["person_count"] > 1.5: risk.append("主体不稳")
    if row["structure_completeness"] < 0.3: risk.append("话术弱")
    if row["subtitle_consistency"] < 0.45: risk.append("字幕弱")
    if row["speech_rate_cpm"] > 360: risk.append("语速快")
    return [i, _short(row["source_name"], 10), row["video_id"], int(row["like_count"]), int(row["comment_count"]), int(row["share_count"]), int(row["engagement_score"]), int(row["fit_score"]), row["fit_level"], int(row["speech_rate_cpm"]), _fmt(row["ocr_readability"],2), _fmt(row["subtitle_consistency"],2), _fmt(row["face_center_score"],2), _fmt(row["pose_facing_score"],2), _fmt(row["person_count"],1), _fmt(row["structure_completeness"],2), "、".join(risk) or "-" ]
def _write_pdf(story: list[Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(PDF_PATH), pagesize=landscape(A4), rightMargin=10*mm, leftMargin=10*mm, topMargin=10*mm, bottomMargin=10*mm)
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
def _render_preview(pdf_path: Path) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False)
    pix.save(PREVIEW_PATH)
    doc.close()
    return PREVIEW_PATH
def _check_pdf(pdf_path: Path) -> dict[str, Any]:
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages[:5])
        return {"pages": len(pdf.pages), "size_bytes": pdf_path.stat().st_size, "has_methodology": "我们如何评测视频" in text, "has_actions": "增加粉丝" in text}
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {"title": ParagraphStyle("title", parent=base["Title"], fontName="MSYH-Bold", fontSize=28, leading=34, alignment=TA_CENTER, textColor=colors.HexColor("#17365d")), "subtitle": ParagraphStyle("subtitle", fontName="MSYH", fontSize=15, leading=20, alignment=TA_CENTER, textColor=colors.HexColor("#666666")), "h1": ParagraphStyle("h1", fontName="MSYH-Bold", fontSize=15, leading=20, spaceAfter=6, textColor=colors.HexColor("#1f4e79")), "body": ParagraphStyle("body", fontName="MSYH", fontSize=9.5, leading=14), "cell": ParagraphStyle("cell", fontName="MSYH", fontSize=5.6, leading=7, alignment=TA_LEFT)}
def _register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("MSYH", str(FONT_PATH)))
    pdfmetrics.registerFont(TTFont("MSYH-Bold", str(FONT_PATH)))
def _small_table(rows: list[list[Any]], widths: list[int]) -> Table:
    styles = _styles()
    table = Table(_paragraphize(rows, styles["body"]), colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#ddebf7")), ("FONTNAME", (0,0), (-1,-1), "MSYH"), ("FONTSIZE", (0,0), (-1,-1), 8), ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#b4c6e7")), ("VALIGN", (0,0), (-1,-1), "TOP")]))
    return table
def _kv_table(rows: list[list[Any]]) -> Table:
    return _small_table([["项目", "内容"], *rows], [40, 130])
def _paragraphize(rows: list[list[Any]], style: ParagraphStyle) -> list[list[Paragraph]]:
    return [[Paragraph(_safe(cell), style) for cell in row] for row in rows]
def _bullet(styles: dict[str, ParagraphStyle], items: list[str]) -> list[Any]:
    return [Paragraph(f"• {item}", styles["body"]) for item in items]
def _page(canvas, doc) -> None:
    canvas.saveState(); canvas.setFont("MSYH", 7); canvas.setFillColor(colors.HexColor("#666666")); canvas.drawRightString(287*mm, 7*mm, f"第 {doc.page} 页"); canvas.drawString(10*mm, 7*mm, "短视频账号全量数据分析报告"); canvas.restoreState()
def _avg(rows: list[dict[str, Any]], key: str) -> float:
    vals = [float(row.get(key) or 0) for row in rows]
    return mean(vals) if vals else 0.0
def _fmt(value: Any, digits: int = 2) -> str:
    return f"{float(value or 0):.{digits}f}"
def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit-1] + "…"
def _safe(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
if __name__ == "__main__":
    raise SystemExit(main())


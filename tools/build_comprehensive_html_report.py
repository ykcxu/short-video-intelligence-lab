from __future__ import annotations
import html, importlib.util, json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "html"
HTML_PATH = OUT_DIR / "20260428_短视频账号全量数据分析报告_可核验.html"
METRIC_LIMIT = 1000
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def main() -> int:
    """生成可点击核验的 HTML 分析报告。"""
    rows = _mark_metric_quality(_load_rows())
    analysis = _build_analysis(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(_render_html(analysis, rows), encoding="utf-8")
    print(json.dumps({"ok": True, "html": str(HTML_PATH), "rows": len(rows), "trusted": analysis["summary"]["trusted_metric_count"], "suspicious": analysis["summary"]["suspicious_metric_count"]}, ensure_ascii=False, indent=2))
    return 0
def _load_full_module():
    """复用全量多模态合并脚本，避免重复解析分片产物。"""
    spec = importlib.util.spec_from_file_location("full_mm", ROOT / "tools" / "build_full_multimodal_analysis.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module
def _load_rows() -> list[dict[str, Any]]:
    """读取 362 条全量视频多模态行。"""
    module = _load_full_module()
    return module._join_rows(module._load_all_outputs(ROOT))
def _mark_metric_quality(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """隔离超过阈值的可疑互动指标，保留视频用于核验。"""
    marked = []
    for row in rows:
        copied = dict(row)
        over = [key for key in ("like_count", "comment_count", "share_count", "view_count") if float(copied.get(key) or 0) > METRIC_LIMIT]
        if float(copied.get("engagement_score") or 0) > METRIC_LIMIT:
            over.append("engagement_score")
        copied["metric_suspicious"] = bool(over)
        copied["suspicious_reason"] = "、".join(over)
        copied["trusted_engagement_score"] = 0 if over else copied["engagement_score"]
        marked.append(copied)
    return marked
def _build_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """生成页面需要的汇总、账号表和可信 Top 列表。"""
    trusted = [row for row in rows if not row["metric_suspicious"]]
    return {"summary": _summary(rows, trusted), "accounts": _accounts(rows, trusted), "top_trusted": sorted(trusted, key=lambda x: x["trusted_engagement_score"], reverse=True)[:30], "risks": _risk_counts(rows)}
def _summary(rows: list[dict[str, Any]], trusted: list[dict[str, Any]]) -> dict[str, Any]:
    return {"video_count": len(rows), "trusted_metric_count": len(trusted), "suspicious_metric_count": len(rows)-len(trusted), "account_count": len({r["source_name"] for r in rows}), "average_fit_score": round(_avg(rows,"fit_score"), 2), "average_trusted_engagement": round(_avg(trusted,"trusted_engagement_score"), 2)}
def _accounts(rows: list[dict[str, Any]], trusted: list[dict[str, Any]]) -> list[dict[str, Any]]:
    all_groups, trusted_groups = defaultdict(list), defaultdict(list)
    for row in rows: all_groups[row["source_name"]].append(row)
    for row in trusted: trusted_groups[row["source_name"]].append(row)
    return [_account(name, all_groups[name], trusted_groups.get(name, [])) for name in sorted(all_groups)]
def _account(name: str, rows: list[dict[str, Any]], trusted: list[dict[str, Any]]) -> dict[str, Any]:
    return {"account": name, "video_count": len(rows), "trusted_count": len(trusted), "suspicious_count": len(rows)-len(trusted), "average_fit_score": round(_avg(rows,"fit_score"), 2), "trusted_engagement_avg": round(_avg(trusted,"trusted_engagement_score"), 2), "average_structure": round(_avg(rows,"structure_completeness"), 3), "average_person_count": round(_avg(rows,"person_count"), 2), "recommendations": _recommendations(rows)}
def _recommendations(rows: list[dict[str, Any]]) -> list[str]:
    tips = []
    if _avg(rows, "structure_completeness") < 0.3: tips.append("补齐钩子-痛点-方法-例子-行动的话术结构")
    if _avg(rows, "subtitle_consistency") < 0.5: tips.append("统一字幕样式和关键词位置")
    if _avg(rows, "person_count") > 1.5: tips.append("减少多人/复杂背景，突出单一视觉焦点")
    if _avg(rows, "face_center_score") < 0.75: tips.append("提高人物居中和脸部清晰度")
    return tips or ["优先优化选题、标题和评论钩子"]
def _risk_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"主体不稳": sum(1 for r in rows if r["person_count"] > 1.5), "话术弱": sum(1 for r in rows if r["structure_completeness"] < 0.3), "字幕弱": sum(1 for r in rows if r["subtitle_consistency"] < 0.45), "语速快": sum(1 for r in rows if r["speech_rate_cpm"] > 360)}
def _render_html(analysis: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """渲染单文件 HTML，内置筛选与搜索，方便逐条核验。"""
    s = analysis["summary"]
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>短视频账号全量数据分析报告（可核验）</title>{_style()}</head><body><header><h1>短视频账号全量数据分析报告（可核验 HTML）</h1><p>生成时间：{_e(datetime.now().strftime('%Y-%m-%d %H:%M'))}｜点击任意视频ID可打开抖音视频页核验。</p></header><section class='cards'>{_card('全量视频', s['video_count'])}{_card('可信互动样本', s['trusted_metric_count'])}{_card('可疑指标样本', s['suspicious_metric_count'])}{_card('平均融合评分', s['average_fit_score'])}{_card('可信互动均值', s['average_trusted_engagement'])}</section>{_methodology()}{_risk_section(analysis)}{_account_section(analysis)}{_top_section(analysis)}{_video_section(rows)}{_script()}</body></html>"""
def _methodology() -> str:
    return """<section><h2>1. 评测依据与数据质量口径</h2><ul><li>数据来源：已登录浏览器会话采集主页、详情、评论和本地视频，不使用抖音官方接口。</li><li>互动代理分：like_count + 3*comment_count + 2*share_count；但只在可信指标样本内参与结论。</li><li>可疑规则：任一点赞/评论/转发/播放或互动代理分超过 1000，即标记为可疑，等待人工点击核验或重采。</li><li>融合评分由基础画面、人脸/出镜、姿态、人物主体、OCR字幕、ASR语音、口播结构组成。</li></ul></section>"""
def _risk_section(analysis: dict[str, Any]) -> str:
    risks = "".join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>" for k,v in analysis["risks"].items())
    return f"<section><h2>2. 全量制作侧风险</h2><table><thead><tr><th>风险</th><th>数量</th></tr></thead><tbody>{risks}</tbody></table></section>"
def _account_section(analysis: dict[str, Any]) -> str:
    rows = "".join(_account_row(item) for item in analysis["accounts"])
    return f"<section><h2>3. 每个主页综合评价</h2><table><thead><tr><th>主页</th><th>视频</th><th>可信</th><th>可疑</th><th>融合均值</th><th>可信互动均值</th><th>话术</th><th>主体数</th><th>建议</th></tr></thead><tbody>{rows}</tbody></table></section>"
def _account_row(item: dict[str, Any]) -> str:
    tips = "；".join(item["recommendations"])
    return f"<tr><td>{_e(item['account'])}</td><td>{item['video_count']}</td><td>{item['trusted_count']}</td><td>{item['suspicious_count']}</td><td>{item['average_fit_score']}</td><td>{item['trusted_engagement_avg']}</td><td>{item['average_structure']}</td><td>{item['average_person_count']}</td><td>{_e(tips)}</td></tr>"
def _top_section(analysis: dict[str, Any]) -> str:
    rows = "".join(_video_row(row, i) for i, row in enumerate(analysis["top_trusted"], 1))
    return f"<section><h2>4. 可信互动 Top 视频</h2><table>{_video_head()}<tbody>{rows}</tbody></table></section>"
def _video_section(rows: list[dict[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda x: (x["source_name"], x["metric_suspicious"], -x["trusted_engagement_score"]))
    body = "".join(_video_row(row, i) for i, row in enumerate(ordered, 1))
    return f"<section><h2>5. 所有视频详细评分表</h2><div class='toolbar'><input id='q' placeholder='搜索账号 / 视频ID / 风险'><select id='quality'><option value='all'>全部指标</option><option value='可信'>只看可信</option><option value='可疑'>只看可疑</option></select><button onclick='resetFilters()'>重置</button></div><table id='videoTable'>{_video_head()}<tbody>{body}</tbody></table></section>"
def _video_head() -> str:
    return "<thead><tr><th>序</th><th>账号</th><th>视频ID</th><th>赞</th><th>评</th><th>转</th><th>互动</th><th>指标</th><th>融合</th><th>等级</th><th>语速</th><th>OCR</th><th>字幕</th><th>出镜</th><th>姿态</th><th>主体</th><th>话术</th><th>风险</th></tr></thead>"
def _video_row(row: dict[str, Any], i: int) -> str:
    quality = "可疑" if row["metric_suspicious"] else "可信"
    cls = "suspicious" if row["metric_suspicious"] else "trusted"
    url = _e(row["video_url"])
    cells = [i, _e(row["source_name"]), f"<a href='{url}' target='_blank' rel='noopener noreferrer'>{_e(row['video_id'])}</a>", _n(row,"like_count"), _n(row,"comment_count"), _n(row,"share_count"), _n(row,"engagement_score"), f"<span class='badge {cls}'>{quality}</span>", _n(row,"fit_score"), _e(row["fit_level"]), int(row["speech_rate_cpm"]), _fmt(row["ocr_readability"],2), _fmt(row["subtitle_consistency"],2), _fmt(row["face_center_score"],2), _fmt(row["pose_facing_score"],2), _fmt(row["person_count"],1), _fmt(row["structure_completeness"],2), _e(_risk_text(row))]
    return "<tr data-quality='%s'>%s</tr>" % (quality, "".join(f"<td>{cell}</td>" for cell in cells))
def _risk_text(row: dict[str, Any]) -> str:
    risks = []
    if row.get("metric_suspicious"): risks.append("指标可疑:" + row.get("suspicious_reason", ""))
    if row["person_count"] > 1.5: risks.append("主体不稳")
    if row["structure_completeness"] < 0.3: risks.append("话术弱")
    if row["subtitle_consistency"] < 0.45: risks.append("字幕弱")
    if row["speech_rate_cpm"] > 360: risks.append("语速快")
    return "、".join(risks) or "-"
def _style() -> str:
    return """<style>body{font-family:'Microsoft YaHei',Arial,sans-serif;margin:0;background:#f5f7fb;color:#1f2937}header{background:#16365c;color:white;padding:28px 36px}h1{margin:0 0 8px}section{margin:18px 28px;padding:18px;background:white;border-radius:12px;box-shadow:0 1px 4px #d6dce8}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;background:transparent;box-shadow:none;padding:0}.card{background:white;border-radius:12px;padding:16px;box-shadow:0 1px 4px #d6dce8}.card b{font-size:26px;color:#16365c}table{width:100%;border-collapse:collapse;font-size:12px}th{position:sticky;top:0;background:#1f4e79;color:white;z-index:1}th,td{border:1px solid #d9e2f3;padding:6px;vertical-align:top}tbody tr:nth-child(even){background:#f7fbff}a{color:#0b63ce;font-weight:600}.badge{border-radius:999px;padding:2px 8px;color:white}.trusted{background:#16803c}.suspicious{background:#b42318}.toolbar{display:flex;gap:10px;margin:8px 0 12px}.toolbar input{width:360px}.toolbar input,.toolbar select,.toolbar button{padding:8px;border:1px solid #b4c6e7;border-radius:8px}</style>"""
def _script() -> str:
    return """<script>function applyFilters(){const q=document.getElementById('q').value.toLowerCase();const quality=document.getElementById('quality').value;document.querySelectorAll('#videoTable tbody tr').forEach(tr=>{const okQ=!q||tr.innerText.toLowerCase().includes(q);const okQuality=quality==='all'||tr.dataset.quality===quality;tr.style.display=(okQ&&okQuality)?'':'none';});}function resetFilters(){document.getElementById('q').value='';document.getElementById('quality').value='all';applyFilters();}document.getElementById('q').addEventListener('input',applyFilters);document.getElementById('quality').addEventListener('change',applyFilters);</script>"""
def _card(label: str, value: Any) -> str: return f"<div class='card'><div>{_e(label)}</div><b>{_e(value)}</b></div>"
def _avg(rows: list[dict[str, Any]], key: str) -> float:
    vals = [float(row.get(key) or 0) for row in rows]; return mean(vals) if vals else 0.0
def _fmt(value: Any, digits: int = 2) -> str: return f"{float(value or 0):.{digits}f}"
def _n(row: dict[str, Any], key: str) -> int: return int(float(row.get(key) or 0))
def _e(value: Any) -> str: return html.escape(str(value), quote=True)
if __name__ == "__main__":
    raise SystemExit(main())

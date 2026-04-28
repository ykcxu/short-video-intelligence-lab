from __future__ import annotations
import html, importlib.util, json, shutil, sqlite3, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from short_video_intel.analysis.video_effect_evaluator import score_video, train_effect_model
ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "output" / "html" / "20260428_理想视频模型与视频效果评价器.html"
MODEL_PATH = ROOT / "artifacts" / "analysis" / "video_effect_model.json"
SCORES_PATH = ROOT / "artifacts" / "analysis" / "video_effect_scores.json"
BUNDLE_DIR = ROOT / "deliverables" / "20260428_video_effect_evaluator"
METRIC_LIMIT = 1000
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def main() -> int:
    rows = _mark_quality(_load_rows())
    comments = _load_comments(rows)
    model = train_effect_model(rows, comments)
    scores = [score_video(row, comments.get(row["video_id"], []), model) for row in rows]
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    SCORES_PATH.write_text(json.dumps({"model_path": str(MODEL_PATH), "scores": scores}, ensure_ascii=False, indent=2), encoding="utf-8")
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(_render(rows, comments, model, scores), encoding="utf-8")
    bundle = _write_bundle()
    print(json.dumps({"ok": True, "html": str(HTML_PATH), "model": str(MODEL_PATH), "scores": str(SCORES_PATH), "bundle": str(bundle), "videos": len(rows)}, ensure_ascii=False, indent=2))
    return 0
def _load_module():
    spec = importlib.util.spec_from_file_location("full_mm", ROOT / "tools" / "build_full_multimodal_analysis.py")
    module = importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module)
    return module
def _load_rows() -> list[dict[str, Any]]:
    module = _load_module(); return module._join_rows(module._load_all_outputs(ROOT))
def _mark_quality(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    marked = []
    for row in rows:
        item = dict(row); suspicious, reason = _is_suspicious_metrics(item)
        item["metric_suspicious"] = suspicious; item["suspicious_reason"] = reason
        item["trusted_engagement_score"] = 0 if suspicious else item.get("engagement_score", 0)
        marked.append(item)
    return marked
def _is_suspicious_metrics(row: dict[str, Any]) -> tuple[bool, str]:
    like_count = float(row.get("like_count") or 0); comment_count = float(row.get("comment_count") or 0); share_count = float(row.get("share_count") or 0)
    if share_count > METRIC_LIMIT and like_count < METRIC_LIMIT and comment_count < 50:
        return True, "低赞低评高转发"
    return False, ""
def _load_comments(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    source_by_id = {row["video_id"]: row["source_name"] for row in rows}
    con = sqlite3.connect(ROOT / "data" / "app.db"); con.row_factory = sqlite3.Row
    sql = """select v.video_id,v.video_url,c.nickname,c.content,c.like_count,c.reply_count from comments c join videos v on v.id=c.video_id_fk"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in con.execute(sql):
        item = dict(rec); text = (item.get("content") or "").strip()
        if item["video_id"] not in source_by_id or not text or _is_noise(text):
            continue
        item["source_name"] = source_by_id[item["video_id"]]
        item["is_author_reply"] = _is_author(item.get("nickname"), item["source_name"])
        item["topics"] = _topics(text); item["sentiment"] = _sentiment(text)
        grouped[item["video_id"]].append(item)
    con.close(); return grouped
def _is_noise(text: str) -> bool:
    return any(key in text for key in ["直播间带货榜", "上榜直播间", "国家补贴榜", "排序规则", "平台将实时监控"])
def _is_author(nickname: Any, source: str) -> bool:
    nick = str(nickname or "").strip(); src = str(source or "").strip()
    return bool(nick and src and (nick == src or nick in src or src in nick))
def _topics(text: str) -> list[str]:
    rules = [("资料领取", ["资料", "打印", "领取", "在哪", "链接", "单词有吗"]), ("考试报考", ["ket", "pet", "报考", "真题", "考试", "听力", "阅读"]), ("家长咨询", ["我家", "孩子", "适合", "怎么办", "几岁", "几年级"]), ("难度质疑", ["太简单", "太难", "难度", "差距", "够不上"]), ("方法追问", ["怎么", "如何", "方法", "技巧", "训练"]), ("正向反馈", ["有用", "谢谢", "学到了", "收藏", "厉害"]), ("情绪/共鸣", ["哈哈", "笑", "同款", "一样", "真的"])]
    found = [name for name, keys in rules if any(k.lower() in text.lower() for k in keys)]
    return found or ["其他"]
def _sentiment(text: str) -> str:
    if any(k in text for k in ["太简单", "差距", "不行", "错", "难", "够不上"]): return "质疑/负向"
    if any(k in text for k in ["谢谢", "有用", "学到了", "厉害", "收藏"]): return "正向"
    if "?" in text or "？" in text or any(k in text for k in ["吗", "怎么", "如何", "在哪"]): return "咨询"
    return "中性"
def _render(rows: list[dict[str, Any]], comments: dict[str, list[dict[str, Any]]], model: dict[str, Any], scores: list[dict[str, Any]]) -> str:
    score_by_id = {s["video_id"]: s for s in scores}; trusted = [r for r in rows if not r["metric_suspicious"]]
    return f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>理想视频模型与视频效果评价器</title>{_style()}</head><body><header><h1>理想视频模型与视频效果评价器</h1><p>生成时间：{_e(datetime.now().strftime('%Y-%m-%d %H:%M'))}｜以后报告默认 HTML 输出</p></header><section class='cards'>{_card('全量视频',len(rows))}{_card('可信互动样本',len(trusted))}{_card('可疑互动样本',len(rows)-len(trusted))}{_card('有评论视频',sum(1 for v in comments.values() if v))}{_card('模型版本',model['version'])}</section>{_ideal_section(model)}{_model_section()}{_top_section(rows, score_by_id)}{_account_section(rows, scores)}{_all_scores_section(rows, score_by_id)}{_usage_section()}{_script()}</body></html>"
def _ideal_section(model: dict[str, Any]) -> str:
    rows = ''.join(f"<tr><td>{_e(k)}</td><td>{v['low']}</td><td>{v['mid']}</td><td>{v['high']}</td></tr>" for k,v in model['ideal_profile'].items())
    return f"<section><h2>1. 理想视频模型</h2><p>理想画像来自可信互动 Top25% 与评论 Top25% 视频的交集/并集，不把低赞低评高转发样本用于训练。</p><table><thead><tr><th>特征</th><th>理想低位</th><th>中位</th><th>理想高位</th></tr></thead><tbody>{rows}</tbody></table></section>"
def _model_section() -> str:
    items = ["前3秒给明确结论或强问题，避免慢热铺垫。", "口播结构采用：钩子 - 痛点 - 方法 - 例子 - 评论问题。", "字幕关键词稳定、高亮、少跳字，保证静音观看也能理解。", "画面突出单一老师或单一视觉焦点，减少多人和复杂背景。", "评论区设置低门槛问题：年级报数、是否需要资料、A/B 选择。", "如果评论出现 PET/KET/资料/报考/难度质疑，要立刻转成系列内容和作者回复。"]
    return "<section><h2>2. 理想视频的制作模型</h2><ul>" + ''.join(f"<li>{_e(x)}</li>" for x in items) + "</ul></section>"
def _top_section(rows: list[dict[str, Any]], score_by_id: dict[str, dict[str, Any]]) -> str:
    top = sorted(score_by_id.values(), key=lambda x: x['effect_score'], reverse=True)[:30]
    body = ''.join(_score_row(i, s) for i,s in enumerate(top,1))
    return f"<section><h2>3. 评价器 Top 视频</h2><table>{_score_head()}<tbody>{body}</tbody></table></section>"
def _account_section(rows: list[dict[str, Any]], scores: list[dict[str, Any]]) -> str:
    groups = defaultdict(list)
    for s in scores: groups[s['source_name']].append(s)
    body = ''
    for name, items in sorted(groups.items()):
        body += f"<tr><td>{_e(name)}</td><td>{len(items)}</td><td>{_avg([x['effect_score'] for x in items]):.2f}</td><td>{_avg([x['production_quality_score'] for x in items]):.2f}</td><td>{_avg([x['comment_potential_score'] for x in items]):.2f}</td><td>{_e(_account_tip(items))}</td></tr>"
    return f"<section><h2>4. 账号级评分和改进方向</h2><table><thead><tr><th>账号</th><th>视频数</th><th>效果均分</th><th>制作均分</th><th>评论潜力均分</th><th>优先动作</th></tr></thead><tbody>{body}</tbody></table></section>"
def _account_tip(items: list[dict[str, Any]]) -> str:
    risk_counter = Counter(r for item in items for r in item.get('risks', []))
    if not risk_counter: return "复用高分视频结构，继续测试标题和评论钩子"
    return risk_counter.most_common(1)[0][0]
def _all_scores_section(rows: list[dict[str, Any]], score_by_id: dict[str, dict[str, Any]]) -> str:
    body = ''.join(_score_row(i, score_by_id[r['video_id']]) for i,r in enumerate(sorted(rows, key=lambda x: score_by_id[x['video_id']]['effect_score'], reverse=True),1))
    return f"<section><h2>5. 全量视频效果评分表</h2><div class='toolbar'><input id='q' placeholder='搜索账号 / 视频ID / 风险'><select id='level'><option value='all'>全部等级</option><option>excellent</option><option>good</option><option>medium</option><option>weak</option></select><button onclick='resetFilters()'>重置</button></div><table id='scoreTable'>{_score_head()}<tbody>{body}</tbody></table></section>"
def _score_head() -> str:
    return "<thead><tr><th>序</th><th>账号</th><th>视频ID</th><th>效果分</th><th>等级</th><th>制作</th><th>互动</th><th>评论转化</th><th>评论主题</th><th>风险</th><th>建议</th></tr></thead>"
def _score_row(i: int, s: dict[str, Any]) -> str:
    topics = '、'.join(f"{k}({v})" for k,v in s.get('comment_topics', {}).items()) or '-'
    risks = '；'.join(s.get('risks') or ['-']); actions = '；'.join(s.get('actions') or ['-'])
    return f"<tr data-level='{_e(s['effect_level'])}'><td>{i}</td><td>{_e(s['source_name'])}</td><td><a href='{_e(s['video_url'])}' target='_blank'>{_e(s['video_id'])}</a></td><td>{s['effect_score']}</td><td>{_e(s['effect_level'])}</td><td>{s['production_quality_score']}</td><td>{s['content_structure_score']}</td><td>{s['comment_potential_score']}</td><td>{_e(topics)}</td><td>{_e(risks)}</td><td>{_e(actions)}</td></tr>"
def _usage_section() -> str:
    return f"<section><h2>6. 视频评价器使用方式</h2><pre>py -3.11 tools/evaluate_video_effect.py --model artifacts/analysis/video_effect_model.json --input your_video_features.json</pre><p>打包文件位于 deliverables/20260428_video_effect_evaluator_bundle.zip，包含模型 JSON、全量评分 JSON、HTML 报告和评价 CLI。</p></section>"
def _write_bundle() -> Path:
    if BUNDLE_DIR.exists(): shutil.rmtree(BUNDLE_DIR)
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    for p in [MODEL_PATH, SCORES_PATH, HTML_PATH, ROOT/'tools/evaluate_video_effect.py']:
        if p.exists(): shutil.copy2(p, BUNDLE_DIR / p.name)
    (BUNDLE_DIR / '使用说明.txt').write_text('运行：py -3.11 tools/evaluate_video_effect.py --model artifacts/analysis/video_effect_model.json --input your_video_features.json\n输入 JSON 需包含 row 和 comments 两个字段。\n', encoding='utf-8')
    return Path(shutil.make_archive(str((ROOT/'deliverables'/'20260428_video_effect_evaluator_bundle').with_suffix('')), 'zip', BUNDLE_DIR))
def _style() -> str:
    return """<style>body{font-family:'Microsoft YaHei',Arial,sans-serif;margin:0;background:#f5f7fb;color:#1f2937}header{background:#16365c;color:white;padding:28px 36px}section{margin:18px 28px;padding:18px;background:white;border-radius:12px;box-shadow:0 1px 4px #d6dce8}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;background:transparent;box-shadow:none;padding:0}.card{background:white;border-radius:12px;padding:16px;box-shadow:0 1px 4px #d6dce8}.card b{font-size:24px;color:#16365c}table{width:100%;border-collapse:collapse;font-size:12px}th{position:sticky;top:0;background:#1f4e79;color:white;z-index:1}th,td{border:1px solid #d9e2f3;padding:6px;vertical-align:top}tbody tr:nth-child(even){background:#f7fbff}a{color:#0b63ce;font-weight:600}.toolbar{display:flex;gap:10px;margin:8px 0 12px}.toolbar input{width:360px}.toolbar input,.toolbar select,.toolbar button{padding:8px;border:1px solid #b4c6e7;border-radius:8px}pre{background:#0f172a;color:#e5e7eb;padding:12px;border-radius:8px}</style>"""
def _script() -> str:
    return """<script>function applyFilters(){const q=document.getElementById('q').value.toLowerCase();const level=document.getElementById('level').value;document.querySelectorAll('#scoreTable tbody tr').forEach(tr=>{const okQ=!q||tr.innerText.toLowerCase().includes(q);const okL=level==='all'||tr.dataset.level===level;tr.style.display=(okQ&&okL)?'':'none';});}function resetFilters(){document.getElementById('q').value='';document.getElementById('level').value='all';applyFilters();}document.getElementById('q').addEventListener('input',applyFilters);document.getElementById('level').addEventListener('change',applyFilters);</script>"""
def _card(label: str, value: Any) -> str: return f"<div class='card'><div>{_e(label)}</div><b>{_e(value)}</b></div>"
def _avg(values: list[float]) -> float: return sum(values)/len(values) if values else 0.0
def _e(value: Any) -> str: return html.escape(str(value), quote=True)
if __name__ == "__main__": raise SystemExit(main())


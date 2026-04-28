from __future__ import annotations
import html, importlib.util, json, sqlite3, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "html" / "20260428_短视频评论系统分析报告.html"
METRIC_LIMIT = 1000
TOP_COMMENTS = 8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def main() -> int:
    rows = _mark_quality(_load_rows())
    comments = _load_comments(rows)
    video_stats = _video_comment_stats(rows, comments)
    analysis = _build_analysis(rows, comments, video_stats)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(_render(analysis, rows, comments, video_stats), encoding="utf-8")
    print(json.dumps({"ok": True, "html": str(OUT), "videos": len(rows), "comments": len(comments), "videos_with_comments": analysis["summary"]["videos_with_comments"]}, ensure_ascii=False, indent=2))
    return 0
def _load_module():
    spec = importlib.util.spec_from_file_location("full_mm", ROOT / "tools" / "build_full_multimodal_analysis.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module
def _load_rows() -> list[dict[str, Any]]:
    module = _load_module()
    return module._join_rows(module._load_all_outputs(ROOT))
def _is_suspicious_metrics(row: dict[str, Any]) -> tuple[bool, str]:
    # 只隔离“点赞/评论很低但转发极高”的疑似解析错位；上万播放/点赞/评论本身不再判错。
    like_count = float(row.get("like_count") or 0)
    comment_count = float(row.get("comment_count") or 0)
    share_count = float(row.get("share_count") or 0)
    if share_count > METRIC_LIMIT and like_count < METRIC_LIMIT and comment_count < 50:
        return True, "低赞低评高转发"
    return False, ""
def _mark_quality(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    marked = []
    for row in rows:
        item = dict(row)
        suspicious, reason = _is_suspicious_metrics(item)
        item["metric_suspicious"] = suspicious
        item["trusted_engagement_score"] = 0 if suspicious else item.get("engagement_score", 0)
        item["suspicious_reason"] = reason
        marked.append(item)
    return marked
def _load_comments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_by_id = {row["video_id"]: row["source_name"] for row in rows}
    con = sqlite3.connect(ROOT / "data" / "app.db"); con.row_factory = sqlite3.Row
    sql = """select v.video_id,v.video_url,c.nickname,c.content,c.like_count,c.reply_count,c.comment_platform_id,c.raw_json_path from comments c join videos v on v.id=c.video_id_fk"""
    comments = []
    for rec in con.execute(sql):
        d = dict(rec); text = (d.get("content") or "").strip()
        if d["video_id"] not in source_by_id:
            continue
        if not text or _is_noise(text):
            continue
        d["source_name"] = source_by_id.get(d["video_id"], "")
        d["is_author_reply"] = _is_author(d.get("nickname"), d["source_name"])
        d["topics"] = _topics(text)
        d["sentiment"] = _sentiment(text)
        comments.append(d)
    con.close(); return comments
def _is_noise(text: str) -> bool:
    noise = ["直播间带货榜", "上榜直播间", "国家补贴榜", "排序规则", "平台将实时监控"]
    return any(key in text for key in noise)
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
def _video_comment_stats(rows: list[dict[str, Any]], comments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped = defaultdict(list)
    for c in comments: grouped[c["video_id"]].append(c)
    stats = {}
    for row in rows:
        cs = grouped.get(row["video_id"], [])
        topic_counter = Counter(t for c in cs for t in c["topics"])
        stats[row["video_id"]] = {"collected": len(cs), "author_replies": sum(1 for c in cs if c["is_author_reply"]), "question_count": sum(1 for c in cs if c["sentiment"] == "咨询"), "top_topics": topic_counter.most_common(3), "comments": cs[:TOP_COMMENTS]}
    return stats
def _build_analysis(rows: list[dict[str, Any]], comments: list[dict[str, Any]], stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    trusted = [r for r in rows if not r["metric_suspicious"]]
    return {"summary": _summary(rows, trusted, comments, stats), "topics": Counter(t for c in comments for t in c["topics"]).most_common(), "sentiments": Counter(c["sentiment"] for c in comments).most_common(), "accounts": _account_analysis(rows, comments, stats), "opportunities": _opportunities(comments), "top_discussion": _top_discussion(rows, stats)}
def _summary(rows: list[dict[str, Any]], trusted: list[dict[str, Any]], comments: list[dict[str, Any]], stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"video_count": len(rows), "trusted_metric_count": len(trusted), "suspicious_metric_count": len(rows)-len(trusted), "comment_count": len(comments), "videos_with_comments": sum(1 for s in stats.values() if s["collected"]), "author_reply_count": sum(1 for c in comments if c["is_author_reply"]), "question_comment_count": sum(1 for c in comments if c["sentiment"] == "咨询")}
def _account_analysis(rows: list[dict[str, Any]], comments: list[dict[str, Any]], stats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_account, comments_by_account = defaultdict(list), defaultdict(list)
    for r in rows: by_account[r["source_name"]].append(r)
    for c in comments: comments_by_account[c["source_name"]].append(c)
    output = []
    for name in sorted(by_account):
        cs = comments_by_account.get(name, []); vids = by_account[name]
        topics = Counter(t for c in cs for t in c["topics"]).most_common(3)
        output.append({"account": name, "videos": len(vids), "comments": len(cs), "videos_with_comments": len({c["video_id"] for c in cs}), "author_replies": sum(1 for c in cs if c["is_author_reply"]), "question_rate": _rate(sum(1 for c in cs if c["sentiment"] == "咨询"), len(cs)), "top_topics": topics, "advice": _account_advice(vids, cs, topics)})
    return output
def _account_advice(videos: list[dict[str, Any]], comments: list[dict[str, Any]], topics: list[tuple[str,int]]) -> list[str]:
    labels = {name for name, _ in topics}; tips = []
    if "资料领取" in labels: tips.append("把资料领取入口固定到评论置顶/私信关键词，减少用户流失")
    if "考试报考" in labels or "家长咨询" in labels: tips.append("围绕报考门槛、备考路径、年级适配做系列内容")
    if "难度质疑" in labels: tips.append("用真题对比、难度标尺和适用人群回应质疑")
    if sum(1 for c in comments if c["is_author_reply"]) == 0 and comments: tips.append("提高作者回复率，把咨询评论转成二次选题")
    if _avg(videos, "structure_completeness") < 0.3: tips.append("口播结构补齐钩子-方法-例子-行动")
    return tips or ["评论样本较少，优先扩大评论采集覆盖再细分策略"]
def _opportunities(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = defaultdict(list)
    for c in comments:
        for t in c["topics"]: buckets[t].append(c)
    return [{"topic": k, "count": len(v), "examples": [x["content"] for x in v[:5]]} for k, v in sorted(buckets.items(), key=lambda x: len(x[1]), reverse=True)]
def _top_discussion(rows: list[dict[str, Any]], stats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {r["video_id"]: r for r in rows}
    vids = sorted(stats.items(), key=lambda kv: (kv[1]["collected"], kv[1]["question_count"]), reverse=True)[:30]
    return [{**by_id[vid], **s} for vid, s in vids if s["collected"]]
def _render(a: dict[str, Any], rows: list[dict[str, Any]], comments: list[dict[str, Any]], stats: dict[str, dict[str, Any]]) -> str:
    s = a["summary"]
    return f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>评论系统分析报告</title>{_style()}</head><body><header><h1>短视频评论系统分析报告</h1><p>生成时间：{_e(datetime.now().strftime('%Y-%m-%d %H:%M'))}｜点击视频ID打开抖音核验</p></header><section class='cards'>{_card('全量视频',s['video_count'])}{_card('已采评论',s['comment_count'])}{_card('有评论视频',s['videos_with_comments'])}{_card('作者回复',s['author_reply_count'])}{_card('咨询评论',s['question_comment_count'])}</section>{_method()}{_topic_section(a)}{_account_section(a)}{_opportunity_section(a)}{_video_section(a)}{_comment_table(comments)}{_script()}</body></html>"
def _method() -> str:
    return "<section><h2>1. 分析口径</h2><ul><li>互动指标只隔离低赞低评高转发的疑似错位样本；评论分析独立使用已采集评论文本。</li><li>评论被划分为资料领取、考试报考、家长咨询、难度质疑、方法追问、正向反馈、情绪共鸣等主题。</li><li>教育账号分析重点不是评论数量，而是评论暴露出的需求、质疑、转化入口和二次选题。</li></ul></section>"
def _topic_section(a: dict[str, Any]) -> str:
    rows = ''.join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>" for k,v in a['topics'])
    sent = ''.join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>" for k,v in a['sentiments'])
    return f"<section class='grid2'><div><h2>2. 评论主题分布</h2><table><tbody>{rows}</tbody></table></div><div><h2>评论意图/情绪</h2><table><tbody>{sent}</tbody></table></div></section>"
def _account_section(a: dict[str, Any]) -> str:
    body = ''.join(_account_row(x) for x in a['accounts'])
    return f"<section><h2>3. 每个主页的评论画像和动作建议</h2><table><thead><tr><th>主页</th><th>视频</th><th>评论</th><th>有评论视频</th><th>作者回复</th><th>咨询占比</th><th>主主题</th><th>动作建议</th></tr></thead><tbody>{body}</tbody></table></section>"
def _account_row(x: dict[str, Any]) -> str:
    topics = '、'.join(f"{k}({v})" for k,v in x['top_topics']) or '-'
    return f"<tr><td>{_e(x['account'])}</td><td>{x['videos']}</td><td>{x['comments']}</td><td>{x['videos_with_comments']}</td><td>{x['author_replies']}</td><td>{x['question_rate']:.1%}</td><td>{_e(topics)}</td><td>{_e('；'.join(x['advice']))}</td></tr>"
def _opportunity_section(a: dict[str, Any]) -> str:
    blocks = ''.join(f"<div class='opp'><h3>{_e(x['topic'])}：{x['count']}条</h3><ul>{''.join('<li>'+_e(t)+'</li>' for t in x['examples'])}</ul></div>" for x in a['opportunities'][:8])
    return f"<section><h2>4. 可转化需求和内容机会</h2><div class='opps'>{blocks}</div></section>"
def _video_section(a: dict[str, Any]) -> str:
    body = ''.join(_video_row(x, i) for i,x in enumerate(a['top_discussion'],1))
    return f"<section><h2>5. 评论讨论度 Top 视频</h2><table><thead><tr><th>序</th><th>账号</th><th>视频ID</th><th>采集评论</th><th>问题评论</th><th>作者回复</th><th>主题</th><th>融合</th><th>风险</th></tr></thead><tbody>{body}</tbody></table></section>"
def _video_row(x: dict[str, Any], i: int) -> str:
    topics = '、'.join(f"{k}({v})" for k,v in x['top_topics']) or '-'
    return f"<tr><td>{i}</td><td>{_e(x['source_name'])}</td><td><a href='{_e(x['video_url'])}' target='_blank'>{_e(x['video_id'])}</a></td><td>{x['collected']}</td><td>{x['question_count']}</td><td>{x['author_replies']}</td><td>{_e(topics)}</td><td>{int(x['fit_score'])}</td><td>{_e(_risk_text(x))}</td></tr>"
def _comment_table(comments: list[dict[str, Any]]) -> str:
    body = ''.join(_comment_row(c, i) for i,c in enumerate(comments,1))
    return f"<section><h2>6. 评论明细</h2><div class='toolbar'><input id='q' placeholder='搜索评论/账号/主题'><select id='topic'><option value='all'>全部主题</option>{_topic_options(comments)}</select><button onclick='resetFilters()'>重置</button></div><table id='commentTable'><thead><tr><th>序</th><th>账号</th><th>视频ID</th><th>昵称</th><th>主题</th><th>意图</th><th>评论</th></tr></thead><tbody>{body}</tbody></table></section>"
def _topic_options(comments: list[dict[str, Any]]) -> str:
    topics = sorted({t for c in comments for t in c['topics']})
    return ''.join(f"<option value='{_e(t)}'>{_e(t)}</option>" for t in topics)
def _comment_row(c: dict[str, Any], i: int) -> str:
    topic = '、'.join(c['topics'])
    return f"<tr data-topic='{_e(topic)}'><td>{i}</td><td>{_e(c['source_name'])}</td><td><a href='{_e(c['video_url'])}' target='_blank'>{_e(c['video_id'])}</a></td><td>{_e(c.get('nickname') or '')}</td><td>{_e(topic)}</td><td>{_e(c['sentiment'])}</td><td>{_e(c['content'])}</td></tr>"
def _risk_text(row: dict[str, Any]) -> str:
    r=[]
    if row.get('metric_suspicious'): r.append('指标可疑')
    if row['person_count']>1.5: r.append('主体不稳')
    if row['structure_completeness']<0.3: r.append('话术弱')
    if row['subtitle_consistency']<0.45: r.append('字幕弱')
    return '、'.join(r) or '-'
def _style() -> str:
    return """<style>body{font-family:'Microsoft YaHei',Arial,sans-serif;margin:0;background:#f5f7fb;color:#1f2937}header{background:#16365c;color:white;padding:28px 36px}section{margin:18px 28px;padding:18px;background:white;border-radius:12px;box-shadow:0 1px 4px #d6dce8}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;background:transparent;box-shadow:none;padding:0}.card{background:white;border-radius:12px;padding:16px;box-shadow:0 1px 4px #d6dce8}.card b{font-size:26px;color:#16365c}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}.opps{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.opp{border:1px solid #d9e2f3;border-radius:10px;padding:10px}table{width:100%;border-collapse:collapse;font-size:12px}th{position:sticky;top:0;background:#1f4e79;color:white;z-index:1}th,td{border:1px solid #d9e2f3;padding:6px;vertical-align:top}tbody tr:nth-child(even){background:#f7fbff}a{color:#0b63ce;font-weight:600}.toolbar{display:flex;gap:10px;margin:8px 0 12px}.toolbar input{width:360px}.toolbar input,.toolbar select,.toolbar button{padding:8px;border:1px solid #b4c6e7;border-radius:8px}</style>"""
def _script() -> str:
    return """<script>function applyFilters(){const q=document.getElementById('q').value.toLowerCase();const topic=document.getElementById('topic').value;document.querySelectorAll('#commentTable tbody tr').forEach(tr=>{const okQ=!q||tr.innerText.toLowerCase().includes(q);const okT=topic==='all'||tr.dataset.topic.includes(topic);tr.style.display=(okQ&&okT)?'':'none';});}function resetFilters(){document.getElementById('q').value='';document.getElementById('topic').value='all';applyFilters();}document.getElementById('q').addEventListener('input',applyFilters);document.getElementById('topic').addEventListener('change',applyFilters);</script>"""
def _card(label: str, value: Any) -> str: return f"<div class='card'><div>{_e(label)}</div><b>{_e(value)}</b></div>"
def _avg(rows: list[dict[str, Any]], key: str) -> float:
    vals=[float(r.get(key) or 0) for r in rows]; return mean(vals) if vals else 0.0
def _rate(a: int, b: int) -> float: return a / b if b else 0.0
def _e(value: Any) -> str: return html.escape(str(value), quote=True)
if __name__ == "__main__":
    raise SystemExit(main())


# -*- coding: utf-8 -*-
"""
youtube_auto_task.py  v15.0 (分布式处理架构：逐个总结 + 全局策划)
Architecture: RSS/Search -> Strategy Top 5 -> Single Video Analysis (x5) -> Global Synthesis -> Distribution
"""

import os
import re
import json
import time
import datetime
from datetime import timezone, timedelta
from pathlib import Path
import html
import requests
import feedparser
from openai import OpenAI

# ── Environment variables ────────────────────────────────────────────────────
FEISHU_WEBHOOK_URL    = os.getenv("FEISHU_WEBHOOK_URL", "")
OPENROUTER_API_KEY    = os.getenv("OPENROUTER_API_KEY", "")
QWEN_API_KEY          = os.getenv("QWEN_API_KEY", "")
TWTAPI_KEY            = os.getenv("TWTAPI_KEY", "") 
JIJIANYUN_WEBHOOK_URL = os.getenv("JIJIANYUN_WEBHOOK_URL", "") 
TOP_IMAGE_URL         = "http://mmbiz.qpic.cn/sz_mmbiz_png/SfPwFYYicIliagEk8zLcesc7sBVZqibHnxN8khWb60NicWDGKiaKQum7ysAXHwXW1RF4zKLKnMrsKYBDO5U3mPIhye2r4Zzdwica9XqaMWiaW8zU7s/0?wx_fmt=png"

# ── Content Strategy Constants ───────────────────────────────────────────────
DEEP_DIVE_THRESHOLD_SEC = 45 * 60 
MIN_CLIP_THRESHOLD_SEC = 10 * 60  
MAX_TOTAL_VIDEOS = 5 # 🚨 每日精选 5 个最火爆的视频进行“分布式处理”

CORE_CHANNELS = {
    "UC1yNl2E66ZzKApQdRu53wwA": {"name": "Lex Fridman", "cat": "深度播客"},
    "UCcefcZRL2oaA_uBNeo5UOWg": {"name": "a16z", "cat": "顶级VC"},
    "UCaOtN7i8H72E7Uj4P1-QzQA": {"name": "Dwarkesh Patel", "cat": "深度播客"},
    "UC0vOXJzXQGoqYq4n1-YkH-w": {"name": "All-In Podcast", "cat": "商业创投"},
    "UCLNgu_OupwoeESgtab33CCw": {"name": "Andrej Karpathy", "cat": "硬核技术"},
}

VIP_LIST = ["Elon Musk", "Sam Altman", "Jensen Huang", "Ilya Sutskever", "Dario Amodei", "Satya Nadella"]

def load_tracking_state():
    path = Path("data/yt_tracking.json")
    if path.exists():
        try: return json.loads(path.read_text("utf-8"))
        except: pass
    return {"vips": {}}

def parse_duration(s):
    if not s: return 0
    parts = str(s).split(':')
    return sum(int(x) * 60**i for i, x in enumerate(reversed(parts)))

def parse_views(s):
    s = str(s).lower().replace(',', '')
    if 'k' in s: return int(float(re.search(r'[\d\.]+', s).group()) * 1000)
    if 'm' in s: return int(float(re.search(r'[\d\.]+', s).group()) * 1000000)
    num = re.search(r'\d+', s)
    return int(num.group()) if num else 0

# ════════════════════════════════════════════════════════════════════════════
# Phase 1: 筛选 Top 5 最优信源
# ════════════════════════════════════════════════════════════════════════════
def scan_best_videos():
    print("\n[策略] 正在从全网情报中锁定 Top 5 核心母版视频...")
    now = datetime.datetime.now(timezone.utc)
    deadline = now - timedelta(hours=24)
    candidates = []

    # VIP 独占搜索
    for vip in VIP_LIST:
        try:
            url = f"https://www.youtube.com/results?search_query={requests.utils.quote(vip + ' interview')}&sp=CAI%253D"
            html_text = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15).text
            data = json.loads(re.search(r'(?:ytInitialData)\s*=\s*(\{.+?\});', html_text).group(1))
            items = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents'][0]['itemSectionRenderer']['contents']
            
            vip_vids = []
            for item in items:
                if 'videoRenderer' in item:
                    vr = item['videoRenderer']
                    d_sec = parse_duration(vr.get('lengthText', {}).get('simpleText', '0:00'))
                    v_count = parse_views(vr.get('viewCountText', {}).get('simpleText', '0'))
                    if d_sec > MIN_CLIP_THRESHOLD_SEC:
                        vip_vids.append({"video_id": vr['videoId'], "title": vr['title']['runs'][0]['text'], "author": vr['ownerText']['runs'][0]['text'], "views": v_count, "duration_sec": d_sec})
            
            if vip_vids:
                long_forms = [v for v in vip_vids if v["duration_sec"] >= DEEP_DIVE_THRESHOLD_SEC]
                selected = max(long_forms, key=lambda x: x["views"]) if long_forms else max(vip_vids, key=lambda x: x["duration_sec"])
                candidates.append(selected)
        except: pass

    # 去重并取播放量前 5
    unique = {v['video_id']: v for v in candidates}.values()
    final = sorted(unique, key=lambda x: x["views"], reverse=True)[:MAX_TOTAL_VIDEOS]
    print(f"  🎯 已锁定今日 5 大核心信源：{[v['title'][:15] for v in final]}")
    return final

# ════════════════════════════════════════════════════════════════════════════
# Phase 2: 分布式总结与全局策划
# ════════════════════════════════════════════════════════════════════════════
def run_single_video_analysis(video, model_type="claude"):
    print(f"  🎬 正在处理视频: {video['title'][:30]}...")
    yt_url = f"https://www.youtube.com/watch?v={video['video_id']}"
    try:
        resp = requests.get(f"https://r.jina.ai/{yt_url}", headers={"X-Return-Format": "text"}, timeout=40)
        transcript = " ".join(resp.text.split("Title:", 1)[-1].split()[:20000]) # 每个视频给足 2万字上限
        
        prompt = f"""你是一名资深科技分析师。请对该视频进行深度拆解。
【字幕内容】：{transcript}
【要求】：只输出一个合法的 JSON。如有引号用 \\" 转义。
{{
  "title": "深度中文标题",
  "original_english_title": "{video['title']}",
  "tldr": "一句话中文总结(TL;DR)",
  "core_thesis": "最核心逻辑",
  "arguments": ["核心论点1", "核心论点2"],
  "counter_consensus": "反常规认知点",
  "implications": "产业影响推演"
}}"""
        if model_type == "claude":
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, json={"model": "anthropic/claude-3.7-sonnet", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}, timeout=120)
            return json.loads(re.search(r'\{[\s\S]*\}', r.json()["choices"][0]["message"]["content"]).group(0))
        else:
            client = OpenAI(api_key=QWEN_API_KEY, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
            r = client.chat.completions.create(model="qwen-max", messages=[{"role": "user", "content": prompt}], temperature=0.3)
            return json.loads(re.search(r'\{[\s\S]*\}', r.choices[0].message.content).group(0))
    except Exception as e:
        print(f"    ❌ 单视频处理失败: {e}")
        return None

def generate_global_wrapup(summaries, model_type="claude"):
    print("\n[策划] 正在进行全案策划（标题与导读）...")
    base_data = [{"title": s['title'], "tldr": s['tldr']} for s in summaries if s]
    prompt = f"""基于今日这 {len(base_data)} 篇深度情报，请策划一个全局标题和精炼导读。
【数据】：{json.dumps(base_data, ensure_ascii=False)}
【要求】：输出合法 JSON。
{{
  "article_title": "5-10字爆款中文标题",
  "article_summary": "30字最抓马的反共识动向总结"
}}"""
    try:
        if model_type == "claude":
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, json={"model": "anthropic/claude-3.7-sonnet", "messages": [{"role": "user", "content": prompt}]}, timeout=60)
            return json.loads(re.search(r'\{[\s\S]*\}', r.json()["choices"][0]["message"]["content"]).group(0))
        else:
            client = OpenAI(api_key=QWEN_API_KEY, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
            r = client.chat.completions.create(model="qwen-max", messages=[{"role": "user", "content": prompt}])
            return json.loads(re.search(r'\{[\s\S]*\}', r.choices[0].message.content).group(0))
    except: return {"article_title": "硅谷 AI 深度情报", "article_summary": "今日硬核 AI 情报汇总提取"}

# ════════════════════════════════════════════════════════════════════════════
# Phase 3: 视觉推送层 (对齐需求)
# ════════════════════════════════════════════════════════════════════════════
def build_and_push_final(summaries, wrapup, channel="feishu"):
    if not summaries: return
    date_str = datetime.datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    
    if channel == "feishu":
        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": "**⚠️ 每早8点准时更新 | 从昨晚放出的深度访谈里拆解硅谷**"}},
            {"tag": "note", "elements": [{"tag": "lark_md", "content": f"💡 **导读**：{wrapup['article_summary']}"}], "background_color": "blue"},
            {"tag": "hr"}
        ]
        for i, v in enumerate(summaries, 1):
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🍉 {i}. {v['title']}**\n💡 {v['original_english_title']}\n**TL;DR:** {v['tldr']}"}})
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🎯 核心主张**：{v['core_thesis']}\n**🧠 反共识**：{v['counter_consensus']}"}})
            elements.append({"tag": "hr"})
        
        payload = {"msg_type": "interactive", "card": {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": "🌍 硅谷油管长博客拆解"}, "subtitle": {"tag": "plain_text", "content": f"{wrapup['article_title']} | {date_str}"}, "template": "purple"}, "elements": elements}}
        requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=10)
        
    else: # WeChat
        html_p = [f'<section style="text-align:center;"><img src="{TOP_IMAGE_URL}" style="max-width:100%; border-radius:8px;"/></section>',
                  f'<section style="margin:20px 0; padding:15px; background:#f8f9fa; border-left:5px solid #2b579a;"><p style="font-size:15px;"><strong>⚠️ 每早 8 点｜全域硅谷情报拆解</strong><br><br><strong>💡 今日摘要：</strong>{wrapup["article_summary"]}</p></section>']
        for i, v in enumerate(summaries, 1):
            v_h = f"""<section style="margin-bottom:30px;"><h2 style="font-size:18px; color:#2b579a; border-bottom:1px solid #eef2f8;">🍉 {i}. {v['title']}</h2>
                      <p style="font-size:13px; color:#999; margin:5px 0;">Source: {v['original_english_title']}</p>
                      <p style="margin:10px 0; font-size:15px; background:#eef2f8; padding:10px; border-radius:6px;"><strong>TL;DR:</strong> {v['tldr']}</p>
                      <p style="font-size:14px; line-height:1.6; color:#555;"><strong>🎯 核心主张：</strong>{v['core_thesis']}<br><strong>🧱 证据：</strong>{" / ".join(v['arguments'][:2])}<br><strong>🧠 反共识：</strong>{v['counter_consensus']}</p></section>"""
            html_p.append(v_h)
        
        payload = {"author": "大尉 Prinski", "cover_jpg": TOP_IMAGE_URL, "html_content": "".join(html_p).replace('\n',''), "title": wrapup['article_title']}
        requests.post(JIJIANYUN_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=15)

def main():
    print("=" * 60 + "\n🚀 深度情报分布式引擎 V15.0 启动\n" + "=" * 60)
    best_videos = scan_best_videos()
    if not best_videos: return

    # 1. 独立分析每个视频 (Claude 通道)
    claude_summaries = []
    for v in best_videos:
        res = run_single_video_analysis(v, "claude")
        if res: claude_summaries.append(res)
    
    if claude_summaries:
        claude_wrap = generate_global_wrapup(claude_summaries, "claude")
        build_and_push_final(claude_summaries, claude_wrap, "feishu")

    # 2. 独立分析每个视频 (Qwen 通道 - 为了满足您的AB测试对比需求)
    qwen_summaries = []
    for v in best_videos:
        res = run_single_video_analysis(v, "qwen")
        if res: qwen_summaries.append(res)
    
    if qwen_summaries:
        qwen_wrap = generate_global_wrapup(qwen_summaries, "qwen")
        build_and_push_final(qwen_summaries, qwen_wrap, "wechat")

    print("\n🎉 V15.0 分布式全案分发完毕！")

if __name__ == "__main__":
    main()

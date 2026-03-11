# -*- coding: utf-8 -*-
"""
youtube_auto_task.py  v17.0 (排版精修版：清洗不可见乱码 + 论点分列排版 + 全新导读话术)
Architecture: RSS/Search -> Strict 24H Filter -> Distributed Analysis -> Global Synthesis
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

# ── 环境变量 ─────────────────────────────────────────────────────
FEISHU_WEBHOOK_URL    = os.getenv("FEISHU_WEBHOOK_URL", "")
OPENROUTER_API_KEY    = os.getenv("OPENROUTER_API_KEY", "")
QWEN_API_KEY          = os.getenv("QWEN_API_KEY", "")
TWTAPI_KEY            = os.getenv("TWTAPI_KEY", "") 
JIJIANYUN_WEBHOOK_URL = os.getenv("JIJIANYUN_WEBHOOK_URL", "") 
TOP_IMAGE_URL         = "http://mmbiz.qpic.cn/sz_mmbiz_png/SfPwFYYicIliagEk8zLcesc7sBVZqibHnxN8khWb60NicWDGKiaKQum7ysAXHwXW1RF4zKLKnMrsKYBDO5U3mPIhye2r4Zzdwica9XqaMWiaW8zU7s/0?wx_fmt=png"

# ── 策略常量 ─────────────────────────────────────────────────────
DEEP_DIVE_THRESHOLD_SEC = 40 * 60 
MAX_TOTAL_VIDEOS = 5 

VIP_LIST = ["Elon Musk", "Sam Altman", "Jensen Huang", "Ilya Sutskever", "Dario Amodei", "Satya Nadella", "DeepSeek"]
CORE_CHANNELS = {
    "UC1yNl2E66ZzKApQdRu53wwA": {"name": "Lex Fridman", "cat": "深度播客"},
    "UCcefcZRL2oaA_uBNeo5UOWg": {"name": "a16z", "cat": "顶级VC"},
    "UCaOtN7i8H72E7Uj4P1-QzQA": {"name": "Dwarkesh Patel", "cat": "深度播客"},
    "UC0vOXJzXQGoqYq4n1-YkH-w": {"name": "All-In Podcast", "cat": "商业创投"},
}

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

# ════════════════════════════════════════════════════════════════
# Phase 1: 严格时空锁定（只抓过去 24 小时）
# ════════════════════════════════════════════════════════════════
def scan_best_videos_strictly():
    print("\n[扫描] 启动 24H 强效过滤引擎（排除所有老视频）...")
    now = datetime.datetime.now(timezone.utc)
    deadline = now - timedelta(hours=24)
    candidates = []

    for ch_id, info in CORE_CHANNELS.items():
        try:
            feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}")
            for entry in feed.entries:
                pub_time = datetime.datetime.strptime(entry.published, "%Y-%m-%dT%H:%M:%S%z")
                if pub_time > deadline:
                    candidates.append({
                        "video_id": entry.yt_videoid, 
                        "title": entry.title, 
                        "author": info["name"], 
                        "views": 999999, 
                        "duration_sec": 3600
                    })
        except: pass

    for vip in VIP_LIST:
        try:
            url = f"https://www.youtube.com/results?search_query={requests.utils.quote(vip + ' interview')}&sp=EgQIAhAB"
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
            html_text = requests.get(url, headers=headers, timeout=15).text
            
            data_match = re.search(r'(?:ytInitialData)\s*=\s*(\{.+?\});', html_text)
            if not data_match: continue
            data = json.loads(data_match.group(1))
            
            try:
                items = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents'][0]['itemSectionRenderer']['contents']
            except: continue

            for item in items:
                if 'videoRenderer' in item:
                    vr = item['videoRenderer']
                    title = vr['title']['runs'][0]['text']
                    time_text = vr.get('publishedTimeText', {}).get('simpleText', '').lower()
                    
                    if any(x in time_text for x in ['day', 'week', 'month', 'year', '天', '周', '月', '年']):
                        continue
                    if not any(x in time_text for x in ['hour', 'minute', 'second', 'ago', '前', '刚刚']):
                        continue

                    d_sec = parse_duration(vr.get('lengthText', {}).get('simpleText', '0:00'))
                    v_count = parse_views(vr.get('viewCountText', {}).get('simpleText', '0'))
                    
                    if d_sec > 600:
                        candidates.append({
                            "video_id": vr['videoId'], 
                            "title": title, 
                            "author": vr['ownerText']['runs'][0]['text'], 
                            "views": v_count, 
                            "duration_sec": d_sec
                        })
                        print(f"  ✨ 捕获新鲜母版: {title[:25]}... ({time_text})")
        except Exception as e:
            print(f"  ⚠️ 搜索 @{vip} 异常: {e}")

    unique = {}
    for v in candidates:
        if v['video_id'] not in unique:
            unique[v['video_id']] = v
    
    final = sorted(unique.values(), key=lambda x: (x['duration_sec'] >= DEEP_DIVE_THRESHOLD_SEC, x['views']), reverse=True)[:MAX_TOTAL_VIDEOS]
    print(f"\n🎯 最终入选今日情报的 24H 新鲜母版共 {len(final)} 个")
    return final

# ════════════════════════════════════════════════════════════════
# Phase 2: 分布式处理
# ════════════════════════════════════════════════════════════════
def run_single_video_analysis(video, model_type="claude"):
    print(f"  🎬 正在解剖: {video['title'][:30]}...")
    yt_url = f"https://www.youtube.com/watch?v={video['video_id']}"
    try:
        resp = requests.get(f"https://r.jina.ai/{yt_url}", headers={"X-Return-Format": "text"}, timeout=40)
        transcript = " ".join(resp.text.split("Title:", 1)[-1].split()[:20000])
        
        prompt = f"""你是一名顶级科技分析师。请对该视频进行深度逻辑拆解。
【字幕内容】：{transcript}
【要求】：只输出一个合法的 JSON。
{{
  "title": "深度中文标题",
  "original_english_title": "{video['title']}",
  "tldr": "一句话中文总结(TL;DR)",
  "core_thesis": "最核心逻辑",
  "arguments": ["论点1", "论点2", "论点3"], 
  "counter_consensus": "反常规认知",
  "implications": "行业推演"
}}"""
        if model_type == "claude":
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, json={"model": "anthropic/claude-3.7-sonnet", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}, timeout=120)
            return json.loads(re.search(r'\{[\s\S]*\}', r.json()["choices"][0]["message"]["content"]).group(0))
        else:
            client = OpenAI(api_key=QWEN_API_KEY, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
            r = client.chat.completions.create(model="qwen-max", messages=[{"role": "user", "content": prompt}], temperature=0.3)
            return json.loads(re.search(r'\{[\s\S]*\}', r.choices[0].message.content).group(0))
    except: return None

def generate_global_wrapup(summaries, model_type="claude"):
    print("\n[全案] 正在合成爆款标题与精华摘要...")
    base_data = [{"title": s['title'], "tldr": s['tldr']} for s in summaries if s]
    prompt = f"""基于今日这 {len(base_data)} 篇情报，起一个5-10字标题，并写一个30字内最抓马的反共识导读。
【数据】：{json.dumps(base_data, ensure_ascii=False)}
【输出】：JSON
{{ "article_title": "爆款标题", "article_summary": "30字导读" }}"""
    try:
        if model_type == "claude":
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, json={"model": "anthropic/claude-3.7-sonnet", "messages": [{"role": "user", "content": prompt}]}, timeout=60)
            return json.loads(re.search(r'\{[\s\S]*\}', r.json()["choices"][0]["message"]["content"]).group(0))
        else:
            client = OpenAI(api_key=QWEN_API_KEY, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
            r = client.chat.completions.create(model="qwen-max", messages=[{"role": "user", "content": prompt}])
            return json.loads(re.search(r'\{[\s\S]*\}', r.choices[0].message.content).group(0))
    except: return {"article_title": "硅谷 AI 深度情报", "article_summary": "今日硬核 AI 情报汇总"}

# ════════════════════════════════════════════════════════════════
# Phase 3: 多端视觉分发 (V17.0 排版精修版)
# ════════════════════════════════════════════════════════════════
def build_and_push(summaries, wrapup, channel="feishu"):
    if not summaries: return
    date_str = datetime.datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    
    if channel == "feishu":
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "**⚠️ 每早 8 点｜拆解超长视频访谈｜硅谷大佬在想什么**"}},
                    {"tag": "note", "elements": [{"tag": "lark_md", "content": f"💡 **导读**：{wrapup['article_summary']}"}], "background_color": "blue"},
                    {"tag": "hr"}]
        for i, v in enumerate(summaries, 1):
            # 🚨 强力清洗 TL;DR 中的不可见字符和多余空格
            clean_tldr = str(v.get('tldr', '')).replace('\xa0', ' ').replace('\u200b', '').strip()
            # 🚨 将论点提取为项目符号列表
            args_text = "\n".join([f"• {str(arg).strip()}" for arg in v.get('arguments', [])])
            
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🍉 {i}. {v['title']}**\n💡 {v['original_english_title']}\n**TL;DR:** {clean_tldr}"}})
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🎯 核心主张**：{v.get('core_thesis', '')}\n**🧱 论点与证据链**：\n{args_text}\n**🧠 反共识**：{v.get('counter_consensus', '')}"}})
            elements.append({"tag": "hr"})
        
        payload = {"msg_type": "interactive", "card": {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": "🌍 硅谷油管长博客拆解"}, "subtitle": {"tag": "plain_text", "content": f"{wrapup['article_title']} | {date_str}"}, "template": "purple"}, "elements": elements}}
        requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=10)
        
    else: # WeChat
        html_p = [f'<section style="text-align:center;"><img src="{TOP_IMAGE_URL}" style="max-width:100%; border-radius:8px;"/></section>',
                  f'<section style="margin:20px 0; padding:15px; background:#f8f9fa; border-left:5px solid #2b579a;"><p style="font-size:15px;"><strong>⚠️ 每早 8 点｜拆解超长视频访谈｜硅谷大佬在想什么</strong><br><br><strong>💡 今日摘要：</strong>{wrapup["article_summary"]}</p></section>']
        for i, v in enumerate(summaries, 1):
            # 🚨 强力清洗 TL;DR 中的不可见字符
            clean_tldr = str(v.get('tldr', '')).replace('\xa0', ' ').replace('\u200b', '').strip()
            # 🚨 微信端独立渲染为带有间距的 List
            args_html = "".join([f'<div style="margin-top: 6px; padding-left: 6px;">• {str(a).strip()}</div>' for a in v.get('arguments', [])])
            
            v_h = f"""<section style="margin-bottom:35px;">
                      <h2 style="font-size:18px; color:#2b579a; border-bottom:1px solid #eef2f8; padding-bottom:8px;">🍉 {i}. {v['title']}</h2>
                      <p style="font-size:13px; color:#999; margin:6px 0;">Source: {v['original_english_title']}</p>
                      <div style="margin:12px 0; font-size:15px; background:#eef2f8; padding:12px; border-radius:6px; color:#333;"><strong>TL;DR:</strong> {clean_tldr}</div>
                      <div style="font-size:15px; line-height:1.7; color:#444;">
                          <p style="margin: 10px 0;"><strong>🎯 核心主张：</strong>{v.get('core_thesis', '')}</p>
                          <div style="margin: 10px 0;"><strong>🧱 论点与证据链：</strong>{args_html}</div>
                          <p style="margin: 10px 0;"><strong>🧠 反共识：</strong>{v.get('counter_consensus', '')}</p>
                      </div>
                      </section>"""
            html_p.append(v_h)
        
        payload = {"author": "大尉 Prinski", "cover_jpg": TOP_IMAGE_URL, "html_content": "".join(html_p).replace('\n',''), "title": wrapup['article_title']}
        requests.post(JIJIANYUN_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=15)

def main():
    print("=" * 60 + "\n🚀 24H 严格时空锁定情报系统 V17.0 启动\n" + "=" * 60)
    fresh_videos = scan_best_videos_strictly()
    if not fresh_videos:
        print("📭 过去 24 小时没有任何新鲜情报。")
        return

    # 1. Claude 通道（发飞书）
    c_summaries = []
    for v in fresh_videos:
        res = run_single_video_analysis(v, "claude")
        if res: c_summaries.append(res)
    if c_summaries:
        build_and_push(c_summaries, generate_global_wrapup(c_summaries, "claude"), "feishu")

    # 2. Qwen 通道（发微信）
    q_summaries = []
    for v in fresh_videos:
        res = run_single_video_analysis(v, "qwen")
        if res: q_summaries.append(res)
    if q_summaries:
        build_and_push(q_summaries, generate_global_wrapup(q_summaries, "qwen"), "wechat")

    print("\n🎉 V17.0 24H 鲜瓜分发完毕！")

if __name__ == "__main__":
    main()

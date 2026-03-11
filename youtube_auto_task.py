# -*- coding: utf-8 -*-
"""
youtube_auto_task.py  v19.0 (泛科技AI智能过滤 + 终极乱码粉碎机 + 原生列表排版)
Architecture: RSS/Search -> Top 15 Candidates -> LLM Semantic Filter -> Top 5 Synthesis
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
CANDIDATE_POOL_SIZE = 15   # 🚨 扩大候补池，防垃圾视频占位
MAX_VALID_VIDEOS = 5       # 最终只取 5 个纯正科技视频

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

# 🚨 终极文本清洗器：使用正则碾碎所有不可见的空白符（解决 TL;DR 乱码）
def sanitize_text(text):
    if not text: return ""
    # 强力猎杀：\xa0, \u200b, \u3000, \r, \n, \t 全部压平成一个标准空格
    clean = re.sub(r'[\s\u200b\u3000\xa0]+', ' ', str(text))
    return clean.strip()

# ════════════════════════════════════════════════════════════════
# Phase 1: 扩大撒网 (只抓过去 24 小时)
# ════════════════════════════════════════════════════════════════
def scan_best_videos_strictly():
    print(f"\n[扫描] 启动 24H 引擎，构建 {CANDIDATE_POOL_SIZE} 大候补池...")
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
        except: pass

    unique = {}
    for v in candidates:
        if v['video_id'] not in unique:
            unique[v['video_id']] = v
    
    # 按照先长视频、再高播放量排序，取前 15 名进入安检池
    pool = sorted(unique.values(), key=lambda x: (x['duration_sec'] >= DEEP_DIVE_THRESHOLD_SEC, x['views']), reverse=True)[:CANDIDATE_POOL_SIZE]
    print(f"🎯 寻获 {len(pool)} 个新鲜视频，即将交由 AI 进行领域安检。")
    return pool

# ════════════════════════════════════════════════════════════════
# Phase 2: 分布式处理与 AI 语义安检护盾
# ════════════════════════════════════════════════════════════════
def run_single_video_analysis(video, model_type="claude"):
    print(f"  🎬 正在查验与解剖: {video['title'][:30]}...")
    yt_url = f"https://www.youtube.com/watch?v={video['video_id']}"
    try:
        resp = requests.get(f"https://r.jina.ai/{yt_url}", headers={"X-Return-Format": "text"}, timeout=40)
        if resp.status_code != 200 or len(resp.text) < 500:
            return None
        transcript = " ".join(resp.text.split("Title:", 1)[-1].split()[:20000])
        
        # 🚨 AI 语义安检：强迫大模型判断该内容是否属于我们的圈子
        prompt = f"""你是一名顶级科技与创投分析师。
【核心任务一：领域过滤】
我们只关注“硅谷、科技公司、AI人工智能、VC创投、商业逻辑、一级/二级市场投资”等泛科技与商业领域。
如果该视频内容与上述领域【完全无关】（例如：纯粹的美食探店、美妆、日常Vlog、不相关的社会火灾新闻、纯体育等），请直接且仅输出：
{{"irrelevant": true}}

【核心任务二：深度拆解】
如果内容相关，请仔细阅读字幕并输出以下JSON结构，绝不能包含Markdown标记：
{{
  "irrelevant": false,
  "title": "深度中文标题",
  "original_english_title": "{video['title']}",
  "tldr": "一句话中文总结(TL;DR)",
  "core_thesis": "最核心逻辑",
  "arguments": ["论点1", "论点2", "论点3"], 
  "counter_consensus": "反常规认知",
  "implications": "行业推演"
}}
【字幕内容】：{transcript}
"""
        result_json = None
        if model_type == "claude":
            r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, json={"model": "anthropic/claude-3.7-sonnet", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}, timeout=120)
            result_json = json.loads(re.search(r'\{[\s\S]*\}', r.json()["choices"][0]["message"]["content"]).group(0))
        else:
            client = OpenAI(api_key=QWEN_API_KEY, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
            r = client.chat.completions.create(model="qwen-max", messages=[{"role": "user", "content": prompt}], temperature=0.3)
            result_json = json.loads(re.search(r'\{[\s\S]*\}', r.choices[0].message.content).group(0))
        
        # 🚨 检查模型判决：如果是无关垃圾视频，直接丢弃
        if result_json and result_json.get("irrelevant") is True:
            print("    ⏭️ [护盾拦截] 判定为非科技/投资领域无关内容，直接丢弃。")
            return None
            
        print("    ✅ [安检通过] 内容优质，解析成功。")
        return result_json
        
    except Exception as e: 
        return None

def generate_global_wrapup(summaries, model_type="claude"):
    print(f"\n[全案] 成功集齐 {len(summaries)} 篇纯正科技情报，正在合成爆款标题...")
    base_data = [{"title": s['title'], "tldr": s['tldr']} for s in summaries if s]
    prompt = f"""基于今日这 {len(base_data)} 篇硅谷科技情报，起一个5-10字标题，并写一个30字内最抓马的反共识导读。
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
    except: return {"article_title": "硅谷前沿深度解码", "article_summary": "今日硬核科技与投资情报汇总"}

# ════════════════════════════════════════════════════════════════
# Phase 3: 多端视觉分发 (彻底无乱码版)
# ════════════════════════════════════════════════════════════════
def build_and_push(summaries, wrapup, channel="feishu"):
    if not summaries: return
    date_str = datetime.datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    
    if channel == "feishu":
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "**⚠️ 每早 8 点｜拆解超长视频访谈｜硅谷大佬在想什么**"}},
                    {"tag": "note", "elements": [{"tag": "lark_md", "content": f"💡 **今日摘要**：{sanitize_text(wrapup['article_summary'])}"}], "background_color": "blue"},
                    {"tag": "hr"}]
        for i, v in enumerate(summaries, 1):
            clean_tldr = sanitize_text(v.get('tldr', ''))
            core_thesis = sanitize_text(v.get('core_thesis', ''))
            counter_consensus = sanitize_text(v.get('counter_consensus', ''))
            
            args_list = [sanitize_text(a) for a in v.get('arguments', []) if a]
            args_text = "\n".join([f"• {a}" for a in args_list])
            
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🍉 {i}. {sanitize_text(v['title'])}**\n💡 {sanitize_text(v['original_english_title'])}\n**TL;DR:** {clean_tldr}"}})
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🎯 核心主张**：{core_thesis}\n**🧱 论点与证据链**：\n{args_text}\n**🧠 反共识**：{counter_consensus}"}})
            elements.append({"tag": "hr"})
        
        payload = {"msg_type": "interactive", "card": {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": "🌍 硅谷油管长博客拆解"}, "subtitle": {"tag": "plain_text", "content": f"{sanitize_text(wrapup['article_title'])} | {date_str}"}, "template": "purple"}, "elements": elements}}
        requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=10)
        
    else: # WeChat
        html_p = [f'<section style="text-align:center;"><img src="{TOP_IMAGE_URL}" style="max-width:100%; border-radius:8px;"/></section>',
                  f'<section style="margin:20px 0; padding:15px; background:#f8f9fa; border-left:5px solid #2b579a;"><p style="font-size:15px;"><strong>⚠️ 每早 8 点｜拆解超长视频访谈｜硅谷大佬在想什么</strong><br><br><strong>💡 今日摘要：</strong>{sanitize_text(wrapup["article_summary"])}</p></section>']
        for i, v in enumerate(summaries, 1):
            clean_tldr = sanitize_text(v.get('tldr', ''))
            core_thesis = sanitize_text(v.get('core_thesis', ''))
            counter_consensus = sanitize_text(v.get('counter_consensus', ''))
            
            args_list = [sanitize_text(a) for a in v.get('arguments', []) if a]
            args_html = "<ul style='margin: 10px 0; padding-left: 22px; font-size: 14px; color: #555; line-height: 1.6; list-style-type: disc;'>"
            for a in args_list:
                args_html += f"<li style='margin-bottom: 8px; padding-left: 4px;'>{a}</li>"
            args_html += "</ul>"
            
            v_h = f"""<section style="margin-bottom:35px;">
                      <h2 style="font-size:18px; color:#2b579a; border-bottom:1px solid #eef2f8; padding-bottom:8px;">🍉 {i}. {sanitize_text(v['title'])}</h2>
                      <p style="font-size:13px; color:#999; margin:6px 0;">Source: {sanitize_text(v['original_english_title'])}</p>
                      <div style="margin:12px 0; font-size:15px; background:#eef2f8; padding:12px; border-radius:6px; color:#333;"><strong>TL;DR:</strong> {clean_tldr}</div>
                      <div style="font-size:15px; line-height:1.7; color:#444;">
                          <p style="margin: 10px 0;"><strong>🎯 核心主张：</strong>{core_thesis}</p>
                          <div style="margin: 10px 0;"><strong>🧱 论点与证据链：</strong>{args_html}</div>
                          <p style="margin: 10px 0;"><strong>🧠 反共识：</strong>{counter_consensus}</p>
                      </div>
                      </section>"""
            html_p.append(v_h)
        
        payload = {"author": "大尉 Prinski", "cover_jpg": TOP_IMAGE_URL, "html_content": "".join(html_p).replace('\n',''), "title": sanitize_text(wrapup['article_title'])}
        requests.post(JIJIANYUN_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=15)

def main():
    print("=" * 60 + "\n🚀 硅谷智能护盾情报系统 V19.0 启动\n" + "=" * 60)
    candidates_pool = scan_best_videos_strictly()
    if not candidates_pool:
        print("📭 过去 24 小时没有任何新鲜情报。")
        return

    # 1. Claude 通道（发飞书）
    print("\n---------- [启动 Claude 分析流] ----------")
    c_summaries = []
    for v in candidates_pool:
        res = run_single_video_analysis(v, "claude")
        if res: 
            c_summaries.append(res)
        # 🚨 收集满 5 个有效的优质科技视频，立刻停止干活！
        if len(c_summaries) >= MAX_VALID_VIDEOS:
            break
            
    if c_summaries:
        build_and_push(c_summaries, generate_global_wrapup(c_summaries, "claude"), "feishu")

    # 2. Qwen 通道（发微信）
    print("\n---------- [启动 Qwen 分析流] ----------")
    q_summaries = []
    for v in candidates_pool:
        res = run_single_video_analysis(v, "qwen")
        if res: 
            q_summaries.append(res)
        if len(q_summaries) >= MAX_VALID_VIDEOS:
            break
            
    if q_summaries:
        build_and_push(q_summaries, generate_global_wrapup(q_summaries, "qwen"), "wechat")

    print("\n🎉 V19.0 (智能过滤无乱码版) 圆满分发完毕！")

if __name__ == "__main__":
    main()

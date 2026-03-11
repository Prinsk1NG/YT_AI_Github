# -*- coding: utf-8 -*-
"""
youtube_auto_task.py  v11.0 (全能情报版：YouTube + TwtAPI X 轨道)
Architecture: RSS/Search + TwtAPI -> Jina -> Claude(JSON) -> Feishu | Qwen(JSON) -> WeChat
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
FIRECRAWL_API_KEY     = os.getenv("FIRECRAWL_API_KEY", "")
TWTAPI_KEY            = os.getenv("TWTAPI_KEY", "")  # 🚨 新增：TwtAPI 的 API Key

# 🚨 极简云与微信公众号相关配置
JIJIANYUN_WEBHOOK_URL = os.getenv("JIJIANYUN_WEBHOOK_URL", "") 
TOP_IMAGE_URL         = "http://mmbiz.qpic.cn/sz_mmbiz_png/SfPwFYYicIliagEk8zLcesc7sBVZqibHnxN8khWb60NicWDGKiaKQum7ysAXHwXW1RF4zKLKnMrsKYBDO5U3mPIhye2r4Zzdwica9XqaMWiaW8zU7s/0?wx_fmt=png"

# ── Tracking & Thresholds ────────────────────────────────────────────────────
MIN_DURATION_SEC = 15 * 60
MIN_VIEWS        = 5000
EVICTION_DAYS    = 30

CORE_CHANNELS = {
    "UC1yNl2E66ZzKApQdRu53wwA": {"name": "Lex Fridman", "cat": "深度播客"},
    "UCcefcZRL2oaA_uBNeo5UOWg": {"name": "a16z", "cat": "顶级VC"},
    "UCaOtN7i8H72E7Uj4P1-QzQA": {"name": "Dwarkesh Patel", "cat": "深度播客"},
    "UC0vOXJzXQGoqYq4n1-YkH-w": {"name": "All-In Podcast", "cat": "商业创投"},
    "UCcefcZRL2oaA_uBNeo5UOWh": {"name": "Y Combinator", "cat": "顶级VC"},
    "UCLNgu_OupwoeESgtab33CCw": {"name": "Andrej Karpathy", "cat": "硬核技术"},
    "UCbfYPyITQ-7l4upoX8nvctg": {"name": "Two Minute Papers", "cat": "学术前沿"},
    "UC3XGzPbbB1_xR0Q8z_K3_ww": {"name": "AI Explained", "cat": "深度评测"},
}

VIP_LIST = ["Elon Musk", "Sam Altman", "Jensen Huang", "Ilya Sutskever", "Dario Amodei", "Satya Nadella"]

# 🚨 X 轨道关注名单 (对应 TwtAPI 的 username)
X_VIP_ACCOUNTS = ["sama", "elonmusk", "karpathy", "darioamodei", "OpenAI", "AnthropicAI"]

def load_tracking_state():
    path = Path("data/yt_tracking.json")
    if path.exists():
        try: return json.loads(path.read_text("utf-8"))
        except: pass
    return {"channels": {}, "vips": {}, "x_accounts": {}}

def save_tracking_state(state):
    path = Path("data/yt_tracking.json")
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")

def parse_duration(duration_str):
    if not duration_str: return 0
    parts = str(duration_str).split(':')
    secs = 0
    for part in parts: secs = secs * 60 + int(part.replace(',', '').strip())
    return secs

def parse_views(view_str):
    if not view_str: return 0
    view_str = str(view_str).lower().replace(',', '')
    if 'k' in view_str: return int(float(re.search(r'[\d\.]+', view_str).group()) * 1000)
    if 'm' in view_str: return int(float(re.search(r'[\d\.]+', view_str).group()) * 1000000)
    num = re.search(r'\d+', view_str)
    return int(num.group()) if num else 0

# ════════════════════════════════════════════════════════════════════════════
# Phase 1: 搜索与爬虫 (YouTube)
# ════════════════════════════════════════════════════════════════════════════
def fetch_html_bypass(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        mirror_url = url.replace("youtube.com", "invidious.jing.rocks") 
        return requests.get(mirror_url, headers=headers, timeout=15).text
    except:
        return requests.get(url, headers=headers, timeout=15).text

def native_youtube_search(query, limit=5):
    url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}&sp=CAI%253D"
    results = []
    try:
        html_text = fetch_html_bypass(url)
        match = re.search(r'(?:ytInitialData|window\["ytInitialData"\])\s*=\s*(\{.+?\});', html_text)
        if not match: return results
        data = json.loads(match.group(1))
        contents = data.get('contents', {}).get('twoColumnSearchResultsRenderer', {}).get('primaryContents', {}).get('sectionListRenderer', {}).get('contents', [])
        if not contents: return results
        video_items = contents[0].get('itemSectionRenderer', {}).get('contents', [])
        for item in video_items:
            if 'videoRenderer' in item:
                vr = item['videoRenderer']
                results.append({
                    "id": vr.get('videoId'), "title": vr.get('title', {}).get('runs', [{}])[0].get('text', ''),
                    "channel": vr.get('ownerText', {}).get('runs', [{}])[0].get('text', ''),
                    "publishedTime": vr.get('publishedTimeText', {}).get('simpleText', ''),
                    "viewCount": vr.get('viewCountText', {}).get('simpleText', '0'),
                    "duration": vr.get('lengthText', {}).get('simpleText', '0:00')
                })
                if len(results) >= limit: break
    except: pass
    return results

def scan_rss_channels(tracking_state):
    print(f"\n[轨道A] 正在通过 RSS 扫描核心频道...")
    now = datetime.datetime.now(timezone.utc)
    deadline = now - timedelta(hours=24)
    results = []
    for ch_id, info in CORE_CHANNELS.items():
        try:
            feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}")
            for entry in feed.entries:
                pub_time = datetime.datetime.strptime(entry.published, "%Y-%m-%dT%H:%M:%S%z")
                if pub_time > deadline:
                    results.append({"video_id": entry.yt_videoid, "title": entry.title, "author": info["name"], "category": info["cat"]})
        except: pass
    return results

def scan_vip_interviews(tracking_state):
    print(f"\n[轨道B] 正在全网搜索 VIP 大佬最新访谈...")
    results = []
    for vip in VIP_LIST:
        try:
            search_res = native_youtube_search(f'"{vip}" (interview OR podcast)', limit=3)
            for vid in search_res:
                if parse_duration(vid.get("duration", "0:00")) < MIN_DURATION_SEC: continue
                if parse_views(vid.get("viewCount", "0")) < MIN_VIEWS: continue
                results.append({"video_id": vid["id"], "title": vid["title"], "author": vid.get("channel", "Unknown"), "category": f"大佬追踪: {vip}"})
        except: pass
    return results

def fetch_transcripts(video_list):
    print("\n[提取] 启动纯净解析引擎...")
    valid_videos = []
    seen_ids = set()
    for v in video_list:
        vid = v["video_id"]
        if vid in seen_ids: continue
        seen_ids.add(vid)
        yt_url = f"https://www.youtube.com/watch?v={vid}"
        full_text = ""
        try:
            resp = requests.get(f"https://r.jina.ai/{yt_url}", headers={"Accept": "text/plain", "X-Return-Format": "text"}, timeout=40)
            if resp.status_code == 200 and len(resp.text) > 500:
                raw_text = resp.text
                if "Title:" in raw_text: raw_text = raw_text.split("Title:", 1)[-1]
                full_text = re.sub(r'\[.*?\]\(.*?\)', '', raw_text)
        except: pass

        if full_text and len(full_text) > 800:
            v["transcript"] = " ".join(full_text.split())
            valid_videos.append(v)
            print(f"  ✅ 成功: {v['title'][:25]}...")
    return valid_videos

# ════════════════════════════════════════════════════════════════════════════
# 🚀 轨道 C: TwtAPI X 情报提取
# ════════════════════════════════════════════════════════════════════════════
def fetch_x_updates():
    if not TWTAPI_KEY: 
        print("\n[轨道C] 未配置 TWTAPI_KEY，跳过 X 情报扫描。")
        return []
    
    print(f"\n[轨道C] 正在通过 TwtAPI 扫描 {len(X_VIP_ACCOUNTS)} 个 X 账号...")
    x_intel = []
    
    # 获取 24 小时前的时间戳
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    
    for username in X_VIP_ACCOUNTS:
        try:
            # 这里以常见的 TwtAPI / RapidAPI 接口为例
            # 接口文档参考：https://rapidapi.com/twtapi/api/twitter-api45
            url = "https://twitter-api45.p.rapidapi.com/user/tweets.php"
            querystring = {"screenname": username}
            headers = {
                "X-RapidAPI-Key": TWTAPI_KEY,
                "X-RapidAPI-Host": "twitter-api45.p.rapidapi.com"
            }
            
            resp = requests.get(url, headers=headers, params=querystring, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                tweets = data.get("timeline", [])[:10] # 取最近10条
                
                user_content = ""
                for t in tweets:
                    text = t.get("text", "")
                    created_at = t.get("created_at", "")
                    # 简单过滤，仅保留有实质内容的推文
                    if len(text) > 20:
                        user_content += f"- [{created_at}] {text}\n"
                
                if user_content:
                    x_intel.append({
                        "author": username,
                        "type": "X_Post",
                        "content": user_content[:5000] # 截断防爆
                    })
                    print(f"  ✅ 已捕获 @{username} 的瞬时动态")
            time.sleep(1) # 频控
        except Exception as e:
            print(f"  ❌ 抓取 @{username} 失败: {e}")
            
    return x_intel

# ════════════════════════════════════════════════════════════════════════════
# Phase 2: 大模型深度拆解 (多源情报提示词)
# ════════════════════════════════════════════════════════════════════════════
def build_llm_prompt(videos, x_data):
    video_payload = [{"author": v["author"], "title": v["title"], "text": v["transcript"][:12000]} for v in videos]
    x_payload = x_data
    
    return f"""你是顶级硅谷创投分析师。请对以下多源情报（YouTube长对谈 + X平台瞬时动态）进行深度拆解。

【YouTube视频数据】：{json.dumps(video_payload, ensure_ascii=False)}
【X平台动态数据】：{json.dumps(x_payload, ensure_ascii=False)}

【严格输出要求】：
1. 你必须且只能输出一个合法的 JSON 对象。
2. article_title：基于今日所有内容，起一个5-10字的微信爆款标题。
3. article_summary：总结本次情报中最吸引眼球的反共识认知、行业动向，不超过 30 字。
4. 视频部分 (videos)：每个视频保留英文原标题和深度中文 TL;DR。
5. X动态部分 (x_briefs)：针对 X 上的讨论，提炼出 3-5 条最有价值的行业八卦或技术趋势。

格式要求：
{{
  "article_title": "爆款标题",
  "article_summary": "30字摘要",
  "videos": [
    {{
      "title": "中文标题",
      "original_english_title": "Original Title",
      "tldr": "视频核心结论的一句话总结",
      "core_thesis": "最核心逻辑",
      "arguments": ["论点1", "论点2"],
      "counter_consensus": "反常规认知",
      "implications": "对市场的具体影响"
    }}
  ],
  "x_intel": [
    {{ "author": "账号", "insight": "一句话神评论或动态总结" }}
  ]
}}
"""

def _extract_global_json(text):
    try:
        clean_text = text.replace('```json', '').replace('```', '').strip()
        json_match = re.search(r'\{[\s\S]*\}', clean_text)
        if not json_match: return {}
        return json.loads(json_match.group(0))
    except Exception as e:
        print(f"JSON 解析失败: {e}")
        return {}

# 🚀 赛道 1：Claude 3.7
def run_claude_analysis(videos, x_data):
    if not (videos or x_data) or not OPENROUTER_API_KEY: return {}
    print("\n[大脑 A] 呼叫 Claude 3.7 策划全案...")
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}, json={"model": "anthropic/claude-3.7-sonnet", "messages": [{"role": "user", "content": build_llm_prompt(videos, x_data)}], "temperature": 0.5}, timeout=240)
        return _extract_global_json(resp.json()["choices"][0]["message"]["content"])
    except: return {}

# 🚀 赛道 2：通义千问 (国际版)
def run_qwen_analysis(videos, x_data):
    if not (videos or x_data) or not QWEN_API_KEY: return {}
    print(f"\n[大脑 B] 呼叫 千问 qwen-max 策划全案...")
    try:
        client = OpenAI(api_key=QWEN_API_KEY, base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
        resp = client.chat.completions.create(model="qwen-max", messages=[{"role": "user", "content": build_llm_prompt(videos, x_data)}], temperature=0.5)
        return _extract_global_json(resp.choices[0].message.content)
    except: return {}

# ════════════════════════════════════════════════════════════════════════════
# Phase 3: 飞书推送 (含 X 动态卡片)
# ════════════════════════════════════════════════════════════════════════════
def push_to_feishu(data):
    if not FEISHU_WEBHOOK_URL or not data: return
    date_str = datetime.datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    
    art_title = data.get("article_title", "硅谷 AI 情报")
    art_summary = data.get("article_summary", "今日硬核情报提取")

    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": "**⚠️ 每早8点准时更新 | 深度访谈 + X瞬时情报**"}, "icon": {"tag": "standard_icon", "token": "time_outlined", "color": "blue"}},
        {"tag": "note", "elements": [{"tag": "lark_md", "content": f"💡 **今日摘要**：{art_summary}"}], "background_color": "blue"},
        {"tag": "hr"}
    ]
    
    # 1. 插入 X 动态简报
    if data.get("x_intel"):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**🐦 X 瞬时情报汇总**"}})
        x_text = "\n".join([f"• **@{item['author']}**: {item['insight']}" for item in data['x_intel']])
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": x_text}})
        elements.append({"tag": "hr"})

    # 2. 插入视频深度拆解
    for i, v in enumerate(data.get("videos", []), 1):
        en_title = v.get("original_english_title", "Original Title")
        tldr = v.get("tldr", "无摘要")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🍉 {i}. {v.get('title', '深度访谈')}**"}})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"💡 {en_title}\n\n**TL;DR:** {tldr}"}})
        
        args_text = "\n".join([f"• {str(arg).strip()}" for arg in v.get('arguments', [])])
        content_md = (f"**🎯 核心主张**\n{v.get('core_thesis','')}\n\n"
                      f"**🧱 论点与证据链**\n{args_text}\n\n"
                      f"**🧠 反共识与认知盲区**\n{v.get('counter_consensus','')}\n\n"
                      f"**💼 产业与投资推演**\n{v.get('implications','')}")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content_md}})
        elements.append({"tag": "hr"})

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🌍 硅谷油管长博客拆解"},
                "subtitle": {"tag": "plain_text", "content": f"{art_title} | {date_str}"},
                "template": "purple",
                "ud_icon": {"tag": "standard_icon", "token": "video_outlined"}
            },
            "elements": elements
        }
    }
    requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=10)

# ════════════════════════════════════════════════════════════════════════════
# Phase 4: 微信推送 (含 X 动态)
# ════════════════════════════════════════════════════════════════════════════
def push_to_wechat(data):
    if not JIJIANYUN_WEBHOOK_URL or not data: return
    
    art_title = data.get("article_title", "硅谷 AI 深度追踪")
    art_summary = data.get("article_summary", "今日硬核情报提取")
    
    html_parts = [
        f'<section style="text-align: center; margin-bottom: 20px;"><img src="{TOP_IMAGE_URL}" style="max-width: 100%; border-radius: 8px;" /></section>',
        f'<section style="margin-bottom: 30px; padding: 15px; background-color: #f8f9fa; border-radius: 8px; border-left: 5px solid #2b579a;">'
        f'<p style="margin: 0; font-size: 15px; color: #333; line-height: 1.6;"><strong>⚠️ 每早 8 点｜全域硅谷情报拆解</strong><br><br>'
        f'<span style="color:#2b579a; font-weight:bold;">💡 今日摘要：</span>{art_summary}</p></section>'
    ]

    # X 动态部分
    if data.get("x_intel"):
        x_html = '<section style="margin-bottom: 30px; padding: 15px; background-color: #eef7ff; border-radius: 8px;">'
        x_html += '<p style="font-weight:bold; color:#007aff; margin-bottom:10px;">🐦 昨夜 X 平台热议：</p>'
        for item in data['x_intel']:
            x_html += f'<p style="font-size:14px; color:#333; margin-bottom:8px;">• <strong>@{item["author"]}</strong>: {item["insight"]}</p>'
        x_html += '</section>'
        html_parts.append(x_html)

    # 视频部分
    for i, v in enumerate(data.get("videos", []), 1):
        en_title = v.get("original_english_title", "Original Title")
        tldr = v.get("tldr", "No TLDR")
        args_html = "".join([f'<li style="margin-bottom: 8px;">{str(a).strip()}</li>' for a in v.get('arguments', [])])

        video_html = f"""
        <section style="margin-bottom: 40px;">
            <h2 style="font-size: 18px; color: #2b579a; margin-bottom: 15px; border-bottom: 2px solid #2b579a; padding-bottom: 5px;">🍉 {i}. {v.get('title')}</h2>
            <section style="background-color: #eef2f8; padding: 12px; border-radius: 6px; margin-bottom: 15px;">
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #333; font-weight: bold;">💡 {en_title}</p>
                <p style="margin: 0; font-size: 14px; color: #555; line-height:1.5;"><strong>TL;DR:</strong> {tldr}</p>
            </section>
            <p style="margin: 0 0 5px 0; font-size: 15px; font-weight:bold; color:#333;">🎯 核心主张：</p>
            <p style="margin: 0 0 15px 0; font-size: 14px; color:#555; line-height:1.6;">{v.get('core_thesis')}</p>
            <p style="margin: 0 0 5px 0; font-size: 15px; font-weight:bold; color:#333;">🧱 论点与证据链：</p>
            <ul style="padding-left: 20px; font-size: 14px; color:#555; line-height:1.6;">{args_html}</ul>
            <p style="margin: 0 0 5px 0; font-size: 15px; font-weight:bold; color:#333;">🧠 反共识与认知盲区：</p>
            <p style="margin: 0 0 15px 0; font-size: 14px; color:#555; line-height:1.6;">{v.get('counter_consensus')}</p>
            <p style="margin: 0 0 5px 0; font-size: 15px; font-weight:bold; color:#333;">💼 产业与投资推演：</p>
            <p style="margin: 0 0 15px 0; font-size: 14px; color:#555; line-height:1.6;">{v.get('implications')}</p>
            <hr style="border: none; border-top: 1px dashed #e1e4e8; margin-top: 25px;">
        </section>
        """
        html_parts.append(video_html)

    final_html = "".join(html_parts).replace('\n', '').replace('\r', '')
    payload = {"author": "大尉 Prinski", "cover_jpg": TOP_IMAGE_URL, "html_content": final_html, "title": art_title}
    requests.post(JIJIANYUN_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=15)

# ════════════════════════════════════════════════════════════════════════════
# 核心主循环
# ════════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("🚀 YouTube + X 深度情报系统 (V11.0 TwtAPI 集成版) 启动")
    print("=" * 60)
    track_state = load_tracking_state()
    
    # 1. 扫描 YouTube
    rss_v = scan_rss_channels(track_state)
    vip_v = scan_vip_interviews(track_state)
    all_v = rss_v + vip_v
    ready_v = fetch_transcripts(all_v)
    
    # 2. 扫描 X (TwtAPI)
    x_intel_raw = fetch_x_updates()
    
    if not ready_v and not x_intel_raw:
        print("📭 过去 24 小时没有任何新动态。")
        return
    
    # 3. 汇总并分发
    claude_data = run_claude_analysis(ready_v, x_intel_raw)
    push_to_feishu(claude_data)
    
    qwen_data = run_qwen_analysis(ready_v, x_intel_raw)
    push_to_wechat(qwen_data)
    
    save_tracking_state(track_state)
    print("\n🎉 全域情报全案分发完毕！")

if __name__ == "__main__":
    main()

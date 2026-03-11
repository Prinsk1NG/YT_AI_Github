# -*- coding: utf-8 -*-
"""
youtube_auto_task.py  v7.0 (飞书精美卡片版 + Kimi 2.5 官方SDK版)
Architecture: RSS/Search -> Jina / Firecrawl -> Claude(JSON)->Feishu Card & Kimi(JSON)->WeChat
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
from openai import OpenAI  # 🚨 引入官方库

# ── Environment variables ────────────────────────────────────────────────────
FEISHU_WEBHOOK_URL    = os.getenv("FEISHU_WEBHOOK_URL", "")
OPENROUTER_API_KEY    = os.getenv("OPENROUTER_API_KEY", "")
KIMI_API_KEY          = os.getenv("KIMI_API_KEY", "")
FIRECRAWL_API_KEY     = os.getenv("FIRECRAWL_API_KEY", "")

# 🚨 极简云与微信公众号相关配置
JIJIANYUN_WEBHOOK_URL = os.getenv("JIJIANYUN_WEBHOOK_URL", "") 
COVER_MEDIA_ID        = "这里填入你真实的微信封面图media_id"
TOP_IMAGE_URL         = "http://mmbiz.qpic.cn/sz_mmbiz_png/SfPwFYYicIliagEk8zLcesc7sBVZqibHnxN8khWb60NicWDGKiaKQum7ysAXHwXW1RF4zKLKnMrsKYBDO5U3mPIhye2r4Zzdwica9XqaMWiaW8zU7s/0?wx_fmt=png"

if FIRECRAWL_API_KEY:
    print("🚀 已挂载 Firecrawl 顶级大模型爬虫引擎...")
if JIJIANYUN_WEBHOOK_URL:
    print("🚀 已挂载 Webhook 微信自动化推送通道...")

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

VIP_LIST = [
    "Elon Musk", "Sam Altman", "Jensen Huang", "Ilya Sutskever", 
    "Dario Amodei", "Yann LeCun", "Mark Zuckerberg", "Demis Hassabis", 
    "Andrej Karpathy", "Satya Nadella", "Kevin Scott"
]

def load_tracking_state():
    path = Path("data/yt_tracking.json")
    if path.exists():
        try: return json.loads(path.read_text("utf-8"))
        except: pass
    return {"channels": {}, "vips": {}}

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
    if '万' in view_str: return int(float(re.search(r'[\d\.]+', view_str).group()) * 10000)
    num = re.search(r'\d+', view_str)
    return int(num.group()) if num else 0

# ════════════════════════════════════════════════════════════════════════════
# 搜索引擎与爬虫阶段
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
                vid = vr.get('videoId')
                title = vr.get('title', {}).get('runs', [{}])[0].get('text', '')
                channel = vr.get('ownerText', {}).get('runs', [{}])[0].get('text', '')
                pub_time = vr.get('publishedTimeText', {}).get('simpleText', '')
                view_count_str = vr.get('viewCountText', {}).get('simpleText', '0')
                duration_str = vr.get('lengthText', {}).get('simpleText', '0:00')
                results.append({
                    "id": vid, "title": title, "channel": channel,
                    "publishedTime": pub_time, "viewCount": view_count_str, "duration": duration_str
                })
                if len(results) >= limit: break
    except: pass
    return results

def scan_rss_channels(tracking_state, time_limit_hours=24):
    print(f"\n[轨道A] 正在通过 RSS 扫描核心频道...")
    now = datetime.datetime.now(timezone.utc)
    deadline = now - timedelta(hours=time_limit_hours)
    results = []
    for ch_id, info in CORE_CHANNELS.items():
        last_active = tracking_state["channels"].get(ch_id, now.isoformat())
        if (now - datetime.datetime.fromisoformat(last_active)).days > EVICTION_DAYS: continue
        try:
            feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}")
            has_new = False
            for entry in feed.entries:
                pub_time = datetime.datetime.strptime(entry.published, "%Y-%m-%dT%H:%M:%S%z")
                if pub_time > deadline:
                    has_new = True
                    results.append({
                        "video_id": entry.yt_videoid, "title": entry.title,
                        "author": info["name"], "category": info["cat"]
                    })
            if has_new: tracking_state["channels"][ch_id] = now.isoformat()
        except: pass
    return results

def scan_vip_interviews(tracking_state):
    print(f"\n[轨道B] 正在全网搜索 VIP 大佬最新访谈...")
    now = datetime.datetime.now(timezone.utc)
    results = []
    for vip in VIP_LIST:
        last_active = tracking_state["vips"].get(vip, now.isoformat())
        if (now - datetime.datetime.fromisoformat(last_active)).days > EVICTION_DAYS: continue
        try:
            search_res = native_youtube_search(f'"{vip}" (interview OR podcast)', limit=3)
            has_valid = False
            for vid in search_res:
                pub_time_str = vid.get("publishedTime", "")
                if not pub_time_str or ("year" in pub_time_str or "month" in pub_time_str): continue
                if parse_duration(vid.get("duration", "0:00")) < MIN_DURATION_SEC: continue
                if parse_views(vid.get("viewCount", "0")) < MIN_VIEWS: continue
                has_valid = True
                results.append({
                    "video_id": vid["id"], "title": vid["title"],
                    "author": vid.get("channel", "Unknown Channel"),
                    "category": f"大佬追踪: {vip}"
                })
            if has_valid: tracking_state["vips"][vip] = now.isoformat()
            time.sleep(1) 
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
        full_text = ""
        yt_url = f"https://www.youtube.com/watch?v={vid}"
        
        try:
            resp = requests.get(f"https://r.jina.ai/{yt_url}", headers={"Accept": "text/plain", "X-Return-Format": "text"}, timeout=40)
            if resp.status_code == 200 and len(resp.text) > 500:
                raw_text = resp.text
                if "Title:" in raw_text: raw_text = raw_text.split("Title:", 1)[-1]
                full_text = re.sub(r'\[.*?\]\(.*?\)', '', raw_text)
        except: pass

        if (not full_text or len(full_text) < 500) and FIRECRAWL_API_KEY:
            try:
                fc_resp = requests.post("https://api.firecrawl.dev/v1/scrape", headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}", "Content-Type": "application/json"}, json={"url": yt_url, "formats": ["markdown"], "onlyMainContent": True}, timeout=60).json()
                if fc_resp.get("success"): full_text = re.sub(r'\[.*?\]\(.*?\)', '', fc_resp.get("data", {}).get("markdown", ""))
            except: pass

        if not full_text or len(full_text) < 500:
            try:
                mirror_resp = requests.get(f"https://youtubetranscript.com/?server_vid={vid}", timeout=20)
                if "<text" in mirror_resp.text:
                    texts = re.findall(r'<text[^>]*>(.*?)</text>', mirror_resp.text, flags=re.DOTALL)
                    full_text = " ".join([html.unescape(t) for t in texts])
            except: pass

        if full_text and len(full_text) > 800:
            v["transcript"] = " ".join(full_text.split())
            valid_videos.append(v)
            print(f"  ✅ 提取成功: {v['title'][:25]}... ({len(v['transcript'])} 字)")
        else:
            print(f"  ❌ 提取失败: {v['title'][:20]}")
    return valid_videos

def _extract_json(text):
    try:
        start = text.find("@@@START@@@") + 11
        end = text.find("@@@END@@@")
        json_str = text[start:end].strip() if start > 10 and end > -1 else text.strip('` \njson')
        json_str = json_str.replace('\x00', '').replace('\x08', '')
        return json.loads(json_str).get("videos", [])
    except Exception as e:
        print(f"解析 JSON 失败: {e}")
        return []

# ════════════════════════════════════════════════════════════════════════════
# 🚀 轨道一：Claude 3.7 JSON提取 + 飞书高级卡片排版
# ════════════════════════════════════════════════════════════════════════════
def run_claude_json_analysis(videos):
    if not videos or not OPENROUTER_API_KEY: return []
    print("\n[大脑 A] 呼叫 Claude 3.7 生成结构化情报 (飞书专用)...")
    
    payload = [{"channel": v["author"], "title": v["title"], "tag": v["category"], "text": v["transcript"][:15000] + "..." if len(v["transcript"])>15000 else v["transcript"]} for v in videos]
    
    prompt = f"""你是顶级硅谷创投分析师。请对以下内容进行深度拆解。
【原始数据】：{json.dumps(payload, ensure_ascii=False)}

【处理要求】：
1. 剔除广告，保留硬核观点。
2. 直接写正文内容，禁止输出“💡【TL;DR】”等前缀。
3. 必须输出绝对合法的 JSON！如果有双引号必须转义（\\"）。

@@@START@@@
{{
  "videos": [
    {{
      "category": "原数据的 tag",
      "channel": "原频道名",
      "title": "极具深度的中文标题 (不带序号)",
      "tldr": "一句话总结核心结论",
      "core_thesis": "最核心逻辑或预测",
      "arguments": ["论点一及证据", "论点二及证据"],
      "counter_consensus": "反常规偏见",
      "implications": "对市场的具体影响"
    }}
  ]
}}
@@@END@@@
"""
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions", 
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}, 
            json={"model": "anthropic/claude-3.7-sonnet", "messages": [{"role": "user", "content": prompt}], "temperature": 0.5, "max_tokens": 10000}, 
            timeout=240
        )
        resp.raise_for_status()
        return _extract_json(resp.json()["choices"][0]["message"]["content"])
    except Exception as e:
        print(f"  ❌ Claude 解析失败: {e}")
        return []

def build_youtube_feishu_card(analyzed_videos):
    if not analyzed_videos: return None
    date_str = datetime.datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    
    # 飞书卡片头部模块
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": "**⚠️ 每日早8点准时更新 | 深度长视频拆解 | 认知升级引擎**"}, "icon": {"tag": "standard_icon", "token": "time_outlined", "color": "blue"}},
        {"tag": "hr"}
    ]
    
    # 动态组装每条新闻的排版
    for i, v in enumerate(analyzed_videos, 1):
        title = str(v.get('title', '重磅访谈')).replace('🍉', '').replace('#', '').strip()
        tldr = str(v.get('tldr', '')).strip()
        core = str(v.get('core_thesis', '')).strip()
        counter = str(v.get('counter_consensus', '')).strip()
        imp = str(v.get('implications', '')).strip()
        
        args_list = v.get('arguments', [])
        args_text = "\n".join([f"• {str(arg).strip()}" for arg in args_list])

        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🍉 {i}. {title}**"}})
        elements.append({"tag": "note", "elements": [{"tag": "lark_md", "content": f"📺 **频道/来源**：{v.get('channel', '未知频道')} | 🏷️ **标签**：{v.get('category', '科技播客')}\n💡 **【TL;DR】** {tldr}"}], "background_color": "blue"})
        
        content_md = (
            f"**🎯 核心主张**\n<font color='grey'>{core}</font>\n\n"
            f"**🧱 论点与证据链**\n<font color='grey'>{args_text}</font>\n\n"
            f"**🧠 反共识与认知盲区**\n<font color='grey'>{counter}</font>\n\n"
            f"**💼 产业与投资推演**\n<font color='grey'>{imp}</font>"
        )
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content_md}})
        elements.append({"tag": "hr"})
        
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "*🤖 本次播客深研分析由 Claude 3.7 Sonnet 强力驱动*"}})
    
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🌍 硅谷油管深极客"},
                "subtitle": {"tag": "plain_text", "content": f"长内容认知折叠 | {date_str}"},
                "template": "purple",
                "ud_icon": {"tag": "standard_icon", "token": "video_outlined"}
            },
            "elements": elements
        }
    }

def push_feishu_card(card_payload):
    if not FEISHU_WEBHOOK_URL or not card_payload: 
        print("📭 跳过飞书推送 (无URL或无内容)。")
        return
    try:
        resp = requests.post(FEISHU_WEBHOOK_URL, json=card_payload, timeout=10)
        print(f"✅ 飞书 (精美卡片版) 推送成功: {resp.status_code}")
    except Exception as e: print(f"❌ 飞书推送异常: {e}")


# ════════════════════════════════════════════════════════════════════════════
# 🚀 轨道二：Kimi 2.5 官方 SDK JSON 提取 (专供微信公众号组装)
# ════════════════════════════════════════════════════════════════════════════
def run_kimi_json_analysis(videos):
    if not videos or not KIMI_API_KEY: return []
    print(f"\n[大脑 B] 呼叫 Kimi 最新版 (kimi-latest 官方SDK) 生成严苛 JSON (微信专用)...")
    
    payload = [{"channel": v["author"], "title": v["title"], "tag": v["category"], "text": v["transcript"][:15000] + "..." if len(v["transcript"])>30000 else v["transcript"]} for v in videos]
    
    prompt = f"""你是顶级硅谷创投分析师。请对以下内容进行深度拆解。
【原始数据】：{json.dumps(payload, ensure_ascii=False)}

【处理要求】：
1. 剔除广告，保留真正有价值的内容。
2. 直接写正文内容，禁止输出“💡【TL;DR】”等前缀。
3. 必须输出绝对合法的 JSON！如果有双引号必须转义（\\"）。

@@@START@@@
{{
  "videos": [
    {{
      "category": "原数据的 tag",
      "channel": "原频道名",
      "title": "极具深度的中文标题 (不带序号)",
      "tldr": "一句话总结视频核心结论",
      "core_thesis": "最核心逻辑或预测",
      "arguments": ["论点一及证据", "论点二及证据"],
      "counter_consensus": "反常规偏见与认知",
      "implications": "对市场的具体影响"
    }}
  ]
}}
@@@END@@@
"""
    try:
        # 🚨 核心修改：使用官方 OpenAI SDK 进行标准化调用，并将模型改为 kimi-latest 解决 404 权限问题
        client = OpenAI(
            api_key=KIMI_API_KEY,
            base_url="https://api.moonshot.cn/v1"
        )
        
        response = client.chat.completions.create(
            model="kimi-latest",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )
        
        text = response.choices[0].message.content
        return _extract_json(text)
    except Exception as e:
        print(f"  ❌ Kimi JSON 解析失败: {e}")
        return []


def push_kimi_json_to_wechat(analyzed_videos):
    if not JIJIANYUN_WEBHOOK_URL or not analyzed_videos: 
        print("📭 跳过微信推送 (无URL或无Kimi数据)。")
        return

    print(f"\n[装配] 正在将 Kimi 的 JSON 组装成标准微信 HTML...")
    date_str = datetime.datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    article_title = f"硅谷吃瓜零时差 | 每日AI追踪 {date_str}"
    
    html_parts = []
    html_parts.append(f'<section style="text-align: center; margin-bottom: 20px;"><img src="{TOP_IMAGE_URL}" style="max-width: 100%; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);" /></section>')
    html_parts.append(f'<section style="margin-bottom: 30px; padding: 15px; background-color: #f8f9fa; border-radius: 8px; border-left: 5px solid #2b579a;"><p style="margin: 0; font-size: 15px; color: #333; line-height: 1.6;"><strong>⚠️ 每日早 8 点更新 | 深度长视频拆解 | Kimi结构化版</strong><br>本篇内容由 Kimi 2.5 大模型进行底层重构与逻辑拆解。</p></section>')

    for i, v in enumerate(analyzed_videos, 1):
        title = str(v.get('title', '重磅访谈')).replace('🍉', '').replace('#', '').strip()
        channel = str(v.get('channel', '未知'))
        tag = str(v.get('category', '播客'))
        tldr = str(v.get('tldr', '')).strip()
        core = str(v.get('core_thesis', '')).strip()
        counter = str(v.get('counter_consensus', '')).strip()
        imp = str(v.get('implications', '')).strip()
        
        args_html = "".join([f'<li style="margin-bottom: 8px;">{str(a).strip()}</li>' for a in v.get('arguments', [])])

        video_html = f"""
        <section style="margin-bottom: 40px;">
            <h2 style="font-size: 18px; color: #2b579a; margin-bottom: 15px; border-bottom: 2px solid #2b579a; padding-bottom: 5px;">🍉 {i}. {title}</h2>
            <section style="background-color: #eef2f8; padding: 12px; border-radius: 6px; margin-bottom: 15px;">
                <p style="margin: 0 0 8px 0; font-size: 13px; color: #666;">📺 频道：{channel} &nbsp;|&nbsp; 🏷️ 标签：{tag}</p>
                <p style="margin: 0; font-size: 15px; color: #333; font-weight: bold;">💡 TL;DR: <span style="font-weight: normal;">{tldr}</span></p>
            </section>
            <p style="margin: 0 0 8px 0; font-size: 15px; color: #333;"><strong>🎯 核心主张：</strong></p>
            <p style="margin: 0 0 15px 0; font-size: 15px; color: #555; line-height: 1.6;">{core}</p>
            <p style="margin: 0 0 8px 0; font-size: 15px; color: #333;"><strong>🧱 论点与证据链：</strong></p>
            <ul style="margin: 0 0 15px 0; padding-left: 20px; font-size: 15px; color: #555; line-height: 1.6;">{args_html}</ul>
            <p style="margin: 0 0 8px 0; font-size: 15px; color: #333;"><strong>🧠 反共识与认知盲区：</strong></p>
            <p style="margin: 0 0 15px 0; font-size: 15px; color: #555; line-height: 1.6;">{counter}</p>
            <p style="margin: 0 0 8px 0; font-size: 15px; color: #333;"><strong>💼 产业与投资推演：</strong></p>
            <p style="margin: 0 0 15px 0; font-size: 15px; color: #555; line-height: 1.6;">{imp}</p>
            <hr style="border: none; border-top: 1px dashed #e1e4e8; margin-top: 25px;">
        </section>
        """
        html_parts.append(video_html)

    html_parts.append('<section style="text-align: center; margin-top: 30px;"><p style="font-size: 12px; color: #999;">* 本文由 Kimi 大模型全自动逻辑重组生成 *</p></section>')
    final_html = "".join(html_parts).replace('\n', '').replace('\r', '')

    payload = {
        "author": "硅谷吃瓜",        
        "cover_jpg": TOP_IMAGE_URL,   
        "html_content": final_html,   
        "title": article_title        
    }

    try:
        resp = requests.post(JIJIANYUN_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        print(f"✅ 微信 (Kimi版) 推送成功: {resp.status_code}")
    except Exception as e:
        print(f"❌ 微信推送失败: {e}")

# ════════════════════════════════════════════════════════════════════════════
# 核心主循环
# ════════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("🚀 YouTube 播客深度深研系统 (V7.0 精美卡片+官方SDK版) 启动")
    print("=" * 60)
    
    track_state = load_tracking_state()
    rss_videos = scan_rss_channels(track_state)
    vip_videos = scan_vip_interviews(track_state)
    all_videos = rss_videos + vip_videos
    
    if not all_videos:
        print("📭 过去24小时无新视频。")
        return
        
    ready_videos = fetch_transcripts(all_videos)
    
    # 🚀 赛道 1：Claude 提取 JSON -> 组装成精美的飞书 Message Card
    claude_data = run_claude_json_analysis(ready_videos)
    feishu_card = build_youtube_feishu_card(claude_data)
    push_feishu_card(feishu_card)
    
    # 🚀 赛道 2：Kimi (官方 openai SDK 方案) 提取 JSON -> 组装成微信 HTML
    kimi_data = run_kimi_json_analysis(ready_videos)
    push_kimi_json_to_wechat(kimi_data)
    
    save_tracking_state(track_state)
    print("\n🎉 双轨结构化流执行完毕！")

if __name__ == "__main__":
    main()

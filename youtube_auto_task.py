# -*- coding: utf-8 -*-
"""
youtube_auto_task.py  v5.0 (双端引擎：飞书推送 + 极简云微信排版直通版)
Architecture: RSS + Native Search -> Jina / Firecrawl / Mirror -> Claude 3.7 -> Feishu & JiJianYun
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

# ── Environment variables ────────────────────────────────────────────────────
FEISHU_WEBHOOK_URL    = os.getenv("FEISHU_WEBHOOK_URL", "")
OPENROUTER_API_KEY    = os.getenv("OPENROUTER_API_KEY", "")
KIMI_API_KEY          = os.getenv("KIMI_API_KEY", "")
FIRECRAWL_API_KEY     = os.getenv("FIRECRAWL_API_KEY", "")

# 🚨 极简云与微信公众号相关配置
JIJIANYUN_WEBHOOK_URL = os.getenv("JIJIANYUN_WEBHOOK_URL", "") # 请在 GitHub Secrets 添加此变量
COVER_MEDIA_ID        = "这里填入你真实的微信封面图media_id"
TOP_IMAGE_URL         = "http://mmbiz.qpic.cn/sz_mmbiz_png/SfPwFYYicIliagEk8zLcesc7sBVZqibHnxN8khWb60NicWDGKiaKQum7ysAXHwXW1RF4zKLKnMrsKYBDO5U3mPIhye2r4Zzdwica9XqaMWiaW8zU7s/0?wx_fmt=png"

if FIRECRAWL_API_KEY:
    print("🚀 已挂载 Firecrawl 顶级大模型爬虫引擎作为重装 Plan B...")
if JIJIANYUN_WEBHOOK_URL:
    print("🚀 已挂载 极简云 Webhook 微信自动化推送通道...")

# ── Tracking & Thresholds ────────────────────────────────────────────────────
MIN_DURATION_SEC = 15 * 60   # 最短 15 分钟
MIN_VIEWS        = 5000      # 搜索轨最低播放量
EVICTION_DAYS    = 30        # 30天无动态自动淘汰

# ── 50 大核心频道 ─────────────────────────────────────────
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
    "Andrej Karpathy", "Satya Nadella", "Kevin Scott",
    "王小川 AI", "杨植麟", "朱啸虎 AI", "陆奇", "李彦宏", "傅盛"
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
    for part in parts:
        secs = secs * 60 + int(part.replace(',', '').strip())
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
# 搜索引擎：使用公共代理绕过限制
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
    print(f"\n[轨道A] 正在通过 RSS 扫描 {len(CORE_CHANNELS)} 个核心频道...")
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
                        "author": info["name"], "category": info["cat"],
                        "pub_time": pub_time.strftime("%Y-%m-%d"), "source": "RSS"
                    })
            if has_new: tracking_state["channels"][ch_id] = now.isoformat()
        except: pass
    print(f"[轨道A] 扫描完成，发现 {len(results)} 个最新视频。")
    return results

def scan_vip_interviews(tracking_state):
    print(f"\n[轨道B] 正在全网搜索 {len(VIP_LIST)} 位 VIP 大佬的最新访谈...")
    now = datetime.datetime.now(timezone.utc)
    results = []
    
    for vip in VIP_LIST:
        last_active = tracking_state["vips"].get(vip, now.isoformat())
        if (now - datetime.datetime.fromisoformat(last_active)).days > EVICTION_DAYS: continue

        try:
            search_res = native_youtube_search(f'"{vip}" (interview OR podcast OR 访谈)', limit=5)
            has_valid = False
            for vid in search_res:
                pub_time_str = vid.get("publishedTime", "")
                if not pub_time_str or ("year" in pub_time_str or "month" in pub_time_str or "week" in pub_time_str): continue
                if parse_duration(vid.get("duration", "0:00")) < MIN_DURATION_SEC: continue
                if parse_views(vid.get("viewCount", "0")) < MIN_VIEWS: continue
                
                has_valid = True
                results.append({
                    "video_id": vid["id"], "title": vid["title"],
                    "author": vid.get("channel", "Unknown Channel"),
                    "category": f"大佬追踪: {vip}", "pub_time": "Today", "source": "Search"
                })
            if has_valid: tracking_state["vips"][vip] = now.isoformat()
            time.sleep(1) 
        except: pass
    print(f"[轨道B] 扫描完成，捕获 {len(results)} 个 VIP 高质长视频。")
    return results

# ════════════════════════════════════════════════════════════════════════════
# Phase 2: 纯净三层瀑布流引擎 (Plan A: Jina -> Plan B: Firecrawl -> Plan C)
# ════════════════════════════════════════════════════════════════════════════
def fetch_transcripts(video_list):
    print("\n[提取] 启动纯净解析引擎 (Jina 主攻 -> Firecrawl 护航)...")

    valid_videos = []
    seen_ids = set()
    
    for v in video_list:
        vid = v["video_id"]
        if vid in seen_ids: continue
        seen_ids.add(vid)
        full_text = ""
        yt_url = f"https://www.youtube.com/watch?v={vid}"
        
        # 🟢 Plan A: Jina AI Reader API
        try:
            print(f"  ➡️ [Plan A] 呼叫 Jina 提取: {vid}")
            jina_url = f"https://r.jina.ai/{yt_url}"
            headers = {"Accept": "text/plain", "X-Return-Format": "text"}
            resp = requests.get(jina_url, headers=headers, timeout=40)
            
            if resp.status_code == 200 and len(resp.text) > 500:
                raw_text = resp.text
                if "Title:" in raw_text: raw_text = raw_text.split("Title:", 1)[-1]
                full_text = re.sub(r'\[.*?\]\(.*?\)', '', raw_text)
                full_text = " ".join(full_text.split())
        except Exception as e:
            print(f"  [Debug] Jina 引擎未命中: {e}")

        # 🟡 Plan B: Firecrawl API
        if (not full_text or len(full_text) < 500) and FIRECRAWL_API_KEY:
            try:
                print(f"  ➡️ [Plan B] Jina 失效，启动 Firecrawl 强力解析...")
                fc_url = "https://api.firecrawl.dev/v1/scrape"
                headers = {
                    "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "url": yt_url,
                    "formats": ["markdown"],
                    "onlyMainContent": True
                }
                fc_resp = requests.post(fc_url, headers=headers, json=payload, timeout=60)
                fc_data = fc_resp.json()
                
                if fc_data.get("success"):
                    raw_text = fc_data.get("data", {}).get("markdown", "")
                    full_text = re.sub(r'\[.*?\]\(.*?\)', '', raw_text)
                    full_text = " ".join(full_text.split())
                else:
                    print(f"  [Debug] Firecrawl 被拦截: {fc_data.get('error')}")
            except Exception as e:
                print(f"  [Debug] Firecrawl 引擎请求失败: {e}")

        # 🟠 Plan C: 第三方公共镜像站
        if not full_text or len(full_text) < 500:
            try:
                print(f"  ➡️ [Plan C] 切换公共镜像站...")
                yt_mirror = f"https://youtubetranscript.com/?server_vid={vid}"
                mirror_resp = requests.get(yt_mirror, timeout=20)
                if "<text" in mirror_resp.text:
                    texts = re.findall(r'<text[^>]*>(.*?)</text>', mirror_resp.text, flags=re.DOTALL)
                    full_text = " ".join([html.unescape(t) for t in texts])
            except Exception as e:
                print(f"  [Debug] 公共镜像站抓取失败")
                

        # --- 最终验证 ---
        if full_text and len(full_text) > 800:
            v["transcript"] = full_text
            valid_videos.append(v)
            print(f"  ✅ [{v['author'][:10]}] {v['title'][:25]}... ({len(full_text)} 字)")
        else:
            print(f"  ❌ 跳过 [{v['title'][:20]}]: 三层提取全部失败，此视频极可能无字幕")
            
    return valid_videos

# ════════════════════════════════════════════════════════════════════════════
# Phase 3: LLM 深度提炼与推送
# ════════════════════════════════════════════════════════════════════════════
def llm_deep_analysis(videos):
    if not videos: return []
    print(f"\n[大脑] 提交 {len(videos)} 个长视频给 LLM 进行金字塔分析...")
    
    payload = [{"channel": v["author"], "title": v["title"], "tag": v["category"], "text": v["transcript"][:15000] + "\n...\n" + v["transcript"][-15000:] if len(v["transcript"])>30000 else v["transcript"]} for v in videos]
    
    prompt = f"""你是顶级硅谷创投分析师。以下是过去24小时内YouTube高价值AI对谈/讲座的全量字幕。
请使用「金字塔原理」对这些长内容进行降维打击级的深度拆解。

【原始数据】：
{json.dumps(payload, ensure_ascii=False)}

【处理要求及防错机制】：
1. 剔除广告、口水话，只保留对商业、技术、创投有真正启发的视频。
2. 严格按以下 JSON 格式输出结果（严禁输出额外文字）。
3. 🚨 极其重要：在填写内容时，【直接写正文】，绝对禁止自己输出“💡【TL;DR】”等前缀。
4. 🚨 致命格式警告：你输出的必须是绝对合法的 JSON！所有的字符串内部如果出现双引号，必须使用反斜杠转义（\\"）。严禁在数组或对象末尾留下多余的逗号。

@@@START@@@
{{
  "videos": [
    {{
      "category": "原数据的 tag",
      "channel": "原频道名",
      "title": "极具深度的中文标题 (注意：直接写标题，不要带数字序号)",
      "tldr": "一句话总结视频核心结论",
      "core_thesis": "受访者提出的最核心逻辑或预测",
      "arguments": [
        "[时间锚点] 论点一及证据",
        "[时间锚点] 论点二及证据"
      ],
      "counter_consensus": "视频中打破了哪些大众常规偏见？",
      "implications": "对当前市场的具体影响"
    }}
  ]
}}
@@@END@@@
"""
    
    analyzed_data = []
    
    # 🟢 第一顺位：尝试使用 Claude 3.7
    if OPENROUTER_API_KEY:
        try:
            print("  ➡️ 调用 Claude 3.7 Sonnet 中...")
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}, json={"model": "anthropic/claude-3.7-sonnet", "messages": [{"role": "user", "content": prompt}], "temperature": 0.5, "max_tokens": 10000}, timeout=240)
            resp.raise_for_status()
            analyzed_data = _extract_json(resp.json()["choices"][0]["message"]["content"])
        except Exception as e: 
            print(f"  ❌ Claude 请求异常: {e}")

    # 🚨 终极自愈机制：如果 Claude 失败或 JSON 解析崩溃导致数据为空，立刻触发 Kimi 兜底！
    if not analyzed_data and KIMI_API_KEY:
        try:
            print("  ➡️ Claude 解析失败，触发 Kimi 32k 兜底重试中...")
            resp = requests.post("https://api.moonshot.cn/v1/chat/completions", headers={"Authorization": f"Bearer {KIMI_API_KEY}", "Content-Type": "application/json"}, json={"model": "moonshot-v1-32k", "messages": [{"role": "user", "content": prompt}], "temperature": 0.5}, timeout=240)
            resp.raise_for_status()
            analyzed_data = _extract_json(resp.json()["choices"][0]["message"]["content"])
        except Exception as e: 
            print(f"  ❌ Kimi 兜底失败: {e}")
            
    return analyzed_data

def _extract_json(text):
    try:
        start = text.find("@@@START@@@") + 11
        end = text.find("@@@END@@@")
        json_str = text[start:end].strip() if start > 10 and end > -1 else text.strip('` \njson')
        
        # 暴力清洗大模型偶尔生成的非法控制字符
        json_str = json_str.replace('\x00', '').replace('\x08', '').replace('\x0b', '').replace('\x0c', '').replace('\x0e', '')
        
        return json.loads(json_str).get("videos", [])
    except Exception as e:
        print(f"解析 JSON 失败: {e}")
        return []

# ════════════════════════════════════════════════════════════════════════════
# Phase 4A: Feishu 研报级卡片排版渲染
# ════════════════════════════════════════════════════════════════════════════
def build_youtube_feishu_card(analyzed_videos):
    if not analyzed_videos: return None
    date_str = datetime.datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "**⚠️ 每日早8点准时更新 | 深度长视频拆解 | 认知升级引擎**"}, "icon": {"tag": "standard_icon", "token": "time_outlined", "color": "blue"}}, {"tag": "hr"}]
    
    for i, v in enumerate(analyzed_videos, 1):
        title = str(v.get('title', '重磅访谈提取')).replace('🍉', '').replace('#', '').strip()
        tldr = str(v.get('tldr', '暂无摘要')).replace('💡【TL;DR】', '').replace('【TL;DR】', '').replace('💡', '').strip()
        core = str(v.get('core_thesis', '')).replace('🎯 核心主张：', '').replace('🎯 核心主张', '').replace('🎯', '').strip()
        counter = str(v.get('counter_consensus', '')).replace('🧠 反共识认知：', '').replace('🧠 反共识认知', '').replace('🧠', '').strip()
        imp = str(v.get('implications', '')).replace('💼 产业与投资推演：', '').replace('💼 产业与投资推演', '').replace('💼', '').strip()
        
        args_list = v.get('arguments', [])
        args_text = "\n".join([f"• {str(arg).replace('•', '').strip()}" for arg in args_list])

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
        
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "*📅 本内容基于 AI 对长视频自动提取与分析，保留时间轴结构，方便定位原声核实。*"}})
    
    return {"msg_type": "interactive", "card": {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": "🌍 硅谷油管深极客"}, "subtitle": {"tag": "plain_text", "content": f"长内容认知折叠 | {date_str}"}, "template": "purple", "ud_icon": {"tag": "standard_icon", "token": "video_outlined"}}, "elements": elements}}

def push_to_feishu(card_payload):
    if not FEISHU_WEBHOOK_URL: 
        print("📭 未配置飞书 Webhook，跳过飞书推送。")
        return
    if not card_payload:
        print("⚠️ 飞书推送被跳过：无有效卡片数据。")
        return
    try:
        resp = requests.post(FEISHU_WEBHOOK_URL, json=card_payload, timeout=10)
        print(f"✅ 飞书推送成功: {resp.status_code}")
    except Exception as e: print(f"❌ 飞书推送异常: {e}")

# ════════════════════════════════════════════════════════════════════════════
# Phase 4B: 极简云/腾讯轻联 Webhook 微信公众号直通排版引擎
# ════════════════════════════════════════════════════════════════════════════
def push_to_jijianyun_webhook(analyzed_videos):
    # 🚨 精准区分错误原因
    if not JIJIANYUN_WEBHOOK_URL: 
        print("📭 未配置 Webhook 链接，跳过微信排版与推送。")
        return
    if not analyzed_videos:
        print("⚠️ 微信推送被跳过：大模型未能生成有效数据。")
        return

    print(f"\n[推送] 正在生成标准微信 HTML 并推送到 Webhook...")
    
    # 导语框
    html_parts.append(f'<section style="margin-bottom: 30px; padding: 15px; background-color: #f8f9fa; border-radius: 8px; border-left: 5px solid #2b579a;"><p style="margin: 0; font-size: 15px; color: #333; line-height: 1.6;"><strong>⚠️ 每日早 8 点更新 | 深度长视频拆解 | 认知升级引擎</strong><br>以下是过去 24 小时内硅谷最具价值的硬核技术对谈深度提炼。</p></section>')

    # 循环遍历组装每个话题的 HTML 卡片
    for i, v in enumerate(analyzed_videos, 1):
        title = str(v.get('title', '重磅访谈提取')).replace('🍉', '').replace('#', '').strip()
        channel = str(v.get('channel', '未知频道'))
        tag = str(v.get('category', '科技播客'))
        tldr = str(v.get('tldr', '暂无摘要')).replace('💡【TL;DR】', '').replace('【TL;DR】', '').replace('💡', '').strip()
        core = str(v.get('core_thesis', '')).replace('🎯 核心主张：', '').replace('🎯 核心主张', '').replace('🎯', '').strip()
        counter = str(v.get('counter_consensus', '')).replace('🧠 反共识认知：', '').replace('🧠 反共识认知', '').replace('🧠', '').strip()
        imp = str(v.get('implications', '')).replace('💼 产业与投资推演：', '').replace('💼 产业与投资推演', '').replace('💼', '').strip()
        
        args_html = ""
        for arg in v.get('arguments', []):
            clean_arg = str(arg).replace("•", "").strip()
            args_html += f'<li style="margin-bottom: 8px;">{clean_arg}</li>'

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
            <ul style="margin: 0 0 15px 0; padding-left: 20px; font-size: 15px; color: #555; line-height: 1.6;">
                {args_html}
            </ul>
            
            <p style="margin: 0 0 8px 0; font-size: 15px; color: #333;"><strong>🧠 反共识与认知盲区：</strong></p>
            <p style="margin: 0 0 15px 0; font-size: 15px; color: #555; line-height: 1.6;">{counter}</p>
            
            <p style="margin: 0 0 8px 0; font-size: 15px; color: #333;"><strong>💼 产业与投资推演：</strong></p>
            <p style="margin: 0 0 15px 0; font-size: 15px; color: #555; line-height: 1.6;">{imp}</p>
            
            <hr style="border: none; border-top: 1px dashed #e1e4e8; margin-top: 25px;">
        </section>
        """
        html_parts.append(video_html)

    html_parts.append('<section style="text-align: center; margin-top: 30px;"><p style="font-size: 12px; color: #999;">* 本文由 AI 追踪 YouTube 创投频道全自动排版生成 *</p></section>')

    # 🚨 终极防爆处理：将换行符全部抹平
    final_html = "".join(html_parts).replace('\n', '').replace('\r', '')

    # 🚨 严格按照极简云参数文档匹配 Key
    payload = {
        "author": "硅谷吃瓜",        # 对应参数：内容作者
        "cover_jpg": TOP_IMAGE_URL,   # 对应参数：内容的JPG格式封面图
        "html_content": final_html,   # 对应参数：内容的HTML格式内容
        "title": article_title        # 对应参数：内容标题
    }

    try:
        resp = requests.post(JIJIANYUN_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        print(f"✅ 成功投递至极简云 Webhook, 状态码: {resp.status_code}")
    except Exception as e:
        print(f"❌ 发送至极简云失败: {e}")

# ════════════════════════════════════════════════════════════════════════════
# 核心主循环
# ════════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("🚀 YouTube 播客深度深研系统 (V5.0 双端引擎版) 启动")
    print("=" * 60)
    
    track_state = load_tracking_state()
    rss_videos = scan_rss_channels(track_state)
    vip_videos = scan_vip_interviews(track_state)
    all_videos = rss_videos + vip_videos
    
    if not all_videos:
        print("📭 过去24小时没有找到任何符合标准的硬核视频。")
        save_tracking_state(track_state)
        return
        
    ready_videos = fetch_transcripts(all_videos)
    analyzed_data = llm_deep_analysis(ready_videos)
    
    # 1. 飞书卡片推送通道
    card = build_youtube_feishu_card(analyzed_data)
    push_to_feishu(card)
    
    # 2. 极简云 Webhook 微信直通通道
    push_to_jijianyun_webhook(analyzed_data)
    
    save_tracking_state(track_state)
    print("\n🎉 YouTube 深度情报提取与双端分发流执行完毕！")

if __name__ == "__main__":
    main()

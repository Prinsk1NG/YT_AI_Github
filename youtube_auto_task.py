# -*- coding: utf-8 -*-
"""
youtube_auto_task.py  v1.7 (钛合金装甲版：三引擎并联 + 极客 Debug)
Architecture: RSS + Native Search -> API/yt-dlp/Scrapeless -> Claude 3.7 -> Feishu
"""

import os
import re
import json
import time
import sys
import datetime
from datetime import timezone, timedelta
from pathlib import Path
import html

import requests
import feedparser

# ── Environment variables ────────────────────────────────────────────────────
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
KIMI_API_KEY       = os.getenv("KIMI_API_KEY", "")

# 🌐 新增：Scrapeless API Token 及 代理配置
SCRAPELESS_API_KEY = os.getenv("SCRAPELESS_API_KEY", "")
YT_PROXY = os.getenv("YT_PROXY", "")

if YT_PROXY:
    os.environ["http_proxy"]  = YT_PROXY
    os.environ["https_proxy"] = YT_PROXY
    print("🛡️ 已挂载全局网络代理...")

if SCRAPELESS_API_KEY:
    print("🚀 已挂载 Scrapeless Web Unlocker 引擎，准备强力穿透防线...")

# ── Tracking & Thresholds ────────────────────────────────────────────────────
MIN_DURATION_SEC = 15 * 60   # 最短 15 分钟（过滤切片）
MIN_VIEWS        = 5000      # 搜索轨最低播放量
EVICTION_DAYS    = 30        # 30天淘汰机制

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

# ── 30 大流动超级节点 ───────────────────────────────────────
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
# 🚀 独家黑科技：Scrapeless 防爆网页请求器
# ════════════════════════════════════════════════════════════════════════════
def fetch_html_anti_bot(url):
    if SCRAPELESS_API_KEY:
        try:
            api_url = "https://api.scrapeless.com/api/v1/scraper/request"
            payload = {"actor": "scraper.webunlocker", "input": {"url": url}}
            headers = {"x-api-token": SCRAPELESS_API_KEY, "Content-Type": "application/json"}
            resp = requests.post(api_url, json=payload, headers=headers, proxies={"http": None, "https": None}, timeout=45)
            try:
                json_res = resp.json()
                if "data" in json_res and "body" in json_res["data"]: return json_res["data"]["body"]
                if "data" in json_res and "html" in json_res["data"]: return json_res["data"]["html"]
                if "html" in json_res: return json_res["html"]
            except: pass
            return resp.text
        except Exception as e:
            print(f"  ⚠️ Scrapeless 解锁失败，尝试回退直连: {e}")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept-Language": "en-US,en;q=0.9"}
    return requests.get(url, headers=headers, timeout=15).text

# ════════════════════════════════════════════════════════════════════════════
# Phase 1.5: 搜索解析引擎 (已接入 Scrapeless)
# ════════════════════════════════════════════════════════════════════════════
def native_youtube_search(query, limit=5):
    url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}&sp=CAI%253D"
    results = []
    try:
        html_text = fetch_html_anti_bot(url)
        match = re.search(r'var ytInitialData = (\{.*?\});</script>', html_text)
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
    except Exception as e:
        print(f"  ⚠️ 原生解析异常: {e}")
    return results

# ════════════════════════════════════════════════════════════════════════════
# Phase 1: 轨道 A - RSS 订阅
# ════════════════════════════════════════════════════════════════════════════
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
# Phase 2: 钛合金装甲字幕引擎 (三引擎无缝接力)
# ════════════════════════════════════════════════════════════════════════════
def fetch_transcripts(video_list):
    print("\n[提取] 正在挂载装甲字幕引擎 (开启三引擎协同防爆机制)...")
    
    # 确保依赖存在
    os.system(f"{sys.executable} -m pip install -q yt-dlp youtube-transcript-api")
    import subprocess
    from youtube_transcript_api import YouTubeTranscriptApi

    valid_videos = []
    seen_ids = set()
    
    for v in video_list:
        vid = v["video_id"]
        if vid in seen_ids: continue
        seen_ids.add(vid)
        full_text = ""
        
        # 🟢 引擎 1: 高速 API 抓取 (带代理穿透)
        try:
            proxies = {"http": YT_PROXY, "https": YT_PROXY} if YT_PROXY else None
            t_list = YouTubeTranscriptApi.get_transcript(vid, languages=['zh-Hans', 'zh-Hant', 'zh', 'en', 'en-US'], proxies=proxies)
            full_text = " ".join([x['text'] for x in t_list])
        except Exception as e1:
            try:
                # 兜底：不限制语言强抓
                t_list = YouTubeTranscriptApi.get_transcript(vid, proxies=proxies)
                full_text = " ".join([x['text'] for x in t_list])
            except Exception as e1_2:
                print(f"  [Debug] 引擎1(API) 抓取失败: {str(e1_2).split(chr(10))[0][:60]}")

        # 🟡 引擎 2: yt-dlp 强穿透解析
        if not full_text or len(full_text) < 500:
            try:
                # 使用 sys.executable 绝对路径，彻底解决 GitHub Actions 中找不到命令的问题
                cmd = [sys.executable, "-m", "yt_dlp", "--dump-json", "--skip-download"]
                if YT_PROXY: cmd.extend(["--proxy", YT_PROXY])
                cmd.append(f"https://www.youtube.com/watch?v={vid}")
                
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
                if res.returncode == 0:
                    info = json.loads(res.stdout)
                    subs = info.get('subtitles', {})
                    auto_subs = info.get('automatic_captions', {})
                    
                    chosen_track = None
                    for lang in ['zh-Hans', 'zh-Hant', 'zh', 'en', 'en-US']:
                        if lang in subs: chosen_track = subs[lang]; break
                        if lang in auto_subs: chosen_track = auto_subs[lang]; break
                    if not chosen_track:
                        if subs: chosen_track = list(subs.values())[0]
                        elif auto_subs: chosen_track = list(auto_subs.values())[0]
                        
                    if chosen_track:
                        target_url = next((f['url'] for f in chosen_track if f.get('ext') == 'json3'), chosen_track[0]['url'])
                        t_resp = requests.get(target_url, timeout=15, proxies={"http": YT_PROXY, "https": YT_PROXY} if YT_PROXY else None)
                        try:
                            t_data = t_resp.json()
                            full_text = " ".join([seg.get('utf8', '') for e in t_data.get('events', []) for seg in e.get('segs', [])])
                        except:
                            # VTT 强洗
                            raw = re.sub(r'<[^>]+>', ' ', t_resp.text)
                            full_text = html.unescape(re.sub(r'[\d:\.\->]+', ' ', raw))
                else:
                    print(f"  [Debug] 引擎2(yt-dlp) 执行错误: {res.stderr.strip()[:60]}")
            except Exception as e2: 
                print(f"  [Debug] 引擎2(yt-dlp) 崩溃: {e2}")

        # 🔴 引擎 3: Scrapeless 强解底包 (最强备胎)
        if (not full_text or len(full_text) < 500) and SCRAPELESS_API_KEY:
            try:
                html_text = fetch_html_anti_bot(f"https://www.youtube.com/watch?v={vid}")
                # 不用脆弱的正测猜链接，直接生抠官方核心 JSON
                match = re.search(r'ytInitialPlayerResponse\s*=\s*(\{.+?\});', html_text)
                if match:
                    data = json.loads(match.group(1))
                    tracks = data.get('captions', {}).get('playerCaptionsTracklistRenderer', {}).get('captionTracks', [])
                    if tracks:
                        target_url = tracks[0]['baseUrl']
                        for t in tracks:
                            if 'zh' in t.get('languageCode', '') or 'en' in t.get('languageCode', ''):
                                target_url = t['baseUrl']; break
                        t_resp = requests.get(target_url, proxies={"http": None, "https": None}, timeout=15)
                        texts = re.findall(r'<text[^>]*>(.*?)</text>', t_resp.text, flags=re.DOTALL)
                        full_text = re.sub(r'<[^>]+>', '', " ".join([html.unescape(t) for t in texts]))
                else:
                    print("  [Debug] 引擎3(Scrapeless) 未能在此视频页找到 JSON 结构")
            except Exception as e3: 
                print(f"  [Debug] 引擎3(Scrapeless) 解析失败: {e3}")

        # --- 最终验证与录入 ---
        full_text = " ".join(full_text.split())
        if len(full_text) > 800:
            v["transcript"] = full_text
            valid_videos.append(v)
            print(f"  ✅ [{v['author'][:10]}] {v['title'][:25]}... ({len(full_text)} 字)")
        else:
            # 极少部分视频是真的没有字幕！这里打出结论
            print(f"  ❌ 跳过 [{v['title'][:20]}]: 三大引擎均无法获取内容 (大概率原视频未开启字幕)")
            
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

【处理要求】：
1. 剔除广告、口水话，只保留对商业、技术、创投有真正启发的视频。
2. 严格按以下 JSON 格式输出结果（严禁输出额外文字）：

@@@START@@@
{{
  "videos": [
    {{
      "category": "原数据的 tag",
      "channel": "原频道名",
      "title": "重新拟定一个极具深度的中文标题 (如：Dwarkesh对话Dario：Scaling Law的终局)",
      "tldr": "💡【TL;DR】一句话总结视频的核心结论 (50字内)",
      "core_thesis": "🎯 核心主张：受访者提出的最核心逻辑或预测",
      "arguments": [
        "• [时间锚点] 论点一及证据 (例如：[前期] 算力集群规模将决定生死)",
        "• [时间锚点] 论点二及证据"
      ],
      "counter_consensus": "🧠 反共识认知：打破了哪些大众常规偏见？",
      "implications": "💼 产业与投资推演：对当前市场的具体影响"
    }}
  ]
}}
@@@END@@@
"""
    proxies_bypass = {"http": None, "https": None}
    if OPENROUTER_API_KEY:
        try:
            print("  ➡️ 调用 Claude 3.7 Sonnet 中...")
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}, json={"model": "anthropic/claude-3.7-sonnet", "messages": [{"role": "user", "content": prompt}], "temperature": 0.5, "max_tokens": 10000}, proxies=proxies_bypass, timeout=240)
            resp.raise_for_status()
            return _extract_json(resp.json()["choices"][0]["message"]["content"])
        except Exception as e: print(f"  ❌ Claude 失败: {e}")

    if KIMI_API_KEY:
        try:
            print("  ➡️ 调用 Kimi 32k 兜底中...")
            resp = requests.post("https://api.moonshot.cn/v1/chat/completions", headers={"Authorization": f"Bearer {KIMI_API_KEY}", "Content-Type": "application/json"}, json={"model": "moonshot-v1-32k", "messages": [{"role": "user", "content": prompt}], "temperature": 0.5}, proxies=proxies_bypass, timeout=240)
            resp.raise_for_status()
            return _extract_json(resp.json()["choices"][0]["message"]["content"])
        except Exception as e: print(f"  ❌ Kimi 兜底失败: {e}")
    return []

def _extract_json(text):
    try:
        start = text.find("@@@START@@@") + 11
        end = text.find("@@@END@@@")
        json_str = text[start:end].strip() if start > 10 and end > -1 else text.strip('` \njson')
        return json.loads(json_str).get("videos", [])
    except Exception as e:
        print(f"解析 JSON 失败: {e}")
        return []

def build_youtube_feishu_card(analyzed_videos):
    if not analyzed_videos: return None
    date_str = datetime.datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "**⚠️ 每日早8点准时更新 | 深度长视频拆解 | 认知升级引擎**"}, "icon": {"tag": "standard_icon", "token": "time_outlined", "color": "blue"}}, {"tag": "hr"}]
    for i, v in enumerate(analyzed_videos, 1):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"## 🍉 {i}. {v.get('title', '重磅访谈提取')}"}})
        elements.append({"tag": "note", "elements": [{"tag": "lark_md", "content": f"📺 **频道/来源**：{v.get('channel', '未知频道')} | 🏷️ **标签**：{v.get('category', '科技播客')}\n{v.get('tldr', '💡【TL;DR】暂无摘要')}"}], "background_color": "blue"})
        args_text = "\n".join(v.get('arguments', []))
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🎯 核心主张**\n<font color='grey'>{v.get('core_thesis', '')}</font>\n\n**🧱 论点与证据链**\n<font color='grey'>{args_text}</font>\n\n**🧠 反共识与认知盲区**\n<font color='grey'>{v.get('counter_consensus', '')}</font>\n\n**💼 产业与投资推演**\n<font color='grey'>{v.get('implications', '')}</font>"}})
        elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "*📅 本内容基于 AI 对长视频自动提取与分析，保留时间轴结构，方便定位原声核实。*"}})
    return {"msg_type": "interactive", "card": {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": "🌍 硅谷油管深极客"}, "subtitle": {"tag": "plain_text", "content": f"长内容认知折叠 | {date_str}"}, "template": "purple", "ud_icon": {"tag": "standard_icon", "token": "video_outlined"}}, "elements": elements}}

def push_to_feishu(card_payload):
    if not FEISHU_WEBHOOK_URL or not card_payload: return
    try:
        resp = requests.post(FEISHU_WEBHOOK_URL, json=card_payload, proxies={"http": None, "https": None}, timeout=10)
        print(f"✅ 飞书推送成功: {resp.status_code}")
    except Exception as e: print(f"❌ 飞书推送异常: {e}")

def main():
    print("=" * 60)
    print("🚀 YouTube 播客深度深研系统 (V1.7 钛合金装甲版) 启动")
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
    card = build_youtube_feishu_card(analyzed_data)
    push_to_feishu(card)
    save_tracking_state(track_state)
    print("\n🎉 YouTube 深度情报提取流执行完毕！")

if __name__ == "__main__":
    main()

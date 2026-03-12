# -*- coding: utf-8 -*-
"""
youtube_auto_task.py  v26.0 (Base64 URL终极防腐版 + 强力爆款标题 + 飞书排版对位)
Architecture: Search -> LLM Filter -> Top 5 Synthesis -> AI Cover Gen -> ImgBB -> Distribution
"""

import os
import re
import json
import time
import datetime
import base64
from datetime import timezone, timedelta
from pathlib import Path
import requests
import feedparser
from openai import OpenAI

# ── 环境变量 (严格对齐 Secrets 规范) ──────────────────────────────
OPENROUTER_API_KEY    = os.getenv("OPENROUTER_API_KEY", "")
KIMI_API_KEY          = os.getenv("KIMI_API_KEY", "")
TWTAPI_KEY            = os.getenv("TWTAPI_KEY", "") 
JIJYUN_WEBHOOK_URL    = os.getenv("JIJYUN_WEBHOOK_URL", "") 
SF_API_KEY            = os.getenv("SF_API_KEY", "")       
IMGBB_API_KEY         = os.getenv("IMGBB_API_KEY", "")    

OPENROUTER_MODEL      = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.7-sonnet")
try:
    KIMI_TEMPERATURE  = float(os.getenv("KIMI_TEMPERATURE", "0.3"))
except:
    KIMI_TEMPERATURE  = 0.3

# ── Base64 URL 隐身术 (彻底防止编辑器将代码内的网址转换为Markdown超链接) ──
def D(b64_str):
    return base64.b64decode(b64_str).decode("utf-8")

URL_YT_RSS = D("aHR0cHM6Ly93d3cueW91dHViZS5jb20vZmVlZHMvdmlkZW9zLnhtbD9jaGFubmVsX2lkPQ==")
URL_YT_SEARCH = D("aHR0cHM6Ly93d3cueW91dHViZS5jb20vcmVzdWx0cz9zZWFyY2hfcXVlcnk9")
URL_YT_WATCH = D("aHR0cHM6Ly93d3cueW91dHViZS5jb20vd2F0Y2g/dj0=")
URL_JINA = D("aHR0cHM6Ly9yLmppbmEuYWkv")
URL_OPENROUTER = D("aHR0cHM6Ly9vcGVucm91dGVyLmFpL2FwaS92MS9jaGF0L2NvbXBsZXRpb25z")
URL_MOONSHOT = D("aHR0cHM6Ly9hcGkubW9vbnNob3QuY24vdjE=")
URL_SILICONFLOW = D("aHR0cHM6Ly9hcGkuc2lsaWNvbmZsb3cuY24vdjEvaW1hZ2VzL2dlbmVyYXRpb25z")
URL_IMGBB = D("aHR0cHM6Ly9hcGkuaW1nYmIuY29tLzEvdXBsb2Fk")
DEFAULT_COVER_URL = D("aHR0cDovL21tYml6LnFwaWMuY24vc3pfbW1iaXpfcG5nL1NmUHdGWVlpY0lsaWFnRWs4ekxjZXNjN3NCVlpxaWJIbnhOOGtoV2I2ME5pY1dER0tpYUtRdW03eXNBWEh3WFcxUkY0ektMS25NcnNLWUJETzVVM21QSWh5ZTJyNFp6ZHdpY2E5WHFhTVdpYVc4elU3cy8wP3d4X2ZtdD1wbmc=")

# ── 飞书多 Webhook 支持 ──────────────────────────────────────────
def get_feishu_webhooks() -> list:
    urls = []
    for suffix in ["", "_1", "_2", "_3"]:
        url = os.getenv(f"FEISHU_WEBHOOK_URL{suffix}", "")
        if url:
            urls.append(url)
    return urls

# ── 策略常量 ─────────────────────────────────────────────────────
DEEP_DIVE_THRESHOLD_SEC = 40 * 60 
CANDIDATE_POOL_SIZE = 20   
MAX_VALID_VIDEOS = 5       

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

def sanitize_text(text):
    if not text: return ""
    clean = re.sub(r'[\xa0\u200b\u3000\r\t]+', ' ', str(text))
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()

def safe_parse_json(text):
    text = text.replace('```json', '').replace('```', '').strip()
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            pass
    return None

# ════════════════════════════════════════════════════════════════
# Phase 1: 扫描与过滤 (URL 防腐保护)
# ════════════════════════════════════════════════════════════════
def scan_best_videos_strictly():
    print(f"\n[扫描] 启动 24H 引擎，构建 {CANDIDATE_POOL_SIZE} 大候补池...")
    now = datetime.datetime.now(timezone.utc)
    deadline = now - timedelta(hours=24)
    candidates = []

    for ch_id, info in CORE_CHANNELS.items():
        try:
            feed = feedparser.parse(URL_YT_RSS + ch_id)
            for entry in feed.entries:
                pub_time = datetime.datetime.strptime(entry.published, "%Y-%m-%dT%H:%M:%S%z")
                if pub_time > deadline:
                    candidates.append({"video_id": entry.yt_videoid, "title": entry.title, "author": info["name"], "views": 999999, "duration_sec": 3600})
        except: pass

    for vip in VIP_LIST:
        try:
            url = URL_YT_SEARCH + requests.utils.quote(vip + ' interview') + "&sp=EgQIAhAB"
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
            html_text = requests.get(url, headers=headers, timeout=15).text
            
            data_match = re.search(r'(?:ytInitialData)\s*=\s*(\{.+?\});', html_text)
            if not data_match: continue
            data = json.loads(data_match.group(1))
            
            try: items = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents'][0]['itemSectionRenderer']['contents']
            except: continue

            for item in items:
                if 'videoRenderer' in item:
                    vr = item['videoRenderer']
                    title = vr['title']['runs'][0]['text']
                    time_text = vr.get('publishedTimeText', {}).get('simpleText', '').lower()
                    
                    if any(x in time_text for x in ['day', 'week', 'month', 'year', '天', '周', '月', '年']): continue
                    if not any(x in time_text for x in ['hour', 'minute', 'second', 'ago', '前', '刚刚']): continue

                    d_sec = parse_duration(vr.get('lengthText', {}).get('simpleText', '0:00'))
                    if d_sec > 600:
                        candidates.append({"video_id": vr['videoId'], "title": title, "author": vr['ownerText']['runs'][0]['text'], "views": parse_views(vr.get('viewCountText', {}).get('simpleText', '0')), "duration_sec": d_sec})
        except: pass

    unique = {v['video_id']: v for v in candidates}
    pool = sorted(unique.values(), key=lambda x: (x['duration_sec'] >= DEEP_DIVE_THRESHOLD_SEC, x['views']), reverse=True)[:CANDIDATE_POOL_SIZE]
    print(f"🎯 寻获 {len(pool)} 个新鲜视频。")
    return pool

def run_single_video_analysis(video, model_type="claude"):
    print(f"  🎬 正在解剖: {video['title'][:30]}...")
    yt_url = URL_YT_WATCH + video['video_id']
    try:
        resp = requests.get(URL_JINA + yt_url, headers={"X-Return-Format": "text"}, timeout=40)
        if resp.status_code != 200 or len(resp.text) < 500: return None
        transcript = " ".join(resp.text.split("Title:", 1)[-1].split()[:20000])
        
        prompt = f"""你是一名极为严苛的硅谷创投分析师。
【过滤】：如果内容是纯社会新闻/娱乐/美食等无关内容，判定 is_relevant: false
【输出 JSON】：必须且只能输出合法的JSON对象，不要包含 markdown 标记。
{{
  "relevance_analysis": "一句话分析",
  "is_relevant": true/false,
  "title": "深度中文标题",
  "original_english_title": "{video['title']}",
  "tldr": "一句话中文总结",
  "core_thesis": "最核心逻辑",
  "arguments": ["论点1", "论点2", "论点3"], 
  "counter_consensus": "反常规认知",
  "implications": "行业推演"
}}
【字幕】：{transcript}"""
        
        if model_type == "claude":
            r = requests.post(URL_OPENROUTER, headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, json={"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}, timeout=120)
            res = safe_parse_json(r.json()["choices"][0]["message"]["content"])
        else:
            client = OpenAI(api_key=KIMI_API_KEY, base_url=URL_MOONSHOT)
            r = client.chat.completions.create(model="kimi-k2.5", messages=[{"role": "user", "content": prompt}], temperature=KIMI_TEMPERATURE)
            res = safe_parse_json(r.choices[0].message.content)
        
        if res and res.get("is_relevant") is False:
            print(f"    ⏭️ [拦截] {res.get('relevance_analysis', '无关内容')} -> 丢弃。")
            return None
        if res:
            print("    ✅ [通过] 优质情报。")
            return res
        return None
    except: return None

# ════════════════════════════════════════════════════════════════
# Phase 2: 全案策划 (加入重试机制 + 极强提示词压制)
# ════════════════════════════════════════════════════════════════
def generate_global_wrapup(summaries, model_type="claude"):
    print(f"\n[全案] 正在合成爆款标题、导读与封面 Prompt...")
    base_data = [{"title": s['title'], "tldr": s['tldr']} for s in summaries if s]
    
    prompt = f"""基于今日这 {len(base_data)} 篇情报，完成以下3个任务：
1. 拟定一个 20 字以内的爆款中文标题（具有极强的新媒体/微信公众号吸睛风格，制造悬念或点明核心冲突，严禁平淡无味）。
2. 撰写一个不超过 50 字的最抓马的反共识中文导读（今日摘要）。
3. 生成一段高质量的英文生图提示词(cover_prompt)。紧扣今日情报核心，画风：Cyberpunk, neon glowing, cinematic lighting, unreal engine 5。不要包含任何文本或字母。

【数据】：{json.dumps(base_data, ensure_ascii=False)}
【严格输出要求】：必须且只能输出一个合法的 JSON 对象，绝对不能包含任何分析、问候语或 markdown 代码块标记。
{{ "article_title": "极具网感的公众号爆款标题", "article_summary": "50字内导读", "cover_prompt": "A futuristic..." }}"""
    
    for attempt in range(3):
        try:
            if model_type == "claude":
                r = requests.post(URL_OPENROUTER, headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, json={"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}]}, timeout=60)
                res = safe_parse_json(r.json()["choices"][0]["message"]["content"])
            else:
                client = OpenAI(api_key=KIMI_API_KEY, base_url=URL_MOONSHOT)
                r = client.chat.completions.create(model="kimi-k2.5", messages=[{"role": "user", "content": prompt}])
                res = safe_parse_json(r.choices[0].message.content)
            
            if res and res.get("article_title"):
                print(f"  ✅ 策划生成成功！标题：{res['article_title'][:15]}...")
                return res
        except Exception as e: 
            print(f"  ⚠️ 策划解析失败 ({model_type} 尝试 {attempt+1}/3): {e}")
            time.sleep(2)
            
    return {"article_title": "巨头暗战与前沿科技：硅谷硬核解码", "article_summary": "当地缘核弹引爆能源危机，AI正从硅谷实验室悄然接管全球生存法则。", "cover_prompt": "Futuristic AI artificial intelligence brain glowing circuits, cyberpunk"}

# ════════════════════════════════════════════════════════════════
# Phase 3: AI生图与图床转存 (URL 防腐保护)
# ════════════════════════════════════════════════════════════════
def generate_ai_cover(prompt):
    if not SF_API_KEY or not prompt: return ""
    print(f"\n[生图] 正在调用硅基流动 FLUX 生成封面...")
    try:
        resp = requests.post(
            URL_SILICONFLOW,
            headers={"Authorization": f"Bearer {SF_API_KEY}", "Content-Type": "application/json"},
            json={"model": "black-forest-labs/FLUX.1-schnell", "prompt": prompt, "n": 1, "image_size": "1024x576"},
            timeout=60
        )
        if resp.status_code == 200:
            url = resp.json().get("images", [{}])[0].get("url") or resp.json().get("data", [{}])[0].get("url")
            print(f"  ✅ 生图成功！URL: {url[:60]}...")
            return url
    except Exception as e: print(f"  ❌ 生图失败: {e}")
    return ""

def upload_to_imgbb_via_url(sf_url):
    if not IMGBB_API_KEY or not sf_url: return sf_url 
    print(f"  [图床] 正在转存至 ImgBB 以保障微信环境渲染...")
    try:
        img_resp = requests.get(sf_url, timeout=30)
        img_b64 = base64.b64encode(img_resp.content).decode("utf-8")
        
        upload_resp = requests.post(URL_IMGBB, data={"key": IMGBB_API_KEY, "image": img_b64}, timeout=45)
        if upload_resp.status_code == 200:
            final_url = upload_resp.json()["data"]["url"]
            print(f"  ✅ 图床转存成功！固定直链: {final_url}")
            return final_url
    except Exception as e: print(f"  ⚠️ 转存失败，继续使用原链: {e}")
    return sf_url

# ════════════════════════════════════════════════════════════════
# Phase 4: 多端视觉分发 (最新对位排版)
# ════════════════════════════════════════════════════════════════
def build_and_push(summaries, wrapup, final_cover_url, channel="feishu"):
    if not summaries: return
    date_str = datetime.datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    
    if channel == "feishu":
        webhooks = get_feishu_webhooks()
        if not webhooks: return

        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "**⚠️ 早 8 点｜拆解超长视频｜硅谷人在想啥**"}},
                    {"tag": "note", "elements": [{"tag": "lark_md", "content": f"💡 **今日摘要**：{sanitize_text(wrapup['article_summary'])}"}], "background_color": "blue"},
                    {"tag": "hr"}]
        for i, v in enumerate(summaries, 1):
            clean_tldr = re.sub(r'(?i)^(TL;?DR\s*[:：]\s*)', '', sanitize_text(v.get('tldr', ''))).strip()
            args_text = "\n".join([f"• {sanitize_text(a)}" for a in v.get('arguments', []) if a])
            
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🍉 {i}. {sanitize_text(v['title'])}**\n💡 {sanitize_text(v['original_english_title'])}\n**核心速读：**{clean_tldr}"}})
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**🎯 核心主张**：{sanitize_text(v.get('core_thesis', ''))}\n**🧱 论点与证据链**：\n{args_text}\n**🧠 反共识**：{sanitize_text(v.get('counter_consensus', ''))}"}})
            elements.append({"tag": "hr"})
        
        # 🚨 飞书：主标题固定，副标题展示自动生成的公众号爆款标题
        payload = {
            "msg_type": "interactive", 
            "card": {
                "config": {"wide_screen_mode": True}, 
                "header": {
                    "title":  {"tag": "plain_text", "content": f"{sanitize_text(wrapup['article_title'])} | {date_str}"},  
                    "subtitle":{"tag": "plain_text", "content": "🌍 长播客拆解：过去24h的硅谷人物深度访谈"}, 
                    "template": "purple"
                }, 
                "elements": elements
            }
        }
        
        for url in webhooks:
            try: requests.post(url, json=payload, timeout=10)
            except: pass
        print(f"  ✅ 已向 {len(webhooks)} 个飞书群推送完毕")
        
    else: # WeChat
        if not JIJYUN_WEBHOOK_URL: return
        
        intro_html = (
            f'<section style="margin:20px 0; padding:15px; background:#f8f9fa; border-left:5px solid #2b579a;">'
            f'<p style="font-size:15px; font-weight:bold; color:#333; margin:0 0 8px 0;">💡 今日摘要：<span style="font-weight:normal;">{sanitize_text(wrapup["article_summary"])}</span></p>'
            f'<p style="font-size:13px; color:#888; margin:0;">⚠️ 早 8 点｜拆解超长视频｜硅谷人在想啥</p>'
            f'</section>'
        )

        html_p = [f'<section style="text-align:center;"><img src="{final_cover_url}" style="max-width:100%; border-radius:8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); display:block; margin: 0 auto;"/></section>',
                  intro_html]
                  
        for i, v in enumerate(summaries, 1):
            clean_tldr = re.sub(r'(?i)^(TL;?DR\s*[:：]\s*)', '', sanitize_text(v.get('tldr', ''))).strip()
            args_html = "<ul style='margin: 10px 0; padding-left: 22px; font-size: 14px; color: #555; line-height: 1.6; list-style-type: disc;'>"
            for a in [sanitize_text(a) for a in v.get('arguments', []) if a]: args_html += f"<li style='margin-bottom: 8px; padding-left: 4px;'>{a}</li>"
            args_html += "</ul>"
            
            v_h = f"""<section style="margin-bottom:35px;">
                      <h2 style="font-size:18px; color:#2b579a; border-bottom:1px solid #eef2f8; padding-bottom:8px;">🍉 {i}. {sanitize_text(v['title'])}</h2>
                      <p style="font-size:13px; color:#999; margin:6px 0;">Source: {sanitize_text(v['original_english_title'])}</p>
                      <div style="margin:12px 0; font-size:15px; background:#eef2f8; padding:12px; border-radius:6px; color:#333;"><strong>💡 核心速读：</strong>{clean_tldr}</div>
                      <div style="font-size:15px; line-height:1.7; color:#444;">
                          <p style="margin: 10px 0;"><strong>🎯 核心主张：</strong>{sanitize_text(v.get('core_thesis', ''))}</p>
                          <div style="margin: 10px 0;"><strong>🧱 论点与证据链：</strong>{args_html}</div>
                          <p style="margin: 10px 0;"><strong>🧠 反共识：</strong>{sanitize_text(v.get('counter_consensus', ''))}</p>
                      </div>
                      </section>"""
            html_p.append(v_h)
        
        payload = {"author": "大尉 Prinski", "cover_jpg": final_cover_url, "html_content": "".join(html_p).replace('\n',''), "title": sanitize_text(wrapup['article_title'])}
        requests.post(JIJYUN_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        print("  ✅ 已向微信极简云推送完毕")

def main():
    print("=" * 60 + "\n🚀 硅谷智能护盾情报系统 V26.0 (URL隐身防腐版) 启动\n" + "=" * 60)
    candidates_pool = scan_best_videos_strictly()
    if not candidates_pool:
        print("📭 过去 24 小时没有任何新鲜情报。")
        return

    # -------- 飞书通道 (Claude 主导) --------
    c_summaries = []
    for v in candidates_pool:
        res = run_single_video_analysis(v, "claude")
        if res: c_summaries.append(res)
        if len(c_summaries) >= MAX_VALID_VIDEOS: break
    if c_summaries:
        claude_wrap = generate_global_wrapup(c_summaries, "claude")
        build_and_push(c_summaries, claude_wrap, DEFAULT_COVER_URL, "feishu")

    # -------- 微信通道 (Kimi 主导 + 生图) --------
    print("\n---------- [启动 Kimi 微信分发流与生图引擎] ----------")
    k_summaries = []
    for v in candidates_pool:
        res = run_single_video_analysis(v, "kimi")
        if res: k_summaries.append(res)
        if len(k_summaries) >= MAX_VALID_VIDEOS: break
            
    if k_summaries:
        kimi_wrap = generate_global_wrapup(k_summaries, "kimi")
        
        final_cover_url = DEFAULT_COVER_URL
        prompt = kimi_wrap.get("cover_prompt", "")
        if prompt:
            sf_url = generate_ai_cover(prompt)
            if sf_url:
                imgbb_url = upload_to_imgbb_via_url(sf_url)
                if imgbb_url: final_cover_url = imgbb_url
                else: final_cover_url = sf_url 
        
        build_and_push(k_summaries, kimi_wrap, final_cover_url, "wechat")

    print("\n🎉 V26.0 全量处理完毕！")

if __name__ == "__main__":
    main()

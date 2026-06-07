"""
Forex News Discord Bot — BTC Better Together
ดึงข่าว Forex จาก ForexFactory + NewsAPI
วิเคราะห์และเรียบเรียงเป็นภาษาไทยด้วย OpenAI GPT
กรองเฉพาะข่าว USD ผลกระทบสูง ส่งเข้า Discord อัตโนมัติ
"""

import os
import time
import json
import hashlib
import requests
import feedparser
from datetime import datetime, timezone
from openai import OpenAI

# ─── CONFIG ────────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
NEWSAPI_KEY         = os.environ.get("NEWSAPI_KEY", "")
OPENAI_API_KEY      = os.environ.get("OPENAI_API_KEY", "")
CHECK_INTERVAL_SEC  = int(os.environ.get("CHECK_INTERVAL_SEC", "600"))

client = OpenAI(api_key=OPENAI_API_KEY)

# ─── SOURCES ──────────────────────────────────────────────────────────────────
FOREXFACTORY_URL = "https://www.forexfactory.com/ff_calendar_thisweek.xml"
NEWSAPI_URL      = "https://newsapi.org/v2/everything"
NEWSAPI_PARAMS   = {
    "q": "USD OR \"federal reserve\" OR FOMC OR \"interest rate\" OR \"nonfarm payroll\" OR \"US inflation\" OR \"US GDP\"",
    "language": "en",
    "sortBy": "publishedAt",
    "pageSize": 8,
    "apiKey": NEWSAPI_KEY,
}

# ─── STATE ────────────────────────────────────────────────────────────────────
SENT_IDS_FILE = "sent_ids.json"

def load_sent_ids() -> set:
    if os.path.exists(SENT_IDS_FILE):
        with open(SENT_IDS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_sent_ids(ids: set):
    with open(SENT_IDS_FILE, "w") as f:
        json.dump(list(ids)[-500:], f)

def make_id(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


# ─── FETCH: ForexFactory ──────────────────────────────────────────────────────
def fetch_forexfactory() -> list[dict]:
    try:
        feed = feedparser.parse(FOREXFACTORY_URL)
        results = []
        for entry in feed.entries[:8]:
            results.append({
                "source": "ForexFactory",
                "title": entry.get("title", ""),
                "summary": entry.get("summary", entry.get("description", "")),
                "link": entry.get("link", ""),
            })
        return results
    except Exception as e:
        print(f"[ForexFactory] Error: {e}")
        return []


# ─── FETCH: NewsAPI ────────────────────────────────────────────────────────────
def fetch_newsapi() -> list[dict]:
    if not NEWSAPI_KEY:
        return []
    try:
        resp = requests.get(NEWSAPI_URL, params=NEWSAPI_PARAMS, timeout=10)
        resp.raise_for_status()
        results = []
        for a in resp.json().get("articles", []):
            results.append({
                "source": f"NewsAPI · {a.get('source', {}).get('name', 'Unknown')}",
                "title": a.get("title", ""),
                "summary": a.get("description", "") or a.get("content", ""),
                "link": a.get("url", ""),
            })
        return results
    except Exception as e:
        print(f"[NewsAPI] Error: {e}")
        return []


# ─── FILTER: USD + High Impact ────────────────────────────────────────────────
USD_KEYWORDS = [
    "usd", "dollar", "fed", "federal reserve", "fomc", "powell",
    "us economy", "u.s.", "united states", "nonfarm", "us cpi",
    "us gdp", "us inflation", "us jobs", "treasury",
    "eur/usd", "gbp/usd", "usd/jpy", "usd/cad", "usd/chf"
]
HIGH_IMPACT_KEYWORDS = [
    "fed", "federal reserve", "interest rate", "rate hike", "rate cut",
    "inflation", "cpi", "gdp", "nonfarm", "fomc", "powell",
    "central bank", "monetary policy", "recession", "unemployment"
]

def is_high_impact_usd(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    usd_hit    = any(kw in text for kw in USD_KEYWORDS)
    impact_hits = sum(1 for kw in HIGH_IMPACT_KEYWORDS if kw in text)
    return usd_hit and impact_hits >= 2


# ─── ANALYZE via OpenAI ───────────────────────────────────────────────────────
def analyze_with_gpt(item: dict) -> dict | None:
    """
    ใช้ GPT แปล สรุป และวิเคราะห์ข่าว Forex เป็นภาษาไทย
    คืนค่า dict พร้อมทุก field สำหรับ Discord embed
    """
    prompt = f"""คุณคือนักวิเคราะห์ตลาด Forex มืออาชีพที่เขียนบทวิเคราะห์ภาษาไทยให้นักลงทุนในชุมชน BTC Better Together

ข่าวต้นฉบับ:
หัวข้อ: {item['title']}
รายละเอียด: {item['summary'][:1000]}

กรุณาวิเคราะห์และตอบเป็น JSON เท่านั้น ตามรูปแบบนี้:
{{
  "emoji": "emoji 1 ตัวที่เหมาะกับข่าวนี้",
  "title_th": "หัวข้อภาษาไทย กระชับ น่าสนใจ ไม่เกิน 70 ตัวอักษร",
  "summary_th": "สรุปเนื้อหา 2-3 ประโยค อ่านเข้าใจง่าย ตรงประเด็น",
  "analysis": "วิเคราะห์ผลกระทบต่อค่าเงิน USD และตลาด Forex 2-3 ประโยค บอกทิศทางที่คาดว่าจะเกิดขึ้น",
  "action": "แนวทางที่นักเทรดควรระวังหรือจับตามอง เขียนเป็นข้อสั้นๆ 2-3 ข้อ",
  "currencies": "สกุลเงินที่เกี่ยวข้อง เช่น USD · EUR · JPY",
  "direction": "USD_UP หรือ USD_DOWN หรือ NEUTRAL (ทิศทาง USD ที่คาดการณ์)"
}}

ตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",   # ประหยัด และดีพอสำหรับงานนี้
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700,
            temperature=0.3,
        )
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[OpenAI] Error: {e}")
        return None


# ─── DISCORD: ส่งข่าว ─────────────────────────────────────────────────────────
DIRECTION_COLOR = {
    "USD_UP":   0x2ECC71,   # เขียว — USD แข็ง
    "USD_DOWN": 0xE74C3C,   # แดง   — USD อ่อน
    "NEUTRAL":  0xF39C12,   # ส้ม   — ทรงตัว
}

def send_to_discord(item: dict, analysis: dict):
    direction = analysis.get("direction", "NEUTRAL")
    color     = DIRECTION_COLOR.get(direction, 0x3498DB)
    now_th    = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    # แปลง action string เป็น bullet list
    action_text = analysis.get("action", "")
    if action_text and not action_text.startswith("•"):
        lines = [l.strip() for l in action_text.split("\n") if l.strip()]
        action_text = "\n".join(f"• {l.lstrip('•-').strip()}" for l in lines)

    # Direction badge
    direction_badge = {
        "USD_UP":   "💚 USD แข็งค่า",
        "USD_DOWN": "🔴 USD อ่อนค่า",
        "NEUTRAL":  "🟡 ทรงตัว",
    }.get(direction, "⚪ ไม่ชัดเจน")

    fields = [
        {
            "name": "📋 สรุปข่าว",
            "value": analysis.get("summary_th", "-"),
            "inline": False,
        },
        {
            "name": "🔍 วิเคราะห์ผลกระทบ",
            "value": analysis.get("analysis", "-"),
            "inline": False,
        },
        {
            "name": "💡 จับตามอง",
            "value": action_text or "-",
            "inline": False,
        },
        {
            "name": "💱 สกุลเงิน",
            "value": analysis.get("currencies", "USD"),
            "inline": True,
        },
        {
            "name": "📊 ทิศทาง USD",
            "value": f"**{direction_badge}**",
            "inline": True,
        },
        {
            "name": "🔗 ต้นฉบับ",
            "value": f"[อ่านเพิ่มเติม]({item['link']})" if item.get("link") else "-",
            "inline": True,
        },
    ]

    payload = {
        "username": "BTC Forex News 📡",
        "embeds": [{
            "title": f"{analysis.get('emoji','📰')} {analysis.get('title_th', item['title'])}",
            "color": color,
            "fields": fields,
            "footer": {
                "text": f"แหล่งที่มา: {item['source']}  •  {now_th}  •  วิเคราะห์โดย GPT-4o mini"
            },
        }]
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            print(f"  ✅ ส่งสำเร็จ: {analysis.get('title_th','')[:50]}")
        else:
            print(f"  ❌ Discord Error {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"  ❌ Discord Error: {e}")


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("🚀 BTC Forex News Bot เริ่มทำงาน")
    print(f"   ตรวจข่าวทุก {CHECK_INTERVAL_SEC // 60} นาที")
    print(f"   วิเคราะห์โดย: OpenAI GPT-4o mini")
    print(f"   กรอง: USD + ผลกระทบสูงเท่านั้น")
    print("=" * 55)

    if not DISCORD_WEBHOOK_URL:
        print("❌ ERROR: ยังไม่ได้ตั้งค่า DISCORD_WEBHOOK_URL")
        return
    if not OPENAI_API_KEY:
        print("❌ ERROR: ยังไม่ได้ตั้งค่า OPENAI_API_KEY")
        return

    sent_ids = load_sent_ids()

    while True:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] กำลังตรวจข่าวใหม่...")

        all_items = fetch_forexfactory() + fetch_newsapi()
        new_count = 0

        for item in all_items:
            if not item["title"].strip():
                continue

            item_id = make_id(item["title"])
            if item_id in sent_ids:
                continue

            # ─── Filter: USD + High Impact ──────────────────────────────────
            if not is_high_impact_usd(item["title"], item["summary"]):
                print(f"  ⏭ ข้าม: {item['title'][:55]}")
                sent_ids.add(item_id)
                continue

            print(f"  📥 วิเคราะห์: {item['title'][:60]}")

            # ─── วิเคราะห์ด้วย GPT ─────────────────────────────────────────
            analysis = analyze_with_gpt(item)
            if not analysis:
                continue

            # ─── ส่ง Discord ────────────────────────────────────────────────
            send_to_discord(item, analysis)

            sent_ids.add(item_id)
            new_count += 1
            time.sleep(2)

        save_sent_ids(sent_ids)
        print(f"  ✔ ส่ง {new_count} ข่าว | รอ {CHECK_INTERVAL_SEC // 60} นาที...")
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()

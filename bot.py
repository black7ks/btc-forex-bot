"""
Forex News Discord Bot
ดึงข่าว Forex จาก ForexFactory + NewsAPI
แปลเป็นภาษาไทยด้วย deep-translator (ฟรี 100%)
ส่งเข้า Discord อัตโนมัติ
"""

import os
import re
import time
import json
import hashlib
import requests
import feedparser
from datetime import datetime, timezone
from deep_translator import GoogleTranslator

# ─── CONFIG ────────────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
NEWSAPI_KEY         = os.environ.get("NEWSAPI_KEY", "")
CHECK_INTERVAL_SEC  = int(os.environ.get("CHECK_INTERVAL_SEC", "600"))

# ─── SOURCES ──────────────────────────────────────────────────────────────────
FOREXFACTORY_URL = "https://www.forexfactory.com/ff_calendar_thisweek.xml"
NEWSAPI_URL      = "https://newsapi.org/v2/everything"
NEWSAPI_PARAMS   = {
    "q": "forex OR currency OR \"interest rate\" OR \"central bank\" OR USD OR EUR OR GBP",
    "language": "en",
    "sortBy": "publishedAt",
    "pageSize": 5,
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
    trimmed = list(ids)[-500:]
    with open(SENT_IDS_FILE, "w") as f:
        json.dump(trimmed, f)

def make_id(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


# ─── FETCH: ForexFactory RSS ──────────────────────────────────────────────────
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
        print("[NewsAPI] ไม่มี API key ข้าม...")
        return []
    try:
        resp = requests.get(NEWSAPI_URL, params=NEWSAPI_PARAMS, timeout=10)
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
        results = []
        for a in articles:
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


# ─── TRANSLATE via deep-translator (ฟรี ไม่ต้องมี key) ────────────────────────
translator = GoogleTranslator(source="en", target="th")

def translate(text: str) -> str:
    """แปลข้อความอังกฤษ → ไทย ด้วย Google Translate ฟรี"""
    if not text or not text.strip():
        return ""
    try:
        # Google Translate รับได้สูงสุด 5000 ตัวอักษรต่อครั้ง
        text = text[:4500]
        return translator.translate(text)
    except Exception as e:
        print(f"[Translate] Error: {e}")
        return text  # fallback คืนค่าภาษาอังกฤษถ้าแปลไม่ได้


# ─── DETECT: สกุลเงินและระดับผลกระทบ ─────────────────────────────────────────
CURRENCY_PAIRS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF",
                  "CNY", "THB", "SGD", "HKD", "MXN", "INR"]

HIGH_IMPACT_KEYWORDS = [
    "fed", "federal reserve", "interest rate", "rate hike", "rate cut",
    "inflation", "cpi", "gdp", "nonfarm", "fomc", "ecb", "boe",
    "central bank", "monetary policy", "recession", "unemployment"
]

def detect_currencies(text: str) -> str:
    """หาสกุลเงินที่พูดถึงในข่าว"""
    found = [c for c in CURRENCY_PAIRS if c in text.upper()]
    return " · ".join(found[:4]) if found else ""

def detect_impact(title: str, summary: str) -> tuple[str, int]:
    """
    ประเมินระดับผลกระทบจาก keyword
    คืนค่า (label, discord_color)
    """
    combined = (title + " " + summary).lower()
    hits = sum(1 for kw in HIGH_IMPACT_KEYWORDS if kw in combined)
    if hits >= 3:
        return "🔴 สูง", 0xE74C3C
    elif hits >= 1:
        return "🟠 กลาง", 0xF39C12
    else:
        return "🟢 ต่ำ", 0x2ECC71

def pick_emoji(title: str) -> str:
    """เลือก emoji ให้เหมาะกับเนื้อหาข่าว"""
    t = title.lower()
    if any(w in t for w in ["rate", "interest", "fed", "bank"]):
        return "🏦"
    elif any(w in t for w in ["inflation", "cpi", "price"]):
        return "📈"
    elif any(w in t for w in ["gdp", "growth", "economy"]):
        return "💹"
    elif any(w in t for w in ["job", "unemployment", "nonfarm"]):
        return "👷"
    elif any(w in t for w in ["gold", "oil", "commodity"]):
        return "🛢️"
    elif any(w in t for w in ["war", "conflict", "sanction"]):
        return "⚠️"
    else:
        return "📰"


# ─── PROCESS: แปลและวิเคราะห์ข่าว ────────────────────────────────────────────
def process_item(item: dict) -> dict | None:
    """แปลข่าวเป็นไทย + วิเคราะห์ผลกระทบ"""
    title_th   = translate(item["title"])
    summary_th = translate(item["summary"][:800]) if item["summary"] else ""

    if not title_th:
        return None

    impact_label, color = detect_impact(item["title"], item["summary"])
    currencies = detect_currencies(item["title"] + " " + item["summary"])
    emoji = pick_emoji(item["title"])

    return {
        "title_th":   title_th,
        "summary_th": summary_th,
        "impact":     impact_label,
        "color":      color,
        "currencies": currencies,
        "emoji":      emoji,
    }


# ─── DISCORD: ส่งข่าว ─────────────────────────────────────────────────────────
def send_to_discord(item: dict, processed: dict):
    now_th = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    fields = []
    if processed["currencies"]:
        fields.append({
            "name": "💱 สกุลเงินที่เกี่ยวข้อง",
            "value": processed["currencies"],
            "inline": True,
        })
    fields.append({
        "name": "📊 ระดับผลกระทบ",
        "value": f"**{processed['impact']}**",
        "inline": True,
    })
    fields.append({
        "name": "🔗 อ่านเพิ่มเติม",
        "value": f"[คลิกที่นี่]({item['link']})" if item.get("link") else "ไม่มีลิงก์",
        "inline": True,
    })

    payload = {
        "username": "BTC Forex News 📡",
        "embeds": [{
            "title": f"{processed['emoji']} {processed['title_th']}",
            "description": processed["summary_th"] or "(ไม่มีรายละเอียดเพิ่มเติม)",
            "color": processed["color"],
            "fields": fields,
            "footer": {
                "text": f"แหล่งที่มา: {item['source']}  •  {now_th}"
            },
        }]
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            print(f"  ✅ ส่งสำเร็จ: {processed['title_th'][:50]}")
        else:
            print(f"  ❌ Discord Error {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"  ❌ Discord Error: {e}")


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("🚀 BTC Forex News Bot เริ่มทำงาน (ฟรี 100%)")
    print(f"   ตรวจข่าวทุก {CHECK_INTERVAL_SEC // 60} นาที")
    print(f"   แปลภาษา: Google Translate (deep-translator)")
    print(f"   ⚠️  ส่งเฉพาะข่าวผลกระทบ: 🔴 สูง เท่านั้น")
    print("=" * 55)

    if not DISCORD_WEBHOOK_URL:
        print("❌ ERROR: ยังไม่ได้ตั้งค่า DISCORD_WEBHOOK_URL")
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

            print(f"  📥 {item['title'][:65]}")

            # แปลและวิเคราะห์
            processed = process_item(item)
            if not processed:
                continue

            # ✅ กรองเฉพาะข่าวผลกระทบสูงเท่านั้น
            if "สูง" not in processed["impact"]:
                print(f"  ⏭ ข้าม ({processed['impact']}): {item['title'][:50]}")
                sent_ids.add(item_id)  # mark ว่าเห็นแล้ว ไม่ต้องเช็คซ้ำ
                continue

            # ✅ กรองเฉพาะข่าวที่เกี่ยวกับ USD เท่านั้น
            USD_KEYWORDS = [
                "usd", "dollar", "fed", "federal reserve", "fomc",
                "us economy", "u.s.", "united states", "nonfarm",
                "cpi us", "us gdp", "us inflation", "us jobs",
                "powell", "treasury", "eur/usd", "gbp/usd",
                "usd/jpy", "usd/cad", "usd/chf", "audusd", "nzdusd"
            ]
            title_lower   = item["title"].lower()
            summary_lower = item["summary"].lower()
            is_usd_related = any(kw in title_lower or kw in summary_lower for kw in USD_KEYWORDS)

            if not is_usd_related:
                print(f"  ⏭ ข้าม (ไม่เกี่ยว USD): {item['title'][:50]}")
                sent_ids.add(item_id)
                continue

            # ส่งเข้า Discord
            send_to_discord(item, processed)

            sent_ids.add(item_id)
            new_count += 1
            time.sleep(2)  # ป้องกัน rate limit

        save_sent_ids(sent_ids)
        print(f"  ✔ ข่าวใหม่ {new_count} ข่าว | รอ {CHECK_INTERVAL_SEC // 60} นาที...")
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()

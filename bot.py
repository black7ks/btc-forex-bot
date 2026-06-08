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
    # normalize ให้ตัวพิมพ์เล็ก ตัดช่องว่างซ้ำ ป้องกันข่าวเบิ้ล
    normalized = " ".join(text.lower().split())
    return hashlib.md5(normalized.encode()).hexdigest()






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


# ─── FILTER ───────────────────────────────────────────────────────────────────

# ข่าวทรัมป์ → ส่งทุกข่าวที่เกี่ยวทรัมป์
TRUMP_KEYWORDS = [
    "trump", "donald trump", "white house", "tariff", "trade war",
    "executive order", "sanction", "iran deal", "nato", "pentagon",
    "ceasefire", "truth social", "maga"
]

# ข่าวทองคำโดยตรง
GOLD_KEYWORDS = [
    "gold", "xau", "xauusd", "xau/usd", "bullion",
    "gold price", "spot gold", "precious metal"
]

# ข่าว USD ระดับกลาง-สูง ที่กระทบทอง
USD_MEDIUM_HIGH = [
    # Fed / ดอกเบี้ย (สูง)
    "fed", "federal reserve", "fomc", "powell", "interest rate",
    "rate hike", "rate cut", "rate decision", "monetary policy",
    # เศรษฐกิจสหรัฐฯ (สูง)
    "nonfarm", "payroll", "jobs report", "unemployment", "cpi",
    "inflation", "gdp", "retail sales", "pce", "ism",
    # ตลาดการเงิน (กลาง)
    "treasury yield", "bond yield", "dxy", "dollar index",
    "real yield", "debt ceiling", "us budget", "us deficit",
    "recession", "stagflation",
    # ภูมิรัฐศาสตร์ที่กระทบ safe haven (กลาง)
    "war", "conflict", "crisis", "geopolit", "risk off",
    "safe haven", "russia", "israel", "china tension",
    "north korea", "middle east"
]

def should_send(title: str, summary: str) -> tuple[bool, str]:
    text = (title + " " + summary).lower()

    # 1. ทรัมป์ที่กระทบตลาดโดยตรง
    trump_market_kw = [
        "trump tariff", "trump sanction", "trump trade",
        "trump iran", "trump israel", "trump fed", "trump rate",
        "trump china", "trump russia", "trump nato", "trump deal",
        "trump ceasefire", "trump executive order"
    ]
    if any(kw in text for kw in trump_market_kw):
        return True, "🇺🇸 ทรัมป์"

    # 2. ภูมิรัฐศาสตร์รุนแรง — กระทบ safe haven / ทอง
    geo_kw = [
        "airstrike", "missile attack", "nuclear", "invasion",
        "war declared", "ceasefire", "israel iran", "russia ukraine",
        "middle east war", "breaking: attack", "military strike"
    ]
    if any(kw in text for kw in geo_kw):
        return True, "⚠️ ภูมิรัฐศาสตร์"

    # 3. Fed / ดอกเบี้ย — ข่าวสำคัญระดับสูง
    fed_kw = [
        "rate hike", "rate cut", "fomc decision", "fed raises",
        "fed cuts", "powell press conference", "emergency rate",
        "rate decision", "federal reserve raises", "federal reserve cuts"
    ]
    if any(kw in text for kw in fed_kw):
        return True, "🏦 Fed"

    # 4. ข้อมูลเศรษฐกิจสหรัฐฯ ระดับสูงมาก (top-tier only)
    macro_kw = [
        "nonfarm payroll", "non-farm payroll",
        "cpi beats", "cpi misses", "cpi higher", "cpi lower",
        "inflation surges", "inflation falls",
        "gdp contracts", "gdp shrinks", "recession confirmed",
        "unemployment jumps", "unemployment falls"
    ]
    if any(kw in text for kw in macro_kw):
        return True, "📊 เศรษฐกิจ"

    # 5. ทองคำ — เฉพาะข่าวใหญ่มาก
    gold_big_kw = [
        "gold hits record", "gold all-time high", "gold surges",
        "gold plunges", "gold crashes", "gold breaks", "xau record"
    ]
    if any(kw in text for kw in gold_big_kw):
        return True, "🥇 ทองคำ"

    return False, ""


# ─── ANALYZE via OpenAI ───────────────────────────────────────────────────────
def analyze_with_gpt(item: dict) -> dict | None:
    prompt = f"""คุณคือบรรณาธิการข่าวการเงินภาษาไทย สำหรับชุมชนนักเทรดทองคำ BTC Better Together

ข่าวต้นฉบับ:
หัวข้อ: {item['title']}
รายละเอียด: {item['summary'][:1000]}

งานของคุณ: เขียนข่าวด่วนภาษาไทยแบบกระชับ อ่านเข้าใจใน 5 วินาที
สไตล์: เหมือนข่าวด่วน Breaking News บน X (Twitter) — สั้น ตรง มีผลกระทบชัดเจน

ตอบเป็น JSON เท่านั้น:
{{
  "emoji": "emoji 1 ตัวที่เหมาะสม (⚠️ ข่าวรุนแรง, 🇺🇸 ทรัมป์/สหรัฐฯ, 🏦 Fed/ดอกเบี้ย, 🥇 ทอง, 📊 เศรษฐกิจ)",
  "title_th": "หัวข้อข่าวด่วนภาษาไทย กระชับ ตรง ไม่เกิน 60 ตัวอักษร",
  "body_th": "เนื้อหาข่าว 3-4 ประโยค: (1) เกิดอะไรขึ้น (2) ทำไมถึงสำคัญ (3) กระทบ USD และทองคำอย่างไร เขียนเป็นย่อหน้าเดียว อ่านลื่นไหล ไม่มีหัวข้อย่อย"
}}

ตอบ JSON เท่านั้น ห้ามมีข้อความอื่น"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3,
        )
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[OpenAI] Error: {e}")
        return None


# ─── DISCORD: ส่งข่าว ─────────────────────────────────────────────────────────
def send_to_discord(item: dict, analysis: dict):
    # ส่งเป็น plain text เหมือนคนพิมพ์ ไม่มีกล่อง/กรอบ ไม่มีลิงก์
    message = (
        f"{analysis.get('emoji','📰')} **{analysis.get('title_th', item['title'])}**\n"
        f"\n"
        f"{analysis.get('body_th', '-')}"
    )

    payload = {
        "username": "BTC Forex News 📡",
        "content": message,   # ใช้ content แทน embeds → ไม่มีกรอบ
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

        # ดึงและ dedup ข่าว
        all_items = fetch_forexfactory() + fetch_newsapi()

        seen_titles: set[str] = set()
        deduped_items = []
        for it in all_items:
            short = " ".join(it["title"].lower().split()[:6])
            if short not in seen_titles:
                seen_titles.add(short)
                deduped_items.append(it)

        new_count = 0
        for item in deduped_items:
            if not item["title"].strip():
                continue

            item_id = make_id(item["title"])
            if item_id in sent_ids:
                continue

            pass_filter, reason = should_send(item["title"], item["summary"])
            if not pass_filter:
                print(f"  ⏭ ข้าม: {item['title'][:55]}")
                sent_ids.add(item_id)
                continue
            print(f"  📥 [{reason}] {item['title'][:55]}")

            analysis = analyze_with_gpt(item)
            if not analysis:
                continue

            send_to_discord(item, analysis)

            sent_ids.add(item_id)
            new_count += 1
            time.sleep(2)

        save_sent_ids(sent_ids)
        print(f"  ✔ ส่ง {new_count} ข่าว | รอ {CHECK_INTERVAL_SEC // 60} นาที...")
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()

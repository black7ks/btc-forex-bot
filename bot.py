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


# ─── FETCH: ราคาทองคำ real-time ──────────────────────────────────────────────
def fetch_gold_price() -> str:
    """ดึงราคาทองคำปัจจุบันจาก public API"""
    try:
        # ใช้ metals.live — ฟรี ไม่ต้อง key
        resp = requests.get(
            "https://api.metals.live/v1/spot/gold",
            timeout=8
        )
        if resp.status_code == 200:
            data = resp.json()
            price = data.get("price") or data.get("gold")
            if price:
                return f"${float(price):,.2f}"
    except Exception:
        pass

    # fallback: frankfurter (ใช้ XAU ต่อ USD)
    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest?from=XAU&to=USD",
            timeout=8
        )
        if resp.status_code == 200:
            rate = resp.json().get("rates", {}).get("USD")
            if rate:
                return f"${float(rate):,.2f}"
    except Exception:
        pass

    return "N/A (ไม่สามารถดึงราคาได้)"


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
    """
    คืนค่า (ส่งไหม, เหตุผล)
    ส่งถ้าเข้าเงื่อนไขใดเงื่อนไขหนึ่ง:
      1. ข่าวทรัมป์
      2. ข่าวทองคำโดยตรง
      3. ข่าว USD ระดับกลาง-สูง ที่กระทบทอง
    """
    text = (title + " " + summary).lower()

    # เงื่อนไข 1: ข่าวทรัมป์
    if any(kw in text for kw in TRUMP_KEYWORDS):
        return True, "ทรัมป์"

    # เงื่อนไข 2: ข่าวทองคำโดยตรง
    if any(kw in text for kw in GOLD_KEYWORDS):
        return True, "ทองคำ"

    # เงื่อนไข 3: USD ระดับกลาง-สูง (ต้องมีอย่างน้อย 2 keyword)
    usd_hits = sum(1 for kw in USD_MEDIUM_HIGH if kw in text)
    if usd_hits >= 2:
        return True, f"USD ({usd_hits} hits)"

    return False, ""


# ─── ANALYZE via OpenAI ───────────────────────────────────────────────────────
def analyze_with_gpt(item: dict, gold_price: str) -> dict | None:
    prompt = f"""คุณคือนักวิเคราะห์ตลาดทองคำและ Forex มืออาชีพ เขียนบทวิเคราะห์ภาษาไทยให้นักลงทุนในชุมชน BTC Better Together

ราคาทองคำ XAU/USD ปัจจุบัน: {gold_price}
(ใช้ราคานี้เป็นฐานในการวิเคราะห์แนวรับ/แนวต้านจริง)

ข่าวต้นฉบับ:
หัวข้อ: {item['title']}
รายละเอียด: {item['summary'][:1000]}

โฟกัสหลัก: วิเคราะห์ว่าข่าวนี้กระทบ **ราคาทองคำ (XAU/USD)** อย่างไร
- ข่าว Fed / ดอกเบี้ย / เงินเฟ้อ → กระทบ real yield → กระทบทอง
- ข่าวทรัมป์ / ภูมิรัฐศาสตร์ / สงคราม → กระทบ safe haven → กระทบทอง
- ข่าวเศรษฐกิจสหรัฐฯ → กระทบ USD → กระทบทอง

ตอบเป็น JSON เท่านั้น:
{{
  "emoji": "emoji 1 ตัว (🥇 ทอง, 🏦 Fed, ⚠️ ภูมิรัฐศาสตร์, 📈 เศรษฐกิจ, 🇺🇸 ทรัมป์)",
  "title_th": "หัวข้อภาษาไทย กระชับ ไม่เกิน 65 ตัวอักษร",
  "summary_th": "สรุป 2 ประโยค ว่าเกิดอะไรขึ้น",
  "gold_impact": "วิเคราะห์ผลต่อทองคำ 2 ประโยค อ้างอิงราคาปัจจุบัน {gold_price} และบอกทิศทางที่คาดว่าจะเกิดขึ้น",
  "action": ["แนวรับ/ต้านที่ใกล้เคียงราคาปัจจุบัน", "โอกาส/ความเสี่ยงที่ต้องระวัง", "สัญญาณที่ควรจับตา"],
  "gold_direction": "GOLD_UP หรือ GOLD_DOWN หรือ NEUTRAL",
  "usd_direction": "USD_UP หรือ USD_DOWN หรือ NEUTRAL"
}}

ตอบ JSON เท่านั้น ห้ามมีข้อความอื่น"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
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
GOLD_COLOR = {
    "GOLD_UP":   0xF1C40F,   # ทอง  — ราคาทองขึ้น
    "GOLD_DOWN": 0xE74C3C,   # แดง  — ราคาทองลง
    "NEUTRAL":   0x95A5A6,   # เทา  — ทรงตัว
}

def send_to_discord(item: dict, analysis: dict):
    gold_dir  = analysis.get("gold_direction", "NEUTRAL")
    usd_dir   = analysis.get("usd_direction", "NEUTRAL")
    color     = GOLD_COLOR.get(gold_dir, 0x95A5A6)
    now_th    = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    # แปลง action เป็น bullet list (รองรับทั้ง string และ list จาก GPT)
    action_raw = analysis.get("action", "")
    if isinstance(action_raw, list):
        action_text = "\n".join(f"• {a.lstrip('•-').strip()}" for a in action_raw if a)
    elif isinstance(action_raw, str) and action_raw:
        lines = [l.strip() for l in action_raw.split("\n") if l.strip()]
        action_text = "\n".join(f"• {l.lstrip('•-').strip()}" for l in lines)
    else:
        action_text = "-"

    # Badge ทิศทาง
    gold_badge = {"GOLD_UP": "🟡 ทองขึ้น", "GOLD_DOWN": "🔴 ทองลง", "NEUTRAL": "⚪ ทรงตัว"}.get(gold_dir, "⚪")
    usd_badge  = {"USD_UP": "💚 USD แข็ง", "USD_DOWN": "🔴 USD อ่อน", "NEUTRAL": "⚪ ทรงตัว"}.get(usd_dir, "⚪")

    # ใช้ description แทน fields เพื่อให้มีช่องว่างระหว่างหัวข้อ อ่านง่ายขึ้น
    description = (
        f"**📋 สรุปข่าว**\n"
        f"{analysis.get('summary_th', '-')}\n"
        f"\n"
        f"**🥇 ผลต่อทองคำ**\n"
        f"{analysis.get('gold_impact', '-')}\n"
        f"\n"
        f"**💡 จับตามอง**\n"
        f"{action_text}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🥇 ทองคำ: **{gold_badge}**　　💵 USD: **{usd_badge}**\n"
        f"🔗 [อ่านต้นฉบับ]({item['link']})" if item.get("link") else ""
    )

    payload = {
        "username": "BTC Forex News 📡",
        "embeds": [{
            "title": f"{analysis.get('emoji','🥇')} {analysis.get('title_th', item['title'])}",
            "description": description,
            "color": color,

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

        # ดึงราคาทองคำปัจจุบันก่อนเลย
        gold_price = fetch_gold_price()
        print(f"  🥇 ราคาทอง XAU/USD: {gold_price}")

        all_items = fetch_forexfactory() + fetch_newsapi()

        # dedup ข่าวที่หัวข้อคล้ายกันมาก (ป้องกันเบิ้ล)
        seen_titles: set[str] = set()
        deduped_items = []
        for it in all_items:
            # ตัดคำสั้นๆ เหลือแค่ 6 คำแรก ใช้ fuzzy match แบบง่าย
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

            # ─── Filter ─────────────────────────────────────────────────────
            pass_filter, reason = should_send(item["title"], item["summary"])
            if not pass_filter:
                print(f"  ⏭ ข้าม: {item['title'][:55]}")
                sent_ids.add(item_id)
                continue
            print(f"  📥 [{reason}] {item['title'][:55]}")

            # ─── วิเคราะห์ด้วย GPT (ส่งราคาทองจริงเข้าไปด้วย) ────────────
            analysis = analyze_with_gpt(item, gold_price)
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

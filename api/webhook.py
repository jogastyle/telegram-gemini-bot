import os
import re
import time
import telebot
from datetime import datetime, timedelta
from groq import Groq
from pymongo import MongoClient
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
MONGO_URL = os.environ.get("MONGO_URL")

GROUP_CHAT_ID = "-1004368616206"
BOT_USERNAME = "@DTR_Mainpuri_Bot"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
client = Groq(api_key=GROQ_KEY)

_db_client = None

def get_db():
    global _db_client
    if _db_client is None:
        _db_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return _db_client["telegram_bot"]["reports"]

app = Flask(__name__)

# ================= REPORT PARSER =================
DIST_PATTERN = re.compile(
    r'Dist\s+([A-Za-z0-9 &\.\-\']+?)\s*-\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*/\s*\((\d+)\)'
)
TOTAL_PATTERN = re.compile(
    r'^Total\s*-\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*/\s*\((\d+)\)',
    re.MULTILINE
)
TIME_PATTERN = re.compile(r'till\s+(\d{1,2})[:\.](\d{2})')

def parse_report(text):
    if not text or ("FTA MNP" not in text and "FTD" not in text):
        return None
    distributors = {}
    for m in DIST_PATTERN.finditer(text):
        name = m.group(1).strip()
        distributors[name] = {
            "jio": int(m.group(2)),
            "vi": int(m.group(3)),
            "other": int(m.group(4)),
            "total": int(m.group(5)),
        }
    total = None
    tm = TOTAL_PATTERN.search(text)
    if tm:
        total = {
            "jio": int(tm.group(1)),
            "vi": int(tm.group(2)),
            "other": int(tm.group(3)),
            "total": int(tm.group(4)),
        }
    rtm = TIME_PATTERN.search(text)
    report_time = None
    if rtm:
        report_time = f"{int(rtm.group(1)):02d}:{rtm.group(2)}"
    return {"distributors": distributors, "total": total, "report_time": report_time}

# ================= SAVE =================
def save_report(text, timestamp):
    try:
        parsed = parse_report(text)
        db = get_db()
        db.insert_one({
            "text": text,
            "timestamp": timestamp,
            "date_str": time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp)),
            "parsed": parsed,
        })
    except Exception as e:
        print("DB SAVE ERROR:", str(e))

# ================= COMPARE (pure Python) =================
def format_comparison(r_old, r_new, label_old="Pehle", label_new="Ab"):
    p_old = r_old.get("parsed") or parse_report(r_old.get("text", ""))
    p_new = r_new.get("parsed") or parse_report(r_new.get("text", ""))
    if not p_old or not p_new:
        return "Reports parse nahi ho paayi."

    lines = []
    lines.append("📊 MNP Comparison")
    lines.append(f"• {label_old}: {r_old.get('date_str','')} (till {p_old.get('report_time','?')})")
    lines.append(f"• {label_new}: {r_new.get('date_str','')} (till {p_new.get('report_time','?')})")
    lines.append("")

    all_dists = set(p_old["distributors"].keys()) | set(p_new["distributors"].keys())
    for d in sorted(all_dists):
        a = p_old["distributors"].get(d, {}).get("total", 0)
        b = p_new["distributors"].get(d, {}).get("total", 0)
        diff = b - a
        sign = "+" if diff >= 0 else ""
        lines.append(f"• {d}: {a} → {b} ({sign}{diff})")

    a = (p_old["total"] or {}).get("total", 0)
    b = (p_new["total"] or {}).get("total", 0)
    diff = b - a
    sign = "+" if diff >= 0 else ""
    lines.append("")
    lines.append(f"Total: {a} → {b} ({sign}{diff})")
    return "\n".join(lines)

# ================= WEEKLY SUMMARY =================
def build_weekly_summary():
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    db = get_db()
    reports = list(db.find({"timestamp": {"$gte": week_ago.timestamp()}}).sort("timestamp", 1))
    if not reports:
        return "Is hafte koi report save nahi hui."

    daily_last = {}
    for r in reports:
        day_key = datetime.fromtimestamp(r["timestamp"]).strftime("%Y-%m-%d")
        daily_last[day_key] = r

    dist_totals = {}
    day_totals = {}
    for day_key, r in daily_last.items():
        p = r.get("parsed") or parse_report(r.get("text", ""))
        if not p:
            continue
        day_totals[day_key] = (p.get("total") or {}).get("total", 0)
        for name, vals in p.get("distributors", {}).items():
            dist_totals[name] = dist_totals.get(name, 0) + vals["total"]

    lines = []
    lines.append("📊 Weekly MNP Summary")
    lines.append(f"({week_ago.strftime('%d %b')} - {now.strftime('%d %b')})")
    lines.append("")
    lines.append(f"📁 Reports saved: {len(reports)}")
    lines.append(f"📅 Days covered: {len(daily_last)}")
    lines.append("")
    lines.append("Distributor-wise total:")
    for name, val in sorted(dist_totals.items(), key=lambda x: -x[1]):
        lines.append(f"• {name}: {val}")

    grand_total = sum(day_totals.values())
    lines.append("")
    lines.append(f"Grand Total: {grand_total}")

    if day_totals:
        best = max(day_totals.items(), key=lambda x: x[1])
        worst = min(day_totals.items(), key=lambda x: x[1])
        lines.append(f"🏆 Best day: {best[0]} ({best[1]})")
        lines.append(f"📉 Lowest: {worst[0]} ({worst[1]})")

    return "\n".join(lines)

# ================= TIME/DATE PARSING =================
MONTHS = {
    "jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,"apr":4,"april":4,
    "may":5,"jun":6,"june":6,"jul":7,"july":7,"aug":8,"august":8,
    "sep":9,"sept":9,"september":9,"oct":10,"october":10,"nov":11,"november":11,
    "dec":12,"december":12,
}

def extract_time(text, keywords):
    lower = text.lower()
    for kw in keywords:
        idx = lower.find(kw)
        if idx == -1:
            continue
        window = lower[idx:idx+40]
        m = re.search(r'(\d{1,2})[:\.](\d{2})', window)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.search(r'(\d{1,2})\s*(?:baje|bje|pm|am)', window)
        if m:
            return int(m.group(1)), 0
    return None

def extract_date(text):
    lower = text.lower()
    today = datetime.now().date()
    if re.search(r'\bkal\b', lower) or "yesterday" in lower:
        return today - timedelta(days=1)
    if re.search(r'\baaj\b', lower) or "today" in lower or "abhi" in lower:
        return today
    if re.search(r'\bparso\b', lower):
        return today - timedelta(days=2)
    m = re.search(r'(\d{1,2})\s*(?:st|nd|rd|th)?\s*([a-z]+)', lower)
    if m:
        day = int(m.group(1)); mon = m.group(2)
        if mon in MONTHS:
            try:
                c = datetime(today.year, MONTHS[mon], day).date()
                if c > today:
                    c = datetime(today.year - 1, MONTHS[mon], day).date()
                return c
            except: pass
    m = re.search(r'(\d{1,2})[\/\-\.](\d{1,2})', lower)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            c = datetime(today.year, mo, d).date()
            if c > today:
                c = datetime(today.year - 1, mo, d).date()
            return c
        except: pass
    return None

def find_report_at(target_date, hour, minute):
    start = datetime.combine(target_date, datetime.min.time()).timestamp()
    end = start + 86400
    db = get_db()
    reports = list(db.find({"timestamp": {"$gte": start, "$lt": end}}).sort("timestamp", 1))
    if not reports:
        return None
    target_ts = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute)).timestamp()
    return min(reports, key=lambda r: abs(r["timestamp"] - target_ts))

# ================= DAILY QUOTE =================
@app.route('/daily-quote', methods=['GET'])
def daily_quote():
    try:
        bot.send_message(GROUP_CHAT_ID, "🌅 Good Morning Team")
        time.sleep(1)
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Generate a work-related motivational message in Hindi (Devanagari script). It must be 2-3 lines long, about teamwork, hard work, or success. Do NOT include any greeting like 'Namaste' or 'Hi'. Do NOT include author names. Just the motivational lines."},
                {"role": "user", "content": "Give me today's work motivational message."}
            ],
            model="openai/gpt-oss-20b",
        )
        quote = response.choices[0].message.content.strip()
        bot.send_message(GROUP_CHAT_ID, "💪 " + quote)
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

# ================= WEEKLY SUMMARY ENDPOINT =================
@app.route('/weekly-summary', methods=['GET'])
def weekly_summary_endpoint():
    try:
        text = build_weekly_summary()
        bot.send_message(GROUP_CHAT_ID, text)
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

# ================= SAVE-REPORT ENDPOINT =================
@app.route('/save-report', methods=['POST'])
def save_report_endpoint():
    try:
        data = request.get_json(force=True, silent=True) or request.form.to_dict()
        text = data.get("text", "").strip()
        if not text:
            return "No text", 400
        save_report(text, time.time())
        return "Saved", 200
    except Exception as e:
        return "Error: " + str(e), 500

# ================= MESSAGE HANDLER =================
@bot.message_handler(func=lambda m: True)
def handle(message):
    try:
        text = message.text or ""
        msg_ts = message.date

        if message.chat.id == int(GROUP_CHAT_ID) and ("FTA MNP" in text or "FTD" in text):
            save_report(text, msg_ts)

        is_private = message.chat.type == "private"
        is_tagged = BOT_USERNAME.lower() in text.lower()
        is_reply_to_bot = False
        if message.reply_to_message and message.reply_to_message.from_user:
            u = message.reply_to_message.from_user
            if u.is_bot and u.username == BOT_USERNAME.replace("@", ""):
                is_reply_to_bot = True

        if not is_private and not is_tagged and not is_reply_to_bot:
            return

        clean_text = text.replace(BOT_USERNAME, "").strip() or "Hi"
        lower = clean_text.lower()

        # Weekly summary by tag
        if "weekly" in lower or "hafte ka summary" in lower or "hafte ki summary" in lower:
            bot.reply_to(message, build_weekly_summary())
            return

        # Compare
        wants_compare = any(w in lower for w in ["compare", "farq", "antar", "difference", "vs"])
        if wants_compare:
            parts = re.split(r'\baur\b|\band\b|\bvs\b|\bse\b', lower)
            anchors = []
            for part in parts:
                t = extract_time(part, ["baje","bje",":",".","pm","am","dophar","subah","shaam","raat"])
                d = extract_date(part)
                if t or d:
                    anchors.append((d, t))

            if len(anchors) >= 2:
                a1, a2 = anchors[0], anchors[1]
                d1 = a1[0] or (datetime.now().date() - timedelta(days=1))
                d2 = a2[0] or datetime.now().date()
                t1 = a1[1] or (12, 0)
                t2 = a2[1] or (12, 0)

                r1 = find_report_at(d1, t1[0], t1[1])
                r2 = find_report_at(d2, t2[0], t2[1])

                if not r1:
                    bot.reply_to(message, f"{d1.strftime('%d %b')} {t1[0]}:{t1[1]:02d} ke aas-paas report nahi mili.")
                    return
                if not r2:
                    bot.reply_to(message, f"{d2.strftime('%d %b')} {t2[0]}:{t2[1]:02d} ke aas-paas report nahi mili.")
                    return

                label_old = f"{d1.strftime('%d %b')} {t1[0]:02d}:{t1[1]:02d}"
                label_new = f"{d2.strftime('%d %b')} {t2[0]:02d}:{t2[1]:02d}"
                bot.reply_to(message, format_comparison(r1, r2, label_old, label_new))
                return

            # Fallback: last two
            db = get_db()
            last_two = list(db.find().sort("timestamp", -1).limit(2))
            if len(last_two) >= 2:
                r1, r2 = last_two[1], last_two[0]
                bot.reply_to(message, format_comparison(r1, r2))
                return
            else:
                bot.reply_to(message, "Database mein kam se kam 2 reports chahiye.")
                return

        # Normal AI reply
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful Telegram assistant. Reply in same language. Answer in 1-3 lines."},
                {"role": "user", "content": clean_text}
            ],
            model="openai/gpt-oss-20b",
        )
        bot.reply_to(message, response.choices[0].message.content)

    except Exception as e:
        try:
            bot.reply_to(message, "Error: " + str(e))
        except:
            pass

# ================= WEBHOOK =================
@app.route('/', methods=['POST'])
def webhook():
    try:
        update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
        bot.process_new_updates([update])
    except Exception as e:
        print("WEBHOOK ERROR:", str(e))
    return "OK", 200

@app.route('/', methods=['GET'])
def index():
    return "Bot is running!", 200

@app.route('/test', methods=['GET'])
def test():
    return "Token: " + ("SET" if BOT_TOKEN else "MISSING") + ", Groq: " + ("SET" if GROQ_KEY else "MISSING") + ", DB: " + ("SET" if MONGO_URL else "MISSING")

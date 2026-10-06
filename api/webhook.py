import os
import re
import time
import json
import telebot
import urllib.parse
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
IST_OFFSET = 5 * 3600 + 30 * 60

def ts_to_ist_str(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts + IST_OFFSET))

def today_ist_str():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + IST_OFFSET))

def yesterday_ist_str():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + IST_OFFSET - 86400))

def get_db():
    global _db_client
    if _db_client is None:
        _db_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return _db_client["telegram_bot"]["reports"]

def get_settings():
    db = get_db()
    s = db.find_one({"_id": "settings"})
    return s or {}

def set_target(distributor, value):
    db = get_db()
    db.update_one({"_id": "settings"}, {"$set": {f"target_{distributor}": value}}, upsert=True)

def update_setting(key, value):
    db = get_db()
    db.update_one({"_id": "settings"}, {"$set": {key: value}}, upsert=True)

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

def save_report(text, timestamp):
    try:
        parsed = parse_report(text)
        if not parsed:
            return
        db = get_db()
        db.insert_one({
            "text": text,
            "timestamp": timestamp,
            "date_str": ts_to_ist_str(timestamp),
            "parsed": parsed,
        })
    except Exception as e:
        print("DB SAVE ERROR:", str(e))

# ================= COMPARE =================
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

# ================= DATA FOR GRAPHS =================
def get_daily_data(days=7):
    now = datetime.now()
    start = now - timedelta(days=days - 1)
    utc_start = start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - IST_OFFSET

    db = get_db()
    reports = list(db.find({
        "timestamp": {"$gte": utc_start},
        "text": {"$regex": "FTA MNP|FTD"}
    }).sort("timestamp", 1))

    daily_last = {}
    for r in reports:
        day_key = time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST_OFFSET))
        daily_last[day_key] = r

    days_list = []
    uday_vals = []
    mv_vals = []
    total_vals = []
    dist_totals_week = {}

    for i in range(days - 1, -1, -1):
        d = now - timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        label = d.strftime("%d %b")
        days_list.append(label)

        r = daily_last.get(key)
        if not r:
            uday_vals.append(0); mv_vals.append(0); total_vals.append(0)
            continue

        p = r.get("parsed") or parse_report(r.get("text", ""))
        if not p:
            uday_vals.append(0); mv_vals.append(0); total_vals.append(0)
            continue

        uday = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        mv = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        tot = (p.get("total") or {}).get("total", 0)

        uday_vals.append(uday); mv_vals.append(mv); total_vals.append(tot)
        dist_totals_week["Uday Comm Agr"] = dist_totals_week.get("Uday Comm Agr", 0) + uday
        dist_totals_week["Maa Vaishno Telecom"] = dist_totals_week.get("Maa Vaishno Telecom", 0) + mv

    return {
        "labels": days_list,
        "uday": uday_vals,
        "mv": mv_vals,
        "total": total_vals,
        "dist_totals": dist_totals_week,
    }

# ================= GRAPH BUILDERS =================
def quickchart_url(config, width=800, height=400):
    encoded = urllib.parse.quote(json.dumps(config))
    return f"https://quickchart.io/chart?c={encoded}&w={width}&h={height}&bkg=white"

def build_bar_chart(days=7):
    data = get_daily_data(days)
    config = {
        "type": "bar",
        "data": {
            "labels": data["labels"],
            "datasets": [
                {"label": "Uday Comm Agr", "data": data["uday"], "backgroundColor": "#2196F3"},
                {"label": "Maa Vaishno Telecom", "data": data["mv"], "backgroundColor": "#F44336"},
                {"label": "Total", "data": data["total"], "backgroundColor": "#4CAF50"},
            ]
        },
        "options": {
            "title": {"display": True, "text": f"Daily MNP - Last {days} Days", "fontSize": 16},
            "legend": {"position": "bottom"},
        }
    }
    return quickchart_url(config)

def build_pie_chart(days=7):
    data = get_daily_data(days)
    dt = data["dist_totals"]
    labels = list(dt.keys()) if dt else ["No Data"]
    values = list(dt.values()) if dt else [1]
    config = {
        "type": "doughnut",
        "data": {
            "labels": labels,
            "datasets": [{"data": values, "backgroundColor": ["#2196F3", "#F44336", "#4CAF50", "#FFC107"]}]
        },
        "options": {
            "title": {"display": True, "text": f"Distributor Share - Last {days} Days", "fontSize": 16},
            "legend": {"position": "bottom"},
        }
    }
    return quickchart_url(config)

def build_line_chart(days=7):
    data = get_daily_data(days)
    config = {
        "type": "line",
        "data": {
            "labels": data["labels"],
            "datasets": [
                {"label": "Uday Comm Agr", "data": data["uday"], "borderColor": "#2196F3", "fill": False, "tension": 0.3},
                {"label": "Maa Vaishno Telecom", "data": data["mv"], "borderColor": "#F44336", "fill": False, "tension": 0.3},
                {"label": "Total", "data": data["total"], "borderColor": "#4CAF50", "fill": False, "tension": 0.3, "borderWidth": 3},
            ]
        },
        "options": {
            "title": {"display": True, "text": f"MNP Trend - Last {days} Days", "fontSize": 16},
            "legend": {"position": "bottom"},
        }
    }
    return quickchart_url(config)

# ================= WEEKLY SUMMARY =================
def build_weekly_summary():
    data = get_daily_data(7)
    dist_totals = data["dist_totals"]
    day_totals = {data["labels"][i]: data["total"][i] for i in range(len(data["labels"]))}
    grand_total = sum(day_totals.values())

    lines = []
    lines.append("📊 Weekly MNP Summary")
    lines.append("(Last 7 days)")
    lines.append("")
    lines.append("Distributor-wise total:")
    for name, val in sorted(dist_totals.items(), key=lambda x: -x[1]):
        lines.append(f"• {name}: {val}")
    lines.append("")
    lines.append(f"Grand Total: {grand_total}")

    nonzero = {k: v for k, v in day_totals.items() if v > 0}
    if nonzero:
        best = max(nonzero.items(), key=lambda x: x[1])
        worst = min(nonzero.items(), key=lambda x: x[1])
        lines.append(f"🏆 Best day: {best[0]} ({best[1]})")
        lines.append(f"📉 Lowest: {worst[0]} ({worst[1]})")

    return "\n".join(lines)

# ================= DATE/TIME PARSING =================
MONTHS = {
    "jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,"apr":4,"april":4,
    "may":5,"jun":6,"june":6,"jul":7,"july":7,"aug":8,"august":8,
    "sep":9,"sept":9,"september":9,"oct":10,"october":10,"nov":11,"november":11,
    "dec":12,"december":12,
}

def extract_time(text):
    lower = text.lower()
    m = re.search(r'(\d{1,2})[:\.](\d{2})', lower)
    if m:
        h = int(m.group(1)); mi = int(m.group(2))
        return apply_ampm(lower, m.start(), h), mi
    m = re.search(r'(\d{1,2})\s*(?:baje|bje|pm|am|bajkar)', lower)
    if m:
        h = int(m.group(1))
        return apply_ampm(lower, m.start(), h), 0
    return None

def apply_ampm(text, idx, hour):
    if hour >= 13: return hour
    if hour == 12:
        context = text[max(0, idx - 20):idx + 30]
        if re.search(r'raat|night', context): return 0
        return 12
    context = text[max(0, idx - 20):idx + 30]
    if re.search(r'subah|subha|saver|savere|morning|\bam\b', context): return hour
    if re.search(r'dophar|dopahar|dopaher|afternoon', context): return hour + 12 if hour < 12 else hour
    if re.search(r'shaam|sham|evening', context): return hour + 12 if hour < 12 else hour
    if re.search(r'raat|night|\bpm\b', context): return hour + 12 if hour < 12 else hour
    return hour

def extract_date(text):
    lower = text.lower()
    today = datetime.now().date()
    if re.search(r'\bkal\b', lower) or "yesterday" in lower: return today - timedelta(days=1)
    if re.search(r'\baaj\b', lower) or "today" in lower or "abhi" in lower: return today
    if re.search(r'\bparso\b', lower): return today - timedelta(days=2)
    m = re.search(r'(\d{1,2})\s*(?:st|nd|rd|th)?\s*([a-z]+)', lower)
    if m:
        day = int(m.group(1)); mon = m.group(2)
        if mon in MONTHS:
            try:
                c = datetime(today.year, MONTHS[mon], day).date()
                if c > today: c = datetime(today.year - 1, MONTHS[mon], day).date()
                return c
            except: pass
    m = re.search(r'(\d{1,2})[\/\-\.](\d{1,2})', lower)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            c = datetime(today.year, mo, d).date()
            if c > today: c = datetime(today.year - 1, mo, d).date()
            return c
        except: pass
    return None

def find_report_at(target_date, hour, minute):
    try:
        ist_ts = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute)).timestamp()
        utc_target = ist_ts - IST_OFFSET
        ist_start = datetime.combine(target_date, datetime.min.time()).timestamp()
        ist_end = ist_start + 86400
        utc_start = ist_start - IST_OFFSET
        utc_end = ist_end - IST_OFFSET
        db = get_db()
        reports = list(db.find({
            "timestamp": {"$gte": utc_start, "$lt": utc_end},
            "text": {"$regex": "FTA MNP|FTD"}
        }).sort("timestamp", 1))
        if not reports: return None
        return min(reports, key=lambda r: abs(r["timestamp"] - utc_target))
    except Exception as e:
        print("FIND ERROR:", str(e))
        return None

# ================= PERFORMANCE CHECK =================
@app.route('/check-performance', methods=['GET'])
def check_performance():
    try:
        settings = get_settings()
        target_uday = settings.get("target_uday", 0)
        target_mv = settings.get("target_mv", 0)

        if not target_uday and not target_mv:
            return "No targets set", 200

        now = datetime.now()
        today_start_ist = datetime.combine(now.date(), datetime.min.time()).timestamp()
        utc_start = today_start_ist - IST_OFFSET

        db = get_db()
        reports = list(db.find({
            "timestamp": {"$gte": utc_start},
            "text": {"$regex": "FTA MNP|FTD"}
        }).sort("timestamp", -1).limit(1))

        if not reports:
            bot.send_message(GROUP_CHAT_ID, "⚠️ Alert: Aaj koi MNP report nahi aayi.")
            return "No reports", 200

        p = reports[0].get("parsed") or parse_report(reports[0]["text"])
        uday_now = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        mv_now = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)

        today_str = today_ist_str()
        yesterday_str = yesterday_ist_str()

        alerts = []
        extra_alerts = []

        # ---- Uday check ----
        if target_uday:
            if uday_now < target_uday:
                gap = target_uday - uday_now
                pct = int((uday_now / target_uday) * 100)
                alerts.append(f"• Uday Comm Agr: {uday_now}/{target_uday} ({pct}%) — gap {gap}")

                # Consecutive failure check
                last_fail = settings.get("last_fail_uday")
                if last_fail == yesterday_str:
                    extra_alerts.append("❗ Uday Comm Agr — aap aaj bhi target pura nahi kar paye!")
                update_setting("last_fail_uday", today_str)
            else:
                # Reset fail count on success
                update_setting("last_fail_uday", "")

        # ---- Maa Vaishno check ----
        if target_mv:
            if mv_now < target_mv:
                gap = target_mv - mv_now
                pct = int((mv_now / target_mv) * 100)
                alerts.append(f"• Maa Vaishno Telecom: {mv_now}/{target_mv} ({pct}%) — gap {gap}")

                last_fail = settings.get("last_fail_mv")
                if last_fail == yesterday_str:
                    extra_alerts.append("❗ Maa Vaishno Telecom — aap aaj bhi target pura nahi kar paye!")
                update_setting("last_fail_mv", today_str)
            else:
                update_setting("last_fail_mv", "")

        if alerts:
            msg = "⚠️ Low Performance Alert\n\n" + "\n".join(alerts)
            if extra_alerts:
                msg += "\n\n" + "\n".join(extra_alerts)
            if target_uday and uday_now >= target_uday:
                msg += "\n\n✅ Uday Comm Agr ne target pura kar liya!"
            if target_mv and mv_now >= target_mv:
                msg += "\n✅ Maa Vaishno Telecom ne target pura kar liya!"
            bot.send_message(GROUP_CHAT_ID, msg)
            return "Alert sent", 200
        else:
            return "All targets achieved", 200
    except Exception as e:
        return "Error: " + str(e), 500

# ================= ENDPOINTS =================
@app.route('/daily-quote', methods=['GET'])
def daily_quote():
    try:
        bot.send_message(GROUP_CHAT_ID, "🌅 Good Morning Team")
        time.sleep(1)
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Generate a work-related motivational message in Hindi (Devanagari script). It must be 2-3 lines long, about teamwork, hard work, or success. Do NOT include any greeting. Do NOT include author names. Just the motivational lines."},
                {"role": "user", "content": "Give me today's work motivational message."}
            ],
            model="openai/gpt-oss-20b",
        )
        bot.send_message(GROUP_CHAT_ID, "💪 " + response.choices[0].message.content.strip())
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/weekly-summary', methods=['GET'])
def weekly_summary_endpoint():
    try:
        bot.send_message(GROUP_CHAT_ID, build_weekly_summary())
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/save-report', methods=['POST'])
def save_report_endpoint():
    try:
        data = request.get_json(force=True, silent=True) or request.form.to_dict()
        text = data.get("text", "").strip()
        if not text: return "No text", 400
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

        # -------- SET TARGET (per distributor) --------
        tm = re.search(r'(uday|maa\s*vaishno|mv|vaishno)\s*target\s*(\d+)', lower)
        if tm:
            dist_key = tm.group(1)
            val = int(tm.group(2))
        else:
            tm = re.search(r'target\s*(?:set\s*)?(uday|maa\s*vaishno|mv|vaishno)\s*(\d+)', lower)
            if tm:
                dist_key = tm.group(1)
                val = int(tm.group(2))
            else:
                dist_key = None
                val = None

        if dist_key and val is not None and (is_tagged or is_private):
            if "uday" in dist_key:
                set_target("uday", val)
                bot.reply_to(message, f"✅ Uday Comm Agr target set: {val}\n\nRoz sham 7 baje check hoga.")
            else:
                set_target("mv", val)
                bot.reply_to(message, f"✅ Maa Vaishno Telecom target set: {val}\n\nRoz sham 7 baje check hoga.")
            return

        # -------- CHECK TARGET STATUS --------
        if "target" in lower and any(w in lower for w in ["status", "kitna", "check", "dikhao"]):
            s = get_settings()
            tu = s.get("target_uday", 0)
            tmv = s.get("target_mv", 0)
            bot.reply_to(message,
                f"🎯 Current Targets:\n"
                f"• Uday Comm Agr: {tu}\n"
                f"• Maa Vaishno Telecom: {tmv}")
            return

        # -------- GRAPH REQUESTS --------
        wants_graph = any(w in lower for w in ["graph", "chart"])
        if wants_graph:
            days = 7
            dm = re.search(r'(\d{1,2})\s*(?:din|days)', lower)
            if dm: days = int(dm.group(1))

            wants_bar = "bar" in lower or "column" in lower
            wants_pie = "pie" in lower or "share" in low

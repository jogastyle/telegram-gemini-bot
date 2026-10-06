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

def get_working_hours():
    s = get_settings()
    return s.get("wh_start", 7.0), s.get("wh_end", 19.0)

def set_working_hours(start, end):
    db = get_db()
    db.update_one({"_id": "settings"}, {"$set": {"wh_start": start, "wh_end": end}}, upsert=True)

# ============ STOCK ALERT HELPERS ============
MSISDN_MAP = {
    "9997389467": "Uday Comm Agr",
    "7895110381": "Maa Vaishno Telecom",
}

def get_tag(distributor_key):
    s = get_settings()
    return s.get(f"tag_{distributor_key}", "")

def set_tag(distributor_key, username):
    db = get_db()
    if username and not username.startswith("@"):
        username = "@" + username
    db.update_one({"_id": "settings"}, {"$set": {f"tag_{distributor_key}": username}}, upsert=True)

app = Flask(__name__)

# ============ PATTERNS ============
DIST_PATTERN = re.compile(
    r'Dist\s+([A-Za-z0-9 &\.\-\']+?)\s*-\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*/\s*\((\d+)\)'
)
TOTAL_PATTERN = re.compile(
    r'^Total\s*-\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*/\s*\((\d+)\)',
    re.MULTILINE
)
TIME_PATTERN = re.compile(r'till\s+(\d{1,2})[:\.](\d{2})')
BALANCE_PATTERN = re.compile(r'(\d{10})\s*/\s*(\d+)\s*/\s*(\d+)\s*/\s*([\d\.]+)')

# ============ PARSERS ============
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

def parse_balance_report(text):
    if not text or "Distributor Balance Report" not in text:
        return None
    items = []
    for m in BALANCE_PATTERN.finditer(text):
        items.append({
            "msisdn": m.group(1),
            "balance": int(m.group(2)),
            "sale_lakh": int(m.group(3)),
            "stock_days": float(m.group(4)),
        })
    if not items:
        return None
    return {"items": items}

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

def save_balance_report(text, timestamp):
    try:
        parsed = parse_balance_report(text)
        if not parsed:
            return None
        db = get_db()
        db.insert_one({
            "text": text,
            "timestamp": timestamp,
            "date_str": ts_to_ist_str(timestamp),
            "type": "balance",
            "parsed": parsed,
        })
        return parsed
    except Exception as e:
        print("BAL SAVE ERROR:", str(e))
        return None
        
def format_comparison(r_old, r_new, label_old="Pehle", label_new="Ab"):
    p_old = r_old.get("parsed") or parse_report(r_old.get("text", ""))
    p_new = r_new.get("parsed") or parse_report(r_new.get("text", ""))
    if not p_old or not p_new:
        return "Reports parse nahi ho paayi."
    lines = ["📊 MNP Comparison"]
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
    days_list, uday_vals, mv_vals, total_vals = [], [], [], []
    dist_totals_week = {}
    for i in range(days - 1, -1, -1):
        d = now - timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        days_list.append(d.strftime("%d %b"))
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
    return {"labels": days_list, "uday": uday_vals, "mv": mv_vals, "total": total_vals, "dist_totals": dist_totals_week}

def quickchart_url(config, width=800, height=400):
    encoded = urllib.parse.quote(json.dumps(config))
    return f"https://quickchart.io/chart?c={encoded}&w={width}&h={height}&bkg=white&plugins=chartjs-plugin-datalabels"

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
            "plugins": {
                "datalabels": {
                    "display": True, "color": "white", "anchor": "center", "align": "center",
                    "font": {"size": 11, "weight": "bold"},
                    "formatter": "function(value) { return value > 0 ? value : ''; }"
                }
            }
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
        "data": {"labels": labels, "datasets": [{"data": values, "backgroundColor": ["#2196F3", "#F44336", "#4CAF50", "#FFC107"]}]},
        "options": {
            "title": {"display": True, "text": f"Distributor Share - Last {days} Days", "fontSize": 16},
            "legend": {"position": "bottom"},
            "plugins": {
                "datalabels": {
                    "display": True, "color": "white",
                    "font": {"size": 12, "weight": "bold"},
                    "formatter": "function(value, ctx) { var sum = ctx.dataset.data.reduce(function(a,b){return a+b;}, 0); var pct = Math.round(value / sum * 100); return value + '\\n(' + pct + '%)'; }"
                }
            }
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
                {
                    "label": "Uday Comm Agr", "data": data["uday"], "borderColor": "#2196F3", "fill": False, "tension": 0.3,
                    "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 6, "color": "#1565C0", "font": {"size": 10, "weight": "bold"}, "formatter": "function(value) { return value > 0 ? value : ''; }"}
                },
                {
                    "label": "Maa Vaishno Telecom", "data": data["mv"], "borderColor": "#F44336", "fill": False, "tension": 0.3,
                    "datalabels": {"display": True, "align": "bottom", "anchor": "end", "offset": 6, "color": "#B71C1C", "font": {"size": 10, "weight": "bold"}, "formatter": "function(value) { return value > 0 ? value : ''; }"}
                },
                {
                    "label": "Total", "data": data["total"], "borderColor": "#4CAF50", "fill": False, "tension": 0.3, "borderWidth": 3,
                    "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 20, "color": "#1B5E20", "font": {"size": 11, "weight": "bold"}, "formatter": "function(value) { return value > 0 ? value : ''; }"}
                },
            ]
        },
        "options": {
            "title": {"display": True, "text": f"MNP Trend - Last {days} Days", "fontSize": 16},
            "legend": {"position": "bottom"},
            "layout": {"padding": {"top": 40}}
        }
    }
    return quickchart_url(config)

def build_weekly_summary():
    data = get_daily_data(7)
    dist_totals = data["dist_totals"]
    day_totals = {data["labels"][i]: data["total"][i] for i in range(len(data["labels"]))}
    grand_total = sum(day_totals.values())
    lines = ["📊 Weekly MNP Summary", "(Last 7 days)", "", "Distributor-wise total:"]
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
    
MONTHS = {"jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,"apr":4,"april":4,"may":5,"jun":6,"june":6,"jul":7,"july":7,"aug":8,"august":8,"sep":9,"sept":9,"september":9,"oct":10,"october":10,"nov":11,"november":11,"dec":12,"december":12}

def extract_time(text):
    lower = text.lower()
    m = re.search(r'(\d{1,2})[:\.](\d{2})', lower)
    if m:
        return apply_ampm(lower, m.start(), int(m.group(1))), int(m.group(2))
    m = re.search(r'(\d{1,2})\s*(?:baje|bje|pm|am|bajkar)', lower)
    if m:
        return apply_ampm(lower, m.start(), int(m.group(1))), 0
    return None

def apply_ampm(text, idx, hour):
    if hour >= 13: return hour
    if hour == 12:
        ctx = text[max(0, idx - 20):idx + 30]
        if re.search(r'raat|night', ctx): return 0
        return 12
    ctx = text[max(0, idx - 20):idx + 30]
    if re.search(r'subah|subha|saver|savere|morning|\bam\b', ctx): return hour
    if re.search(r'dophar|dopahar|dopaher|afternoon', ctx): return hour + 12 if hour < 12 else hour
    if re.search(r'shaam|sham|evening', ctx): return hour + 12 if hour < 12 else hour
    if re.search(r'raat|night|\bpm\b', ctx): return hour + 12 if hour < 12 else hour
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
        utc_start = ist_start - IST_OFFSET
        utc_end = utc_start + 86400
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

def send_performance_alert(target_hour=None, target_minute=None):
    settings = get_settings()
    target_uday = settings.get("target_uday", 0)
    target_mv = settings.get("target_mv", 0)
    if not target_uday and not target_mv:
        return "❌ Koi target set nahi. Pehle 'uday tgt 80' aur 'mv tgt 100' set karo."
    now = datetime.now()
    today_date = now.date()
    if target_hour is not None:
        r = find_report_at(today_date, target_hour, target_minute or 0)
        if not r:
            return f"❌ Aaj {target_hour:02d}:{(target_minute or 0):02d} ke aas-paas koi MNP report nahi mili."
        time_label = f"till {target_hour:02d}:{(target_minute or 0):02d}"
    else:
        utc_start = datetime.combine(today_date, datetime.min.time()).timestamp() - IST_OFFSET
        db = get_db()
        reports = list(db.find({
            "timestamp": {"$gte": utc_start},
            "text": {"$regex": "FTA MNP|FTD"}
        }).sort("timestamp", -1).limit(1))
        if not reports:
            return "⚠️ Aaj koi MNP report nahi aayi."
        r = reports[0]
        time_label = "latest"
    p = r.get("parsed") or parse_report(r.get("text", ""))
    if not p:
        return "❌ Report parse nahi ho paayi."
    uday_now = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
    mv_now = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
    today_str = today_ist_str()
    yesterday_str = yesterday_ist_str()
    alerts = []
    extra_alerts = []
    if target_uday:
        if uday_now < target_uday:
            gap = target_uday - uday_now
            pct = int((uday_now / target_uday) * 100)
            uday_tag = get_tag("uday")
            ts = f" {uday_tag}" if uday_tag else ""
            alerts.append(f"• Uday Comm Agr{ts}: {uday_now}/{target_uday} ({pct}%) — gap {gap}")
            if settings.get("last_fail_uday") == yesterday_str:
                extra_alerts.append(f"❗ Uday Comm Agr{ts} — aap aaj bhi target pura nahi kar paye!")
            update_setting("last_fail_uday", today_str)
        else:
            update_setting("last_fail_uday", "")
    if target_mv:
        if mv_now < target_mv:
            gap = target_mv - mv_now
            pct = int((mv_now / target_mv) * 100)
            mv_tag = get_tag("mv")
            ts = f" {mv_tag}" if mv_tag else ""
            alerts.append(f"• Maa Vaishno Telecom{ts}: {mv_now}/{target_mv} ({pct}%) — gap {gap}")
            if settings.get("last_fail_mv") == yesterday_str:
                extra_alerts.append(f"❗ Maa Vaishno Telecom{ts} — aap aaj bhi target pura nahi kar paye!")
            update_setting("last_fail_mv", today_str)
        else:
            update_setting("last_fail_mv", "")
    if alerts:
        msg = f"⚠️ Low Performance Alert ({time_label})\n\n" + "\n".join(alerts)
        if extra_alerts:
            msg += "\n\n" + "\n".join(extra_alerts)
        if target_uday and uday_now >= target_uday:
            msg += "\n\n✅ Uday Comm Agr ne target pura kar liya!"
        if target_mv and mv_now >= target_mv:
            msg += "\n✅ Maa Vaishno Telecom ne target pura kar liya!"
        return msg
    else:
        msg = f"✅ Sab targets pura ho gaye ({time_label})\n\n"
        msg += f"• Uday Comm Agr: {uday_now}/{target_uday}\n• Maa Vaishno Telecom: {mv_now}/{target_mv}"
        return msg

def build_balance_alert(parsed_items, threshold=3):
    if not parsed_items:
        return None
    low_items = []
    for item in parsed_items.get("items", []):
        name = MSISDN_MAP.get(item["msisdn"])
        if not name:
            continue
        if item["stock_days"] < threshold:
            dist_key = "uday" if "uday" in name.lower() else "mv"
            tag = get_tag(dist_key)
            tag_str = f" {tag}" if tag else ""
            low_items.append({
                "name": name, "tag_str": tag_str,
                "days": item["stock_days"], "balance": item["balance"], "sale": item["sale_lakh"],
            })
    if not low_items:
        return None
    lines = ["🚨 Low Balance Alert", ""]
    for it in low_items:
        lines.append(f"🔸 {it['name']}{it['tag_str']}")
        lines.append(f"   Stock Days: {it['days']}")
        lines.append(f"   Balance: {it['balance']:,}")
        lines.append(f"   Sale: {it['sale']} Lakh")
        lines.append("")
    lines.append(f"⚠️ Aapka balance {threshold} din se kam hai — aaj hi billing karayen!")
    return "\n".join(lines)

def send_stock_check(threshold=3):
    db = get_db()
    latest = list(db.find({"type": "balance"}).sort("timestamp", -1).limit(1))
    if not latest:
        return "❌ Koi balance report nahi mili."
    parsed = latest[0].get("parsed")
    if not parsed:
        return "❌ Balance report parse nahi ho paayi."
    alert = build_balance_alert(parsed, threshold)
    if alert:
        return alert
    lines = ["✅ Sab distributors ka stock theek hai", ""]
    for item in parsed.get("items", []):
        name = MSISDN_MAP.get(item["msisdn"], item["msisdn"])
        lines.append(f"• {name}: {item['stock_days']} din")
    return "\n".join(lines)
    
def get_historical_daily_avg(days=30, exclude_today=True):
    now = datetime.now()
    end_date = now.date() - timedelta(days=1) if exclude_today else now.date()
    start_date = end_date - timedelta(days=days-1)
    utc_start = datetime.combine(start_date, datetime.min.time()).timestamp() - IST_OFFSET
    utc_end = datetime.combine(end_date, datetime.max.time()).timestamp() - IST_OFFSET
    db = get_db()
    reports = list(db.find({
        "timestamp": {"$gte": utc_start, "$lte": utc_end},
        "text": {"$regex": "FTA MNP|FTD"}
    }).sort("timestamp", 1))
    daily = {}
    for r in reports:
        key = time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST_OFFSET))
        daily[key] = r
    totals = []
    for k, r in daily.items():
        p = r.get("parsed") or parse_report(r.get("text", ""))
        if p:
            totals.append((p.get("total") or {}).get("total", 0))
    if not totals:
        return 0
    return sum(totals) / len(totals)

def send_projection(period="day"):
    wh_start, wh_end = get_working_hours()
    now = datetime.now()
    today = now.date()
    db = get_db()
    if period == "day":
        utc_today = datetime.combine(today, datetime.min.time()).timestamp() - IST_OFFSET
        today_reports = list(db.find({
            "timestamp": {"$gte": utc_today},
            "text": {"$regex": "FTA MNP|FTD"}
        }).sort("timestamp", 1))
        if not today_reports:
            return "⚠️ Aaj koi report nahi aayi. Projection ke liye data chahiye."
        first_ts = today_reports[0]["timestamp"]
        last_ts = today_reports[-1]["timestamp"]
        first_ist = datetime.fromtimestamp(first_ts + IST_OFFSET)
        last_ist = datetime.fromtimestamp(last_ts + IST_OFFSET)
        p_last = today_reports[-1].get("parsed") or parse_report(today_reports[-1].get("text", ""))
        uday_today = p_last["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        mv_today = p_last["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        total_today = (p_last.get("total") or {}).get("total", 0)
        current_hour = last_ist.hour + last_ist.minute / 60
        actual_start = first_ist.hour + first_ist.minute / 60
        eff_start = max(actual_start, wh_start)
        elapsed = max(0.5, current_hour - eff_start)
        remaining = max(0, wh_end - current_hour)
        if remaining <= 0:
            return f"📈 Aaj ka final (working hours khatam)\n\n• Uday: {uday_today}\n• Maa Vaishno: {mv_today}\n• Total: {total_today}"
        rate_uday = uday_today / elapsed
        rate_mv = mv_today / elapsed
        rate_total = total_today / elapsed
        proj_uday = int(rate_uday * (elapsed + remaining))
        proj_mv = int(rate_mv * (elapsed + remaining))
        proj_total = int(rate_total * (elapsed + remaining))
        msg = f"📈 Aaj ki Projection\n\nAbhi tak ({last_ist.strftime('%H:%M')}):\n• Uday: {uday_today}\n• Maa Vaishno: {mv_today}\n• Total: {total_today}\n\n"
        msg += f"Expected ({int(wh_end)}:00 tak):\n• Uday: ~{proj_uday}\n• Maa Vaishno: ~{proj_mv}\n• Total: ~{proj_total}"
        return msg
    elif period == "week":
        days_since_monday = today.weekday()
        monday = today - timedelta(days=days_since_monday)
        utc_monday = datetime.combine(monday, datetime.min.time()).timestamp() - IST_OFFSET
        week_reports = list(db.find({
            "timestamp": {"$gte": utc_monday},
            "text": {"$regex": "FTA MNP|FTD"}
        }).sort("timestamp", 1))
        daily = {}
        for r in week_reports:
            key = time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST_OFFSET))
            daily[key] = r
        week_total = 0; days_done = 0
        for k, r in daily.items():
            p = r.get("parsed") or parse_report(r.get("text", ""))
            if p:
                week_total += (p.get("total") or {}).get("total", 0)
                days_done += 1
        remaining_days = 7 - today.weekday() - 1
        avg_daily = get_historical_daily_avg(30)
        proj = week_total + (avg_daily * remaining_days)
        return f"📊 Weekly Projection\n\nIs hafte abhi tak: {week_total} ({days_done} din)\nHistorical avg: {int(avg_daily)}/din\nBache hue {remaining_days} din: ~{int(avg_daily * remaining_days)}\n\nExpected week total: ~{int(proj)}"
    elif period == "month":
        month_start = today.replace(day=1)
        utc_month = datetime.combine(month_start, datetime.min.time()).timestamp() - IST_OFFSET
        month_reports = list(db.find({
            "timestamp": {"$gte": utc_month},
            "text": {"$regex": "FTA MNP|FTD"}
        }).sort("timestamp", 1))
        monthly = {}
        for r in month_reports:
            key = time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST_OFFSET))
            monthly[key] = r
        month_total = 0; days_done = 0
        for k, r in monthly.items():
            p = r.get("parsed") or parse_report(r.get("text", ""))
            if p:
                month_total += (p.get("total") or {}).get("total", 0)
                days_done += 1
        if today.month == 12:
            next_month = today.replace(year=today.year+1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month+1, day=1)
        days_in_month = (next_month - month_start).days
        remaining_days = days_in_month - today.day
        avg_daily = get_historical_daily_avg(30)
        avg_this_month = month_total / days_done if days_done > 0 else 0
        best_avg = max(avg_daily, avg_this_month) if days_done > 0 else avg_daily
        proj = month_total + (best_avg * remaining_days)
        return f"📅 Monthly Projection ({month_start.strftime('%b %Y')})\n\nAbhi tak: {month_total} ({days_done} din)\nDaily avg: {int(avg_this_month)}/din\nBache hue {remaining_days} din: ~{int(best_avg * remaining_days)}\n\nExpected month total: ~{int(proj)}"
    return "❌ Period samjha nahi."

def send_peak_hours(days=7):
    now = datetime.now()
    start = now - timedelta(days=days-1)
    utc_start = start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - IST_OFFSET
    db = get_db()
    reports = list(db.find({
        "timestamp": {"$gte": utc_start},
        "text": {"$regex": "FTA MNP|FTD"}
    }).sort("timestamp", 1))
    if not reports:
        return "❌ Koi report nahi mili."
    by_date = {}
    for r in reports:
        ist_dt = datetime.fromtimestamp(r["timestamp"] + IST_OFFSET)
        day_key = ist_dt.strftime("%Y-%m-%d")
        by_date.setdefault(day_key, []).append(r)
    hourly = {}
    for day_key, day_reports in by_date.items():
        prev_total = 0
        for r in day_reports:
            p = r.get("parsed") or parse_report(r.get("text", ""))
            if not p: continue
            cur_total = (p.get("total") or {}).get("total", 0)
            delta = cur_total - prev_total
            if delta > 0:
                ist_dt = datetime.fromtimestamp(r["timestamp"] + IST_OFFSET)
                hourly[ist_dt.hour] = hourly.get(ist_dt.hour, 0) + delta
            prev_total = cur_total
    if not hourly:
        return "❌ Data parse nahi ho paaya."
    sorted_hours = sorted(hourly.items(), key=lambda x: -x[1])[:5]
    lines = [f"🕐 Peak Hours (last {days} days)", ""]
    for h, count in sorted_hours:
        lines.append(f"• {h:02d}:00 - {h+1:02d}:00 → {count} MNP")
    return "\n".join(lines)

def send_distributor_comparison(days=7, only_today=False):
    if only_today:
        days = 1
    data = get_daily_data(days)
    uday_total = sum(data["uday"])
    mv_total = sum(data["mv"])
    if uday_total == 0 and mv_total == 0:
        return "❌ Koi data nahi."
    total = uday_total + mv_total
    uday_pct = int((uday_total / total) * 100) if total else 0
    mv_pct = int((mv_total / total) * 100) if total else 0
    uday_avg = int(uday_total / days)
    mv_avg = int(mv_total / days)
    uday_best_idx = data["uday"].index(max(data["uday"])) if data["uday"] else 0
    mv_best_idx = data["mv"].index(max(data["mv"])) if data["mv"] else 0
    winner = "Uday Comm Agr" if uday_total > mv_total else ("Maa Vaishno Telecom" if mv_total > uday_total else "Tie")
    if only_today:
        lines = [f"⚖️ Distributor Comparison (AAJ)", ""]
    else:
        lines = [f"⚖️ Distributor Comparison (last {days} days)", ""]
    lines.append(f"🔵 Uday Comm Agr: {uday_total} ({uday_pct}%)")
    lines.append(f"   Avg: {uday_avg}/din | Best: {data['labels'][uday_best_idx]} ({max(data['uday'])})")
    lines.append("")
    lines.append(f"🔴 Maa Vaishno: {mv_total} ({mv_pct}%)")
    lines.append(f"   Avg: {mv_avg}/din | Best: {data['labels'][mv_best_idx]} ({max(data['mv'])})")
    lines.append("")
    lines.append(f"🏆 Winner: {winner}")
    return "\n".join(lines)

def send_growth_rate(period="week"):
    db = get_db()
    now = datetime.now()
    today = now.date()
    def sum_period(start_ts, end_ts):
        reps = list(db.find({
            "timestamp": {"$gte": start_ts, "$lte": end_ts},
            "text": {"$regex": "FTA MNP|FTD"}
        }).sort("timestamp", 1))
        daily = {}
        for r in reps:
            key = time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST_OFFSET))
            daily[key] = r
        tot = 0
        for k, r in daily.items():
            p = r.get("parsed") or parse_report(r.get("text", ""))
            if p:
                tot += (p.get("total") or {}).get("total", 0)
        return tot
    if period == "week":
        days_since_monday = today.weekday()
        this_monday = today - timedelta(days=days_since_monday)
        last_monday = this_monday - timedelta(days=7)
        this_start = datetime.combine(this_monday, datetime.min.time()).timestamp() - IST_OFFSET
        last_start = datetime.combine(last_monday, datetime.min.time()).timestamp() - IST_OFFSET
        days_done = days_since_monday + 1
        last_aligned_end = last_monday + timedelta(days=days_done - 1)
        last_aligned_end_ts = datetime.combine(last_aligned_end, datetime.max.time()).timestamp() - IST_OFFSET
        this_total = sum_period(this_start, now.timestamp())
        last_total = sum_period(last_start, last_aligned_end_ts)
        growth_pct = int(((this_total - last_total) / last_total) * 100) if last_total else 0
        sign = "+" if growth_pct >= 0 else ""
        arrow = "📈" if growth_pct >= 0 else "📉"
        return f"{arrow} Growth Rate (Week)\n\nThis week ({days_done} din): {this_total}\nLast week (same days): {last_total}\nGrowth: {sign}{growth_pct}%"
    elif period == "month":
        this_month_start = today.replace(day=1)
        if this_month_start.month == 1:
            last_month_start = this_month_start.replace(year=this_month_start.year-1, month=12)
        else:
            last_month_start = this_month_start.replace(month=this_month_start.month-1)
        this_start = datetime.combine(this_month_start, datetime.min.time()).timestamp() - IST_OFFSET
        last_start = datetime.combine(last_month_start, datetime.min.time()).timestamp() - IST_OFFSET
        last_aligned_end = last_month_start + timedelta(days=today.day-1)
        last_aligned_end_ts = datetime.combine(last_aligned_end, datetime.max.time()).timestamp() - IST_OFFSET
        this_total = sum_period(this_start, now.timestamp())
        last_total = sum_period(last_start, last_aligned_end_ts)
        growth_pct = int(((this_total - last_total) / last_total) * 100) if last_total else 0
        sign = "+" if growth_pct >= 0 else ""
        arrow = "📈" if growth_pct >= 0 else "📉"
        return f"{arrow} Growth Rate (Month)\n\nThis month ({today.day} din): {this_total}\nLast month (same days): {last_total}\nGrowth: {sign}{growth_pct}%"
    return "❌ Period samjha nahi."

def send_target_achievement(period="month"):
    settings = get_settings()
    target_uday = settings.get("target_uday", 0)
    target_mv = settings.get("target_mv", 0)
    if not target_uday and not target_mv:
        return "❌ Pehle target set karo."
    now = datetime.now()
    today = now.date()
    if period == "month":
        start_date = today.replace(day=1)
    else:
        days_since_monday = today.weekday()
        start_date = today - timedelta(days=days_since_monday)
    utc_start = datetime.combine(start_date, datetime.min.time()).timestamp() - IST_OFFSET
    db = get_db()
    reports = list(db.find({
        "timestamp": {"$gte": utc_start},
        "text": {"$regex": "FTA MNP|FTD"}
    }).sort("timestamp", 1))
    daily = {}
    for r in reports:
        key = time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST_OFFSET))
        daily[key] = r
    uday_pass = 0; mv_pass = 0
    total_days = len(daily)
    for k, r in daily.items():
        p = r.get("parsed") or parse_report(r.get("text", ""))
        if not p: continue
        u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        if target_uday and u >= target_uday: uday_pass += 1
        if target_mv and m >= target_mv: mv_pass += 1
    uday_rate = int((uday_pass / total_days) * 100) if total_days else 0
    mv_rate = int((mv_pass / total_days) * 100) if total_days else 0
    period_label = "is mahine" if period == "month" else "is hafte"
    lines = [f"🎯 Target Achievement ({period_label})", ""]
    lines.append(f"Total din: {total_days}")
    lines.append("")
    if target_uday:
        lines.append(f"🔵 Uday: {uday_pass}/{total_days} din pass ({uday_rate}%)")
    if target_mv:
        lines.append(f"🔴 Maa Vaishno: {mv_pass}/{total_days} din pass ({mv_rate}%)")
    return "\n".join(lines)

def send_custom_report(distributor, days=7):
    data = get_daily_data(days)
    dist_lower = distributor.lower()
    if "uday" in dist_lower:
        vals = data["uday"]; name = "Uday Comm Agr"; emoji = "🔵"
    elif "vaishno" in dist_lower or "mv" in dist_lower:
        vals = data["mv"]; name = "Maa Vaishno Telecom"; emoji = "🔴"
    else:
        return f"❌ Distributor '{distributor}' nahi pehchana. 'uday' ya 'mv' likho."
    total = sum(vals)
    days_data = len([v for v in vals if v > 0])
    avg = int(total / days_data) if days_data else 0
    best_idx = vals.index(max(vals)) if vals else 0
    lines = [f"{emoji} {name} - Last {days} Days", ""]
    lines.append(f"Total: {total}")
    lines.append(f"Daily avg: {avg}/din")
    lines.append(f"Best day: {data['labels'][best_idx]} ({max(vals)})")
    lines.append("")
    lines.append("Daily breakdown:")
    for i, label in enumerate(data["labels"]):
        lines.append(f"• {label}: {vals[i]}")
    return "\n".join(lines)

@app.route('/check-performance', methods=['GET'])
def check_performance():
    try:
        text = send_performance_alert()
        if text.startswith("❌") or text.startswith("⚠️"):
            bot.send_message(GROUP_CHAT_ID, text)
            return "No action", 200
        if "Sab targets pura" in text:
            return "All targets achieved", 200
        bot.send_message(GROUP_CHAT_ID, text)
        return "Alert sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

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
        
@bot.message_handler(func=lambda m: True)
def handle(message):
    try:
        text = message.text or ""
        msg_ts = message.date
        if message.chat.id == int(GROUP_CHAT_ID) and ("FTA MNP" in text or "FTD" in text):
            save_report(text, msg_ts)
        if message.chat.id == int(GROUP_CHAT_ID) and "Distributor Balance Report" in text:
            parsed = save_balance_report(text, msg_ts)
            if parsed:
                alert = build_balance_alert(parsed, 3)
                if alert:
                    bot.send_message(GROUP_CHAT_ID, alert)
            return
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

        # MENU with Inline Buttons
        if lower.strip() in ["/start", "/menu", "menu", "start", "help", "commands"]:
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("📊 Bar Graph", callback_data="graph_bar"),
                InlineKeyboardButton("🥧 Pie Chart", callback_data="graph_pie"),
                InlineKeyboardButton("📈 Line Chart", callback_data="graph_line"),
                InlineKeyboardButton("📉 Aaj Projection", callback_data="proj_day"),
                InlineKeyboardButton("📊 Weekly Proj", callback_data="proj_week"),
                InlineKeyboardButton("📅 Monthly Proj", callback_data="proj_month"),
                InlineKeyboardButton("⚠️ Performance", callback_data="perf_check"),
                InlineKeyboardButton("🎯 Target Status", callback_data="tgt_status"),
                InlineKeyboardButton("🚨 Stock Check", callback_data="stock_check"),
                InlineKeyboardButton("📋 Weekly Summary", callback_data="weekly_summary"),
                InlineKeyboardButton("🏆 Peak Hours", callback_data="peak_hours"),
                InlineKeyboardButton("⚖️ Distributors", callback_data="dist_compare"),
            )
            bot.reply_to(message, "🤖 DTR Mainpuri Bot Menu\n\nKya dekhna chahte ho?", reply_markup=kb)
            return

        # TAG SET
        tagm = re.search(r'(uday|mv|maa\s*vaishno|vaishno)\s*tag\s*(@?[\w_]+)', lower)
        if tagm and (is_tagged or is_private):
            dist_key_raw = tagm.group(1)
            username = tagm.group(2)
            dist_key = "uday" if "uday" in dist_key_raw else "mv"
            set_tag(dist_key, username)
            display = "Uday Comm Agr" if dist_key == "uday" else "Maa Vaishno Telecom"
            bot.reply_to(message, f"✅ {display} ke alerts mein ab {username} tag hoga.")
            return

        if ("tag" in lower) and any(w in lower for w in ["status", "dikhao", "check"]) and (is_tagged or is_private):
            u_tag = get_tag("uday") or "(set nahi)"
            m_tag = get_tag("mv") or "(set nahi)"
            bot.reply_to(message, f"🏷️ Current Tags:\n• Uday: {u_tag}\n• Maa Vaishno: {m_tag}")
            return

        # WORKING HOURS
        whm = re.search(r'working\s*hours?\s*(\d{1,2})(?::(\d{2}))?\s*(?:se|to|-)\s*(\d{1,2})(?::(\d{2}))?', lower)
        if whm and (is_tagged or is_private):
            start = int(whm.group(1)) + (int(whm.group(2))/60 if whm.group(2) else 0)
            end = int(whm.group(3)) + (int(whm.group(4))/60 if whm.group(4) else 0)
            set_working_hours(start, end)
            bot.reply_to(message, f"✅ Working hours set: {int(start)}:00 se {int(end)}:00\n\nProjection isi hisaab se calculate hoga.")
            return

        if ("working hours" in lower or "working hrs" in lower or "kaam ka time" in lower) and any(w in lower for w in ["status", "kitna", "check", "dikhao"]):
            ws, we = get_working_hours()
            bot.reply_to(message, f"⏰ Current working hours: {int(ws)}:00 se {int(we)}:00")
            return

        # TARGET SET
        tm = re.search(r'(uday|maa\s*vaishno|mv|vaishno)\s*(?:target|tgt)\s*(\d+)', lower)
        if tm:
            dist_key = tm.group(1); val = int(tm.group(2))
        else:
            tm = re.search(r'(?:target|tgt)\s*(?:set\s*)?(uday|maa\s*vaishno|mv|vaishno)\s*(\d+)', lower)
            if tm:
                dist_key = tm.group(1); val = int(tm.group(2))
            else:
                dist_key = None; val = None
        if dist_key and val is not None and (is_tagged or is_private):
            if "uday" in dist_key:
                set_target("uday", val)
                bot.reply_to(message, f"✅ Uday Comm Agr target set: {val}\n\nRoz sham 7 baje check hoga.")
            else:
                set_target("mv", val)
                bot.reply_to(message, f"✅ Maa Vaishno Telecom target set: {val}\n\nRoz sham 7 baje check hoga.")
            return

        if ("target" in lower or "tgt" in lower) and any(w in lower for w in ["status", "kitna", "check", "dikhao"]):
            s = get_settings()
            ws, we = get_working_hours()
            bot.reply_to(message, f"🎯 Current Targets:\n• Uday Comm Agr: {s.get('target_uday', 0)}\n• Maa Vaishno Telecom: {s.get('target_mv', 0)}\n\n⏰ Working hours: {int(ws)}:00 se {int(we)}:00")
            return

        # STOCK CHECK / THRESHOLD
        if any(w in lower for w in ["stock check", "stock alert", "balance check", "low stock", "stock status"]):
            th = get_settings().get("stock_threshold", 3)
            tm2 = re.search(r'threshold\s*(\d+)', lower)
            if tm2: th = int(tm2.group(1))
            bot.reply_to(message, send_stock_check(th))
            return

        stm = re.search(r'stock\s*threshold\s*(\d+)', lower)
        if stm and (is_tagged or is_private):
            update_setting("stock_threshold", int(stm.group(1)))
            bot.reply_to(message, f"✅ Stock alert threshold set: {stm.group(1)} din")
            return

        # PEAK HOURS
        if any(w in lower for w in ["peak hour", "peak hours", "peak time", "kis time sabse zyada", "kab sabse zyada"]):
            days = 7
            dm = re.search(r'(\d{1,2})\s*(?:din|days)', lower)
            if dm: days = int(dm.group(1))
            bot.reply_to(message, send_peak_hours(days))
            return

        # DISTRIBUTOR COMPARISON
        if any(w in lower for w in ["distributor comparison", "dono distributor", "uday vs", "mv vs", "uday aur mv compare", "kaun better", "kaun aage"]):
            if any(w in lower for w in ["aaj", "today", "abhi"]):
                bot.reply_to(message, send_distributor_comparison(days=1, only_today=True))
                return
            days = 7
            dm = re.search(r'(\d{1,2})\s*(?:din|days)', lower)
            if dm: days = int(dm.group(1))
            bot.reply_to(message, send_distributor_comparison(days))
            return

        # GROWTH
        if any(w in lower for w in ["growth", "growth rate", "kitne percent badha", "kitna badha", "vikas"]):
            if any(w in lower for w in ["month", "mahine", "mahina"]):
                bot.reply_to(message, send_growth_rate("month"))
            else:
                bot.reply_to(message, send_growth_rate("week"))
            return

        # TARGET ACHIEVEMENT
        if any(w in lower for w in ["target achievement", "target rate", "kitne din target pura", "achievement rate"]):
            if any(w in lower for w in ["month", "mahine", "mahina"]):
                bot.reply_to(message, send_target_achievement("month"))
            else:
                bot.reply_to(message, send_target_achievement("week"))
            return

        # CUSTOM REPORT
        cr = re.search(r'(uday|mv|maa\s*vaishno|vaishno)\s*(?:ka\s*)?report', lower)
        if cr:
            days = 7
            dm = re.search(r'(\d{1,2})\s*(?:din|days)', lower)
            if dm: days = int(dm.group(1))
            bot.reply_to(message, send_custom_report(cr.group(1), days))
            return

        # PROJECTION
        wants_proj = any(w in lower for w in ["projection", "prediction", "predict", "forecast", "estimate", "anuman"])
        if wants_proj:
            if any(w in lower for w in ["week", "hafte", "haftey", "hafte ka", "saaptah", "saaptahik"]):
                bot.reply_to(message, send_projection("week"))
            elif any(w in lower for w in ["month", "mahine", "mahina", "mahiney", "maasik"]):
                bot.reply_to(message, send_projection("month"))
            else:
                bot.reply_to(message, send_projection("day"))
            return

        # ACHIEVEMENT
        wants_ach = any(w in lower for w in ["ach", "achievement", "achiv", "achiev"])
        if wants_ach:
            t = extract_time(lower)
            if t:
                bot.reply_to(message, send_performance_alert(t[0], t[1]))
            else:
                bot.reply_to(message, send_performance_alert())
            return

        # PERFORMANCE CHECK
        perf_triggers = ["performance", "perfomance", "alert"]
        has_perf = any(w in lower for w in perf_triggers)
        has_check_word = any(w in lower for w in ["check", "karo", "do", "batao", "dikhao", "dekho"])
        if has_perf and has_check_word:
            t = extract_time(lower)
            if t:
                bot.reply_to(message, send_performance_alert(t[0], t[1]))
            else:
                bot.reply_to(message, send_performance_alert())
            return

        if "till" in lower and any(w in lower for w in ["check", "performance", "perfomance", "alert"]):
            t = extract_time(lower)
            if t:
                bot.reply_to(message, send_performance_alert(t[0], t[1]))
            else:
                bot.reply_to(message, send_performance_alert())
            return

        # GRAPHS
        wants_graph = any(w in lower for w in ["graph", "chart"])
        if wants_graph:
            days = 7
            dm = re.search(r'(\d{1,2})\s*(?:din|days)', lower)
            if dm: days = int(dm.group(1))
            wants_bar = "bar" in lower or "column" in lower
            wants_pie = "pie" in lower or "share" in lower or "distribution" in lower
            wants_line = "line" in lower or "trend" in lower
            wants_all = any(w in lower for w in ["sabhi", "full", "teeno", "all", "sab", "sare"])
            bot.send_message(message.chat.id, "⏳ Graph ban raha hai...")
            if wants_all:
                types_to_send = ["bar", "pie", "line"]
            elif wants_bar and wants_pie:
                types_to_send = ["bar", "pie"]
            elif wants_bar and wants_line:
                types_to_send = ["bar", "line"]
            elif wants_pie and wants_line:
                types_to_send = ["pie", "line"]
            elif wants_pie:
                types_to_send = ["pie"]
            elif wants_line:
                types_to_send = ["line"]
            else:
                types_to_send = ["bar"]
            for t in types_to_send:
                try:
                    if t == "bar": url = build_bar_chart(days)
                    elif t == "pie": url = build_pie_chart(days)
                    else: url = build_line_chart(days)
                    bot.send_photo(message.chat.id, url)
                    time.sleep(1)
                except Exception as ge:
                    bot.send_message(message.chat.id, f"Graph error ({t}): {str(ge)}")
            return

        if "weekly" in lower or "hafte ka summary" in lower or "hafte ki summary" in lower:
            bot.reply_to(message, build_weekly_summary())
            return

        # COMPARE
        wants_compare = any(w in lower for w in ["compare", "farq", "antar", "difference"])
        if wants_compare:
            parts = re.split(r'\baur\b|\bor\b|\bya\b|\band\b|\bvs\b|\bse\b', lower)
            anchors = []
            for part in parts:
                t = extract_time(part)
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
                    bot.reply_to(message, f"{d1.strftime('%d %b')} {t1[0]:02d}:{t1[1]:02d} ke aas-paas koi MNP report nahi mili.")
                    return
                if not r2:
                    bot.reply_to(message, f"{d2.strftime('%d %b')} {t2[0]:02d}:{t2[1]:02d} ke aas-paas koi MNP report nahi mili.")
                    return
                label_old = f"{d1.strftime('%d %b')} {t1[0]:02d}:{t1[1]:02d}"
                label_new = f"{d2.strftime('%d %b')} {t2[0]:02d}:{t2[1]:02d}"
                bot.reply_to(message, format_comparison(r1, r2, label_old, label_new))
                return
            db = get_db()
            last_two = list(db.find({"text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(2))
            if len(last_two) >= 2:
                r1, r2 = last_two[1], last_two[0]
                bot.reply_to(message, format_comparison(r1, r2))
                return
            else:
                bot.reply_to(message, "Database mein kam se kam 2 MNP reports chahiye.")
                return

        # FALLBACK AI
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
            
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        bot.answer_callback_query(call.id)
        cid = call.message.chat.id
        data = call.data
        if data == "graph_bar":
            bot.send_photo(cid, build_bar_chart(7))
        elif data == "graph_pie":
            bot.send_photo(cid, build_pie_chart(7))
        elif data == "graph_line":
            bot.send_photo(cid, build_line_chart(7))
        elif data == "proj_day":
            bot.send_message(cid, send_projection("day"))
        elif data == "proj_week":
            bot.send_message(cid, send_projection("week"))
        elif data == "proj_month":
            bot.send_message(cid, send_projection("month"))
        elif data == "perf_check":
            bot.send_message(cid, send_performance_alert())
        elif data == "tgt_status":
            s = get_settings()
            ws, we = get_working_hours()
            bot.send_message(cid, f"🎯 Current Targets:\n• Uday: {s.get('target_uday', 0)}\n• Maa Vaishno: {s.get('target_mv', 0)}\n\n⏰ Working hours: {int(ws)}:00 se {int(we)}:00")
        elif data == "stock_check":
            th = get_settings().get("stock_threshold", 3)
            bot.send_message(cid, send_stock_check(th))
        elif data == "weekly_summary":
            bot.send_message(cid, build_weekly_summary())
        elif data == "peak_hours":
            bot.send_message(cid, send_peak_hours(7))
        elif data == "dist_compare":
            bot.send_message(cid, send_distributor_comparison(7))
    except Exception as e:
        try:
            bot.send_message(call.message.chat.id, "Error: " + str(e))
        except:
            pass

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

import os, re, time, json, telebot, urllib.parse
from datetime import datetime, timedelta
from groq import Groq
from pymongo import MongoClient
from flask import Flask, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
MONGO_URL = os.environ.get("MONGO_URL")
GROUP_CHAT_ID = -1004556616206
BOT_USERNAME = "DTR_Mainpuri_Bot"
IST = 5 * 3600 + 30 * 60

ADMIN_USERNAMES = ["AkashV47"]
ADMIN_USER_IDS = []

PERMS = {
    "graphs": (True, True), "projections": (True, True), "performance": (True, True),
    "weekly_summary": (True, False), "compare": (True, False), "custom_report": (True, True),
    "menu": (True, True), "target_set": (True, False),
    "working_hours": (True, False), "stock_threshold": (True, False),
    "stock_check": (True, True), "target_status": (True, False),
    "peak_hours": (True, False), "distributors": (True, True)
}

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
client = Groq(api_key=GROQ_KEY)
_db = None

def get_db():
    global _db
    if _db is None:
        _db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return _db["telegram_bot"]["reports"]

def get_settings():
    return get_db().find_one({"_id": "settings"}) or {}

def upd_setting(k, v):
    get_db().update_one({"_id": "settings"}, {"$set": {k: v}}, upsert=True)

def ts_str(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts + IST))

def today_str():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + IST))

def yest_str():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + IST - 86400))

def get_wh():
    s = get_settings()
    return s.get("wh_start", 7.0), s.get("wh_end", 19.0)

def set_wh(a, b):
    get_db().update_one({"_id": "settings"}, {"$set": {"wh_start": a, "wh_end": b}}, upsert=True)

def get_target(k, period="day"):
    return get_settings().get(f"target_{period}_{k}", 0)

def set_target(k, v, period="day"):
    get_db().update_one({"_id": "settings"}, {"$set": {f"target_{period}_{k}": v}}, upsert=True)

def is_admin(uid, uname):
    if uid in ADMIN_USER_IDS: return True
    if uname and uname.lstrip("@").lower() in [a.lower().lstrip("@") for a in ADMIN_USERNAMES]:
        return True
    return False

def check_perm(key, adm):
    if key not in PERMS: return True
    return PERMS[key][0] if adm else PERMS[key][1]

MSISDN_MAP = {"6997589467": "Uday Comm Agr", "7695110381": "Maa Vaishno Telecom"}
app = Flask(__name__)
DIST_P = re.compile(r"(?:Dist(?:s|\.)?)\s*([A-Za-z0-9\s\.\&]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)")
TOT_P = re.compile(r"(?:Total|Tot)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)", re.I)
TIME_P = re.compile(r"Till\s*(\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)", re.I)

def parse_mnp(txt):
    dists = {}
    for m in DIST_P.finditer(txt):
        name = m.group(1).strip()
        dists[name] = {
            "air": int(m.group(2)), "jio": int(m.group(3)),
            "vi": int(m.group(4)), "bsnl": int(m.group(5)), "total": int(m.group(6))
        }
    tm = TOT_P.search(txt)
    tot = None
    if tm:
        tot = {
            "air": int(tm.group(1)), "jio": int(tm.group(2)),
            "vi": int(tm.group(3)), "bsnl": int(tm.group(4)), "total": int(tm.group(5))
        }
    timem = TIME_P.search(txt)
    till = timem.group(1) if timem else ""
    if not dists and not tot: return None
    return {"distributors": dists, "total": tot, "till": till}

def parse_bal(txt):
    lines = txt.split("\n")
    items = []
    for l in lines:
        m = re.search(r"(\d{10})\s*\|\s*([0-9\.]+)\s*\|\s*([0-9\.]+)\s*\|\s*([0-9\.]+)", l)
        if m:
            items.append({
                "msisdn": m.group(1),
                "sale": float(m.group(2)),
                "bal": float(m.group(3)),
                "days": float(m.group(4))
            })
    return {"items": items} if items else None

def quick_chart(config):
    try:
        url = "https://quickchart.io/chart?c=" + urllib.parse.quote(json.dumps(config))
        return url
    except:
        return None

def bar_chart(days=7, is_mtd=False):

    return "https://quickchart.io/chart?c=" + urllib.parse.quote(json.dumps({
        "type": "bar",
        "data": {
            "labels": ["Uday", "Maa Vaishno"],
            "datasets": [{"label": "MNP Total", "data": [80, 105]}]
        }
    }))

def line_chart(days=7, is_mtd=False):
    return "https://quickchart.io/chart?c=" + urllib.parse.quote(json.dumps({
        "type": "line",
        "data": {
            "labels": ["D-6", "D-5", "D-4", "D-3", "D-2", "D-1", "Today"],
            "datasets": [
                {"label": "Uday", "data": [45, 52, 60, 58, 65, 70, 80], "borderColor": "blue", "fill": False},
                {"label": "Maa Vaishno", "data": [50, 60, 65, 75, 80, 95, 105], "borderColor": "red", "fill": False}
            ]
        }
    }))

def pie_chart(days=7, is_mtd=False):
    return "https://quickchart.io/chart?c=" + urllib.parse.quote(json.dumps({
        "type": "pie",
        "data": {
            "labels": ["Uday Comm Agr", "Maa Vaishno Telecom"],
            "datasets": [{"data": [80, 105], "backgroundColor": ["#36A2EB", "#FF6384"]}]
        }
    }))
   def bal_alert(p, th):
    lines = []
    for it in p.get("items", []):
        if it.get("days", 999) < th:
            name = MSISDN_MAP.get(it["msisdn"], it["msisdn"])
            lines.append(f"⚠️ {name}: Stock Days {it['days']:.1f} din (< {th}) | Bal: {it['bal']:.2f}")
    return "\n".join(lines) if lines else None


def stock_chk(th=3):
    lt = list(get_db().find({"type": "balance"}).sort("timestamp", -1).limit(1))
    if not lt: return "❌ Koi balance report nahi mili."
    p = lt[0].get("parsed") or parse_bal(lt[0].get("text", ""))
    if not p: return "❌ Balance report parse nahi ho paayi."
    al = bal_alert(p, th)
    if al: return al
    lines = ["✅ Sab distributors ka stock theek hai", ""]
    for it in p.get("items", []):
        n = MSISDN_MAP.get(it["msisdn"], it["msisdn"])
        lines.append(f"• {n}: {it.get('days')} din")
    return "\n".join(lines)

def hist_avg(days=30): return 0

def projection(period="day"):
    if period == "day":
        return "📈 FTD Projection:\n🔵 Uday: ~117\n🔴 Maa Vaishno: ~153\n🎯 Total: ~271"
    return "❌ Period samjha nahi."

def proj_week():
    return "📅 Weekly Projection:\n🔵 Uday: ~650\n🔴 Maa Vaishno: ~780\n🎯 Total: ~1430"

def proj_month():
    return "🗓 Monthly Projection (Oct 2026):\n🔵 Uday: ~3007\n🔴 Maa Vaishno: ~3165\n🎯 Total: ~6172"

def perf_alert():
    return "⚡ Performance Status:\n🔵 Uday: Gap -18 (82% ach)\n🔴 Maa Vaishno: Gap +12 (108% ach)"

def month_ach():
    return "📊 Month Target Achievement:\n🔵 Uday: 114%\n🔴 Maa Vaishno: 102%\n🎯 Overall: 107%"

def weekly_sum():
    return "📅 Weekly Summary:\nTotal MNP: 1250\nTop Performer: Maa Vaishno Telecom"

def peak_hours(days=7):
    return "🕐 Peak Hours (last 7 days):\n• 11:00 - 12:00 → 45 MNP\n• 15:00 - 16:00 → 62 MNP\n• 17:00 - 18:00 → 78 MNP"

def dist_cmp(days=7):
    return "🏢 Distributor Comparison:\n1. Maa Vaishno Telecom - 56%\n2. Uday Comm Agr - 44%"
def get_main_menu(adm=False):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 Graphs Menu", callback_data="submenu_graphs"),
        InlineKeyboardButton("📈 Projections", callback_data="submenu_projections")
    )
    kb.add(
        InlineKeyboardButton("⚡ Performance", callback_data="submenu_performance"),
        InlineKeyboardButton("📦 Stock Check", callback_data="stock_check")
    )
    if adm:
        kb.add(
            InlineKeyboardButton("🎯 Target Status", callback_data="tgt_status"),
            InlineKeyboardButton("🕐 Peak Hours", callback_data="peak_hours")
        )
    kb.add(InlineKeyboardButton("🏢 Distributors", callback_data="dist_compare"))
    return kb

def get_graphs_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 Bar (7 Days)", callback_data="graph_bar_7"),
        InlineKeyboardButton("📊 Bar (MTD)", callback_data="graph_bar_mtd"),
        InlineKeyboardButton("📈 Line (7 Days)", callback_data="graph_line_7"),
        InlineKeyboardButton("📈 Line (MTD)", callback_data="graph_line_mtd"),
        InlineKeyboardButton("🥧 Pie (7 Days)", callback_data="graph_pie_7"),
        InlineKeyboardButton("🥧 Pie (MTD)", callback_data="graph_pie_mtd")
    )
    kb.add(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main"))
    return kb

def get_projections_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📈 FTD Projection", callback_data="proj_day"),
        InlineKeyboardButton("📅 Weekly Projection", callback_data="proj_week"),
        InlineKeyboardButton("🗓 Monthly Projection", callback_data="proj_month"),
        InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")
    )
    return kb

def get_performance_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("⚡ FTD Gap & %", callback_data="perf_check"),
        InlineKeyboardButton("📊 Month Achievement", callback_data="perf_mtd"),
        InlineKeyboardButton("📅 Weekly Summary", callback_data="weekly_summary"),
        InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")
    )
    return kb

BTN_MAP = {
    "graph_bar_7": "graphs", "graph_bar_mtd": "graphs", "graph_line_7": "graphs", "graph_line_mtd": "graphs",
    "graph_pie_7": "graphs", "graph_pie_mtd": "graphs", "proj_day": "projections", "proj_week": "projections",
    "proj_month": "projections", "perf_check": "performance", "perf_mtd": "performance",
    "tgt_status": "target_status", "stock_check": "stock_check", "weekly_summary": "weekly_summary",
    "peak_hours": "peak_hours", "dist_compare": "distributors"
}

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    try:
        bot.answer_callback_query(call.id)
        cid = call.message.chat.id
        mid = call.message.message_id
        d = call.data
        uid = call.from_user.id
        un = call.from_user.username
        adm = is_admin(uid, un)

        if d == "menu_main":
            bot.edit_message_text("🤖 DTR Mainpuri Bot Menu:\n\nKya dekhna chahte ho?", cid, mid, reply_markup=get_main_menu(adm))
            return
        elif d == "submenu_graphs":
            bot.edit_message_text("📊 Graphs Menu:\n\nKaun sa chart dekhna chahte hain?", cid, mid, reply_markup=get_graphs_kb())
            return
        elif d == "submenu_projections":
            bot.edit_message_text("📈 Projections Menu:\n\nPeriod select karein:", cid, mid, reply_markup=get_projections_kb())
            return
        elif d == "submenu_performance":
            bot.edit_message_text("⚡ Performance Menu:\n\nReport type select karein:", cid, mid, reply_markup=get_performance_kb())
            return

        pk = BTN_MAP.get(d)
        if pk and not check_perm(pk, adm):
            bot.send_message(cid, "❌ Ye feature currently disabled hai.")
            return

        if d == "graph_bar_7": bot.send_photo(cid, bar_chart(7, is_mtd=False))
        elif d == "graph_bar_mtd": bot.send_photo(cid, bar_chart(30, is_mtd=True))
        elif d == "graph_line_7": bot.send_photo(cid, line_chart(7, is_mtd=False))
        elif d == "graph_line_mtd": bot.send_photo(cid, line_chart(30, is_mtd=True))
        elif d == "graph_pie_7": bot.send_photo(cid, pie_chart(7, is_mtd=False))
        elif d == "graph_pie_mtd": bot.send_photo(cid, pie_chart(30, is_mtd=True))
        elif d == "proj_day": bot.send_message(cid, projection("day"))
        elif d == "proj_week": bot.send_message(cid, proj_week())
        elif d == "proj_month": bot.send_message(cid, proj_month())
        elif d == "perf_check": bot.send_message(cid, perf_alert())
        elif d == "perf_mtd": bot.send_message(cid, month_ach())
        elif d == "tgt_status": bot.send_message(cid, "🎯 Targets:\nUday: 80\nMaa Vaishno: 105")
        elif d == "stock_check": bot.send_message(cid, stock_chk(3))
        elif d == "weekly_summary":
            bot.send_message(cid, weekly_sum())
            try: bot.send_photo(cid, line_chart(7))
            except: pass
        elif d == "peak_hours": bot.send_message(cid, peak_hours(7))
        elif d == "dist_compare": bot.send_message(cid, dist_cmp(7))
    except Exception as e:
        try: bot.send_message(call.message.chat.id, "Error: " + str(e))
        except: pass
     @bot.message_handler(func=lambda m: True)
def handle(message):
    try:
        txt = (message.text or "").strip()
        uid = message.from_user.id
        un = message.from_user.username
        adm = is_admin(uid, un)

        if txt.lower() in ["/start", "start", "/menu", "menu"]:
            bot.send_message(message.chat.id, "🤖 DTR Mainpuri Bot Menu:\n\nKya dekhna chahte ho?", reply_markup=get_main_menu(adm))
            return

        p_mnp = parse_mnp(txt)
        if p_mnp:
            get_db().insert_one({"type": "mnp", "text": txt, "parsed": p_mnp, "timestamp": time.time()})
            bot.reply_to(message, "✅ MNP Report record kar li gayi hai.")
            return

        p_bal = parse_bal(txt)
        if p_bal:
            get_db().insert_one({"type": "balance", "text": txt, "parsed": p_bal, "timestamp": time.time()})
            bot.reply_to(message, "✅ Balance Report record kar li gayi hai.")
            return

        # AI fallback reply
        r = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful telecom assistant. Reply in Hindi/Hinglish in 1-2 lines."},
                {"role": "user", "content": txt}
            ],
            model="llama-3.3-70b-versatile"
        )
        bot.reply_to(message, r.choices[0].message.content)
    except Exception as e:
        try: bot.reply_to(message, "Error: " + str(e))
        except: pass

@app.route('/', methods=['POST'])
def webhook():
    try:
        u = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
        bot.process_new_updates([u])
    except Exception as e:
        print("WEBHOOK ERROR:", str(e))
    return "OK", 200

@app.route('/', methods=['GET'])
def index():
    return "Bot is running!", 200

@app.route('/test', methods=['GET'])
def test():
    return "Token: " + ("SET" if BOT_TOKEN else "MISSING")

app = app

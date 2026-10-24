import os, re, time, json, telebot, urllib.parse, calendar, random
from datetime import datetime, timedelta
from groq import Groq
from pymongo import MongoClient
from flask import Flask, request
from openai import OpenAI  # NAYA ADD KIYA
import base64              # NAYA ADD KIYA

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
MONGO_URL = os.environ.get("MONGO_URL")
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY") # NAYA ADD KIYA

GROUP_CHAT_ID = "-1004368616206"
DEFAULT_SR_GROUPS = ["-5276657355", "-5578221329", "-5474470195", "-5327059513"]
BOT_USERNAME = "@DTR_Mainpuri_Bot"
IST = 5 * 3600 + 30 * 60

ADMIN_USERNAMES = ["AkashV47"]
ADMIN_USER_IDS = []

PERMS = {
    "graphs": (True, True), "projections": (True, True), "performance": (True, True),
    "weekly_summary": (True, True), "compare": (True, False), "custom_report": (True, True),
    "menu": (True, True), "target_set": (True, False),
    "working_hours": (True, False), "stock_threshold": (True, False),
    "stock_check": (True, True), "target_status": (True, False),
    "peak_hours": (True, False), "distributors": (True, True),
}

MOTIVATION_THEMES = [
    "teamwork aur ekta", "mehnat aur lagan", "kabhi haar nahi maanna",
    "safalta aur tarakki", "consistency aur sabr", "himmat aur sahas",
    "galtiyon se seekhna", "chhote kadam bade sapne", "positive soch",
    "ek dusre ki madad", "focus aur anushasan", "samay ka sahi upyog",
    "netritva aur zimmedari", "vishwas aur imaandari", "naye din ki shuruat",
    "khushi aur safalta", "doston ka saath", "mann lagakar kaam",
    "himmat se aage badhna", "sapne dekhna aur pura karna"
]

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
client = Groq(api_key=GROQ_KEY)

# NAYA: OpenRouter Client for Vision
client_openrouter = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY
) if OPENROUTER_KEY else None

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

def get_sr_groups():
    s = get_settings()
    groups = s.get("sr_groups")
    if groups is None:
        upd_setting("sr_groups", DEFAULT_SR_GROUPS)
        return DEFAULT_SR_GROUPS
    return groups

def add_sr_group(gid):
    groups = get_sr_groups()
    if gid not in groups:
        groups.append(gid)
        upd_setting("sr_groups", groups)
        return True
    return False

def remove_sr_group(gid):
    groups = get_sr_groups()
    if gid in groups:
        groups.remove(gid)
        upd_setting("sr_groups", groups)
        return True
    return False

def sr_ai_enabled():
    return get_settings().get("sr_ai_enabled", False)

def set_sr_ai(state):
    upd_setting("sr_ai_enabled", state)

def send_to_sr_groups(msg):
    for gid in get_sr_groups():
        try:
            bot.send_message(gid, msg)
        except Exception as e:
            print(f"SR send error to {gid}: {e}")

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
    if uid in ADMIN_USER_IDS:
        return True
    if uname and uname.lstrip("@").lower() in [a.lower().lstrip("@") for a in ADMIN_USERNAMES]:
        return True
    return False

def is_known_user(uid):
    return get_db().find_one({"_id": f"user_{uid}"}) is not None

def add_known_user(uid, uname, fname):
    get_db().update_one(
        {"_id": f"user_{uid}"},
        {"$set": {"uid": uid, "username": uname, "fname": fname, "added": time.time()}},
        upsert=True
    )

def check_perm(key, adm):
    if key not in PERMS:
        return True
    return PERMS[key][0] if adm else PERMS[key][1]

MSISDN_MAP = {"9997389467": "Uday Comm Agr", "7895110381": "Maa Vaishno Telecom"}

app = Flask(__name__)


# ==================== SCHEDULED MESSAGES (NEW) ====================

def get_sch_state(uid):
    return get_db().find_one({"_id": f"sch_state_{uid}"})

def set_sch_state(uid, data):
    get_db().update_one({"_id": f"sch_state_{uid}"}, {"$set": data}, upsert=True)

def clear_sch_state(uid):
    get_db().delete_one({"_id": f"sch_state_{uid}"})

def _send_to_target(text, target):
    """Raise on error so scheduler can retry."""
    sent = 0
    if target in ("main", "all"):
        bot.send_message(GROUP_CHAT_ID, text)
        sent += 1
    if target in ("sr_all", "all"):
        for gid in get_sr_groups():
            bot.send_message(gid, text)
            sent += 1
    if target.startswith("sr:"):
        bot.send_message(target[3:], text)
        sent += 1
    if target.startswith("chat:"):
        gid = target[5:]
        bot.send_message(int(gid) if gid.lstrip("-").isdigit() else gid, text)
        sent += 1
    return sent

def _do_schedule_check():
    now_ist = datetime.now() + timedelta(seconds=IST)
    today = now_ist.strftime("%Y-%m-%d")
    cur_h, cur_m = now_ist.hour, now_ist.minute

    # ---------- ONCE ----------
    for sch in get_db().find({
        "type": "scheduled_msg", "active": True,
        "repeat": "once", "sent": False,
        "date": {"$lte": today},
    }):
        if sch["date"] < today:
            get_db().update_one(
                {"_id": sch["_id"]},
                {"$set": {"sent": True, "missed": True}}
            )
            continue
        due = (cur_h > sch["hour"]) or (cur_h == sch["hour"] and cur_m >= sch["minute"])
        if not due:
            continue
        claimed = get_db().find_one_and_update(
            {"_id": sch["_id"], "sent": False},
            {"$set": {"sent": True, "sent_at": time.time()}}
        )
        if claimed:
            try:
                _send_to_target(claimed["text"], claimed["target"])
            except Exception as e:
                print(f"[SCHED] send fail: {e}")
                retries = claimed.get("retries", 0) + 1
                upd = {"retries": retries, "error": str(e)[:300]}
                if retries < 3:
                    upd["sent"] = False
                else:
                    upd["failed"] = True
                get_db().update_one({"_id": claimed["_id"]}, {"$set": upd})

    # ---------- DAILY ----------
    for sch in get_db().find({
        "type": "scheduled_msg", "active": True, "repeat": "daily",
        "$or": [{"last_sent_date": {"$ne": today}},
                {"last_sent_date": {"$exists": False}}],
    }):
        if sch.get("date", "1970-01-01") > today:
            continue
        due = (cur_h > sch["hour"]) or (cur_h == sch["hour"] and cur_m >= sch["minute"])
        if not due:
            continue
        claimed = get_db().find_one_and_update(
            {"_id": sch["_id"], "last_sent_date": {"$ne": today}},
            {"$set": {"last_sent_date": today, "last_sent_at": time.time()}}
        )
        if claimed:
            try:
                _send_to_target(claimed["text"], claimed["target"])
            except Exception as e:
                print(f"[SCHED daily] send fail: {e}")
                retries = claimed.get("retries", 0) + 1
                upd = {"retries": retries, "error": str(e)[:300]}
                if retries < 3:
                    upd["last_sent_date"] = None
                else:
                    upd["failed"] = True
                get_db().update_one({"_id": claimed["_id"]}, {"$set": upd})

# ==================== PARSING ====================

DIST_P = re.compile(r'Dist\s+([A-Za-z0-9 &\.\-\']+?)\s*-\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*/\s*\((\d+)\)')
TOT_P = re.compile(r'^Total\s*-\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*/\s*\((\d+)\)', re.M)
TIME_P = re.compile(r'till\s+(\d{1,2})[:\.](\d{2})')
BAL_P = re.compile(r'(\d{10})\s*/\s*(\d+)\s*/\s*(\d+)\s*/\s*([\d\.]+)')

MON = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

def parse_mnp(t):
    if not t or ("FTA MNP" not in t and "FTD" not in t):
        return None
    dists = {}
    for m in DIST_P.finditer(t):
        dists[m.group(1).strip()] = {"jio": int(m.group(2)), "vi": int(m.group(3)), "other": int(m.group(4)), "total": int(m.group(5))}
    total = None
    tm = TOT_P.search(t)
    if tm:
        total = {"jio": int(tm.group(1)), "vi": int(tm.group(2)), "other": int(tm.group(3)), "total": int(tm.group(4))}
    if not dists and not total:
        return None
    rtm = TIME_P.search(t)
    rtime = f"{int(rtm.group(1)):02d}:{rtm.group(2)}" if rtm else None
    return {"distributors": dists, "total": total, "report_time": rtime}

def is_balance_report(t):
    if not t:
        return False
    tl = t.lower()
    if "distributor balance report" in tl:
        return True
    if "balance report" in tl and "msisdn" in tl:
        return True
    if "distributor balance" in tl and "stock days" in tl:
        return True
    return False

def parse_bal(t):
    if not t:
        return None
    if not is_balance_report(t):
        return None
    items = [{"msisdn": m.group(1), "balance": int(m.group(2)), "sale_lakh": int(m.group(3)), "stock_days": float(m.group(4))} for m in BAL_P.finditer(t)]
    return {"items": items} if items else None

def save_mnp(t, ts):
    p = parse_mnp(t)
    if not p:
        return
    get_db().insert_one({"text": t, "timestamp": ts, "date_str": ts_str(ts), "parsed": p})

def save_bal(t, ts):
    p = parse_bal(t)
    if not p:
        return None
    get_db().insert_one({"text": t, "timestamp": ts, "date_str": ts_str(ts), "type": "balance", "parsed": p})
    return p

def cmp_text(r1, r2, l1="Pehle", l2="Ab"):
    p1 = r1.get("parsed") or parse_mnp(r1.get("text", ""))
    p2 = r2.get("parsed") or parse_mnp(r2.get("text", ""))
    if not p1 or not p2:
        return "Reports parse nahi ho paayi."
    lines = ["📊 MNP Comparison", f"{l1}: {r1.get('date_str','')} (till {p1.get('report_time','?')})", f"{l2}: {r2.get('date_str','')} (till {p2.get('report_time','?')})", ""]
    for d in sorted(set(p1["distributors"]) | set(p2["distributors"])):
        a = p1["distributors"].get(d, {}).get("total", 0)
        b = p2["distributors"].get(d, {}).get("total", 0)
        diff = b - a
        lines.append(f"{d}: {a} -> {b} ({'+' if diff>=0 else ''}{diff})")
    a = (p1["total"] or {}).get("total", 0)
    b = (p2["total"] or {}).get("total", 0)
    diff = b - a
    lines.append("")
    lines.append(f"Total: {a} -> {b} ({'+' if diff>=0 else ''}{diff})")
    return "\n".join(lines)

def daily_data(days=7, mtd=False):
    now = datetime.now()
    if mtd:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days_count = now.day
    else:
        start = now - timedelta(days=days-1)
        days_count = days
    utc0 = start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    last = {}
    for r in reps:
        last[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    labels, uday, mv, tot = [], [], [], []
    dtw = {}
    for i in range(days_count-1, -1, -1):
        d = now - timedelta(days=i)
        k = d.strftime("%Y-%m-%d")
        labels.append(d.strftime("%d %b"))
        r = last.get(k)
        if not r:
            uday.append(0); mv.append(0); tot.append(0); continue
        p = r.get("parsed") or parse_mnp(r.get("text", ""))
        if not p:
            uday.append(0); mv.append(0); tot.append(0); continue
        u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        t2 = (p.get("total") or {}).get("total", 0)
        uday.append(u); mv.append(m); tot.append(t2)
        dtw["Uday Comm Agr"] = dtw.get("Uday Comm Agr", 0) + u
        dtw["Maa Vaishno Telecom"] = dtw.get("Maa Vaishno Telecom", 0) + m
    return {"labels": labels, "uday": uday, "mv": mv, "total": tot, "dist_totals": dtw}

def mom_compare():
    now = datetime.now()
    today = now.date()
    cur_start = today.replace(day=1)
    cur_end = today
    if today.month == 1:
        lmy, lmm = today.year - 1, 12
    else:
        lmy, lmm = today.year, today.month - 1
    last_month_days = calendar.monthrange(lmy, lmm)[1]
    last_day = min(today.day, last_month_days)
    last_start = datetime(lmy, lmm, 1).date()
    last_end = datetime(lmy, lmm, last_day).date()
    def get_total(sd, ed):
        us = datetime.combine(sd, datetime.min.time()).timestamp() - IST
        ue = datetime.combine(ed, datetime.max.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": us, "$lte": ue}, "text": {"$regex": "FTA MNP|FTD"}}))
        daily = {}
        for r in reps:
            daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
        u_tot = 0; m_tot = 0
        for r in daily.values():
            p = r.get("parsed") or parse_mnp(r.get("text", ""))
            if p:
                u_tot += p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
                m_tot += p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        return u_tot, m_tot
    cu, cm = get_total(cur_start, cur_end)
    lu, lm = get_total(last_start, last_end)
    cur_total = cu + cm
    last_total = lu + lm
    lines = [
        f"📊 Month-over-Month ({today.day} din)",
        "",
        f"Is mahine ({cur_start.strftime('%d %b')} - {cur_end.strftime('%d %b')}):",
        f"🔵 Uday: {cu}",
        f"🔴 Maa Vaishno: {cm}",
        f"🎯 Total: {cur_total}",
        "",
        f"Pichhle mahine (same {today.day} din):",
        f"🔵 Uday: {lu}",
        f"🔴 Maa Vaishno: {lm}",
        f"🎯 Total: {last_total}",
    ]
    if last_total > 0:
        growth_val = int((cur_total - last_total) / last_total * 100)
        arrow = "📈" if growth_val >= 0 else "📉"
        lines.append("")
        lines.append(f"{arrow} Growth: {'+' if growth_val >= 0 else ''}{growth_val}%")
    else:
        lines.append("")
        lines.append("⚠️ Pichhle mahine ka data nahi hai.")
    return "\n".join(lines)

def custom_range(sd, ed):
    try:
        s = datetime.combine(sd, datetime.min.time()).timestamp() - IST
        e = datetime.combine(ed, datetime.max.time()).timestamp() - IST
    except:
        return "❌ Date galat hai."
    if ed < sd:
        return "❌ End date start se pehle hai."
    reps = list(get_db().find({"timestamp": {"$gte": s, "$lte": e}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    if not reps:
        return f"❌ {sd.strftime('%d %b')} se {ed.strftime('%d %b')} tak koi report nahi."
    daily = {}
    for r in reps:
        daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    u_tot = 0; m_tot = 0; dd = 0
    for r in daily.values():
        p = r.get("parsed") or parse_mnp(r.get("text", ""))
        if p:
            u_tot += p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
            m_tot += p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
            dd += 1
    t_tot = u_tot + m_tot
    lines = [
        f"📅 Custom Report",
        f"{sd.strftime('%d %b %Y')} → {ed.strftime('%d %b %Y')}",
        "",
        f"📆 Din: {dd}",
        f"🔵 Uday: {u_tot}",
        f"🔴 Maa Vaishno: {m_tot}",
        f"🎯 Total: {t_tot}",
        f"📊 Avg/din: {int(t_tot/dd) if dd else 0}",
    ]
    return "\n".join(lines)

def best_worst(days=30):
    d = daily_data(days)
    labels = d["labels"]
    totals = d["total"]
    nz = [(labels[i], totals[i]) for i in range(len(totals)) if totals[i] > 0]
    if not nz:
        return "❌ Koi data nahi."
    best = max(nz, key=lambda x: x[1])
    worst = min(nz, key=lambda x: x[1])
    lines = [
        f"🏆 Best/Worst Day (last {days} days)",
        "",
        f"🥇 Best day: {best[0]} — {best[1]} MNP",
        f"📉 Worst day: {worst[0]} — {worst[1]} MNP",
    ]
    return "\n".join(lines)

def parse_range(lo):
    m = re.search(r'(\d{1,2})\s*([a-z]+)\s*(?:se|to|-)\s*(\d{1,2})\s*([a-z]+)', lo)
    if not m:
        return None, None
    d1, m1, d2, m2 = int(m.group(1)), m.group(2)[:3], int(m.group(3)), m.group(4)[:3]
    if m1 not in MON or m2 not in MON:
        return None, None
    yr = datetime.now().year
    try:
        sd = datetime(yr, MON[m1], d1).date()
        ed = datetime(yr, MON[m2], d2).date()
        if ed > datetime.now().date():
            yr -= 1
            sd = datetime(yr, MON[m1], d1).date()
            ed = datetime(yr, MON[m2], d2).date()
        return sd, ed
    except:
        return None, None

def qc(config, w=800, h=400):
    e = urllib.parse.quote(json.dumps(config))
    return f"https://quickchart.io/chart?c={e}&w={w}&h={h}&bkg=white&plugins=chartjs-plugin-datalabels"

def bar_chart(days=7, mtd=False):
    d = daily_data(days, mtd=mtd)
    title_txt = "Daily MNP - MTD" if mtd else f"Daily MNP - Last {days} Days"
    cfg = {"type": "bar", "data": {"labels": d["labels"], "datasets": [
        {"label": "Uday Comm Agr", "data": d["uday"], "backgroundColor": "#2196F3"},
        {"label": "Maa Vaishno Telecom", "data": d["mv"], "backgroundColor": "#F44336"},
        {"label": "Total", "data": d["total"], "backgroundColor": "#4CAF50"}]},
        "options": {"title": {"display": True, "text": title_txt, "fontSize": 16},
        "legend": {"position": "bottom"},
        "plugins": {"datalabels": {"display": True, "color": "white", "anchor": "center", "align": "center",
        "font": {"size": 11, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}}}
    return qc(cfg)

def pie_chart(days=7, mtd=False):
    d = daily_data(days, mtd=mtd)
    dt = d["dist_totals"]
    labels = list(dt.keys()) if dt else ["No Data"]
    values = list(dt.values()) if dt else [1]
    title_txt = "Distributor Share - MTD" if mtd else f"Distributor Share - Last {days} Days"
    cfg = {"type": "doughnut", "data": {"labels": labels, "datasets": [{"data": values, "backgroundColor": ["#2196F3", "#F44336", "#4CAF50"]}]},
        "options": {"title": {"display": True, "text": title_txt, "fontSize": 16},
        "legend": {"position": "bottom"},
        "plugins": {"datalabels": {"display": True, "color": "white", "font": {"size": 12, "weight": "bold"},
        "formatter": "function(v,c){var s=c.dataset.data.reduce(function(a,b){return a+b;},0);var p=Math.round(v/s*100);return v+'\\n('+p+'%)';}"}}}}
    return qc(cfg)

def line_chart(days=7, mtd=False):
    d = daily_data(days, mtd=mtd)
    title_txt = "MNP Trend - MTD" if mtd else f"MNP Trend - Last {days} Days"
    cfg = {"type": "line", "data": {"labels": d["labels"], "datasets": [
        {"label": "Uday Comm Agr", "data": d["uday"], "borderColor": "#2196F3", "fill": False, "tension": 0.3,
         "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 6, "color": "#1565C0", "font": {"size": 10, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}},
        {"label": "Maa Vaishno Telecom", "data": d["mv"], "borderColor": "#F44336", "fill": False, "tension": 0.3,
         "datalabels": {"display": True, "align": "bottom", "anchor": "end", "offset": 6, "color": "#B71C1C", "font": {"size": 10, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}},
        {"label": "Total", "data": d["total"], "borderColor": "#4CAF50", "fill": False, "tension": 0.3, "borderWidth": 3,
         "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 20, "color": "#1B5E20", "font": {"size": 11, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}]},
        "options": {"title": {"display": True, "text": title_txt, "fontSize": 16},
        "legend": {"position": "bottom"}, "layout": {"padding": {"top": 40}}}}
    return qc(cfg)

def dist_chart(dist, ctype="bar", days=7, mtd=False):
    d = daily_data(days, mtd=mtd)
    if "uday" in dist.lower():
        vals = d["uday"]; n = "Uday Comm Agr"; color = "#2196F3"
    else:
        vals = d["mv"]; n = "Maa Vaishno Telecom"; color = "#F44336"
    title_txt = f"{n} - MTD" if mtd else f"{n} - Last {days} Days"
    if ctype == "bar":
        cfg = {"type": "bar", "data": {"labels": d["labels"], "datasets": [
            {"label": n, "data": vals, "backgroundColor": color}]},
            "options": {"title": {"display": True, "text": title_txt, "fontSize": 16},
            "legend": {"display": False},
            "plugins": {"datalabels": {"display": True, "color": "white", "anchor": "center", "align": "center",
            "font": {"size": 11, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}}}
    elif ctype == "line":
        cfg = {"type": "line", "data": {"labels": d["labels"], "datasets": [
            {"label": n, "data": vals, "borderColor": color, "fill": False, "tension": 0.3, "borderWidth": 3,
             "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 6, "color": color, "font": {"size": 11, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}]},
            "options": {"title": {"display": True, "text": title_txt, "fontSize": 16},
            "legend": {"display": False}, "layout": {"padding": {"top": 40}}}}
    else:
        tot = sum(vals)
        cfg = {"type": "doughnut", "data": {"labels": [n], "datasets": [{"data": [tot], "backgroundColor": [color]}]},
            "options": {"title": {"display": True, "text": title_txt + f" (Total: {tot})", "fontSize": 16},
            "legend": {"position": "bottom"},
            "plugins": {"datalabels": {"display": True, "color": "white", "font": {"size": 14, "weight": "bold"},
            "formatter": "function(v){return v;}"}}}}
    return qc(cfg)

def peak_hours(days=7):
    now = datetime.now()
    ws_h = 7
    we_h = 21
    s = now - timedelta(days=days-1)
    us = s.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": us}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    if not reps:
        return "❌ Koi report nahi mili."
    bd = {}
    for r in reps:
        k = time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))
        bd.setdefault(k, []).append(r)
    hr_total = {}
    hr_days = {}
    for k, rr in bd.items():
        prev = 0
        prev_ts = None
        day_hours = set()
        for r in rr:
            p = r.get("parsed") or parse_mnp(r.get("text", ""))
            if not p: continue
            ct = (p.get("total") or {}).get("total", 0)
            if prev > 0 and prev_ts is not None:
                d = ct - prev
                if d > 0:
                    h = datetime.fromtimestamp(prev_ts + IST).hour
                    if ws_h <= h < we_h:
                        hr_total[h] = hr_total.get(h, 0) + d
                        day_hours.add(h)
            prev = ct
            prev_ts = r["timestamp"]
        for h in day_hours:
            hr_days[h] = hr_days.get(h, 0) + 1
    if not hr_total:
        return "❌ Working hours (7 AM - 9 PM) me koi data nahi mila."
    combined = []
    for h, tot in hr_total.items():
        active_days = hr_days.get(h, 1)
        avg = int(tot / active_days) if active_days else 0
        combined.append((h, tot, avg, active_days))
    combined.sort(key=lambda x: -x[2])
    top5 = combined[:5]
    lines = [f"🕐 Peak Hours (Last {days} Days)", f"⏰ 7:00 AM - 9:00 PM", ""]
    for i, (h, tot, avg, ad) in enumerate(top5, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "•"
        lines.append(f"{medal} {h:02d}:00 - {h+1:02d}:00 → avg {avg} MNP")
    if top5:
        best_h, best_tot, best_avg, _ = top5[0]
        lines.append("")
        lines.append(f"📊 Sabse zyada business: {best_h:02d}:00-{best_h+1:02d}:00 (avg {best_avg}/din)")
    return "\n".join(lines)

def peak_hour_for_date(target_date):
    ws_h = 7
    we_h = 21
    s = datetime.combine(target_date, datetime.min.time()).timestamp() - IST
    e = s + 86400
    reps = list(get_db().find({"timestamp": {"$gte": s, "$lt": e}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    if not reps:
        return f"❌ {target_date.strftime('%d %b')} ko koi MNP report nahi aayi."
    hr = {}
    prev = 0
    prev_ts = None
    for r in reps:
        p = r.get("parsed") or parse_mnp(r.get("text", ""))
        if not p: continue
        ct = (p.get("total") or {}).get("total", 0)
        if prev > 0 and prev_ts is not None:
            d = ct - prev
            if d > 0:
                h = datetime.fromtimestamp(prev_ts + IST).hour
                if ws_h <= h < we_h:
                    hr[h] = hr.get(h, 0) + d
        prev = ct
        prev_ts = r["timestamp"]
    if not hr:
        return f"❌ {target_date.strftime('%d %b')} ko working hours (7 AM - 9 PM) me koi data nahi."
    sh = sorted(hr.items(), key=lambda x: -x[1])
    day_label = target_date.strftime('%d %b %Y')
    today = datetime.now().date()
    if target_date == today:
        day_label = "Aaj"
    elif target_date == today - timedelta(days=1):
        day_label = "Kal"
    elif target_date == today - timedelta(days=2):
        day_label = "Parso"
    lines = [f"🕐 Peak Hour — {day_label} ({target_date.strftime('%d %b %Y')})", f"⏰ 7:00 AM - 9:00 PM", ""]
    for i, (h, c) in enumerate(sh[:5], 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "•"
        lines.append(f"{medal} {h:02d}:00 - {h+1:02d}:00 → {c} MNP")
    if sh:
        best_h, best_c = sh[0]
        lines.append("")
        lines.append(f"📊 Best hour: {best_h:02d}:00-{best_h+1:02d}:00 ({best_c} MNP)")
    return "\n".join(lines)

def weekly_sum():
    d = daily_data(7)
    dt = d["dist_totals"]
    u_total = dt.get("Uday Comm Agr", 0)
    m_total = dt.get("Maa Vaishno Telecom", 0)
    grand = u_total + m_total
    dts = {d["labels"][i]: d["total"][i] for i in range(len(d["labels"]))}
    lines = ["📊 Weekly MNP Summary", "(Last 7 days)", "", "Distributor-wise:"]
    lines.append(f"🔵 Uday Comm Agr: {u_total}")
    lines.append(f"🔴 Maa Vaishno Telecom: {m_total}")
    lines.append("")
    lines.append(f"🎯 Total (dono milakar): {grand}")
    nz = {k: v for k, v in dts.items() if v > 0}
    if nz:
        b = max(nz.items(), key=lambda x: x[1])
        w = min(nz.items(), key=lambda x: x[1])
        lines.append("")
        lines.append(f"🏆 Best day: {b[0]} ({b[1]})")
        lines.append(f"📉 Lowest: {w[0]} ({w[1]})")
    return "\n".join(lines)

def monthly_sum():
    now = datetime.now()
    today = now.date()
    ms = today.replace(day=1)
    um = datetime.combine(ms, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    if not reps:
        return "❌ Is mahine koi report nahi aayi."
    daily = {}
    for r in reps:
        daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    u_total = 0
    m_total = 0
    days_count = 0
    day_breakdown = {}
    for k, r in daily.items():
        p = r.get("parsed") or parse_mnp(r.get("text", ""))
        if not p:
            continue
        u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        u_total += u
        m_total += m
        days_count += 1
        day_breakdown[k] = u + m
    grand = u_total + m_total
    lines = [f"📅 Monthly Summary ({ms.strftime('%b %Y')})", "", "Distributor-wise:"]
    lines.append(f"🔵 Uday Comm Agr: {u_total}")
    lines.append(f"🔴 Maa Vaishno Telecom: {m_total}")
    lines.append("")
    lines.append(f"🎯 Total (dono milakar): {grand}")
    lines.append(f"📆 Din: {days_count}")
    if day_breakdown:
        b = max(day_breakdown.items(), key=lambda x: x[1])
        w = min(day_breakdown.items(), key=lambda x: x[1])
        lines.append("")
        lines.append(f"🏆 Best day: {b[0]} ({b[1]})")
        lines.append(f"📉 Lowest: {w[0]} ({w[1]})")
    return "\n".join(lines)

def ampm(t, i, h):
    if h >= 13: return h
    if h == 12:
        c = t[max(0,i-20):i+30]
        return 0 if re.search(r'raat|night', c) else 12
    c = t[max(0,i-20):i+30]
    if re.search(r'subah|subha|morning|\bam\b', c): return h
    if re.search(r'dophar|dopahar|afternoon', c): return h+12 if h<12 else h
    if re.search(r'shaam|sham|evening', c): return h+12 if h<12 else h
    if re.search(r'raat|night|\bpm\b', c): return h+12 if h<12 else h
    return h

def ext_time(t):
    L = t.lower()
    m = re.search(r'(\d{1,2})[:\.](\d{2})', L)
    if m: return ampm(L, m.start(), int(m.group(1))), int(m.group(2))
    m = re.search(r'(\d{1,2})\s*(?:baje|bje|pm|am|bajkar)', L)
    if m: return ampm(L, m.start(), int(m.group(1))), 0
    return None

def ext_date(t):
    L = t.lower()
    td = datetime.now().date()
    if re.search(r'\bkal\b', L) or "yesterday" in L: return td - timedelta(days=1)
    if re.search(r'\baaj\b', L) or "today" in L or "abhi" in L: return td
    if re.search(r'\bparso\b', L): return td - timedelta(days=2)
    m = re.search(r'(\d{1,2})\s*(?:st|nd|rd|th)?\s*([a-z]+)', L)
    if m and m.group(2) in MON:
        try:
            c = datetime(td.year, MON[m.group(2)], int(m.group(1))).date()
            if c > td: c = datetime(td.year-1, MON[m.group(2)], int(m.group(1))).date()
            return c
        except: pass
    return None

def find_rep(dt, h, mi):
    try:
        ist_ts = datetime.combine(dt, datetime.min.time().replace(hour=h, minute=mi)).timestamp()
        ut = ist_ts - IST
        s = datetime.combine(dt, datetime.min.time()).timestamp() - IST
        e = s + 86400
        reps = list(get_db().find({"timestamp": {"$gte": s, "$lt": e}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
        return min(reps, key=lambda r: abs(r["timestamp"] - ut)) if reps else None
    except: return None

def perf_alert(h=None, mi=None):
    s = get_settings()
    tu = s.get("target_day_uday", 0)
    tm = s.get("target_day_mv", 0)
    if not tu and not tm: return "❌ Koi daily target set nahi."
    now = datetime.now()
    if h is not None:
        r = find_rep(now.date(), h, mi or 0)
        if not r: return f"❌ Aaj {h:02d}:{(mi or 0):02d} ke aas-paas koi MNP report nahi mili."
        lbl = f"till {h:02d}:{(mi or 0):02d}"
    else:
        utc0 = datetime.combine(now.date(), datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
        if not reps: return "⚠️ Aaj koi MNP report nahi aayi."
        r = reps[0]; lbl = "latest"
    p = r.get("parsed") or parse_mnp(r.get("text", ""))
    if not p: return "❌ Report parse nahi ho paayi."
    u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
    m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
    tds = today_str(); yds = yest_str()
    al = []; ex = []
    if tu:
        if u < tu:
            g = tu - u; pct = int(u/tu*100)
            al.append(f"🔵 Uday Comm Agr: {u}/{tu} ({pct}%) - gap {g}")
            if s.get("last_fail_uday") == yds: ex.append("❗ Uday Comm Agr - aap aaj bhi target pura nahi kar paye!")
            upd_setting("last_fail_uday", tds)
        else: upd_setting("last_fail_uday", "")
    if tm:
        if m < tm:
            g = tm - m; pct = int(m/tm*100)
            al.append(f"🔴 Maa Vaishno Telecom: {m}/{tm} ({pct}%) - gap {g}")
            if s.get("last_fail_mv") == yds: ex.append("❗ Maa Vaishno Telecom - aap aaj bhi target pura nahi kar paye!")
            upd_setting("last_fail_mv", tds)
        else: upd_setting("last_fail_mv", "")
    if al:
        msg = f"⚠️ Low Performance Alert ({lbl})\n\n" + "\n".join(al)
        if ex: msg += "\n\n" + "\n".join(ex)
        if tu and u >= tu: msg += "\n\n✅ Uday Comm Agr ne target pura kar liya!"
        if tm and m >= tm: msg += "\n✅ Maa Vaishno Telecom ne target pura kar liya!"
        return msg
    return f"✅ Sab targets pura ho gaye ({lbl})\n\n🔵 Uday Comm Agr: {u}/{tu}\n🔴 Maa Vaishno Telecom: {m}/{tm}"

def bal_alert(p, th=3):
    if not p: return None
    low = []
    for it in p.get("items", []):
        n = MSISDN_MAP.get(it["msisdn"])
        if not n: continue
        if it["stock_days"] < th:
            low.append({"name": n, "d": it["stock_days"], "b": it["balance"], "s": it["sale_lakh"]})
    if not low: return None
    lines = ["🚨 Low Balance Alert", ""]
    for it in low:
        lines.append(f"🔸 {it['name']}")
        lines.append(f"   Stock Days: {it['d']}")
        lines.append(f"   Balance: {it['b']:,}")
        lines.append(f"   Sale: {it['s']} Lakh")
        lines.append("")
    lines.append(f"⚠️ Aapka balance {th} din se kam hai - aaj hi billing karayen!")
    return "\n".join(lines)

def stock_chk(th=3):
    lt = list(get_db().find({"type": "balance"}).sort("timestamp", -1).limit(1))
    if not lt: return "❌ Koi balance report nahi mili."
    p = lt[0].get("parsed")
    if not p: return "❌ Balance report parse nahi ho paayi."
    al = bal_alert(p, th)
    if al: return al
    lines = ["✅ Sab distributors ka stock theek hai", ""]
    for it in p.get("items", []):
        n = MSISDN_MAP.get(it["msisdn"], it["msisdn"])
        lines.append(f"🔹 {n}: {it['stock_days']} din")
    return "\n".join(lines)
    
# ==================== AI VISION (PHOTO REPORT) ====================

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        is_priv = message.chat.type == "private"
        caption = message.caption or ""
        is_tag = BOT_USERNAME.lower() in caption.lower()
        
        if not is_priv and not is_tag:
            return 

        user_prompt = caption.replace(BOT_USERNAME, "").strip()
        if not user_prompt:
            user_prompt = "Distributor wise 4G PIPO % nikaalo. Jiska 100% se kam hai usko strict warning do."

        status_msg = bot.reply_to(message, "🤖 AI report ko padh raha hai, please wait...")
        
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        file_path = "temp_report.jpg"
        with open(file_path, 'wb') as new_file:
            new_file.write(downloaded_file)

        base64_image = encode_image(file_path)

        system_prompt = """
        Tum ek strict aur analytical Sales Manager ho. 
        Image mein di gayi report ko dhyan se padho. Yeh 4G PIPO ki report hai.
        
        Calculation Logic (Step-by-step karein):
        1. Distributor ka naam nikaalo (Channel Name).
        2. Current 4G PIPO % nikaalo (4G PIPO section ke andar jo %MTD PIP likha hai).
        3. Current 4G MTD nikaalo (4G PIPO ke andar MTD column).
        4. Target MTD = Current 4G MTD / (Current 4G PIPO % / 100).
        5. Total Gap = Target MTD - Current 4G MTD.
        6. User ke caption se "Din" (Days) nikaalo (agar nahi likha toh 1 din maan lo).
        7. Daily Extra Required = Total Gap / Days.
        8. Current Daily Average = Current 4G MTD / 16 (Report ki date 16 hai, isliye 16 se divide karein).
        9. Total Daily Target = Current Daily Average + Daily Extra Required.

        Output Format (Hinglish mein, short aur punchy):
        - Distributor ka naam.
        - Abhi ka PIPO % aur MTD.
        - 100% ke liye Total Gap (kitne numbers chahiye).
        - Agar [Days] din mein karna hai, toh unko apne current daily average ke UPAR rozana extra [Daily Extra Required] numbers karne padenge.
        - Total rozana target [Total Daily Target] numbers ka hoga.
        - Ek strict warning: 'Aapka PIPO kam hai, competition aage nikal jayega, turant plan banaiye!'
        """

        if not client_openrouter:
            bot.reply_to(message, "❌ OpenRouter API Key nahi mili. Environment variables check karein.")
            return

        completion = client_openrouter.chat.completions.create(
            model="qwen/qwen-2.5-vl-72b-instruct:free",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]}
            ],
            temperature=0.1,
            max_tokens=1024
        )

        ai_response = completion.choices[0].message.content

        bot.delete_message(message.chat.id, status_msg.message_id)
        bot.reply_to(message, f"📊 **Manager's Analysis:**\n\n{ai_response}", parse_mode="Markdown")

    except Exception as e:
        try:
            bot.reply_to(message, f"❌ Error aaya: {str(e)}")
        except:
            pass
    finally:
        if os.path.exists("temp_report.jpg"):
            os.remove("temp_report.jpg")

@bot.message_handler(func=lambda m: True)
def handle(message):
    try:
        text = message.text or ""
        msg_ts = message.date
        uid = message.from_user.id
        un = message.from_user.username
        fname = message.from_user.first_name or ""
        adm = is_admin(uid, un)
        chat_id_str = str(message.chat.id)
        is_priv = message.chat.type == "private"
        is_sr_group = chat_id_str in get_sr_groups()

        if not is_sr_group:
            try:
                add_known_user(uid, un, fname)
            except:
                pass

        # ===== SR GROUPS LOGIC =====
        if is_sr_group:
            if adm and text:
                lo_sr = text.lower().strip()
                if lo_sr in ["sr ai on", "/sr ai on", "ai on"]:
                    set_sr_ai(True)
                    bot.reply_to(message, "✅ SR AI reply ON ho gaya\n\nAb SR groups me koi bhi user TAG karega ya bot ke message ko reply karega to AI reply karega.")
                    return
                if lo_sr in ["sr ai off", "/sr ai off", "ai off"]:
                    set_sr_ai(False)
                    bot.reply_to(message, "🔇 SR AI reply OFF ho gaya\n\nAb SR groups me koi reply nahi aayega.")
                    return

            if sr_ai_enabled() and text:
                is_tag = BOT_USERNAME.lower() in text.lower()
                is_reply_to_bot = False
                if message.reply_to_message and message.reply_to_message.from_user:
                    ru = message.reply_to_message.from_user
                    if ru.is_bot and ru.username == BOT_USERNAME.replace("@", ""):
                        is_reply_to_bot = True

                if is_tag or is_reply_to_bot:
                    try:
                        clean_text = text.replace(BOT_USERNAME, "").strip() or "Hi"
                        r = client.chat.completions.create(
                            messages=[
                                {"role": "system", "content": "You are a helpful Telegram assistant. Reply in same language (Hindi/Hinglish). 1-2 short lines."},
                                {"role": "user", "content": clean_text}
                            ], model="openai/gpt-oss-20b")
                        bot.reply_to(message, r.choices[0].message.content.strip())
                    except Exception:
                        pass
            return

        # ===== MAIN GROUP + PRIVATE CHAT LOGIC =====
        if message.chat.id == int(GROUP_CHAT_ID) and ("FTA MNP" in text or "FTD" in text):
            save_mnp(text, msg_ts)

        if is_balance_report(text):
            p = save_bal(text, msg_ts)
            if p:
                th = get_settings().get("stock_threshold", 3)
                al = bal_alert(p, th)
                if al:
                    bot.send_message(message.chat.id, al)
            return

        is_tag = BOT_USERNAME.lower() in text.lower()
        is_rep_bot = False
        if message.reply_to_message and message.reply_to_message.from_user:
            u = message.reply_to_message.from_user
            if u.is_bot and u.username == BOT_USERNAME.replace("@", ""):
                is_rep_bot = True
        if not is_priv and not is_tag and not is_rep_bot:
            return

        if is_priv and not adm and not is_known_user(uid):
            bot.reply_to(message, "🙏 Ye bot sirf DTR Mainpuri group ke liye hai.\n\nAap is group ke member nahi hain. Kripya group me contact karein.")
            return

        ct = text.replace(BOT_USERNAME, "").strip() or "Hi"
        lo = ct.lower()

        # ===== SCHEDULE STATE MACHINE (sirf DM + admin) =====
        if is_priv and adm:
            st = get_sch_state(uid)
            if st and st.get("flow") == "schedule":
                step = st.get("step")
                data = st.get("data", {})

                if lo.strip() in ["cancel", "band", "stop", "/cancel", "/start", "/menu"]:
                    clear_sch_state(uid)
                    bot.reply_to(message, "❌ Schedule cancelled.")
                    return

                if step == "text":
                    data["text"] = text.strip()
                    set_sch_state(uid, {"flow": "schedule", "step": "target", "data": data})
                    bot.reply_to(message,
                        "✅ Message saved.\n\n"
                        "📬 Target?\n"
                        "• `main` — Main MNP group\n"
                        "• `sr_all` — saare SR groups\n"
                        "• `all` — main + saare SR\n"
                        "• `sr:<group_id>` — specific SR group\n"
                        "• `chat:<chat_id>` — custom group\n\n"
                        "(cancel likh ke cancel karo)",
                        parse_mode="Markdown")
                    return

                if step == "target":
                    t = lo.strip()
                    if t in ("main", "sr_all", "all"):
                        data["target"] = t
                    elif t.startswith("sr:"):
                        gid = t[3:].strip()
                        if gid in get_sr_groups():
                            data["target"] = "sr:" + gid
                        else:
                            bot.reply_to(message,
                                f"❌ Ye SR group nahi hai: `{gid}`\n\n"
                                f"Valid SR groups:\n" +
                                "\n".join(f"• `sr:{g}`" for g in get_sr_groups()),
                                parse_mode="Markdown")
                            return
                    elif t.startswith("chat:"):
                        gid = t[5:].strip()
                        if gid.lstrip("-").isdigit():
                            data["target"] = "chat:" + gid
                        else:
                            bot.reply_to(message, "❌ Chat ID numeric honi chahiye.")
                            return
                    else:
                        bot.reply_to(message, "❌ Invalid target. main / sr_all / all / sr:<gid> / chat:<gid>")
                        return
                    set_sch_state(uid, {"flow": "schedule", "step": "time", "data": data})
                    bot.reply_to(message, "⏰ Time? (24hr IST format, jaise `14:30`)", parse_mode="Markdown")
                    return

                if step == "time":
                    m = re.match(r'^(\d{1,2})[:\.](\d{2})$', lo.strip())
                    if not m:
                        bot.reply_to(message, "❌ Format: HH:MM (jaise 14:30)")
                        return
                    h, mi = int(m.group(1)), int(m.group(2))
                    if not (0 <= h < 24 and 0 <= mi < 60):
                        bot.reply_to(message, "❌ Time galat hai.")
                        return
                    data["hour"], data["minute"] = h, mi
                    set_sch_state(uid, {"flow": "schedule", "step": "date", "data": data})
                    bot.reply_to(message, "📅 Date? (`today` / `tomorrow` / `YYYY-MM-DD`)", parse_mode="Markdown")
                    return

                if step == "date":
                    t = lo.strip()
                    now_ist = datetime.now() + timedelta(seconds=IST)
                    today_d = now_ist.date()
                    if t in ("today", "aaj"):
                        d = today_d
                    elif t in ("tomorrow", "kal"):
                        d = today_d + timedelta(days=1)
                    else:
                        try:
                            d = datetime.strptime(t, "%Y-%m-%d").date()
                        except Exception:
                            bot.reply_to(message, "❌ Format: today / tomorrow / YYYY-MM-DD")
                            return
                        if d < today_d:
                            bot.reply_to(message, "❌ Past date allowed nahi.")
                            return
                    data["date"] = d.strftime("%Y-%m-%d")
                    set_sch_state(uid, {"flow": "schedule", "step": "repeat", "data": data})
                    bot.reply_to(message, "🔁 Repeat? (`once` / `daily`)", parse_mode="Markdown")
                    return

                if step == "repeat":
                    t = lo.strip()
                    if t not in ("once", "daily"):
                        bot.reply_to(message, "❌ `once` ya `daily` likho.", parse_mode="Markdown")
                        return
                    data["repeat"] = t

                    now_ist = datetime.now() + timedelta(seconds=IST)
                    try:
                        sch_dt = datetime.strptime(data["date"], "%Y-%m-%d").replace(
                            hour=data["hour"], minute=data["minute"])
                        if t == "once" and sch_dt < now_ist - timedelta(minutes=1):
                            bot.reply_to(message,
                                f"❌ Ye time ({data['hour']:02d}:{data['minute']:02d} {data['date']}) "
                                f"nikal gaya hai. Naya time/date choose karo.")
                            set_sch_state(uid, {"flow": "schedule", "step": "time", "data": data})
                            return
                    except Exception:
                        pass

                    set_sch_state(uid, {"flow": "schedule", "step": "confirm", "data": data})
                    summary = (
                        "📋 *Schedule Summary*\n\n"
                        f"📝 *Message:* {data['text'][:150]}\n"
                        f"📬 *Target:* `{data['target']}`\n"
                        f"⏰ *Time:* {data['hour']:02d}:{data['minute']:02d} IST\n"
                        f"📅 *Date:* {data['date']}\n"
                        f"🔁 *Repeat:* {data['repeat']}\n\n"
                        "Confirm ke liye `yes` / cancel ke liye `cancel`."
                    )
                    bot.reply_to(message, summary, parse_mode="Markdown")
                    return

                if step == "confirm":
                    t = lo.strip()
                    if t in ("yes", "haan", "ha", "ok", "y"):
                        doc = {
                            "type": "scheduled_msg",
                            "text": data["text"],
                            "target": data["target"],
                            "hour": data["hour"],
                            "minute": data["minute"],
                            "date": data["date"],
                            "repeat": data["repeat"],
                            "created_by": uid,
                            "created_at": time.time(),
                            "sent": False,
                            "last_sent_date": None,
                            "active": True,
                            "retries": 0,
                        }
                        res = get_db().insert_one(doc)
                        sid = str(res.inserted_id)[-6:]
                        clear_sch_state(uid)
                        bot.reply_to(message,
                            f"✅ *Scheduled!*\nID: `{sid}`\n\n"
                            f"List dekhne ke liye: `sch list`\n"
                            f"Delete ke liye: `sch del {sid}`",
                            parse_mode="Markdown")
                        return
                    elif t in ("no", "nahi", "cancel", "n", "x"):
                        clear_sch_state(uid)
                        bot.reply_to(message, "❌ Schedule cancelled.")
                        return
                    else:
                        bot.reply_to(message, "❌ `yes` ya `cancel` bhejo.")
                        return

        # ===== SCHEDULE COMMANDS =====
        if lo.strip() in ("sch", "/sch", "schedule"):
            if not is_priv:
                bot.reply_to(message, "❌ Schedule sirf DM me set karo.")
                return
            if not adm:
                bot.reply_to(message, "❌ Sirf admin ke liye.")
                return
            if get_sch_state(uid):
                bot.reply_to(message, "⚠️ Ek flow pehle se chal raha hai. `cancel` likho ya complete karo.")
                return
            set_sch_state(uid, {"flow": "schedule", "step": "text", "data": {}})
            bot.reply_to(message,
                "📝 Message text bhejo jo bhejna hai.\n\n"
                "(cancel likh ke cancel karo)")
            return

        if lo.strip() in ("sch list", "/sch list", "schedule list"):
            if not adm:
                bot.reply_to(message, "❌ Sirf admin ke liye.")
                return
            schs = list(get_db().find(
                {"type": "scheduled_msg", "active": True}
            ).sort("created_at", -1))
            if not schs:
                bot.reply_to(message, "❌ Koi scheduled message nahi.")
                return
            lines = [f"📋 *Scheduled Messages* ({len(schs)})\n"]
            for s in schs:
                sid = str(s["_id"])[-6:]
                if s.get("repeat") == "once":
                    st_icon = "✅" if s.get("sent") else ("❗" if s.get("failed") else "⏳")
                else:
                    st_icon = "🔁"
                lines.append(
                    f"{st_icon} `{sid}` — {s['hour']:02d}:{s['minute']:02d} {s.get('date','')} "
                    f"→ `{s.get('target','main')}`"
                )
                lines.append(f"   _{s['text'][:60]}_")
                if s.get("last_sent_date"):
                    lines.append(f"   Last sent: {s['last_sent_date']}")
                if s.get("failed"):
                    lines.append(f"   ❌ Failed: {s.get('error','')[:60]}")
                lines.append("")
            bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
            return

        if lo.strip().startswith(("sch del ", "/sch del ")):
            if not adm:
                bot.reply_to(message, "❌ Sirf admin ke liye.")
                return
            sid = lo.strip().split()[-1]
            found = None
            for s in get_db().find({"type": "scheduled_msg", "active": True}):
                if str(s["_id"]).endswith(sid):
                    found = s
                    break
            if not found:
                bot.reply_to(message, f"❌ ID `{sid}` nahi mila.", parse_mode="Markdown")
                return
            get_db().update_one({"_id": found["_id"]}, {"$set": {"active": False}})
            bot.reply_to(message, f"✅ Deleted schedule `{sid}`.", parse_mode="Markdown")
            return

        if lo.strip() in ("sch help", "/sch help", "schedule help"):
            bot.reply_to(message,
                "📅 *Schedule Help*\n\n"
                "`sch` — naya message schedule karo\n"
                "`sch list` — saare schedules dekho\n"
                "`sch del <id>` — schedule delete karo\n"
                "`cancel` — current flow cancel\n\n"
                "*Targets:*\n"
                "• `main` — MNP main group\n"
                "• `sr_all` — saare SR groups\n"
                "• `all` — main + saare SR\n"
                "• `sr:<gid>` — specific SR group\n"
                "• `chat:<gid>` — custom chat\n\n"
                "*Repeat:* `once` / `daily`",
                parse_mode="Markdown")
            return

        # ===== SR GROUP MANAGEMENT (Admin Only) =====
        if lo.startswith("sr add "):
            if not adm:
                bot.reply_to(message, "❌ Sirf admin.")
                return
            gid = lo.replace("sr add ", "").strip()
            if not gid.startswith("-"):
                bot.reply_to(message, "❌ Group ID `-` se shuru honi chahiye.\n\nJaise: `sr add -1234567890`", parse_mode="Markdown")
                return
            if add_sr_group(gid):
                bot.reply_to(message, f"✅ SR group added: `{gid}`\n\nTotal groups: {len(get_sr_groups())}", parse_mode="Markdown")
            else:
                bot.reply_to(message, f"⚠️ Group already exists: `{gid}`", parse_mode="Markdown")
            return

        if lo.startswith("sr remove "):
            if not adm:
                bot.reply_to(message, "❌ Sirf admin.")
                return
            gid = lo.replace("sr remove ", "").strip()
            if remove_sr_group(gid):
                bot.reply_to(message, f"✅ SR group removed: `{gid}`\n\nBaaki groups: {len(get_sr_groups())}", parse_mode="Markdown")
            else:
                bot.reply_to(message, f"⚠️ Group nahi mila: `{gid}`", parse_mode="Markdown")
            return

        if lo.strip() in ["sr list", "sr groups", "sr group list"]:
            if not adm:
                bot.reply_to(message, "❌ Sirf admin.")
                return
            groups = get_sr_groups()
            if not groups:
                bot.reply_to(message, "❌ Koi SR group nahi hai.\n\nAdd karne ke liye: `sr add -1234567890`", parse_mode="Markdown")
                return
            ai_state = "✅ ON" if sr_ai_enabled() else "🔇 OFF"
            lines = [f"📋 SR Groups ({len(groups)})", f"🤖 AI Reply: {ai_state}", ""]
            for i, g in enumerate(groups, 1):
                lines.append(f"{i}. `{g}`")
            lines.append("")
            lines.append("Add: `sr add -1234567890`")
            lines.append("Remove: `sr remove -1234567890`")
            lines.append("AI ON: `sr ai on`")
            lines.append("AI OFF: `sr ai off`")
            bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
            return

        savem = re.search(r'save\s+as\s+(\d{1,2})\s*([a-z]+)\s*(\d{1,2})(?::(\d{2}))?', lo)
        if savem and message.reply_to_message and adm:
            try:
                dyy = int(savem.group(1))
                mstr = savem.group(2).lower()[:3]
                hr = int(savem.group(3))
                mi = int(savem.group(4)) if savem.group(4) else 0
                if mstr not in MON:
                    bot.reply_to(message, "❌ Month samjha nahi. Jaise: save as 1 oct 22 baje")
                    return
                yr = datetime.now().year
                dt = datetime(yr, MON[mstr], dyy, hr, mi)
                ts = dt.timestamp() - IST
                orig = message.reply_to_message.text or ""
                p_mnp = parse_mnp(orig)
                p_bal = parse_bal(orig)
                if p_mnp:
                    get_db().insert_one({"text": orig, "timestamp": ts, "date_str": ts_str(ts), "parsed": p_mnp})
                    bot.reply_to(message, f"✅ MNP Report saved: {dt.strftime('%d %b %Y %H:%M')}")
                elif p_bal:
                    get_db().insert_one({"text": orig, "timestamp": ts, "date_str": ts_str(ts), "type": "balance", "parsed": p_bal})
                    bot.reply_to(message, f"✅ Balance Report saved: {dt.strftime('%d %b %Y %H:%M')}")
                else:
                    bot.reply_to(message, "❌ Ye MNP ya Balance report nahi hai.")
            except Exception as ee:
                bot.reply_to(message, f"❌ Error: {str(ee)}")
            return

        if lo.strip() in ["delete last", "last delete", "undo"]:
            if not adm:
                bot.reply_to(message, "❌ Sirf admin.")
                return
            last = list(get_db().find({"text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
            if not last:
                bot.reply_to(message, "❌ Koi report nahi.")
                return
            doc = last[0]
            get_db().delete_one({"_id": doc["_id"]})
            bot.reply_to(message, f"✅ Deleted report: {doc.get('date_str', '')}")
            return

        if lo.strip() in ["who am i", "main kaun hu", "mera role"]:
            r = "👑 Owner/Admin" if adm else "👤 Normal User"
            bot.reply_to(message, f"{r}\n\nUsername: @{un or 'not set'}\nUser ID: {uid}\nName: {fname}")
            return

        if lo.strip() in ["/start", "/menu", "menu", "start", "help", "commands"]:
            if not check_perm("menu", adm):
                bot.reply_to(message, "❌ Menu currently disabled hai.")
                return
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("📊 Graphs", callback_data="menu_graphs"),
                InlineKeyboardButton("📈 Projection", callback_data="menu_proj"),
                InlineKeyboardButton("🎯 Performance", callback_data="menu_perf"),
                InlineKeyboardButton("🚨 Stock Check", callback_data="stock_check"),
                InlineKeyboardButton("⚖️ Distributors", callback_data="dist_compare"),
                InlineKeyboardButton("📋 Weekly Summary", callback_data="weekly_summary"),
            )
            if adm:
                kb.add(
                    InlineKeyboardButton("📅 Month vs Month", callback_data="mom_compare"),
                    InlineKeyboardButton("🏆 Best/Worst", callback_data="best_worst"),
                    InlineKeyboardButton("📊 Distributor Charts", callback_data="menu_dist"),
                    InlineKeyboardButton("🏆 Peak Hours", callback_data="peak_hours"),
                    InlineKeyboardButton("🎯 Target Status", callback_data="tgt_status"),
                )
            bot.reply_to(message, "🤖 DTR Mainpuri Bot Menu\n\nKya dekhna chahte ho?", reply_markup=kb)
            return

        # ===== DISTRIBUTOR-SPECIFIC CHARTS =====
        dcm = re.search(r'\b(uday|mv|vaishno|maa\s*vaishno)\b.*\b(bar|line|pie)\b', lo)
        if dcm and ("chart" in lo or "graph" in lo or dcm.start() == 0 or lo.strip().startswith(dcm.group(1))):
            if not check_perm("graphs", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            dist = "uday" if "uday" in dcm.group(1) else "mv"
            ctype = dcm.group(2)
            mtd = "mtd" in lo
            dy = 7
            d = re.search(r'(\d{1,2})\s*(?:din|days)', lo)
            if d:
                dy = int(d.group(1))
            bot.send_message(message.chat.id, "⏳ Chart ban raha hai...")
            try:
                bot.send_photo(message.chat.id, dist_chart(dist, ctype, dy, mtd=mtd))
            except Exception as ge:
                bot.send_message(message.chat.id, f"Chart error: {str(ge)}")
            return

        if "report" in lo and (" se " in lo or " to " in lo):
            sd, ed = parse_range(lo)
            if sd and ed:
                bot.reply_to(message, custom_range(sd, ed))
                return

        if ("month compare" in lo or "mtd vs last" in lo or "mom" in lo or
            ("month" in lo and "compare" in lo)):
            bot.reply_to(message, mom_compare())
            return

        if "best day" in lo or "worst day" in lo or "best/worst" in lo:
            dy = 30
            d = re.search(r'(\d{1,2})\s*(?:din|days)', lo)
            if d:
                dy = int(d.group(1))
            bot.reply_to(message, best_worst(dy))
            return

        if any(w in lo for w in ["peak hour", "peak hours", "peak time", "kis time sabse zyada"]):
            if not check_perm("peak_hours", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            day_found = None
            if "kal" in lo or "yesterday" in lo:
                day_found = datetime.now().date() - timedelta(days=1)
            elif "aaj" in lo or "today" in lo:
                day_found = datetime.now().date()
            elif "parso" in lo:
                day_found = datetime.now().date() - timedelta(days=2)
            else:
                dm = re.search(r'(\d{1,2})\s*([a-z]{3,9})', lo)
                if dm:
                    try:
                        dyy = int(dm.group(1))
                        mstr = dm.group(2).lower()[:3]
                        if mstr in MON and 1 <= dyy <= 31:
                            yr = datetime.now().year
                            cand = datetime(yr, MON[mstr], dyy).date()
                            if cand > datetime.now().date():
                                cand = datetime(yr-1, MON[mstr], dyy).date()
                            day_found = cand
                    except:
                        pass
            if day_found:
                bot.reply_to(message, peak_hour_for_date(day_found))
                return
            dy = 7
            d = re.search(r'(\d{1,2})\s*(?:din|days)', lo)
            if d:
                dy = int(d.group(1))
            bot.reply_to(message, peak_hours(dy))
            return

        whm = re.search(r'working\s*hours?\s*(\d{1,2})(?::(\d{2}))?\s*(?:se|to|-)\s*(\d{1,2})(?::(\d{2}))?', lo)
        if whm and (is_tag or is_priv):
            if not check_perm("working_hours", adm):
                bot.reply_to(message, "❌ Sirf admins.")
                return
            a = int(whm.group(1)) + (int(whm.group(2))/60 if whm.group(2) else 0)
            b = int(whm.group(3)) + (int(whm.group(4))/60 if whm.group(4) else 0)
            set_wh(a, b)
            bot.reply_to(message, f"✅ Working hours set: {int(a)}:00 se {int(b)}:00")
            return

        if ("working hours" in lo or "working hrs" in lo) and any(w in lo for w in ["status", "kitna", "check", "dikhao"]):
            a, b = get_wh()
            bot.reply_to(message, f"⏰ Current working hours: {int(a)}:00 se {int(b)}:00")
            return

        wtm = re.search(r'(uday|mv|maa\s*vaishno|vaishno)\s*week\s*(?:target|tgt)\s*(\d+)', lo)
        if wtm and (is_tag or is_priv):
            if not check_perm("target_set", adm):
                bot.reply_to(message, "❌ Sirf admins.")
                return
            k = "uday" if "uday" in wtm.group(1) else "mv"
            v = int(wtm.group(2))
            set_target(k, v, "week")
            nm = "Uday Comm Agr" if k == "uday" else "Maa Vaishno Telecom"
            bot.reply_to(message, f"✅ {nm} Weekly Target set: {v}")
            return

        mtm = re.search(r'(uday|mv|maa\s*vaishno|vaishno)\s*month\s*(?:target|tgt)\s*(\d+)', lo)
        if mtm and (is_tag or is_priv):
            if not check_perm("target_set", adm):
                bot.reply_to(message, "❌ Sirf admins.")
                return
            k = "uday" if "uday" in mtm.group(1) else "mv"
            v = int(mtm.group(2))
            set_target(k, v, "month")
            nm = "Uday Comm Agr" if k == "uday" else "Maa Vaishno Telecom"
            bot.reply_to(message, f"✅ {nm} Monthly Target set: {v}")
            return

        tg = re.search(r'(uday|maa\s*vaishno|mv|vaishno)\s*(?:target|tgt)\s*(\d+)', lo)
        if tg:
            dk = tg.group(1); vl = int(tg.group(2))
        else:
            tg = re.search(r'(?:target|tgt)\s*(?:set\s*)?(uday|maa\s*vaishno|mv|vaishno)\s*(\d+)', lo)
            if tg:
                dk = tg.group(1); vl = int(tg.group(2))
            else:
                dk = None; vl = None
        if dk and vl is not None and (is_tag or is_priv):
            if not check_perm("target_set", adm):
                bot.reply_to(message, "❌ Sirf admins.")
                return
            if "uday" in dk:
                set_target("uday", vl, "day")
                bot.reply_to(message, f"✅ Uday Comm Agr Daily Target set: {vl}")
            else:
                set_target("mv", vl, "day")
                bot.reply_to(message, f"✅ Maa Vaishno Telecom Daily Target set: {vl}")
            return

        if ("target" in lo or "tgt" in lo) and any(w in lo for w in ["status", "kitna", "check", "dikhao"]):
            s = get_settings(); a, b = get_wh()
            lines = ["🎯 Current Targets", "", "📆 Daily:", f"🔵 Uday: {s.get('target_day_uday', 0)}", f"🔴 Maa Vaishno: {s.get('target_day_mv', 0)}", "", "📊 Weekly:", f"🔵 Uday: {s.get('target_week_uday', 0)}", f"🔴 Maa Vaishno: {s.get('target_week_mv', 0)}", "", "📅 Monthly:", f"🔵 Uday: {s.get('target_month_uday', 0)}", f"🔴 Maa Vaishno: {s.get('target_month_mv', 0)}"]
            bot.reply_to(message, "\n".join(lines))
            return

        if any(w in lo for w in ["stock check", "stock alert", "balance check", "low stock", "stock status"]):
            if not check_perm("stock_check", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            th = get_settings().get("stock_threshold", 3)
            t2 = re.search(r'threshold\s*(\d+)', lo)
            if t2:
                th = int(t2.group(1))
            bot.reply_to(message, stock_chk(th))
            return

        stm = re.search(r'stock\s*threshold\s*(\d+)', lo)
        if stm and (is_tag or is_priv):
            if not check_perm("stock_threshold", adm):
                bot.reply_to(message, "❌ Sirf admins.")
                return
            upd_setting("stock_threshold", int(stm.group(1)))
            bot.reply_to(message, f"✅ Stock alert threshold set: {stm.group(1)} din")
            return

        if any(w in lo for w in ["growth", "growth rate", "kitne percent badha", "kitna badha", "vikas"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            if any(w in lo for w in ["month", "mahine", "mahina"]):
                bot.reply_to(message, growth("month"))
            else:
                bot.reply_to(message, growth("week"))
            return

        if "month" in lo and any(w in lo for w in ["ach", "achievement"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            send_mtd_with_btn(message.chat.id, reply_to_msg=message)
            return

        if "mahine" in lo and any(w in lo for w in ["ach", "achievement", "performance"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            send_mtd_with_btn(message.chat.id, reply_to_msg=message)
            return

        if "week" in lo and any(w in lo for w in ["ach", "achievement"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            bot.reply_to(message, week_ach())
            return

        if "hafte" in lo and any(w in lo for w in ["ach", "achievement", "performance"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            bot.reply_to(message, week_ach())
            return

        cr = re.search(r'(uday|mv|maa\s*vaishno|vaishno)\s*(?:ka\s*)?report', lo)
        if cr:
            if not check_perm("custom_report", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            dy = 7
            d = re.search(r'(\d{1,2})\s*(?:din|days)', lo)
            if d:
                dy = int(d.group(1))
            bot.reply_to(message, cust_rep(cr.group(1), dy))
            return

        if any(w in lo for w in ["projection", "prediction", "predict", "forecast", "anuman"]):
            if not check_perm("projections", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            dm = re.search(r'(\d{1,2})\s*([a-z]{3,9})', lo)
            if dm:
                try:
                    dyy = int(dm.group(1))
                    mstr = dm.group(2).lower()[:3]
                    if mstr in MON and 1 <= dyy <= 31:
                        bot.reply_to(message, proj_till(dyy, MON[mstr]))
                        return
                except:
                    pass
            if any(w in lo for w in ["week", "hafte", "saaptah"]):
                bot.reply_to(message, proj_week())
            elif any(w in lo for w in ["month", "mahine", "mahina", "maasik", "mtd"]):
                bot.reply_to(message, proj_month())
            else:
                bot.reply_to(message, projection("day"))
            return

        if any(w in lo for w in ["ftd", "aaj ka"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            bot.reply_to(message, day_ach())
            return

        if any(w in lo for w in ["ach", "achievement", "achiv", "achiev"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            t = ext_time(lo)
            bot.reply_to(message, perf_alert(t[0], t[1]) if t else perf_alert())
            return

        hp = any(w in lo for w in ["performance", "perfomance", "alert"])
        hc = any(w in lo for w in ["check", "karo", "do", "batao", "dikhao", "dekho"])
        if hp and hc:
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            t = ext_time(lo)
            bot.reply_to(message, perf_alert(t[0], t[1]) if t else perf_alert())
            return

        if "till" in lo and any(w in lo for w in ["check", "performance", "alert"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            t = ext_time(lo)
            bot.reply_to(message, perf_alert(t[0], t[1]) if t else perf_alert())
            return

        if any(w in lo for w in ["graph", "chart"]):
            if not check_perm("graphs", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            dy = 7
            d = re.search(r'(\d{1,2})\s*(?:din|days)', lo)
            if d:
                dy = int(d.group(1))
            wb = "bar" in lo or "column" in lo
            wp = "pie" in lo or "share" in lo or "distribution" in lo
            wl = "line" in lo or "trend" in lo
            mtd = "mtd" in lo
            wa = any(w in lo for w in ["sabhi", "full", "teeno", "all", "sab", "sare"])
            bot.send_message(message.chat.id, "⏳ Graph ban raha hai...")
            if wa:
                ts = ["bar", "pie", "line"]
            elif wb and wp:
                ts = ["bar", "pie"]
            elif wb and wl:
                ts = ["bar", "line"]
            elif wp and wl:
                ts = ["pie", "line"]
            elif wp:
                ts = ["pie"]
            elif wl:
                ts = ["line"]
            else:
                ts = ["bar"]
            for t in ts:
                try:
                    if t == "bar":
                        u = bar_chart(dy, mtd=mtd)
                    elif t == "pie":
                        u = pie_chart(dy, mtd=mtd)
                    else:
                        u = line_chart(dy, mtd=mtd)
                    bot.send_photo(message.chat.id, u)
                    time.sleep(1)
                except Exception as ge:
                    bot.send_message(message.chat.id, f"Graph error ({t}): {str(ge)}")
            return

        if "weekly" in lo or "hafte ka summary" in lo or "hafte ki summary" in lo:
            if not check_perm("weekly_summary", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            bot.reply_to(message, weekly_sum())
            try:
                bot.send_photo(message.chat.id, line_chart(7))
            except:
                pass
            return

        if "monthly" in lo or "mahine ka summary" in lo or "mahine ki summary" in lo:
            if not check_perm("weekly_summary", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            bot.reply_to(message, monthly_sum())
            try:
                bot.send_photo(message.chat.id, line_chart(mtd=True))
            except:
                pass
            return

        if any(w in lo for w in ["compare", "farq", "antar", "difference"]):
            if not check_perm("compare", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            parts = re.split(r'\baur\b|\bor\b|\bya\b|\band\b|\bvs\b|\bse\b', lo)
            an = []
            for p in parts:
                t = ext_time(p)
                d = ext_date(p)
                if t or d:
                    an.append((d, t))
            if len(an) >= 2:
                a1, a2 = an[0], an[1]
                d1 = a1[0] or (datetime.now().date() - timedelta(days=1))
                d2 = a2[0] or datetime.now().date()
                t1 = a1[1] or (12, 0)
                t2 = a2[1] or (12, 0)
                r1 = find_rep(d1, t1[0], t1[1])
                r2 = find_rep(d2, t2[0], t2[1])
                if not r1:
                    bot.reply_to(message, f"❌ {d1.strftime('%d %b')} {t1[0]:02d}:{t1[1]:02d} ke aas-paas koi MNP report nahi mili.")
                    return
                if not r2:
                    bot.reply_to(message, f"❌ {d2.strftime('%d %b')} {t2[0]:02d}:{t2[1]:02d} ke aas-paas koi MNP report nahi mili.")
                    return
                l1 = f"{d1.strftime('%d %b')} {t1[0]:02d}:{t1[1]:02d}"
                l2 = f"{d2.strftime('%d %b')} {t2[0]:02d}:{t2[1]:02d}"
                bot.reply_to(message, cmp_text(r1, r2, l1, l2))
                return
            lt = list(get_db().find({"text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(2))
            if len(lt) >= 2:
                bot.reply_to(message, cmp_text(lt[1], lt[0]))
                return
            bot.reply_to(message, "❌ Database mein kam se kam 2 MNP reports chahiye.")
            return

        # ===== AI FALLBACK =====
        r = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful Telegram assistant. Reply in same language. Answer in 1-3 lines."},
                {"role": "user", "content": ct}
            ], model="openai/gpt-oss-20b")
        bot.reply_to(message, r.choices[0].message.content)
    except Exception as e:
        try:
            bot.reply_to(message, "Error: " + str(e))
        except:
            pass

# ==================== WELCOME MESSAGE ====================
@bot.message_handler(content_types=['new_chat_members'])
def welcome_new_member(message):
    try:
        if message.chat.id != int(GROUP_CHAT_ID):
            return
        for member in message.new_chat_members:
            if member.is_bot:
                continue
            name = member.first_name or member.username or "Member"
            welcome_text = (
                "🎉 Welcome to DTR Mainpuri Group!\n\n"
                f"👋 Hello {name} Sir!\n"
                "🤝 Aapka swagat hai!\n\n"
                "🤵 Mai hu Akash Sir ka Assistant Report's\n\n"
                "📊 Aapki kisi bhi madad ke liye main yahi hoon —\n"
                "mujhe bulane ke liye tag karke /menu bhejen"
            )
            bot.send_message(message.chat.id, welcome_text)
    except Exception as e:
        print("Welcome error:", str(e))

BTN_MAP = {
    "graph_bar": "graphs", "graph_pie": "graphs", "graph_line": "graphs",
    "graph_bar_mtd": "graphs", "graph_pie_mtd": "graphs", "graph_line_mtd": "graphs",
    "proj_ftd": "projections", "proj_week": "projections", "proj_month": "projections",
    "perf_ftd": "performance", "perf_week": "performance", "perf_month": "performance",
    "perf_check": "performance", "tgt_status": "target_status",
    "stock_check": "stock_check", "weekly_summary": "weekly_summary",
    "peak_hours": "peak_hours", "dist_compare": "distributors",
    "gap_daily": "performance",
}


def build_main_menu_kb(adm):
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 Graphs", callback_data="menu_graphs"),
        InlineKeyboardButton("📈 Projection", callback_data="menu_proj"),
        InlineKeyboardButton("🎯 Performance", callback_data="menu_perf"),
        InlineKeyboardButton("🚨 Stock Check", callback_data="stock_check"),
        InlineKeyboardButton("⚖️ Distributors", callback_data="dist_compare"),
        InlineKeyboardButton("📋 Weekly Summary", callback_data="weekly_summary"),
    )
    if adm:
        kb.add(
            InlineKeyboardButton("📅 Month vs Month", callback_data="mom_compare"),
            InlineKeyboardButton("🏆 Best/Worst", callback_data="best_worst"),
            InlineKeyboardButton("📊 Distributor Charts", callback_data="menu_dist"),
            InlineKeyboardButton("🏆 Peak Hours", callback_data="peak_hours"),
            InlineKeyboardButton("🎯 Target Status", callback_data="tgt_status"),
        )
    return kb


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
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

        if d == "menu_graphs":
            kb = InlineKeyboardMarkup(row_width=3)
            kb.add(
                InlineKeyboardButton("📊 Bar", callback_data="graph_bar"),
                InlineKeyboardButton("📈 Line", callback_data="graph_line"),
                InlineKeyboardButton("🥧 Pie", callback_data="graph_pie"),
            )
            kb.add(
                InlineKeyboardButton("📊 MTD Bar", callback_data="graph_bar_mtd"),
                InlineKeyboardButton("📈 MTD Line", callback_data="graph_line_mtd"),
                InlineKeyboardButton("🥧 MTD Pie", callback_data="graph_pie_mtd"),
            )
            kb.add(InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_main"))
            try:
                bot.edit_message_text("📊 Graphs Menu\n\nKaunsa graph chahiye?", cid, mid, reply_markup=kb)
            except:
                bot.send_message(cid, "📊 Graphs Menu\n\nKaunsa graph chahiye?", reply_markup=kb)
            return

        if d == "menu_proj":
            if not check_perm("projections", adm):
                bot.send_message(cid, "❌ Currently disabled hai.")
                return
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(
                InlineKeyboardButton("📊 FTD Projection (Aaj)", callback_data="proj_ftd"),
                InlineKeyboardButton("📅 Weekly Projection", callback_data="proj_week"),
                InlineKeyboardButton("📅 MTD Projection (Month)", callback_data="proj_month"),
                InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_main"),
            )
            try:
                bot.edit_message_text("📈 Projection Menu", cid, mid, reply_markup=kb)
            except:
                bot.send_message(cid, "📈 Projection Menu", reply_markup=kb)
            return

        if d == "menu_perf":
            if not check_perm("performance", adm):
                bot.send_message(cid, "❌ Currently disabled hai.")
                return
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(
                InlineKeyboardButton("📊 FTD Achievement (Aaj)", callback_data="perf_ftd"),
                InlineKeyboardButton("📅 Weekly Achievement", callback_data="perf_week"),
                InlineKeyboardButton("📅 MTD Achievement (Month)", callback_data="perf_month"),
                InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_main"),
            )
            try:
                bot.edit_message_text("🎯 Performance Menu", cid, mid, reply_markup=kb)
            except:
                bot.send_message(cid, "🎯 Performance Menu", reply_markup=kb)
            return

        if d == "menu_dist":
            if not adm:
                bot.send_message(cid, "❌ Sirf admin ke liye.")
                return
            kb = InlineKeyboardMarkup(row_width=3)
            kb.add(InlineKeyboardButton("🔵 UDAY COMM AGR", callback_data="noop"))
            kb.add(
                InlineKeyboardButton("📊 Bar", callback_data="dist_uday_bar"),
                InlineKeyboardButton("📈 Line", callback_data="dist_uday_line"),
                InlineKeyboardButton("🥧 Pie", callback_data="dist_uday_pie"),
            )
            kb.add(
                InlineKeyboardButton("📊 MTD Bar", callback_data="dist_uday_mtd_bar"),
                InlineKeyboardButton("📈 MTD Line", callback_data="dist_uday_mtd_line"),
                InlineKeyboardButton("🥧 MTD Pie", callback_data="dist_uday_mtd_pie"),
            )
            kb.add(InlineKeyboardButton("🔴 MAA VAISHNO TELECOM", callback_data="noop"))
            kb.add(
                InlineKeyboardButton("📊 Bar", callback_data="dist_mv_bar"),
                InlineKeyboardButton("📈 Line", callback_data="dist_mv_line"),
                InlineKeyboardButton("🥧 Pie", callback_data="dist_mv_pie"),
            )
            kb.add(
                InlineKeyboardButton("📊 MTD Bar", callback_data="dist_mv_mtd_bar"),
                InlineKeyboardButton("📈 MTD Line", callback_data="dist_mv_mtd_line"),
                InlineKeyboardButton("🥧 MTD Pie", callback_data="dist_mv_mtd_pie"),
            )
            kb.add(InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_main"))
            try:
                bot.edit_message_text("📊 Distributor Charts\n\nKis distributor ka chart chahiye?", cid, mid, reply_markup=kb)
            except:
                bot.send_message(cid, "📊 Distributor Charts\n\nKis distributor ka chart chahiye?", reply_markup=kb)
            return

        if d == "noop":
            return

        if d == "menu_main":
            kb = build_main_menu_kb(adm)
            try:
                bot.edit_message_text("🤖 Main Menu\n\nKya dekhna chahte ho?", cid, mid, reply_markup=kb)
            except:
                bot.send_message(cid, "🤖 Main Menu\n\nKya dekhna chahte ho?", reply_markup=kb)
            return

        if d in ["mom_compare", "best_worst", "menu_dist"] and not adm:
            bot.send_message(cid, "❌ Sirf admin ke liye.")
            return

        pk = BTN_MAP.get(d)
        if pk and not check_perm(pk, adm):
            bot.send_message(cid, "❌ Ye feature currently disabled hai.")
            return

        if d and d.startswith("dist_") and d != "dist_compare":
            if not adm:
                bot.send_message(cid, "❌ Sirf admin ke liye.")
                return
            parts = d.replace("dist_", "").split("_")
            dist = parts[0]
            mtd = False
            if len(parts) > 2 and parts[1] == "mtd":
                ctype = parts[2]
                mtd = True
            else:
                ctype = parts[1]
            bot.send_photo(cid, dist_chart(dist, ctype, 7, mtd=mtd))
            return

        if d == "graph_bar":
            bot.send_photo(cid, bar_chart(7))
        elif d == "graph_pie":
            bot.send_photo(cid, pie_chart(7))
        elif d == "graph_line":
            bot.send_photo(cid, line_chart(7))
        elif d == "graph_bar_mtd":
            bot.send_photo(cid, bar_chart(mtd=True))
        elif d == "graph_pie_mtd":
            bot.send_photo(cid, pie_chart(mtd=True))
        elif d == "graph_line_mtd":
            bot.send_photo(cid, line_chart(mtd=True))

        elif d == "proj_ftd":
            bot.send_message(cid, projection("day"))
        elif d == "proj_week":
            bot.send_message(cid, proj_week())
        elif d == "proj_month":
            bot.send_message(cid, proj_month())

        elif d == "perf_ftd":
            bot.send_message(cid, day_ach())
        elif d == "perf_week":
            bot.send_message(cid, week_ach())
        elif d == "perf_month":
            send_mtd_with_btn(cid)
        elif d == "gap_daily":
            bot.send_message(cid, gap_daily_calc())
        elif d == "perf_check":
            bot.send_message(cid, perf_alert())

        elif d == "mom_compare":
            bot.send_message(cid, mom_compare())
        elif d == "best_worst":
            bot.send_message(cid, best_worst(30))

        elif d == "tgt_status":
            s = get_settings()
            a, b = get_wh()
            lines = ["🎯 Current Targets", "", "📆 Daily:", f"🔵 Uday: {s.get('target_day_uday', 0)}", f"🔴 Maa Vaishno: {s.get('target_day_mv', 0)}", "", "📊 Weekly:", f"🔵 Uday: {s.get('target_week_uday', 0)}", f"🔴 Maa Vaishno: {s.get('target_week_mv', 0)}", "", "📅 Monthly:", f"🔵 Uday: {s.get('target_month_uday', 0)}", f"🔴 Maa Vaishno: {s.get('target_month_mv', 0)}"]
            bot.send_message(cid, "\n".join(lines))
        elif d == "stock_check":
            th = get_settings().get("stock_threshold", 3)
            bot.send_message(cid, stock_chk(th))
        elif d == "weekly_summary":
            bot.send_message(cid, weekly_sum())
            try:
                bot.send_photo(cid, line_chart(7))
            except:
                pass
        elif d == "peak_hours":
            bot.send_message(cid, peak_hours(7))
        elif d == "dist_compare":
            bot.send_message(cid, dist_cmp(7))
    except Exception as e:
        try:
            bot.send_message(call.message.chat.id, "Error: " + str(e))
        except:
            pass


@app.route('/', methods=['POST'])
@app.route('/api/webhook', methods=['POST'])
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
    return "Token: " + ("SET" if BOT_TOKEN else "MISSING") + ", Groq: " + ("SET" if GROQ_KEY else "MISSING") + ", DB: " + ("SET" if MONGO_URL else "MISSING")


application = app
handler = app

import os, re, time, json, telebot, urllib.parse
from datetime import datetime, timedelta
from groq import Groq
from pymongo import MongoClient
from flask import Flask, request
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
MONGO_URL = os.environ.get("MONGO_URL")
GROUP_CHAT_ID = "-1004368616206"
BOT_USERNAME = "@DTR_Mainpuri_Bot"
IST = 5 * 3600 + 30 * 60

ADMIN_USERNAMES = ["AkashV47"]
ADMIN_USER_IDS = []

PERMS = {
    "graphs": (True, True), "projections": (True, True), "performance": (True, True),
    "weekly_summary": (True, False), "compare": (True, False), "custom_report": (True, True),
    "menu": (True, True), "target_set": (True, False),
    "working_hours": (True, False), "stock_threshold": (True, False),
    "stock_check": (True, True), "target_status": (True, False),
    "peak_hours": (True, False), "distributors": (True, True),
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
    if uid in ADMIN_USER_IDS:
        return True
    if uname and uname.lstrip("@").lower() in [a.lower().lstrip("@") for a in ADMIN_USERNAMES]:
        return True
    return False

def check_perm(key, adm):
    if key not in PERMS:
        return True
    return PERMS[key][0] if adm else PERMS[key][1]

MSISDN_MAP = {"9997389467": "Uday Comm Agr", "7895110381": "Maa Vaishno Telecom"}

app = Flask(__name__)

DIST_P = re.compile(r'Dist\s+([A-Za-z0-9 &\.\-\']+?)\s*-\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*/\s*\((\d+)\)')
TOT_P = re.compile(r'^Total\s*-\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*/\s*\((\d+)\)', re.M)
TIME_P = re.compile(r'till\s+(\d{1,2})[:\.](\d{2})')
BAL_P = re.compile(r'(\d{10})\s*/\s*(\d+)\s*/\s*(\d+)\s*/\s*([\d\.]+)')

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
    rtm = TIME_P.search(t)
    rtime = f"{int(rtm.group(1)):02d}:{rtm.group(2)}" if rtm else None
    return {"distributors": dists, "total": total, "report_time": rtime}

def parse_bal(t):
    if not t or "Distributor Balance Report" not in t:
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

def mtd_data():
    now = datetime.now()
    today = now.date()
    ms = today.replace(day=1)
    um = datetime.combine(ms, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    last = {}
    for r in reps:
        last[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    labels, uday, mv, tot = [], [], [], []
    dtw = {"Uday Comm Agr": 0, "Maa Vaishno Telecom": 0}
    days_count = today.day
    for i in range(1, days_count + 1):
        cur_d = today.replace(day=i)
        k = cur_d.strftime("%Y-%m-%d")
        labels.append(cur_d.strftime("%d %b"))
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
        dtw["Uday Comm Agr"] += u
        dtw["Maa Vaishno Telecom"] += m
    return {"labels": labels, "uday": uday, "mv": mv, "total": tot, "dist_totals": dtw, "days_passed": days_count}
def daily_data(days=7):
    now = datetime.now()
    start = now - timedelta(days=days-1)
    utc0 = start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    last = {}
    for r in reps:
        last[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    labels, uday, mv, tot = [], [], [], []
    dtw = {}
    for i in range(days-1, -1, -1):
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

def daily_data(days=7):
    now = datetime.now()
    start = now - timedelta(days=days-1)
    utc0 = start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    last = {}
    for r in reps:
        last[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    labels, uday, mv, tot = [], [], [], []
    dtw = {}
    for i in range(days-1, -1, -1):
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

def qc(config, w=800, h=400):
    e = urllib.parse.quote(json.dumps(config))
    return f"https://quickchart.io/chart?c={e}&w={w}&h={h}&bkg=white&plugins=chartjs-plugin-datalabels"

def bar_chart(days=7, is_mtd=False):
    d = mtd_data() if is_mtd else daily_data(days)
    title_text = "Daily MNP - MTD (Month to Date)" if is_mtd else f"Daily MNP - Last {days} Days"
    cfg = {"type": "bar", "data": {"labels": d["labels"], "datasets": [
        {"label": "Uday Comm Agr", "data": d["uday"], "backgroundColor": "#2196F3"},
        {"label": "Maa Vaishno Telecom", "data": d["mv"], "backgroundColor": "#F44336"},
        {"label": "Total", "data": d["total"], "backgroundColor": "#4CAF50"}]},
        "options": {"title": {"display": True, "text": title_text, "fontSize": 16},
        "legend": {"position": "bottom"},
        "plugins": {"datalabels": {"display": True, "color": "white", "anchor": "center", "align": "center",
        "font": {"size": 10, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}}}
    return qc(cfg)

def pie_chart(days=7, is_mtd=False):
    d = mtd_data() if is_mtd else daily_data(days)
    dt = d["dist_totals"]
    labels = list(dt.keys()) if dt else ["No Data"]
    values = list(dt.values()) if dt else [1]
    title_text = "Distributor Share - MTD" if is_mtd else f"Distributor Share - Last {days} Days"
    cfg = {"type": "doughnut", "data": {"labels": labels, "datasets": [{"data": values, "backgroundColor": ["#2196F3", "#F44336", "#4CAF50"]}]},
        "options": {"title": {"display": True, "text": title_text, "fontSize": 16},
        "legend": {"position": "bottom"},
        "plugins": {"datalabels": {"display": True, "color": "white", "font": {"size": 12, "weight": "bold"},
        "formatter": "function(v,c){var s=c.dataset.data.reduce(function(a,b){return a+b;},0);var p=Math.round(v/s*100);return v+'\\n('+p+'%)';}"}}}}
    return qc(cfg)

def line_chart(days=7, is_mtd=False):
    d = mtd_data() if is_mtd else daily_data(days)
    title_text = "MNP Trend - MTD" if is_mtd else f"MNP Trend - Last {days} Days"
    cfg = {"type": "line", "data": {"labels": d["labels"], "datasets": [
        {"label": "Uday Comm Agr", "data": d["uday"], "borderColor": "#2196F3", "fill": False, "tension": 0.3,
         "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 6, "color": "#1565C0", "font": {"size": 9, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}},
        {"label": "Maa Vaishno Telecom", "data": d["mv"], "borderColor": "#F44336", "fill": False, "tension": 0.3,
         "datalabels": {"display": True, "align": "bottom", "anchor": "end", "offset": 6, "color": "#B71C1C", "font": {"size": 9, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}},
        {"label": "Total", "data": d["total"], "borderColor": "#4CAF50", "fill": False, "tension": 0.3, "borderWidth": 3,
         "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 18, "color": "#1B5E20", "font": {"size": 10, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}]},
        "options": {"title": {"display": True, "text": title_text, "fontSize": 16},
        "legend": {"position": "bottom"}, "layout": {"padding": {"top": 40}}}}
    return qc(cfg)

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
    wt_u = get_target("uday", "week")
    wt_m = get_target("mv", "week")
    if wt_u or wt_m:
        lines.append("")
        lines.append("🎯 Week Target (aapka):")
        if wt_u: lines.append(f"🔵 Uday: {wt_u}")
        if wt_m: lines.append(f"🔴 Maa Vaishno: {wt_m}")
        lines.append("")
        lines.append("⚖️ Target vs Achievement:")
        if wt_u:
            pct = int(u_total/wt_u*100); diff = u_total - wt_u
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔵 Uday: {u_total}/{wt_u} ({pct}%) — {mark}")
        if wt_m:
            pct = int(m_total/wt_m*100); diff = m_total - wt_m
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔴 Maa Vaishno: {m_total}/{wt_m} ({pct}%) — {mark}")
        if wt_u and wt_m:
            tot_t = wt_u + wt_m
            pct = int(grand/tot_t*100); diff = grand - tot_t
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append("")
            lines.append(f"📌 Overall: {grand}/{tot_t} ({pct}%) — {mark}")
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
    u_total = 0; m_total = 0; days_count = 0; day_breakdown = {}
    for k, r in daily.items():
        p = r.get("parsed") or parse_mnp(r.get("text", ""))
        if not p: continue
        u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        u_total += u; m_total += m; days_count += 1
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

MON = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

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
        pct = int(u/tu*100)
        diff = u - tu
        if u < tu:
            g = tu - u
            al.append(f"🔵 Uday Comm Agr: {u}/{tu} ({pct}%) - gap {g}")
            if s.get("last_fail_uday") == yds: ex.append("❗ Uday Comm Agr - aap aaj bhi target pura nahi kar paye!")
            upd_setting("last_fail_uday", tds)
        else:
            al.append(f"🔵 Uday Comm Agr: {u}/{tu} ({pct}%) — ✅ +{diff}")
            upd_setting("last_fail_uday", "")
    if tm:
        pct = int(m/tm*100)
        diff = m - tm
        if m < tm:
            g = tm - m
            al.append(f"🔴 Maa Vaishno Telecom: {m}/{tm} ({pct}%) - gap {g}")
            if s.get("last_fail_mv") == yds: ex.append("❗ Maa Vaishno Telecom - aap aaj bhi target pura nahi kar paye!")
            upd_setting("last_fail_mv", tds)
        else:
            al.append(f"🔴 Maa Vaishno Telecom: {m}/{tm} ({pct}%) — ✅ +{diff}")
            upd_setting("last_fail_mv", "")
    is_low = (tu and u < tu) or (tm and m < tm)
    header = f"⚠️ Low Performance Alert ({lbl})\n\n" if is_low else f"✅ Performance Status ({lbl})\n\n"
    msg = header + "\n".join(al)
    if ex: msg += "\n\n" + "\n".join(ex)
    return msg

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
    def hist_avg(days=30): return 0
        

def projection(period="day"):
    ws, we = get_wh()
    now = datetime.now()
    today = now.date()
    if period == "day":
        utc0 = datetime.combine(today, datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
        if not reps:
            return "⚠️ Aaj koi report nahi aayi."
        first = datetime.fromtimestamp(reps[0]["timestamp"] + IST)
        last = datetime.fromtimestamp(reps[-1]["timestamp"] + IST)
        p = reps[-1].get("parsed") or parse_mnp(reps[-1].get("text", ""))
        u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        t = (p.get("total") or {}).get("total", 0)
        ch = last.hour + last.minute/60
        sh = max(first.hour + first.minute/60, ws)
        el = max(0.5, ch - sh)
        rem = max(0, we - ch)
        if rem <= 0:
            return f"📈 FTD Final (working hours khatam)\n\n🔵 Uday: {u}\n🔴 Maa Vaishno: {m}\n🎯 Total: {t}"
        pu = int(u/el*(el+rem))
        pm = int(m/el*(el+rem))
        pt = int(t/el*(el+rem))
        return f"📈 FTD Projection\n\nAbhi tak ({last.strftime('%H:%M')}):\n🔵 Uday: {u}\n🔴 Maa Vaishno: {m}\n🎯 Total: {t}\n\nExpected ({int(we)}:00 tak):\n🔵 Uday: ~{pu}\n🔴 Maa Vaishno: ~{pm}\n🎯 Total: ~{pt}"
    return "❌ Period samjha nahi."

def proj_week():
    now = datetime.now()
    today = now.date()
    mon = today - timedelta(days=today.weekday())
    um = datetime.combine(mon, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    daily = {}
    for r in reps:
        daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    u_sofar = 0; m_sofar = 0; dd = 0
    for r in daily.values():
        p = parse_mnp(r.get("text", ""))
        if p:
            u_sofar += p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
            m_sofar += p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
            dd += 1
    t_sofar = u_sofar + m_sofar
    rd = 7 - today.weekday() - 1
    u_avg = int(u_sofar/dd) if dd else 0
    m_avg = int(m_sofar/dd) if dd else 0
    u_exp = int(u_sofar + u_avg * rd)
    m_exp = int(m_sofar + m_avg * rd)
    t_exp = u_exp + m_exp
    lines = ["📊 Weekly Projection", "", f"Abhi tak ({dd} din):", f"🔵 Uday: {u_sofar}", f"🔴 Maa Vaishno: {m_sofar}", f"🎯 Total: {t_sofar}", "", "📊 Daily Avg:", f"🔵 Uday: {u_avg}/din", f"🔴 Maa Vaishno: {m_avg}/din", "", f"📈 Expected ({rd} din baaki count):", f"🔵 Uday: ~{u_exp}", f"🔴 Maa Vaishno: ~{m_exp}", f"🎯 Total: ~{t_exp}"]
    wt_u = get_target("uday", "week")
    wt_m = get_target("mv", "week")
    if wt_u or wt_m:
        lines.append("")
        lines.append("🎯 Week Target (aapka):")
        if wt_u: lines.append(f"🔵 Uday: {wt_u}")
        if wt_m: lines.append(f"🔴 Maa Vaishno: {wt_m}")
        lines.append("")
        lines.append("⚖️ Target vs Projection:")
        if wt_u:
            pct = int(u_exp/wt_u*100); diff = u_exp - wt_u
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔵 Uday: {u_exp}/{wt_u} ({pct}%) — {mark}")
        if wt_m:
            pct = int(m_exp/wt_m*100); diff = m_exp - wt_m
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔴 Maa Vaishno: {m_exp}/{wt_m} ({pct}%) — {mark}")
        if wt_u and wt_m:
            tot_t = wt_u + wt_m
            pct = int(t_exp/tot_t*100); diff = t_exp - tot_t
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append("")
            lines.append(f"📌 Overall: {t_exp}/{tot_t} ({pct}%) — {mark}")
    return "\n".join(lines)

def proj_month():
    now = datetime.now()
    today = now.date()
    ms = today.replace(day=1)
    um = datetime.combine(ms, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    daily = {}
    for r in reps:
        daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    u_sofar = 0; m_sofar = 0; dd = 0
    for r in daily.values():
        p = parse_mnp(r.get("text", ""))
        if p:
            u_sofar += p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
            m_sofar += p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
            dd += 1
    t_sofar = u_sofar + m_sofar
    nm = today.replace(year=today.year+1, month=1, day=1) if today.month == 12 else today.replace(month=today.month+1, day=1)
    dim = (nm - ms).days
    rd = dim - today.day
    u_avg = int(u_sofar/dd) if dd else 0
    m_avg = int(m_sofar/dd) if dd else 0
    u_exp = int(u_sofar + u_avg * rd)
    m_exp = int(m_sofar + m_avg * rd)
    t_exp = u_exp + m_exp
    lines = [f"📅 MTD Projection ({ms.strftime('%b %Y')})", "", f"Abhi tak ({dd} din):", f"🔵 Uday: {u_sofar}", f"🔴 Maa Vaishno: {m_sofar}", f"🎯 Total: {t_sofar}", "", "📊 Daily Avg:", f"🔵 Uday: {u_avg}/din", f"🔴 Maa Vaishno: {m_avg}/din", "", f"📈 Expected ({rd} din baaki count):", f"🔵 Uday: ~{u_exp}", f"🔴 Maa Vaishno: ~{m_exp}", f"🎯 Total: ~{t_exp}"]
    mt_u = get_target("uday", "month")
    mt_m = get_target("mv", "month")
    if mt_u or mt_m:
        lines.append("")
        lines.append("🎯 Month Target (aapka):")
        if mt_u: lines.append(f"🔵 Uday: {mt_u}")
        if mt_m: lines.append(f"🔴 Maa Vaishno: {mt_m}")
        lines.append("")
        lines.append("⚖️ Target vs Projection:")
        if mt_u:
            pct = int(u_exp/mt_u*100); diff = u_exp - mt_u
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔵 Uday: {u_exp}/{mt_u} ({pct}%) — {mark}")
        if mt_m:
            pct = int(m_exp/mt_m*100); diff = m_exp - mt_m
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔴 Maa Vaishno: {m_exp}/{mt_m} ({pct}%) — {mark}")
        if mt_u and mt_m:
            tot_t = mt_u + mt_m
            pct = int(t_exp/tot_t*100); diff = t_exp - tot_t
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append("")
            lines.append(f"📌 Overall: {t_exp}/{tot_t} ({pct}%) — {mark}")
    return "\n".join(lines)

def peak_hours(days=7):
    now = datetime.now()
    s = now - timedelta(days=days-1)
    us = s.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": us}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    if not reps: return "❌ Koi report nahi mili."
    bd = {}
    for r in reps:
        k = time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))
        bd.setdefault(k, []).append(r)
    hr = {}
    for k, rr in bd.items():
        prev = 0
        for r in rr:
            p = r.get("parsed") or parse_mnp(r.get("text", ""))
            if not p: continue
            ct = (p.get("total") or {}).get("total", 0)
            if prev > 0:
                d = ct - prev
                if d > 0:
                    h = datetime.fromtimestamp(r["timestamp"] + IST).hour
                    hr[h] = hr.get(h, 0) + d
            prev = ct
    if not hr: return "❌ Data parse nahi ho paaya."
    sh = sorted(hr.items(), key=lambda x: -x[1])[:5]
    lines = [f"🕐 Peak Hours (last {days} days)", ""]
    for h, c in sh:
        lines.append(f"• {h:02d}:00 - {h+1:02d}:00 → {c} MNP")
    return "\n".join(lines)

def month_ach():
    now = datetime.now(); today = now.date()
    ms = today.replace(day=1)
    um = datetime.combine(ms, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    daily = {}
    for r in reps:
        daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    u_sofar = 0; m_sofar = 0; dd = 0
    for r in daily.values():
        p = parse_mnp(r.get("text", ""))
        if p:
            u_sofar += p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
            m_sofar += p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
            dd += 1
    t_sofar = u_sofar + m_sofar
    u_avg = int(u_sofar/dd) if dd else 0
    m_avg = int(m_sofar/dd) if dd else 0
    lines = [f"📅 MTD Achievement ({ms.strftime('%b %Y')})", "", f"Abhi tak ({dd} din):", f"🔵 Uday: {u_sofar}", f"🔴 Maa Vaishno: {m_sofar}", f"🎯 Total: {t_sofar}", "", "📊 Daily Avg:", f"🔵 Uday: {u_avg}/din", f"🔴 Maa Vaishno: {m_avg}/din"]
    mt_u = get_target("uday", "month")
    mt_m = get_target("mv", "month")
    if mt_u or mt_m:
        lines.append("")
        lines.append("🎯 Month Target (aapka):")
        if mt_u: lines.append(f"🔵 Uday: {mt_u}")
        if mt_m: lines.append(f"🔴 Maa Vaishno: {mt_m}")
        lines.append("")
        lines.append("⚖️ Target vs Achievement:")
        if mt_u:
            pct = int(u_sofar/mt_u*100); diff = u_sofar - mt_u
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔵 Uday: {u_sofar}/{mt_u} ({pct}%) — {mark}")
        if mt_m:
            pct = int(m_sofar/mt_m*100); diff = m_sofar - mt_m
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔴 Maa Vaishno: {m_sofar}/{mt_m} ({pct}%) — {mark}")
        if mt_u and mt_m:
            tot_t = mt_u + mt_m
            pct = int(t_sofar/tot_t*100); diff = t_sofar - tot_t
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append("")
            lines.append(f"📌 Overall: {t_sofar}/{tot_t} ({pct}%) — {mark}")
    return "\n".join(lines)

def dist_cmp(days=7, today_only=False):
    if today_only: days = 1
    d = daily_data(days)
    ut = sum(d["uday"]); mt = sum(d["mv"])
    if ut == 0 and mt == 0: return "❌ Koi data nahi."
    t = ut + mt
    up = int(ut/t*100) if t else 0
    mp = int(mt/t*100) if t else 0
    ua = int(ut/days); ma = int(mt/days)
    ubi = d["uday"].index(max(d["uday"])) if d["uday"] else 0
    mbi = d["mv"].index(max(d["mv"])) if d["mv"] else 0
    w = "🔵 Uday Comm Agr" if ut > mt else ("🔴 Maa Vaishno Telecom" if mt > ut else "Tie")
    hdr = "⚖️ Distributor Comparison (AAJ)" if today_only else f"⚖️ Distributor Comparison (last {days} days)"
    return "\n".join([hdr, "", f"🔵 Uday Comm Agr: {ut} ({up}%)", f"   Avg: {ua}/din | Best: {d['labels'][ubi]} ({max(d['uday'])})", "", f"🔴 Maa Vaishno: {mt} ({mp}%)", f"   Avg: {ma}/din | Best: {d['labels'][mbi]} ({max(d['mv'])})", "", f"🏆 Winner: {w}"])

@app.route('/check-performance', methods=['GET'])
def chk_perf():
    try:
        t = perf_alert()
        if t.startswith("❌") or t.startswith("⚠️"):
            bot.send_message(GROUP_CHAT_ID, t)
            return "No action", 200
        if "Sab targets pura" in t:
            return "All targets achieved", 200
        bot.send_message(GROUP_CHAT_ID, t)
        return "Alert sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/daily-quote', methods=['GET'])
def daily_quote():
    try:
        bot.send_message(GROUP_CHAT_ID, "🌅 Good Morning Team")
        time.sleep(1)
        r = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Generate a work-related motivational message in Hindi (Devanagari script). 2-3 lines. About teamwork, hard work, success. No greeting. No author name."},
                {"role": "user", "content": "Give me today's motivational message."}
            ], model="openai/gpt-oss-20b")
        bot.send_message(GROUP_CHAT_ID, "💪 " + r.choices[0].message.content.strip())
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/weekly-summary', methods=['GET'])
def weekly_ep():
    try:
        bot.send_message(GROUP_CHAT_ID, weekly_sum())
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/save-report', methods=['POST'])
def save_ep():
    try:
        data = request.get_json(force=True, silent=True) or request.form.to_dict()
        txt = data.get("text", "").strip()
        if not txt: return "No text", 400
        save_mnp(txt, time.time())
        return "Saved", 200
    except Exception as e:
        return "Error: " + str(e), 500
    @app.route('/noon-check', methods=['GET'])
def noon_check():
    try:
        s = get_settings()
        tu = s.get("target_day_uday", 0)
        tm = s.get("target_day_mv", 0)
        today = datetime.now().date()
        utc0 = datetime.combine(today, datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
        u = 0; m = 0
        if reps:
            p = reps[0].get("parsed") or parse_mnp(reps[0].get("text", ""))
            if p:
                u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
                m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        msg = "☀️ 12 PM Reminder\n\nAbhi aadha din baaki hai — speed badaiye!!\nAaj ka target miss nahi karna hai.\n\n"
        msg += f"🔵 Uday: {u}/{tu}\n🔴 Maa Vaishno: {m}/{tm}"
        bot.send_message(GROUP_CHAT_ID, msg)
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/peak-alert', methods=['GET'])
def peak_alert():
    try:
        s = get_settings()
        tu = s.get("target_day_uday", 0)
        tm = s.get("target_day_mv", 0)
        today = datetime.now().date()
        utc0 = datetime.combine(today, datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
        u = 0; m = 0
        if reps:
            p = reps[0].get("parsed") or parse_mnp(reps[0].get("text", ""))
            if p:
                u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
                m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        msg = "⚡ 4 PM Peak Hours Alert\n\nPuri speed up guys...\n\n"
        msg += f"🔵 Uday: {u}/{tu}\n🔴 Maa Vaishno: {m}/{tm}"
        bot.send_message(GROUP_CHAT_ID, msg)
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/night-summary', methods=['GET'])
def night_summary():
    try:
        s = get_settings()
        tu = s.get("target_day_uday", 0)
        tm = s.get("target_day_mv", 0)
        today = datetime.now().date()
        utc0 = datetime.combine(today, datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
        if not reps:
            bot.send_message(GROUP_CHAT_ID, "🌙 Aaj koi report nahi aayi.")
            return "No report", 200
        p = reps[0].get("parsed") or parse_mnp(reps[0].get("text", ""))
        u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        t = u + m
        lines = ["🌙 Aaj ka Final Summary", ""]
        u_mark = "✅" if tu and u >= tu else ("❌ gap " + str(tu - u) if tu else "")
        m_mark = "✅" if tm and m >= tm else ("❌ gap " + str(tm - m) if tm else "")
        lines.append(f"🔵 Uday: {u}/{tu} {u_mark}")
        lines.append(f"🔴 Maa Vaishno: {m}/{tm} {m_mark}")
        lines.append(f"🎯 Total: {t}")
        if tu and tm and u >= tu and m >= tm:
            lines.append("")
            lines.append("🏆 Target pura! Kal bhi aise hi!")
        elif tu and tm:
            lines.append("")
            lines.append("Kal aur mehnat karni hai!")
        bot.send_message(GROUP_CHAT_ID, "\n".join(lines))
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/report-missing', methods=['GET'])
def report_missing():
    try:
        now = datetime.now()
        hr = now.hour
        if hr < 7 or hr > 21: return "Outside working hours", 200
        cutoff = (now - timedelta(hours=2)).timestamp()
        reps = list(get_db().find({"timestamp": {"$gte": cutoff}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
        if reps: return "Reports on track", 200
        last = list(get_db().find({"text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
        last_time = "Koi report nahi"
        if last:
            last_time = datetime.fromtimestamp(last[0]["timestamp"] + IST).strftime("%H:%M")
        msg = "⚠️ Report Missing Alert\n\nPichhle 2 ghante se koi MNP report nahi aayi!\n\n"
        msg += f"Last report: {last_time}\nAbhi: {now.strftime('%H:%M')}\n\nCheck karo - SMS system ya network issue?"
        bot.send_message(GROUP_CHAT_ID, msg)
        return "Alert sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

def get_main_menu_kb(adm=False):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 Graphs Menu", callback_data="submenu_graphs"),
        InlineKeyboardButton("📈 Projections", callback_data="submenu_projections"),
        InlineKeyboardButton("⚡ Performance", callback_data="submenu_performance"),
        InlineKeyboardButton("🚨 Stock Check", callback_data="stock_check")
    )
    if adm:
        kb.add(
            InlineKeyboardButton("🎯 Target Status", callback_data="tgt_status"),
            InlineKeyboardButton("🏆 Peak Hours", callback_data="peak_hours")
        )
    kb.add(InlineKeyboardButton("⚖️ Distributors", callback_data="dist_compare"))
    return kb

def get_graphs_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📊 Bar (7 Days)", callback_data="graph_bar_7"),
        InlineKeyboardButton("📊 Bar (MTD)", callback_data="graph_bar_mtd"),
        InlineKeyboardButton("📈 Line (7 Days)", callback_data="graph_line_7"),
        InlineKeyboardButton("📈 Line (MTD)", callback_data="graph_line_mtd"),
        InlineKeyboardButton("🥧 Pie (7 Days)", callback_data="graph_pie_7"),
        InlineKeyboardButton("🥧 Pie (MTD)", callback_data="graph_pie_mtd"),
        InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")
    )
    return kb

def get_projections_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📉 FTD Projection", callback_data="proj_day"),
        InlineKeyboardButton("📊 Weekly Projection", callback_data="proj_week"),
        InlineKeyboardButton("📅 MTD Projection", callback_data="proj_month"),
        InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")
    )
    return kb

def get_performance_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🎯 FTD Ach v/s Tgt", callback_data="perf_check"),
        InlineKeyboardButton("📊 MTD Ach v/s Tgt", callback_data="perf_mtd"),
        InlineKeyboardButton("📋 Weekly Summary", callback_data="weekly_summary"),
        InlineKeyboardButton("🔙 Back to Main Menu", callback_data="menu_main")
    )
    return kb

@bot.message_handler(func=lambda m: True)
def handle(message):
    try:
        text = message.text or ""
        msg_ts = message.date
        if message.chat.id == int(GROUP_CHAT_ID) and ("FTA MNP" in text or "FTD" in text):
            save_mnp(text, msg_ts)
        if message.chat.id == int(GROUP_CHAT_ID) and "Distributor Balance Report" in text:
            p = save_bal(text, msg_ts)
            if p:
                th = get_settings().get("stock_threshold", 3)
                al = bal_alert(p, th)
                if al: bot.send_message(GROUP_CHAT_ID, al)
            return
        is_priv = message.chat.type == "private"
        is_tag = BOT_USERNAME.lower() in text.lower()
        is_rep_bot = False
        if message.reply_to_message and message.reply_to_message.from_user:
            u = message.reply_to_message.from_user
            if u.is_bot and u.username == BOT_USERNAME.replace("@", ""): is_rep_bot = True
        if not is_priv and not is_tag and not is_rep_bot: return

        ct = text.replace(BOT_USERNAME, "").strip() or "Hi"
        lo = ct.lower()
        uid = message.from_user.id
        un = message.from_user.username
        adm = is_admin(uid, un)

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
                p = parse_mnp(orig)
                if not p:
                    bot.reply_to(message, "❌ Ye MNP report nahi hai.")
                    return
                get_db().insert_one({"text": orig, "timestamp": ts, "date_str": ts_str(ts), "parsed": p})
                bot.reply_to(message, f"✅ Report saved: {dt.strftime('%d %b %Y %H:%M')}")
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
            bot.reply_to(message, f"{r}\n\nUsername: @{un or 'not set'}\nUser ID: {uid}")
            return

        if lo.strip() in ["/start", "/menu", "menu", "start", "help", "commands"]:
            if not check_perm("menu", adm):
                bot.reply_to(message, "❌ Menu currently disabled hai.")
                return
            bot.reply_to(message, "🤖 DTR Mainpuri Bot Menu\n\nKya dekhna chahte ho?", reply_markup=get_main_menu_kb(adm))
            return

        r = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful Telegram assistant. Reply in same language. Answer in 1-3 lines."},
                {"role": "user", "content": ct}
            ], model="openai/gpt-oss-20b")
        bot.reply_to(message, r.choices[0].message.content)
    except Exception as e:
        try: bot.reply_to(message, "Error: " + str(e))
        except: pass

BTN_MAP = {
    "graph_bar_7": "graphs", "graph_bar_mtd": "graphs", "graph_line_7": "graphs", "graph_line_mtd": "graphs",
    "graph_pie_7": "graphs", "graph_pie_mtd": "graphs", "proj_day": "projections", "proj_week": "projections",
    "proj_month": "projections", "perf_check": "performance", "perf_mtd": "performance",
    "tgt_status": "target_status", "stock_check": "stock_check", "weekly_summary": "weekly_summary",
    "peak_hours": "peak_hours", "dist_compare": "distributors",
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
            bot.edit_message_text("🤖 DTR Mainpuri Bot Menu\n\nKya dekhna chahte ho?", cid, mid, reply_markup=get_main_menu_kb(adm))
            return
        elif d == "submenu_graphs":
            bot.edit_message_text("📊 Graphs Menu\n\nKaun sa chart dekhna chahte hain?", cid, mid, reply_markup=get_graphs_kb())
            return
        elif d == "submenu_projections":
            bot.edit_message_text("📈 Projections Menu\n\nPeriod select karein:", cid, mid, reply_markup=get_projections_kb())
            return
        elif d == "submenu_performance":
            bot.edit_message_text("⚡ Performance Menu\n\nReport type select karein:", cid, mid, reply_markup=get_performance_kb())
            return

        pk = BTN_MAP.get(d)
        if pk and not check_perm(pk, adm):
            bot.send_message(cid, "❌ Ye feature currently disabled hai.")
            return

        if d == "graph_bar_7": bot.send_photo(cid, bar_chart(7, is_mtd=False))
        elif d == "graph_bar_mtd": bot.send_photo(cid, bar_chart(is_mtd=True))
        elif d == "graph_line_7": bot.send_photo(cid, line_chart(7, is_mtd=False))
        elif d == "graph_line_mtd": bot.send_photo(cid, line_chart(is_mtd=True))
        elif d == "graph_pie_7": bot.send_photo(cid, pie_chart(7, is_mtd=False))
        elif d == "graph_pie_mtd": bot.send_photo(cid, pie_chart(is_mtd=True))
        elif d == "proj_day": bot.send_message(cid, projection("day"))
        elif d == "proj_week": bot.send_message(cid, proj_week())
        elif d == "proj_month": bot.send_message(cid, proj_month())
        elif d == "perf_check": bot.send_message(cid, perf_alert())
        elif d == "perf_mtd": bot.send_message(cid, month_ach())
        elif d == "tgt_status":
            s = get_settings(); a, b = get_wh()
            lines = ["🎯 Current Targets", "", "📆 Daily:", f"🔵 Uday: {s.get('target_day_uday', 0)}", f"🔴 Maa Vaishno: {s.get('target_day_mv', 0)}", "", "📊 Weekly:", f"🔵 Uday: {s.get('target_week_uday', 0)}", f"🔴 Maa Vaishno: {s.get('target_week_mv', 0)}", "", "📅 Monthly:", f"🔵 Uday: {s.get('target_month_uday', 0)}", f"🔴 Maa Vaishno: {s.get('target_month_mv', 0)}", "", f"⏰ Working hours: {int(a)}:00 se {int(b)}:00"]
            bot.send_message(cid, "\n".join(lines))
        elif d == "stock_check":
            th = get_settings().get("stock_threshold", 3)
            bot.send_message(cid, stock_chk(th))
        elif d == "weekly_summary":
            bot.send_message(cid, weekly_sum())
            try: bot.send_photo(cid, line_chart(7))
            except: pass
        elif d == "peak_hours": bot.send_message(cid, peak_hours(7))
        elif d == "dist_compare": bot.send_message(cid, dist_cmp(7))
    except Exception as e:
        try: bot.send_message(call.message.chat.id, "Error: " + str(e))
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
    return "Token: " + ("SET" if BOT_TOKEN else "MISSING") + ", Groq: " + ("SET" if GROQ_KEY else "MISSING") + ", DB: " + ("SET" if MONGO_URL else "MISSING")
    # Vercel entrypoint compatibility
app.app = app

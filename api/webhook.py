import os, re, time, json, telebot, urllib.parse, calendar
from datetime import datetime, timedelta
from groq import Groq
from pymongo import MongoClient
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
MONGO_URL = os.environ.get("MONGO_URL")
GROUP_CHAT_ID = "-1004368616206"
SR_GROUPS = ["-5276657355", "-5578221329"]
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

def send_to_sr_groups(msg):
    for gid in SR_GROUPS:
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
        growth = int((cur_total - last_total) / last_total * 100)
        arrow = "📈" if growth >= 0 else "📉"
        lines.append("")
        lines.append(f"{arrow} Growth: {'+' if growth >= 0 else ''}{growth}%")
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
            return f"📈 FTD Projection (Aaj ka final - working hours khatam)\n\n🔵 Uday: {u}\n🔴 Maa Vaishno: {m}\n🎯 Total: {t}"
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
    lines = ["📊 Weekly Projection", "", f"Abhi tak ({dd} din):", f"🔵 Uday: {u_sofar}", f"🔴 Maa Vaishno: {m_sofar}", f"🎯 Total: {t_sofar}", "", "📊 Daily Avg:", f"🔵 Uday: {u_avg}/din", f"🔴 Maa Vaishno: {m_avg}/din", "", f"📈 Expected ({rd} din baaki):", f"🔵 Uday: ~{u_exp}", f"🔴 Maa Vaishno: ~{m_exp}", f"🎯 Total: ~{t_exp}"]
    wt_u = get_target("uday", "week")
    wt_m = get_target("mv", "week")
    if wt_u or wt_m:
        lines.append("")
        lines.append("🎯 Week Target:")
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
    lines = [f"📅 MTD Projection ({ms.strftime('%b %Y')})", "", f"Abhi tak ({dd} din):", f"🔵 Uday: {u_sofar}", f"🔴 Maa Vaishno: {m_sofar}", f"🎯 Total: {t_sofar}", "", "📊 Daily Avg:", f"🔵 Uday: {u_avg}/din", f"🔴 Maa Vaishno: {m_avg}/din", "", f"📈 Expected ({rd} din baaki):", f"🔵 Uday: ~{u_exp}", f"🔴 Maa Vaishno: ~{m_exp}", f"🎯 Total: ~{t_exp}"]
    mt_u = get_target("uday", "month")
    mt_m = get_target("mv", "month")
    if mt_u or mt_m:
        lines.append("")
        lines.append("🎯 Month Target:")
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
    return "\n".join(lines)

def proj_till(dy, mon_num):
    now = datetime.now()
    today = now.date()
    try:
        target_date = datetime(today.year, mon_num, dy).date()
    except:
        return "❌ Galat date."
    if target_date < today:
        return f"❌ {dy} {target_date.strftime('%b')} ki date nikal gayi."
    days_until = (target_date - today).days
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
    if dd == 0:
        return "❌ Koi data nahi hai."
    t_sofar = u_sofar + m_sofar
    u_avg = int(u_sofar / dd)
    m_avg = int(m_sofar / dd)
    u_exp = int(u_sofar + u_avg * days_until)
    m_exp = int(m_sofar + m_avg * days_until)
    t_exp = u_exp + m_exp
    target_str = target_date.strftime("%d %b")
    lines = [f"📈 Projection till {target_str}", "", f"Aaj tak ({dd} din):", f"🔵 Uday: {u_sofar}", f"🔴 Maa Vaishno: {m_sofar}", f"🎯 Total: {t_sofar}", "", f"📈 Expected till {target_str} ({days_until} din baaki):", f"🔵 Uday: ~{u_exp}", f"🔴 Maa Vaishno: ~{m_exp}", f"🎯 Total: ~{t_exp}"]
    return "\n".join(lines)

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
        day_hours = set()
        for r in rr:
            p = r.get("parsed") or parse_mnp(r.get("text", ""))
            if not p: continue
            ct = (p.get("total") or {}).get("total", 0)
            if prev > 0:
                d = ct - prev
                if d > 0:
                    h = datetime.fromtimestamp(r["timestamp"] + IST).hour
                    if ws_h <= h < we_h:
                        hr_total[h] = hr_total.get(h, 0) + d
                        day_hours.add(h)
            prev = ct
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
    for r in reps:
        p = r.get("parsed") or parse_mnp(r.get("text", ""))
        if not p: continue
        ct = (p.get("total") or {}).get("total", 0)
        if prev > 0:
            d = ct - prev
            if d > 0:
                h = datetime.fromtimestamp(r["timestamp"] + IST).hour
                if ws_h <= h < we_h:
                    hr[h] = hr.get(h, 0) + d
        prev = ct
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

def growth(period="week"):
    now = datetime.now(); today = now.date()
    def sp(a, b):
        reps = list(get_db().find({"timestamp": {"$gte": a, "$lte": b}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
        daily = {}
        for r in reps:
            daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
        tt = 0
        for r in daily.values():
            p = parse_mnp(r.get("text", ""))
            if p: tt += (p.get("total") or {}).get("total", 0)
        return tt
    if period == "week":
        dsm = today.weekday()
        tm = today - timedelta(days=dsm)
        lm = tm - timedelta(days=7)
        ts = datetime.combine(tm, datetime.min.time()).timestamp() - IST
        ls = datetime.combine(lm, datetime.min.time()).timestamp() - IST
        dd = dsm + 1
        lae = lm + timedelta(days=dd-1)
        lae_ts = datetime.combine(lae, datetime.max.time()).timestamp() - IST
        tt = sp(ts, now.timestamp())
        lt = sp(ls, lae_ts)
        g = int((tt-lt)/lt*100) if lt else 0
        arrow = "📈" if g >= 0 else "📉"
        return f"{arrow} Growth Rate (Week)\n\nThis week ({dd} din): {tt}\nLast week (same days): {lt}\nGrowth: {'+' if g>=0 else ''}{g}%"
    elif period == "month":
        tms = today.replace(day=1)
        lms = tms.replace(year=tms.year-1, month=12) if tms.month == 1 else tms.replace(month=tms.month-1)
        ts = datetime.combine(tms, datetime.min.time()).timestamp() - IST
        ls = datetime.combine(lms, datetime.min.time()).timestamp() - IST
        lae = lms + timedelta(days=today.day-1)
        lae_ts = datetime.combine(lae, datetime.max.time()).timestamp() - IST
        tt = sp(ts, now.timestamp())
        lt = sp(ls, lae_ts)
        g = int((tt-lt)/lt*100) if lt else 0
        arrow = "📈" if g >= 0 else "📉"
        return f"{arrow} Growth Rate (Month)\n\nThis month ({today.day} din): {tt}\nLast month (same days): {lt}\nGrowth: {'+' if g>=0 else ''}{g}%"
    return "❌ Period samjha nahi."

def day_ach():
    now = datetime.now()
    today = now.date()
    utc0 = datetime.combine(today, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
    if not reps:
        return "❌ Aaj koi report nahi aayi."
    p = reps[0].get("parsed") or parse_mnp(reps[0].get("text", ""))
    if not p:
        return "❌ Report parse nahi ho paayi."
    u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
    m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
    t = u + m
    lines = ["📊 FTD Achievement", ""]
    lines.append(f"Latest: {reps[0].get('date_str', '')}")
    lines.append("")
    lines.append(f"🔵 Uday: {u}")
    lines.append(f"🔴 Maa Vaishno: {m}")
    lines.append(f"🎯 Total: {t}")
    tu = get_target("uday", "day")
    tm = get_target("mv", "day")
    if tu or tm:
        lines.append("")
        lines.append("🎯 Daily Target:")
        if tu: lines.append(f"🔵 Uday: {tu}")
        if tm: lines.append(f"🔴 Maa Vaishno: {tm}")
        lines.append("")
        lines.append("⚖️ Target vs Achievement:")
        if tu:
            pct = int(u / tu * 100) if tu else 0
            diff = u - tu
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔵 Uday: {u}/{tu} ({pct}%) — {mark}")
        if tm:
            pct = int(m / tm * 100) if tm else 0
            diff = m - tm
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔴 Maa Vaishno: {m}/{tm} ({pct}%) — {mark}")
    return "\n".join(lines)

def week_ach():
    now = datetime.now(); today = now.date()
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
    u_avg = int(u_sofar/dd) if dd else 0
    m_avg = int(m_sofar/dd) if dd else 0
    lines = ["📊 Weekly Achievement", "", f"Abhi tak ({dd} din):", f"🔵 Uday: {u_sofar}", f"🔴 Maa Vaishno: {m_sofar}", f"🎯 Total: {t_sofar}", "", "📊 Daily Avg:", f"🔵 Uday: {u_avg}/din", f"🔴 Maa Vaishno: {m_avg}/din"]
    wt_u = get_target("uday", "week")
    wt_m = get_target("mv", "week")
    if wt_u or wt_m:
        lines.append("")
        lines.append("🎯 Week Target:")
        if wt_u: lines.append(f"🔵 Uday: {wt_u}")
        if wt_m: lines.append(f"🔴 Maa Vaishno: {wt_m}")
        lines.append("")
        lines.append("⚖️ Target vs Achievement:")
        if wt_u:
            pct = int(u_sofar/wt_u*100); diff = u_sofar - wt_u
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔵 Uday: {u_sofar}/{wt_u} ({pct}%) — {mark}")
        if wt_m:
            pct = int(m_sofar/wt_m*100); diff = m_sofar - wt_m
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔴 Maa Vaishno: {m_sofar}/{wt_m} ({pct}%) — {mark}")
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
        lines.append("🎯 Month Target:")
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
    return "\n".join(lines)

def cust_rep(dist, days=7):
    d = daily_data(days)
    dl = dist.lower()
    if "uday" in dl:
        vals = d["uday"]; n = "Uday Comm Agr"; em = "🔵"
    elif "vaishno" in dl or "mv" in dl:
        vals = d["mv"]; n = "Maa Vaishno Telecom"; em = "🔴"
    else:
        return f"❌ Distributor '{dist}' nahi pehchana."
    tot = sum(vals)
    dd = len([v for v in vals if v > 0])
    av = int(tot/dd) if dd else 0
    bi = vals.index(max(vals)) if vals else 0
    lines = [f"{em} {n} - Last {days} Days", "", f"Total: {tot}", f"Daily avg: {av}/din", f"Best day: {d['labels'][bi]} ({max(vals)})", "", "Daily breakdown:"]
    for i, l in enumerate(d["labels"]):
        lines.append(f"• {l}: {vals[i]}")
    return "\n".join(lines)

def dist_cmp(days=7, today_only=False):
    if today_only: days = 1
    d = daily_data(days)
    ut = sum(d["uday"]); mt = sum(d["mv"])
    if ut == 0 and mt == 0:
        return "❌ Koi data nahi."
    t = ut + mt
    up = int(ut/t*100) if t else 0
    mp = int(mt/t*100) if t else 0
    ua = int(ut/days); ma = int(mt/days)
    ubi = d["uday"].index(max(d["uday"])) if d["uday"] else 0
    mbi = d["mv"].index(max(d["mv"])) if d["mv"] else 0
    w = "🔵 Uday Comm Agr" if ut > mt else ("🔴 Maa Vaishno Telecom" if mt > ut else "Tie")
    hdr = "⚖️ Distributor Comparison (AAJ)" if today_only else f"⚖️ Distributor Comparison (last {days} days)"
    return "\n".join([hdr, "", f"🔵 Uday Comm Agr: {ut} ({up}%)", f"   Avg: {ua}/din | Best: {d['labels'][ubi]} ({max(d['uday'])})", "", f"🔴 Maa Vaishno: {mt} ({mp}%)", f"   Avg: {ma}/din | Best: {d['labels'][mbi]} ({max(d['mv'])})", "", f"🏆 Winner: {w}"])


# ==================== SR ENDPOINTS ====================
@app.route('/sr-10am', methods=['GET'])
def sr_10am():
    try:
        send_to_sr_groups("📋 Aaj ki open SR report download karli??\n\nKitni SR hai??\n\nNahi kari to abhi karo!!")
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/sr-2pm', methods=['GET'])
def sr_2pm():
    try:
        send_to_sr_groups("⏰ Aaj ki panding SR Close ho gi??\n\nAgar nahi to 5 bje se pahle close karo or final data bhejo!!")
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/sr-5pm', methods=['GET'])
def sr_5pm():
    try:
        send_to_sr_groups("🚨 Sari SR complete ho gi??\n\nKoi ZI to panding nhi hai?\n\nAgar nahi to remark bhejo!!")
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

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
        if not txt:
            return "No text", 400
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
        bot.send_message(GROUP_CHAT_ID, "\n".join(lines))
        return "Sent", 200
    except Exception as e:
        return "Error: " + str(e), 500

@app.route('/report-missing', methods=['GET'])
def report_missing():
    try:
        now = datetime.now()
        hr = now.hour
        if hr < 7 or hr > 21:
            return "Outside working hours", 200
        cutoff = (now - timedelta(hours=2)).timestamp()
        reps = list(get_db().find({"timestamp": {"$gte": cutoff}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
        if reps:
            return "Reports on track", 200
        last = list(get_db().find({"text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
        last_time = "Koi report nahi"
        if last:
            last_time = datetime.fromtimestamp(last[0]["timestamp"] + IST).strftime("%H:%M")
        msg = "⚠️ Report Missing Alert\n\nPichhle 2 ghante se koi MNP report nahi aayi!\n\n"
        msg += f"Last report: {last_time}\nAbhi: {now.strftime('%H:%M')}\n\n"
        msg += "Check karo - SMS system ya network issue?"
        bot.send_message(GROUP_CHAT_ID, msg)
        return "Alert sent", 200
    except Exception as e:
        return "Error: " + str(e), 500


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
                if al:
                    bot.send_message(GROUP_CHAT_ID, al)
            return
        is_priv = message.chat.type == "private"
        is_tag = BOT_USERNAME.lower() in text.lower()
        is_rep_bot = False
        if message.reply_to_message and message.reply_to_message.from_user:
            u = message.reply_to_message.from_user
            if u.is_bot and u.username == BOT_USERNAME.replace("@", ""):
                is_rep_bot = True
        if not is_priv and not is_tag and not is_rep_bot:
            return
        ct = text.replace(BOT_USERNAME, "").strip() or "Hi"
        lo = ct.lower()
        uid = message.from_user.id
        un = message.from_user.username
        adm = is_admin(uid, un)

        # SAVE AS COMMAND
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
            bot.reply_to(message, f"{r}\n\nUsername: @{un or 'not set'}\nUser ID: {uid}")
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
                InlineKeyboardButton("📅 Month vs Month", callback_data="mom_compare"),
                InlineKeyboardButton("🏆 Best/Worst", callback_data="best_worst"),
                InlineKeyboardButton("📊 Distributor Charts", callback_data="menu_dist"),
            )
            if adm:
                kb.add(
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

        # ===== CUSTOM DATE RANGE =====
        if "report" in lo and (" se " in lo or " to " in lo):
            sd, ed = parse_range(lo)
            if sd and ed:
                bot.reply_to(message, custom_range(sd, ed))
                return

        # ===== MONTH vs MONTH =====
        if ("month compare" in lo or "mtd vs last" in lo or "mom" in lo or
            ("month" in lo and "compare" in lo)):
            bot.reply_to(message, mom_compare())
            return

        # ===== BEST / WORST =====
        if "best day" in lo or "worst day" in lo or "best/worst" in lo:
            dy = 30
            d = re.search(r'(\d{1,2})\s*(?:din|days)', lo)
            if d:
                dy = int(d.group(1))
            bot.reply_to(message, best_worst(dy))
            return

        # ===== WORKING HOURS =====
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

        # ===== TARGETS =====
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

        # ===== STOCK CHECK =====
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

        # ===== PEAK HOURS =====
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

        # ===== GROWTH =====
        if any(w in lo for w in ["growth", "growth rate", "kitne percent badha", "kitna badha", "vikas"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            if any(w in lo for w in ["month", "mahine", "mahina"]):
                bot.reply_to(message, growth("month"))
            else:
                bot.reply_to(message, growth("week"))
            return

        # ===== ACHIEVEMENTS =====
        if "month" in lo and any(w in lo for w in ["ach", "achievement"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            bot.reply_to(message, month_ach())
            return

        if "mahine" in lo and any(w in lo for w in ["ach", "achievement", "performance"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            bot.reply_to(message, month_ach())
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

        # ===== CUSTOM REPORTS =====
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

        # ===== PROJECTIONS =====
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

        # ===== FTD =====
        if any(w in lo for w in ["ftd", "aaj ka"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "❌ Currently disabled.")
                return
            bot.reply_to(message, day_ach())
            return

        # ===== ACHIEVEMENT ALERT =====
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

        # ===== GRAPHS =====
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

        # ===== SUMMARIES =====
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

        # ===== COMPARE =====
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



BTN_MAP = {
    "graph_bar": "graphs", "graph_pie": "graphs", "graph_line": "graphs",
    "graph_bar_mtd": "graphs", "graph_pie_mtd": "graphs", "graph_line_mtd": "graphs",
    "proj_ftd": "projections", "proj_week": "projections", "proj_month": "projections",
    "perf_ftd": "performance", "perf_week": "performance", "perf_month": "performance",
    "perf_check": "performance", "tgt_status": "target_status",
    "stock_check": "stock_check", "weekly_summary": "weekly_summary",
    "peak_hours": "peak_hours", "dist_compare": "distributors",
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
        InlineKeyboardButton("📅 Month vs Month", callback_data="mom_compare"),
        InlineKeyboardButton("🏆 Best/Worst", callback_data="best_worst"),
        InlineKeyboardButton("📊 Distributor Charts", callback_data="menu_dist"),
    )
    if adm:
        kb.add(
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

        # ===== SUBMENU: GRAPHS (same message edit) =====
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

        # ===== SUBMENU: PROJECTION (same message edit) =====
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

        # ===== SUBMENU: PERFORMANCE (same message edit) =====
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

        # ===== SUBMENU: DISTRIBUTOR CHARTS (same message edit) =====
        if d == "menu_dist":
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

        # ===== BACK TO MAIN MENU (same message edit) =====
        if d == "menu_main":
            kb = build_main_menu_kb(adm)
            try:
                bot.edit_message_text("🤖 Main Menu\n\nKya dekhna chahte ho?", cid, mid, reply_markup=kb)
            except:
                bot.send_message(cid, "🤖 Main Menu\n\nKya dekhna chahte ho?", reply_markup=kb)
            return

        # ===== Permission check =====
        pk = BTN_MAP.get(d)
        if pk and not check_perm(pk, adm):
            bot.send_message(cid, "❌ Ye feature currently disabled hai.")
            return

        # ===== DISTRIBUTOR CHARTS =====
        if d and d.startswith("dist_") and d != "dist_compare":
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

        # ===== GRAPHS =====
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

        # ===== PROJECTION =====
        elif d == "proj_ftd":
            bot.send_message(cid, projection("day"))
        elif d == "proj_week":
            bot.send_message(cid, proj_week())
        elif d == "proj_month":
            bot.send_message(cid, proj_month())

        # ===== PERFORMANCE =====
        elif d == "perf_ftd":
            bot.send_message(cid, day_ach())
        elif d == "perf_week":
            bot.send_message(cid, week_ach())
        elif d == "perf_month":
            bot.send_message(cid, month_ach())
        elif d == "perf_check":
            bot.send_message(cid, perf_alert())

        # ===== MOM, BEST/WORST =====
        elif d == "mom_compare":
            bot.send_message(cid, mom_compare())
        elif d == "best_worst":
            bot.send_message(cid, best_worst(30))

        # ===== OTHER =====
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

import os, re, time, json, telebot, urllib.parse
from datetime import datetime, timedelta
from groq import Groq
from pymongo import MongoClient
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
MONGO_URL = os.environ.get("MONGO_URL")
GROUP_CHAT_ID = "-1004368616206"
BOT_USERNAME = "@DTR_Mainpuri_Bot"
IST = 5 * 3600 + 30 * 60

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

def set_target(k, v):
    get_db().update_one({"_id": "settings"}, {"$set": {f"target_{k}": v}}, upsert=True)

def get_tag(k):
    return get_settings().get(f"tag_{k}", "")

def set_tag(k, u):
    if u and not u.startswith("@"):
        u = "@" + u
    get_db().update_one({"_id": "settings"}, {"$set": {f"tag_{k}": u}}, upsert=True)

def is_admin(uid, uname):
    s = get_settings()
    if s.get("owner_id") and uid == s["owner_id"]:
        return True
    admins = s.get("admins", [])
    if uname:
        u = "@" + uname.lstrip("@").lower()
        return u in [a.lower() for a in admins]
    return False

def add_admin(uname):
    if not uname.startswith("@"):
        uname = "@" + uname
    s = get_settings()
    admins = s.get("admins", [])
    if uname.lower() not in [a.lower() for a in admins]:
        admins.append(uname)
    get_db().update_one({"_id": "settings"}, {"$set": {"admins": admins}}, upsert=True)

def rm_admin(uname):
    if not uname.startswith("@"):
        uname = "@" + uname
    s = get_settings()
    admins = [a for a in s.get("admins", []) if a.lower() != uname.lower()]
    get_db().update_one({"_id": "settings"}, {"$set": {"admins": admins}}, upsert=True)

MSISDN_MAP = {"9997389467": "Uday Comm Agr", "7895110381": "Maa Vaishno Telecom"}

DEFAULT_PERMS = {
    "graphs": (True, True), "projections": (True, True), "performance": (True, True),
    "weekly_summary": (True, False), "compare": (True, False), "custom_report": (True, True),
    "menu": (True, True), "target_set": (True, False), "tag_set": (True, False),
    "working_hours": (True, False), "stock_threshold": (True, False),
    "stock_check": (True, True), "target_status": (True, False),
    "peak_hours": (True, False), "distributors": (True, True), "admin_mgmt": (True, False)
}

PERM_AL = {
    "graph": "graphs", "chart": "graphs", "proj": "projections", "projection": "projections",
    "perf": "performance", "weekly": "weekly_summary", "ws": "weekly_summary",
    "cmp": "compare", "custom": "custom_report", "cr": "custom_report",
    "target": "target_set", "tgt": "target_set", "tag": "tag_set",
    "wh": "working_hours", "hours": "working_hours", "st": "stock_threshold",
    "sc": "stock_check", "ts": "target_status", "peak": "peak_hours", "ph": "peak_hours",
    "dist": "distributors", "admin": "admin_mgmt"
}

def get_perms():
    p = get_settings().get("permissions", {})
    return {k: (p.get(k, {}).get("admin", v[0]), p.get(k, {}).get("user", v[1])) for k, v in DEFAULT_PERMS.items()}

def check_perm(key, admin):
    perms = get_perms()
    if key not in perms:
        return True
    return perms[key][0] if admin else perms[key][1]

def set_perm(key, role, val):
    s = get_settings()
    p = s.get("permissions", {})
    p.setdefault(key, {})[role] = val
    get_db().update_one({"_id": "settings"}, {"$set": {"permissions": p}}, upsert=True)

def reset_perms():
    get_db().update_one({"_id": "settings"}, {"$unset": {"permissions": ""}}, upsert=True)

def perm_dash():
    perms = get_perms()
    lines = ["Permission Dashboard", "", f"{'Command':<20} {'Admin':<8} {'User':<8}", "-" * 38]
    for k in DEFAULT_PERMS:
        a = "OK" if perms[k][0] else "X"
        u = "OK" if perms[k][1] else "X"
        lines.append(f"{k:<20} {a:<8} {u:<8}")
    lines.append("")
    lines.append("Badalne: perm <name> <admin|user> <on|off>")
    return "\n".join(lines)

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
    lines = ["MNP Comparison", f"{l1}: {r1.get('date_str','')} (till {p1.get('report_time','?')})", f"{l2}: {r2.get('date_str','')} (till {p2.get('report_time','?')})", ""]
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

def bar_chart(days=7):
    d = daily_data(days)
    cfg = {"type": "bar", "data": {"labels": d["labels"], "datasets": [
        {"label": "Uday Comm Agr", "data": d["uday"], "backgroundColor": "#2196F3"},
        {"label": "Maa Vaishno Telecom", "data": d["mv"], "backgroundColor": "#F44336"},
        {"label": "Total", "data": d["total"], "backgroundColor": "#4CAF50"}]},
        "options": {"title": {"display": True, "text": f"Daily MNP - Last {days} Days", "fontSize": 16},
        "legend": {"position": "bottom"},
        "plugins": {"datalabels": {"display": True, "color": "white", "anchor": "center", "align": "center",
        "font": {"size": 11, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}}}
    return qc(cfg)

def pie_chart(days=7):
    d = daily_data(days)
    dt = d["dist_totals"]
    labels = list(dt.keys()) if dt else ["No Data"]
    values = list(dt.values()) if dt else [1]
    cfg = {"type": "doughnut", "data": {"labels": labels, "datasets": [{"data": values, "backgroundColor": ["#2196F3", "#F44336", "#4CAF50"]}]},
        "options": {"title": {"display": True, "text": f"Distributor Share - Last {days} Days", "fontSize": 16},
        "legend": {"position": "bottom"},
        "plugins": {"datalabels": {"display": True, "color": "white", "font": {"size": 12, "weight": "bold"},
        "formatter": "function(v,c){var s=c.dataset.data.reduce(function(a,b){return a+b;},0);var p=Math.round(v/s*100);return v+'\\n('+p+'%)';}"}}}}
    return qc(cfg)

def line_chart(days=7):
    d = daily_data(days)
    cfg = {"type": "line", "data": {"labels": d["labels"], "datasets": [
        {"label": "Uday Comm Agr", "data": d["uday"], "borderColor": "#2196F3", "fill": False, "tension": 0.3,
         "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 6, "color": "#1565C0", "font": {"size": 10, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}},
        {"label": "Maa Vaishno Telecom", "data": d["mv"], "borderColor": "#F44336", "fill": False, "tension": 0.3,
         "datalabels": {"display": True, "align": "bottom", "anchor": "end", "offset": 6, "color": "#B71C1C", "font": {"size": 10, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}},
        {"label": "Total", "data": d["total"], "borderColor": "#4CAF50", "fill": False, "tension": 0.3, "borderWidth": 3,
         "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 20, "color": "#1B5E20", "font": {"size": 11, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}]},
        "options": {"title": {"display": True, "text": f"MNP Trend - Last {days} Days", "fontSize": 16},
        "legend": {"position": "bottom"}, "layout": {"padding": {"top": 40}}}}
    return qc(cfg)

def weekly_sum():
    d = daily_data(7)
    dt = d["dist_totals"]
    dts = {d["labels"][i]: d["total"][i] for i in range(len(d["labels"]))}
    gt = sum(dts.values())
    lines = ["Weekly MNP Summary", "(Last 7 days)", "", "Distributor-wise total:"]
    for n, v in sorted(dt.items(), key=lambda x: -x[1]):
        lines.append(f"{n}: {v}")
    lines.append("")
    lines.append(f"Grand Total: {gt}")
    nz = {k: v for k, v in dts.items() if v > 0}
    if nz:
        b = max(nz.items(), key=lambda x: x[1])
        w = min(nz.items(), key=lambda x: x[1])
        lines.append(f"Best day: {b[0]} ({b[1]})")
        lines.append(f"Lowest: {w[0]} ({w[1]})")
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
    today = datetime.now().date()
    if re.search(r'\bkal\b', L) or "yesterday" in L: return today - timedelta(days=1)
    if re.search(r'\baaj\b', L) or "today" in L or "abhi" in L: return today
    if re.search(r'\bparso\b', L): return today - timedelta(days=2)
    m = re.search(r'(\d{1,2})\s*(?:st|nd|rd|th)?\s*([a-z]+)', L)
    if m and m.group(2) in MON:
        try:
            c = datetime(today.year, MON[m.group(2)], int(m.group(1))).date()
            if c > today: c = datetime(today.year-1, MON[m.group(2)], int(m.group(1))).date()
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
    tu = s.get("target_uday", 0)
    tm = s.get("target_mv", 0)
    if not tu and not tm: return "Koi target set nahi."
    now = datetime.now()
    if h is not None:
        r = find_rep(now.date(), h, mi or 0)
        if not r: return f"Aaj {h:02d}:{(mi or 0):02d} ke aas-paas koi MNP report nahi mili."
        lbl = f"till {h:02d}:{(mi or 0):02d}"
    else:
        utc0 = datetime.combine(now.date(), datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
        if not reps: return "Aaj koi MNP report nahi aayi."
        r = reps[0]; lbl = "latest"
    p = r.get("parsed") or parse_mnp(r.get("text", ""))
    if not p: return "Report parse nahi ho paayi."
    u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
    m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
    tds = today_str(); yds = yest_str()
    al = []; ex = []
    if tu:
        if u < tu:
            g = tu - u; pct = int(u/tu*100)
            tg = get_tag("uday"); ts = f" {tg}" if tg else ""
            al.append(f"Uday Comm Agr{ts}: {u}/{tu} ({pct}%) - gap {g}")
            if s.get("last_fail_uday") == yds: ex.append(f"Uday Comm Agr{ts} - aap aaj bhi target pura nahi kar paye!")
            upd_setting("last_fail_uday", tds)
        else: upd_setting("last_fail_uday", "")
    if tm:
        if m < tm:
            g = tm - m; pct = int(m/tm*100)
            tg = get_tag("mv"); ts = f" {tg}" if tg else ""
            al.append(f"Maa Vaishno Telecom{ts}: {m}/{tm} ({pct}%) - gap {g}")
            if s.get("last_fail_mv") == yds: ex.append(f"Maa Vaishno Telecom{ts} - aap aaj bhi target pura nahi kar paye!")
            upd_setting("last_fail_mv", tds)
        else: upd_setting("last_fail_mv", "")
    if al:
        msg = f"Low Performance Alert ({lbl})\n\n" + "\n".join(al)
        if ex: msg += "\n\n" + "\n".join(ex)
        if tu and u >= tu: msg += "\n\nUday Comm Agr ne target pura kar liya!"
        if tm and m >= tm: msg += "\nMaa Vaishno Telecom ne target pura kar liya!"
        return msg
    return f"Sab targets pura ho gaye ({lbl})\n\nUday Comm Agr: {u}/{tu}\nMaa Vaishno Telecom: {m}/{tm}"

def bal_alert(p, th=3):
    if not p: return None
    low = []
    for it in p.get("items", []):
        n = MSISDN_MAP.get(it["msisdn"])
        if not n: continue
        if it["stock_days"] < th:
            k = "uday" if "uday" in n.lower() else "mv"
            tg = get_tag(k); ts = f" {tg}" if tg else ""
            low.append({"name": n, "ts": ts, "d": it["stock_days"], "b": it["balance"], "s": it["sale_lakh"]})
    if not low: return None
    lines = ["Low Balance Alert", ""]
    for it in low:
        lines.append(f"- {it['name']}{it['ts']}")
        lines.append(f"   Stock Days: {it['d']}")
        lines.append(f"   Balance: {it['b']:,}")
        lines.append(f"   Sale: {it['s']} Lakh")
        lines.append("")
    lines.append(f"Aapka balance {th} din se kam hai - aaj hi billing karayen!")
    return "\n".join(lines)

def stock_chk(th=3):
    latest = list(get_db().find({"type": "balance"}).sort("timestamp", -1).limit(1))
    if not latest: return "Koi balance report nahi mili."
    p = latest[0].get("parsed")
    if not p: return "Balance report parse nahi ho paayi."
    al = bal_alert(p, th)
    if al: return al
    lines = ["Sab distributors ka stock theek hai", ""]
    for it in p.get("items", []):
        n = MSISDN_MAP.get(it["msisdn"], it["msisdn"])
        lines.append(f"{n}: {it['stock_days']} din")
    return "\n".join(lines)

def hist_avg(days=30):
    now = datetime.now()
    ed = now.date() - timedelta(days=1)
    sd = ed - timedelta(days=days-1)
    us = datetime.combine(sd, datetime.min.time()).timestamp() - IST
    ue = datetime.combine(ed, datetime.max.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": us, "$lte": ue}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    daily = {}
    for r in reps:
        daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    tots = [(parse_mnp(r.get("text", "")) or {}).get("total", {}).get("total", 0) for r in daily.values()]
    tots = [t for t in tots if t]
    return sum(tots)/len(tots) if tots else 0

def projection(period="day"):
    ws, we = get_wh()
    now = datetime.now()
    today = now.date()
    if period == "day":
        utc0 = datetime.combine(today, datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
        if not reps: return "Aaj koi report nahi aayi."
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
        if rem <= 0: return f"Aaj ka final\n\nUday: {u}\nMaa Vaishno: {m}\nTotal: {t}"
        pu = int(u/el*(el+rem)); pm = int(m/el*(el+rem)); pt = int(t/el*(el+rem))
        return f"Aaj ki Projection\n\nAbhi tak ({last.strftime('%H:%M')}):\nUday: {u}\nMaa Vaishno: {m}\nTotal: {t}\n\nExpected ({int(we)}:00 tak):\nUday: ~{pu}\nMaa Vaishno: ~{pm}\nTotal: ~{pt}"
    elif period == "week":
        dsm = today.weekday()
        mon = today - timedelta(days=dsm)
        um = datetime.combine(mon, datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
        daily = {}
        for r in reps:
            daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
        wt = 0; dd = 0
        for k, r in daily.items():
            p = r.get("parsed") or parse_mnp(r.get("text", ""))
            if p: wt += (p.get("total") or {}).get("total", 0); dd += 1
        rd = 7 - today.weekday() - 1
        ad = hist_avg(30)
        pr = wt + ad * rd
        return f"Weekly Projection\n\nIs hafte abhi tak: {wt} ({dd} din)\nHistorical avg: {int(ad)}/din\nBache hue {rd} din: ~{int(ad*rd)}\n\nExpected week total: ~{int(pr)}"
    elif period == "month":
        ms = today.replace(day=1)
        um = datetime.combine(ms, datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
        daily = {}
        for r in reps:
            daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
        mt = 0; dd = 0
        for k, r in daily.items():
            p = r.get("parsed") or parse_mnp(r.get("text", ""))
            if p: mt += (p.get("total") or {}).get("total", 0); dd += 1
        nm = today.replace(year=today.year+1, month=1, day=1) if today.month == 12 else today.replace(month=today.month+1, day=1)
        dim = (nm - ms).days
        rd = dim - today.day
        ad = hist_avg(30)
        am = mt/dd if dd else 0
        ba = max(ad, am) if dd else ad
        pr = mt + ba * rd
        return f"Monthly Projection ({ms.strftime('%b %Y')})\n\nAbhi tak: {mt} ({dd} din)\nDaily avg: {int(am)}/din\nBache hue {rd} din: ~{int(ba*rd)}\n\nExpected month total: ~{int(pr)}"
    return "Period samjha nahi."

def peak_hours(days=7):
    now = datetime.now()
    s = now - timedelta(days=days-1)
    us = s.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": us}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    if not reps: return "Koi report nahi mili."
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
    if not hr: return "Data parse nahi ho paaya."
    sh = sorted(hr.items(), key=lambda x: -x[1])[:5]
    lines = [f"Peak Hours (last {days} days)", ""]
    for h, c in sh:
        lines.append(f"{h:02d}:00 - {h+1:02d}:00 -> {c} MNP")
    return "\n".join(lines)

def dist_cmp(days=7, today_only=False):
    if today_only: days = 1
    d = daily_data(days)
    ut = sum(d["uday"]); mt = sum(d["mv"])
    if ut == 0 and mt == 0: return "Koi data nahi."
    t = ut + mt
    up = int(ut/t*100) if t else 0
    mp = int(mt/t*100) if t else 0
    ua = int(ut/days); ma = int(mt/days)
    ubi = d["uday"].index(max(d["uday"])) if d["uday"] else 0
    mbi = d["mv"].index(max(d["mv"])) if d["mv"] else 0
    w = "Uday Comm Agr" if ut > mt else ("Maa Vaishno Telecom" if mt > ut else "Tie")
    hdr = "Distributor Comparison (AAJ)" if today_only else f"Distributor Comparison (last {days} days)"
    lines = [hdr, "", f"Uday Comm Agr: {ut} ({up}%)", f"   Avg: {ua}/din | Best: {d['labels'][ubi]} ({max(d['uday'])})", "", f"Maa Vaishno: {mt} ({mp}%)", f"   Avg: {ma}/din | Best: {d['labels'][mbi]} ({max(d['mv'])})", "", f"Winner: {w}"]
    return "\n".join(lines)

def growth(period="week"):
    db = get_db()
    now = datetime.now()
    today = now.date()
    def sp(a, b):
        reps = list(db.find({"timestamp": {"$gte": a, "$lte": b}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
        daily = {}
        for r in reps:
            daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
        tt = 0
        for k, r in daily.items():
            p = r.get("parsed") or parse_mnp(r.get("text", ""))
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
        tt = sp(ts, now.timestamp()); lt = sp(ls, lae_ts)
        g = int((tt-lt)/lt*100) if lt else 0
        return f"Growth Rate (Week)\n\nThis week ({dd} din): {tt}\nLast week (same days): {lt}\nGrowth: {'+' if g>=0 else ''}{g}%"
    elif period == "month":
        tms = today.replace(day=1)
        lms = tms.replace(year=tms.year-1, month=12) if tms.month == 1 else tms.replace(month=tms.month-1)
        ts = datetime.combine(tms, datetime.min.time()).timestamp() - IST
        ls = datetime.combine(lms, datetime.min.time()).timestamp() - IST
        lae = lms + timedelta(days=today.day-1)
        lae_ts = datetime.combine(lae, datetime.max.time()).timestamp() - IST
        tt = sp(ts, now.timestamp()); lt = sp(ls, lae_ts)
        g = int((tt-lt)/lt*100) if lt else 0
        return f"Growth Rate (Month)\n\nThis month ({today.day} din): {tt}\nLast month (same days): {lt}\nGrowth: {'+' if g>=0 else ''}{g}%"
    return "Period samjha nahi."

def tgt_ach(period="month"):
    s = get_settings()
    tu = s.get("target_uday", 0); tm = s.get("target_mv", 0)
    if not tu and not tm: return "Pehle target set karo."
    now = datetime.now(); today = now.date()
    sd = today.replace(day=1) if period == "month" else today - timedelta(days=today.weekday())
    us = datetime.combine(sd, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": us}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    daily = {}
    for r in reps:
        daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    up = 0; mp = 0; td = len(daily)
    for k, r in daily.items():
        p = r.get("parsed") or parse_mnp(r.get("text", ""))
        if not p: continue
        u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        if tu and u >= tu: up += 1
        if tm and m >= tm: mp += 1
    ur = int(up/td*100) if td else 0; mr = int(mp/td*100) if td else 0
    lbl = "is mahine" if period == "month" else "is hafte"
    lines = [f"Target Achievement ({lbl})", "", f"Total din: {td}", ""]
    if tu: lines.append(f"Uday: {up}/{td} din pass ({ur}%)")
    if tm: lines.append(f"Maa Vaishno: {mp}/{td} din pass ({mr}%)")
    return "\n".join(lines)

def cust_rep(dist, days=7):
    d = daily_data(days)
    dl = dist.lower()
    if "uday" in dl: vals = d["uday"]; n = "Uday Comm Agr"
    elif "vaishno" in dl or "mv" in dl: vals = d["mv"]; n = "Maa Vaishno Telecom"
    else: return f"Distributor '{dist}' nahi pehchana."
    tot = sum(vals); dd = len([v for v in vals if v > 0])
    av = int(tot/dd) if dd else 0
    bi = vals.index(max(vals)) if vals else 0
    lines = [f"{n} - Last {days} Days", "", f"Total: {tot}", f"Daily avg: {av}/din", f"Best day: {d['labels'][bi]} ({max(vals)})", "", "Daily breakdown:"]
    for i, l in enumerate(d["labels"]):
        lines.append(f"{l}: {vals[i]}")
    return "\n".join(lines)

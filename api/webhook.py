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

# ============================================================
# YAHAN APNE TELEGRAM USERNAME / USER ID DAALO
# Telegram username bina @ ke likho. User ID numeric.
# Example: ADMIN_USERNAMES = ["akashverma", "pankaj"]
# ============================================================
ADMIN_USERNAMES = ["DTR_Mainpuri_Bot","@AkashV47"]
ADMIN_USER_IDS = []

# ============================================================
# PERMISSIONS - FIXED (Telegram se change nahi hongi)
# Format: "feature": (admin_allowed, user_allowed)
# ============================================================
PERMS = {
    "graphs": (True, True),
    "projections": (True, True),
    "performance": (True, True),
    "weekly_summary": (True, False),
    "compare": (True, False),
    "custom_report": (True, True),
    "menu": (True, True),
    "target_set": (True, False),
    "tag_set": (True, False),
    "working_hours": (True, False),
    "stock_threshold": (True, False),
    "stock_check": (True, True),
    "target_status": (True, False),
    "peak_hours": (True, False),
    "distributors": (True, True),
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

def set_target(k, v):
    get_db().update_one({"_id": "settings"}, {"$set": {f"target_{k}": v}}, upsert=True)

def get_tag(k):
    return get_settings().get(f"tag_{k}", "")

def set_tag(k, u):
    if u and not u.startswith("@"):
        u = "@" + u
    get_db().update_one({"_id": "settings"}, {"$set": {f"tag_{k}": u}}, upsert=True)

# ADMIN CHECK - hardcoded
def is_admin(uid, uname):
    if uid in ADMIN_USER_IDS:
        return True
    if uname and uname.lstrip("@").lower() in [a.lower().lstrip("@") for a in ADMIN_USERNAMES]:
        return True
    return False

# PERMISSION CHECK - fixed
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
    tu = s.get("target_uday", 0); tm = s.get("target_mv", 0)
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
    lt = list(get_db().find({"type": "balance"}).sort("timestamp", -1).limit(1))
    if not lt: return "Koi balance report nahi mili."
    p = lt[0].get("parsed")
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
    ed = now.date() - timedelta(days=1); sd = ed - timedelta(days=days-1)
    us = datetime.combine(sd, datetime.min.time()).timestamp() - IST
    ue = datetime.combine(ed, datetime.max.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": us, "$lte": ue}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    daily = {}
    for r in reps:
        daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    tots = []
    for r in daily.values():
        p = parse_mnp(r.get("text", ""))
        if p: tots.append((p.get("total") or {}).get("total", 0))
    return sum(tots)/len(tots) if tots else 0

def projection(period="day"):
    ws, we = get_wh()
    now = datetime.now(); today = now.date()
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
        el = max(0.5, ch - sh); rem = max(0, we - ch)
        if rem <= 0: return f"Aaj ka final\n\nUday: {u}\nMaa Vaishno: {m}\nTotal: {t}"
        pu = int(u/el*(el+rem)); pm = int(m/el*(el+rem)); pt = int(t/el*(el+rem))
        return f"Aaj ki Projection\n\nAbhi tak ({last.strftime('%H:%M')}):\nUday: {u}\nMaa Vaishno: {m}\nTotal: {t}\n\nExpected ({int(we)}:00 tak):\nUday: ~{pu}\nMaa Vaishno: ~{pm}\nTotal: ~{pt}"
    elif period == "week":
        mon = today - timedelta(days=today.weekday())
        um = datetime.combine(mon, datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
        daily = {}
        for r in reps:
            daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
        wt = 0; dd = 0
        for r in daily.values():
            p = parse_mnp(r.get("text", ""))
            if p: wt += (p.get("total") or {}).get("total", 0); dd += 1
        rd = 7 - today.weekday() - 1
        ad = hist_avg(30); pr = wt + ad * rd
        return f"Weekly Projection\n\nIs hafte abhi tak: {wt} ({dd} din)\nHistorical avg: {int(ad)}/din\nBache hue {rd} din: ~{int(ad*rd)}\n\nExpected week total: ~{int(pr)}"
    elif period == "month":
        ms = today.replace(day=1)
        um = datetime.combine(ms, datetime.min.time()).timestamp() - IST
        reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
        daily = {}
        for r in reps:
            daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
        mt = 0; dd = 0
        for r in daily.values():
            p = parse_mnp(r.get("text", ""))
            if p: mt += (p.get("total") or {}).get("total", 0); dd += 1
        nm = today.replace(year=today.year+1, month=1, day=1) if today.month == 12 else today.replace(month=today.month+1, day=1)
        dim = (nm - ms).days; rd = dim - today.day
        ad = hist_avg(30); am = mt/dd if dd else 0
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
    up = int(ut/t*100) if t else 0; mp = int(mt/t*100) if t else 0
    ua = int(ut/days); ma = int(mt/days)
    ubi = d["uday"].index(max(d["uday"])) if d["uday"] else 0
    mbi = d["mv"].index(max(d["mv"])) if d["mv"] else 0
    w = "Uday Comm Agr" if ut > mt else ("Maa Vaishno Telecom" if mt > ut else "Tie")
    hdr = "Distributor Comparison (AAJ)" if today_only else f"Distributor Comparison (last {days} days)"
    return "\n".join([hdr, "", f"Uday Comm Agr: {ut} ({up}%)", f"   Avg: {ua}/din | Best: {d['labels'][ubi]} ({max(d['uday'])})", "", f"Maa Vaishno: {mt} ({mp}%)", f"   Avg: {ma}/din | Best: {d['labels'][mbi]} ({max(d['mv'])})", "", f"Winner: {w}"])

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
        tm = today - timedelta(days=dsm); lm = tm - timedelta(days=7)
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
    for r in daily.values():
        p = parse_mnp(r.get("text", ""))
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
    d = daily_data(days); dl = dist.lower()
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
    

@app.route('/check-performance', methods=['GET'])
def chk_perf():
    try:
        t = perf_alert()
        if t.startswith("Koi target") or t.startswith("Aaj koi"):
            bot.send_message(GROUP_CHAT_ID, t); return "No action", 200
        if "Sab targets pura" in t: return "All targets achieved", 200
        bot.send_message(GROUP_CHAT_ID, t); return "Alert sent", 200
    except Exception as e: return "Error: " + str(e), 500


@app.route('/daily-quote', methods=['GET'])
def daily_quote():
    try:
        bot.send_message(GROUP_CHAT_ID, "Good Morning Team")
        time.sleep(1)
        r = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Generate a work-related motivational message in Hindi (Devanagari script). 2-3 lines. About teamwork, hard work, success. No greeting. No author name."},
                {"role": "user", "content": "Give me today's motivational message."}
            ], model="openai/gpt-oss-20b")
        bot.send_message(GROUP_CHAT_ID, r.choices[0].message.content.strip())
        return "Sent", 200
    except Exception as e: return "Error: " + str(e), 500


@app.route('/weekly-summary', methods=['GET'])
def weekly_ep():
    try:
        bot.send_message(GROUP_CHAT_ID, weekly_sum()); return "Sent", 200
    except Exception as e: return "Error: " + str(e), 500


@app.route('/save-report', methods=['POST'])
def save_ep():
    try:
        data = request.get_json(force=True, silent=True) or request.form.to_dict()
        txt = data.get("text", "").strip()
        if not txt: return "No text", 400
        save_mnp(txt, time.time()); return "Saved", 200
    except Exception as e: return "Error: " + str(e), 500


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
            if u.is_bot and u.username == BOT_USERNAME.replace("@", ""):
                is_rep_bot = True
        if not is_priv and not is_tag and not is_rep_bot:
            return
        ct = text.replace(BOT_USERNAME, "").strip() or "Hi"
        lo = ct.lower()
        uid = message.from_user.id
        un = message.from_user.username
        adm = is_admin(uid, un)

        if lo.strip() in ["who am i", "main kaun hu", "mera role"]:
            r = "Owner/Admin" if adm else "Normal User"
            bot.reply_to(message, f"{r}\n\nUsername: @{un or 'not set'}\nUser ID: {uid}")
            return

        if lo.strip() in ["/start", "/menu", "menu", "start", "help", "commands"]:
            if not check_perm("menu", adm):
                bot.reply_to(message, "Menu currently disabled hai.")
                return
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

        tm = re.search(r'(uday|mv|maa\s*vaishno|vaishno)\s*tag\s*(@?[\w_]+)', lo)
        if tm and (is_tag or is_priv):
            if not check_perm("tag_set", adm):
                bot.reply_to(message, "Ye command sirf admins use kar sakte hain.")
                return
            k = "uday" if "uday" in tm.group(1) else "mv"
            set_tag(k, tm.group(2))
            d = "Uday Comm Agr" if k == "uday" else "Maa Vaishno Telecom"
            bot.reply_to(message, f"{d} ke alerts mein ab {tm.group(2)} tag hoga.")
            return

        tr = re.search(r'(uday|mv|maa\s*vaishno|vaishno)\s*(?:tag\s*)?(?:remove|hatao|hata|delete|clear)', lo)
        if tr and (is_tag or is_priv):
            if not check_perm("tag_set", adm):
                bot.reply_to(message, "Ye command sirf admins use kar sakte hain.")
                return
            k = "uday" if "uday" in tr.group(1) else "mv"
            set_tag(k, "")
            d = "Uday Comm Agr" if k == "uday" else "Maa Vaishno Telecom"
            bot.reply_to(message, f"{d} ka tag hata diya gaya.")
            return

        if "tag" in lo and any(w in lo for w in ["status", "dikhao", "check"]) and (is_tag or is_priv):
            bot.reply_to(message, f"Current Tags:\nUday: {get_tag('uday') or '(set nahi)'}\nMaa Vaishno: {get_tag('mv') or '(set nahi)'}")
            return

        whm = re.search(r'working\s*hours?\s*(\d{1,2})(?::(\d{2}))?\s*(?:se|to|-)\s*(\d{1,2})(?::(\d{2}))?', lo)
        if whm and (is_tag or is_priv):
            if not check_perm("working_hours", adm):
                bot.reply_to(message, "Ye command sirf admins use kar sakte hain.")
                return
            a = int(whm.group(1)) + (int(whm.group(2))/60 if whm.group(2) else 0)
            b = int(whm.group(3)) + (int(whm.group(4))/60 if whm.group(4) else 0)
            set_wh(a, b)
            bot.reply_to(message, f"Working hours set: {int(a)}:00 se {int(b)}:00")
            return

        if ("working hours" in lo or "working hrs" in lo or "kaam ka time" in lo) and any(w in lo for w in ["status", "kitna", "check", "dikhao"]):
            a, b = get_wh()
            bot.reply_to(message, f"Current working hours: {int(a)}:00 se {int(b)}:00")
            return

        tg = re.search(r'(uday|maa\s*vaishno|mv|vaishno)\s*(?:target|tgt)\s*(\d+)', lo)
        if tg: dk = tg.group(1); vl = int(tg.group(2))
        else:
            tg = re.search(r'(?:target|tgt)\s*(?:set\s*)?(uday|maa\s*vaishno|mv|vaishno)\s*(\d+)', lo)
            if tg: dk = tg.group(1); vl = int(tg.group(2))
            else: dk = None; vl = None
        if dk and vl is not None and (is_tag or is_priv):
            if not check_perm("target_set", adm):
                bot.reply_to(message, "Ye command sirf admins use kar sakte hain.")
                return
            if "uday" in dk: set_target("uday", vl); bot.reply_to(message, f"Uday Comm Agr target set: {vl}")
            else: set_target("mv", vl); bot.reply_to(message, f"Maa Vaishno Telecom target set: {vl}")
            return

        if ("target" in lo or "tgt" in lo) and any(w in lo for w in ["status", "kitna", "check", "dikhao"]):
            s = get_settings(); a, b = get_wh()
            bot.reply_to(message, f"Current Targets:\nUday Comm Agr: {s.get('target_uday', 0)}\nMaa Vaishno Telecom: {s.get('target_mv', 0)}\n\nWorking hours: {int(a)}:00 se {int(b)}:00")
            return

        if any(w in lo for w in ["stock check", "stock alert", "balance check", "low stock", "stock status"]):
            if not check_perm("stock_check", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            th = get_settings().get("stock_threshold", 3)
            t2 = re.search(r'threshold\s*(\d+)', lo)
            if t2: th = int(t2.group(1))
            bot.reply_to(message, stock_chk(th))
            return

        stm = re.search(r'stock\s*threshold\s*(\d+)', lo)
        if stm and (is_tag or is_priv):
            if not check_perm("stock_threshold", adm):
                bot.reply_to(message, "Ye command sirf admins use kar sakte hain.")
                return
            upd_setting("stock_threshold", int(stm.group(1)))
            bot.reply_to(message, f"Stock alert threshold set: {stm.group(1)} din")
            return

        if any(w in lo for w in ["peak hour", "peak hours", "peak time", "kis time sabse zyada", "kab sabse zyada"]):
            if not check_perm("peak_hours", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            dy = 7
            d = re.search(r'(\d{1,2})\s*(?:din|days)', lo)
            if d: dy = int(d.group(1))
            bot.reply_to(message, peak_hours(dy))
            return

        if any(w in lo for w in ["distributor comparison", "dono distributor", "uday vs", "mv vs", "uday aur mv compare", "kaun better", "kaun aage"]):
            if not check_perm("distributors", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            if any(w in lo for w in ["aaj", "today", "abhi"]):
                bot.reply_to(message, dist_cmp(days=1, today_only=True)); return
            dy = 7
            d = re.search(r'(\d{1,2})\s*(?:din|days)', lo)
            if d: dy = int(d.group(1))
            bot.reply_to(message, dist_cmp(dy))
            return

        if any(w in lo for w in ["growth", "growth rate", "kitne percent badha", "kitna badha", "vikas"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            if any(w in lo for w in ["month", "mahine", "mahina"]): bot.reply_to(message, growth("month"))
            else: bot.reply_to(message, growth("week"))
            return

        if any(w in lo for w in ["target achievement", "target rate", "kitne din target pura", "achievement rate"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            if any(w in lo for w in ["month", "mahine", "mahina"]): bot.reply_to(message, tgt_ach("month"))
            else: bot.reply_to(message, tgt_ach("week"))
            return

        cr = re.search(r'(uday|mv|maa\s*vaishno|vaishno)\s*(?:ka\s*)?report', lo)
        if cr:
            if not check_perm("custom_report", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            dy = 7
            d = re.search(r'(\d{1,2})\s*(?:din|days)', lo)
            if d: dy = int(d.group(1))
            bot.reply_to(message, cust_rep(cr.group(1), dy))
            return

        if any(w in lo for w in ["projection", "prediction", "predict", "forecast", "estimate", "anuman"]):
            if not check_perm("projections", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            if any(w in lo for w in ["week", "hafte", "haftey", "saaptah"]): bot.reply_to(message, projection("week"))
            elif any(w in lo for w in ["month", "mahine", "mahina", "maasik"]): bot.reply_to(message, projection("month"))
            else: bot.reply_to(message, projection("day"))
            return

        if any(w in lo for w in ["ach", "achievement", "achiv", "achiev"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            t = ext_time(lo)
            bot.reply_to(message, perf_alert(t[0], t[1]) if t else perf_alert())
            return

        hp = any(w in lo for w in ["performance", "perfomance", "alert"])
        hc = any(w in lo for w in ["check", "karo", "do", "batao", "dikhao", "dekho"])
        if hp and hc:
            if not check_perm("performance", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            t = ext_time(lo)
            bot.reply_to(message, perf_alert(t[0], t[1]) if t else perf_alert())
            return

        if "till" in lo and any(w in lo for w in ["check", "performance", "perfomance", "alert"]):
            if not check_perm("performance", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            t = ext_time(lo)
            bot.reply_to(message, perf_alert(t[0], t[1]) if t else perf_alert())
            return

        if any(w in lo for w in ["graph", "chart"]):
            if not check_perm("graphs", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            dy = 7
            d = re.search(r'(\d{1,2})\s*(?:din|days)', lo)
            if d: dy = int(d.group(1))
            wb = "bar" in lo or "column" in lo
            wp = "pie" in lo or "share" in lo or "distribution" in lo
            wl = "line" in lo or "trend" in lo
            wa = any(w in lo for w in ["sabhi", "full", "teeno", "all", "sab", "sare"])
            bot.send_message(message.chat.id, "Graph ban raha hai...")
            if wa: ts = ["bar", "pie", "line"]
            elif wb and wp: ts = ["bar", "pie"]
            elif wb and wl: ts = ["bar", "line"]
            elif wp and wl: ts = ["pie", "line"]
            elif wp: ts = ["pie"]
            elif wl: ts = ["line"]
            else: ts = ["bar"]
            for t in ts:
                try:
                    if t == "bar": u = bar_chart(dy)
                    elif t == "pie": u = pie_chart(dy)
                    else: u = line_chart(dy)
                    bot.send_photo(message.chat.id, u); time.sleep(1)
                except Exception as ge:
                    bot.send_message(message.chat.id, f"Graph error ({t}): {str(ge)}")
            return

        if "weekly" in lo or "hafte ka summary" in lo or "hafte ki summary" in lo:
            if not check_perm("weekly_summary", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            bot.reply_to(message, weekly_sum())
            return

        if any(w in lo for w in ["compare", "farq", "antar", "difference"]):
            if not check_perm("compare", adm):
                bot.reply_to(message, "Ye feature currently disabled hai.")
                return
            parts = re.split(r'\baur\b|\bor\b|\bya\b|\band\b|\bvs\b|\bse\b', lo)
            an = []
            for p in parts:
                t = ext_time(p); d = ext_date(p)
                if t or d: an.append((d, t))
            if len(an) >= 2:
                a1, a2 = an[0], an[1]
                d1 = a1[0] or (datetime.now().date() - timedelta(days=1))
                d2 = a2[0] or datetime.now().date()
                t1 = a1[1] or (12, 0); t2 = a2[1] or (12, 0)
                r1 = find_rep(d1, t1[0], t1[1]); r2 = find_rep(d2, t2[0], t2[1])
                if not r1:
                    bot.reply_to(message, f"{d1.strftime('%d %b')} {t1[0]:02d}:{t1[1]:02d} ke aas-paas koi MNP report nahi mili."); return
                if not r2:
                    bot.reply_to(message, f"{d2.strftime('%d %b')} {t2[0]:02d}:{t2[1]:02d} ke aas-paas koi MNP report nahi mili."); return
                l1 = f"{d1.strftime('%d %b')} {t1[0]:02d}:{t1[1]:02d}"
                l2 = f"{d2.strftime('%d %b')} {t2[0]:02d}:{t2[1]:02d}"
                bot.reply_to(message, cmp_text(r1, r2, l1, l2)); return
            lt = list(get_db().find({"text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(2))
            if len(lt) >= 2: bot.reply_to(message, cmp_text(lt[1], lt[0])); return
            bot.reply_to(message, "Database mein kam se kam 2 MNP reports chahiye.")
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
    "graph_bar": "graphs", "graph_pie": "graphs", "graph_line": "graphs",
    "proj_day": "projections", "proj_week": "projections", "proj_month": "projections",
    "perf_check": "performance", "tgt_status": "target_status",
    "stock_check": "stock_check", "weekly_summary": "weekly_summary",
    "peak_hours": "peak_hours", "dist_compare": "distributors",
}


@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    try:
        bot.answer_callback_query(call.id)
        cid = call.message.chat.id
        d = call.data
        uid = call.from_user.id
        un = call.from_user.username
        adm = is_admin(uid, un)
        pk = BTN_MAP.get(d)
        if pk and not check_perm(pk, adm):
            bot.send_message(cid, "Ye feature currently disabled hai.")
            return
        if d == "graph_bar": bot.send_photo(cid, bar_chart(7))
        elif d == "graph_pie": bot.send_photo(cid, pie_chart(7))
        elif d == "graph_line": bot.send_photo(cid, line_chart(7))
        elif d == "proj_day": bot.send_message(cid, projection("day"))
        elif d == "proj_week": bot.send_message(cid, projection("week"))
        elif d == "proj_month": bot.send_message(cid, projection("month"))
        elif d == "perf_check": bot.send_message(cid, perf_alert())
        elif d == "tgt_status":
            s = get_settings(); a, b = get_wh()
            bot.send_message(cid, f"Current Targets:\nUday: {s.get('target_uday', 0)}\nMaa Vaishno: {s.get('target_mv', 0)}\n\nWorking hours: {int(a)}:00 se {int(b)}:00")
        elif d == "stock_check":
            th = get_settings().get("stock_threshold", 3)
            bot.send_message(cid, stock_chk(th))
        elif d == "weekly_summary": bot.send_message(cid, weekly_sum())
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

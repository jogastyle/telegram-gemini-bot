import os, re, time, json, telebot, urllib.parse, calendar, random, uuid
from datetime import datetime, timedelta
from groq import Groq
from pymongo import MongoClient
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
MONGO_URL = os.environ.get("MONGO_URL")
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
_db = None

def get_db():
    global _db
    if _db is None: _db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return _db["telegram_bot"]["reports"]

def get_settings(): return get_db().find_one({"_id": "settings"}) or {}
def upd_setting(k, v): get_db().update_one({"_id": "settings"}, {"$set": {k: v}}, upsert=True)

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

def sr_ai_enabled(): return get_settings().get("sr_ai_enabled", False)
def set_sr_ai(state): upd_setting("sr_ai_enabled", state)

def send_to_sr_groups(msg, specific_gid=None):
    if specific_gid:
        try: bot.send_message(specific_gid, msg)
        except Exception as e: print(f"SR send error to {specific_gid}: {e}")
        return
    for gid in get_sr_groups():
        try: bot.send_message(gid, msg)
        except Exception as e: print(f"SR send error to {gid}: {e}")

def save_scheduled(target, time_str, msg, sched_type="daily", specific_gid=None):
    key = f"{target}_{time_str.replace(':','')}_{uuid.uuid4().hex[:4]}"
    doc = {"_id": f"sched_{key}", "target": target, "time": time_str, "message": msg, "type": sched_type, "date": datetime.now().strftime("%Y-%m-%d"), "last_sent": "", "created": time.time(), "specific_gid": specific_gid}
    get_db().insert_one(doc)
    return key

def get_all_scheduled(): return list(get_db().find({"_id": {"$regex": "^sched_"}}))
def mark_sent(key, date_str): get_db().update_one({"_id": f"sched_{key}"}, {"$set": {"last_sent": date_str}})

def ts_str(ts): return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts + IST))
def today_str(): return time.strftime("%Y-%m-%d", time.gmtime(time.time() + IST))
def yest_str(): return time.strftime("%Y-%m-%d", time.gmtime(time.time() + IST - 86400))

def get_wh():
    s = get_settings()
    return s.get("wh_start", 7.0), s.get("wh_end", 19.0)
def set_wh(a, b): get_db().update_one({"_id": "settings"}, {"$set": {"wh_start": a, "wh_end": b}}, upsert=True)

def get_target(k, period="day"): return get_settings().get(f"target_{period}_{k}", 0)
def set_target(k, v, period="day"): get_db().update_one({"_id": "settings"}, {"$set": {f"target_{period}_{k}": v}}, upsert=True)

def is_admin(uid, uname):
    if uid in ADMIN_USER_IDS: return True
    if uname and uname.lstrip("@").lower() in [a.lower().lstrip("@") for a in ADMIN_USERNAMES]: return True
    return False

def is_known_user(uid): return get_db().find_one({"_id": f"user_{uid}"}) is not None
def add_known_user(uid, uname, fname): get_db().update_one({"_id": f"user_{uid}"}, {"$set": {"uid": uid, "username": uname, "fname": fname, "added": time.time()}}, upsert=True)

def check_perm(key, adm):
    if key not in PERMS: return True
    return PERMS[key][0] if adm else PERMS[key][1]

MSISDN_MAP = {"9997389467": "Uday Comm Agr", "7895110381": "Maa Vaishno Telecom"}
app = Flask(__name__)

DIST_P = re.compile(r'Dist\s+([A-Za-z0-9 &\.\-\']+?)\s*-\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*/\s*\((\d+)\)')
TOT_P = re.compile(r'^Total\s*-\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*/\s*\((\d+)\)', re.M)
TIME_P = re.compile(r'till\s+(\d{1,2})[:\.](\d{2})')
BAL_P = re.compile(r'(\d{10})\s*/\s*(\d+)\s*/\s*(\d+)\s*/\s*([\d\.]+)')

def parse_mnp(t):
    if not t or ("FTA MNP" not in t and "FTD" not in t): return None
    dists = {}
    for m in DIST_P.finditer(t):
        dists[m.group(1).strip()] = {"jio": int(m.group(2)), "vi": int(m.group(3)), "other": int(m.group(4)), "total": int(m.group(5))}
    total = None
    tm = TOT_P.search(t)
    if tm: total = {"jio": int(tm.group(1)), "vi": int(tm.group(2)), "other": int(tm.group(3)), "total": int(tm.group(4))}
    if not dists and not total: return None
    rtm = TIME_P.search(t)
    rtime = f"{int(rtm.group(1)):02d}:{rtm.group(2)}" if rtm else None
    return {"distributors": dists, "total": total, "report_time": rtime}

def is_balance_report(t):
    if not t: return False
    tl = t.lower()
    return "distributor balance report" in tl or ("balance report" in tl and "msisdn" in tl) or ("distributor balance" in tl and "stock days" in tl)

def parse_bal(t):
    if not t or not is_balance_report(t): return None
    items = [{"msisdn": m.group(1), "balance": int(m.group(2)), "sale_lakh": int(m.group(3)), "stock_days": float(m.group(4))} for m in BAL_P.finditer(t)]
    return {"items": items} if items else None

def save_mnp(t, ts):
    p = parse_mnp(t)
    if p: get_db().insert_one({"text": t, "timestamp": ts, "date_str": ts_str(ts), "parsed": p})

def save_bal(t, ts):
    p = parse_bal(t)
    if not p: return None
    get_db().insert_one({"text": t, "timestamp": ts, "date_str": ts_str(ts), "type": "balance", "parsed": p})
    return p

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
    for r in reps: last[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
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

def weekly_sum():
    d = daily_data(7)
    dt = d["dist_totals"]
    u_total = dt.get("Uday Comm Agr", 0)
    m_total = dt.get("Maa Vaishno Telecom", 0)
    grand = u_total + m_total
    dts = {d["labels"][i]: d["total"][i] for i in range(len(d["labels"]))}
    lines = ["📊 Weekly MNP Summary", "(Last 7 days)", "", "Distributor-wise:"]
    lines.append(f"🔵 Uday Comm Agr: {u_total}")
    lines.append(f"🔴 Maa Vaishno: {m_total}")
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
    if not reps: return "❌ Is mahine koi report nahi aayi."
    daily = {}
    for r in reps: daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    u_total = 0; m_total = 0; days_count = 0; day_breakdown = {}
    for k, r in daily.items():
        p = r.get("parsed") or parse_mnp(r.get("text", ""))
        if not p: continue
        u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
        m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
        u_total += u; m_total += m; days_count += 1; day_breakdown[k] = u + m
    grand = u_total + m_total
    lines = [f"📅 Monthly Summary ({ms.strftime('%b %Y')})", "", "Distributor-wise:"]
    lines.append(f"🔵 Uday Comm Agr: {u_total}")
    lines.append(f"🔴 Maa Vaishno: {m_total}")
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

def qc(config, w=800, h=400):
    e = urllib.parse.quote(json.dumps(config))
    return f"https://quickchart.io/chart?c={e}&w={w}&h={h}&bkg=white&plugins=chartjs-plugin-datalabels"

def bar_chart(days=7, mtd=False):
    d = daily_data(days, mtd=mtd)
    title_txt = "Daily MNP - MTD" if mtd else f"Daily MNP - Last {days} Days"
    cfg = {"type": "bar", "data": {"labels": d["labels"], "datasets": [{"label": "Uday Comm Agr", "data": d["uday"], "backgroundColor": "#2196F3"}, {"label": "Maa Vaishno Telecom", "data": d["mv"], "backgroundColor": "#F44336"}, {"label": "Total", "data": d["total"], "backgroundColor": "#4CAF50"}]}, "options": {"title": {"display": True, "text": title_txt, "fontSize": 16}, "legend": {"position": "bottom"}, "plugins": {"datalabels": {"display": True, "color": "white", "anchor": "center", "align": "center", "font": {"size": 11, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}}}
    return qc(cfg)

def pie_chart(days=7, mtd=False):
    d = daily_data(days, mtd=mtd)
    dt = d["dist_totals"]
    labels = list(dt.keys()) if dt else ["No Data"]
    values = list(dt.values()) if dt else [1]
    title_txt = "Distributor Share - MTD" if mtd else f"Distributor Share - Last {days} Days"
    cfg = {"type": "doughnut", "data": {"labels": labels, "datasets": [{"data": values, "backgroundColor": ["#2196F3", "#F44336", "#4CAF50"]}]}, "options": {"title": {"display": True, "text": title_txt, "fontSize": 16}, "legend": {"position": "bottom"}, "plugins": {"datalabels": {"display": True, "color": "white", "font": {"size": 12, "weight": "bold"}, "formatter": "function(v,c){var s=c.dataset.data.reduce(function(a,b){return a+b;},0);var p=Math.round(v/s*100);return v+'\\n('+p+'%)';}"}}}}
    return qc(cfg)

def line_chart(days=7, mtd=False):
    d = daily_data(days, mtd=mtd)
    title_txt = "MNP Trend - MTD" if mtd else f"MNP Trend - Last {days} Days"
    cfg = {"type": "line", "data": {"labels": d["labels"], "datasets": [{"label": "Uday Comm Agr", "data": d["uday"], "borderColor": "#2196F3", "fill": False, "tension": 0.3, "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 6, "color": "#1565C0", "font": {"size": 10, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}, {"label": "Maa Vaishno Telecom", "data": d["mv"], "borderColor": "#F44336", "fill": False, "tension": 0.3, "datalabels": {"display": True, "align": "bottom", "anchor": "end", "offset": 6, "color": "#B71C1C", "font": {"size": 10, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}, {"label": "Total", "data": d["total"], "borderColor": "#4CAF50", "fill": False, "tension": 0.3, "borderWidth": 3, "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 20, "color": "#1B5E20", "font": {"size": 11, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}]}, "options": {"title": {"display": True, "text": title_txt, "fontSize": 16}, "legend": {"position": "bottom"}, "layout": {"padding": {"top": 40}}}}
    return qc(cfg)

def dist_chart(dist, ctype="bar", days=7, mtd=False):
    d = daily_data(days, mtd=mtd)
    if "uday" in dist.lower(): vals = d["uday"]; n = "Uday Comm Agr"; color = "#2196F3"
    else: vals = d["mv"]; n = "Maa Vaishno Telecom"; color = "#F44336"
    title_txt = f"{n} - MTD" if mtd else f"{n} - Last {days} Days"
    if ctype == "bar":
        cfg = {"type": "bar", "data": {"labels": d["labels"], "datasets": [{"label": n, "data": vals, "backgroundColor": color}]}, "options": {"title": {"display": True, "text": title_txt, "fontSize": 16}, "legend": {"display": False}, "plugins": {"datalabels": {"display": True, "color": "white", "anchor": "center", "align": "center", "font": {"size": 11, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}}}
    elif ctype == "line":
        cfg = {"type": "line", "data": {"labels": d["labels"], "datasets": [{"label": n, "data": vals, "borderColor": color, "fill": False, "tension": 0.3, "borderWidth": 3, "datalabels": {"display": True, "align": "top", "anchor": "end", "offset": 6, "color": color, "font": {"size": 11, "weight": "bold"}, "formatter": "function(v){return v>0?v:'';}"}}]}, "options": {"title": {"display": True, "text": title_txt, "fontSize": 16}, "legend": {"display": False}, "layout": {"padding": {"top": 40}}}}
    else:
        tot = sum(vals)
        cfg = {"type": "doughnut", "data": {"labels": [n], "datasets": [{"data": [tot], "backgroundColor": [color]}]}, "options": {"title": {"display": True, "text": title_txt + f" (Total: {tot})", "fontSize": 16}, "legend": {"position": "bottom"}, "plugins": {"datalabels": {"display": True, "color": "white", "font": {"size": 14, "weight": "bold"}, "formatter": "function(v){return v;}"}}}}
    return qc(cfg)

def peak_hours(days=7):
    now = datetime.now()
    ws_h = 7
    we_h = 21
    s = now - timedelta(days=days-1)
    us = s.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": us}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    if not reps: return "❌ Koi report nahi mili."
    
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
            
    if not hr_total: return "❌ Working hours (7 AM - 9 PM) me koi data nahi mila."
    
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
    if not reps: return f"❌ {target_date.strftime('%d %b')} ko koi MNP report nahi aayi."
    
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
        
    if not hr: return f"❌ {target_date.strftime('%d %b')} ko working hours (7 AM - 9 PM) me koi data nahi."
    
    sh = sorted(hr.items(), key=lambda x: -x[1])
    day_label = target_date.strftime('%d %b %Y')
    today = datetime.now().date()
    if target_date == today: day_label = "Aaj"
    elif target_date == today - timedelta(days=1): day_label = "Kal"
    elif target_date == today - timedelta(days=2): day_label = "Parso"
    
    lines = [f"🕐 Peak Hour — {day_label} ({target_date.strftime('%d %b %Y')})", f"⏰ 7:00 AM - 9:00 PM", ""]
    for i, (h, c) in enumerate(sh[:5], 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "•"
        lines.append(f"{medal} {h:02d}:00 - {h+1:02d}:00 → {c} MNP")
    if sh:
        best_h, best_c = sh[0]
        lines.append("")
        lines.append(f"📊 Best hour: {best_h:02d}:00-{best_h+1:02d}:00 ({best_c} MNP)")
    return "\n".join(lines)

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
            al.append(f"🔴 Maa Vaishno: {m}/{tm} ({pct}%) - gap {g}")
            if s.get("last_fail_mv") == yds: ex.append("❗ Maa Vaishno - aap aaj bhi target pura nahi kar paye!")
            upd_setting("last_fail_mv", tds)
        else: upd_setting("last_fail_mv", "")
        
    if al:
        msg = f"⚠️ Low Performance Alert ({lbl})\n\n" + "\n".join(al)
        if ex: msg += "\n\n" + "\n".join(ex)
        if tu and u >= tu: msg += "\n\n✅ Uday Comm Agr ne target pura kar liya!"
        if tm and m >= tm: msg += "\n✅ Maa Vaishno ne target pura kar liya!"
        return msg
    return f"✅ Sab targets pura ho gaye ({lbl})\n\n🔵 Uday Comm Agr: {u}/{tu}\n🔴 Maa Vaishno: {m}/{tm}"

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
        if not reps: return "⚠️ Aaj koi report nahi aayi."
        
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
    for r in reps: daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
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
    return "\n".join(lines)

def proj_month():
    now = datetime.now()
    today = now.date()
    ms = today.replace(day=1)
    um = datetime.combine(ms, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    daily = {}
    for r in reps: daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
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
    return "\n".join(lines)

def day_ach():
    now = datetime.now()
    today = now.date()
    utc0 = datetime.combine(today, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": utc0}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
    if not reps: return "❌ Aaj koi report nahi aayi."
    p = reps[0].get("parsed") or parse_mnp(reps[0].get("text", ""))
    if not p: return "❌ Report parse nahi ho paayi."
    u = p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
    m = p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
    t = u + m
    lines = ["📊 FTD Achievement", ""]
    lines.append(f"Latest: {reps[0].get('date_str', '')}")
    lines.append("")
    lines.append(f"🔵 Uday: {u}")
    lines.append(f"🔴 Maa Vaishno: {m}")
    lines.append(f"🎯 Total: {t}")
    return "\n".join(lines)

def week_ach():
    now = datetime.now()
    today = now.date()
    mon = today - timedelta(days=today.weekday())
    um = datetime.combine(mon, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    daily = {}
    for r in reps: daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
    u_sofar = 0; m_sofar = 0; dd = 0
    for r in daily.values():
        p = parse_mnp(r.get("text", ""))
        if p:
            u_sofar += p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
            m_sofar += p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
            dd += 1
    t_sofar = u_sofar + m_sofar
    return f"📊 Weekly Achievement\n\nAbhi tak ({dd} din):\n🔵 Uday: {u_sofar}\n🔴 Maa Vaishno: {m_sofar}\n🎯 Total: {t_sofar}"

def month_ach():
    now = datetime.now()
    today = now.date()
    ms = today.replace(day=1)
    um = datetime.combine(ms, datetime.min.time()).timestamp() - IST
    reps = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    daily = {}
    for r in reps: daily[time.strftime("%Y-%m-%d", time.gmtime(r["timestamp"] + IST))] = r
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
    
    lines = [f"📅 MTD Achievement ({ms.strftime('%b %Y')})", "", f"Abhi tak ({dd} din):", f"🔵 Uday: {u_sofar}", f"🔴 Maa Vaishno: {m_sofar}", f"🎯 Total: {t_sofar}", "", "📊 Current Daily Avg (CRR):", f"🔵 Uday: {u_avg} MNP/din", f"🔴 Maa Vaishno: {m_avg} MNP/din"]
    
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
            pct = int(u_sofar/mt_u*100)
            diff = u_sofar - mt_u
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔵 Uday: {u_sofar}/{mt_u} ({pct}%) — {mark}")
        if mt_m:
            pct = int(m_sofar/mt_m*100)
            diff = m_sofar - mt_m
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append(f"🔴 Maa Vaishno: {m_sofar}/{mt_m} ({pct}%) — {mark}")
        if mt_u and mt_m:
            tot_t = mt_u + mt_m
            pct = int(t_sofar/tot_t*100)
            diff = t_sofar - tot_t
            mark = f"❌ gap {-diff}" if diff < 0 else f"✅ +{diff}"
            lines.append("")
            lines.append(f"📌 Overall: {t_sofar}/{tot_t} ({pct}%) — {mark}")
            
    return "\n".join(lines)

def crr_rrr_month():
    now = datetime.now()
    today = now.date()
    ms = today.replace(day=1)
    
    if today.month == 12:
        nm = today.replace(year=today.year+1, month=1, day=1)
    else:
        nm = today.replace(month=today.month+1, day=1)
        
    total_days_m = (nm - ms).days
    days_passed = today.day
    days_left_m = total_days_m - days_passed
    
    um = datetime.combine(ms, datetime.min.time()).timestamp() - IST
    reps_m = list(get_db().find({"timestamp": {"$gte": um}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
    u_sofar_m = 0; m_sofar_m = 0
    for r in reps_m:
        p = parse_mnp(r.get("text", ""))
        if p:
            u_sofar_m += p["distributors"].get("Uday Comm Agr", {}).get("total", 0)
            m_sofar_m += p["distributors"].get("Maa Vaishno Telecom", {}).get("total", 0)
            
    mt_u = get_target("uday", "month")
    mt_m = get_target("mv", "month")
    
    lines = ["📊 Required Run Rate (CRR & RRR)", "", f"📅 MTD Target Analysis ({days_left_m} din baaki):"]
    total_gap_m = 0
    total_sofar = u_sofar_m + m_sofar_m
    
    if mt_u:
        gap_u = mt_u - u_sofar_m
        crr_u = int(u_sofar_m / days_passed) if days_passed > 0 else 0
        if gap_u > 0:
            # RRR Calculation: (Target - Achieved) / Days Left
            rrr_u = int(gap_u / days_left_m)
            if gap_u % days_left_m != 0: rrr_u += 1 # Round up for perfect coverage
            lines.append(f"🔵 Uday: gap {gap_u}")
            lines.append(f"   ⚡ CRR: {crr_u} MNP/din")
            lines.append(f"   🎯 RRR: {rrr_u} MNP/din")
            total_gap_m += gap_u
        else:
            lines.append(f"🔵 Uday: ✅ Target Pura (CRR: {crr_u} MNP/din)")
            
    lines.append("")
    
    if mt_m:
        gap_m = mt_m - m_sofar_m
        crr_m = int(m_sofar_m / days_passed) if days_passed > 0 else 0
        if gap_m > 0:
            rrr_m = int(gap_m / days_left_m)
            if gap_m % days_left_m != 0: rrr_m += 1
            lines.append(f"🔴 Maa Vaishno: gap {gap_m}")
            lines.append(f"   ⚡ CRR: {crr_m} MNP/din")
            lines.append(f"   🎯 RRR: {rrr_m} MNP/din")
            total_gap_m += gap_m
        else:
            lines.append(f"🔴 Maa Vaishno: ✅ Target Pura (CRR: {crr_m} MNP/din)")
            
    lines.append("")
    
    if total_gap_m > 0:
        tot_mt = mt_u + mt_m
        crr_t = int(total_sofar / days_passed) if days_passed > 0 else 0
        rrr_t = int(total_gap_m / days_left_m)
        if total_gap_m % days_left_m != 0: rrr_t += 1
        
        lines.append(f"🎯 Overall Gap: {total_gap_m}")
        lines.append(f"   ⚡ CRR (Total): {crr_t} MNP/din")
        lines.append(f"   🎯 RRR (Total): {rrr_t} MNP/din")
        lines.append("")
        lines.append("⚠️ Alert: Target time pe pura karne ke liye daily RRR drive karein!!")
        
    return "\n".join(lines)

def dist_cmp(days=7, today_only=False):
    if today_only: days = 1
    d = daily_data(days)
    ut = sum(d["uday"])
    mt = sum(d["mv"])
    if ut == 0 and mt == 0: return "❌ Koi data nahi."
    t = ut + mt
    up = int(ut/t*100) if t else 0
    mp = int(mt/t*100) if t else 0
    w = "🔵 Uday Comm Agr" if ut > mt else ("🔴 Maa Vaishno Telecom" if mt > ut else "Tie")
    hdr = "⚖️ Distributor Comparison (AAJ)" if today_only else f"⚖️ Distributor Comparison (last {days} days)"
    return f"{hdr}\n\n🔵 Uday Comm Agr: {ut} ({up}%)\n🔴 Maa Vaishno: {mt} ({mp}%)\n\n🏆 Winner: {w}"

def find_rep(dt, h, mi):
    try:
        ist_ts = datetime.combine(dt, datetime.min.time().replace(hour=h, minute=mi)).timestamp()
        ut = ist_ts - IST
        s = datetime.combine(dt, datetime.min.time()).timestamp() - IST
        e = s + 86400
        reps = list(get_db().find({"timestamp": {"$gte": s, "$lt": e}, "text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", 1))
        return min(reps, key=lambda r: abs(r["timestamp"] - ut)) if reps else None
    except: return None

MON = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

def ampm(t, i, h):
    if h >= 13: return h
    if h == 12: return 0 if re.search(r'raat|night', t[max(0,i-20):i+30]) else 12
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
    L = t.lower(); td = datetime.now().date()
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
            try: add_known_user(uid, un, fname)
            except: pass

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
                    if ru.is_bot and ru.username == BOT_USERNAME.replace("@", ""): is_reply_to_bot = True
                if is_tag or is_reply_to_bot:
                    try:
                        clean_text = text.replace(BOT_USERNAME, "").strip() or "Hi"
                        r = client.chat.completions.create(messages=[{"role": "system", "content": "You are a helpful Telegram assistant. Reply in same language (Hindi/Hinglish). 1-2 short lines."}, {"role": "user", "content": clean_text}], model="openai/gpt-oss-20b")
                        bot.reply_to(message, r.choices[0].message.content.strip())
                    except Exception: pass
            return

        # ===== MAIN GROUP & PRIVATE =====
        if message.chat.id == int(GROUP_CHAT_ID) and ("FTA MNP" in text or "FTD" in text): save_mnp(text, msg_ts)
        if is_balance_report(text):
            p = save_bal(text, msg_ts)
            if p:
                al = bal_alert(p, get_settings().get("stock_threshold", 3))
                if al: bot.send_message(message.chat.id, al)
            return

        is_tag = BOT_USERNAME.lower() in text.lower()
        is_rep_bot = False
        if message.reply_to_message and message.reply_to_message.from_user:
            u = message.reply_to_message.from_user
            if u.is_bot and u.username == BOT_USERNAME.replace("@", ""): is_rep_bot = True
        if not is_priv and not is_tag and not is_rep_bot: return
        if is_priv and not adm and not is_known_user(uid):
            bot.reply_to(message, "🙏 Ye bot sirf DTR Mainpuri group ke liye hai.\n\nAap is group ke member nahi hain. Kripya group me contact karein.")
            return

        ct = text.replace(BOT_USERNAME, "").strip() or "Hi"
        lo = ct.lower()

        # ===== DM SCHEDULED MESSAGES (SETMNP / SETSR) =====
        if lo.startswith("setmnp ") or lo.startswith("setsr "):
            if not adm: return
            target = "mnp" if lo.startswith("setmnp ") else "sr"
            rest = text[len("setmnp " if target == "mnp" else "setsr "):].strip()
            parts = rest.split(" ", 1)
            sched_type = "once" if (parts and parts[0].lower() == "once") else "daily"
            specific_gid = None
            
            if sched_type == "once":
                if len(parts) < 2: return
                rem_parts = parts[1].split(" ", 1)
                if len(rem_parts) < 2: return
                time_str = rem_parts[0].strip()
                msg_text = rem_parts[1].strip()
            else:
                if len(parts) < 2: return
                if target == "sr" and parts[0].startswith("-"):
                    specific_gid = parts[0].strip()
                    rem_parts = parts[1].split(" ", 1)
                    if len(rem_parts) < 2: return
                    time_str = rem_parts[0].strip()
                    msg_text = rem_parts[1].strip()
                else:
                    time_str = parts[0].strip()
                    msg_text = parts[1].strip()

            if not re.match(r'^\d{1,2}:\d{2}$', time_str): return
            key = save_scheduled(target, time_str, msg_text, sched_type, specific_gid)
            target_name = "MNP Group" if target == "mnp" else ("SR Specific" if specific_gid else "SR All Groups")
            bot.reply_to(message, f"✅ Saved!\n\n🎯 Target: {target_name}\n⏰ Time: {time_str} ({sched_type})\n📝 Msg: {msg_text}\n🔑 Key: `{key}`\n\nDelete: `delmsg {key}`", parse_mode="Markdown")
            return

        if lo.strip() in ["listmsgs", "schedlist"]:
            if not adm: return
            all_msgs = get_all_scheduled()
            if not all_msgs: bot.reply_to(message, "❌ Koi scheduled message nahi hai."); return
            lines = [f"📋 Scheduled Messages ({len(all_msgs)})", ""]
            for doc in all_msgs:
                k = doc["_id"].replace("sched_", ""); tg = "MNP" if doc.get("target") == "mnp" else "SR"
                gid = doc.get("specific_gid", "All")
                lines.append(f"🔑 `{k}`\n   🎯 {tg} ({gid}) | ⏰ {doc.get('time','')} | {doc.get('type','')}\n   📝 {doc.get('message','')[:50]}")
            lines.append("Delete: `delmsg <key>`")
            bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
            return

        if lo.startswith("delmsg "):
            if not adm: return
            key = text.replace("delmsg ", "").strip()
            res = get_db().delete_one({"_id": f"sched_{key}"})
            bot.reply_to(message, f"✅ Deleted: `{key}`" if res.deleted_count else f"⚠️ Not found: `{key}`", parse_mode="Markdown")
            return

        # ===== SR GROUP MANAGEMENT =====
        if lo.startswith("sr add "):
            if not adm: return
            gid = lo.replace("sr add ", "").strip()
            if not gid.startswith("-"): return
            add_sr_group(gid)
            bot.reply_to(message, f"✅ SR group added: `{gid}`", parse_mode="Markdown")
            return

        if lo.startswith("sr remove "):
            if not adm: return
            gid = lo.replace("sr remove ", "").strip()
            remove_sr_group(gid)
            bot.reply_to(message, f"✅ SR group removed: `{gid}`", parse_mode="Markdown")
            return

        if lo.strip() in ["sr list", "sr groups"]:
            if not adm: return
            groups = get_sr_groups()
            bot.reply_to(message, "📋 SR Groups:\n" + "\n".join([f"{i+1}. `{g}`" for i, g in enumerate(groups)]), parse_mode="Markdown")
            return

        # ===== SAVE AS & DELETE =====
        savem = re.search(r'save\s+as\s+(\d{1,2})\s*([a-z]+)\s*(\d{1,2})(?::(\d{2}))?', lo)
        if savem and message.reply_to_message and adm:
            try:
                dyy = int(savem.group(1)); mstr = savem.group(2).lower()[:3]; hr = int(savem.group(3)); mi = int(savem.group(4)) if savem.group(4) else 0
                if mstr not in MON: bot.reply_to(message, "❌ Month galat hai."); return
                dt = datetime(datetime.now().year, MON[mstr], dyy, hr, mi); ts = dt.timestamp() - IST
                orig = message.reply_to_message.text or ""
                p_mnp = parse_mnp(orig); p_bal = parse_bal(orig)
                if p_mnp: get_db().insert_one({"text": orig, "timestamp": ts, "date_str": ts_str(ts), "parsed": p_mnp}); bot.reply_to(message, f"✅ MNP Saved: {dt.strftime('%d %b %H:%M')}")
                elif p_bal: get_db().insert_one({"text": orig, "timestamp": ts, "date_str": ts_str(ts), "type": "balance", "parsed": p_bal}); bot.reply_to(message, f"✅ Balance Saved: {dt.strftime('%d %b %H:%M')}")
            except Exception as e: bot.reply_to(message, f"❌ Error: {e}")
            return

        if lo.strip() in ["delete last", "undo"]:
            if not adm: return
            last = list(get_db().find({"text": {"$regex": "FTA MNP|FTD"}}).sort("timestamp", -1).limit(1))
            if last: get_db().delete_one({"_id": last[0]["_id"]}); bot.reply_to(message, "✅ Deleted last report.")
            return

        # ===== NEW RRR / CRR COMMAND =====
        if lo.strip() in ["rrr", "crr", "rrr month"]:
            bot.reply_to(message, crr_rrr_month())
            return

        if any(w in lo for w in ["peak hour", "peak time"]):
            bot.reply_to(message, peak_hours(7))
            return
        if "month compare" in lo:
            bot.reply_to(message, mom_compare())
            return
        if "best day" in lo or "worst day" in lo:
            bot.reply_to(message, best_worst(30))
            return
        if "projection" in lo:
            if "week" in lo: bot.reply_to(message, proj_week())
            elif "month" in lo: bot.reply_to(message, proj_month())
            else: bot.reply_to(message, projection("day"))
            return
        if "graph" in lo or "chart" in lo:
            if "uday" in lo: bot.send_photo(message.chat.id, dist_chart("uday", "bar", 7))
            elif "mv" in lo or "vaishno" in lo: bot.send_photo(message.chat.id, dist_chart("mv", "bar", 7))
            else: bot.send_photo(message.chat.id, bar_chart(7))
            return
        if "stock" in lo:
            bot.reply_to(message, stock_chk(3))
            return

        # ===== MENU =====
        if lo.strip() in ["/start", "/menu", "menu", "start"]:
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("📊 Graphs", callback_data="menu_graphs"),
                InlineKeyboardButton("📈 Projection", callback_data="menu_proj"),
                InlineKeyboardButton("🎯 Performance", callback_data="menu_perf"),
                InlineKeyboardButton("🚨 Stock Check", callback_data="stock_check"),
                InlineKeyboardButton("⚖️ Distributors", callback_data="dist_compare"),
                InlineKeyboardButton("📋 Weekly Summary", callback_data="weekly_summary")
            )
            if adm:
                kb.add(
                    InlineKeyboardButton("📅 Month vs Month", callback_data="mom_compare"),
                    InlineKeyboardButton("🏆 Best/Worst", callback_data="best_worst"),
                    InlineKeyboardButton("📊 Dist Charts", callback_data="menu_dist"),
                    InlineKeyboardButton("🏆 Peak Hours", callback_data="peak_hours")
                )
            bot.reply_to(message, "🤖 DTR Bot Menu", reply_markup=kb)
            return

        # ===== AI FALLBACK =====
        r = client.chat.completions.create(messages=[{"role": "system", "content": "You are a helpful Telegram assistant. Reply in 1-2 lines."}, {"role": "user", "content": ct}], model="openai/gpt-oss-20b")
        bot.reply_to(message, r.choices[0].message.content)
    except Exception as e: 
        print("Handle Error:", e)

BTN_MAP = {
    "graph_bar": "graphs", "graph_line": "graphs", "graph_pie": "graphs",
    "graph_bar_mtd": "graphs", "graph_line_mtd": "graphs", "graph_pie_mtd": "graphs",
    "proj_ftd": "projections", "proj_week": "projections", "proj_month": "projections",
    "perf_ftd": "performance", "perf_week": "performance", "perf_month": "performance",
    "stock_check": "stock_check", "weekly_summary": "weekly_summary", "dist_compare": "distributors"
}

@bot.callback_query_handler(func=lambda c: True)
def cb(call):
    try:
        bot.answer_callback_query(call.id)
        cid = call.message.chat.id
        mid = call.message.message_id
        d = call.data
        adm = is_admin(call.from_user.id, call.from_user.username)
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

        if d == "menu_graphs":
            kb = InlineKeyboardMarkup(row_width=3)
            kb.add(InlineKeyboardButton("📊 Bar", callback_data="graph_bar"), InlineKeyboardButton("📈 Line", callback_data="graph_line"), InlineKeyboardButton("🥧 Pie", callback_data="graph_pie"))
            kb.add(InlineKeyboardButton("📊 MTD Bar", callback_data="graph_bar_mtd"), InlineKeyboardButton("📈 MTD Line", callback_data="graph_line_mtd"), InlineKeyboardButton("🥧 MTD Pie", callback_data="graph_pie_mtd"))
            kb.add(InlineKeyboardButton("🔙 Back", callback_data="menu_main"))
            bot.edit_message_text("📊 Graphs Menu", cid, mid, reply_markup=kb)
            return

        if d == "menu_proj":
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("📊 FTD (Aaj)", callback_data="proj_ftd"), InlineKeyboardButton("📅 Weekly", callback_data="proj_week"), InlineKeyboardButton("📅 MTD", callback_data="proj_month"), InlineKeyboardButton("🔙 Back", callback_data="menu_main"))
            bot.edit_message_text("📈 Projection Menu", cid, mid, reply_markup=kb)
            return

        if d == "menu_perf":
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("📊 FTD ACH", callback_data="perf_ftd"), InlineKeyboardButton("📅 Weekly ACH", callback_data="perf_week"), InlineKeyboardButton("📅 MTD ACH", callback_data="perf_month"), InlineKeyboardButton("🔙 Back", callback_data="menu_main"))
            bot.edit_message_text("🎯 Performance Menu", cid, mid, reply_markup=kb)
            return

        if d == "menu_dist":
            if not adm: bot.send_message(cid, "❌ Sirf admin."); return
            kb = InlineKeyboardMarkup(row_width=3)
            kb.add(InlineKeyboardButton("🔵 UDAY CHARTS", callback_data="noop"))
            kb.add(InlineKeyboardButton("📊 Bar", callback_data="dist_uday_bar"), InlineKeyboardButton("📈 Line", callback_data="dist_uday_line"), InlineKeyboardButton("🥧 Pie", callback_data="dist_uday_pie"))
            kb.add(InlineKeyboardButton("🔴 MAA VAISHNO CHARTS", callback_data="noop"))
            kb.add(InlineKeyboardButton("📊 Bar", callback_data="dist_mv_bar"), InlineKeyboardButton("📈 Line", callback_data="dist_mv_line"), InlineKeyboardButton("🥧 Pie", callback_data="dist_mv_pie"))
            kb.add(InlineKeyboardButton("🔙 Back", callback_data="menu_main"))
            bot.edit_message_text("📊 Distributor Charts", cid, mid, reply_markup=kb)
            return

        if d == "menu_main":
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(InlineKeyboardButton("📊 Graphs", callback_data="menu_graphs"), InlineKeyboardButton("📈 Projection", callback_data="menu_proj"), InlineKeyboardButton("🎯 Perf", callback_data="menu_perf"), InlineKeyboardButton("🚨 Stock", callback_data="stock_check"), InlineKeyboardButton("⚖️ Dist", callback_data="dist_compare"), InlineKeyboardButton("📋 Weekly", callback_data="weekly_summary"))
            if adm: kb.add(InlineKeyboardButton("📅 MOM", callback_data="mom_compare"), InlineKeyboardButton("🏆 Best", callback_data="best_worst"), InlineKeyboardButton("📊 D-Charts", callback_data="menu_dist"), InlineKeyboardButton("🏆 Peak", callback_data="peak_hours"))
            bot.edit_message_text("🤖 Main Menu", cid, mid, reply_markup=kb)
            return

        if d == "noop": return
        if d == "show_rrr": bot.send_message(cid, crr_rrr_month()); return

        if d and d.startswith("dist_"):
            if not adm: return
            parts = d.replace("dist_", "").split("_")
            bot.send_photo(cid, dist_chart(parts[0], parts[1], 7))
            return

        if d == "graph_bar": bot.send_photo(cid, bar_chart(7))
        elif d == "graph_pie": bot.send_photo(cid, pie_chart(7))
        elif d == "graph_line": bot.send_photo(cid, line_chart(7))
        elif d == "graph_bar_mtd": bot.send_photo(cid, bar_chart(mtd=True))
        elif d == "graph_pie_mtd": bot.send_photo(cid, pie_chart(mtd=True))
        elif d == "graph_line_mtd": bot.send_photo(cid, line_chart(mtd=True))
        
        elif d == "proj_ftd": bot.send_message(cid, projection("day"))
        elif d == "proj_week": bot.send_message(cid, proj_week())
        elif d == "proj_month": bot.send_message(cid, proj_month())
        
        elif d == "perf_ftd": bot.send_message(cid, day_ach())
        elif d == "perf_week": bot.send_message(cid, week_ach())
        
        elif d == "perf_month":
            msg = bot.send_message(cid, month_ach())
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("📊 CRR & RRR", callback_data="show_rrr"))
            bot.edit_message_reply_markup(cid, msg.message_id, reply_markup=kb)
            return
            
        elif d == "mom_compare": bot.send_message(cid, mom_compare())
        elif d == "best_worst": bot.send_message(cid, best_worst(30))
        elif d == "stock_check": bot.send_message(cid, stock_chk(3))
        elif d == "weekly_summary": bot.send_message(cid, weekly_sum())
        elif d == "peak_hours": bot.send_message(cid, peak_hours(7))
        elif d == "dist_compare": bot.send_message(cid, dist_cmp(7))
    except Exception as e: print("CB Error:", e)

# ==================== WELCOME ====================
@bot.message_handler(content_types=['new_chat_members'])
def welcome_new_member(message):
    try:
        if message.chat.id != int(GROUP_CHAT_ID): return
        for member in message.new_chat_members:
            if member.is_bot: continue
            name = member.first_name or member.username or "Member"
            text = f"🎉 Welcome to DTR Mainpuri Group!\n\n👋 Hello {name} Sir!\n🤝 Aapka swagat hai!\n\n🤵 Mai hu Akash Sir ka Assistant\n\n📊 Aapki kisi bhi madad ke liye main yahi hoon —\nmujhe bulane ke liye tag karke /menu bhejen"
            bot.send_message(message.chat.id, text)
    except Exception as e: print("Welcome error:", e)

@app.route('/', methods=['POST'])
@app.route('/api/webhook', methods=['POST'])
def webhook():
    try:
        u = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
        bot.process_new_updates([u])
    except Exception as e: print("WEBHOOK ERROR:", str(e))
    return "OK", 200

@app.route('/', methods=['GET'])
def index(): return "Bot is running!", 200

@app.route('/test', methods=['GET'])
def test(): return "Token: " + ("SET" if BOT_TOKEN else "MISSING") + ", Groq: " + ("SET" if GROQ_KEY else "MISSING") + ", DB: " + ("SET" if MONGO_URL else "MISSING")

application = app
handler = app


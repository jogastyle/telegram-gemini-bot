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

# ---------- DAILY QUOTE ----------
@app.route('/daily-quote', methods=['GET'])
def daily_quote():
    try:
        bot.send_message(GROUP_CHAT_ID, "🌅 Good Morning Sir & Team")
        time.sleep(1)
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Generate ONE short work-related motivational quote in Hindi (Devanagari script only). Just the quote — no greeting, no extra text, no author name."},
                {"role": "user", "content": "Give me a new work motivational quote for today."}
            ],
            model="openai/gpt-oss-20b",
        )
        quote = response.choices[0].message.content.strip()
        bot.send_message(GROUP_CHAT_ID, "💪 " + quote)
        return "Sent both messages", 200
    except Exception as e:
        return "Error: " + str(e), 500

# ---------- DB HELPERS ----------
def save_report(text, timestamp):
    try:
        db = get_db()
        db.insert_one({
            "text": text,
            "timestamp": timestamp,
            "date_str": time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
        })
    except Exception as e:
        print("DB SAVE ERROR:", str(e))

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

def extract_time(text, keywords):
    lower = text.lower()
    for kw in keywords:
        idx = lower.find(kw)
        if idx == -1:
            continue
        window = lower[idx:idx + 40]
        m = re.search(r'(\d{1,2})[:\.](\d{2})', window)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.search(r'(\d{1,2})\s*(?:baje|bje|pm|am|o.?clock|o.?clock)', window)
        if m:
            return int(m.group(1)), 0
    return None

def extract_date(text):
    """Returns a datetime.date or None."""
    lower = text.lower()
    today = datetime.now().date()

    # 'kal' / 'yesterday' → yesterday
    if re.search(r'\bkal\b', lower) or "yesterday" in lower:
        return today - timedelta(days=1)

    # 'aaj' / 'today' / 'abhi' → today
    if re.search(r'\baaj\b', lower) or "today" in lower or "abhi" in lower:
        return today

    # 'parso' / 'day before yesterday'
    if re.search(r'\bparso\b', lower) or "day before yesterday" in lower:
        return today - timedelta(days=2)

    # 'DD Month' or 'DD MonthName'
    m = re.search(r'(\d{1,2})\s*(?:st|nd|rd|th)?\s*([a-z]+)', lower)
    if m:
        day = int(m.group(1))
        mon_str = m.group(2)
        if mon_str in MONTHS:
            try:
                # Try current year first
                candidate = datetime(today.year, MONTHS[mon_str], day).date()
                if candidate > today:
                    candidate = datetime(today.year - 1, MONTHS[mon_str], day).date()
                return candidate
            except:
                pass

    # 'DD/MM' or 'DD-MM' or 'DD.MM'
    m = re.search(r'(\d{1,2})[\/\-\.](\d{1,2})', lower)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            candidate = datetime(today.year, mo, d).date()
            if candidate > today:
                candidate = datetime(today.year - 1, mo, d).date()
            return candidate
        except:
            pass

    return None

def find_report_at(target_date, hour, minute):
    """Find closest report on target_date near given hour:minute."""
    try:
        start = datetime.combine(target_date, datetime.min.time()).timestamp()
        end = start + 86400  # +1 day

        db = get_db()
        reports = list(db.find({"timestamp": {"$gte": start, "$lt": end}}).sort("timestamp", 1))
        if not reports:
            return None

        target_ts = datetime.combine(
            target_date,
            datetime.min.time().replace(hour=hour, minute=minute)
        ).timestamp()

        return min(reports, key=lambda r: abs(r["timestamp"] - target_ts))
    except Exception as e:
        print("FIND ERROR:", str(e))
        return None

# ---------- MESSAGE HANDLER ----------
@bot.message_handler(func=lambda m: True)
def handle(message):
    try:
        text = message.text or ""
        msg_ts = message.date

        # Auto-save MNP reports
        if message.chat.id == int(GROUP_CHAT_ID) and ("FTA MNP" in text or "FTD" in text):
            save_report(text, msg_ts)

        is_private = message.chat.type == "private"
        is_tagged = BOT_USERNAME.lower() in text.lower()

        is_reply_to_bot = False
        if message.reply_to_message and message.reply_to_message.from_user:
            replied_user = message.reply_to_message.from_user
            if replied_user.is_bot and replied_user.username == BOT_USERNAME.replace("@", ""):
                is_reply_to_bot = True

        if not is_private and not is_tagged and not is_reply_to_bot:
            return

        clean_text = text.replace(BOT_USERNAME, "").strip()
        if not clean_text:
            clean_text = "Hi"

        lower = clean_text.lower()

        # ---------- COMPARE LOGIC ----------
        wants_compare = any(w in lower for w in ["compare", "farq", "antar", "difference", "vs"])
        if wants_compare:
            # Extract two time anchors (before/after 'aur', 'and', 'vs', 'se')
            parts = re.split(r'\baur\b|\band\b|\bvs\b|\bse\b', lower)

            anchors = []
            for part in parts:
                t = extract_time(part, ["baje", "bje", ":", ".", "pm", "am", "dophar", "subah", "shaam", "raat"])
                d = extract_date(part)
                if t or d:
                    anchors.append((d, t, part.strip()))

            if len(anchors) >= 2:
                a1, a2 = anchors[0], anchors[1]
                # Fill missing date with defaults
                d1 = a1[0] if a1[0] else (datetime.now().date() - timedelta(days=1))
                d2 = a2[0] if a2[0] else datetime.now().date()
                t1 = a1[1] if a1[1] else (12, 0)
                t2 = a2[1] if a2[1] else (12, 0)

                r1 = find_report_at(d1, t1[0], t1[1])
                r2 = find_report_at(d2, t2[0], t2[1])

                if not r1:
                    bot.reply_to(message, f"{d1.strftime('%d %b')} {t1[0]}:{t1[1]:02d} ke aas-paas koi report database mein nahi mili.")
                    return
                if not r2:
                    bot.reply_to(message, f"{d2.strftime('%d %b')} {t2[0]}:{t2[1]:02d} ke aas-paas koi report database mein nahi mili.")
                    return

                combined = (
                    "Compare these two reports:\n\n"
                    f"REPORT 1 ({d1.strftime('%d %b')} {t1[0]}:{t1[1]:02d}, saved at {r1['date_str']}):\n{r1['text']}\n\n"
                    f"REPORT 2 ({d2.strftime('%d %b')} {t2[0]}:{t2[1]:02d}, saved at {r2['date_str']}):\n{r2['text']}"
                )
                response = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": "Compare the two MNP reports. Reply in Hinglish, under 5 lines. For each district: 'X: pehle A thi, ab B hai (C ka farq)'. End with 'Total: pehle X, ab Y (Z ka farq)'. No tables."},
                        {"role": "user", "content": combined}
                    ],
                    model="openai/gpt-oss-20b",
                )
                bot.reply_to(message, response.choices[0].message.content)
                return

            # Fallback: last two reports
            db = get_db()
            last_two = list(db.find().sort("timestamp", -1).limit(2))
            if len(last_two) >= 2:
                r1, r2 = last_two[1], last_two[0]
                combined = (
                    "Compare these two reports:\n\n"
                    f"REPORT 1 ({r1['date_str']}):\n{r1['text']}\n\n"
                    f"REPORT 2 ({r2['date_str']}):\n{r2['text']}"
                )
                response = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": "Compare the two MNP reports. Reply in Hinglish, under 5 lines. For each district: 'X: pehle A thi, ab B hai (C ka farq)'. End with 'Total: pehle X, ab Y (Z ka farq)'. No tables."},
                        {"role": "user", "content": combined}
                    ],
                    model="openai/gpt-oss-20b",
                )
                bot.reply_to(message, response.choices[0].message.content)
                return
            else:
                bot.reply_to(message, "Database mein kam se kam 2 reports chahiye compare karne ke liye. Abhi " + str(len(last_two)) + " hai.")
                return

        # ---------- NORMAL REPLY ----------
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful Telegram assistant. Reply in same language. For general questions answer in 1-3 lines."},
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

# ---------- WEBHOOK ----------
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

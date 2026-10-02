import os
import time
import telebot
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
            replied_user = message.reply_to_message.from_user
            if replied_user.is_bot and replied_user.username == BOT_USERNAME.replace("@", ""):
                is_reply_to_bot = True

        if not is_private and not is_tagged and not is_reply_to_bot:
            return

        clean_text = text.replace(BOT_USERNAME, "").strip()
        if not clean_text:
            clean_text = "Hi"

        lower = clean_text.lower()
        if ("compare" in lower or "farq" in lower or "antar" in lower) and "report" in lower:
            db = get_db()
            last_two = list(db.find().sort("timestamp", -1).limit(2))
            if len(last_two) >= 2:
                r1 = last_two[1]["text"]
                r2 = last_two[0]["text"]
                combined = f"Compare these two reports:\n\nREPORT 1 (older):\n{r1}\n\nREPORT 2 (newer):\n{r2}"
                response = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": "Compare the two MNP reports. Reply in Hinglish, under 4 lines. For each district: 'X: kal A thi, aaj B hai (C ka farq)'. End with 'Total: kal X, aaj Y (Z ka farq)'. No tables."},
                        {"role": "user", "content": combined}
                    ],
                    model="openai/gpt-oss-20b",
                )
                bot.reply_to(message, response.choices[0].message.content)
                return
            else:
                bot.reply_to(message, "Database mein sirf ek report hai. Do reports save hone ke baad compare karunga.")
                return

        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful Telegram assistant. Reply in same language. For general questions answer in 1-3 lines. If user sends MNP reports and asks to compare, reply in Hinglish under 6 lines."},
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

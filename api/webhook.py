
import os
import telebot
from groq import Groq
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID")

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
client = Groq(api_key=GROQ_KEY)

BOT_USERNAME = "@DTR_Mainpuri_Bot"

app = Flask(__name__)

@app.route('/daily-quote', methods=['GET'])
def daily_quote():
    try:
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Generate one short work-related motivational quote in Hindi (Devanagari script only). Just the quote, no extra text."},
                {"role": "user", "content": "Give me a new motivational quote for the day."}
            ],
            model="openai/gpt-oss-20b",
        )
        quote = response.choices[0].message.content.strip()
        bot.send_message(GROUP_CHAT_ID, "🌅 " + quote)
        return "Sent: " + quote, 200
    except Exception as e:
        return "Error: " + str(e), 500

@bot.message_handler(func=lambda m: True)
def handle(message):
    try:
        is_private = message.chat.type == "private"
        text = message.text or ""
        is_tagged = BOT_USERNAME.lower() in text.lower()

        # group mein sirf tag par reply, DM mein hamesha reply
        if not is_private and not is_tagged:
            return

        # tag hata do taaki AI ko saaf sawal mile
        clean_text = text.replace(BOT_USERNAME, "").strip()
        if not clean_text:
            clean_text = "Hi"

        response = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful Telegram assistant. Reply in the same language the user uses (Hindi, English, or Hinglish).\n\n"
                        "SPECIAL RULE — only when the user sends MNP report(s) and asks to compare:\n"
                        "- Reply in Hinglish, under 6 lines total.\n"
                        "- For each district write: '[District]: kal X thi, aaj Y hai (Z ka farq)'\n"
                        "- End with: 'Total: kal A thi, aaj B hai (C ka farq)'\n"
                        "- Use the 'Total' number in brackets from each district line.\n"
                        "- No tables, no bullet points, no causes.\n"
                        "- If a district is missing in one report, treat it as 0.\n\n"
                        "For ALL other questions, answer normally in 1-3 lines."
                    )
                },
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
    return "Token: " + ("SET" if BOT_TOKEN else "MISSING") + ", Groq: " + ("SET" if GROQ_KEY else "MISSING") + ", Group: " + ("SET" if GROUP_CHAT_ID else "MISSING")

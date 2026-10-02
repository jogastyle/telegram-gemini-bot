import os
import telebot
from groq import Groq
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")

# Group Chat ID — direct yahan daal diya
GROUP_CHAT_ID = "-1004368616206"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
client = Groq(api_key=GROQ_KEY)

BOT_USERNAME = "@DTR_Mainpuri_Bot"

app = Flask(__name__)

@app.route('/daily-quote', methods=['GET'])
def daily_quote():
    try:
        response = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate ONE short work-related motivational quote in Hindi (Devanagari script only). "
                        "It must be about work, effort, teamwork, or success. "
                        "Just the quote — no greeting, no extra text, no author name."
                    )
                },
                {"role": "user", "content": "Give me a new work motivational quote for today."}
            ],
            model="openai/gpt-oss-20b",
        )
        quote = response.choices[0].message.content.strip()
        final_message = "🌅 Good Morning Sir & Team\n\n" + quote
        bot.send_message(GROUP_CHAT_ID, final_message)
        return "Sent: " + final_message, 200
    except Exception as e:
        return "Error: " + str(e), 500

@bot.message_handler(func=lambda m: True)
def handle(message):
    try:
        is_private = message.chat.type == "private"
        text = message.text or ""
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
    return "Token: " + ("SET" if BOT_TOKEN else "MISSING") + ", Groq: " + ("SET" if GROQ_KEY else "MISSING") + ", Group: " + GROUP_CHAT_ID

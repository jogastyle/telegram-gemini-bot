import os
import telebot
from groq import Groq
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
client = Groq(api_key=GROQ_KEY)

app = Flask(__name__)

@bot.message_handler(func=lambda m: True)
def handle(message):
    try:
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
                        "- Use the 'Total' number in brackets (Total) from each district line.\n"
                        "- No tables, no bullet points, no causes, no recommendations.\n"
                        "- If a district is missing in one report, treat it as 0.\n\n"
                        "For ALL other questions (motivation quotes, general knowledge, math, etc.), answer normally and helpfully in 1-3 lines. Do not ask for MNP reports."
                    )
                },
                {"role": "user", "content": message.text}
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
    return "Token: " + ("SET" if BOT_TOKEN else "MISSING") + ", Groq: " + ("SET" if GROQ_KEY else "MISSING")

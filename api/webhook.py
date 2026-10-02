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
                        "You are a concise assistant. Answer in 1-3 lines maximum. "
                        "Give only the key numbers and the difference. "
                        "Do not write tables, lists, causes, or recommendations unless asked. "
                        "Reply in the same language the user writes in (Hindi, English, or Hinglish)."
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

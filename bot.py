import os
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)
from openai import OpenAI
import asyncio

# Логирование
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# Переменные среды
TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 8443))

# Telegram App + OpenAI клиент
application = ApplicationBuilder().token(TOKEN).build()
client = OpenAI(api_key=OPENAI_KEY)

# Обработка команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот. Задай мне вопрос.")

# Обработка текста
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Отвечай кратко и по делу."},
                {"role": "user", "content": user_text}
            ]
        )
        reply = response.choices[0].message.content.strip()
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text("Ошибка при обращении к OpenAI!")

# Регистрируем handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

# Flask сервер для webhook
flask_app = Flask(__name__)

@flask_app.route("/webhook", methods=["POST"])
async def webhook():
    if request.method == "POST":
        await application.update_queue.put(Update.de_json(request.get_json(force=True), application.bot))
        return "OK", 200

async def run():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(url=WEBHOOK_URL)
    await application.updater.start_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="webhook",
        webhook_url=WEBHOOK_URL,
    )
    await application.updater.idle()

if __name__ == "__main__":
    asyncio.run(run())

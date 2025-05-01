import os
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)

from openai import OpenAI

# Получение ключей и портов из переменных окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # Пример: https://your-app.onrender.com/webhook
PORT = int(os.environ.get("PORT", 8443))     # Default порт 8443 для HTTPS

# Инициализация OpenAI-клиента
client = OpenAI(api_key=OPENAI_KEY)

# Flask-приложение для входящих запросов
flask_app = Flask(__name__)

# Telegram-обработчик
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    print(f"[LOG] Получено сообщение от {update.effective_user.first_name}: {user_message}")
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Отвечай на русском, кратко."},
                {"role": "user", "content": user_message},
            ]
        )
        reply = response.choices[0].message.content
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# Инициализация Telegram-приложения
telegram_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
telegram_app.add_handler(CommandHandler("start", handle_message))

# Подключение Webhook к Flask
@flask_app.route("/webhook", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    await telegram_app.process_update(update)
    return "ok", 200

# Запуск Webhook
if __name__ == "__main__":
    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
        app=flask_app
    )

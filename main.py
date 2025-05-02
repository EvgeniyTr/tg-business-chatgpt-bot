import os
import logging
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from flask import Flask, request

app = Flask(__name__)

# Настройка Telegram бота
def setup_telegram_bot():
    telegram_app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    
    async def handle_message(update, context):
        await update.message.reply_text("Привет! Я работаю!")
    
    telegram_app.add_handler(MessageHandler(filters.TEXT, handle_message))
    return telegram_app

# Для работы на Render
@app.route('/')
def home():
    return "Бот работает!"

if __name__ == '__main__':
    # Режим для Render
    if "RENDER" in os.environ:
        telegram_app = setup_telegram_bot()
        telegram_app.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv("PORT", 5000)),
            webhook_url=os.getenv("WEBHOOK_URL")
        )
        app.run(host='0.0.0.0', port=os.getenv("PORT", 5000))
    else:
        # Локальный режим
        telegram_app = setup_telegram_bot()
        telegram_app.run_polling()

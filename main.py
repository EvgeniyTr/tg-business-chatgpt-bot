import os
import asyncio
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from flask import Flask, request

app = Flask(__name__)
bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))

# Обработчик сообщений
async def handle_message(update: Update, context):
    await update.message.reply_text("✅ Бот работает корректно!")

# Инициализация приложения
application = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
application.add_handler(MessageHandler(filters.TEXT, handle_message))

# Вебхук для Telegram
@app.route('/webhook', methods=['POST'])
async def webhook():
    json_data = await request.get_json()
    update = Update.de_json(json_data, bot)
    await application.process_update(update)
    return "OK"

# Главная страница
@app.route('/')
def home():
    return "Telegram Bot is running on Render!"

# Асинхронная настройка вебхука
async def set_webhook_async():
    await bot.set_webhook(url=os.getenv("WEBHOOK_URL") + '/webhook')

if __name__ == '__main__':
    # Настройка для Render
    if "RENDER" in os.environ:
        # Устанавливаем вебхук асинхронно
        loop = asyncio.get_event_loop()
        loop.run_until_complete(set_webhook_async())
        
        # Запускаем Flask сервер
        app.run(host='0.0.0.0', port=os.getenv("PORT", 5000))
    else:
        # Локальный режим с polling
        application.run_polling()

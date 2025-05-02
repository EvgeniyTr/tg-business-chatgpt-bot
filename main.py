import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from flask import Flask, request, jsonify

# Инициализация Flask
app = Flask(__name__)

# Глобальная переменная для Application
application = None

async def initialize_bot():
    global application
    application = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    
    # Обработчик сообщений
    async def handle_message(update: Update, context):
        await update.message.reply_text("✅ Бот работает корректно!")
    
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    
    # Инициализация приложения
    await application.initialize()
    
    # Установка вебхука
    if "RENDER" in os.environ:
        await application.bot.set_webhook(
            url=os.getenv("WEBHOOK_URL") + '/webhook'
        )

# Синхронная обертка для обработки вебхука
@app.route('/webhook', methods=['POST'])
def webhook():
    json_data = request.get_json()
    update = Update.de_json(json_data, application.bot)
    
    # Запускаем асинхронную обработку
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(application.process_update(update))
        return jsonify({"status": "ok"})
    finally:
        loop.close()

@app.route('/')
def home():
    return "Telegram Bot is running on Render!"

if __name__ == '__main__':
    # Инициализация бота
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(initialize_bot())
    
    # Запуск production-сервера
    if "RENDER" in os.environ:
        from waitress import serve
        serve(app, host="0.0.0.0", port=10000)
    else:
        app.run(host='0.0.0.0', port=5000)

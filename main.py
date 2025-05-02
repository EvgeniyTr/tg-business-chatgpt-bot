import os
import asyncio
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from flask import Flask, request, jsonify

app = Flask(__name__)
bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))

# Создаем Application один раз при запуске
application = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

# Обработчик сообщений
async def handle_message(update: Update, context):
    await update.message.reply_text("✅ Бот работает корректно!")

# Добавляем обработчик
application.add_handler(MessageHandler(filters.TEXT, handle_message))

# Синхронная обертка для обработки вебхука
@app.route('/webhook', methods=['POST'])
def webhook():
    json_data = request.get_json()
    update = Update.de_json(json_data, bot)
    
    # Запускаем асинхронную обработку в event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.process_update(update))
    loop.close()
    
    return jsonify({"status": "ok"})

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
        # Устанавливаем вебхук
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(set_webhook_async())
        loop.close()
        
        # Запускаем Flask сервер
        app.run(host='0.0.0.0', port=os.getenv("PORT", 10000))
    else:
        # Локальный режим с polling
        application.run_polling()

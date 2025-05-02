import os
import asyncio
from threading import Thread, Event
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from flask import Flask, request, jsonify

app = Flask(__name__)

# Глобальные переменные
application = None
loop_ready = Event()

def run_async_loop():
    global application
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Создаем Application внутри event loop
    async def create_app():
        global application
        application = ApplicationBuilder() \
            .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
            .build()
        
        async def handle_message(update: Update, context):
            await update.message.reply_text("✅ Бот работает стабильно!")
        
        application.add_handler(MessageHandler(filters.TEXT, handle_message))
        await application.initialize()
        
        if "RENDER" in os.environ:
            await application.bot.set_webhook(
                url=os.getenv("WEBHOOK_URL") + '/webhook'
            )
        
        loop_ready.set()  # Сигнализируем, что бот готов
    
    loop.run_until_complete(create_app())
    loop.run_forever()

@app.route('/webhook', methods=['POST'])
def webhook():
    if not loop_ready.is_set():
        return jsonify({"status": "error", "message": "Bot is initializing"}), 503
    
    json_data = request.get_json()
    update = Update.de_json(json_data, application.bot)
    
    future = asyncio.run_coroutine_threadsafe(
        application.process_update(update),
        asyncio.get_event_loop()
    )
    try:
        future.result(timeout=10)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def home():
    return "Telegram Bot is running!"

if __name__ == '__main__':
    # Запускаем event loop в отдельном потоке
    thread = Thread(target=run_async_loop)
    thread.daemon = True
    thread.start()
    
    # Ждем инициализации бота (максимум 30 секунд)
    loop_ready.wait(timeout=30)
    
    # Запускаем сервер
    if "RENDER" in os.environ:
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=10000)
        except ImportError:
            app.run(host='0.0.0.0', port=10000)
    else:
        app.run(host='0.0.0.0', port=5000)

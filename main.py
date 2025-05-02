import os
import asyncio
from threading import Thread, Lock
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from flask import Flask, request, jsonify

app = Flask(__name__)

# Глобальные переменные
application = None
loop = None
loop_lock = Lock()

def run_async_loop():
    global loop, application
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def init_bot():
        global application
        application = ApplicationBuilder() \
            .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
            .build()
        
        async def handle_message(update: Update, context):
            await update.message.reply_text("✅ Бот работает!")
        
        application.add_handler(MessageHandler(filters.TEXT, handle_message))
        await application.initialize()
        
        if "RENDER" in os.environ:
            await application.bot.set_webhook(
                url=os.getenv("WEBHOOK_URL") + '/webhook'
            )
    
    loop.run_until_complete(init_bot())
    loop.run_forever()

@app.route('/webhook', methods=['POST'])
def webhook():
    if not application:
        return jsonify({"status": "error", "message": "Bot not initialized"}), 503
    
    with loop_lock:
        future = asyncio.run_coroutine_threadsafe(
            process_update(request.get_json()),
            loop
        )
        try:
            future.result(timeout=10)
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

async def process_update(json_data):
    update = Update.de_json(json_data, application.bot)
    await application.process_update(update)

if __name__ == '__main__':
    # Запускаем event loop в отдельном потоке
    thread = Thread(target=run_async_loop, daemon=True)
    thread.start()
    
    # Запускаем сервер
    if "RENDER" in os.environ:
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=10000)
        except ImportError:
            app.run(host='0.0.0.0', port=10000)
    else:
        app.run(host='0.0.0.0', port=5000)

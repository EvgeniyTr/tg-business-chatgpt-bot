import os
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from flask import Flask, request, jsonify

app = Flask(__name__)

class BotManager:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.application = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.initialized = threading.Event()

    def start(self):
        self.executor.submit(self._run_loop)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        
        async def init_bot():
            self.application = ApplicationBuilder() \
                .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
                .build()
            
            async def handle_message(update: Update, context):
                await update.message.reply_text("✅ Бот работает стабильно!")
            
            self.application.add_handler(MessageHandler(filters.TEXT, handle_message))
            await self.application.initialize()
            
            if "RENDER" in os.environ:
                await self.application.bot.set_webhook(
                    url=os.getenv("WEBHOOK_URL") + '/webhook'
                )
            
            self.initialized.set()
        
        self.loop.run_until_complete(init_bot())
        self.loop.run_forever()

    async def _process_update_async(self, json_data):
        update = Update.de_json(json_data, self.application.bot)
        await self.application.process_update(update)

    def process_update(self, json_data):
        if not self.initialized.wait(timeout=30):
            raise RuntimeError("Bot initialization timeout")
        
        future = asyncio.run_coroutine_threadsafe(
            self._process_update_async(json_data),
            self.loop
        )
        return future.result(timeout=10)

# Инициализация менеджера бота
bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        bot_manager.process_update(request.get_json())
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def home():
    return "Telegram Bot is running!"

if __name__ == '__main__':
    if "RENDER" in os.environ:
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=10000)
        except ImportError:
            app.run(host='0.0.0.0', port=10000)
    else:
        app.run(host='0.0.0.0', port=5000)

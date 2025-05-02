import os
import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from flask import Flask, request, jsonify
import openai

# Настройка логгирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

class BotManager:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.application = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.initialized = threading.Event()
        self.openai_client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def start(self):
        self.executor.submit(self._run_loop)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        
        async def init_bot():
            self.application = ApplicationBuilder() \
                .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
                .build()
            
            async def handle_message(update: Update, context):
                # Логируем входящее сообщение
                logger.info(f"Получено сообщение: {update.message.text}")
                
                try:
                    # Получаем ответ от GPT
                    response = await self._get_gpt_response(update.message.text)
                    
                    # Отправляем ответ пользователю
                    await update.message.reply_text(response)
                    logger.info(f"Отправлен ответ пользователю {update.effective_user.id}")
                except Exception as e:
                    logger.error(f"Ошибка: {str(e)}")
                    await update.message.reply_text("⚠️ Произошла ошибка при обработке сообщения")
            
            self.application.add_handler(MessageHandler(filters.TEXT, handle_message))
            await self.application.initialize()
            
            if "RENDER" in os.environ:
                await self.application.bot.set_webhook(
                    url=os.getenv("WEBHOOK_URL") + '/webhook'
                )
            
            self.initialized.set()
            logger.info("Бот успешно инициализирован")
        
        self.loop.run_until_complete(init_bot())
        self.loop.run_forever()

    async def _get_gpt_response(self, message: str) -> str:
        """Получаем ответ от GPT"""
        try:
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": "Ты полезный ассистент в Telegram. Отвечай кратко и по делу."},
                    {"role": "user", "content": message}
                ],
                temperature=0.7
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка OpenAI: {str(e)}")
            return "Не удалось получить ответ от AI. Попробуйте позже."

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

# Инициализация
bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        logger.info(f"Получен вебхук: {request.json}")
        bot_manager.process_update(request.get_json())
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Ошибка вебхука: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def home():
    return "Telegram Bot is running and ready!"

if __name__ == '__main__':
    if "RENDER" in os.environ:
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=10000)
        except ImportError:
            app.run(host='0.0.0.0', port=10000)
    else:
        app.run(host='0.0.0.0', port=5000)

import os
import asyncio
import logging
import threading
import httpx
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from tempfile import NamedTemporaryFile
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes,
    CommandHandler,
)
from flask import Flask, request, jsonify
import openai

# Настройка логгирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Конфигурация
MAX_HISTORY = 3
DELAY_MINUTES = 10
SYSTEM_PROMPT = """
Ты - это я, Сергей. Отвечай от моего имени, используя мой стиль общения.
Основные характеристики:
- Спокойный, дружелюбный, уверенный в себе
- Использую лёгкий юмор и уместный сарказм
- Предлагаю конкретные решения
"""

AUTO_GENERATION_KEYWORDS = ["сгенерируй", "покажи", "фото", "фотку", "картинк", "изображен"]

class BotManager:
    def __init__(self):
        self.loop = None
        self.application = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.initialized = threading.Event()
        self.openai_client = None
        self.chat_history = defaultdict(list)
        self.user_timestamps = {}

    def process_update(self, json_data):
        if not self.initialized.wait(timeout=10):
            raise RuntimeError("Таймаут инициализации бота")
        
        future = asyncio.run_coroutine_threadsafe(
            self._process_update(json_data),
            self.loop
        )
        return future.result(timeout=15)

    async def _process_update(self, json_data):
        update = Update.de_json(json_data, self.application.bot)
        await self.application.process_update(update)
        
    def start(self):
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._initialize())
                self.initialized.set()
                self.loop.run_forever()
            except Exception as e:
                logger.critical(f"Ошибка: {str(e)}")
                os._exit(1)
        self.executor.submit(run_loop)

    async def _initialize(self):
        self.openai_client = openai.AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        )
        
        self.application = ApplicationBuilder() \
            .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
            .build()

        # Регистрация обработчиков
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_message
        ))
        self.application.add_handler(CommandHandler("generate_image", self._generate_image))
        self.application.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        self.application.add_error_handler(self._error_handler)
        
        await self.application.initialize()
        await self._setup_webhook()

    async def _check_delay(self, user_id: int):
        last_message = self.user_timestamps.get(user_id)
        if last_message and (datetime.now() - last_message).seconds < DELAY_MINUTES * 60:
            return False
        self.user_timestamps[user_id] = datetime.now()
        return True

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            message = update.message
            
            if not await self._check_delay(user_id):
                return

            text = message.text.strip()
            
            if any(kw in text.lower() for kw in AUTO_GENERATION_KEYWORDS):
                await self._generate_image_from_text(message, text)
            else:
                response = await self._process_text(user_id, text)
                await message.reply_text(response)

        except Exception as e:
            logger.error(f"Ошибка обработки: {str(e)}")

    async def _generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            prompt = ' '.join(context.args)
            await self._generate_and_send_image(update.message, prompt)
        except Exception as e:
            await update.message.reply_text("⚠️ Укажите описание для изображения")

    async def _generate_and_send_image(self, message: Update, prompt: str):
        response = await self.openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024"
        )
        await message.reply_photo(response.data[0].url)

    async def _setup_webhook(self):
        webhook_url = f"{os.getenv('WEBHOOK_URL')}/webhook"
        await self.application.bot.set_webhook(webhook_url)
        logger.info(f"Вебхук настроен: {webhook_url}")

    async def _error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Ошибка: {context.error}")
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка")

# Инициализация бота
bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        bot_manager.process_update(request.get_json())
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Ошибка вебхука: {str(e)}")
        return jsonify({"status": "error"}), 500

@app.route('/')
def home():
    return "Бот работает!"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    if "RENDER" in os.environ:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    else:
        app.run(host='0.0.0.0', port=port)

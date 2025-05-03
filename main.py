import os
import asyncio
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes,
    CommandHandler,
    BusinessConnection,
    BusinessMessagesDeleted
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
SYSTEM_PROMPT = """
Ты - это я, {owner_name}. Отвечай от моего имени, используя мой стиль общения.
Основные характеристики:
- {owner_style}
- {owner_details}

Всегда придерживайся этих правил:
1. Отвечай только от моего лица
2. Сохраняй мой стиль общения
3. Будь естественным
"""

class BotManager:
    def __init__(self):
        self.loop = None
        self.application = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.initialized = threading.Event()
        self.openai_client = None
        self.chat_history = defaultdict(list)
        self.business_connections = set()

        self.owner_info = {
            "owner_name": "Сергей Кажарнович",
            "owner_style": "Спокойный, дружелюбный, уверенный в себе, использую лёгкий юмор и уместный сарказм, если нужно — могу быть прямым.",
            "owner_details": "Предпочитаю говорить по делу, но умею развить мысль. Ценю структурированные подходы, часто предлагаю решения и иду на шаг вперёд. Готов делиться опытом и вовлекать других в процесс, если вижу в этом смысл."
        }

    def start(self):
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._initialize_components())
                self.initialized.set()
                self.loop.run_forever()
            except Exception as e:
                logger.critical(f"Фатальная ошибка: {str(e)}", exc_info=True)
                os._exit(1)

        self.executor.submit(run_loop)

    async def _initialize_components(self):
        logger.info("Инициализация OpenAI...")
        self.openai_client = openai.AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=30.0
        )

        logger.info("Создание Telegram Application...")
        self.application = ApplicationBuilder() \
            .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
            .business(api_id=int(os.getenv("TELEGRAM_API_ID")),
                      api_hash=os.getenv("TELEGRAM_API_HASH")) \
            .build()

        logger.info("Регистрация обработчиков...")
        # Обработчик бизнес-сообщений
        self.application.add_handler(
            MessageHandler(
                filters.TEXT & filters.BusinessConnection(),
                self._handle_business_message
            )
        )

        # Обычные обработчики
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_message
        ))

        logger.info("Инициализация приложения...")
        await self.application.initialize()

        if "RENDER" in os.environ:
            await self._setup_webhook()

        logger.info("✅ Бот успешно инициализирован")

    async def _handle_business_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            business_connection = update.business_connection
            self.business_connections.add(business_connection.id)

            user_id = update.effective_user.id
            message = update.message
            text = message.text

            logger.info(f"Бизнес-сообщение от {user_id}: {text}")

            response = await self._get_gpt_response(user_id, text)
            await message.reply_text(
                response,
                business_connection_id=business_connection.id
            )

        except Exception as e:
            logger.error(f"Ошибка обработки бизнес-сообщения: {str(e)}", exc_info=True)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            message = update.message
            user_id = update.effective_user.id
            text = message.text

            logger.info(f"Сообщение от {user_id}: {text}")

            response = await self._get_gpt_response(user_id, text)
            await message.reply_text(response)

        except Exception as e:
            logger.error(f"Ошибка обработки: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Произошла ошибка")

    async def _get_gpt_response(self, user_id: int, message: str) -> str:
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT.format(**self.owner_info)},
                *self.chat_history[user_id][-MAX_HISTORY*2:],
                {"role": "user", "content": message}
            ]

            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=messages,
                temperature=0.7
            )

            response = completion.choices[0].message.content
            self._update_history(user_id, message, response)
            return response

        except Exception as e:
            logger.error(f"Ошибка OpenAI: {str(e)}")
            return "Извините, произошла ошибка"

    def _update_history(self, user_id: int, user_message: str, bot_response: str):
        self.chat_history[user_id].extend([
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": bot_response}
        ])
        if len(self.chat_history[user_id]) > MAX_HISTORY * 2:
            self.chat_history[user_id] = self.chat_history[user_id][-MAX_HISTORY*2:]

    async def _setup_webhook(self):
        webhook_url = f"{os.getenv('WEBHOOK_URL')}/webhook"
        await self.application.bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "business_message"],
            business_connection_id="all"
        )
        logger.info(f"Вебхук настроен: {webhook_url}")

    def process_update(self, json_data):
        if not self.initialized.wait(timeout=120):
            raise RuntimeError("Таймаут инициализации")

        future = asyncio.run_coroutine_threadsafe(
            self._process_update(json_data),
            self.loop
        )
        return future.result(timeout=15)

    async def _process_update(self, json_data):
        update = Update.de_json(json_data, self.application.bot)
        await self.application.process_update(update)

# Инициализация бота
bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        if not bot_manager.initialized.is_set():
            return jsonify({"status": "initializing"}), 503

        bot_manager.process_update(request.get_json())
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error"}), 500

@app.route('/')
def home():
    return "Telegram Bot is running!"

if __name__ == '__main__':
    if "RENDER" in os.environ:
        from waitress import serve
        serve(app, host="0.0.0.0", port=10000)
    else:
        app.run(host='0.0.0.0', port=5000)

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
        self.openai_client = None
        self.init_timeout = 120  # Увеличили таймаут до 2 минут
        self.start_time = None
        self.init_success = False

    async def _initialize_components(self):
        """Асинхронная инициализация всех компонентов"""
        try:
            # 1. Инициализация OpenAI
            self.openai_client = openai.AsyncOpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                timeout=30.0
            )
            
            # 2. Создание Telegram Application
            self.application = ApplicationBuilder() \
                .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
                .build()
            
            # 3. Регистрация обработчиков
            self.application.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._handle_message
            ))
            
            # 4. Инициализация приложения
            await self.application.initialize()
            
            # 5. Настройка вебхука (только на Render)
            if "RENDER" in os.environ:
                await self._setup_webhook()
            
            self.start_time = asyncio.get_event_loop().time()
            self.init_success = True
            logger.info("✅ Все компоненты успешно инициализированы")
            
        except Exception as e:
            logger.critical(f"Ошибка инициализации: {str(e)}", exc_info=True)
            raise

    def start(self):
        """Запуск бота в отдельном потоке"""
        def run_loop():
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._initialize_components())
                self.initialized.set()
                self.loop.run_forever()
            except Exception as e:
                logger.critical(f"Фатальная ошибка: {str(e)}", exc_info=True)
                os._exit(1)  # Аварийное завершение при критической ошибке

        self.executor.submit(run_loop)

    async def _handle_message(self, update: Update, context):
        """Обработчик входящих сообщений"""
        try:
            message = update.message or update.business_message
            user_id = message.from_user.id
            text = message.text
            
            logger.info(f"Сообщение от {user_id}: {text}")
            
            response = await self._get_gpt_response(user_id, text)
            await message.reply_text(response)
            
        except Exception as e:
            logger.error(f"Ошибка обработки: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Произошла ошибка, попробуйте позже")

    async def _get_gpt_response(self, user_id: int, message: str) -> str:
        """Запрос к OpenAI с обработкой ошибок"""
        try:
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу."},
                    {"role": "user", "content": message}
                ],
                temperature=0.7
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка OpenAI: {str(e)}")
            return "Извините, не могу обработать запрос. Попробуйте позже."

    async def _setup_webhook(self):
        """Настройка вебхука для бизнес-аккаунта"""
        webhook_url = os.getenv("WEBHOOK_URL") + '/webhook'
        await self.application.bot.set_webhook(
            url=webhook_url,
            max_connections=50,
            allowed_updates=["message", "business_message"],
            drop_pending_updates=True
        )
        logger.info(f"Вебхук настроен: {webhook_url}")

    def process_update(self, json_data):
        """Потокобезопасная обработка обновления"""
        if not self.initialized.wait(timeout=self.init_timeout):
            raise RuntimeError(f"Таймаут инициализации ({self.init_timeout} сек)")
        
        future = asyncio.run_coroutine_threadsafe(
            self._process_update(json_data),
            self.loop
        )
        return future.result(timeout=15)

    async def _process_update(self, json_data):
        """Асинхронная обработка обновления"""
        update = Update.de_json(json_data, self.application.bot)
        await self.application.process_update(update)

# Инициализация бота при старте приложения
bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработчик вебхука Telegram"""
    try:
        if not bot_manager.initialized.is_set():
            return jsonify({"status": "error", "message": "Bot is initializing"}), 503
            
        bot_manager.process_update(request.get_json())
        return jsonify({"status": "ok"})
        
    except Exception as e:
        logger.error(f"Ошибка вебхука: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def home():
    """Статусная страница"""
    status = "running" if bot_manager.initialized.is_set() else "initializing"
    return f"Telegram Bot Status: {status}"

if __name__ == '__main__':
    if "RENDER" in os.environ:
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=10000)
        except ImportError:
            app.run(host='0.0.0.0', port=10000)
    else:
        app.run(host='0.0.0.0', port=5000)

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
        self.openai_client = None  # Инициализируем в event loop
        self.lock = threading.Lock()
        self.start_time = None

    def start(self):
        self.executor.submit(self._run_loop)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        
        async def init_bot():
            # Инициализация OpenAI внутри event loop
            self.openai_client = openai.AsyncOpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                timeout=10.0
            )
            
            self.application = ApplicationBuilder() \
                .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
                .post_init(self._post_init) \
                .build()
            
            async def handle_message(update: Update, context):
                with self.lock:
                    logger.info(f"Получено сообщение от {update.effective_user.id}: {update.message.text}")
                    
                    try:
                        # Логирование контекста для отладки
                        logger.debug(f"Context data: {context.bot_data}")
                        
                        response = await self._get_gpt_response(
                            user_id=update.effective_user.id,
                            message=update.message.text
                        )
                        
                        await update.message.reply_text(response)
                        logger.info(f"Ответ отправлен пользователю {update.effective_user.id}")
                    except Exception as e:
                        logger.error(f"Ошибка обработки: {str(e)}", exc_info=True)
                        await update.message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")
            
            self.application.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                handle_message
            ))
            
            await self.application.initialize()
            await self._setup_webhook()
            self.initialized.set()
            logger.info("Бот успешно инициализирован")

        async def _setup_webhook(self):
            if "RENDER" in os.environ:
                webhook_url = os.getenv("WEBHOOK_URL") + '/webhook'
                await self.application.bot.set_webhook(
                    url=webhook_url,
                    max_connections=40,
                    allowed_updates=["message"]
                )
                logger.info(f"Вебхук установлен: {webhook_url}")

        try:
            self.loop.run_until_complete(init_bot())
            self.loop.run_forever()
        except Exception as e:
            logger.critical(f"Фатальная ошибка в event loop: {str(e)}", exc_info=True)
            raise

    async def _post_init(self, app):
            """Дополнительная инициализация после создания приложения"""
            self.start_time = asyncio.get_event_loop().time()
            app.bot_data["start_time"] = self.start_time
            logger.info("Post-init завершен")
    async def _get_gpt_response(self, user_id: int, message: str) -> str:
        """Получаем ответ от GPT с учетом истории"""
        try:
            # Здесь можно добавить логику работы с историей сообщений
            prompt = [
                {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу."},
                {"role": "user", "content": message}
            ]
            
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=prompt,
                temperature=0.7
            )
            
            response = completion.choices[0].message.content
            logger.debug(f"Ответ GPT для {user_id}: {response}")
            return response
        except Exception as e:
            logger.error(f"Ошибка OpenAI для {user_id}: {str(e)}")
            return "Не удалось получить ответ. Пожалуйста, попробуйте позже."

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

# Инициализация бота
bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        logger.info(f"Получен вебхук: {request.json}")
        bot_manager.process_update(request.get_json())
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Ошибка вебхука: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def home():
    uptime = "неизвестно"
    if bot_manager.start_time:
        uptime = f"{asyncio.get_event_loop().time() - bot_manager.start_time:.2f} сек"
    return f"Telegram Bot is running! Uptime: {uptime}"

async def test_bot():
    """Тестовая функция для проверки работы бота"""
    logger.info("Запуск тестовой функции...")
    
    test_manager = BotManager()
    test_manager.start()
    
    # Ждем инициализации
    await asyncio.sleep(5)
    
    if not test_manager.initialized.is_set():
        logger.error("Бот не инициализировался за отведенное время")
        return
    
    # Тестовое сообщение
    test_msg = "Привет! Как дела?"
    try:
        response = await test_manager._get_gpt_response(
            user_id=12345,
            message=test_msg
        )
        logger.info(f"Тестовый запрос: '{test_msg}'")
        logger.info(f"Тестовый ответ: '{response}'")
    except Exception as e:
        logger.error(f"Ошибка теста: {str(e)}", exc_info=True)

if __name__ == '__main__':
    # Для тестирования (раскомментируйте при необходимости)
    # asyncio.run(test_bot())
    
    # Основной запуск
    if "RENDER" in os.environ:
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=10000)
        except ImportError:
            app.run(host='0.0.0.0', port=10000)
    else:
        app.run(host='0.0.0.0', port=5000)

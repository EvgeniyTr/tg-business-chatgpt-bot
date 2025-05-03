import os
import asyncio
import logging
import threading
import httpx
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from tempfile import NamedTemporaryFile
from datetime import datetime
import pytz

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
AUTO_GENERATION_KEYWORDS = ["сгенерируй", "покажи", "фото", "фотку", "картинк", "изображен"]

class BotManager:
    def __init__(self):
        self.loop = None
        self.application = None
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.initialized = threading.Event()
        self.openai_client = None
        self.chat_history = defaultdict(list)
        
        self.owner_info = {
            "owner_name": "Сергей",
            "owner_style": "Спокойный, дружелюбный, уверенный в себе, использую лёгкий юмор и уместный сарказм, если нужно — могу быть прямым.",
            "owner_details": "Предпочитаю говорить по делу, но умею развить мысль. Ценю структурированные подходы, часто предлагаю решения и иду на шаг вперёд. Готов делиться опытом и вовлекать других в процесс, если вижу в этом смысл."
        }

    def process_update(self, json_data):
        """Обработка входящего обновления через вебхук"""
        try:
            if not self.initialized.wait(timeout=15):
                raise RuntimeError("Таймаут инициализации бота")
            
            future = asyncio.run_coroutine_threadsafe(
                self._process_update(json_data),
                self.loop
            )
            return future.result(timeout=30)
        except Exception as e:
            logger.error(f"Ошибка обработки обновления: {str(e)}", exc_info=True)
            raise

    async def _process_update(self, json_data):
        """Асинхронная обработка обновления"""
        try:
            update = Update.de_json(json_data, self.application.bot)
            await self.application.process_update(update)
        except Exception as e:
            logger.error(f"Ошибка обработки обновления: {str(e)}", exc_info=True)
            raise
        
    def start(self):
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._initialize())
                self.initialized.set()
                self.loop.run_forever()
            except Exception as e:
                logger.critical(f"Ошибка инициализации: {str(e)}", exc_info=True)
                os._exit(1)
        self.executor.submit(run_loop)

    async def _initialize(self):
        """Инициализация компонентов бота"""
        try:
            # Инициализация OpenAI
            self.openai_client = openai.AsyncOpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                timeout=30.0
            )
            
            # Инициализация Telegram бота
            self.application = ApplicationBuilder() \
                .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
                .build()

            # Регистрация обработчиков
            self.application.add_handler(CommandHandler("start", self._start_command))
            self.application.add_handler(CommandHandler("generate_image", self._generate_image))
            self.application.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._handle_text_message
            ))
            self.application.add_handler(MessageHandler(
                filters.VOICE,
                self._handle_voice_message
            ))
            
            self.application.add_error_handler(self._error_handler)
            
            await self.application.initialize()
            
            if "RENDER" in os.environ:
                await self._setup_webhook()

            logger.info("Бот успешно инициализирован")

        except Exception as e:
            logger.critical(f"Критическая ошибка инициализации: {str(e)}", exc_info=True)
            raise

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        try:
            user = update.effective_user
            logger.info(f"Команда /start от пользователя {user.id}")
            await update.message.reply_text(
                f"🤖 Привет, {user.first_name}! Я твой персональный AI-ассистент.\n"
                "Отправь мне сообщение, и я отвечу как ты!\n"
                "Также могу генерировать изображения по ключевым словам."
            )
        except Exception as e:
            logger.error(f"Ошибка обработки /start: {str(e)}", exc_info=True)

    async def _handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений"""
        try:
            user = update.effective_user
            message = update.message
            logger.info(f"Текстовое сообщение от {user.id}: {message.text}")

            text = message.text.strip()
            
            if any(kw in text.lower() for kw in AUTO_GENERATION_KEYWORDS):
                await self._generate_image_from_text(message, text)
            else:
                response = await self._process_text(user.id, text)
                await message.reply_text(response)

        except Exception as e:
            logger.error(f"Ошибка обработки текста: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Произошла ошибка при обработке запроса")

    async def _generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /generate_image"""
        try:
            user = update.effective_user
            prompt = ' '.join(context.args)
            logger.info(f"Запрос генерации изображения от {user.id}: {prompt}")
            
            if not prompt:
                raise ValueError("Пустой промпт")
                
            await self._generate_and_send_image(update.message, prompt)
        except Exception as e:
            logger.error(f"Ошибка генерации изображения: {str(e)}", exc_info=True)
            await update.message.reply_text("⚠️ Укажите описание для изображения через пробел после команды")

    async def _generate_image_from_text(self, message: Update, text: str):
        """Генерация изображения из текста"""
        try:
            prompt = await self._create_image_prompt(text)
            await self._generate_and_send_image(message, prompt)
        except Exception as e:
            logger.error(f"Ошибка генерации: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Ошибка генерации изображения")

    async def _generate_and_send_image(self, message: Update, prompt: str):
        """Отправка сгенерированного изображения"""
        try:
            logger.info(f"Генерация изображения: {prompt}")
            response = await self.openai_client.images.generate(
                model="dall-e-3",
                prompt=prompt[:1000],
                size="1024x1024",
                quality="standard"
            )
            await message.reply_photo(response.data[0].url)
            logger.info("Изображение успешно отправлено")
        except Exception as e:
            logger.error(f"Ошибка отправки изображения: {str(e)}")
            raise

    async def _create_image_prompt(self, text: str) -> str:
        """Создание промпта для DALL-E через GPT"""
        try:
            messages = [{
                "role": "system", 
                "content": "Сгенерируй детальное англоязычное описание для DALL-E на основе запроса пользователя"
            }, {
                "role": "user", 
                "content": text
            }]
            
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка создания промпта: {str(e)}")
            return text

    async def _handle_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка голосовых сообщений"""
        try:
            user = update.effective_user
            message = update.message
            logger.info(f"Голосовое сообщение от {user.id}")

            voice_file = await message.voice.get_file()
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(voice_file.file_path)
                with NamedTemporaryFile(delete=True, suffix=".ogg") as temp_file:
                    temp_file.write(response.content)
                    temp_file.seek(0)
                    
                    transcript = await self.openai_client.audio.transcriptions.create(
                        file=temp_file,
                        model="whisper-1",
                        response_format="text"
                    )
                    
                    logger.info(f"Распознанный текст: {transcript}")
                    
                    if any(kw in transcript.lower() for kw in AUTO_GENERATION_KEYWORDS):
                        await self._generate_image_from_text(message, transcript)
                    else:
                        response = await self._process_text(user.id, transcript)
                        await message.reply_text(f"🎤 Распознано: {transcript}\n\n📝 Ответ: {response}")

        except Exception as e:
            logger.error(f"Ошибка обработки голоса: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Ошибка обработки голосового сообщения")

    async def _process_text(self, user_id: int, text: str) -> str:
        """Обработка текста через GPT с историей"""
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT.format(**self.owner_info)},
                *self.chat_history[user_id][-MAX_HISTORY*2:],
                {"role": "user", "content": text}
            ]
            
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=messages,
                temperature=0.7,
                max_tokens=1000
            )
            
            response = completion.choices[0].message.content
            self._update_history(user_id, text, response)
            return response
        except Exception as e:
            logger.error(f"Ошибка обработки текста: {str(e)}", exc_info=True)
            return "⚠️ Ошибка генерации ответа"

    def _update_history(self, user_id: int, text: str, response: str):
        """Обновление истории чата"""
        self.chat_history[user_id].extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": response}
        ])
        if len(self.chat_history[user_id]) > MAX_HISTORY * 2:
            self.chat_history[user_id] = self.chat_history[user_id][-MAX_HISTORY*2:]

    async def _setup_webhook(self):
        """Настройка вебхука"""
        try:
            webhook_url = f"{os.getenv('WEBHOOK_URL')}/webhook"
            await self.application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES
            )
            logger.info(f"Вебхук успешно настроен: {webhook_url}")
        except Exception as e:
            logger.critical(f"Ошибка настройки вебхука: {str(e)}", exc_info=True)
            raise

    async def _error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Глобальный обработчик ошибок"""
        logger.error(f"Необработанная ошибка: {str(context.error)}", exc_info=True)
        if update and update.message:
            try:
                await update.message.reply_text("⚠️ Произошла внутренняя ошибка")
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения об ошибке: {str(e)}")

# Инициализация бота
bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        logger.info(f"Получено обновление: {data}")
        bot_manager.process_update(data)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Ошибка вебхука: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/status')
def status():
    return jsonify({
        "status": "ok",
        "initialized": bot_manager.initialized.is_set(),
        "webhook_configured": bool(bot_manager.application and bot_manager.application.bot.get_webhook_info().url)
    })

@app.route('/')
def home():
    return "Telegram Bot is running!"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    if "RENDER" in os.environ:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    else:
        app.run(host='0.0.0.0', port=port, use_reloader=False)

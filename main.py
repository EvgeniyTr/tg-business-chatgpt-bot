import os
import asyncio
import logging
import threading
import httpx
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from tempfile import NamedTemporaryFile
from datetime import datetime, timezone

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
RESPONSE_DELAY_SECONDS = 10  # Задержка ответа в секундах

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
                (filters.TEXT & ~filters.COMMAND) | (filters.TEXT & filters.UpdateType.BUSINESS_MESSAGE),
                self._handle_message
            ))
            self.application.add_handler(MessageHandler(
                filters.VOICE | (filters.VOICE & filters.UpdateType.BUSINESS_MESSAGE),
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
            logger.info(f"Команда /start от пользователя {user.id} ({user.username or user.first_name})")
            await (update.message or update.business_message).reply_text(
                f"🤖 Привет, {user.first_name}! Я твой персональный AI-ассистент.\n"
                "Я буду отвечать на сообщения от твоего имени, если ты не успеешь.\n"
                "Также могу генерировать изображения по ключевым словам."
            )
            logger.info(f"Ответ на /start отправлен в чат {user.id}")
        except Exception as e:
            logger.error(f"Ошибка обработки /start: {str(e)}", exc_info=True)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений в бизнес-чатах и личных чатах"""
        try:
            # Проверяем, является ли обновление бизнес-сообщением
            if update.business_message:
                message = update.business_message
                is_business = True
            else:
                message = update.message
                is_business = False

            user = update.effective_user
            chat_id = message.chat_id
            message_time = message.date
            text = message.text.strip()

            logger.info(
                f"{'Бизнес-сообщение' if is_business else 'Сообщение'} в чате {chat_id} "
                f"от {user.id} ({user.username or user.first_name}): {text} "
                f"(время: {message_time})"
            )

            # Запускаем асинхронную задачу с задержкой
            asyncio.create_task(self._delayed_message_processing(message, text, chat_id, is_business))
            logger.info(f"Запущена задача обработки сообщения с задержкой 10 секунд для чата {chat_id}")

        except Exception as e:
            logger.error(f"Ошибка обработки {'бизнес-сообщения' if is_business else 'сообщения'}: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Произошла ошибка при обработке запроса")

    async def _delayed_message_processing(self, message, text: str, chat_id: int, is_business: bool):
        """Обработка сообщения с задержкой 10 секунд"""
        try:
            # Ждем 10 секунд
            await asyncio.sleep(RESPONSE_DELAY_SECONDS)

            logger.info(
                f"Обработка {'бизнес-сообщения' if is_business else 'сообщения'} после задержки "
                f"для чата {chat_id}: {text}"
            )

            if any(kw in text.lower() for kw in AUTO_GENERATION_KEYWORDS):
                await self._generate_image_from_text(message, text)
            else:
                response = await self._process_text(chat_id, text)
                await message.reply_text(response)
                logger.info(f"Ответ отправлен в чат {chat_id}: {response}")

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения после задержки для чата {chat_id}: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Произошла ошибка при обработке запроса")

    async def _generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /generate_image"""
        try:
            user = update.effective_user
            prompt = ' '.join(context.args)
            logger.info(f"Запрос генерации изображения от {user.id} ({user.username or user.first_name}): {prompt}")
            
            if not prompt:
                raise ValueError("Пустой промпт")
                
            await self._generate_and_send_image(update.message or update.business_message, prompt)
            logger.info(f"Изображение отправлено в чат {user.id}")
        except Exception as e:
            logger.error(f"Ошибка генерации изображения: {str(e)}", exc_info=True)
            await (update.message or update.business_message).reply_text(
                "⚠️ Укажите описание для изображения через пробел после команды"
            )

    async def _generate_image_from_text(self, message: Update, text: str):
        """Генерация изображения из текста"""
        try:
            prompt = await self._create_image_prompt(text)
            await self._generate_and_send_image(message, prompt)
        except Exception as e:
            logger.error(f"Ошибка генерации изображения: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Ошибка генерации изображения")

    async def _generate_and_send_image(self, message: Update, prompt: str):
        """Отправка сгенерированного изображения"""
        try:
            logger.info(f"Генерация изображения с промптом: {prompt}")
            response = await self.openai_client.images.generate(
                model="dall-e-3",
                prompt=prompt[:1000],
                size="1024x1024",
                quality="standard"
            )
            await message.reply_photo(response.data[0].url)
            logger.info(f"Изображение успешно отправлено в чат {message.chat_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки изображения: {str(e)}")
            raise

    async def _create_image_prompt(self, text: str) -> str:
        """Создание промпта для DALL-E через GPT"""
        try:
            logger.info(f"Создание промпта для DALL-E с текстом: {text}")
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
            prompt = completion.choices[0].message.content
            logger.info(f"Промпт для DALL-E создан: {prompt}")
            return prompt
        except Exception as e:
            logger.error(f"Ошибка создания промпта для DALL-E: {str(e)}")
            return text

    async def _handle_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка голосовых сообщений"""
        try:
            # Проверяем, является ли обновление бизнес-сообщением
            if update.business_message:
                message = update.business_message
                is_business = True
            else:
                message = update.message
                is_business = False

            user = update.effective_user
            chat_id = message.chat_id
            message_time = message.date

            logger.info(
                f"{'Бизнес-голосовое сообщение' if is_business else 'Голосовое сообщение'} "
                f"в чате {chat_id} от {user.id} ({user.username or user.first_name}) "
                f"(время: {message_time})"
            )

            # Запускаем асинхронную задачу с задержкой
            asyncio.create_task(self._delayed_voice_processing(message, chat_id, is_business))
            logger.info(f"Запущена задача обработки голосового сообщения с задержкой 10 секунд для чата {chat_id}")

        except Exception as e:
            logger.error(f"Ошибка обработки {'бизнес-голосового сообщения' if is_business else 'голосового сообщения'}: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Ошибка обработки голосового сообщения")

    async def _delayed_voice_processing(self, message, chat_id: int, is_business: bool):
        """Обработка голосового сообщения с задержкой 10 секунд"""
        try:
            # Ждем 10 секунд
            await asyncio.sleep(RESPONSE_DELAY_SECONDS)

            logger.info(
                f"Обработка {'бизнес-голосового сообщения' if is_business else 'голосового сообщения'} "
                f"после задержки для чата {chat_id}"
            )

            voice_file = await message.voice.get_file()
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(voice_file.file_path)
                with NamedTemporaryFile(delete=True, suffix=".ogg") as temp_file:
                    temp_file.write(response.content)
                    temp_file.seek(0)
                    
                    logger.info(f"Отправка голосового сообщения в Whisper для транскрипции (чат: {chat_id})")
                    transcript = await self.openai_client.audio.transcriptions.create(
                        file=temp_file,
                        model="whisper-1",
                        response_format="text"
                    )
                    
                    logger.info(f"Распознанный текст в чате {chat_id}: {transcript}")
                    
                    if any(kw in transcript.lower() for kw in AUTO_GENERATION_KEYWORDS):
                        await self._generate_image_from_text(message, transcript)
                    else:
                        response = await self._process_text(chat_id, transcript)
                        await message.reply_text(f"🎤 Распознано: {transcript}\n\n📝 Ответ: {response}")
                        logger.info(f"Ответ на голосовое сообщение отправлен в чат {chat_id}: {response}")

        except Exception as e:
            logger.error(f"Ошибка обработки голосового сообщения после задержки для чата {chat_id}: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Ошибка обработки голосового сообщения")

    async def _process_text(self, chat_id: int, text: str) -> str:
        """Обработка текста через GPT с историей"""
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT.format(**self.owner_info)},
                *self.chat_history[chat_id][-MAX_HISTORY*2:],
                {"role": "user", "content": text}
            ]
            
            logger.info(
                f"Отправка запроса в OpenAI (модель: gpt-4-turbo-preview, чат: {chat_id}): {text}"
            )
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=messages,
                temperature=0.7,
                max_tokens=1000
            )
            
            response = completion.choices[0].message.content
            logger.info(f"Получен ответ от OpenAI для чата {chat_id}: {response}")
            self._update_history(chat_id, text, response)
            return response
        except Exception as e:
            logger.error(f"Ошибка обработки текста через OpenAI для чата {chat_id}: {str(e)}", exc_info=True)
            return "⚠️ Ошибка генерации ответа"

    def _update_history(self, chat_id: int, text: str, response: str):
        """Обновление истории чата"""
        self.chat_history[chat_id].extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": response}
        ])
        if len(self.chat_history[chat_id]) > MAX_HISTORY * 2:
            self.chat_history[chat_id] = self.chat_history[chat_id][-MAX_HISTORY*2:]

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
        if update and (update.message or update.business_message):
            try:
                message = update.business_message or update.message
                await message.reply_text("⚠️ Произошла внутренняя ошибка")
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

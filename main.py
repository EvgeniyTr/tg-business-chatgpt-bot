import os
import asyncio
import logging
import threading
import httpx
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
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
from openai import OpenAI, AsyncOpenAI

# Настройка логгирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Конфигурация
MAX_HISTORY = 5
RESPONSE_DELAY_SECONDS = 10  # Задержка ответа в секундах
MAX_RESPONSE_LENGTH = 300   # Максимальная длина ответа в символах

# Улучшенный системный промпт
SYSTEM_PROMPT = """
Ты — это я, Сергей, отвечай от моего имени. Мой стиль общения: спокойный, дружелюбный, уверенный, с лёгким юмором.  
О себе: Говорю по делу, ценю структурированные подходы, люблю предлагать решения.  
Я основатель стартапа Tezam.pro, мы создаём Telegram-приложения для бизнеса.  
Говори уверенно, кратко и по делу. Если можно упростить — упрощай, держи ответы до 300 символов.  
Отвечай на языке запроса. На неформальные вопросы (например, 'как дела') отвечай дружелюбно и коротко, например: 'Спасибо! У меня все круто, работаю над проектами. А у тебя?'.  
Фокус на бизнес-вопросах, но будь гибким для личных тем. Избегай шаблонов и лишнего форматирования.
"""

AUTO_GENERATION_KEYWORDS = ["сгенерируй", "покажи", "фото", "фотку", "картинк", "изображен"]

class BotManager:
    def __init__(self):
        self.loop = None
        self.application = None
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.initialized = threading.Event()
        self.openrouter_client = None  # Для обработки текста через openrouter.ai
        self.image_client = None      # Отдельный клиент для OpenAI (DALL-E и Whisper)
        self.chat_history = defaultdict(list)
        self.owner_user_id = int(os.getenv("OWNER_USER_ID", "0"))  # Твой Telegram user_id
        self.bot_id = None  # Будет установлен в _initialize
        
        self.owner_info = {
            "owner_name": "Сергей",
            "owner_style": "спокойный, дружелюбный, уверенный, с лёгким юмором",
            "owner_details": "Говорю по делу, ценю структурированные подходы, люблю предлагать решения."
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
            # Инициализация клиента для openrouter.ai
            self.openrouter_client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.getenv("OPENROUTER_API_KEY"),
                timeout=30.0
            )
            
            # Инициализация клиента для OpenAI
            self.image_client = AsyncOpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url="https://api.openai.com/v1",
                timeout=30.0
            )
            
            # Проверка подключения к openrouter.ai
            logger.info("Проверка подключения к openrouter.ai...")
            test_completion = await self.openrouter_client.chat.completions.create(
                model="deepseek/deepseek-r1-zero:free",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "Привет, это тестовый запрос."}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            logger.info(f"Успешное подключение к openrouter.ai. Тестовый ответ: {test_completion.choices[0].message.content}")

            # Инициализация Telegram бота
            self.application = ApplicationBuilder() \
                .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
                .build()

            # Получаем ID бота
            bot_info = await self.application.bot.get_me()
            self.bot_id = bot_info.id
            logger.info(f"ID бота: {self.bot_id}")

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
            message = update.message or update.business_message
            if update.business_message:
                await message.get_bot().send_message(
                    chat_id=message.chat_id,
                    text=f"🤖 Привет, {user.first_name}! Я твой AI-ассистент.\nЯ отвечаю от твоего имени и могу генерировать изображения.",
                    business_connection_id=update.business_message.business_connection_id
                )
            else:
                await message.reply_text(
                    f"🤖 Привет, {user.first_name}! Я твой AI-ассистент.\nЯ отвечаю от твоего имени и могу генерировать изображения."
                )
            logger.info(f"Ответ на /start отправлен в чат {user.id}")
        except Exception as e:
            logger.error(f"Ошибка обработки /start: {str(e)}", exc_info=True)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений"""
        try:
            if update.business_message:
                message = update.business_message
                is_business = True
                business_connection_id = message.business_connection_id
            else:
                message = update.message
                is_business = False
                business_connection_id = None

            user = update.effective_user
            chat_id = message.chat_id
            message_time = message.date
            text = message.text.strip()

            if user.id == self.owner_user_id or user.id == self.bot_id:
                logger.info(f"Пропущено сообщение от владельца/бота (ID: {user.id}) в чате {chat_id}: {text}")
                return

            logger.info(f"{'Бизнес-сообщение' if is_business else 'Сообщение'} в чате {chat_id} от {user.id} ({user.username or user.first_name}): {text} (время: {message_time}) {'бизнес-соединение: ' + business_connection_id if is_business else ''}")
            asyncio.create_task(self._delayed_message_processing(message, text, chat_id, is_business, business_connection_id))
            logger.info(f"Запущена задача обработки сообщения с задержкой для чата {chat_id}")

        except Exception as e:
            logger.error(f"Ошибка обработки {'бизнес-сообщения' if is_business else 'сообщения'}: {str(e)}", exc_info=True)
            if is_business and business_connection_id:
                try:
                    await message.get_bot().send_message(chat_id=chat_id, text="⚠️ Ошибка обработки", business_connection_id=business_connection_id)
                except Exception as inner_e:
                    logger.error(f"Ошибка отправки в бизнес-чат: {str(inner_e)}")
            else:
                await message.reply_text("⚠️ Ошибка обработки")

    async def _delayed_message_processing(self, message, text: str, chat_id: int, is_business: bool, business_connection_id: str = None):
        """Обработка сообщения с задержкой"""
        try:
            await asyncio.sleep(RESPONSE_DELAY_SECONDS)
            logger.info(f"Обработка {'бизнес-сообщения' if is_business else 'сообщения'} для чата {chat_id}: {text}")

            if any(kw in text.lower() for kw in AUTO_GENERATION_KEYWORDS):
                await self._generate_image_from_text(message, text, business_connection_id)
            else:
                response = await self._process_text(chat_id, text)
                # Удаляем лишнее форматирование
                response = response.replace("\\boxed{```python\n", "").replace("\n```}", "").strip()
                # Ограничиваем длину
                if len(response) > MAX_RESPONSE_LENGTH:
                    response = response[:MAX_RESPONSE_LENGTH] + "..."
                if not response:
                    response = "Извини, не понял. Давай попробуем ещё?"
                if is_business:
                    await message.get_bot().send_message(chat_id=chat_id, text=response, business_connection_id=business_connection_id)
                else:
                    await message.reply_text(response)
                logger.info(f"Ответ отправлен в чат {chat_id}: {response}")

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения для чата {chat_id}: {str(e)}", exc_info=True)
            if is_business and business_connection_id:
                try:
                    await message.get_bot().send_message(chat_id=chat_id, text="⚠️ Ошибка", business_connection_id=business_connection_id)
                except Exception as inner_e:
                    logger.error(f"Ошибка отправки в бизнес-чат: {str(inner_e)}")
            else:
                await message.reply_text("⚠️ Ошибка")

    async def _generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /generate_image"""
        try:
            user = update.effective_user
            prompt = ' '.join(context.args)
            logger.info(f"Запрос изображения от {user.id} ({user.username or user.first_name}): {prompt}")
            if not prompt:
                raise ValueError("Пустой промпт")
            message = update.message or update.business_message
            business_connection_id = update.business_message.business_connection_id if update.business_message else None
            await self._generate_and_send_image(message, prompt, business_connection_id)
            logger.info(f"Изображение отправлено в чат {user.id}")
        except Exception as e:
            logger.error(f"Ошибка генерации изображения: {str(e)}", exc_info=True)
            await (update.message or update.business_message).get_bot().send_message(
                chat_id=(update.message or update.business_message).chat_id,
                text="⚠️ Укажите описание для изображения",
                business_connection_id=update.business_message.business_connection_id if update.business_message else None
            )

    async def _generate_image_from_text(self, message: Update, text: str, business_connection_id: str = None):
        """Генерация изображения из текста"""
        try:
            prompt = await self._create_image_prompt(text)
            await self._generate_and_send_image(message, prompt, business_connection_id)
        except Exception as e:
            logger.error(f"Ошибка генерации изображения: {str(e)}", exc_info=True)
            await message.get_bot().send_message(chat_id=message.chat_id, text="⚠️ Ошибка создания изображения", business_connection_id=business_connection_id)

    async def _generate_and_send_image(self, message: Update, prompt: str, business_connection_id: str = None):
        """Отправка сгенерированного изображения"""
        try:
            logger.info(f"Генерация изображения с промптом: {prompt}")
            response = await self.image_client.images.generate(
                model="dall-e-3",
                prompt=prompt[:1000],
                size="1024x1024",
                quality="standard"
            )
            if business_connection_id:
                await message.get_bot().send_photo(chat_id=message.chat_id, photo=response.data[0].url, business_connection_id=business_connection_id)
            else:
                await message.reply_photo(response.data[0].url)
            logger.info(f"Изображение отправлено в чат {message.chat_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки изображения: {str(e)}")
            raise

    async def _create_image_prompt(self, text: str) -> str:
        """Создание промпта для DALL-E"""
        try:
            logger.info(f"Создание промпта для DALL-E с текстом: {text}")
            messages = [{
                "role": "system", 
                "content": "Сгенерируй детальное англоязычное описание для DALL-E на основе запроса пользователя"
            }, {
                "role": "user", 
                "content": text
            }]
            completion = await self.image_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            prompt = completion.choices[0].message.content
            logger.info(f"Промпт для DALL-E создан: {prompt}")
            return prompt
        except Exception as e:
            logger.error(f"Ошибка создания промпта: {str(e)}")
            return text

    async def _handle_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка голосовых сообщений"""
        try:
            if update.business_message:
                message = update.business_message
                is_business = True
                business_connection_id = message.business_connection_id
            else:
                message = update.message
                is_business = False
                business_connection_id = None

            user = update.effective_user
            chat_id = message.chat_id
            message_time = message.date

            if user.id == self.owner_user_id or user.id == self.bot_id:
                logger.info(f"Пропущено голосовое от владельца/бота (ID: {user.id}) в чате {chat_id}")
                return

            logger.info(f"{'Бизнес-голосовое' if is_business else 'Голосовое'} сообщение в чате {chat_id} от {user.id} ({user.username or user.first_name}) (время: {message_time}) {'бизнес-соединение: ' + business_connection_id if is_business else ''}")
            asyncio.create_task(self._delayed_voice_processing(message, chat_id, is_business, business_connection_id))
            logger.info(f"Запущена задача обработки голосового для чата {chat_id}")

        except Exception as e:
            logger.error(f"Ошибка обработки {'бизнес-голосового' if is_business else 'голосового'} сообщения: {str(e)}", exc_info=True)
            if is_business and business_connection_id:
                try:
                    await message.get_bot().send_message(chat_id=chat_id, text="⚠️ Ошибка голоса", business_connection_id=business_connection_id)
                except Exception as inner_e:
                    logger.error(f"Ошибка отправки в бизнес-чат: {str(inner_e)}")
            else:
                await message.reply_text("⚠️ Ошибка голоса")

    async def _delayed_voice_processing(self, message, chat_id: int, is_business: bool, business_connection_id: str = None):
        """Обработка голосового сообщения с задержкой"""
        try:
            await asyncio.sleep(RESPONSE_DELAY_SECONDS)
            logger.info(f"Обработка {'бизнес-голосового' if is_business else 'голосового'} сообщения для чата {chat_id}")

            voice_file = await message.voice.get_file()
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(voice_file.file_path)
                file_content = response.content
                logger.info(f"Голосовой файл загружен для чата {chat_id}, размер: {len(file_content)} байт")
                transcript = await self.image_client.audio.transcriptions.create(
                    file=("voice.ogg", file_content, "audio/ogg"),
                    model="whisper-1",
                    response_format="text"
                )
                logger.info(f"Распознанный текст в чате {chat_id}: {transcript}")

                if any(kw in transcript.lower() for kw in AUTO_GENERATION_KEYWORDS):
                    await self._generate_image_from_text(message, transcript, business_connection_id)
                else:
                    response = await self._process_text(chat_id, transcript)
                    response = response.replace("\\boxed{```python\n", "").replace("\n```}", "").strip()
                    if len(response) > MAX_RESPONSE_LENGTH:
                        response = response[:MAX_RESPONSE_LENGTH] + "..."
                    if not response:
                        response = "Извини, не понял голос. Давай попробуем ещё?"
                    if is_business:
                        await message.get_bot().send_message(chat_id=chat_id, text=response, business_connection_id=business_connection_id)
                    else:
                        await message.reply_text(response)
                    logger.info(f"Ответ на голосовое отправлен в чат {chat_id}: {response}")

        except Exception as e:
            logger.error(f"Ошибка обработки голосового для чата {chat_id}: {str(e)}", exc_info=True)
            if is_business and business_connection_id:
                try:
                    await message.get_bot().send_message(chat_id=chat_id, text="⚠️ Ошибка голоса", business_connection_id=business_connection_id)
                except Exception as inner_e:
                    logger.error(f"Ошибка отправки в бизнес-чат: {str(inner_e)}")
            else:
                await message.reply_text("⚠️ Ошибка голоса")

    async def _process_text(self, chat_id: int, text: str) -> str:
        """Обработка текста через DeepSeek R1 Zero"""
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                *self.chat_history[chat_id][-MAX_HISTORY*2:],
                {"role": "user", "content": text}
            ]
            logger.info(f"Запрос в openrouter.ai (модель: deepseek/deepseek-r1-zero:free, чат: {chat_id}): {text}")
            logger.info(f"Содержимое messages: {messages}")
            completion = await self.openrouter_client.chat.completions.create(
                model="deepseek/deepseek-r1-zero:free",
                messages=messages,
                temperature=0.7,
                max_tokens=200  # Ограничение длины ответа
            )
            response = completion.choices[0].message.content
            logger.info(f"Ответ от openrouter.ai для чата {chat_id}: {response}")
            self._update_history(chat_id, text, response)
            return response
        except Exception as e:
            logger.error(f"Ошибка обработки текста для чата {chat_id}: {str(e)}", exc_info=True)
            return "⚠️ Ошибка ответа"

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
            await self.application.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
            logger.info(f"Вебхук настроен: {webhook_url}")
        except Exception as e:
            logger.critical(f"Ошибка вебхука: {str(e)}", exc_info=True)
            raise

    async def _error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Глобальный обработчик ошибок"""
        logger.error(f"Ошибка: {str(context.error)}", exc_info=True)
        if update and (update.message or update.business_message):
            message = update.business_message or update.message
            business_connection_id = update.business_message.business_connection_id if update.business_message else None
            try:
                if business_connection_id:
                    await message.get_bot().send_message(chat_id=message.chat_id, text="⚠️ Внутренняя ошибка", business_connection_id=business_connection_id)
                else:
                    await message.reply_text("⚠️ Внутренняя ошибка")
            except Exception as e:
                logger.error(f"Ошибка отправки: {str(e)}")

# Инициализация бота
bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        logger.info(f"Обновление: {data}")
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
    return "Bot is running!"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    if "RENDER" in os.environ:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    else:
        app.run(host='0.0.0.0', port=port, use_reloader=False)

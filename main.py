import os
import asyncio
import logging
import threading
import httpx
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
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
RESPONSE_DELAY_SECONDS = 10
MAX_MESSAGE_LENGTH = 4096  # Ограничение Telegram для текста в сообщении

AUTO_GENERATION_KEYWORDS = ["сгенерируй", "покажи", "фото", "фотку", "картинк", "изображен"]

class BotManager:
    def __init__(self):
        self.loop = None
        self.application = None
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.initialized = threading.Event()
        # self.deepseek_client = None  # Отключено, DeepSeek больше не используется
        self.openai_client = None  # Используем OpenAI вместо DeepSeek
        self.image_client = None
        self.chat_history = defaultdict(list)
        self.owner_user_id = int(os.getenv("OWNER_USER_ID", "0"))
        self.bot_id = None
        
        self.owner_info = {
            "owner_name": "Сергей",
            "owner_style": "Спокойный, дружелюбный, уверенный в себе, использую лёгкий юмор и уместный сарказм, если нужно[...]",
            "owner_details": "Предпочитаю говорить по делу, но умею развить мысль. Ценю структурированные подходы, часто п�[...]"
        }
        
    def process_update(self, json_data):
        try:
            if not self.initialized.wait(timeout=15):
                raise RuntimeError("Таймаут инициализации")
            future = asyncio.run_coroutine_threadsafe(self._process_update(json_data), self.loop)
            return future.result(timeout=30)
        except Exception as e:
            logger.error(f"Ошибка обработки: {str(e)}", exc_info=True)
            raise

    async def _process_update(self, json_data):
        try:
            update = Update.de_json(json_data, self.application.bot)
            await self.application.process_update(update)
        except Exception as e:
            logger.error(f"Ошибка обработки: {str(e)}", exc_info=True)
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
        try:
            # self.deepseek_client = AsyncOpenAI(
            #     base_url="https://api.deepseek.com",
            #     api_key=os.getenv("DEEPSEEK_API_KEY"),
            #     timeout=30.0
            # )
            self.openai_client = AsyncOpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url="https://api.openai.com/v1",
                timeout=30.0
            )
            self.image_client = self.openai_client  # Можно использовать тот же клиент для изображений
            logger.info("Проверка OpenAI API...")
            test_completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[{"role": "system", "content": self._get_system_prompt()}, {"role": "user", "content": "Привет, тест."}],
                temperature=0.7,
                max_tokens=500  # Увеличено до 500
            )
            logger.info(f"Тестовый ответ: {test_completion.choices[0].message.content}")
            self.application = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
            bot_info = await self.application.bot.get_me()
            self.bot_id = bot_info.id
            logger.info(f"ID бота: {self.bot_id}")
            self.application.add_handler(CommandHandler("start", self._start_command))
            self.application.add_handler(CommandHandler("generate_image", self._generate_image))
            self.application.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | (filters.TEXT & filters.UpdateType.BUSINESS_MESSAGE), self._handle_message))
            self.application.add_handler(MessageHandler(filters.VOICE | (filters.VOICE & filters.UpdateType.BUSINESS_MESSAGE), self._handle_voice_message))
            self.application.add_error_handler(self._error_handler)
            await self.application.initialize()
            if "RENDER" in os.environ:
                await self._setup_webhook()
            logger.info("Бот инициализирован")
        except Exception as e:
            logger.critical(f"Ошибка инициализации: {str(e)}", exc_info=True)
            raise

    def _get_system_prompt(self):
        return """
Ты — это я, {owner_name}, отвечай от моего имени.

🔹 Основной стиль общения:
{owner_style}

🔹 О себе:
{owner_details}
Я — сеньор продуктовый дизайнер и руководитель. Основатель стартапа Tezam.pro, мы создаём Telegram-приложения для би�[...]

📌 Всегда соблюдай эти принципы:
1. Говори **только от моего лица**, как если бы ты — это я.
2. Всегда соблюдай мой стиль: уверенно, спокойно, структурированно. Можно с лёгким юмором и уместным сарказмом[...]
3. Не «помогаешь» — **предлагаю решения**. Если можно сказать проще — скажи проще.
4. Не используй шаблонные фразы. Будь естественным.
5. Если есть путь сделать лучше — предложи.
6. Покажи, что я в теме, что у меня есть опыт и я делюсь им осознанно.
7. Будь краток и сдержан, но давай достаточно деталей для осмысленного ответа.
""".format(**self.owner_info)

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            logger.info(f"/start от {user.id} ({user.username or user.first_name})")
            message = update.message or update.business_message
            text = f"🤖 Привет, {user.first_name}! Я твой AI-ассистент, могу отвечать и генерировать изображения."
            if update.business_message:
                await message.get_bot().send_message(chat_id=message.chat_id, text=text, business_connection_id=update.business_message.business_connection_id)
            else:
                await message.reply_text(text)
            logger.info(f"Ответ /start в чат {user.id}")
        except Exception as e:
            logger.error(f"Ошибка /start: {str(e)}", exc_info=True)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            text = message.text.strip()
            if user.id == self.owner_user_id or user.id == self.bot_id:
                logger.info(f"Пропущено от владельца/бота (ID: {user.id}) в чате {chat_id}: {text}")
                return
            logger.info(f"{'Бизнес-' if is_business else ''}Сообщение в чате {chat_id} от {user.id} ({user.username or user.first_name}): {text}")
            asyncio.create_task(self._delayed_message_processing(message, text, chat_id, is_business, business_connection_id))
            logger.info(f"Задача для чата {chat_id} запущена")
        except Exception as e:
            logger.error(f"Ошибка обработки: {str(e)}", exc_info=True)
            if is_business and business_connection_id:
                await message.get_bot().send_message(chat_id=chat_id, text="⚠️ Ошибка", business_connection_id=business_connection_id)
            else:
                await message.reply_text("⚠️ Ошибка")

    async def _delayed_message_processing(self, message, text: str, chat_id: int, is_business: bool, business_connection_id: str = None):
        try:
            await asyncio.sleep(RESPONSE_DELAY_SECONDS)
            logger.info(f"Обработка {'бизнес-' if is_business else ''}сообщения для чата {chat_id}: {text}")
            if any(kw in text.lower() for kw in AUTO_GENERATION_KEYWORDS):
                await self._generate_image_from_text(message, text, business_connection_id)
            else:
                response = await self._process_text(chat_id, text)
                response = response.replace("\\boxed{", "").replace("}", "").strip()
                if not response or response.isspace():
                    response = f"Привет, {message.from_user.first_name}! Не понял, уточни, например: 'помоги с аналитикой'."
                # Разбиение длинного ответа на части, если превышает лимит Telegram
                while len(response) > MAX_MESSAGE_LENGTH:
                    part = response[:MAX_MESSAGE_LENGTH]
                    response = response[MAX_MESSAGE_LENGTH:]
                    if is_business:
                        await message.get_bot().send_message(chat_id=chat_id, text=part, business_connection_id=business_connection_id)
                    else:
                        await message.reply_text(part)
                user_name = message.from_user.first_name
                response = response.replace("Сергей", user_name)
                if is_business:
                    await message.get_bot().send_message(chat_id=chat_id, text=response, business_connection_id=business_connection_id)
                else:
                    await message.reply_text(response)
                logger.info(f"Ответ в чат {chat_id}: {response}")
        except Exception as e:
            logger.error(f"Ошибка обработки для чата {chat_id}: {str(e)}", exc_info=True)
            if is_business and business_connection_id:
                await message.get_bot().send_message(chat_id=chat_id, text="⚠️ Ошибка", business_connection_id=business_connection_id)
            else:
                await message.reply_text("⚠️ Ошибка")

    async def _generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            prompt = ' '.join(context.args)
            logger.info(f"Запрос изображения от {user.id}: {prompt}")
            if not prompt:
                raise ValueError("Пустой промпт")
            message = update.message or update.business_message
            business_connection_id = update.business_message.business_connection_id if update.business_message else None
            await self._generate_and_send_image(message, prompt, business_connection_id)
            logger.info(f"Изображение в чат {user.id}")
        except Exception as e:
            logger.error(f"Ошибка генерации: {str(e)}", exc_info=True)
            await (update.message or update.business_message).get_bot().send_message(
                chat_id=(update.message or update.business_message).chat_id,
                text="⚠️ Укажи описание",
                business_connection_id=update.business_message.business_connection_id if update.business_message else None
            )

    async def _generate_image_from_text(self, message: Update, text: str, business_connection_id: str = None):
        try:
            prompt = await self._create_image_prompt(text)
            await self._generate_and_send_image(message, prompt, business_connection_id)
        except Exception as e:
            logger.error(f"Ошибка создания изображения: {str(e)}", exc_info=True)
            await message.get_bot().send_message(chat_id=message.chat_id, text="⚠️ Ошибка изображения", business_connection_id=business_connection_id)

    async def _generate_and_send_image(self, message: Update, prompt: str, business_connection_id: str = None):
        try:
            logger.info(f"Генерация: {prompt}")
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
            logger.info(f"Изображение в чат {message.chat_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки: {str(e)}")
            raise

    async def _create_image_prompt(self, text: str) -> str:
        try:
            logger.info(f"Промпт для DALL-E: {text}")
            messages = [{"role": "system", "content": "Сгенерируй англоязычное описание для DALL-E"}, {"role": "user", "content": text}]
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            prompt = completion.choices[0].message.content
            logger.info(f"Промпт: {prompt}")
            return prompt
        except Exception as e:
            logger.error(f"Ошибка промпта: {str(e)}")
            return text

    async def _handle_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            if user.id == self.owner_user_id or user.id == self.bot_id:
                logger.info(f"Пропущено голосовое от {user.id} в чате {chat_id}")
                return
            logger.info(f"{'Бизнес-' if is_business else ''}Голосовое в чате {chat_id} от {user.id}")
            asyncio.create_task(self._delayed_voice_processing(message, chat_id, is_business, business_connection_id))
            logger.info(f"Задача голосового для {chat_id}")
        except Exception as e:
            logger.error(f"Ошибка голосового: {str(e)}", exc_info=True)
            if is_business and business_connection_id:
                await message.get_bot().send_message(chat_id=chat_id, text="⚠️ Ошибка голоса", business_connection_id=business_connection_id)
            else:
                await message.reply_text("⚠️ Ошибка голоса")

    async def _delayed_voice_processing(self, message, chat_id: int, is_business: bool, business_connection_id: str = None):
        try:
            await asyncio.sleep(RESPONSE_DELAY_SECONDS)
            logger.info(f"Обработка {'бизнес-' if is_business else ''}голосового для {chat_id}")
            voice_file = await message.voice.get_file()
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(voice_file.file_path)
                file_content = response.content
                logger.info(f"Голосовой файл: {len(file_content)} байт")
                transcript = await self.image_client.audio.transcriptions.create(
                    file=("voice.ogg", file_content, "audio/ogg"),
                    model="whisper-1",
                    response_format="text"
                )
                logger.info(f"Текст: {transcript}")
                if any(kw in transcript.lower() for kw in AUTO_GENERATION_KEYWORDS):
                    await self._generate_image_from_text(message, transcript, business_connection_id)
                else:
                    response = await self._process_text(chat_id, transcript)
                    response = response.replace("\\boxed{", "").replace("}", "").strip()
                    if not response or response.isspace():
                        response = f"Привет, {message.from_user.first_name}! Не понял, уточни."
                    user_name = message.from_user.first_name
                    response = response.replace("Сергей", user_name)
                    if is_business:
                        await message.get_bot().send_message(chat_id=chat_id, text=response, business_connection_id=business_connection_id)
                    else:
                        await message.reply_text(response)
                    logger.info(f"Ответ голосового: {response}")
        except Exception as e:
            logger.error(f"Ошибка голосового для {chat_id}: {str(e)}", exc_info=True)
            if is_business and business_connection_id:
                await message.get_bot().send_message(chat_id=chat_id, text="⚠️ Ошибка голоса", business_connection_id=business_connection_id)
            else:
                await message.reply_text("⚠️ Ошибка голоса")

    async def _process_text(self, chat_id: int, text: str) -> str:
        try:
            messages = [{"role": "system", "content": self._get_system_prompt()}, *self.chat_history[chat_id][-MAX_HISTORY*2:], {"role": "user", "content": text}]
            logger.info(f"Запрос (чат {chat_id}): {text}")
            logger.info(f"Messages: {messages}")
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=messages,
                temperature=0.7,
                max_tokens=500,  # Увеличено для более длинных ответов
                stream=False
            )
            response = completion.choices[0].message.content
            logger.info(f"Ответ: {response}")
            self._update_history(chat_id, text, response)
            return response
        except Exception as e:
            logger.error(f"Ошибка обработки (чат {chat_id}): {str(e)}", exc_info=True)
            return "⚠️ Ошибка"

    def _update_history(self, chat_id: int, text: str, response: str):
        self.chat_history[chat_id].extend([{"role": "user", "content": text}, {"role": "assistant", "content": response}])
        if len(self.chat_history[chat_id]) > MAX_HISTORY * 2:
            self.chat_history[chat_id] = self.chat_history[chat_id][-MAX_HISTORY*2:]

    async def _setup_webhook(self):
        try:
            webhook_url = f"{os.getenv('WEBHOOK_URL')}/webhook"
            await self.application.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
            logger.info(f"Вебхук: {webhook_url}")
        except Exception as e:
            logger.critical(f"Ошибка вебхука: {str(e)}", exc_info=True)
            raise

    async def _error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Ошибка: {str(context.error)}", exc_info=True)
        if update and (update.message or update.business_message):
            message = update.business_message or update.message
            business_connection_id = update.business_message.business_connection_id if update.business_message else None
            try:
                if business_connection_id:
                    await message.get_bot().send_message(chat_id=message.chat_id, text="⚠️ Ошибка", business_connection_id=business_connection_id)
                else:
                    await message.reply_text("⚠️ Ошибка")
            except Exception as e:
                logger.error(f"Ошибка отправки: {str(e)}")

# Инициализация
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
    return jsonify({"status": "ok", "initialized": bot_manager.initialized.is_set(), "webhook_configured": bool(bot_manager.application and bot_manager.application.bot.get_webhook_info().url)})

@app.route('/')
def home():
    return "Bot running!"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    if "RENDER" in os.environ:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    else:
        app.run(host='0.0.0.0', port=port, use_reloader=False)

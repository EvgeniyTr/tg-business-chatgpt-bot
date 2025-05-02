import os
import asyncio
import logging
import threading
import httpx
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from tempfile import NamedTemporaryFile
from datetime import datetime, timedelta
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
DELAY_MINUTES = 10
SYSTEM_PROMPT = """
Ты - это я, {owner_name}. Отвечай от моего имени, используя мой стиль общения.
Основные характеристики:
- {owner_style}
- {owner_details}

Всегда придерживайся этих правил:
1. Отвечай только от моего лица с подписью, я AI асистент
2. Сохраняй мой стиль общения
3. Будь естественным
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
        
        self.owner_info = {
            "owner_name": "Сергей",
            "owner_style": "Спокойный, дружелюбный, уверенный в себе, использую лёгкий юмор и уместный сарказм, если нужно — могу быть прямым.",
            "owner_details": "Предпочитаю говорить по делу, но умею развить мысль. Ценю структурированные подходы, часто предлагаю решения и иду на шаг вперёд. Готов делиться опытом и вовлекать других в процесс, если вижу в этом смысл."
        }

    def start(self):
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._initialize())
                self.initialized.set()
                self.loop.run_forever()
            except Exception as e:
                logger.critical(f"Ошибка: {str(e)}", exc_info=True)
                os._exit(1)
        self.executor.submit(run_loop)

    async def _initialize(self):
        self.openai_client = openai.AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        )
        
        self.application = ApplicationBuilder() \
            .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
            .build()

        # Регистрация обработчиков
        self.application.add_handler(MessageHandler(
            self._business_filter(),
            self._handle_business_message
        ))
        self.application.add_handler(CommandHandler("generate_image", self._generate_image))
        self.application.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        self.application.add_error_handler(self._error_handler)
        
        await self.application.initialize()
        
        if "RENDER" in os.environ:
            await self._setup_webhook()

    def _business_filter(self):
        return filters.TEXT & filters.MessageFilter(
           lambda msg: bool(msg.business_connection_id)
        )
    async def _check_working_hours(self):
        tz = pytz.timezone("Europe/Moscow")
        now = datetime.now(tz)
        if now.weekday() >= 5:
            return False
        start_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
        end_time = now.replace(hour=18, minute=0, second=0, microsecond=0)
        return start_time <= now < end_time

    async def _check_delay(self, user_id: int):
        last_message = self.user_timestamps.get(user_id)
        if last_message:
            delay = (datetime.now() - last_message).total_seconds() / 60
            if delay < DELAY_MINUTES:
                return False
        self.user_timestamps[user_id] = datetime.now()
        return True

    async def _handle_business_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if await self._check_working_hours():
                return

            user_id = update.effective_user.id
            if not await self._check_delay(user_id):
                return

            text = update.message.text.strip()
            if any(kw in text.lower() for kw in AUTO_GENERATION_KEYWORDS):
                await self._generate_image_from_text(update, text)
            else:
                response = await self._process_text(user_id, text)
                await update.message.reply_text(response)
        except Exception as e:
            logger.error(f"Ошибка обработки: {str(e)}")

    async def _generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            prompt = ' '.join(context.args) or self._extract_prompt_from_history(update.effective_user.id)
            await self._generate_and_send_image(update, prompt)
        except Exception as e:
            await update.message.reply_text("⚠️ Укажите описание для изображения")

    async def _generate_image_from_text(self, update: Update, text: str):
        try:
            prompt = await self._create_image_prompt(text)
            await self._generate_and_send_image(update, prompt)
        except Exception as e:
            logger.error(f"Ошибка генерации: {str(e)}")

    async def _generate_and_send_image(self, update: Update, prompt: str):
        response = await self.openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard"
        )
        await update.message.reply_photo(response.data[0].url)

    async def _create_image_prompt(self, text: str) -> str:
        messages = [{
            "role": "system", 
            "content": "Сгенерируй детальное описание для DALL-E на основе запроса пользователя"
        }, {
            "role": "user", 
            "content": text
        }]
        
        completion = await self.openai_client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=messages,
            temperature=0.7
        )
        return completion.choices[0].message.content

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            voice_file = await update.message.voice.get_file()
            async with httpx.AsyncClient() as client:
                response = await client.get(voice_file.file_path)
                with NamedTemporaryFile(delete=True, suffix=".ogg") as temp_file:
                    temp_file.write(response.content)
                    transcript = await self.openai_client.audio.transcriptions.create(
                        file=open(temp_file.name, "rb"),
                        model="whisper-1",
                        response_format="text"
                    )
                    
                    if any(kw in transcript.lower() for kw in AUTO_GENERATION_KEYWORDS):
                        await self._generate_image_from_text(update, transcript)
                    else:
                        response = await self._process_text(update.effective_user.id, transcript)
                        await update.message.reply_text(f"🎤 Распознано: {transcript}\n\n📝 Ответ: {response}")
        except Exception as e:
            logger.error(f"Ошибка обработки голоса: {str(e)}")

    async def _process_text(self, user_id: int, text: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(**self.owner_info)},
            *self.chat_history[user_id][-MAX_HISTORY:],
            {"role": "user", "content": text}
        ]
        
        completion = await self.openai_client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=messages,
            temperature=0.7
        )
        
        response = completion.choices[0].message.content
        self._update_history(user_id, text, response)
        return response

    def _update_history(self, user_id: int, text: str, response: str):
        self.chat_history[user_id].extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": response}
        ])
        if len(self.chat_history[user_id]) > MAX_HISTORY * 2:
            self.chat_history[user_id] = self.chat_history[user_id][-MAX_HISTORY * 2:]

    async def _setup_webhook(self):
        webhook_url = f"{os.getenv('WEBHOOK_URL')}/webhook"
        await self.application.bot.set_webhook(
            url=webhook_url,
            allowed_updates=["message", "business_message", "voice"]
        )

    async def _error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Ошибка: {context.error}", exc_info=True)
        if update.message:
            await update.message.reply_text("⚠️ Произошла ошибка")

bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        bot_manager.process_update(request.get_json())
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error"}), 500

@app.route('/')
def home():
    return "Telegram Bot is running!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

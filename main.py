import os
import asyncio
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import pytz

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from openai import AsyncOpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
MAX_HISTORY = 3
DELAY_MINUTES = 1
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
        self.dispatcher = None
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.initialized = threading.Event()
        self.openai_client = None
        self.chat_history = defaultdict(list)
        self.bot = None

        self.owner_info = {
            "owner_name": "Сергей",
            "owner_style": "Спокойный, дружелюбный, уверенный в себе, использую лёгкий юмор и уместный сарказм, если нужно — могу быть прямым.",
            "owner_details": "Предпочитаю говорить по делу, но умею развить мысль. Ценю структурированные подходы, часто предлагаю решения и иду на шаг вперёд. Готов делиться опытом и вовлекать других в процесс, если вижу в этом смысл."
        }

    def run(self):
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._init_bot())
                self.initialized.set()
                self.loop.run_forever()
            except Exception as e:
                logger.critical(f"Ошибка запуска: {e}", exc_info=True)
                os._exit(1)

        self.executor.submit(run_loop)

    async def _init_bot(self):
        logger.info("Инициализация OpenAI...")
        self.openai_client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url="https://api.openai.com/v1"
        )

        logger.info("Создание Telegram Bot...")
        self.bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"), parse_mode=ParseMode.HTML)
        self.dispatcher = Dispatcher(storage=MemoryStorage())

        self.dispatcher.message.register(self.handle_message, F.text)

        logger.info("Настройка webhook...")
        if os.getenv("RENDER"):
            await self.bot.set_webhook(f"{os.getenv('WEBHOOK_URL')}/webhook")

    async def handle_message(self, message: Message):
        user_id = message.from_user.id
        text = message.text.strip()

        logger.info(f"Сообщение от {user_id}: {text}")

        if not await self._check_delay(user_id):
            return

        if not await self._check_working_hours():
            await message.answer("⏰ Сейчас не рабочее время (9:00–18:00 МСК)")
            return

        if any(kw in text.lower() for kw in AUTO_GENERATION_KEYWORDS):
            await message.answer("🔧 Генерация изображений пока недоступна")
            return

        response = await self._generate_response(user_id, text)
        await message.answer(response + "\n\n_Ответ от AI ассистента_", parse_mode=ParseMode.MARKDOWN)

    async def _generate_response(self, user_id: int, user_message: str) -> str:
        history = self.chat_history[user_id][-MAX_HISTORY:]

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(**self.owner_info)}
        ] + history + [
            {"role": "user", "content": user_message}
        ]

        try:
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.7
            )
            reply = completion.choices[0].message.content
            self._update_history(user_id, user_message, reply)
            return reply
        except Exception as e:
            logger.error(f"Ошибка GPT: {e}")
            return "⚠️ Ошибка обработки запроса"

    def _update_history(self, user_id: int, user_text: str, bot_reply: str):
        self.chat_history[user_id].extend([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": bot_reply}
        ])
        self.chat_history[user_id] = self.chat_history[user_id][-MAX_HISTORY * 2:]

    async def _check_working_hours(self):
        tz = pytz.timezone("Europe/Moscow")
        now = datetime.now(tz)
        return now.weekday() < 5 and 9 <= now.hour < 18

    async def _check_delay(self, user_id: int):
        return True

bot_manager = BotManager()
bot_manager.run()

# Aiohttp сервер для webhook
async def webhook_handler(request):
    try:
        data = await request.json()
        await bot_manager.dispatcher.feed_raw_update(data)
        return web.Response(text="ok")
    except Exception as e:
        logger.error(f"Ошибка webhook: {e}", exc_info=True)
        return web.Response(status=500)

async def init_app():
    app = web.Application()
    app.router.add_post("/webhook", webhook_handler)
    setup_application(app, bot_manager.dispatcher, bot=bot_manager.bot)
    return app

if __name__ == '__main__':
    import sys
    if os.getenv("RENDER"):
        from waitress import serve
        web.run_app(init_app(), port=int(os.getenv("PORT", 10000)))
    else:
        web.run_app(init_app(), port=int(os.getenv("PORT", 5000)))

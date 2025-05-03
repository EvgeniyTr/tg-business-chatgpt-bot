import os
import asyncio
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from tempfile import NamedTemporaryFile
from datetime import datetime, timedelta
import pytz

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, FSInputFile
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import httpx
from openai import AsyncOpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
MAX_HISTORY = 3
DELAY_MINUTES = 10
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
        self.bot = None
        self.dp = None
        self.router = Router()
        self.executor = ThreadPoolExecutor(max_workers=2)
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
        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._init_bot())
                self.initialized.set()
                self.loop.run_forever()
            except Exception as e:
                logger.critical(f"Ошибка запуска: {e}", exc_info=True)
                os._exit(1)

        self.executor.submit(run)

    async def _init_bot(self):
        self.openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"), parse_mode=ParseMode.HTML)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(self.router)

        self.router.message.register(self._handle_text, F.text)
        self.router.message.register(self._handle_voice, F.voice)

        if "RENDER" in os.environ:
            await self._setup_webhook()

    async def _setup_webhook(self):
        app = web.Application()
        app.router.add_route("POST", "/webhook", SimpleRequestHandler(dispatcher=self.dp, bot=self.bot).handle)
        setup_application(app, self.dp, bot=self.bot)

        await self.bot.set_webhook(f"{os.getenv('WEBHOOK_URL')}/webhook")
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
        await site.start()

    async def _check_working_hours(self):
        now = datetime.now(pytz.timezone("Europe/Moscow"))
        return now.weekday() < 5 and 9 <= now.hour < 18

    async def _check_delay(self, user_id):
        last_time = self.user_timestamps.get(user_id)
        if last_time and (datetime.now() - last_time).total_seconds() < DELAY_MINUTES * 60:
            return False
        self.user_timestamps[user_id] = datetime.now()
        return True

    async def _handle_text(self, message: Message):
        user_id = message.from_user.id
        if not await self._check_delay(user_id): return
        if not await self._check_working_hours(): return

        content = message.text.lower()
        if any(word in content for word in AUTO_GENERATION_KEYWORDS):
            await self._generate_image(message)
        else:
            reply = await self._ask_gpt(user_id, message.text)
            await message.answer(f"{reply}\n\n<i>AI ассистент</i>")

    async def _handle_voice(self, message: Message):
        try:
            file = await self.bot.get_file(message.voice.file_id)
            file_path = file.file_path
            file_url = f"https://api.telegram.org/file/bot{self.bot.token}/{file_path}"

            async with httpx.AsyncClient() as client:
                voice_data = await client.get(file_url)
                with NamedTemporaryFile(delete=False, suffix=".ogg") as f:
                    f.write(voice_data.content)
                    transcript = await self.openai_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=open(f.name, "rb")
                    )

            text = transcript.text
            await self._handle_text(Message(
                chat=message.chat, from_user=message.from_user, text=text
            ))
        except Exception as e:
            logger.error(f"Ошибка голосового сообщения: {e}")

    async def _generate_image(self, message: Message):
        try:
            prompt = message.text
            response = await self.openai_client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="standard"
            )
            await message.answer_photo(photo=response.data[0].url)
        except Exception as e:
            logger.error(f"Ошибка генерации изображения: {e}")
            await message.answer("Не удалось сгенерировать изображение")

    async def _ask_gpt(self, user_id: int, text: str) -> str:
        history = self.chat_history[user_id][-MAX_HISTORY * 2:]
        messages = [{"role": "system", "content": SYSTEM_PROMPT.format(**self.owner_info)}] + history + [{"role": "user", "content": text}]

        response = await self.openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7
        )
        reply = response.choices[0].message.content

        self.chat_history[user_id].append({"role": "user", "content": text})
        self.chat_history[user_id].append({"role": "assistant", "content": reply})
        return reply

# Flask приложение
bot_manager = BotManager()
bot_manager.start()

app = web.Application()

@app.route('/webhook', methods=['POST'])
async def webhook(request):
    try:
        data = await request.json()
        bot_manager.dp.feed_update(bot_manager.bot, data)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return web.json_response({"error": str(e)}, status=500)

@app.route('/')
async def root(request):
    return web.Response(text="Bot is running")

if __name__ == '__main__':
    web.run_app(app, port=int(os.environ.get("PORT", 10000)))

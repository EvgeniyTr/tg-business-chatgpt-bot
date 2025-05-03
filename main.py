import os
import logging
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from tempfile import NamedTemporaryFile

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import httpx
from openai import AsyncOpenAI
import pytz

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
        self.bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"), parse_mode=ParseMode.HTML)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.router = Router()
        self.openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.chat_history = defaultdict(list)
        self.user_timestamps = {}
        
        self.owner_info = {
            "owner_name": "Сергей",
            "owner_style": "Спокойный, дружелюбный, уверенный в себе...",
            "owner_details": "Предпочитаю говорить по делу..."
        }
        
        self._register_handlers()

    def _register_handlers(self):
        self.router.message.register(self._handle_text, F.text)
        self.router.message.register(self._handle_voice, F.voice)
        self.dp.include_router(self.router)

    async def _check_working_hours(self):
        tz = pytz.timezone("Europe/Moscow")
        now = datetime.now(tz)
        return now.weekday() < 5 and 9 <= now.hour < 18

    async def _check_delay(self, user_id: int):
        last_time = self.user_timestamps.get(user_id)
        if last_time and (datetime.now() - last_time) < timedelta(minutes=DELAY_MINUTES):
            return False
        self.user_timestamps[user_id] = datetime.now()
        return True

    async def _handle_text(self, message: Message):
        user_id = message.from_user.id
        if not await self._check_delay(user_id):
            return await message.answer("⏳ Подождите перед следующим запросом")
        
        if not await self._check_working_hours():
            return await message.answer("⏰ Сейчас не рабочее время (9:00-18:00 МСК)")
        
        if any(word in message.text.lower() for word in AUTO_GENERATION_KEYWORDS):
            await self._generate_image(message)
        else:
            reply = await self._ask_gpt(user_id, message.text)
            await message.answer(reply)

    async def _handle_voice(self, message: Message):
        try:
            file = await message.voice.get_file()
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"https://api.telegram.org/file/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/{file.file_path}")
                
                with NamedTemporaryFile(suffix=".ogg") as tmp:
                    tmp.write(resp.content)
                    transcript = await self.openai_client.audio.transcriptions.create(
                        file=open(tmp.name, "rb"),
                        model="whisper-1"
                    )
                    
            await self._handle_text(Message(
                text=transcript.text,
                chat=message.chat,
                from_user=message.from_user
            ))
            
        except Exception as e:
            logger.error(f"Voice error: {e}")
            await message.answer("⚠️ Ошибка обработки голоса")

    async def _generate_image(self, message: Message):
        try:
            response = await self.openai_client.images.generate(
                model="dall-e-3",
                prompt=message.text,
                size="1024x1024"
            )
            await message.answer_photo(response.data[0].url)
        except Exception as e:
            logger.error(f"Image error: {e}")
            await message.answer("⚠️ Ошибка генерации изображения")

    async def _ask_gpt(self, user_id: int, text: str) -> str:
        history = self.chat_history[user_id][-MAX_HISTORY*2:]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(**self.owner_info)},
            *history,
            {"role": "user", "content": text}
        ]
        
        response = await self.openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7
        )
        
        reply = response.choices[0].message.content
        self.chat_history[user_id].extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply}
        ])
        
        return reply

async def on_startup(bot: Bot):
    await bot.set_webhook(
        url=f"{os.getenv('WEBHOOK_URL')}/webhook",
        drop_pending_updates=True
    )

async def main():
    bot_manager = BotManager()
    
    app = web.Application()
    app["bot"] = bot_manager.bot
    
    webhook_handler = SimpleRequestHandler(
        dispatcher=bot_manager.dp,
        bot=bot_manager.bot
    )
    
    webhook_handler.register(app, path="/webhook")
    setup_application(app, bot_manager.dp)
    
    await on_startup(bot_manager.bot)
    return app

if __name__ == "__main__":
    web.run_app(main(), port=int(os.getenv("PORT", 10000)), host="0.0.0.0")

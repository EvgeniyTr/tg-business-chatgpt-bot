from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from aiohttp import web
import logging
import openai
import os
import asyncio

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ключи и конфиг
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
WEBHOOK_PATH = "/webhook"
WEBHOOK_HOST = os.getenv("WEBHOOK_URL")
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 8080))

openai.api_key = OPENAI_KEY

# Бот и диспетчер
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

# Обработчики
@dp.message()
async def echo_message(message: Message):
    try:
        logger.info(f"User: {message.from_user.username} | Text: {message.text}")

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Ты полезный помощник"},
                {"role": "user", "content": message.text},
            ]
        )
        answer = response.choices[0].message.content
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        answer = "Ошибка. Я не могу ответить сейчас."

    await message.answer(answer)

# Приложение aiohttp
async def on_startup(app: web.Application):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()

async def main():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, dp.as_handler())
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()

    logger.info("Bot is up and running via webhook!")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())

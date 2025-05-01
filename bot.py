import os
import logging
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from openai import AsyncOpenAI
from pydantic_settings import BaseSettings
from pydantic import SecretStr

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения
class Settings(BaseSettings):
    TELEGRAM_TOKEN: SecretStr
    OPENAI_KEY: SecretStr
    WEBHOOK_URL: str
    PORT: int = 8080

    class Config:
        env_file = ".env"

settings = Settings()

# Инициализация клиентов
bot = Bot(token=settings.TELEGRAM_TOKEN.get_secret_value(), parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
openai_client = AsyncOpenAI(api_key=settings.OPENAI_KEY.get_secret_value())

# Обработка входящих сообщений
@dp.message()
async def handle_message(message: Message):
    logger.info(f"Message from {message.from_user.id}: {message.text}")
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты полезный AI-бот для поддержки клиентов."},
                {"role": "user", "content": message.text}
            ]
        )
        reply = response.choices[0].message.content
    except Exception as e:
        logger.exception("Ошибка OpenAI:")
        reply = "Произошла ошибка при обработке запроса. Попробуйте позже."

    await message.answer(reply)

# Webhook lifecycle
async def on_startup(app: web.Application):
    await bot.set_webhook(f"{settings.WEBHOOK_URL}/webhook")

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()

# Запуск приложения
async def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    app.router.add_route("POST", "/webhook", webhook_handler.handle)

    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.PORT)
    await site.start()

    logger.info("Webhook сервер запущен...")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())

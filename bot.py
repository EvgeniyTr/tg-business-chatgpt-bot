# bot.py
from aiogram import Bot, Dispatcher, Router, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from pydantic_settings import BaseSettings
from pydantic import SecretStr
import logging
import asyncio
from openai import AsyncOpenAI
from typing import Optional

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
class Settings(BaseSettings):
    TELEGRAM_TOKEN: SecretStr
    OPENAI_KEY: SecretStr
    WEBHOOK_URL: str
    PORT: int = 8080
    class Config:
        env_file = ".env"

settings = Settings()

# Telegram Bot init
bot = Bot(token=settings.TELEGRAM_TOKEN.get_secret_value(), parse_mode=ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# OpenAI клиент (GPT-4o)
client = AsyncOpenAI(api_key=settings.OPENAI_KEY.get_secret_value())

# Обработка нестандартного business_message
types.BusinessMessage = types.TelegramObject.build_class({
    'message_id': int,
    'from_': types.User,
    'chat': types.Chat,
    'date': int,
    'text': Optional[str]
}, name='BusinessMessage')

@router.update(types.BusinessMessage)
async def handle_business_message(message: types.TelegramObject):
    logger.info(f"Business msg from {message.from_.id}: {message.text}")
    try:
        completion = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты AI-бот поддержки клиентов."},
                {"role": "user", "content": message.text}
            ]
        )
        reply = completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка OpenAI: {e}")
        reply = "Произошла ошибка при обработке запроса."

    await bot.send_message(chat_id=message.chat.id, text=reply)

# Webhook
async def on_startup(app: web.Application):
    await bot.set_webhook(f"{settings.WEBHOOK_URL}/webhook")

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()

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

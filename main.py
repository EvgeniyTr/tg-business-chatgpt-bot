import os
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from openai import AsyncOpenAI
from pydantic_settings import BaseSettings
from redis.asyncio import Redis

# Настройка логгирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация через Pydantic
class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    OPENAI_API_KEY: str
    WEBHOOK_URL: str
    ADMIN_ID: int
    REDIS_HOST: str = "localhost"
    DELAY_MINUTES: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()

# Инициализация клиентов
bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
redis = Redis(host=settings.REDIS_HOST)
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

# Системный промпт
SYSTEM_PROMPT = """
Ты - цифровой ассистент Сергея. Отвечай от его имени, используя его стиль общения:
- Спокойный, дружелюбный, уверенный
- Используй легкий юмор и сарказм где уместно
- Предлагай решения и будь конкретным
"""

# Middleware для проверки задержки
class ThrottlingMiddleware:
    async def __call__(self, handler, event: Message, data):
        user_id = event.from_user.id
        
        last_message = await redis.get(f"user:{user_id}")
        if last_message:
            last_time = datetime.fromisoformat(last_message.decode())
            if (datetime.now() - last_time).seconds < settings.DELAY_MINUTES * 60:
                await event.answer("⏳ Пожалуйста, подождите перед следующим запросом")
                return

        await redis.set(f"user:{user_id}", datetime.now().isoformat())
        return await handler(event, data)

# Обработчики
@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "🤖 Привет! Я цифровой ассистент Сергея. "
        "Могу ответить на ваши вопросы или сгенерировать изображение по запросу."
    )

@dp.message(Command("generate_image"))
async def generate_image(message: Message):
    prompt = message.text.replace("/generate_image", "").strip()
    if not prompt:
        return await message.answer("✏️ Укажите описание для изображения")
    
    try:
        response = await openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard"
        )
        await message.answer_photo(response.data[0].url)
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await message.answer("⚠️ Ошибка генерации изображения")

@dp.message()
async def message_handler(message: Message):
    try:
        # Генерация текста
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message.text}
        ]
        
        completion = await openai_client.chat.completions.create(
            model="gpt-4-turbo",
            messages=messages,
            temperature=0.7
        )
        
        await message.answer(completion.choices[0].message.content)
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        await message.answer("⚠️ Произошла ошибка при обработке запроса")

# Вебхук для Render
async def on_startup():
    await bot.set_webhook(
        url=f"{settings.WEBHOOK_URL}/webhook",
        drop_pending_updates=True
    )
    logger.info("Bot started")

# Конфигурация Flask
from flask import Flask, request

app = Flask(__name__)

@app.post("/webhook")
async def webhook():
    try:
        update = types.Update(**request.json)
        await dp.feed_webhook_update(bot, update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error"}, 500

if __name__ == "__main__":
    dp.startup.register(on_startup)
    dp.update.middleware(ThrottlingMiddleware())
    
    if "RENDER" in os.environ:
        from waitress import serve
        serve(app, host="0.0.0.0", port=10000)
    else:
        app.run(host="0.0.0.0", port=10000)

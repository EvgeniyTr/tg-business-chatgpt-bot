import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import Dispatcher
from aiogram.utils.executor import start_webhook
from openai import AsyncOpenAI

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
dp = Dispatcher(bot)
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", 10000))

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)
    logger.info("Bot started!")

async def on_shutdown(dp):
    await bot.delete_webhook()
    logger.info("Bot stopped")

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer("🚀 Бот запущен! Отправьте голосовое сообщение")

@dp.message_handler(content_types=types.ContentType.VOICE)
async def handle_voice(message: types.Message):
    try:
        voice = await message.voice.get_file()
        file = await bot.download_file(voice.file_path)
        
        transcript = await openai_client.audio.transcriptions.create(
            file=("voice.ogg", file),
            model="whisper-1",
            response_format="text"
        )
        
        await message.answer(f"🎤 Распознано:\n{transcript}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await message.answer("❌ Ошибка обработки")

if __name__ == '__main__':
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        host="0.0.0.0",
        port=PORT,
        skip_updates=True
    )

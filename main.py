import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.utils.executor import start_webhook
from openai import AsyncOpenAI

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация клиентов
bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
dp = Dispatcher(bot)
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Конфигурация вебхука
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", 10000))

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)
    logger.info("🤖 Бот запущен и готов к работе!")

async def on_shutdown(dp):
    await bot.delete_webhook()
    logger.info("🛑 Бот остановлен")

@dp.message_handler(commands=["start", "help"])
async def send_welcome(message: types.Message):
    await message.answer(
        "🎙️ Привет! Я бот для работы с голосовыми сообщениями.\n\n"
        "Просто отправь мне голосовое сообщение, и я:\n"
        "1. Распознаю текст\n"
        "2. Сгенерирую ответ с помощью GPT-4\n"
        "3. Пришлю текстовый ответ"
    )

@dp.message_handler(content_types=types.ContentType.VOICE)
async def handle_voice(message: types.Message):
    try:
        # Скачивание голосового сообщения
        voice_file = await message.voice.get_file()
        file_path = await bot.download_file(voice_file.file_path)
        
        # Распознавание речи через Whisper
        transcript = await openai_client.audio.transcriptions.create(
            file=("voice.ogg", file_path),
            model="whisper-1",
            response_format="text"
        )
        
        # Генерация ответа через GPT-4
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{
                "role": "system",
                "content": "Ты полезный ассистент. Отвечай кратко и по делу."
            }, {
                "role": "user",
                "content": transcript
            }]
        )
        
        # Отправка результата
        await message.answer(
            f"🔊 Распознанный текст:\n{transcript}\n\n"
            f"💡 Ответ:\n{response.choices[0].message.content}"
        )

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await message.answer("⚠️ Произошла ошибка при обработке сообщения")

if __name__ == "__main__":
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        host="0.0.0.0",
        port=PORT,
        skip_updates=True
    )

import os
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import Dispatcher
from aiogram.utils.executor import start_webhook
from openai import AsyncOpenAI

# Настройка логгирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
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

async def log_message(message: types.Message):
    """Логирование входящих сообщений"""
    log_data = {
        "timestamp": datetime.now().isoformat(),
        "user_id": message.from_user.id,
        "chat_id": message.chat.id,
        "message_type": message.content_type,
        "text": message.text or message.caption or "[медиа-сообщение]",
        "full_message": message.to_python()
    }
    
    logger.info(
        "New message\n"
        f"User: {message.from_user.full_name} (ID: {log_data['user_id']})\n"
        f"Chat: {message.chat.title if message.chat.title else 'private'} (ID: {log_data['chat_id']})\n"
        f"Type: {log_data['message_type']}\n"
        f"Content: {log_data['text']}\n"
        f"Full data: {log_data['full_message']}"
    )

@dp.message_handler(content_types=types.ContentTypes.ANY)
async def all_messages_handler(message: types.Message):
    """Обработчик для всех типов сообщений"""
    await log_message(message)
    
    # Пересылка только текстовых сообщений дальше по цепочке обработчиков
    if message.content_type == types.ContentType.TEXT:
        message.conf["is_handled"] = False
    else:
        message.conf["is_handled"] = True

@dp.message_handler(commands=["start", "help"])
async def send_welcome(message: types.Message):
    await message.answer(
        "🎙️ Бот-ассистент готов к работе!\n\n"
        "Я могу:\n"
        "1. Обрабатывать голосовые сообщения\n"
        "2. Анализировать текст\n"
        "3. Вести диалог от вашего имени"
    )
    await log_message(message)

@dp.message_handler(content_types=types.ContentType.VOICE)
async def handle_voice(message: types.Message):
    try:
        await log_message(message)
        
        # Скачивание голосового сообщения
        voice_file = await message.voice.get_file()
        file_path = await bot.download_file(voice_file.file_path)
        
        # Распознавание речи
        transcript = await openai_client.audio.transcriptions.create(
            file=("voice.ogg", file_path),
            model="whisper-1",
            response_format="text"
        )
        
        # Логирование распознанного текста
        logger.info(f"Распознанный текст: {transcript}")
        
        # Генерация ответа
        response = await openai_client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{
                "role": "system",
                "content": "Вы - персональный ассистент. Отвечайте кратко и по делу."
            }, {
                "role": "user", 
                "content": transcript
            }]
        )
        
        # Отправка и логирование ответа
        await message.answer(response.choices[0].message.content)
        logger.info(f"Отправлен ответ: {response.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Ошибка: {str(e)}", exc_info=True)
        await message.answer("⚠️ Произошла ошибка при обработке")

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)
    logger.info("✅ Бот успешно запущен")

async def on_shutdown(dp):
    await bot.delete_webhook()
    logger.info("🛑 Бот остановлен")

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

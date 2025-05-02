import os
import asyncio
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from tempfile import NamedTemporaryFile
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler
from flask import Flask, request, jsonify
import openai

# Настройка логгирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Конфигурация
MAX_HISTORY = 3
SYSTEM_PROMPT = """
Ты - это я, {owner_name}. Отвечай от моего имени, используя мой стиль общения.
Основные характеристики:
- {owner_style}
- {owner_details}

Всегда придерживайся этих правил:
1. Отвечай только от моего лица с подписью, я AI асистент
2. Сохраняй мой стиль общения
3. Будь естественным
"""

class BotManager:
    def __init__(self):
        self.loop = None
        self.application = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.initialized = threading.Event()
        self.openai_client = None
        self.chat_history = defaultdict(list)
        
        self.owner_info = {
            "owner_name": "Сергей ",
    "owner_style": "Спокойный, дружелюбный, уверенный в себе, использую лёгкий юмор и уместный сарказм, если нужно — могу быть прямым.",
    "owner_details": "Предпочитаю говорить по делу, но умею развить мысль. Ценю структурированные подходы, часто предлагаю решения и иду на шаг вперёд. Готов делиться опытом и вовлекать других в процесс, если вижу в этом смысл."
        }

    def start(self):
        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._initialize())
                self.initialized.set()
                self.loop.run_forever()
            except Exception as e:
                logger.critical(f"Ошибка: {str(e)}", exc_info=True)
                os._exit(1)

        self.executor.submit(run_loop)

    async def _initialize(self):
        self.openai_client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        self.application = ApplicationBuilder() \
            .token(os.getenv("TELEGRAM_BOT_TOKEN")) \
            .build()
        
        # Регистрация обработчиков
        self.application.add_handler(CommandHandler("generate_image", self._generate_image))
        self.application.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))
        
        await self.application.initialize()
        
        if "RENDER" in os.environ:
            await self._setup_webhook()

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            text = update.message.text
            
            # Обработка команд
            if text.startswith("/generate_image"):
                return  # Команда обрабатывается отдельным хендлером
            
            # Логика обработки текста
            response = await self._process_text(user_id, text)
            await update.message.reply_text(response)
            
        except Exception as e:
            logger.error(f"Ошибка: {str(e)}")
            await update.message.reply_text("⚠️ Ошибка обработки сообщения")

    async def _generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            prompt = ' '.join(context.args)
            response = await self.openai_client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )
            image_url = response.data[0].url
            await update.message.reply_photo(image_url)
        except Exception as e:
            logger.error(f"Ошибка генерации изображения: {str(e)}")
            await update.message.reply_text("⚠️ Не удалось сгенерировать изображение")

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            voice_file = await update.message.voice.get_file()
            
            with NamedTemporaryFile(delete=True, suffix=".ogg") as temp_file:
                await voice_file.download_to_drive(temp_file.name)
                
                transcript = await self.openai_client.audio.transcriptions.create(
                    file=open(temp_file.name, "rb"),
                    model="whisper-1",
                    response_format="text"
                )
                
            response = await self._process_text(update.effective_user.id, transcript)
            await update.message.reply_text(f"🎤 Распознано: {transcript}\n\n📝 Ответ: {response}")
            
        except Exception as e:
            logger.error(f"Ошибка обработки голоса: {str(e)}")
            await update.message.reply_text("⚠️ Ошибка распознавания голоса")

    async def _process_text(self, user_id: int, text: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(**self.owner_info)},
            *self.chat_history[user_id][-MAX_HISTORY:],
            {"role": "user", "content": text}
        ]
        
        completion = await self.openai_client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=messages,
            temperature=0.7
        )
        
        response = completion.choices[0].message.content
        self._update_history(user_id, text, response)
        return response

    def _update_history(self, user_id: int, text: str, response: str):
        self.chat_history[user_id].extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": response}
        ])
        if len(self.chat_history[user_id]) > MAX_HISTORY * 2:
            self.chat_history[user_id] = self.chat_history[user_id][-MAX_HISTORY * 2:]

    async def _setup_webhook(self):
        webhook_url = os.getenv("WEBHOOK_URL") + '/webhook'
        await self.application.bot.set_webhook(
            url=webhook_url,
            max_connections=50,
            allowed_updates=["message", "voice", "business_message"]
        )

        logger.info(f"Вебхук настроен: {webhook_url}")

    def process_update(self, json_data):
        """Обработка обновления"""
        if not self.initialized.wait(timeout=self.init_timeout):
            raise RuntimeError("Таймаут инициализации")
        
        future = asyncio.run_coroutine_threadsafe(
            self._process_update(json_data),
            self.loop
        )
        return future.result(timeout=15)

    async def _process_update(self, json_data):
        """Асинхронная обработка"""
        update = Update.de_json(json_data, self.application.bot)
        await self.application.process_update(update)

# Инициализация бота
bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        if not bot_manager.initialized.is_set():
            return jsonify({"status": "initializing"}), 503
            
        bot_manager.process_update(request.get_json())
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error"}), 500

@app.route('/')
def home():
    return "Telegram Bot is running!"

if __name__ == '__main__':
    if "RENDER" in os.environ:
        from waitress import serve
        serve(app, host="0.0.0.0", port=10000)
    else:
        app.run(host='0.0.0.0', port=5000)

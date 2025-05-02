import os
import asyncio
import logging
import threading
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from tempfile import NamedTemporaryFile
from telegram import Update
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
AUTO_GENERATION_KEYWORDS = ["сгенерируй", "покажи", "фото", "фотку", "картинк", "изображен"]

class BotManager:
    def __init__(self):
        self.loop = None
        self.application = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.initialized = threading.Event()
        self.openai_client = None
        self.chat_history = defaultdict(list)
        self.init_timeout = 120
        self.start_time = None
        
        self.owner_info = {
            "owner_name": "Сергей",
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
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_text
        ))
        self.application.add_handler(MessageHandler(
            self._business_message_filter(),
            self._handle_business_text
        ))
        self.application.add_error_handler(self._error_handler)
        
        await self.application.initialize()
        
        if "RENDER" in os.environ:
            await self._setup_webhook()

    def _business_message_filter(self):
        """Фильтр для бизнес-сообщений"""
        return (
            filters.UpdateType.MESSAGE &
            filters.Message(filters.TEXT) &
            filters.Lambda(lambda upd: bool(upd.message.business_connection_id)
        ))

    async def _log_incoming_message(self, update: Update):
        """Логирование входящих сообщений"""
        try:
            message_data = {
                "update_id": update.update_id,
                "message_id": update.message.message_id if update.message else None,
                "date": update.message.date.isoformat() if update.message and update.message.date else None,
                "chat_type": "business" if update.message and update.message.business_connection_id else "regular",
                "chat_id": update.effective_chat.id if update.effective_chat else None,
                "user_id": update.effective_user.id if update.effective_user else None,
                "content_type": "voice" if update.message and update.message.voice else "text",
                "content": update.message.text if update.message else "<unknown>"
            }
            logger.info("INCOMING MESSAGE: %s", json.dumps(message_data, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Ошибка логирования сообщения: {str(e)}", exc_info=True)

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._process_common_message(update, context, is_business=False)

    async def _handle_business_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._process_common_message(update, context, is_business=True)

    async def _process_common_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, is_business: bool):
        try:
            await self._log_incoming_message(update)
            
            if not update.message or not update.message.text:
                return

            user_id = update.effective_user.id
            text = update.message.text.strip()
            logger.debug(f"Обработка {'бизнес-' if is_business else ''}сообщения от {user_id}: {text}")

            # Автоматическая генерация изображения
            if any(kw in text.lower() for kw in AUTO_GENERATION_KEYWORDS):
                prompt = text + " в стиле профессиональной фотографии"
                await self._generate_image_from_text(update, prompt)
                return

            # Ответ через GPT
            response = await self._process_text(user_id, text)
            await update.message.reply_text(response)

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {str(e)}", exc_info=True)
            if update.message:
                await update.message.reply_text("⚠️ Ошибка обработки сообщения")

    async def _generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await self._log_incoming_message(update)
            
            if not context.args:
                await update.message.reply_text("ℹ️ Формат команды: /generate_image <описание>")
                return
                
            prompt = ' '.join(context.args)
            logger.info(f"Запрос генерации изображения: {prompt}")
            
            response = await self.openai_client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )
            image_url = response.data[0].url
            await update.message.reply_photo(image_url)
            logger.info(f"Изображение сгенерировано: {image_url}")
            
        except Exception as e:
            logger.error(f"Ошибка генерации изображения: {str(e)}", exc_info=True)
            await update.message.reply_text("⚠️ Не удалось сгенерировать изображение")

    async def _generate_image_from_text(self, update: Update, prompt: str):
        """Генерация изображения по текстовому запросу"""
        try:
            context = ContextTypes.DEFAULT_TYPE(application=self.application, update=update)
            context.args = prompt.split()
            await self._generate_image(update, context)
        except Exception as e:
            logger.error(f"Auto-generation error: {str(e)}")
            await update.message.reply_text("⚠️ Не удалось обработать запрос на генерацию")

    async def _handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await self._log_incoming_message(update)
            
            if not update.message.voice:
                return

            logger.debug("Обработка голосового сообщения")
            voice_file = await update.message.voice.get_file()
            
            with NamedTemporaryFile(delete=True, suffix=".ogg") as temp_file:
                await voice_file.download_to_drive(temp_file.name)
                
                transcript = await self.openai_client.audio.transcriptions.create(
                    file=open(temp_file.name, "rb"),
                    model="whisper-1",
                    response_format="text"
                )
                logger.info(f"Транскрипция: {transcript}")
                
                if any(kw in transcript.lower() for kw in AUTO_GENERATION_KEYWORDS):
                    await self._generate_image_from_text(update, transcript)
                else:
                    response = await self._process_text(update.effective_user.id, transcript)
                    await update.message.reply_text(f"🎤 Распознано: {transcript}\n\n📝 Ответ: {response}")
            
        except Exception as e:
            logger.error(f"Ошибка обработки голоса: {str(e)}", exc_info=True)
            await update.message.reply_text("⚠️ Ошибка распознавания голоса")

    async def _process_text(self, user_id: int, text: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(**self.owner_info)},
            *self.chat_history[user_id][-MAX_HISTORY:],
            {"role": "user", "content": text}
        ]
        
        logger.debug(f"GPT запрос: {json.dumps(messages, ensure_ascii=False)}")
        
        completion = await self.openai_client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=messages,
            temperature=0.7
        )
        
        response = completion.choices[0].message.content
        self._update_history(user_id, text, response)
        logger.debug(f"GPT ответ: {response[:100]}...")
        return response

    def _update_history(self, user_id: int, text: str, response: str):
        self.chat_history[user_id].extend([
            {"role": "user", "content": text},
            {"role": "assistant", "content": response}
        ])
        if len(self.chat_history[user_id]) > MAX_HISTORY * 2:
            self.chat_history[user_id] = self.chat_history[user_id][-MAX_HISTORY * 2:]
        logger.debug(f"История обновлена для пользователя {user_id}")

    async def _setup_webhook(self):
        webhook_url = os.getenv("WEBHOOK_URL") + '/webhook'
        await self.application.bot.set_webhook(
            url=webhook_url,
            max_connections=50,
            allowed_updates=["message", "voice", "business_message"]
        )
        logger.info(f"Вебхук настроен: {webhook_url}")

    def process_update(self, json_data):
        """Обработка обновления с увеличенным таймаутом"""
        logger.debug(f"Получено обновление: {json.dumps(json_data, indent=2)}")
        
        if not self.initialized.wait(timeout=self.init_timeout):
            raise RuntimeError(f"Таймаут инициализации ({self.init_timeout} сек)")
        
        future = asyncio.run_coroutine_threadsafe(
            self._process_update(json_data),
            self.loop
        )
        return future.result(timeout=30)

    async def _process_update(self, json_data):
        try:
            logger.debug("Начало обработки обновления")
            update = Update.de_json(json_data, self.application.bot)
            await self.application.process_update(update)
            logger.debug("Обработка обновления завершена")
        except Exception as e:
            logger.error(f"Ошибка обработки обновления: {str(e)}", exc_info=True)

    async def _error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Необработанная ошибка: {context.error}", exc_info=True)
        if update and update.message:
            await update.message.reply_text("⚠️ Произошла внутренняя ошибка")

# Инициализация бота
bot_manager = BotManager()
bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        logger.info("Входящий вебхук запрос. Заголовки: %s", request.headers)
        bot_manager.process_update(request.get_json())
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}", exc_info=True)
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

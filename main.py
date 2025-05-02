import os
import asyncio
import logging
from collections import defaultdict
from typing import Dict, List
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
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
MAX_HISTORY = 3  # Храним 3 последних сообщения
SYSTEM_PROMPT = """
Ты - это я, {owner_name}. Отвечай от моего имени, используя мой стиль общения.
Основные характеристики:
- {owner_style}
- {owner_details}

Всегда придерживайся этих правил:
1. Отвечай только от моего лица (используй "я", "мне" и т.д.)
2. Сохраняй мой стиль общения
3. Будь естественным, как будто это действительно я отвечаю
"""

class BotManager:
    def __init__(self):
        # ... (предыдущий код инициализации)
        self.chat_history: Dict[int, List[Dict]] = defaultdict(list)
        self.owner_info = {
                "owner_name": "Сергей Кажарнович",
    "owner_style": "Спокойный, дружелюбный, уверенный в себе, использую лёгкий юмор и уместный сарказм, если нужно — могу быть прямым.",
    "owner_details": "Предпочитаю говорить по делу, но умею развить мысль. Ценю структурированные подходы, часто предлагаю решения и иду на шаг вперёд. Готов делиться опытом и вовлекать других в процесс, если вижу в этом смысл."
        }

    async def _get_gpt_response(self, user_id: int, message: str) -> str:
        """Генерация ответа с учетом контекста"""
        try:
            # Получаем историю сообщений
            history = self.chat_history.get(user_id, [])
            
            # Формируем промпт
            messages = [
                {
                    "role": "system", 
                    "content": SYSTEM_PROMPT.format(**self.owner_info)
                }
            ]
            
            # Добавляем историю (не более MAX_HISTORY сообщений)
            messages.extend(history[-MAX_HISTORY:])
            
            # Добавляем текущее сообщение
            messages.append({"role": "user", "content": message})
            
            # Запрос к OpenAI
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=messages,
                temperature=0.7
            )
            
            response = completion.choices[0].message.content
            
            # Обновляем историю
            self._update_history(user_id, message, response)
            
            return response
        except Exception as e:
            logger.error(f"Ошибка OpenAI: {str(e)}")
            return "Извините, не могу обработать запрос. Попробуйте позже."

    def _update_history(self, user_id: int, user_message: str, bot_response: str):
        """Обновление истории сообщений"""
        if user_id not in self.chat_history:
            self.chat_history[user_id] = []
        
        # Добавляем сообщение пользователя и ответ бота
        self.chat_history[user_id].extend([
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": bot_response}
        ])
        
        # Ограничиваем размер истории
        if len(self.chat_history[user_id]) > MAX_HISTORY * 2:
            self.chat_history[user_id] = self.chat_history[user_id][-MAX_HISTORY * 2:]

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик входящих сообщений с учетом контекста"""
        try:
            message = update.message or update.business_message
            user_id = message.from_user.id
            text = message.text
            
            logger.info(f"Сообщение от {user_id}: {text}")
            
            response = await self._get_gpt_response(user_id, text)
            await message.reply_text(response)
            
        except Exception as e:
            logger.error(f"Ошибка обработки: {str(e)}", exc_info=True)
            await message.reply_text("⚠️ Произошла ошибка, попробуйте позже")

# ... (остальной код остается без изменений)

    async def _get_gpt_response(self, user_id: int, message: str) -> str:
        """Запрос к OpenAI с обработкой ошибок"""
        try:
            completion = await self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу."},
                    {"role": "user", "content": message}
                ],
                temperature=0.7
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка OpenAI: {str(e)}")
            return "Извините, не могу обработать запрос. Попробуйте позже."

    async def _setup_webhook(self):
        """Настройка вебхука для бизнес-аккаунта"""
        webhook_url = os.getenv("WEBHOOK_URL") + '/webhook'
        await self.application.bot.set_webhook(
            url=webhook_url,
            max_connections=50,
            allowed_updates=["message", "business_message"],
            drop_pending_updates=True
        )
        logger.info(f"Вебхук настроен: {webhook_url}")

    def process_update(self, json_data):
        """Потокобезопасная обработка обновления"""
        if not self.initialized.wait(timeout=self.init_timeout):
            raise RuntimeError(f"Таймаут инициализации ({self.init_timeout} сек)")
        
        future = asyncio.run_coroutine_threadsafe(
            self._process_update(json_data),
            self.loop
        )
        return future.result(timeout=15)

    async def _process_update(self, json_data):
        """Асинхронная обработка обновления"""
        update = Update.de_json(json_data, self.application.bot)
        await self.application.process_update(update)

# Инициализация бота при старте приложения
bot_manager = BotManager()
#bot_manager.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработчик вебхука Telegram"""
    try:
        if not bot_manager.initialized.is_set():
            return jsonify({"status": "error", "message": "Bot is initializing"}), 503
            
        bot_manager.process_update(request.get_json())
        return jsonify({"status": "ok"})
        
    except Exception as e:
        logger.error(f"Ошибка вебхука: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def home():
    """Статусная страница"""
    status = "running" if bot_manager.initialized.is_set() else "initializing"
    return f"Telegram Bot Status: {status}"

if __name__ == '__main__':
    if "RENDER" in os.environ:
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=10000)
        except ImportError:
            app.run(host='0.0.0.0', port=10000)
    else:
        app.run(host='0.0.0.0', port=5000)

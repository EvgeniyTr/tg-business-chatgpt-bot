import os
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from flask import Flask, request

app = Flask(__name__)
bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))

# Обработчик сообщений
async def handle_message(update: Update, context):
    await update.message.reply_text("Привет! Я работаю правильно!")

# Веб-интерфейс для Render
@app.route('/')
def home():
    return "Telegram Bot is running!"

# Вебхук для Telegram
@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    application = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.process_update(update)
    return "OK"

if __name__ == '__main__':
    # Настройка для Render
    if "RENDER" in os.environ:
        # Устанавливаем вебхук
        bot.set_webhook(url=os.getenv("WEBHOOK_URL") + '/webhook')
        # Запускаем Flask сервер
        app.run(host='0.0.0.0', port=os.getenv("PORT", 5000))
    else:
        # Локальный режим с polling
        application = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
        application.add_handler(MessageHandler(filters.TEXT, handle_message))
        application.run_polling()

import os
from telegram import Bot
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from flask import Flask, request

app = Flask(__name__)
bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))

async def handle_message(update, context):
    await update.message.reply_text("Бот работает!")

@app.route('/')
def home():
    return "Telegram Bot is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(), bot)
    ApplicationBuilder().build().process_update(update)
    return "OK"

if __name__ == '__main__':
    # Настройка вебхука
    if "RENDER" in os.environ:
        bot.set_webhook(url=os.getenv("WEBHOOK_URL") + '/webhook')
        app.run(host='0.0.0.0', port=os.getenv("PORT", 5000))
    else:
        # Локальный режим
        application = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
        application.add_handler(MessageHandler(filters.TEXT, handle_message))
        application.run_polling()

import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI
from flask import Flask, request

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # полный URL вида https://telegram-chatgpt-bot-6jg3.onrender.com/

client = OpenAI(api_key=OPENAI_KEY)
app = ApplicationBuilder().token(TELEGRAM_TOKEN).webhook_url(WEBHOOK_URL).build()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    print(f"[LOG] {update.effective_user.full_name}: {user_message}")

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Отвечай кратко, от имени пользователя."},
                {"role": "user", "content": user_message}
            ]
        )
        reply = response.choices[0].message.content
        await update.message.reply_text(reply)

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Flask обёртка для Render
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return 'Бот работает!'

@flask_app.route('/webhook', methods=['POST'])
async def telegram_webhook():
    return await app.update_queue.put(request.get_data(as_text=True)), 200

if __name__ == "__main__":
    import asyncio
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    config = Config()
    config.bind = ["0.0.0.0:" + os.getenv("PORT", "10000")]
    asyncio.run(serve(flask_app, config))

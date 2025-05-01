import os
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler,
    CommandHandler, filters
)
from openai import OpenAI
from flask import Flask, request

# ENV vars
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", "8443"))

client = OpenAI(api_key=OPENAI_KEY)

# Flask app for Webhook route
flask_app = Flask(__name__)

# Telegram bot instance
telegram_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()


@telegram_app.message_handler(filters.TEXT & ~filters.COMMAND)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    print(f"[User] {update.effective_user.full_name}: {user_message}")

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Отвечай от имени пользователя, вежливо и кратко."},
                {"role": "user", "content": user_message}
            ]
        )
        reply = response.choices[0].message.content
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")


# Flask endpoint for Telegram Webhook
@flask_app.route("/webhook", methods=["POST"])
async def webhook():
    await telegram_app.update_queue.put(Update.de_json(request.json, telegram_app.bot))
    return "ok", 200


if __name__ == "__main__":
    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
        web_app=flask_app
    )

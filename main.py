import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters
)
from openai_helper import get_gpt_response

# Настройка логов
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = get_gpt_response(update.message.text)
        await update.message.reply_text(response)
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуйте позже.")

def main():
    app = ApplicationBuilder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Для Render: "прослушка" порта
    if "RENDER" in os.environ:
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 5000)),
            secret_token='WEBHOOK_SECRET',
            webhook_url=os.environ.get("WEBHOOK_URL")
        )
    else:
        app.run_polling()

if __name__ == '__main__':
    main()

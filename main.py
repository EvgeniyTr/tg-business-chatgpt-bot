import os
import logging
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from config import Config
from app.handlers import handle_message

# Настройка логов
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def main():
    app = ApplicationBuilder().token(Config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Для Render: используем вебхук или polling
    if "RENDER" in os.environ:
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 5000)),
            webhook_url=os.environ.get("WEBHOOK_URL")
        )
    else:
        app.run_polling()

if __name__ == '__main__':
    main()

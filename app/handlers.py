from telegram import Update
from telegram.ext import ContextTypes
from app.services import get_gpt_response

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = get_gpt_response(update.message.text)
    await update.message.reply_text(response)

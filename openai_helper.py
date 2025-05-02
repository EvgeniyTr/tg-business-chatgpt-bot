import openai
import os
from dotenv import load_dotenv

load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")

def get_gpt_response(user_message: str, chat_history: list = None) -> str:
    messages = [{"role": "system", "content": "Ты ассистент в Telegram. Отвечай кратко и вежливо."}]
    
    if chat_history:
        messages.extend(chat_history)
    
    messages.append({"role": "user", "content": user_message})
    
    response = openai.ChatCompletion.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4-turbo-preview"),
        messages=messages,
        temperature=0.7
    )
    return response.choices[0].message.content

import openai
from config import Config

openai.api_key = Config.OPENAI_API_KEY

def get_gpt_response(message: str) -> str:
    try:
        response = openai.ChatCompletion.create(
            model=Config.OPENAI_MODEL,
            messages=[{"role": "user", "content": message}],
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"⚠️ Ошибка GPT: {str(e)}"

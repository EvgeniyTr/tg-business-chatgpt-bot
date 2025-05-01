# Telegram GPT Auto-Reply Bot (Business-ready)

Автоматический Telegram-бот с интеграцией OpenAI GPT-3.5 и поддержкой Webhook для Telegram Business аккаунтов.

## 🚀 Возможности
- Принимает входящие сообщения как автоответчик
- Поддерживает Telegram Business режим
- Работает через Webhook (aiohttp)
- Генерирует ответы с помощью OpenAI

---

## 📁 Установка и запуск

### 1. Клонируй репозиторий и установи зависимости
```bash
pip install -r requirements.txt
```

### 2. Создай файл `.env` (можно скопировать из `.env.example`)
```env
TELEGRAM_TOKEN=your_bot_token
OPENAI_KEY=your_openai_api_key
WEBHOOK_URL=https://your-app-name.onrender.com
PORT=8080
```

### 3. Запусти бота
```bash
python bot.py
```

---

## 🌐 Деплой на Render

### 🔧 Settings:
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python bot.py`
- **Environment Variables**:
  - `TELEGRAM_TOKEN`
  - `OPENAI_KEY`
  - `WEBHOOK_URL`
  - `PORT` *(обычно 10000 в Render)*

После деплоя — не забудь вручную установить Webhook:

```bash
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://your-app-name.onrender.com/webhook
```

---

## 💬 Поддержка моделей
- GPT-3.5-turbo
- Поддержка других моделей доступна при необходимости через OpenAI API

## 🧠 Используемые технологии
- `aiogram 3`
- `aiohttp`
- `openai`

---

**Автор:** @kozhariks

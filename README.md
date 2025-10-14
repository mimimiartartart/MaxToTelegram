# MaxToTelegram

Инструмент для ретрансляции сообщений и вложений из Max в Telegram.  
Работает через WebSocket API OneMe, пересылает текст и медиафайлы в выбранный Telegram-чат или канал.

---

## Возможности

- Скрипт подключается к WebSocket `wss://ws-api.oneme.ru/websocket` по токену MAX.
- Обработка личных и групповых сообщений.
- Поддержка вложений:
  - изображения (jpg, png, gif, webp);
  - документы (pdf, docx, txt и др.).
- Сохранение входящих фреймов в `frames.log` для анализа.
- Локальное сохранение вложений в каталог `attachments/`.
- Пересылка текста и файлов в Telegram-чат.
- Хранение информации о названиях чатов и именах отправителей.
- Переподписка на чаты и автоматическое восстановление соединения при обрыве.

---

## Запуск

  ```py
  python main.py
  ```

---

## Установка

1. Клонировать репозиторий:
   ```bash
   git clone https://github.com/mimimiartartart/MaxToTelegram.git
   cd MaxToTelegram
   ```
2. Установка зависимостей:
   ```bash
   pip install -r requirements.txt
   ```
3. Настройте .env:
   ```env
   MAX_TOKEN = "your_max_token"
   TELEGRAM_BOT_TOKEN = "your_telegram_bot_token"
   TELEGRAM_CHAT_ID = "your_chat_id"
   UA_HEADER="your-user-agent-data"
   DEVICE_ID="uuid4"
   HEARTBEAT_INTERVAL=20
   RESUBSCRIBE_INTERVAL=300
   SUBSCRIBE_LIMIT=500
   ```

   


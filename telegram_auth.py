"""
Одноразовая авторизация Telethon.
Запусти в PyCharm-терминале: python telegram_auth.py
Спросит телефон и код — введи, больше не понадобится.
"""
import os
import sys

import telethon.sync  # noqa: F401 — без этого client.get_me() возвращает корутину, а не пользователя
from telethon import TelegramClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from settings import get_tg_credentials, TG_SESSION_FILE as SESSION_FILE  # те же ключи и та же сессия, что у main.py

api_id, api_hash = get_tg_credentials()

if not api_id or not api_hash:
    print("❌ Нет TG_API_ID / TG_API_HASH. Впиши их в файл .env (шаблон — .env.example).")
    sys.exit(1)

print(f"Авторизация (api_id={api_id})...")
with TelegramClient(SESSION_FILE, api_id, api_hash) as client:
    me = client.get_me()
    print(f"👤 Вошёл как: {me.first_name} (@{me.username})")
print("✅ Готово. Сессия сохранена в", SESSION_FILE + ".session")

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))  # ID админов через запятую
OWNER_ID = int(os.getenv("OWNER_ID"))  # Владелец бота
FLUD_CHAT_ID = int(os.getenv("FLUD_CHAT_ID"))  # ID чата для уведомлений

# Цены в каталоге
PRICES = {
    "change_role": 1000,
    "anti_warn": 5000,
    "immunity": 10000,
    "video": 1000,
    "unban": 200000,
    "admin": 50000
}
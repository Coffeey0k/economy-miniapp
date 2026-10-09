import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",") if os.getenv("ADMIN_IDS") else []))
OWNER_ID = int(os.getenv("OWNER_ID"))
FLUD_CHAT_ID = int(os.getenv("FLUD_CHAT_ID"))

# Тех.админы — «пизже влада». Всё могут, всё видят, ничего не подтверждают.
TECH_ADMIN_IDS = list(map(int, os.getenv("TECH_ADMIN_IDS", "").split(",") if os.getenv("TECH_ADMIN_IDS") else []))

# Цены в каталоге (fallback, если БД пуста)
PRICES = {
    "change_role": 1000,
    "anti_warn": 5000,
    "immunity": 10000,
    "video": 1000,
    "unban": 200000,
    "admin": 50000,
}


# ============ РОЛИ ============

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def is_tech_admin(user_id: int) -> bool:
    return user_id in TECH_ADMIN_IDS


def is_admin(user_id: int) -> bool:
    """Обычный админ ИЛИ тех.админ — оба имеют доступ к админ-панели."""
    return user_id in ADMIN_IDS or user_id in TECH_ADMIN_IDS


def is_full_admin(user_id: int) -> bool:
    """Полный доступ (админ или тех.админ) — для проверок в коде."""
    return is_admin(user_id) or is_owner(user_id)


def can_see_logs(user_id: int) -> bool:
    """Логи действий видят ТОЛЬКО тех.админы."""
    return user_id in TECH_ADMIN_IDS


def can_manage_tech(user_id: int) -> bool:
    """Управление тех.панелью — только тех.админы."""
    return user_id in TECH_ADMIN_IDS

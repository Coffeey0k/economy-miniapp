"""
Backend для Telegram Mini App бота Paradise Reef.

Что делает:
1. Проверяет подлинность данных, которые Telegram передаёт из Mini App (initData),
   чтобы никто не мог подделать запрос и залезть в чужой профиль.
2. Читает баланс/статистику/питомцев/профессию/покупки из paradise.db —
   той же базы, с которой работает сам бот (см. database.py).
3. Отдаёт всё это фронтенду в виде JSON.

Backend только ЧИТАЕТ данные (не списывает и не начисляет монеты) — вся игровая
логика по-прежнему остаётся в самом боте (bot.py / games.py / database.py).
Это безопаснее: мини-приложение не может напрямую менять баланс.
"""

import hashlib
import hmac
import os
import sqlite3
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ========== НАСТРОЙКИ ==========

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8200225929:AAGrgCFadMks9-I-O6Dhtcssd2eNmqpN8mA")
# Тот же файл базы, что использует бот (см. DB_NAME в database.py)
DB_PATH = os.environ.get("DB_PATH", "paradise.db")

PROFESSIONS = {
    "artist": "🎨 Художник",
    "musician": "🎸 Музыкант",
    "photographer": "📸 Фотограф",
    "gardener": "🌿 Садовник",
    "cook": "🍳 Повар",
    "vet": "🩺 Ветеринар",
    "programmer": "💻 Программист",
    "ruler": "👑 Правитель Paradise Reef",
}

CATALOG_NAMES = {
    "change_role": "🔁 Смена роли",
    "anti_warn": "🛡️ Антиварн",
    "immunity": "🛡️ Иммунитет на чистке",
    "video": "📹 Видео с вами",
    "unban": "🔓 Разбан",
    "admin": "👑 Админка",
}

app = FastAPI()

# Разреши запросы со своего домена (когда захостишь фронтенд, впиши его сюда вместо "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== ПРОВЕРКА TELEGRAM INIT DATA ==========

def validate_init_data(init_data: str, bot_token: str) -> dict:
    """Проверяет подпись initData, которую передаёт Telegram.WebApp.

    Если подпись неверна — значит запрос не от настоящего Telegram, отклоняем.
    Возвращает распарсенные поля (в т.ч. user) если всё ок.
    """
    parsed = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Нет подписи в initData")

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if calculated_hash != received_hash:
        raise HTTPException(status_code=401, detail="Неверная подпись initData")

    return parsed


def get_telegram_user_id(init_data: str) -> int:
    import json
    parsed = validate_init_data(init_data, BOT_TOKEN)
    user_json = parsed.get("user")
    if not user_json:
        raise HTTPException(status_code=401, detail="Нет данных пользователя")
    user = json.loads(user_json)
    return user["id"]


# ========== ЧТЕНИЕ ДАННЫХ ИЗ paradise.db ==========

def fetch_profile(user_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # --- базовые данные пользователя ---
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user_row = cur.fetchone()
    if not user_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # --- место в топе ---
    cur.execute(
        "SELECT COUNT(*) + 1 FROM users WHERE balance > ? AND is_banned = 0",
        (user_row["balance"],),
    )
    rank = cur.fetchone()[0]

    # --- предупреждения ---
    cur.execute("SELECT warn_count FROM warns WHERE user_id = ?", (user_id,))
    warn_row = cur.fetchone()
    warn_count = warn_row["warn_count"] if warn_row else 0

    # --- статистика игр ---
    cur.execute(
        "SELECT games_played, games_won, total_earned, total_lost "
        "FROM game_stats WHERE user_id = ?",
        (user_id,),
    )
    stats_row = cur.fetchone()
    stats = {
        "Сыграно игр": stats_row["games_played"] if stats_row else 0,
        "Побед": stats_row["games_won"] if stats_row else 0,
        "Заработано": stats_row["total_earned"] if stats_row else 0,
        "Проиграно": stats_row["total_lost"] if stats_row else 0,
    }

    # --- профессия ---
    cur.execute(
        "SELECT profession, level FROM professions WHERE user_id = ?", (user_id,)
    )
    prof_row = cur.fetchone()
    profession = None
    if prof_row and prof_row["profession"]:
        profession = {
            "name": PROFESSIONS.get(prof_row["profession"], prof_row["profession"]),
            "level": prof_row["level"],
        }

    # --- питомцы ---
    cur.execute(
        "SELECT pet_name, rarity, income, is_active FROM pets WHERE user_id = ?",
        (user_id,),
    )
    pets = [
        {
            "name": r["pet_name"],
            "rarity": r["rarity"],
            "income": r["income"],
            "is_active": bool(r["is_active"]),
        }
        for r in cur.fetchall()
    ]

    # --- купленные услуги (каталог) ---
    cur.execute(
        "SELECT item_type, is_used FROM purchases WHERE user_id = ? "
        "ORDER BY purchased_at DESC",
        (user_id,),
    )
    purchases = [
        {
            "name": CATALOG_NAMES.get(r["item_type"], r["item_type"]),
            "is_used": bool(r["is_used"]),
        }
        for r in cur.fetchall()
    ]

    conn.close()

    return {
        "user_id": user_id,
        "username": user_row["username"],
        "balance": user_row["balance"],
        "rank": rank,
        "warns": warn_count,
        "profession": profession,
        "stats": stats,
        "pets": pets,
        "purchases": purchases,
    }


# ========== API ==========

@app.get("/api/profile")
def get_profile(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    return fetch_profile(user_id)


@app.get("/")
def health():
    return {"status": "ok"}

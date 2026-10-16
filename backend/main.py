"""
Backend для Telegram Mini App бота Lavender Dream.

Что делает:
1. Проверяет подлинность данных, которые Telegram передаёт из Mini App (initData),
   чтобы никто не мог подделать запрос и залезть в чужой профиль/баланс.
2. Читает и обновляет paradise.db — ту же базу, с которой работает сам бот.
3. Повторяет игровую логику бота (database.py) для действий, доступных из
   мини-приложения: покупки в каталоге, открытие яиц, профессии, колесо
   фортуны, бонус, переводы.

ЧТО ОСТАЁТСЯ ТОЛЬКО В БОТЕ (специально не переносим сюда):
- Азартные игры (слот/кубик/монетка/угадай число) — там ставки и честная
  случайность, безопаснее держать в одном месте (боте), а не дублировать
  в двух системах.
- Админ-панель — все действия с чужими балансами должны проверять роль
  администратора на сервере, а не только в интерфейсе.
- Промокоды — по задумке активируются только в ЛС с ботом.
- Покупки, требующие подтверждения владельца (unban, admin) — их и здесь
  можно "заказать", но подтверждает их по-прежнему владелец в боте.

Важно: несколько человек могут одновременно писать в paradise.db (бот +
это API). SQLite неплохо с этим справляется для нечастых операций, но
это стоит держать в голове при высокой нагрузке.
"""

import hashlib
import hmac
import json
import logging
import os
import random
import time
import httpx
import config
import db_compat as sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl
from db_compat import init_db
import database as bot_db   # <— для log_action / get_price / is_test_mode

logger = logging.getLogger(__name__)
init_db()
bot_db.DB_NAME = os.environ.get("DB_PATH", bot_db.DB_NAME)
bot_db.init_db()  # Ensure boost/event tables exist even when the API starts before the bot.

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ========== НАСТРОЙКИ ==========

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_БОТА")
# Тот же файл базы, что использует бот (см. DB_NAME в database.py)
DB_PATH = os.environ.get("DB_PATH", "paradise.db")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]

PROFESSIONS = {
    "artist": "🎨 Художник",
    "musician": "🎸 Музыкант",
    "photographer": "📸 Фотограф",
    "gardener": "🌿 Садовник",
    "cook": "🍳 Повар",
    "vet": "🩺 Ветеринар",
    "programmer": "💻 Программист",
    "ruler": "👑 Правитель Lavender Dream",
}

CATALOG_NAMES = {
    "change_role": "🔁 Смена роли",
    "anti_warn": "🛡️ Антиварн",
    "immunity": "🛡️ Иммунитет на чистке",
    "video": "📹 Видео с вами",
    "unban": "🔓 Разбан",
    "admin": "👑 Админка",
}

# Покупка этих товаров требует ручного подтверждения владельца бота —
# из мини-аппа их не выдаём мгновенно, отправляем предупреждение.
CONFIRMATION_REQUIRED = {"unban", "admin"}

PROFESSION_INFO = {
    "artist": {"name": "🎨 Художник", "salary": 500, "cooldown_hours": 2},
    "musician": {"name": "🎸 Музыкант", "salary": 500, "cooldown_hours": 2},
    "photographer": {"name": "📸 Фотограф", "salary": 1000, "cooldown_hours": 3},
    "gardener": {"name": "🌿 Садовник", "salary": 1000, "cooldown_hours": 3},
    "cook": {"name": "🍳 Повар", "salary": 1000, "cooldown_hours": 3},
    "vet": {"name": "🩺 Ветеринар", "salary": 1200, "cooldown_hours": 4},
    "programmer": {"name": "💻 Программист", "salary": 1400, "cooldown_hours": 5},
    "ruler": {"name": "👑 Правитель Lavender Dream", "salary": 10000, "cooldown_hours": 12},
}
HIRE_COST = 1000
FIRE_EXTRA_COST = 1000

PET_EGGS = {
    "ordinary": {"price": 1000, "name": "🥚 Обычное яйцо",
        "pets": [("🐹 Хомячок", "обычный", 100, 70), ("🐰 Кролик", "необычный", 150, 15),
                 ("🐱 Котёнок", "редкий", 200, 7), ("🦊 Лисёнок", "эпический", 250, 6),
                 ("🦄 Маленький единорог", "легендарный", 300, 2)]},
    "sea": {"price": 30000, "name": "🌊 Морское яйцо",
        "pets": [("🐠 Рыбка", "обычный", 100, 70), ("🐙 Осьминожек", "необычный", 150, 15),
                 ("🦀 Крабик", "редкий", 200, 7), ("🐬 Дельфинёнок", "эпический", 250, 6),
                 ("🧜‍♀️ Морской котёнок", "легендарный", 300, 2)]},
    "tropical": {"price": 60000, "name": "🌴 Тропическое яйцо",
        "pets": [("🦜 Попугайчик", "обычный", 100, 70), ("🦎 Ящерка", "необычный", 150, 15),
                 ("🐒 Обезьянка", "редкий", 200, 7), ("🦩 Фламинго", "эпический", 250, 6),
                 ("🐆 Радужный леопард", "легендарный", 300, 2)]},
    "mystic": {"price": 150000, "name": "🔮 Мистическое яйцо",
        "pets": [("🐈 Лунный кот", "обычный", 100, 70), ("🦋 Ночная бабочка", "необычный", 150, 15),
                 ("🐺 Серебряный волк", "редкий", 200, 7), ("🦌 Лунный олень", "эпический", 250, 6),
                 ("🐉 Дракон бездны", "легендарный", 300, 2)]},
    "legendary": {"price": 500000, "name": "⭐ Легендарное яйцо",
        "pets": [("🦊 Кристальный лис", "обычный", 100, 70), ("🐺 Звёздный волк", "необычный", 150, 15),
                 ("🦄 Радужный единорог", "редкий", 200, 7), ("🐉 Небесный дракон", "эпический", 250, 6),
                 ("🌌 Космический феникс", "легендарный", 300, 2)]},
    "divine": {"price": 1000000, "name": "✨ Божественное яйцо",
        "pets": [("🐈‍⬛ Тёмная луна", "обычный", 100, 70), ("🦋 Звёздная бабочка", "необычный", 150, 15),
                 ("🦌 Небесный олень", "редкий", 200, 7), ("🐉 Императорский дракон", "легендарный", 300, 30)]},
}

DAILY_TASK_NAMES = {
    "activist":     "💬 Активист",
    "resident":     "🌴 Житель Lavender Dream",
    "friendly":     "🤍 Дружелюбный",
    "photographer": "📸 Фотограф",
    "musician":     "🎵 Музыкант",
    "night_owl":    "🌙 Ночной житель",
}

DAILY_TASK_DESCS = {
    "activist":     "Отправь 30 сообщений в чат",
    "resident":     "Отправь 50 сообщений в чат",
    "friendly":     "Ответь (реплаем) на 5 разных сообщений",
    "photographer": "Отправь фото в чат",
    "musician":     "Отправь музыку или голосовое в чат",
    "night_owl":    "Напиши сообщение после 20:00",
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

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Неверная подпись initData")

    # Не принимаем украденные/устаревшие initData бесконечно долго.
    try:
        auth_timestamp = int(parsed.get("auth_date", ""))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Нет корректной даты авторизации")
    now_timestamp = int(datetime.now().timestamp())
    if auth_timestamp > now_timestamp + 60:
        raise HTTPException(status_code=401, detail="Некорректная дата авторизации")
    if now_timestamp - auth_timestamp > 86400:
        raise HTTPException(status_code=401, detail="Данные авторизации устарели. Перезапустите Mini App")

    return parsed


def get_telegram_user_id(init_data: str) -> int:
    parsed = validate_init_data(init_data, BOT_TOKEN)
    user_json = parsed.get("user")
    if not user_json:
        raise HTTPException(status_code=401, detail="Нет данных пользователя")
    user = json.loads(user_json)
    return user["id"]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def require_balance(cur, user_id: int) -> int:
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return row["balance"]

def change_balance(cur, user_id: int, delta: int) -> bool:
    """Atomically change balance; never allow concurrent requests to overspend."""
    if delta < 0:
        try:
            if bot_db.is_test_mode(user_id):
                return True
        except Exception:
            logger.exception("Не удалось проверить тест-режим user_id=%s", user_id)

    # Preserve the 404 for unknown users before performing the atomic update.
    require_balance(cur, user_id)
    if delta < 0:
        cur.execute(
            "UPDATE users SET balance = balance + ? "
            "WHERE user_id = ? AND balance >= ?",
            (delta, user_id, -delta),
        )
    else:
        cur.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (delta, user_id),
        )
    return cur.rowcount > 0


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
        "SELECT id, pet_name, rarity, income, is_active FROM pets WHERE user_id = ?",
        (user_id,),
    )
    pets = [
        {
            "id": r["id"],
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

    # --- косметика ---
    cur.execute("""
        SELECT nickname_color, frame, status, theme FROM user_cosmetics WHERE user_id = %s
    """, (user_id,))
    cos_row = cur.fetchone()
    cosmetics = {
        "nickname_color": cos_row["nickname_color"] if cos_row else None,
        "frame": cos_row["frame"] if cos_row else None,
        "status": cos_row["status"] if cos_row else None,
        "theme": (cos_row["theme"] if cos_row and cos_row["theme"] else "reef"),
    }

    # --- значки ---
    cur.execute("SELECT badge FROM user_badges WHERE user_id = %s", (user_id,))
    badges = [r["badge"] for r in cur.fetchall()]
   
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
        "cosmetics": cosmetics,
        "badges": badges,
    }


# ========== API: ПРОФИЛЬ ==========

@app.get("/api/profile")
def get_profile(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    return fetch_profile(user_id)


# ========== API: ТОП ИГРОКОВ ==========

@app.get("/api/top")
def get_top(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, username, balance FROM users
        WHERE is_banned = 0 ORDER BY balance DESC LIMIT 10
    """)
    top = [
        {"user_id": r["user_id"], "username": r["username"], "balance": r["balance"]}
        for r in cur.fetchall()
    ]
    conn.close()
    return {"top": top, "your_id": user_id}


# ========== API: КАТАЛОГ ==========


# ========== ХЭЛЛОУИНСКИЙ ИВЕНТ 2026 ==========
HALLOWEEN_REWARD = 500

def _halloween_is_active() -> bool:
    # Start at midnight Moscow time, independent of the host server timezone.
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    return now.year == 2026 and now.month == 10 and now.day == 31


@app.get("/api/events/halloween")
def halloween_event_status(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    active = _halloween_is_active()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM halloween_claims WHERE user_id = ? AND event_key = 'halloween_2026'",
        (user_id,),
    )
    claimed = cur.fetchone() is not None
    conn.close()
    return {
        "active": active,
        "claimed": claimed,
        "reward": HALLOWEEN_REWARD,
        "title": "🎃 Хэллоуин в Lavender Dream",
        "description": "Загляни на хэллоуинский праздник и забери праздничные монеты!",
        "starts_at": "2026-10-31T00:00:00+03:00",
        "ends_at": "2026-11-01T00:00:00+03:00",
    }


@app.post("/api/events/halloween/claim")
def claim_halloween_event(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    if not _halloween_is_active():
        raise HTTPException(status_code=400, detail="Хэллоуинский ивент пока не активен")

    conn = db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO halloween_claims (user_id, event_key) VALUES (?, 'halloween_2026')",
            (user_id,),
        )
    except Exception:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Ты уже забрал хэллоуинскую награду")
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (HALLOWEEN_REWARD, user_id))
    conn.commit()
    conn.close()
    try:
        bot_db.log_action(user_id, "event", "Хэллоуин 2026: награда", amount=HALLOWEEN_REWARD)
    except Exception:
        logger.exception("Не удалось записать лог хэллоуинской награды")
    return {"ok": True, "amount": HALLOWEEN_REWARD, "message": f"🎃 Ты получил {HALLOWEEN_REWARD} 🪙!"}


@app.get("/api/catalog")
def get_catalog(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT item_type, name, price, description FROM catalog_items
        WHERE is_hidden = 0
    """)
    items = [dict(r) for r in cur.fetchall()]
    balance = require_balance(cur, user_id)
    conn.close()
    return {"items": items, "balance": balance}


@app.post("/api/catalog/buy")
def buy_catalog_item(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    item_type = payload.get("item_type")

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT name, price FROM catalog_items WHERE item_type = ? AND is_hidden = 0",
        (item_type,),
    )
    item = cur.fetchone()
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Товар не найден")

    price = item["price"]

    # These items require owner approval. Do not charge or insert a completed
    # purchase here: database.add_purchase() performs the charge on approval.
    if item_type in CONFIRMATION_REQUIRED:
        balance = require_balance(cur, user_id)
        conn.close()
        if balance < price:
            raise HTTPException(status_code=400, detail="Недостаточно монет")
        try:
            _notify_admins_purchase(user_id, item_type, price)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Не удалось уведомить администраторов о запросе покупки")
        return {
            "ok": True,
            "message": f"Запрос на покупку «{item['name']}» отправлен владельцу. Монеты спишутся только после подтверждения."
        }

    if not change_balance(cur, user_id, -price):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    cur.execute(
        "INSERT INTO purchases (user_id, item_type) VALUES (?, ?)", (user_id, item_type)
    )
    bot_db._grant_catalog_boost_cursor(cur, user_id, item_type)
    conn.commit()
    conn.close()

    # Логируем покупку
    try:
        bot_db.log_action(user_id, "purchase", f"Куплено (Mini App): {item['name']}", amount=price)
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")

    # Уведомляем всех админов
    try:
        _notify_admins_purchase(user_id, item_type, price)
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")

    if item_type in CONFIRMATION_REQUIRED:
        return {"ok": True, "message": f"Покупка «{item['name']}» оформлена, ждите подтверждения владельца в боте."}
    return {"ok": True, "message": f"Куплено: {item['name']} 🎉"}


# ========== API: ПИТОМЦЫ / ЯЙЦА ==========

@app.get("/api/eggs")
def get_eggs(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    balance = require_balance(cur, user_id)
    conn.close()
    eggs = [
        {"egg_type": key, "name": egg["name"], "price": egg["price"]}
        for key, egg in PET_EGGS.items()
    ]
    return {"eggs": eggs, "balance": balance}


@app.post("/api/eggs/open")
def open_egg(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    egg_type = payload.get("egg_type")
    egg = PET_EGGS.get(egg_type)
    if not egg:
        raise HTTPException(status_code=404, detail="Такого яйца нет")

    conn = db()
    cur = conn.cursor()
    if not change_balance(cur, user_id, -egg["price"]):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    pets = egg["pets"]
    total = sum(p[3] for p in pets)
    r = random.uniform(0, total)
    upto = 0
    chosen = pets[-1]
    for p in pets:
        if upto + p[3] >= r:
            chosen = p
            break
        upto += p[3]

    cur.execute(
        "INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income) VALUES (?, ?, ?, ?, ?)",
        (user_id, chosen[0], chosen[0].split()[0], chosen[1], chosen[2]),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "pet": {"name": chosen[0], "rarity": chosen[1], "income": chosen[2]}}


@app.post("/api/pets/toggle")
def toggle_pet(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    pet_id = payload.get("pet_id")
    active = bool(payload.get("active"))

    conn = db()
    cur = conn.cursor()
    if active:
        cur.execute(
            "SELECT COUNT(*) c FROM pets WHERE user_id = ? AND is_active = 1", (user_id,)
        )
        if cur.fetchone()["c"] >= 2:
            conn.close()
            raise HTTPException(status_code=400, detail="Можно держать активными не больше 2 питомцев")
    cur.execute(
        "UPDATE pets SET is_active = ? WHERE id = ? AND user_id = ?",
        (1 if active else 0, pet_id, user_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ========== API: ПРОФЕССИИ ==========

@app.get("/api/professions")
def get_professions(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    is_admin = config.is_full_admin(user_id)

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT profession, level, last_work, days_worked, total_changes "
        "FROM professions WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()

    current = None
    if row and row["profession"] and row["profession"] in PROFESSION_INFO:
        salary = PROFESSION_INFO[row["profession"]]["salary"] + (row["level"] - 1) * 50
        days_worked = row["days_worked"] or 0
        level = row["level"] or 1
        # Прогресс до следующего уровня (7 дней)
        if level >= 5:
            progress_percent = 100
            days_needed = 0
        else:
            progress_percent = int((days_worked / 7) * 100)
            days_needed = 7 - days_worked
        current = {
            "profession": row["profession"],
            "name": PROFESSION_INFO[row["profession"]]["name"],
            "level": level,
            "salary": salary,
            "days_worked": days_worked,
            "progress_percent": progress_percent,
            "days_needed": days_needed,
        }

    all_professions = [
        {"key": key, "name": p["name"], "salary": p["salary"], "cooldown_hours": p["cooldown_hours"]}
        for key, p in PROFESSION_INFO.items()
        if key != "ruler" or is_admin
    ]
    return {"current": current, "all": all_professions, "hire_cost": HIRE_COST}


@app.post("/api/professions/hire")
def hire_profession(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    profession = payload.get("profession")
    if profession not in PROFESSION_INFO:
        raise HTTPException(status_code=404, detail="Неизвестная профессия")
    if profession == "ruler" and not config.is_full_admin(user_id):
        raise HTTPException(status_code=403, detail="Эта профессия доступна только администраторам")

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT total_changes, profession FROM professions WHERE user_id = ?", (user_id,)
    )
    row = cur.fetchone()
    cost = HIRE_COST
    if row and row["profession"]:
        cost += FIRE_EXTRA_COST * (row["total_changes"] + 1)

    if not change_balance(cur, user_id, -cost):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Недостаточно монет. Нужно: {cost} 🪙")

    changes = (row["total_changes"] + 1) if row else 0
    cur.execute("""
        INSERT INTO professions (user_id, profession, level, days_worked, total_changes)
        VALUES (?, ?, 1, 0, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            profession = excluded.profession, level = 1, days_worked = 0, total_changes = ?
    """, (user_id, profession, changes, changes))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"Вы устроились: {PROFESSION_INFO[profession]['name']}"}


@app.post("/api/professions/work")
def work_profession(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT profession, level, last_work, days_worked, last_work_day "
        "FROM professions WHERE user_id = ? FOR UPDATE",
        (user_id,),
    )
    row = cur.fetchone()
    if not row or not row["profession"]:
        conn.close()
        raise HTTPException(status_code=400, detail="У вас нет профессии")

    info = PROFESSION_INFO.get(row["profession"])
    if not info:
        conn.close()
        raise HTTPException(status_code=400, detail="Профессия доступна только в боте")
    now = datetime.now()
    if row["last_work"]:
        delta = now - datetime.fromisoformat(row["last_work"])
        cooldown = bot_db.get_user_cooldown_seconds(user_id, "work", info["cooldown_hours"] * 3600)
        if delta.total_seconds() < cooldown:
            remaining = cooldown - int(delta.total_seconds())
            conn.close()
            raise HTTPException(
                status_code=400,
                detail=f"Отдых ещё не закончен: {remaining // 3600} ч {(remaining % 3600) // 60} мин",
            )

    salary = info["salary"] + (row["level"] - 1) * 50
    change_balance(cur, user_id, salary)

    today = now.date().isoformat()
    new_days, new_level = row["days_worked"], row["level"]
    if row["last_work_day"] != today:
        new_days += 1
        if new_days >= 7 and new_level < 5:
            new_level += 1
            new_days = 0

    cur.execute("""
        UPDATE professions SET last_work = ?, days_worked = ?, level = ?, last_work_day = ?
        WHERE user_id = ?
    """, (now.isoformat(), new_days, new_level, today, user_id))
    conn.commit()
    conn.close()
    reduction = bot_db.consume_user_boost(user_id, "work")
    base_cooldown = info["cooldown_hours"] * 3600
    next_cooldown = base_cooldown * (100 - reduction) // 100 if reduction else base_cooldown
    bot_db.set_user_cooldown_seconds(user_id, "work", next_cooldown)
    boost_message = f" Буст сократил следующий отдых на {reduction}%." if reduction else ""
    return {"ok": True, "message": f"Заработано {salary} 🪙! Уровень {new_level} ({new_days}/7 дней).{boost_message}"}


# ========== API: ЕЖЕДНЕВНЫЕ ЗАДАНИЯ ==========

@app.get("/api/tasks")
def get_tasks(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    today = datetime.now().date().isoformat()
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT task_type, progress, target, reward, is_done FROM daily_tasks
        WHERE user_id = ? AND assigned_date = ?
    """, (user_id, today))
    tasks = [
        {
            "name": DAILY_TASK_NAMES.get(r["task_type"], r["task_type"]),
            "desc": DAILY_TASK_DESCS.get(r["task_type"], ""),
            "progress": r["progress"], "target": r["target"],
            "reward": r["reward"], "is_done": bool(r["is_done"]),
        }
        for r in cur.fetchall()
    ]

    conn.close()
    return {"tasks": tasks}


# ========== API: КОЛЕСО ФОРТУНЫ ==========

WHEEL_COST = 200
WHEEL_COOLDOWN = 10800  # 3 часа


@app.get("/api/wheel")
def get_wheel(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, label, chance FROM wheel_sectors")
    sectors = [dict(r) for r in cur.fetchall()]

    cur.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    # Serialize spins for this user; otherwise concurrent requests can both win.
    cur.execute("SELECT last_wheel_time FROM user_settings WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.commit()
    conn.close()

    can_spin, remaining = True, 0
    if row and row["last_wheel_time"]:
        delta = (datetime.now() - datetime.fromisoformat(row["last_wheel_time"])).total_seconds()
        if delta < WHEEL_COOLDOWN:
            can_spin, remaining = False, int(WHEEL_COOLDOWN - delta)

    return {"sectors": sectors, "cost": WHEEL_COST, "can_spin": can_spin, "remaining_seconds": remaining}


@app.post("/api/wheel/spin")
def spin_wheel(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    conn = db()
    cur = conn.cursor()

    cur.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    # Serialize spins for this user; otherwise concurrent requests can both win.
    cur.execute("SELECT last_wheel_time FROM user_settings WHERE user_id = ? FOR UPDATE", (user_id,))
    row = cur.fetchone()
    if row and row["last_wheel_time"]:
        delta = (datetime.now() - datetime.fromisoformat(row["last_wheel_time"])).total_seconds()
        if delta < WHEEL_COOLDOWN:
            conn.close()
            raise HTTPException(status_code=400, detail="Колесо ещё не готово к следующему кручению")

    if not change_balance(cur, user_id, -WHEEL_COST):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    cur.execute("SELECT label, reward_type, reward_value, chance FROM wheel_sectors")
    sectors = cur.fetchall()
    total = sum(s["chance"] for s in sectors)
    r = random.uniform(0, total)
    upto = 0
    chosen = sectors[-1]
    for s in sectors:
        if upto + s["chance"] >= r:
            chosen = s
            break
        upto += s["chance"]

    result_text = chosen["label"]
    if chosen["reward_type"] == "money":
        change_balance(cur, user_id, int(chosen["reward_value"]))
    elif chosen["reward_type"] == "pet":
        cur.execute(
            "INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income) "
            "VALUES (?, ?, ?, 'обычный', 100)",
            (user_id, chosen["reward_value"], "🐹"),
        )
    elif chosen["reward_type"] == "egg":
        egg = PET_EGGS.get(chosen["reward_value"])
        if egg:
            pets = egg["pets"]
            total_w = sum(p[3] for p in pets)
            rr = random.uniform(0, total_w)
            upto2 = 0
            pet = pets[-1]
            for p in pets:
                if upto2 + p[3] >= rr:
                    pet = p
                    break
                upto2 += p[3]
            cur.execute(
                "INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income) VALUES (?, ?, ?, ?, ?)",
                (user_id, pet[0], pet[0].split()[0], pet[1], pet[2]),
            )
            result_text = f"{egg['name']} → {pet[0]}"

    cur.execute(
        "UPDATE user_settings SET last_wheel_time = ? WHERE user_id = ?",
        (datetime.now().isoformat(), user_id),
    )
    conn.commit()
    conn.close()
    # Логируем только успешно завершённую прокрутку Mini App.
    try:
        bot_db.log_action(
            user_id,
            "wheel",
            f"Колесо (Mini App): {result_text}",
            amount=-WHEEL_COST,
        )
    except Exception:
        # Успешный спин не должен завершаться ошибкой из-за логирования.
        pass
    return {"ok": True, "result": result_text}


# ========== API: БОНУС ==========

BONUS_COOLDOWN = 10800  # 3 часа


@app.get("/api/bonus")
def get_bonus_status(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT last_bonus_time FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    can_claim, remaining = True, 0
    cooldown = bot_db.get_user_cooldown_seconds(user_id, "bonus", BONUS_COOLDOWN)
    if row["last_bonus_time"]:
        delta = (datetime.now() - datetime.fromisoformat(row["last_bonus_time"])).total_seconds()
        if delta < cooldown:
            can_claim, remaining = False, int(cooldown - delta)
    return {"can_claim": can_claim, "remaining_seconds": remaining}


@app.post("/api/bonus/claim")
def claim_bonus(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    conn = db()
    cur = conn.cursor()
    # Lock the user's row so concurrent claims cannot both pass the cooldown check.
    cur.execute("SELECT last_bonus_time FROM users WHERE user_id = ? FOR UPDATE", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if row["last_bonus_time"]:
        delta = (datetime.now() - datetime.fromisoformat(row["last_bonus_time"])).total_seconds()
        cooldown = bot_db.get_user_cooldown_seconds(user_id, "bonus", BONUS_COOLDOWN)
        if delta < cooldown:
            conn.close()
            raise HTTPException(status_code=400, detail="Бонус ещё не готов")

    bonus = random.randint(15, 100)
    now = datetime.now().isoformat()
    cur.execute(
        "UPDATE users SET balance = balance + ?, last_bonus_time = ? WHERE user_id = ?",
        (bonus, now, user_id),
    )
    conn.commit()
    conn.close()
    reduction = bot_db.consume_user_boost(user_id, "bonus")
    next_cooldown = BONUS_COOLDOWN * (100 - reduction) // 100 if reduction else BONUS_COOLDOWN
    bot_db.set_user_cooldown_seconds(user_id, "bonus", next_cooldown)
    try:
        bot_db.log_action(user_id, "bonus", "Ежедневный бонус (Mini App)", amount=bonus)
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")
    return {"ok": True, "amount": bonus}


# ========== API: ПЕРЕВОДЫ ==========

@app.post("/api/transfer")
def transfer_coins(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    target_username = str(payload.get("username") or "").strip().lstrip("@")
    try:
        amount = int(payload.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Сумма должна быть целым числом")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля")
    if not target_username:
        raise HTTPException(status_code=400, detail="Укажи username получателя")

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, username FROM users "
        "WHERE LOWER(REPLACE(TRIM(username), '@', '')) = LOWER(?) AND user_id != ? LIMIT 1",
        (target_username, user_id),
    )
    target = cur.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь с таким username не найден")

    target_id = int(target["user_id"])
    # Перевод — реальная денежная операция: намеренно обходим test-mode скидку,
    # иначе тех.админ в тестовом режиме мог бы переводить монеты бесплатно.
    cur.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
        (amount, user_id, amount),
    )
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Не удалось зачислить перевод; деньги не списаны")
    conn.commit()
    conn.close()
    try:
        bot_db.log_action(
            user_id, "transfer", f"Перевод в Mini App пользователю @{target_username}",
            amount=amount, target_user_id=target_id
        )
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")
    return {"ok": True, "message": f"Переведено {amount} 🪙 пользователю @{target_username}"}


# ========== API: ИГРЫ ==========

SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]


@app.post("/api/games/play")
def play_game(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    game = payload.get("game")
    try:
        bet = int(payload.get("bet") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная ставка")
    choice = payload.get("choice")

    if game not in {"slot", "dice", "coin", "dart", "number"}:
        raise HTTPException(status_code=400, detail="Неизвестная игра")
    if bet <= 0:
        raise HTTPException(status_code=400, detail="Ставка должна быть больше нуля")
    if game in {"dice", "number"}:
        try:
            choice = int(choice)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Сначала выберите число")
        upper = 6 if game == "dice" else 10
        if not 1 <= choice <= upper:
            raise HTTPException(status_code=400, detail=f"Число должно быть от 1 до {upper}")
    elif game == "coin":
        if choice == "Орёл":
            choice = "Орел"
        if choice not in {"Орел", "Решка"}:
            raise HTTPException(status_code=400, detail="Выберите орла или решку")

    conn = db()
    cur = conn.cursor()
    test_mode = bool(bot_db.is_test_mode(user_id))
    if not change_balance(cur, user_id, -bet):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    win = 0
    text = ""

    if game == "slot":
        result = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        text = f"{result[0]} {result[1]} {result[2]}"
        if result[0] == result[1] == result[2]:
            mult = 10 if result[0] == "7️⃣" else (5 if result[0] == "💎" else 3)
            win = bet * mult
        elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
            win = bet * 2
    elif game == "dice":
        roll = random.randint(1, 6)
        text = f"🎲 Выпало: {roll}"
        if str(roll) == str(choice):
            win = bet * 6
    elif game == "coin":
        result = random.choice(["Орел", "Решка"])
        text = f"🪙 Выпало: {result}"
        if str(choice) == result:
            win = bet * 2
    elif game == "dart":
        score = random.randint(0, 15)
        text = f"🎯 Очков: {score}"
        if score == 15: win = bet * 5
        elif score >= 12: win = bet * 3
        elif score >= 8: win = bet * 1
    elif game == "number":
        num = random.randint(1, 10)
        text = f"🃏 Число: {num}"
        try:
            guess = int(choice)
            if guess == num:
                win = bet * 10
            elif abs(guess - num) <= 2:
                win = bet * 2
        except (ValueError, TypeError):
            pass
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Неизвестная игра")

    if test_mode:
        # Тестовые ставки не должны создавать реальные монеты даже при выигрыше.
        text += f"\n🧪 Тест-режим: баланс не изменён."
    elif win > 0:
        change_balance(cur, user_id, bet + win)
        text += f"\n✅ Выигрыш: {win} 🪙"
    else:
        text += f"\n❌ Проигрыш: {bet} 🪙"

    # Обновляем ту же таблицу статистики, которую читает профиль бота и Mini App.
    cur.execute("INSERT OR IGNORE INTO game_stats (user_id) VALUES (?)", (user_id,))
    cur.execute(
        """
        UPDATE game_stats
        SET games_played = COALESCE(games_played, 0) + 1,
            games_won = COALESCE(games_won, 0) + ?,
            total_earned = COALESCE(total_earned, 0) + ?,
            total_lost = COALESCE(total_lost, 0) + ?
        WHERE user_id = ?
        """,
        (1 if win > 0 else 0, win if win > 0 else 0, bet if win <= 0 else 0, user_id),
    )

    conn.commit()
    conn.close()
    try:
        bot_db.log_action(
            user_id, "game",
            f"Игра Mini App: {game}; ставка={bet}; выигрыш={win}; результат={text}",
            amount=(win if win > 0 else -bet),
        )
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")
    return {"ok": True, "win": win, "text": text}



# ========== КРЕСТИКИ-НОЛИКИ MINI APP ==========
def _ttt_bot_choice(board: str) -> int:
    cells = list(board)
    for symbol in ("O", "X"):
        for a, b, c in bot_db.TTT_LINES:
            line = [cells[a], cells[b], cells[c]]
            if line.count(symbol) == 2 and line.count("_") == 1:
                return (a, b, c)[line.index("_")]
    if cells[4] == "_":
        return 4
    for cell in (0, 2, 6, 8, 1, 3, 5, 7):
        if cells[cell] == "_":
            return cell
    return 0


def _ttt_game_payload(game, balance):
    return {
        "game_id": game["id"], "board": game["board"], "status": game["status"],
        "winner": game["winner"], "bet": game["bet"], "balance": balance,
        "payout": game["bet"] * 2 if game["status"] == "finished" and game["winner"] == game["player_x"] else (game["bet"] if game["status"] == "draw" else 0),
    }


@app.post("/api/ttt/start")
def ttt_start(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        bet = int(payload.get("bet") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная ставка")
    if bet <= 0 or bet > 100000:
        raise HTTPException(status_code=400, detail="Ставка должна быть от 1 до 100000")
    conn = db()
    cur = conn.cursor()
    if not change_balance(cur, user_id, -bet):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    cur.execute(
        "INSERT INTO ttt_games (player_x, player_o, is_vs_bot, board, turn, bet, status) "
        "VALUES (?, NULL, 1, '_________', ?, ?, 'active')",
        (user_id, user_id, bet),
    )
    game_id = cur.lastrowid
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {
        "game_id": game_id, "board": "_________", "status": "active",
        "winner": None, "bet": bet, "balance": balance, "payout": 0,
    }


@app.get("/api/ttt/state")
def ttt_state(init_data: str = Query(..., alias="initData"), game_id: int = Query(...)):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, player_x, player_o, is_vs_bot, board, turn, bet, status, winner "
        "FROM ttt_games WHERE id = ? AND player_x = ?",
        (game_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Игра не найдена")
    game = dict(row)
    balance = require_balance(cur, user_id)
    conn.close()
    return _ttt_game_payload(game, balance)


@app.post("/api/ttt/move")
def ttt_move_web(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        game_id, cell = int(payload.get("game_id")), int(payload.get("cell"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный ход")
    if not 0 <= cell <= 8:
        raise HTTPException(status_code=400, detail="Клетка вне поля")

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, player_x, player_o, is_vs_bot, board, turn, bet, status, winner "
        "FROM ttt_games WHERE id = ? AND player_x = ? FOR UPDATE",
        (game_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Игра не найдена")
    game = dict(row)
    if game["status"] != "active":
        conn.close()
        raise HTTPException(status_code=400, detail="Игра уже завершена")
    if game["turn"] != user_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Сейчас не твой ход")
    board = list(game["board"])
    if board[cell] != "_":
        conn.close()
        raise HTTPException(status_code=400, detail="Клетка уже занята")

    board[cell] = "X"
    status, winner_id = "active", None
    result = bot_db.ttt_check_winner("".join(board))
    if result == "X":
        status, winner_id = "finished", user_id
    elif result == "draw":
        status = "draw"
    else:
        bot_cell = _ttt_bot_choice("".join(board))
        board[bot_cell] = "O"
        result = bot_db.ttt_check_winner("".join(board))
        if result == "O":
            status, winner_id = "finished", 0
        elif result == "draw":
            status = "draw"

    final_board = "".join(board)
    cur.execute(
        "UPDATE ttt_games SET board = ?, status = ?, winner = ?, turn = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (final_board, status, winner_id, user_id, game_id),
    )
    payout = 0
    won = status == "finished" and winner_id == user_id
    lost = status == "finished" and winner_id == 0
    if won:
        payout = game["bet"] * 2
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (payout, user_id))
    elif status == "draw":
        payout = game["bet"]
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (payout, user_id))

    cur.execute("INSERT OR IGNORE INTO game_stats (user_id) VALUES (?)", (user_id,))
    if status != "active":
        cur.execute(
            "UPDATE game_stats SET games_played = COALESCE(games_played,0) + 1, "
            "games_won = COALESCE(games_won,0) + ?, total_earned = COALESCE(total_earned,0) + ?, "
            "total_lost = COALESCE(total_lost,0) + ? WHERE user_id = ?",
            (1 if won else 0, game["bet"] if won else 0, game["bet"] if lost else 0, user_id),
        )
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    if status != "active":
        try:
            bot_db.log_action(
                user_id, "game",
                f"Крестики-нолики Mini App: ставка={game['bet']}; статус={status}; выплата={payout}",
                amount=(game["bet"] if won else (-game["bet"] if lost else 0)),
            )
        except Exception:
            logger.exception("Не удалось записать лог игры в крестики-нолики")
    return {
        "game_id": game_id, "board": final_board, "status": status, "winner": winner_id,
        "bet": game["bet"], "balance": balance, "payout": payout,
        "result": "win" if won else ("lose" if lost else ("draw" if status == "draw" else "next")),
    }


# ========== API: НАСТРОЙКИ ==========

@app.get("/api/settings")
def get_settings(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT tutorial_done, hints_enabled FROM user_settings WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return {
        "theme": "dark",
        "hints_enabled": bool(row["hints_enabled"]) if row else True,
        "tutorial_done": bool(row["tutorial_done"]) if row else False,
        "is_admin": config.is_full_admin(user_id),
    }


@app.post("/api/settings/hints")
def set_hints(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    enabled = bool(payload.get("enabled"))
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    cur.execute("UPDATE user_settings SET hints_enabled = ? WHERE user_id = ?", (1 if enabled else 0, user_id))
    conn.commit()
    conn.close()
    return {"ok": True}

# ========== API: АДМИНКА ==========

def _require_admin(user_id: int):
    # The owner, regular admins and tech admins share access to the admin panel.
    if not config.is_full_admin(user_id):
        raise HTTPException(status_code=403, detail="Нет доступа")


@app.post("/api/admin/give")
def admin_give(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    username = (payload.get("username") or "").lstrip("@")
    try:
        amount = int(payload.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная сумма")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    target = cur.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Не найден")
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target["user_id"]))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"Выдано {amount} 🪙 @{username}"}


@app.post("/api/admin/take")
def admin_take(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    username = (payload.get("username") or "").lstrip("@")
    try:
        amount = int(payload.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная сумма")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, balance FROM users WHERE username = ?", (username,))
    target = cur.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Не найден")
    new_bal = max(0, target["balance"] - amount)
    cur.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_bal, target["user_id"]))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"Снято {amount} 🪙 @{username}"}


@app.get("/api/admin/user")
def admin_user(init_data: str = Query(..., alias="initData"), query: str = Query(...)):
    user_id = get_telegram_user_id(init_data)
    _require_admin(user_id)
    q = query.lstrip("@")
    conn = db()
    cur = conn.cursor()
    if q.isdigit():
        cur.execute("SELECT user_id, username, balance FROM users WHERE user_id = ?", (int(q),))
    else:
        cur.execute("SELECT user_id, username, balance FROM users WHERE username = ?", (q,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Не найден")
    return {"user_id": row["user_id"], "username": row["username"], "balance": row["balance"]}


@app.get("/api/admin/stats")
def admin_stats(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    _require_admin(user_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) c FROM users")
    users = cur.fetchone()["c"]
    cur.execute("SELECT SUM(balance) s FROM users")
    balance = cur.fetchone()["s"] or 0
    cur.execute("SELECT COUNT(*) c FROM purchases")
    purchases = cur.fetchone()["c"]
    conn.close()
    return {"total_users": users, "total_balance": balance, "total_purchases": purchases}


@app.post("/api/admin/ban")
def admin_ban(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    username = (payload.get("username") or "").lstrip("@")
    banned = bool(payload.get("banned"))
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_banned = ? WHERE username = ?", (1 if banned else 0, username))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"@{username} {'забанен' if banned else 'разбанен'}"}

# ========== API: АДМИН ПИНГ ==========


@app.get("/api/admin/ping")
def admin_ping(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    _require_admin(user_id)

    try:
        start = time.monotonic()
        # Запрос к Telegram API
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
        with httpx.Client(timeout=5) as client:
            r = client.get(url)
        ping_ms = int((time.monotonic() - start) * 1000)

        if ping_ms < 200:
            status = "🟢 Отлично"
        elif ping_ms < 500:
            status = "🟡 Нормально"
        elif ping_ms < 1000:
            status = "🟠 Медленно"
        else:
            status = "🔴 Плохо"

        return {
            "ok": r.status_code == 200,
            "ping_ms": ping_ms,
            "status": status,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка пинга: {e}")

# ========== API: САПЁР ==========

MS_FIELD_SIZE = 8
MS_MINES = 6
MS_BET = 200
MS_REWARD = 400


@app.post("/api/minesweeper/start")
def ms_start_api(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        bet = int(payload.get("bet") or MS_BET)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная ставка")
    if bet != MS_BET:
        raise HTTPException(status_code=400, detail=f"Ставка в сапёре фиксированная: {MS_BET} 🪙")

    conn = db()
    cur = conn.cursor()
    if not change_balance(cur, user_id, -bet):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    size = MS_FIELD_SIZE
    cells = size * size
    mine_positions = random.sample(range(cells), MS_MINES)

    board = []
    for i in range(cells):
        if i in mine_positions:
            board.append("M")
        else:
            row, col = divmod(i, size)
            count = 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = row + dr, col + dc
                    if 0 <= nr < size and 0 <= nc < size:
                        ni = nr * size + nc
                        if ni in mine_positions:
                            count += 1
            board.append(str(count))

    board_str = "".join(board)
    revealed_str = "0" * cells

    cur.execute("""
        INSERT INTO minesweeper_games (user_id, board, revealed, bet, status)
        VALUES (?, ?, ?, ?, 'active')
    """, (user_id, board_str, revealed_str, bet))
    game_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "game_id": game_id,
        # Never send unrevealed mine positions to the client.
        "board": "?" * cells,
        "revealed": revealed_str,
        "size": size,
        "mines": MS_MINES,
        "bet": bet,
        "reward": MS_REWARD,
    }


@app.post("/api/minesweeper/open")
def ms_open_api(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        game_id = int(payload.get("game_id"))
        cell = int(payload.get("cell"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная игра или клетка")

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT board, revealed, bet, status, field_size FROM minesweeper_games
        WHERE id = ? AND user_id = ?
    """, (game_id, user_id))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Игра не найдена")
    if row["status"] != "active":
        conn.close()
        raise HTTPException(status_code=400, detail="Игра завершена")

    size = row["field_size"]
    if not 0 <= cell < size * size:
        conn.close()
        raise HTTPException(status_code=400, detail="Клетка вне поля")
    board = list(row["board"])
    revealed = list(row["revealed"])

    if revealed[cell] == "1":
        conn.close()
        return {"ok": True, "result": "skip"}

    # Мина
    if board[cell] == "M":
        for i in range(size * size):
            if board[i] == "M":
                revealed[i] = "1"
        revealed_str = "".join(revealed)
        cur.execute("UPDATE minesweeper_games SET revealed = ?, status = 'lose' WHERE id = ?",
                    (revealed_str, game_id))
        cur.execute("INSERT OR IGNORE INTO game_stats (user_id) VALUES (?)", (user_id,))
        cur.execute(
            "UPDATE game_stats SET games_played = COALESCE(games_played, 0) + 1, "
            "total_lost = COALESCE(total_lost, 0) + ? WHERE user_id = ?",
            (row["bet"], user_id),
        )
        cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        balance_row = cur.fetchone()
        balance = balance_row["balance"] if balance_row else 0
        conn.commit()
        conn.close()
        try:
            bot_db.log_action(user_id, "game", f"Сапёр: проигрыш; ставка={row['bet']}", amount=-int(row["bet"]))
        except Exception:
            logger.exception("Не удалось выполнить необязательную операцию")
        return {
            "ok": True, "result": "boom",
            "board": "".join(board), "revealed": revealed_str,
            "balance": balance,
        }

    # Безопасная клетка + автооткрытие пустых
    to_reveal = [cell]
    while to_reveal:
        c = to_reveal.pop()
        if revealed[c] == "1":
            continue
        revealed[c] = "1"
        if board[c] == "0":
            r, co = divmod(c, size)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, co + dc
                    if 0 <= nr < size and 0 <= nc < size:
                        ni = nr * size + nc
                        if revealed[ni] == "0" and board[ni] != "M":
                            to_reveal.append(ni)

    revealed_str = "".join(revealed)

    win = all(board[i] == "M" or revealed[i] == "1" for i in range(size * size))

    if win:
        change_balance(cur, user_id, MS_REWARD)
        cur.execute("UPDATE minesweeper_games SET revealed = ?, status = 'win' WHERE id = ?",
                    (revealed_str, game_id))
        cur.execute("INSERT OR IGNORE INTO game_stats (user_id) VALUES (?)", (user_id,))
        cur.execute(
            "UPDATE game_stats SET games_played = COALESCE(games_played, 0) + 1, "
            "games_won = COALESCE(games_won, 0) + 1, "
            "total_earned = COALESCE(total_earned, 0) + ? WHERE user_id = ?",
            (max(0, MS_REWARD - int(row["bet"])), user_id),
        )
    else:
        cur.execute("UPDATE minesweeper_games SET revealed = ? WHERE id = ?",
                    (revealed_str, game_id))

    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance = cur.fetchone()["balance"]
    conn.commit()
    conn.close()

    if win:
        try:
            bot_db.log_action(user_id, "game", f"Сапёр: победа; ставка={row['bet']}", amount=max(0, MS_REWARD - int(row["bet"])))
        except Exception:
            logger.exception("Не удалось выполнить необязательную операцию")

    return {
        "ok": True,
        "result": "win" if win else "safe",
        # Reveal only cells the player has opened while the game is active.
        "board": "".join(board) if win else "".join(
            board[i] if revealed_str[i] == "1" else "?" for i in range(size * size)
        ),
        "revealed": revealed_str,
        "balance": balance,
        "reward": MS_REWARD if win else 0,
    }

# ========== API: МОРСКОЙ БОЙ ==========

BS_SIZE = 10
BS_BET_MIN = 500
BS_SHIPS = [4, 3, 3, 2, 2, 2, 1, 1, 1, 1]


def _bs_generate_field() -> str:
    grid = [["." for _ in range(BS_SIZE)] for _ in range(BS_SIZE)]
    ships = list(BS_SHIPS)
    random.shuffle(ships)

    for size in ships:
        placed = False
        for _ in range(200):
            horizontal = random.choice([True, False])
            if horizontal:
                r = random.randint(0, BS_SIZE - 1)
                c = random.randint(0, BS_SIZE - size)
                cells = [(r, c + i) for i in range(size)]
            else:
                r = random.randint(0, BS_SIZE - size)
                c = random.randint(0, BS_SIZE - 1)
                cells = [(r + i, c) for i in range(size)]

            ok = True
            for (rr, cc) in cells:
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        nr, nc = rr + dr, cc + dc
                        if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE:
                            if grid[nr][nc] == "S":
                                ok = False
                                break
                    if not ok:
                        break
                if not ok:
                    break
            if not ok:
                continue

            for (rr, cc) in cells:
                grid[rr][cc] = "S"
            placed = True
            break

        if not placed:
            return _bs_generate_field()

    return "".join("".join(row) for row in grid)


def _bs_cell_index(r: int, c: int) -> int:
    return r * BS_SIZE + c


def _bs_check_sunk(field: str, shots: str, r: int, c: int) -> bool:
    visited = set()
    stack = [(r, c)]
    ship_cells = []
    while stack:
        cr, cc = stack.pop()
        if (cr, cc) in visited:
            continue
        idx = _bs_cell_index(cr, cc)
        if field[idx] != "S":
            continue
        visited.add((cr, cc))
        ship_cells.append((cr, cc))
        for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE:
                ni = _bs_cell_index(nr, nc)
                if field[ni] == "S" and (nr, nc) not in visited:
                    stack.append((nr, nc))
    for (sr, sc) in ship_cells:
        si = _bs_cell_index(sr, sc)
        if shots[si] not in ("2", "3"):
            return False
    return True


def _bs_mark_sunk(field: str, shots: list, r: int, c: int):
    visited = set()
    stack = [(r, c)]
    ship_cells = []
    while stack:
        cr, cc = stack.pop()
        if (cr, cc) in visited:
            continue
        idx = _bs_cell_index(cr, cc)
        if field[idx] != "S":
            continue
        visited.add((cr, cc))
        ship_cells.append((cr, cc))
        for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE:
                ni = _bs_cell_index(nr, nc)
                if field[ni] == "S" and (nr, nc) not in visited:
                    stack.append((nr, nc))
    for (sr, sc) in ship_cells:
        si = _bs_cell_index(sr, sc)
        shots[si] = "3"
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nr, nc = sr + dr, sc + dc
                if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE:
                    ni = _bs_cell_index(nr, nc)
                    if shots[ni] == "0":
                        shots[ni] = "1"


@app.post("/api/battleship/start")
def bs_start(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        bet = int(payload.get("bet") or BS_BET_MIN)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная ставка")

    if bet < BS_BET_MIN:
        raise HTTPException(status_code=400, detail=f"Минимальная ставка: {BS_BET_MIN}")

    conn = db()
    cur = conn.cursor()
    if not change_balance(cur, user_id, -bet):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    player_field = _bs_generate_field()
    bot_field = _bs_generate_field()
    empty = "0" * (BS_SIZE * BS_SIZE)

    cur.execute("""
        INSERT INTO battleship_games
        (user_id, player_field, bot_field, player_shots, bot_shots, bet, turn, status)
        VALUES (?, ?, ?, ?, ?, ?, 'player', 'active')
    """, (user_id, player_field, bot_field, empty, empty, bet))
    game_id = cur.lastrowid
    conn.commit()

    balance = require_balance(cur, user_id)
    conn.close()

    return {
        "game_id": game_id,
        "bet": bet,
        "reward": bet * 2,
        "player_field": player_field,
        # Keep the opponent's field server-side until the game is over.
        "player_shots": empty,
        "bot_shots": empty,
        "turn": "player",
        "status": "active",
        "balance": balance,
    }


@app.get("/api/battleship/state")
def bs_state(init_data: str = Query(..., alias="initData"), game_id: int = Query(...)):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, player_field, bot_field, player_shots, bot_shots,
               bet, status, turn
        FROM battleship_games WHERE id = ?
    """, (game_id,))
    row = cur.fetchone()
    if not row or row["user_id"] != user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="Игра не найдена")

    balance = require_balance(cur, user_id)
    conn.close()

    return {
        "game_id": game_id,
        "bet": row["bet"],
        "reward": row["bet"] * 2,
        "player_field": row["player_field"],
        **({"bot_field": row["bot_field"]} if row["status"] in {"win", "lose"} else {}),
        "player_shots": row["player_shots"],
        "bot_shots": row["bot_shots"],
        "turn": row["turn"],
        "status": row["status"],
        "balance": balance,
    }


@app.post("/api/battleship/fire")
def bs_fire(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        game_id = int(payload.get("game_id"))
        row = int(payload.get("row"))
        col = int(payload.get("col"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректные координаты")

    if not (0 <= row < BS_SIZE and 0 <= col < BS_SIZE):
        raise HTTPException(status_code=400, detail="Координаты вне поля")

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, player_field, bot_field, player_shots, bot_shots,
               bet, status, turn
        FROM battleship_games WHERE id = ?
    """, (game_id,))
    g = cur.fetchone()
    if not g or g["user_id"] != user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="Игра не найдена")
    if g["status"] != "active":
        conn.close()
        raise HTTPException(status_code=400, detail="Игра завершена")
    if g["turn"] != "player":
        conn.close()
        raise HTTPException(status_code=400, detail="Не твой ход")

    idx = _bs_cell_index(row, col)
    shots = list(g["player_shots"])
    if shots[idx] in ("1", "2", "3"):
        conn.close()
        raise HTTPException(status_code=400, detail="Уже стрелял сюда")

    bot_field = g["bot_field"]
    if bot_field[idx] == "S":
        shots[idx] = "2"
        if _bs_check_sunk(bot_field, "".join(shots), row, col):
            _bs_mark_sunk(bot_field, shots, row, col)
            result = "sunk"
        else:
            result = "hit"
    else:
        shots[idx] = "1"
        result = "miss"

    shots_str = "".join(shots)

    win = all(
        bot_field[i] != "S" or shots[i] == "3"
        for i in range(BS_SIZE * BS_SIZE)
    )

    if win:
        change_balance(cur, user_id, g["bet"] * 2)
        cur.execute("""
            UPDATE battleship_games SET player_shots = ?, status = 'win' WHERE id = ?
        """, (shots_str, game_id))
    else:
        next_turn = "bot" if result == "miss" else "player"
        cur.execute("""
            UPDATE battleship_games SET player_shots = ?, turn = ? WHERE id = ?
        """, (shots_str, next_turn, game_id))
    conn.commit()

    bot_msg = ""
    # Ход бота (если передан ему и игра идёт)
    cur.execute("SELECT status, turn, player_field, bot_shots FROM battleship_games WHERE id = ?", (game_id,))
    g2 = cur.fetchone()
    if g2 and g2["status"] == "active" and g2["turn"] == "bot":
        while True:
            bot_field_local = g2["player_field"]
            bot_shots = list(g2["bot_shots"])

            candidates = []
            for i in range(BS_SIZE * BS_SIZE):
                if bot_shots[i] == "2":
                    r, c = divmod(i, BS_SIZE)
                    for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE:
                            ni = _bs_cell_index(nr, nc)
                            if bot_shots[ni] == "0":
                                candidates.append((nr, nc))
            if not candidates:
                remaining = [i for i in range(BS_SIZE * BS_SIZE) if bot_shots[i] == "0"]
                if not remaining:
                    break
                ii = random.choice(remaining)
                br, bc = divmod(ii, BS_SIZE)
            else:
                br, bc = random.choice(candidates)

            bidx = _bs_cell_index(br, bc)
            if bot_field_local[bidx] == "S":
                bot_shots[bidx] = "2"
                if _bs_check_sunk(bot_field_local, "".join(bot_shots), br, bc):
                    _bs_mark_sunk(bot_field_local, bot_shots, br, bc)
                    bot_result = "sunk"
                else:
                    bot_result = "hit"
            else:
                bot_shots[bidx] = "1"
                bot_result = "miss"

            bot_shots_str = "".join(bot_shots)

            lose = all(
                bot_field_local[i] != "S" or bot_shots[i] == "3"
                for i in range(BS_SIZE * BS_SIZE)
            )

            if lose:
                cur.execute("""
                    UPDATE battleship_games SET bot_shots = ?, status = 'lose' WHERE id = ?
                """, (bot_shots_str, game_id))
                conn.commit()
                bot_msg = f"Бот попал в ({br + 1}, {bc + 1}) и уничтожил твой флот!"
                break
            else:
                next_turn = "bot" if bot_result in ("hit", "sunk") else "player"
                cur.execute("""
                    UPDATE battleship_games SET bot_shots = ?, turn = ? WHERE id = ?
                """, (bot_shots_str, next_turn, game_id))
                conn.commit()
                bot_msg = f"Бот стреляет в ({br + 1}, {bc + 1}): {bot_result}"
                if bot_result == "miss":
                    break
                # если попал — бот ходит ещё, обновляем g2
                cur.execute("SELECT status, turn, player_field, bot_shots FROM battleship_games WHERE id = ?", (game_id,))
                g2 = cur.fetchone()

    # Финальное состояние
    cur.execute("""
        SELECT player_field, bot_field, player_shots, bot_shots, bet, status, turn
        FROM battleship_games WHERE id = ?
    """, (game_id,))
    final = cur.fetchone()
    completed_status = final["status"] if final else "active"
    if completed_status in {"win", "lose"}:
        cur.execute("INSERT OR IGNORE INTO game_stats (user_id) VALUES (?)", (user_id,))
        cur.execute(
            """
            UPDATE game_stats
            SET games_played = COALESCE(games_played, 0) + 1,
                games_won = COALESCE(games_won, 0) + ?,
                total_earned = COALESCE(total_earned, 0) + ?,
                total_lost = COALESCE(total_lost, 0) + ?
            WHERE user_id = ?
            """,
            (
                1 if completed_status == "win" else 0,
                int(final["bet"]) if completed_status == "win" else 0,
                int(final["bet"]) if completed_status == "lose" else 0,
                user_id,
            ),
        )
        conn.commit()

    balance = require_balance(cur, user_id)
    conn.close()

    if completed_status in {"win", "lose"}:
        try:
            net_amount = int(final["bet"]) if completed_status == "win" else -int(final["bet"])
            bot_db.log_action(
                user_id, "game",
                f"Морской бой: {'победа' if completed_status == 'win' else 'поражение'}; ставка={final['bet']}",
                amount=net_amount,
            )
        except Exception:
            logger.exception("Не удалось выполнить необязательную операцию")

    return {
        "ok": True,
        "result": result,
        "bot_msg": bot_msg,
        "game_id": game_id,
        "bet": final["bet"],
        "reward": final["bet"] * 2,
        "player_field": final["player_field"],
        **({"bot_field": final["bot_field"]} if completed_status in {"win", "lose"} else {}),
        "player_shots": final["player_shots"],
        "bot_shots": final["bot_shots"],
        "turn": final["turn"],
        "status": final["status"],
        "balance": balance,
    }

# ========== API: ДРУЗЬЯ ==========

FRIEND_BONUS_PER = 2
FRIEND_BONUS_MAX = 20

def send_friend_notification_sync(chat_id: int, text: str):
    """Отправляет ЛС от имени бота."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        with httpx.Client(timeout=5) as client:
            client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True
            })
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")

@app.get("/api/friends")
def get_friends(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()

    # Список друзей
    cur.execute("""
        SELECT DISTINCT CASE WHEN user_id = %s THEN friend_id ELSE user_id END AS friend
        FROM friends
        WHERE user_id = %s OR friend_id = %s
    """, (user_id, user_id, user_id))
    friend_ids = [r["friend"] for r in cur.fetchall()]

    friends = []
    for fid in friend_ids:
        cur.execute("SELECT user_id, username, balance FROM users WHERE user_id = %s", (fid,))
        row = cur.fetchone()
        if row:
            friends.append({
                "user_id": row["user_id"],
                "username": row["username"],
                "balance": row["balance"],
            })

    # Входящие заявки
    cur.execute("""
        SELECT id, from_user FROM friend_requests
        WHERE to_user = %s AND status = 'pending'
    """, (user_id,))
    pending_rows = cur.fetchall()
    pending = []
    for p in pending_rows:
        cur.execute("SELECT username FROM users WHERE user_id = %s", (p["from_user"],))
        u = cur.fetchone()
        pending.append({
            "request_id": p["id"],
            "from_user": p["from_user"],
            "from_username": u["username"] if u else str(p["from_user"]),
        })

    conn.close()

    bonus = min(len(friends) * FRIEND_BONUS_PER, FRIEND_BONUS_MAX)
    return {
        "friends": friends,
        "pending": pending,
        "bonus": bonus,
    }
@app.post("/api/friends/add")
def add_friend(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    username = (payload.get("username") or "").lstrip("@").lower()

    if not username:
        raise HTTPException(status_code=400, detail="Укажи username")

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username FROM users WHERE LOWER(username) = %s", (username,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    target_id = row["user_id"]
    target_username = row["username"]
    if target_id == user_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Нельзя добавить себя")

    # Отправитель
    cur.execute("SELECT username FROM users WHERE user_id = %s", (user_id,))
    sender_row = cur.fetchone()
    sender_username = sender_row["username"] if sender_row else str(user_id)

    # Уже друзья?
    cur.execute("""
        SELECT id FROM friends WHERE
        (user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s)
    """, (user_id, target_id, target_id, user_id))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Вы уже друзья")

    # Уже есть заявка?
    cur.execute("""
        SELECT id FROM friend_requests
        WHERE from_user = %s AND to_user = %s AND status = 'pending'
    """, (user_id, target_id))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Заявка уже отправлена")

    # Встречная заявка?
    cur.execute("""
        SELECT id FROM friend_requests
        WHERE from_user = %s AND to_user = %s AND status = 'pending'
    """, (target_id, user_id))
    reverse = cur.fetchone()
    if reverse:
        cur.execute("UPDATE friend_requests SET status = 'accepted' WHERE id = %s", (reverse["id"],))
        cur.execute("""
            INSERT INTO friends (user_id, friend_id) VALUES (%s, %s)
            ON CONFLICT (user_id, friend_id) DO NOTHING
        """, (user_id, target_id))
        cur.execute("""
            INSERT INTO friends (user_id, friend_id) VALUES (%s, %s)
            ON CONFLICT (user_id, friend_id) DO NOTHING
        """, (target_id, user_id))
        conn.commit()
        conn.close()
        try:
            await_bot_send_friend_notification(
                target_id,
                f"✅ @{sender_username} принял твою заявку — вы теперь друзья!"
            )
        except Exception:
            logger.exception("Не удалось выполнить необязательную операцию")
        return {"ok": True, "mutual": True, "message": f"Вы и @{username} теперь друзья!"}
       
    # Обычная заявка (upsert — обновляем статус, если запись уже есть)
    cur.execute("""
        INSERT INTO friend_requests (from_user, to_user, status)
        VALUES (%s, %s, 'pending')
        ON CONFLICT (from_user, to_user) DO UPDATE SET status = 'pending'
    """, (user_id, target_id))
    conn.commit()
    conn.close()

    # Уведомление получателю в бот (ЛС)
    try:
        await_bot_send_friend_notification(
            target_id,
            f"📩 Заявка в друзья от @{sender_username}\n\n"
            f"Открой бота → раздел «👥 Друзья», чтобы принять или отклонить."
        )
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")

    return {"ok": True, "mutual": False, "message": f"Заявка отправлена @{username}"}

    # Уже друзья?
    cur.execute("""
        SELECT id FROM friends WHERE
        (user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s)
    """, (user_id, target_id, target_id, user_id))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Вы уже друзья")

    # Уже есть заявка?
    cur.execute("""
        SELECT id FROM friend_requests
        WHERE from_user = %s AND to_user = %s AND status = 'pending'
    """, (user_id, target_id))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Заявка уже отправлена")

    # Встречная заявка?
    cur.execute("""
        SELECT id FROM friend_requests
        WHERE from_user = %s AND to_user = %s AND status = 'pending'
    """, (target_id, user_id))
    reverse = cur.fetchone()
    if reverse:
        cur.execute("UPDATE friend_requests SET status = 'accepted' WHERE id = %s", (reverse["id"],))
        cur.execute("INSERT INTO friends (user_id, friend_id) VALUES (%s, %s)", (user_id, target_id))
        cur.execute("INSERT INTO friends (user_id, friend_id) VALUES (%s, %s)", (target_id, user_id))
        conn.commit()
        conn.close()
        return {"ok": True, "mutual": True, "message": f"Вы и @{username} теперь друзья!"}

    # Обычная заявка
    cur.execute("""
        INSERT INTO friend_requests (from_user, to_user) VALUES (%s, %s)
    """, (user_id, target_id))
    conn.commit()
    conn.close()
    return {"ok": True, "mutual": False, "message": f"Заявка отправлена @{username}"}


@app.post("/api/friends/accept")
def accept_friend(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    request_id = int(payload.get("request_id"))

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT from_user, to_user, status FROM friend_requests WHERE id = %s
    """, (request_id,))
    row = cur.fetchone()
    if not row or row["status"] != "pending":
        conn.close()
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if row["to_user"] != user_id:
        conn.close()
        raise HTTPException(status_code=403, detail="Не твоя заявка")

    from_u = row["from_user"]
    to_u = row["to_user"]

    cur.execute("UPDATE friend_requests SET status = 'accepted' WHERE id = %s", (request_id,))
    cur.execute("INSERT INTO friends (user_id, friend_id) VALUES (%s, %s)", (from_u, to_u))
    cur.execute("INSERT INTO friends (user_id, friend_id) VALUES (%s, %s)", (to_u, from_u))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Заявка принята"}


@app.post("/api/friends/decline")
def decline_friend(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    request_id = int(payload.get("request_id"))

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT to_user FROM friend_requests WHERE id = %s AND status = 'pending'
    """, (request_id,))
    row = cur.fetchone()
    if not row or row["to_user"] != user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    cur.execute("UPDATE friend_requests SET status = 'declined' WHERE id = %s", (request_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/friends/remove")
def remove_friend(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    friend_id = int(payload.get("friend_id"))

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM friends WHERE
        (user_id = %s AND friend_id = %s) OR (user_id = %s AND friend_id = %s)
    """, (user_id, friend_id, friend_id, user_id))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Удалён из друзей"}

# ========== API: КОСМЕТИКА ==========

NICK_COLORS = {
    "red":     {"name": "🔴 Красный",   "hex": "#ef4444"},
    "orange":  {"name": "🟠 Оранжевый", "hex": "#f97316"},
    "yellow":  {"name": "🟡 Жёлтый",    "hex": "#eab308"},
    "green":   {"name": "🟢 Зелёный",   "hex": "#22c55e"},
    "cyan":    {"name": "🩵 Голубой",   "hex": "#06b6d4"},
    "blue":    {"name": "🔵 Синий",     "hex": "#3b82f6"},
    "purple":  {"name": "🟣 Фиолетовый","hex": "#a855f7"},
    "pink":    {"name": "🩷 Розовый",   "hex": "#ec4899"},
    "white":   {"name": "⚪ Белый",     "hex": "#ffffff"},
    "black":   {"name": "⚫ Чёрный",    "hex": "#000000"},
    "gold":    {"name": "🟨 Золотой",   "hex": "#fbbf24"},
    "mint":    {"name": "🟩 Мятный",    "hex": "#6ee7b7"},
}
NICK_COLOR_PRICE = 15000

FRAMES = {
    "circle":  {"display": "○ {name} ○",  "price": 5000},
    "star":    {"display": "⭐ {name} ⭐", "price": 10000},
    "sparkle": {"display": "✨ {name} ✨", "price": 20000},
    "spiral":  {"display": "🌀 {name} 🌀", "price": 30000},
    "diamond": {"display": "💎 {name} 💎", "price": 100000},
    "crown":   {"display": "👑 {name} 👑", "price": 250000},
    "rainbow": {"display": "🌈 {name} 🌈", "price": 500000},
}

STATUSES = {
    "online":   {"name": "🟢 Онлайн",         "price": 2000},
    "busy":     {"name": "🔴 Занят",           "price": 2000},
    "sleep":    {"name": "😴 Сплю",            "price": 2000},
    "dnd":      {"name": "⛔ Не беспокоить",   "price": 3000},
    "vacation": {"name": "🏖️ В отпуске",       "price": 3000},
    "dream":    {"name": "💭 Мечтаю",          "price": 5000},
}

THEMES = {
    "reef":     {"name": "🏝️ Lavender Dream", "price": 0},
    "dark":     {"name": "🌙 Тёмная",        "price": 0},
    "light":    {"name": "☀️ Светлая",       "price": 0},
    "neon":     {"name": "🌈 Неоновая",      "price": 50000},
    "autumn":   {"name": "🍂 Осенняя",       "price": 25000},
    "azure":    {"name": "🌊 Лазурная",      "price": 25000},
    "sand":     {"name": "🏖️ Песочная",      "price": 25000},
    "white":    {"name": "🤍 Белая",         "price": 30000},
    "green":    {"name": "🌿 Зелёная",       "price": 25000},
    "hell":     {"name": "😈 Адская",        "price": 75000},
    "orange":   {"name": "🔶 Чёрно-оранжевая","price": 50000},
    "paper":    {"name": "📜 Бумажная",      "price": 35000},
    "glass":    {"name": "🪟 Прозрачная",    "price": 100000},
    "pixel":    {"name": "👾 Пиксельная",    "price": 80000},
    "terminal": {"name": "💻 Терминал",      "price": 60000},
    "ice":      {"name": "❄️ Ледяная",       "price": 40000},
    "graphite": {"name": "🩶 Графитовая",    "price": 20000},
    "rgb":      {"name": "🌈 RGB-переливающаяся", "price": 200000},
    "ocean":    {"name": "🌊 Океаническая глубина", "price": 75000},
}

BADGES = {
    "champion": "🏆", "millionaire": "💰", "multi_million": "💎",
    "sniper": "🎯", "gamer": "🎮", "collector": "🐾",
    "egg_hunter": "🥚", "builder": "🏝️", "social": "👥",
    "veteran": "⚔️", "artist": "🎨", "ruler": "👑",
}


@app.get("/api/cosmetics")
def get_cosmetics_api(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()

    # Текущая косметика пользователя
    cur.execute("""
        SELECT nickname_color, frame, status, theme FROM user_cosmetics WHERE user_id = %s
    """, (user_id,))
    row = cur.fetchone()
    current = {
        "nickname_color": row["nickname_color"] if row else None,
        "frame": row["frame"] if row else None,
        "status": row["status"] if row else None,
        "theme": (row["theme"] if row and row["theme"] else "reef"),
    }

    # Значки
    cur.execute("SELECT badge FROM user_badges WHERE user_id = %s", (user_id,))
    badges = [r["badge"] for r in cur.fetchall()]

    # Купленная косметика
    cur.execute("""
        SELECT item_type, item_key FROM user_cosmetics_owned WHERE user_id = %s
    """, (user_id,))
    owned_rows = cur.fetchall()
    owned = {"color": [], "frame": [], "status": [], "theme": []}
    for o_row in owned_rows:
        if o_row["item_type"] in owned:
            owned[o_row["item_type"]].append(o_row["item_key"])

    balance = require_balance(cur, user_id)
    conn.close()

    return {
        "current": current,
        "balance": balance,
        "colors": NICK_COLORS,
        "color_price": NICK_COLOR_PRICE,
        "frames": FRAMES,
        "statuses": STATUSES,
        "themes": THEMES,
        "badges": badges,
        "owned": owned,
    }


@app.post("/api/cosmetics/buy")
def buy_cosmetics(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    item_type = payload.get("type")
    item_key = payload.get("key")

    price = 0
    if item_type == "color":
        if item_key not in NICK_COLORS:
            raise HTTPException(status_code=404, detail="Неизвестный цвет")
        price = NICK_COLOR_PRICE
    elif item_type == "frame":
        if item_key not in FRAMES:
            raise HTTPException(status_code=404, detail="Неизвестная рамка")
        price = FRAMES[item_key]["price"]
    elif item_type == "status":
        if item_key not in STATUSES:
            raise HTTPException(status_code=404, detail="Неизвестный статус")
        price = STATUSES[item_key]["price"]
    elif item_type == "theme":
        if item_key not in THEMES:
            raise HTTPException(status_code=404, detail="Неизвестная тема")
        price = THEMES[item_key]["price"]
    else:
        raise HTTPException(status_code=400, detail="Неизвестный тип")

    conn = db()
    cur = conn.cursor()

    # Уже куплено?
    cur.execute("""
        SELECT 1 FROM user_cosmetics_owned
        WHERE user_id = %s AND item_type = %s AND item_key = %s
    """, (user_id, item_type, item_key))
    already_owned = cur.fetchone() is not None

    # Применяем
    col_map = {"color": "nickname_color", "frame": "frame",
               "status": "status", "theme": "theme"}
    col = col_map[item_type]

    if already_owned or price == 0:
        # Просто применяем, без списания
        cur.execute("INSERT INTO user_cosmetics (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
        cur.execute(f"UPDATE user_cosmetics SET {col} = %s WHERE user_id = %s", (item_key, user_id))
        if not already_owned:
            cur.execute("""
                INSERT INTO user_cosmetics_owned (user_id, item_type, item_key)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
            """, (user_id, item_type, item_key))
        conn.commit()
        balance = require_balance(cur, user_id)
        conn.close()
        msg = "✅ Применено (уже куплено)" if already_owned else "✅ Применено бесплатно"
        return {"ok": True, "message": msg, "balance": balance}

    if not change_balance(cur, user_id, -price):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Недостаточно монет. Нужно: {price:,}")

    cur.execute("INSERT INTO user_cosmetics (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    cur.execute(f"UPDATE user_cosmetics SET {col} = %s WHERE user_id = %s", (item_key, user_id))
    cur.execute("""
        INSERT INTO user_cosmetics_owned (user_id, item_type, item_key)
        VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
    """, (user_id, item_type, item_key))
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {"ok": True, "message": f"Куплено за {price:,} 🪙", "balance": balance}


@app.post("/api/cosmetics/apply")
def apply_cosmetics(payload: dict = Body(...)):
    """Применяет уже купленную косметику без оплаты."""
    user_id = get_telegram_user_id(payload.get("initData", ""))
    item_type = payload.get("type")
    item_key = payload.get("key")

    col_map = {"color": "nickname_color", "frame": "frame",
               "status": "status", "theme": "theme"}
    if item_type not in col_map:
        raise HTTPException(status_code=400, detail="Неизвестный тип")
    col = col_map[item_type]

    conn = db()
    cur = conn.cursor()
    valid_keys = {
        "color": NICK_COLORS,
        "frame": FRAMES,
        "status": STATUSES,
        "theme": THEMES,
    }[item_type]
    if item_key not in valid_keys:
        conn.close()
        raise HTTPException(status_code=404, detail="Неизвестный предмет косметики")

    cur.execute(
        "SELECT 1 FROM user_cosmetics_owned WHERE user_id = %s AND item_type = %s AND item_key = %s",
        (user_id, item_type, item_key),
    )
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=403, detail="Эта косметика ещё не куплена")

    cur.execute("INSERT INTO user_cosmetics (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    cur.execute(f"UPDATE user_cosmetics SET {col} = %s WHERE user_id = %s", (item_key, user_id))
    conn.commit()
    conn.close()
    return {"ok": True}

# ========== API: ОСТРОВ ==========

ISLAND_PRICES = {
    "island": 50000,
    "house": 30000,
    "pier": 45000,
    "ship": 100000,
}
ISLAND_NAMES = {
    "island": "🏝️ Остров",
    "house": "🏠 Домик",
    "pier": "⚓ Причал",
    "ship": "🚢 Корабль",
}
HOUSE_INCOME = 5000
HOUSE_COOLDOWN = 5 * 3600
SHIP_INCOME = 75000
SHIP_COOLDOWN = 48 * 3600



# ========== ТРЕЙДЫ ПРЕДМЕТАМИ MINI APP ==========

TRADE_ITEM_TYPES = {"color", "frame", "status", "theme"}


@app.get("/api/trades/inventory")
def get_trade_inventory(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT item_type, item_key FROM user_cosmetics_owned WHERE user_id = %s ORDER BY item_type, item_key",
        (user_id,),
    )
    items = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"items": items}


@app.get("/api/trades/target-inventory")
def get_target_trade_inventory(username: str = Query(...), init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    target_name = username.strip().lstrip("@")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE LOWER(REPLACE(username, '@', '')) = LOWER(%s) LIMIT 1", (target_name,))
    target = cur.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Игрок не найден")
    target_id = int(target["user_id"])
    if target_id == user_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Нельзя обмениваться с самим собой")
    cur.execute("SELECT item_type, item_key FROM user_cosmetics_owned WHERE user_id = %s ORDER BY item_type, item_key", (target_id,))
    items = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"items": items}


@app.get("/api/trades")
def get_trades(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.id, t.from_user, t.to_user, t.offer_type, t.offer_key,
               t.request_type, t.request_key, t.status, t.created_at,
               u1.username AS from_username, u2.username AS to_username
        FROM web_trades t
        LEFT JOIN users u1 ON u1.user_id = t.from_user
        LEFT JOIN users u2 ON u2.user_id = t.to_user
        WHERE (t.from_user = %s OR t.to_user = %s)
          AND t.status = 'pending'
        ORDER BY t.id DESC LIMIT 50
    """, (user_id, user_id))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return {"trades": rows}


@app.post("/api/trades/create")
def create_trade(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    target_username = str(payload.get("target_username", "")).strip().lstrip("@")
    offer_type = payload.get("offer_type")
    offer_key = payload.get("offer_key")
    request_type = payload.get("request_type")
    request_key = payload.get("request_key")
    if not target_username or not offer_key or not request_key:
        raise HTTPException(status_code=400, detail="Заполни получателя и оба предмета")
    if offer_type not in TRADE_ITEM_TYPES or request_type not in TRADE_ITEM_TYPES:
        raise HTTPException(status_code=400, detail="Неизвестный тип предмета")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE LOWER(REPLACE(username, '@', '')) = LOWER(%s) LIMIT 1", (target_username,))
    target = cur.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Игрок не найден")
    target_id = int(target["user_id"])
    if target_id == user_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Нельзя обмениваться с самим собой")
    cur.execute("SELECT 1 FROM user_cosmetics_owned WHERE user_id = %s AND item_type = %s AND item_key = %s",
                (user_id, offer_type, offer_key))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="У тебя нет предмета, который предлагаешь")
    cur.execute("SELECT 1 FROM user_cosmetics_owned WHERE user_id = %s AND item_type = %s AND item_key = %s",
                (target_id, request_type, request_key))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="У выбранного игрока нет запрошенного предмета")
    cur.execute("SELECT 1 FROM user_cosmetics_owned WHERE user_id = %s AND item_type = %s AND item_key = %s",
                (user_id, request_type, request_key))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Ты уже владеешь запрошенным предметом")
    cur.execute("""
        INSERT INTO web_trades (from_user, to_user, offer_type, offer_key, request_type, request_key, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'pending')
    """, (user_id, target_id, offer_type, offer_key, request_type, request_key))
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"ok": True, "trade_id": trade_id, "message": "Предложение создано. Обмен произойдёт только после принятия."}


@app.post("/api/trades/resolve")
def resolve_trade(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        trade_id = int(payload.get("trade_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный ID обмена")
    action = payload.get("action")
    if action not in {"accept", "decline", "cancel"}:
        raise HTTPException(status_code=400, detail="Неизвестное действие")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM web_trades WHERE id = %s", (trade_id,))
    trade = cur.fetchone()
    if not trade or trade["status"] != "pending":
        conn.close()
        raise HTTPException(status_code=404, detail="Предложение уже недоступно")
    if action == "cancel":
        if user_id != trade["from_user"]:
            conn.close()
            raise HTTPException(status_code=403, detail="Отменить предложение может только автор")
        cur.execute("UPDATE web_trades SET status = 'cancelled', resolved_at = CURRENT_TIMESTAMP WHERE id = %s AND status = 'pending'", (trade_id,))
        conn.commit()
        conn.close()
        return {"ok": True, "message": "Предложение отменено"}
    if user_id != trade["to_user"]:
        conn.close()
        raise HTTPException(status_code=403, detail="Только получатель может ответить на предложение")
    if action == "decline":
        cur.execute("UPDATE web_trades SET status = 'declined', resolved_at = CURRENT_TIMESTAMP WHERE id = %s AND status = 'pending'", (trade_id,))
        conn.commit()
        conn.close()
        return {"ok": True, "message": "Предложение отклонено"}

    # Проверяем владение обеими вещами заново внутри транзакции.
    a = (trade["from_user"], trade["offer_type"], trade["offer_key"])
    b = (trade["to_user"], trade["request_type"], trade["request_key"])
    cur.execute("SELECT 1 FROM user_cosmetics_owned WHERE user_id = %s AND item_type = %s AND item_key = %s", a)
    sender_owns = cur.fetchone() is not None
    cur.execute("SELECT 1 FROM user_cosmetics_owned WHERE user_id = %s AND item_type = %s AND item_key = %s", b)
    receiver_owns = cur.fetchone() is not None
    cur.execute("SELECT 1 FROM user_cosmetics_owned WHERE user_id = %s AND item_type = %s AND item_key = %s",
                (trade["from_user"], trade["request_type"], trade["request_key"]))
    sender_would_duplicate = cur.fetchone() is not None
    cur.execute("SELECT 1 FROM user_cosmetics_owned WHERE user_id = %s AND item_type = %s AND item_key = %s",
                (trade["to_user"], trade["offer_type"], trade["offer_key"]))
    receiver_would_duplicate = cur.fetchone() is not None
    if not sender_owns or not receiver_owns or sender_would_duplicate or receiver_would_duplicate:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail="Предметы изменились или обмен создаст дубликат. Предложение нельзя принять.")

    cur.execute("DELETE FROM user_cosmetics_owned WHERE user_id = %s AND item_type = %s AND item_key = %s", a)
    cur.execute("DELETE FROM user_cosmetics_owned WHERE user_id = %s AND item_type = %s AND item_key = %s", b)
    cur.execute("INSERT INTO user_cosmetics_owned (user_id, item_type, item_key) VALUES (%s, %s, %s)",
                (trade["from_user"], trade["request_type"], trade["request_key"]))
    cur.execute("INSERT INTO user_cosmetics_owned (user_id, item_type, item_key) VALUES (%s, %s, %s)",
                (trade["to_user"], trade["offer_type"], trade["offer_key"]))
    # If an equipped item leaves its owner, clear the matching equipped slot.
    col_map = {"color": "nickname_color", "frame": "frame", "status": "status", "theme": "theme"}
    for owner_id, item_type, item_key in (a, b):
        col = col_map[item_type]
        cur.execute(f"UPDATE user_cosmetics SET {col} = NULL WHERE user_id = %s AND {col} = %s", (owner_id, item_key))
    cur.execute("UPDATE web_trades SET status = 'accepted', resolved_at = CURRENT_TIMESTAMP WHERE id = %s AND status = 'pending'", (trade_id,))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=409, detail="Предложение уже обработано")
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Обмен завершён. Оба предмета переданы."}


@app.get("/api/island")
def get_island(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT has_island, has_house, has_pier, has_ship,
               last_house_income, last_ship_income
        FROM islands WHERE user_id = %s
    """, (user_id,))
    row = cur.fetchone()
    balance = require_balance(cur, user_id)
    conn.close()

    if not row:
        state = {"has_island": False, "has_house": False, "has_pier": False, "has_ship": False,
                 "last_house_income": None, "last_ship_income": None}
    else:
        state = {
            "has_island": bool(row["has_island"]),
            "has_house": bool(row["has_house"]),
            "has_pier": bool(row["has_pier"]),
            "has_ship": bool(row["has_ship"]),
            "last_house_income": row["last_house_income"],
            "last_ship_income": row["last_ship_income"],
        }

    return {
        "state": state,
        "prices": ISLAND_PRICES,
        "names": ISLAND_NAMES,
        "balance": balance,
        "house_income": HOUSE_INCOME,
        "house_cooldown": HOUSE_COOLDOWN,
        "ship_income": SHIP_INCOME,
        "ship_cooldown": SHIP_COOLDOWN,
    }


@app.post("/api/island/buy")
def buy_island(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    item = payload.get("item")

    if item not in ISLAND_PRICES:
        raise HTTPException(status_code=400, detail="Неизвестная постройка")

    conn = db()
    cur = conn.cursor()

    # Проверяем состояние
    cur.execute("""
        SELECT has_island, has_house, has_pier, has_ship
        FROM islands WHERE user_id = %s
    """, (user_id,))
    row = cur.fetchone()

    has_island = bool(row["has_island"]) if row else False
    has_house = bool(row["has_house"]) if row else False
    has_pier = bool(row["has_pier"]) if row else False
    has_ship = bool(row["has_ship"]) if row else False

    # Уже куплено?
    if item == "island" and has_island:
        conn.close(); raise HTTPException(status_code=400, detail="Уже куплено")
    if item == "house" and has_house:
        conn.close(); raise HTTPException(status_code=400, detail="Уже куплено")
    if item == "pier" and has_pier:
        conn.close(); raise HTTPException(status_code=400, detail="Уже куплено")
    if item == "ship" and has_ship:
        conn.close(); raise HTTPException(status_code=400, detail="Уже куплено")

    # Проверка порядка
    if item in ("house", "pier", "ship") and not has_island:
        conn.close(); raise HTTPException(status_code=400, detail="Сначала купи остров")
    if item == "ship" and not has_pier:
        conn.close(); raise HTTPException(status_code=400, detail="Сначала построй причал")

    price = ISLAND_PRICES[item]
    if not change_balance(cur, user_id, -price):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Недостаточно монет. Нужно: {price:,}")

    cur.execute("INSERT INTO islands (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    cur.execute(f"UPDATE islands SET has_{item} = TRUE WHERE user_id = %s", (user_id,))

    if item == "house":
        cur.execute("UPDATE islands SET last_house_income = %s WHERE user_id = %s",
                    (datetime.now().isoformat(), user_id))
    if item == "ship":
        cur.execute("UPDATE islands SET last_ship_income = %s WHERE user_id = %s",
                    (datetime.now().isoformat(), user_id))

    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {"ok": True, "message": f"Куплено: {ISLAND_NAMES[item]}", "balance": balance}

# ========== API: КЛАНЫ ==========

CLAN_CREATE_COST = 500000
CLAN_MAX_MEMBERS = 20
CLAN_BONUS_PER_MEMBER = 1
CLAN_BONUS_MAX = 10


def _get_user_clan(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, c.emoji, c.leader_id, c.description, c.bank, cm.role
        FROM clans c
        JOIN clan_members cm ON cm.clan_id = c.id
        WHERE cm.user_id = %s
    """, (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row["id"], "name": row["name"], "emoji": row["emoji"],
        "leader_id": row["leader_id"], "description": row["description"],
        "bank": row["bank"], "role": row["role"],
    }


def _clan_member_count(cur, clan_id: int) -> int:
    cur.execute("SELECT COUNT(*) c FROM clan_members WHERE clan_id = %s", (clan_id,))
    return cur.fetchone()["c"]


@app.get("/api/clans")
def get_clans(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()

    # Мой клан
    my_clan = None
    cur.execute("""
        SELECT c.id, c.name, c.emoji, c.leader_id, c.description, c.bank, cm.role
        FROM clans c
        JOIN clan_members cm ON cm.clan_id = c.id
        WHERE cm.user_id = %s
    """, (user_id,))
    row = cur.fetchone()
    if row:
        clan_id = row["id"]
        # Участники
        cur.execute("""
            SELECT cm.user_id, cm.role, u.username, u.balance
            FROM clan_members cm
            JOIN users u ON u.user_id = cm.user_id
            WHERE cm.clan_id = %s
            ORDER BY
                CASE cm.role WHEN 'leader' THEN 1 WHEN 'deputy' THEN 2 ELSE 3 END,
                cm.joined_at
        """, (clan_id,))
        members = [
            {"user_id": m["user_id"], "role": m["role"],
             "username": m["username"], "balance": m["balance"]}
            for m in cur.fetchall()
        ]

        # Заявки (только для лидера/зама)
        pending = []
        if row["role"] in ("leader", "deputy"):
            cur.execute("""
                SELECT cr.id, cr.user_id, u.username
                FROM clan_requests cr
                JOIN users u ON u.user_id = cr.user_id
                WHERE cr.clan_id = %s AND cr.status = 'pending'
            """, (clan_id,))
            pending = [
                {"request_id": p["id"], "user_id": p["user_id"], "username": p["username"]}
                for p in cur.fetchall()
            ]

        my_clan = {
            "id": row["id"], "name": row["name"], "emoji": row["emoji"],
            "leader_id": row["leader_id"], "description": row["description"],
            "bank": row["bank"], "role": row["role"],
            "members": members,
            "pending": pending,
        }

    # Топ кланов
    cur.execute("""
        SELECT c.id, c.name, c.emoji, c.bank,
               (SELECT COUNT(*) FROM clan_members WHERE clan_id = c.id) AS members
        FROM clans c
        ORDER BY c.bank DESC LIMIT 10
    """)
    top = [
        {"id": t["id"], "name": t["name"], "emoji": t["emoji"],
         "bank": t["bank"], "members": t["members"]}
        for t in cur.fetchall()
    ]

    # Все кланы (для поиска)
    cur.execute("""
        SELECT c.id, c.name, c.emoji, c.bank,
               (SELECT COUNT(*) FROM clan_members WHERE clan_id = c.id) AS members
        FROM clans c ORDER BY c.created_at DESC LIMIT 50
    """)
    all_clans = [
        {"id": t["id"], "name": t["name"], "emoji": t["emoji"],
         "bank": t["bank"], "members": t["members"]}
        for t in cur.fetchall()
    ]

    balance = require_balance(cur, user_id)
    conn.close()

    return {
        "my_clan": my_clan,
        "top": top,
        "all_clans": all_clans,
        "balance": balance,
        "create_cost": CLAN_CREATE_COST,
        "max_members": CLAN_MAX_MEMBERS,
    }


@app.post("/api/clans/create")
def create_clan(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    name = (payload.get("name") or "").strip()
    emoji = (payload.get("emoji") or "🏛️").strip()
    description = (payload.get("description") or "").strip()

    if len(name) < 3 or len(name) > 20:
        raise HTTPException(status_code=400, detail="Название должно быть 3–20 символов")

    conn = db()
    cur = conn.cursor()

    # Уже в клане?
    cur.execute("SELECT 1 FROM clan_members WHERE user_id = %s", (user_id,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Ты уже в клане")

    cur.execute("SELECT id FROM clans WHERE LOWER(name) = %s", (name.lower(),))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Такое название уже занято")

    if not change_balance(cur, user_id, -CLAN_CREATE_COST):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Недостаточно монет. Нужно: {CLAN_CREATE_COST:,}")

    cur.execute("""
        INSERT INTO clans (name, emoji, leader_id, description)
        VALUES (%s, %s, %s, %s)
    """, (name, emoji, user_id, description))
    cur.execute("SELECT currval(pg_get_serial_sequence('clans', 'id'))")
    clan_id = cur.fetchone()["currval"]

    cur.execute("""
        INSERT INTO clan_members (user_id, clan_id, role)
        VALUES (%s, %s, 'leader')
    """, (user_id, clan_id))
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {"ok": True, "message": f"Клан {emoji} {name} создан!", "balance": balance}


@app.post("/api/clans/join")
def join_clan(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    clan_id = int(payload.get("clan_id"))

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM clan_members WHERE user_id = %s", (user_id,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Ты уже в клане")

    cur.execute("SELECT id FROM clans WHERE id = %s", (clan_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Клан не найден")

    if _clan_member_count(cur, clan_id) >= CLAN_MAX_MEMBERS:
        conn.close()
        raise HTTPException(status_code=400, detail="В клане нет мест")

    cur.execute("""
        INSERT INTO clan_requests (clan_id, user_id)
        VALUES (%s, %s)
        ON CONFLICT (clan_id, user_id) DO UPDATE SET status = 'pending'
    """, (clan_id, user_id))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Заявка отправлена"}


@app.post("/api/clans/accept")
def accept_clan_request(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    request_id = int(payload.get("request_id"))

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT clan_id, user_id, status FROM clan_requests WHERE id = %s", (request_id,))
    row = cur.fetchone()
    if not row or row["status"] != "pending":
        conn.close()
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    clan_id, target_user = row["clan_id"], row["user_id"]

    # Проверка роли
    cur.execute("SELECT role FROM clan_members WHERE user_id = %s AND clan_id = %s",
                (user_id, clan_id))
    rev = cur.fetchone()
    if not rev or rev["role"] not in ("leader", "deputy"):
        conn.close()
        raise HTTPException(status_code=403, detail="Нет прав")

    if _clan_member_count(cur, clan_id) >= CLAN_MAX_MEMBERS:
        conn.close()
        raise HTTPException(status_code=400, detail="В клане нет мест")

    cur.execute("SELECT 1 FROM clan_members WHERE user_id = %s", (target_user,))
    if cur.fetchone():
        cur.execute("UPDATE clan_requests SET status = 'rejected' WHERE id = %s", (request_id,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="Пользователь уже в клане")

    cur.execute("UPDATE clan_requests SET status = 'accepted' WHERE id = %s", (request_id,))
    cur.execute("INSERT INTO clan_members (user_id, clan_id, role) VALUES (%s, %s, 'member')",
                (target_user, clan_id))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Принят в клан"}


@app.post("/api/clans/decline")
def decline_clan_request(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    request_id = int(payload.get("request_id"))

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT clan_id FROM clan_requests WHERE id = %s AND status = 'pending'", (request_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    clan_id = row["clan_id"]

    cur.execute("SELECT role FROM clan_members WHERE user_id = %s AND clan_id = %s",
                (user_id, clan_id))
    rev = cur.fetchone()
    if not rev or rev["role"] not in ("leader", "deputy"):
        conn.close()
        raise HTTPException(status_code=403, detail="Нет прав")

    cur.execute("UPDATE clan_requests SET status = 'rejected' WHERE id = %s", (request_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/clans/leave")
def leave_clan(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT clan_id, role FROM clan_members WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=400, detail="Ты не в клане")
    if row["role"] == "leader":
        conn.close()
        raise HTTPException(status_code=400, detail="Лидер не может выйти. Распусти клан.")

    cur.execute("DELETE FROM clan_members WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Ты вышел из клана"}


@app.post("/api/clans/deposit")
def deposit_clan(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    amount = int(payload.get("amount") or 0)

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше 0")

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT clan_id FROM clan_members WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=400, detail="Ты не в клане")
    clan_id = row["clan_id"]

    if not change_balance(cur, user_id, -amount):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    cur.execute("UPDATE clans SET bank = bank + %s WHERE id = %s", (amount, clan_id))
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {"ok": True, "message": f"Внесено {amount:,} 🪙", "balance": balance}


@app.post("/api/clans/disband")
def disband_clan(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT clan_id, role FROM clan_members WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=400, detail="Ты не в клане")
    if row["role"] != "leader":
        conn.close()
        raise HTTPException(status_code=403, detail="Только лидер может распустить клан")

    clan_id = row["clan_id"]
    cur.execute("SELECT bank FROM clans WHERE id = %s", (clan_id,))
    bank = cur.fetchone()["bank"] or 0

    if bank > 0:
        change_balance(cur, user_id, bank)

    cur.execute("DELETE FROM clan_members WHERE clan_id = %s", (clan_id,))
    cur.execute("DELETE FROM clan_requests WHERE clan_id = %s", (clan_id,))
    cur.execute("DELETE FROM clans WHERE id = %s", (clan_id,))
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {"ok": True, "message": f"Клан распущен. Казна {bank:,} 🪙 возвращена.", "balance": balance}


@app.post("/api/clans/kick")
def kick_from_clan(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    target_query = payload.get("username") or payload.get("user_id")

    conn_tmp = db()
    cur_tmp = conn_tmp.cursor()
    if isinstance(target_query, str) and not target_query.isdigit():
        q = target_query.lstrip("@").lower()
        cur_tmp.execute("SELECT user_id FROM users WHERE LOWER(username) = %s", (q,))
        row = cur_tmp.fetchone()
        conn_tmp.close()
        if not row:
            raise HTTPException(status_code=404, detail="Игрок не найден")
        target_id = row["user_id"]
    else:
        conn_tmp.close()
        target_id = int(target_query)

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT clan_id, role FROM clan_members WHERE user_id = %s", (user_id,))
    rev = cur.fetchone()
    if not rev or rev["role"] not in ("leader", "deputy"):
        conn.close()
        raise HTTPException(status_code=403, detail="Нет прав")
    clan_id = rev["clan_id"]

    cur.execute("SELECT clan_id, role FROM clan_members WHERE user_id = %s", (target_id,))
    tgt = cur.fetchone()
    if not tgt or tgt["clan_id"] != clan_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Игрок не из твоего клана")

    cur.execute("SELECT leader_id FROM clans WHERE id = %s", (clan_id,))
    leader_id = cur.fetchone()["leader_id"]
    if target_id == leader_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Нельзя кикнуть лидера")
    if tgt["role"] == "deputy" and rev["role"] != "leader":
        conn.close()
        raise HTTPException(status_code=400, detail="Зам не может кикнуть зама")

    cur.execute("DELETE FROM clan_members WHERE user_id = %s", (target_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Игрок кикнут"}

# ========== API: МУЗЫКА ==========

MUSIC_PERSONAL_BASE = 15
MUSIC_SHARED_MAX = 100
MUSIC_SLOT_PRICE = 2000


@app.get("/api/music/personal")
def music_personal_get(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, url, title, author, platform, added_at
        FROM music_personal WHERE user_id = %s
        ORDER BY added_at DESC
    """, (user_id,))
    tracks = [
        {"id": r["id"], "url": r["url"], "title": r["title"],
         "author": r["author"], "platform": r["platform"], "added_at": r["added_at"]}
        for r in cur.fetchall()
    ]

    cur.execute("SELECT extra_slots FROM music_slots WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    extra = row["extra_slots"] if row else 0
    limit = MUSIC_PERSONAL_BASE + extra

    balance = require_balance(cur, user_id)
    conn.close()

    return {
        "tracks": tracks,
        "limit": limit,
        "slots_used": len(tracks),
        "slot_price": MUSIC_SLOT_PRICE,
        "balance": balance,
    }


@app.get("/api/music/shared")
def music_shared_get(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ms.id, ms.url, ms.title, ms.author, ms.platform, ms.added_at, u.username
        FROM music_shared ms
        LEFT JOIN users u ON u.user_id = ms.user_id
        WHERE ms.status = 'approved'
        ORDER BY ms.reviewed_at DESC NULLS LAST
    """)
    tracks = [
        {"id": r["id"], "url": r["url"], "title": r["title"],
         "author": r["author"], "platform": r["platform"],
         "added_at": r["added_at"], "added_by": r["username"]}
        for r in cur.fetchall()
    ]

    # Список на модерации (только для админов)
    pending = []
    is_admin = config.is_full_admin(user_id)
    if is_admin:
        cur.execute("""
            SELECT ms.id, ms.url, ms.title, ms.author, ms.platform, ms.added_at,
                   u.username, ms.user_id
            FROM music_shared ms
            LEFT JOIN users u ON u.user_id = ms.user_id
            WHERE ms.status = 'pending'
            ORDER BY ms.added_at ASC
        """)
        pending = [
            {"id": r["id"], "url": r["url"], "title": r["title"],
             "author": r["author"], "platform": r["platform"],
             "added_at": r["added_at"], "added_by": r["username"], "user_id": r["user_id"]}
            for r in cur.fetchall()
        ]

    conn.close()
    return {"tracks": tracks, "pending": pending, "is_admin": is_admin, "max_tracks": MUSIC_SHARED_MAX}


@app.post("/api/music/add_personal")
def music_add_personal(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    url = (payload.get("url") or "").strip()

    if not url:
        raise HTTPException(status_code=400, detail="Укажи ссылку")

    info = _music_get_info(url)
    if not info:
        raise HTTPException(status_code=400, detail="Не удалось получить информацию о треке. Поддерживаются только YouTube и SoundCloud.")

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT extra_slots FROM music_slots WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    extra = row["extra_slots"] if row else 0
    limit = MUSIC_PERSONAL_BASE + extra

    cur.execute("SELECT COUNT(*) c FROM music_personal WHERE user_id = %s", (user_id,))
    count = cur.fetchone()["c"]
    if count >= limit:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Плейлист полон ({count}/{limit}). Купи слот за {MUSIC_SLOT_PRICE:,} 🪙")

    cur.execute("""
        INSERT INTO music_personal (user_id, url, title, author, platform)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, url, info["title"], info["author"], info["platform"]))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"Добавлено: {info['title']} — {info['author']}"}


@app.post("/api/music/add_shared")
def music_add_shared(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    url = (payload.get("url") or "").strip()

    if not url:
        raise HTTPException(status_code=400, detail="Укажи ссылку")

    info = _music_get_info(url)
    if not info:
        raise HTTPException(status_code=400, detail="Не удалось получить информацию. Только YouTube и SoundCloud.")

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) c FROM music_shared WHERE status = 'approved'")
    count = cur.fetchone()["c"]
    if count >= MUSIC_SHARED_MAX:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Общий плейлист полон ({MUSIC_SHARED_MAX})")

    cur.execute("""
        INSERT INTO music_shared (user_id, url, title, author, platform, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
    """, (user_id, url, info["title"], info["author"], info["platform"]))
    conn.commit()

    # Отправим уведомления админам
    sender = _get_username(user_id)
    for aid in ADMIN_IDS:
        if aid == user_id:
            continue
        _bot_send_music_moderation(aid, info["title"], info["author"], sender, url)
    # Владельцу тоже
    if config.OWNER_ID not in ADMIN_IDS:
        _bot_send_music_moderation(config.OWNER_ID, info["title"], info["author"], sender, url)

    conn.close()
    return {"ok": True, "message": "Трек отправлен на модерацию"}


@app.post("/api/music/review")
def music_review(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)

    track_id = int(payload.get("track_id"))
    approve = bool(payload.get("approve"))

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM music_shared WHERE id = %s AND status = 'pending'", (track_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Трек не найден или уже рассмотрен")

    if row["user_id"] == user_id:
        conn.close()
        raise HTTPException(status_code=403, detail="Нельзя одобрить свой же трек")

    new_status = "approved" if approve else "rejected"
    cur.execute("""
        UPDATE music_shared SET status = %s, reviewed_by = %s, reviewed_at = %s WHERE id = %s
    """, (new_status, user_id, datetime.now().isoformat(), track_id))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Трек одобрен" if approve else "Трек отклонён"}


@app.post("/api/music/delete_personal")
def music_delete_personal(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    track_id = int(payload.get("track_id"))

    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM music_personal WHERE id = %s AND user_id = %s", (track_id, user_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="Трек не найден")
    return {"ok": True, "message": "Удалено"}


@app.post("/api/music/delete_shared")
def music_delete_shared(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    track_id = int(payload.get("track_id"))

    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM music_shared WHERE id = %s", (track_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Удалено из общего плейлиста"}


@app.post("/api/music/buy_slot")
def music_buy_slot(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))

    conn = db()
    cur = conn.cursor()
    if not change_balance(cur, user_id, -MUSIC_SLOT_PRICE):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Недостаточно монет. Нужно: {MUSIC_SLOT_PRICE:,}")

    cur.execute("""
        INSERT INTO music_slots (user_id, extra_slots) VALUES (%s, 1)
        ON CONFLICT (user_id) DO UPDATE SET extra_slots = music_slots.extra_slots + 1
    """, (user_id,))
    conn.commit()

    cur.execute("SELECT extra_slots FROM music_slots WHERE user_id = %s", (user_id,))
    extra = cur.fetchone()["extra_slots"]
    balance = require_balance(cur, user_id)
    conn.close()
    return {"ok": True, "message": f"Куплен слот. Теперь лимит: {MUSIC_PERSONAL_BASE + extra}",
            "balance": balance, "limit": MUSIC_PERSONAL_BASE + extra}


# Вспомогательные функции



def _music_get_info(url: str):
    """oEmbed для YouTube и SoundCloud."""
    url_lower = url.lower()
    try:
        if "youtube.com" in url_lower or "youtu.be" in url_lower:
            oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
            platform = "youtube"
        elif "soundcloud.com" in url_lower:
            oembed_url = f"https://soundcloud.com/oembed?url={url}&format=json"
            platform = "soundcloud"
        else:
            return None

        with httpx.Client(timeout=5) as client:
            r = client.get(oembed_url)
            if r.status_code != 200:
                return None
            data = r.json()
            return {
                "title": data.get("title", "Без названия"),
                "author": data.get("author_name", "Неизвестен"),
                "platform": platform,
            }
    except Exception:
        return None


def _get_username(user_id: int) -> str:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT username FROM users WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row["username"] if row and row["username"] else str(user_id)


def _bot_send_music_moderation(admin_id: int, title: str, author: str, sender: str, url: str):
    """Отправляет ЛС админу с треком на модерацию."""
    text = (
        f"🎵 Новый трек на модерацию\n\n"
        f"📌 {title}\n"
        f"👤 Автор: {author}\n"
        f"👥 Добавил: @{sender}\n"
        f"🔗 {url}\n\n"
        f"Открой веб-версию → 🎵 Музыка → Общий плейлист, чтобы одобрить."
    )
    _safe_bot_send(admin_id, text)

# ========== API: RCC ==========

RCC_CLICK_AMOUNT = 0.01
RCC_EXCHANGE_RATE = 5
RCC_GAME_COOLDOWN = 7200


@app.get("/api/rcc")
def rcc_get(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT balance, total_clicks, last_game FROM rcc_balance WHERE user_id = %s", (user_id,))
    row = cur.fetchone()

    if not row:
        cur.execute("INSERT INTO rcc_balance (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
        conn.commit()
        balance = 0.0
        clicks = 0
        last_game = None
    else:
        balance = row["balance"] or 0.0
        clicks = row["total_clicks"] or 0
        last_game = row["last_game"]

    # Кулдаун мини-игры
    can_play = True
    remaining = 0
    if last_game:
        delta = (datetime.now() - datetime.fromisoformat(last_game)).total_seconds()
        if delta < RCC_GAME_COOLDOWN:
            can_play = False
            remaining = int(RCC_GAME_COOLDOWN - delta)

    pc_balance = require_balance(cur, user_id)
    conn.close()

    return {
        "balance": round(balance, 4),
        "total_clicks": clicks,
        "click_amount": RCC_CLICK_AMOUNT,
        "exchange_rate": RCC_EXCHANGE_RATE,
        "can_play": can_play,
        "remaining_seconds": remaining,
        "pc_balance": pc_balance,
    }


@app.post("/api/rcc/click")
def rcc_click(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    amount = float(payload.get("amount") or 0)

    if amount <= 0 or amount > 1:
        raise HTTPException(status_code=400, detail="Слишком много кликов за раз")

    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO rcc_balance (user_id) VALUES (%s)", (user_id,))
    cur.execute(
        "UPDATE rcc_balance SET balance = balance + ?, total_clicks = total_clicks + 1 WHERE user_id = %s",
        (amount, user_id)
    )
    conn.commit()
    cur.execute("SELECT balance FROM rcc_balance WHERE user_id = %s", (user_id,))
    new_balance = cur.fetchone()["balance"]
    conn.close()
    return {"ok": True, "balance": round(new_balance, 4)}


@app.post("/api/rcc/game_finish")
def rcc_game_finish(payload: dict = Body(...)):
    """Завершение мини-игры. Принимает набранное количество RCC."""
    user_id = get_telegram_user_id(payload.get("initData", ""))
    amount = float(payload.get("amount") or 0)

    conn = db()
    cur = conn.cursor()

    # Проверка кулдауна
    cur.execute("SELECT last_game FROM rcc_balance WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if row and row["last_game"]:
        delta = (datetime.now() - datetime.fromisoformat(row["last_game"])).total_seconds()
        if delta < RCC_GAME_COOLDOWN:
            conn.close()
            raise HTTPException(status_code=400, detail="Мини-игра ещё недоступна")

    if amount < 0:
        amount = 0
    if amount > 5:  # защита от накрутки
        amount = 5

    cur.execute("INSERT OR IGNORE INTO rcc_balance (user_id) VALUES (%s)", (user_id,))
    cur.execute(
        "UPDATE rcc_balance SET balance = balance + ?, last_game = %s WHERE user_id = %s",
        (amount, datetime.now().isoformat(), user_id)
    )
    cur.execute(
        "INSERT INTO rcc_transactions (user_id, amount, type) VALUES (%s, %s, 'game')",
        (user_id, amount)
    )
    conn.commit()
    cur.execute("SELECT balance FROM rcc_balance WHERE user_id = %s", (user_id,))
    new_balance = cur.fetchone()["balance"]
    conn.close()
    return {"ok": True, "balance": round(new_balance, 4), "earned": round(amount, 4)}


@app.post("/api/rcc/exchange")
def rcc_exchange_endpoint(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    amount = float(payload.get("amount") or 0)

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше 0")

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT balance FROM rcc_balance WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row or (row["balance"] or 0) < amount:
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно RCC")

    pc_amount = int(amount * RCC_EXCHANGE_RATE)

    cur.execute(
        "UPDATE rcc_balance SET balance = balance - ? WHERE user_id = %s",
        (amount, user_id)
    )
    cur.execute(
        "INSERT INTO rcc_transactions (user_id, amount, type) VALUES (%s, %s, 'exchange')",
        (user_id, -amount)
    )

    if not change_balance(cur, user_id, pc_amount):
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail="Ошибка начисления LDC")

    conn.commit()
    cur.execute("SELECT balance FROM rcc_balance WHERE user_id = %s", (user_id,))
    new_rcc = cur.fetchone()["balance"]
    pc_balance = require_balance(cur, user_id)
    conn.close()

    return {
        "ok": True,
        "message": f"Обменяно {amount:.2f} RCC → {pc_amount:,} LDC",
        "rcc_balance": round(new_rcc, 4),
        "pc_balance": pc_balance,
    }

# ========== API: СПИСКИ ИГРОКОВ ==========

@app.get("/api/admin/users")
def admin_users(init_data: str = Query(..., alias="initData"), filter: str = Query("all")):
    user_id = get_telegram_user_id(init_data)
    _require_admin(user_id)

    conn = db()
    cur = conn.cursor()

    if filter == "banned":
        cur.execute("""
            SELECT user_id, username, balance, is_banned
            FROM users WHERE is_banned = 1 ORDER BY balance DESC LIMIT 200
        """)
    elif filter == "active":
        cur.execute("""
            SELECT user_id, username, balance, is_banned
            FROM users WHERE is_banned = 0 ORDER BY balance DESC LIMIT 200
        """)
    elif filter == "blocked_bot":
        cur.execute("""
            SELECT user_id, username, balance, is_banned
            FROM users WHERE is_banned = 2 ORDER BY balance DESC LIMIT 200
        """)
    else:
        cur.execute("""
            SELECT user_id, username, balance, is_banned
            FROM users ORDER BY balance DESC LIMIT 200
        """)

    users = [
        {"user_id": r["user_id"], "username": r["username"],
         "balance": r["balance"], "status": r["is_banned"]}
        for r in cur.fetchall()
    ]

    # Счётчики для каждой категории
    cur.execute("SELECT COUNT(*) c FROM users WHERE is_banned = 0")
    active_count = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM users WHERE is_banned = 1")
    banned_count = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM users WHERE is_banned = 2")
    blocked_count = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM users")
    total_count = cur.fetchone()["c"]

    conn.close()
    return {
        "users": users,
        "filter": filter,
        "counts": {
            "all": total_count,
            "active": active_count,
            "banned": banned_count,
            "blocked_bot": blocked_count,
        }
    }


@app.post("/api/admin/users/clear_banned")
def admin_clear_banned(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE is_banned = 1")
    ids = [r["user_id"] for r in cur.fetchall()]

    deleted = 0
    errors = []
    for uid in ids:
        try:
            _delete_user_full_main(cur, uid)
            deleted += 1
        except Exception as e:
            errors.append(f"{uid}: {e}")
            print(f"[clear_banned] Ошибка удаления {uid}: {e}")

    conn.commit()
    conn.close()

    msg = f"Удалено {deleted} из {len(ids)}"
    if errors:
        msg += f". Ошибки: {len(errors)}"
        print(f"[clear_banned] Ошибки: {errors}")

    return {"ok": True, "message": msg, "deleted": deleted, "errors": errors}


@app.post("/api/admin/users/delete")
def admin_delete_user(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    target_id = int(payload.get("user_id"))

    conn = db()
    cur = conn.cursor()
    _delete_user_full_main(cur, target_id)
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Пользователь удалён"}

def _delete_user_full_main(cur, user_id: int):
    """Удаляет пользователя из всех таблиц. Каждая таблица — в своём try."""
    tables_with_user_id = [
        "purchases", "warns", "game_stats", "new_members",
        "pets", "professions", "profession_progress",
        "daily_tasks", "promo_activations",
        "user_cosmetics", "user_badges", "user_cosmetics_owned",
        "user_settings", "islands",
        "clan_members",
        "music_personal", "music_shared", "music_slots",
        "rcc_balance", "rcc_transactions",
    ]
    for t in tables_with_user_id:
        try:
            cur.execute(f"DELETE FROM {t} WHERE user_id = %s", (user_id,))
        except Exception as e:
            print(f"[delete_user] {t} (user_id): {e}")
            try:
                cur.connection.rollback()
            except Exception:
                logger.exception("Не удалось выполнить необязательную операцию")

    # Друзья (где он фигурирует как friend_id или to_user)
    for q in [
        ("DELETE FROM friends WHERE friend_id = %s", (user_id,)),
        ("DELETE FROM friends WHERE user_id = %s", (user_id,)),
        ("DELETE FROM friend_requests WHERE to_user = %s", (user_id,)),
        ("DELETE FROM friend_requests WHERE from_user = %s", (user_id,)),
    ]:
        try:
            cur.execute(q[0], q[1])
        except Exception as e:
            print(f"[delete_user] friends: {e}")

    # Кланы, где он лидер — роспуск
    try:
        cur.execute("SELECT id FROM clans WHERE leader_id = %s", (user_id,))
        clan_ids = [r["id"] for r in cur.fetchall()]
        for cid in clan_ids:
            for q in [
                ("DELETE FROM clan_members WHERE clan_id = %s", (cid,)),
                ("DELETE FROM clan_requests WHERE clan_id = %s", (cid,)),
                ("DELETE FROM clans WHERE id = %s", (cid,)),
            ]:
                try:
                    cur.execute(q[0], q[1])
                except Exception as e:
                    print(f"[delete_user] clan {cid}: {e}")
    except Exception as e:
        print(f"[delete_user] clans lookup: {e}")

    # Заявки в кланы, где он заявитель
    try:
        cur.execute("DELETE FROM clan_requests WHERE user_id = %s", (user_id,))
    except Exception as e:
        print(f"[delete_user] clan_requests: {e}")

    # Наконец, сам пользователь
    try:
        cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
    except Exception as e:
        print(f"[delete_user] users: {e}")


# Автоудаление при блокировке бота

def _mark_user_blocked(user_id: int):
    """Помечает пользователя как заблокировавшего бота (status = 2)."""
    try:
        conn = db()
        cur = conn.cursor()
        # Не перезаписываем административный бан статусом блокировки бота.
        cur.execute("UPDATE users SET is_banned = 2 WHERE user_id = %s AND is_banned = 0", (user_id,))
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")


def _safe_bot_send(chat_id: int, text: str, **kwargs) -> bool:
    """Безопасная отправка ЛС. Если юзер заблокировал — помечает его."""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        payload.update(kwargs)
        with httpx.Client(timeout=5) as client:
            r = client.post(url, json=payload)
            if r.status_code == 403:  # Forbidden — бот заблокирован
                _mark_user_blocked(chat_id)
                return False
            return r.status_code == 200
    except Exception:
        return False

def _notify_admins_purchase(buyer_id: int, item_type: str, price: int):
    """Шлёт ЛС всем админам/владельцу/тех.админам о покупке из Mini App."""
    try:
        item_name = bot_db.get_item_name(item_type)
    except Exception:
        item_name = item_type

    try:
        buyer = bot_db.get_user(buyer_id)
        buyer_name = f"@{buyer['username']}" if buyer and buyer.get("username") else f"ID{buyer_id}"
    except Exception:
        buyer_name = f"ID{buyer_id}"

    text = (
        f"🛍️ Покупка в Mini App\n\n"
        f"👤 Покупатель: {buyer_name} (ID {buyer_id})\n"
        f"📦 Товар: {item_name}\n"
        f"💰 Цена: {price:,} 🪙"
    )
    if item_type in CONFIRMATION_REQUIRED:
        text += (
            f"\n\n⚠️ Требуется подтверждение владельца."
            f"\nПодтвердить: /approve_{item_type}_{buyer_id}"
            f"\nОтклонить: /reject_{item_type}_{buyer_id}"
        )

    targets = set(ADMIN_IDS) | set(config.TECH_ADMIN_IDS) | {config.OWNER_ID}
    for admin_id in targets:
        if admin_id == buyer_id:
            continue
        _safe_bot_send(admin_id, text)

# ========== API: ПОДАРКИ ==========


@app.get("/api/gifts")
def get_gifts(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()

    now = datetime.now().isoformat()

    # Входящие
    cur.execute("""
        SELECT g.id, g.from_user, u.username, g.gift_type, g.gift_data, g.created_at, g.expires_at
        FROM gifts g
        LEFT JOIN users u ON u.user_id = g.from_user
        WHERE g.to_user = %s AND g.is_opened = 0 AND g.expires_at > %s
        ORDER BY g.created_at DESC
    """, (user_id, now))
    incoming = [
        {"id": r["id"], "from_user": r["from_user"], "from_username": r["username"],
         "gift_type": r["gift_type"], "gift_data": r["gift_data"],
         "created_at": r["created_at"], "expires_at": r["expires_at"]}
        for r in cur.fetchall()
    ]

    # Исходящие (открытые и нет)
    cur.execute("""
        SELECT g.id, g.to_user, u.username, g.gift_type, g.gift_data,
               g.is_opened, g.created_at
        FROM gifts g
        LEFT JOIN users u ON u.user_id = g.to_user
        WHERE g.from_user = %s
        ORDER BY g.created_at DESC LIMIT 50
    """, (user_id,))
    outgoing = [
        {"id": r["id"], "to_user": r["to_user"], "to_username": r["username"],
         "gift_type": r["gift_type"], "gift_data": r["gift_data"],
         "is_opened": bool(r["is_opened"]), "created_at": r["created_at"]}
        for r in cur.fetchall()
    ]

    balance = require_balance(cur, user_id)
    rcc_balance = 0.0
    try:
        cur.execute("SELECT balance FROM rcc_balance WHERE user_id = %s", (user_id,))
        r = cur.fetchone()
        rcc_balance = r["balance"] if r else 0.0
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")

    # Питомцы
    cur.execute("SELECT id, pet_name, rarity FROM pets WHERE user_id = %s", (user_id,))
    pets = [
        {"id": r["id"], "name": r["pet_name"], "rarity": r["rarity"]}
        for r in cur.fetchall()
    ]

    # Косметика (то, чем владеет)
    cur.execute("""
        SELECT item_type, item_key FROM user_cosmetics_owned WHERE user_id = %s
    """, (user_id,))
    cosmetics = [
        {"item_type": r["item_type"], "item_key": r["item_key"]}
        for r in cur.fetchall()
    ]

    conn.close()
    return {
        "incoming": incoming,
        "outgoing": outgoing,
        "balance": balance,
        "rcc_balance": round(rcc_balance, 4),
        "pets": pets,
        "cosmetics": cosmetics,
    }


@app.post("/api/gifts/send")
def send_gift(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    target_username = (payload.get("username") or "").lstrip("@").lower()
    gift_type = payload.get("gift_type")
    gift_data_raw = payload.get("gift_data")

    if not target_username:
        raise HTTPException(status_code=400, detail="Укажи username")

    if gift_type not in ("money", "pet", "cosmetic", "rcc"):
        raise HTTPException(status_code=400, detail="Неверный тип подарка")

    conn = db()
    cur = conn.cursor()

    # Ищем получателя
    cur.execute("SELECT user_id, username FROM users WHERE LOWER(username) = %s", (target_username,))
    target = cur.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    target_id = target["user_id"]
    if target_id == user_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Нельзя подарить себе")

    # Проверка и списание
    if gift_type == "money":
        amount = int(gift_data_raw)
        if amount <= 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Сумма должна быть больше 0")
        if not change_balance(cur, user_id, -amount):
            conn.close()
            raise HTTPException(status_code=400, detail="Недостаточно монет")
        gift_data = json.dumps({"amount": amount})

    elif gift_type == "rcc":
        amount = float(gift_data_raw)
        if amount <= 0:
            conn.close()
            raise HTTPException(status_code=400, detail="Сумма должна быть больше 0")
        cur.execute("SELECT balance FROM rcc_balance WHERE user_id = %s", (user_id,))
        r = cur.fetchone()
        if not r or r["balance"] < amount:
            conn.close()
            raise HTTPException(status_code=400, detail="Недостаточно RCC")
        cur.execute("UPDATE rcc_balance SET balance = balance - %s WHERE user_id = %s", (amount, user_id))
        gift_data = json.dumps({"amount": amount})

    elif gift_type == "pet":
        pet_id = int(gift_data_raw)
        cur.execute("SELECT pet_name, rarity, income FROM pets WHERE id = %s AND user_id = %s", (pet_id, user_id))
        r = cur.fetchone()
        if not r:
            conn.close()
            raise HTTPException(status_code=404, detail="Питомец не найден")
        cur.execute("DELETE FROM pets WHERE id = %s AND user_id = %s", (pet_id, user_id))
        gift_data = json.dumps({"pet_name": r["pet_name"], "rarity": r["rarity"], "income": r["income"]})

    elif gift_type == "cosmetic":
        # gift_data_raw = "item_type:item_key"
        try:
            item_type, item_key = gift_data_raw.split(":", 1)
        except Exception:
            conn.close()
            raise HTTPException(status_code=400, detail="Неверный формат косметики")

        cur.execute("""
            SELECT 1 FROM user_cosmetics_owned
            WHERE user_id = %s AND item_type = %s AND item_key = %s
        """, (user_id, item_type, item_key))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="У тебя нет этой косметики")
        cur.execute("""
            DELETE FROM user_cosmetics_owned
            WHERE user_id = %s AND item_type = %s AND item_key = %s
        """, (user_id, item_type, item_key))
        gift_data = json.dumps({"item_type": item_type, "item_key": item_key})

    # Создаём подарок
    cur.execute("""
        INSERT INTO gifts (from_user, to_user, gift_type, gift_data, expires_at)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, target_id, gift_type, gift_data,
          (datetime.now() + timedelta(days=5)).isoformat()))
    conn.commit()
    conn.close()

    # Уведомление получателю в бот
    sender_name = _get_username(user_id)
    gift_label = {
        "money": "монеты",
        "rcc": "RCC",
        "pet": "питомца",
        "cosmetic": "косметику",
    }.get(gift_type, "подарок")

    _safe_bot_send(
        target_id,
        f"🎁 Вам пришёл подарок ({gift_label}) от @{sender_name}!\n\n"
        f"Открой Mini App → 🎁 Подарки, чтобы открыть его.\n"
        f"⏳ Сгорает через 5 дней."
    )

    return {"ok": True, "message": f"Подарок отправлен @{target_username}"}


@app.post("/api/gifts/open")
def open_gift(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    gift_id = int(payload.get("gift_id"))

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT from_user, to_user, gift_type, gift_data, is_opened, expires_at
        FROM gifts WHERE id = %s
    """, (gift_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Подарок не найден")
    if row["to_user"] != user_id:
        conn.close()
        raise HTTPException(status_code=403, detail="Не твой подарок")
    if row["is_opened"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Уже открыт")
    if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) < datetime.now():
        conn.close()
        raise HTTPException(status_code=400, detail="Подарок сгорел")

    gift_type = row["gift_type"]
    gift_data = json.loads(row["gift_data"])

    # Выдаём награду
    result_text = ""

    if gift_type == "money":
        amount = gift_data["amount"]
        change_balance(cur, user_id, amount)
        result_text = f"+{amount:,} 🪙"

    elif gift_type == "rcc":
        amount = gift_data["amount"]
        cur.execute("INSERT INTO rcc_balance (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
        cur.execute("UPDATE rcc_balance SET balance = balance + %s WHERE user_id = %s", (amount, user_id))
        result_text = f"+{amount:.2f} RCC"

    elif gift_type == "pet":
        cur.execute("""
            INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, gift_data["pet_name"], gift_data["pet_name"].split()[0],
              gift_data["rarity"], gift_data["income"]))
        result_text = f"Питомец {gift_data['pet_name']} ({gift_data['rarity']})"

    elif gift_type == "cosmetic":
        item_type = gift_data["item_type"]
        item_key = gift_data["item_key"]
        cur.execute("""
            INSERT INTO user_cosmetics_owned (user_id, item_type, item_key)
            VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
        """, (user_id, item_type, item_key))
        result_text = f"Косметика: {item_type} — {item_key}"

    cur.execute("UPDATE gifts SET is_opened = 1 WHERE id = %s", (gift_id,))
    conn.commit()
    conn.close()

    return {"ok": True, "message": f"Открыто: {result_text}", "gift_type": gift_type}

# ========== API: ТЕХ.АДМИН ==========

def _require_tech_admin(user_id: int):
    if user_id not in config.TECH_ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Нет доступа")


@app.get("/api/tech/me")
def tech_me(init_data: str = Query(..., alias="initData")):
    """Возвращает статус: тех.админ ли текущий юзер, включён ли тест-режим."""
    user_id = get_telegram_user_id(init_data)
    is_tech = user_id in config.TECH_ADMIN_IDS
    test_mode = False
    if is_tech:
        try:
            test_mode = bot_db.is_test_mode(user_id)
        except Exception:
            logger.exception("Не удалось выполнить необязательную операцию")
    return {"is_tech_admin": is_tech, "test_mode": test_mode}


@app.post("/api/tech/test_mode")
def tech_toggle_test_mode(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_tech_admin(user_id)
    enabled = bool(payload.get("enabled"))
    bot_db.set_test_mode(user_id, enabled, added_by=user_id)
    return {"ok": True, "test_mode": enabled}


@app.get("/api/tech/logs")
def tech_logs(init_data: str = Query(..., alias="initData"),
              filter_user: int = Query(None),
              filter_type: str = Query(None),
              limit: int = Query(50)):
    """Логи действий. Только для тех.админов."""
    user_id = get_telegram_user_id(init_data)
    _require_tech_admin(user_id)

    try:
        entries = bot_db.get_action_log(
            filter_user=filter_user,
            filter_type=filter_type,
            limit=min(limit, 200),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения логов: {e}")

    return {"logs": entries, "types": bot_db.LOG_TYPES}


@app.get("/api/tech/user_actions")
def tech_user_actions(init_data: str = Query(..., alias="initData"),
                      user_id: int = Query(...)):
    user_id_requester = get_telegram_user_id(init_data)
    _require_tech_admin(user_id_requester)
    try:
        entries = bot_db.get_user_actions(user_id, limit=100)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка: {e}")
    return {"logs": entries}


@app.post("/api/tech/wipe_purchase")
def tech_wipe_purchase(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_tech_admin(user_id)
    purchase_id = int(payload.get("purchase_id"))
    refund = bool(payload.get("refund"))
    ok = bot_db.admin_wipe_purchase(purchase_id, refund=refund)
    return {"ok": ok}


@app.post("/api/tech/wipe_user_purchases")
def tech_wipe_user_purchases(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_tech_admin(user_id)
    target = int(payload.get("target_id"))
    refund = bool(payload.get("refund"))
    count = bot_db.admin_wipe_user_purchases(target, refund=refund)
    return {"ok": True, "count": count}


@app.post("/api/tech/wipe_island")
def tech_wipe_island(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_tech_admin(user_id)
    target = int(payload.get("target_id"))
    item = payload.get("item")  # 'island' | 'house' | 'pier' | 'ship' | None
    ok = bot_db.admin_wipe_island(target, item=item)
    return {"ok": ok}


@app.post("/api/tech/wipe_island_income")
def tech_wipe_island_income(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_tech_admin(user_id)
    target = int(payload.get("target_id"))
    ok = bot_db.admin_wipe_last_island_income(target)
    return {"ok": ok}


@app.post("/api/tech/wipe_clan")
def tech_wipe_clan(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_tech_admin(user_id)
    clan_id = int(payload.get("clan_id"))
    to_leader = bool(payload.get("to_leader", True))
    ok = bot_db.admin_wipe_clan(clan_id, give_bank_to_leader=to_leader)
    return {"ok": ok}


@app.post("/api/tech/take_clan_bank")
def tech_take_clan_bank(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_tech_admin(user_id)
    clan_id = int(payload.get("clan_id"))
    ok = bot_db.admin_take_clan_bank(clan_id)
    return {"ok": ok}


@app.get("/api/tech/find_user")
def tech_find_user(init_data: str = Query(..., alias="initData"),
                   query: str = Query(...)):
    """Поиск игрока по username или ID. Возвращает подробную инфу."""
    user_id = get_telegram_user_id(init_data)
    _require_tech_admin(user_id)

    q = query.strip().lstrip("@")
    conn = db()
    cur = conn.cursor()
    if q.isdigit():
        cur.execute("SELECT user_id, username, balance FROM users WHERE user_id = %s", (int(q),))
    else:
        cur.execute("SELECT user_id, username, balance FROM users WHERE LOWER(username) = LOWER(%s)", (q,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Игрок не найден")

    target_id = row["user_id"]

    # Соберём инфу
    try:
        purchases = bot_db.get_purchases_full(target_id)
    except Exception:
        purchases = []
    try:
        pets = bot_db.get_user_pets(target_id)
    except Exception:
        pets = []
    try:
        clan = bot_db.clan_get_user_clan(target_id)
    except Exception:
        clan = None
    try:
        island = bot_db.island_get(target_id)
    except Exception:
        island = {"has_island": False}

    return {
        "user_id": target_id,
        "username": row["username"],
        "balance": row["balance"],
        "purchases": [{"id": p[0], "item_type": p[1], "is_used": bool(p[2])} for p in purchases],
        "pets_count": len(pets),
        "clan": {"id": clan["id"], "name": clan["name"]} if clan else None,
        "island": island,
    }

@app.get("/")
def health():
    return {"status": "ok"}

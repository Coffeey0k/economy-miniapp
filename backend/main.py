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
import math
import os
import random
import re
import time
import httpx
import config
import db_compat as sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl
from db_compat import init_db
import database as bot_db   # <— для log_action / get_price / is_test_mode

logger = logging.getLogger(__name__)
init_db()
bot_db.DB_NAME = os.environ.get("DB_PATH", bot_db.DB_NAME)
bot_db.init_db()  # Ensure boost/event tables exist even when the API starts before the bot.
import world_events as world_events

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ========== НАСТРОЙКИ ==========

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_БОТА")
# Тот же файл базы, что использует бот (см. DB_NAME в database.py)
DB_PATH = os.environ.get("DB_PATH", "paradise.db")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]

PROFESSIONS = {
    "artist": "🎨 Художник",
    "photographer": "📸 Фотограф",
    "gardener": "🌿 Садовник",
    "cook": "🍳 Повар",
    "vet": "🩺 Ветеринар",
    "programmer": "💻 Программист",
    "farmer": "🌾 Фермер",
    "fisher": "🎣 Рыбак",
    "miner": "⛏️ Шахтёр",
    "blacksmith": "⚒️ Кузнец",
    "engineer": "⚙️ Инженер",
    "explorer": "🧭 Исследователь",
    "merchant": "🛒 Торговец",
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
    "photographer": {"name": "📸 Фотограф", "salary": 1000, "cooldown_hours": 3},
    "gardener": {"name": "🌿 Садовник", "salary": 1000, "cooldown_hours": 3},
    "cook": {"name": "🍳 Повар", "salary": 1000, "cooldown_hours": 3},
    "vet": {"name": "🩺 Ветеринар", "salary": 1200, "cooldown_hours": 4},
    "programmer": {"name": "💻 Программист", "salary": 1400, "cooldown_hours": 5},
    "farmer": {"name": "🌾 Фермер", "salary": 900, "cooldown_hours": 3},
    "fisher": {"name": "🎣 Рыбак", "salary": 950, "cooldown_hours": 3},
    "miner": {"name": "⛏️ Шахтёр", "salary": 1100, "cooldown_hours": 4},
    "blacksmith": {"name": "⚒️ Кузнец", "salary": 1200, "cooldown_hours": 4},
    "engineer": {"name": "⚙️ Инженер", "salary": 1350, "cooldown_hours": 5},
    "explorer": {"name": "🧭 Исследователь", "salary": 1250, "cooldown_hours": 5},
    "merchant": {"name": "🛒 Торговец", "salary": 1150, "cooldown_hours": 4},
    "ruler": {"name": "👑 Правитель Lavender Dream", "salary": 10000, "cooldown_hours": 12},
}
# Трудоустройство и смена профессии бесплатны.
HIRE_COST = 0
FIRE_EXTRA_COST = 0

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
    "activist": "💬 Активист",
    "resident": "🌴 Житель Lavender Dream",
    "friendly": "🤍 Дружелюбный",
    "photographer": "📸 Фотограф",
    "night_owl": "🌙 Ночной житель",
    "early_bird": "🌅 Ранняя пташка",
    "storyteller": "📖 Рассказчик",
    "helper": "🤝 Помощник",
    "social_butterfly": "🦋 Душа компании",
    "emoji_master": "✨ Эмодзи-мастер",
    "questioner": "❓ Любопытный",
    "game_player": "🎮 Игрок",
    "voice_star": "🎙️ Голос чата",
    "link_sharer": "🔗 Делится полезным",
    "weekend_vibes": "🎉 Выходной вайб",
    "conversation_king": "🗣️ Душа чата",
    "reply_master": "💌 Мастер ответов",
    "photo_album": "📸 Фотоальбом",
    "night_shift": "🌃 Ночная смена",
    "sunrise": "🌄 Встречая рассвет",
    "long_story": "📚 Большой рассказ",
    "emoji_festival": "🎊 Фестиваль эмодзи",
    "question_master": "🧠 Почемучка",
    "gamer_marathon": "🎮 Игровой марафон",
    "voice_lover": "🎙️ Голосовой марафон",
    "link_guide": "🧭 Навигатор",
    "weekend_grinder": "🔥 Выходной марафон",
    "social_hours_pro": "🦋 Всегда на связи",
}

DAILY_TASK_DESCS = {
    "activist": "Отправь 30 сообщений в чат",
    "resident": "Отправь 50 сообщений в чат",
    "friendly": "Ответь (реплаем) на 5 разных сообщений",
    "photographer": "Отправь фото в чат",
    "night_owl": "Напиши сообщение после 20:00",
    "early_bird": "Напиши сообщение до 09:00",
    "storyteller": "Отправь 20 сообщений длиной от 50 символов",
    "helper": "Ответь на 10 сообщений других участников",
    "social_butterfly": "Напиши в чат в течение 3 разных часов",
    "emoji_master": "Отправь 15 сообщений с эмодзи",
    "questioner": "Отправь 10 сообщений с вопросительным знаком",
    "game_player": "Сыграй 3 игры в мини-приложении",
    "voice_star": "Отправь 3 голосовых сообщения",
    "link_sharer": "Отправь 3 сообщения со ссылками",
    "weekend_vibes": "Напиши 25 сообщений в выходной день",
    "conversation_king": "Отправь 100 сообщений в чат",
    "reply_master": "Ответь на 15 сообщений участников",
    "photo_album": "Отправь 3 фотографии в чат",
    "night_shift": "Напиши 5 сообщений после 20:00",
    "sunrise": "Напиши 3 сообщения до 09:00",
    "long_story": "Отправь 15 длинных сообщений",
    "emoji_festival": "Отправь 25 сообщений с эмодзи",
    "question_master": "Отправь 20 сообщений с вопросом",
    "gamer_marathon": "Сыграй 10 игр",
    "voice_lover": "Отправь 5 голосовых сообщений",
    "link_guide": "Отправь 5 сообщений со ссылками",
    "weekend_grinder": "Отправь 40 сообщений в выходной",
    "social_hours_pro": "Будь активен в чате в течение 5 разных часов",
}

app = FastAPI()


@app.middleware("http")
async def audit_rejected_api_requests(request: Request, call_next):
    """Record rejected requests and return CORS-compatible 500 responses."""
    client_host = request.client.host if request.client else "unknown"
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "API_UNHANDLED_ERROR method=%s path=%s client=%s",
            request.method, request.url.path, client_host,
        )
        try:
            bot_db.log_security_event(
                "api_exception", "Unhandled server exception",
                ip_address=client_host, method=request.method,
                path=request.url.path, status_code=500,
            )
        except Exception:
            logger.exception("Не удалось сохранить API exception audit")
        # Returning a response instead of re-raising keeps the response inside
        # the outer CORS middleware, so browsers receive the actual 500 status.
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
    if response.status_code < 400 and request.method.upper() == "POST":
        path = request.url.path
        activity = None
        if path == "/api/professions/work":
            activity = ("work", 1)
        elif path.startswith("/api/island/visit"):
            activity = ("visit", 1)
        elif path == "/api/island/profession-action":
            activity = ("craft", 1)
        elif path in {"/api/island/expeditions/start", "/api/island/expeditions/claim"}:
            activity = ("pet", 1)
        if activity:
            try:
                uid = get_telegram_user_id(request.query_params.get("initData", "")) if request.query_params.get("initData") else None
                if uid is None and request.method.upper() == "POST":
                    # Most Mini App POST routes carry initData in their JSON body.
                    body = await request.json()
                    uid = get_telegram_user_id(body.get("initData", "")) if isinstance(body, dict) else None
                if uid:
                    world_events.record_season_activity(uid, activity[0], activity[1])
            except Exception:
                logger.exception("Не удалось обновить сезонный прогресс для %s", path)
    if response.status_code >= 400:
        logger.warning(
            "API_REJECTED method=%s path=%s status=%s client=%s",
            request.method, request.url.path, response.status_code, client_host,
        )
        try:
            bot_db.log_security_event(
                "api_rejected", f"HTTP {response.status_code}",
                ip_address=client_host, method=request.method,
                path=request.url.path, status_code=response.status_code,
            )
        except Exception:
            logger.exception("Не удалось сохранить API rejection audit")
    return response


# CORS must wrap the audit middleware so even server-error responses include CORS headers.
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

    # --- значки и активный титул ---
    cur.execute("SELECT badge FROM user_badges WHERE user_id = %s", (user_id,))
    badges = [r["badge"] for r in cur.fetchall()]
    cur.execute("SELECT title_name FROM user_titles WHERE user_id = %s AND is_active = TRUE LIMIT 1", (user_id,))
    title_row = cur.fetchone()
    active_title = title_row["title_name"] if title_row else None

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
        "active_title": active_title,
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

    # Keep the Mini App on the exact same atomic purchase path as Telegram.
    # Close this read connection before opening the transaction used by the helper.
    if item_type == "wheel_tickets_weekly":
        conn.close()
        try:
            ok, result_text = bot_db.buy_weekly_wheel_tickets(user_id)
        except Exception as exc:
            logger.exception("Weekly wheel-ticket purchase failed (Mini App), user_id=%s", user_id)
            raise HTTPException(
                status_code=500,
                detail="Не удалось оформить покупку билетов. Попробуй ещё раз позже.",
            ) from exc
        if not ok:
            raise HTTPException(status_code=400, detail=result_text)
        try:
            bot_db.log_action(
                user_id, "purchase", "Куплено: wheel_tickets_weekly", amount=-int(price)
            )
        except Exception:
            logger.exception("Не удалось записать лог покупки билетов колеса")
        return {
            "ok": True,
            "message": result_text,
            "tickets": bot_db.get_wheel_tickets(user_id),
            "balance": bot_db.get_balance(user_id),
        }

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


@app.get("/api/inventory")
def get_inventory_endpoint(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    return {"items": bot_db.get_inventory(user_id)}


@app.post("/api/inventory/buy-pet-food")
def buy_pet_food_endpoint(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    item_key = str(payload.get("item_key") or "")
    try:
        quantity = int(payload.get("quantity") or 1)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректное количество")
    prices = {
        "cat_food": 250, "dog_food": 250, "rabbit_food": 250,
        "bird_food": 250, "fish_food": 250, "reptile_food": 250,
        "small_pet_food": 250, "universal_pet_food": 500,
    }
    if item_key not in prices:
        raise HTTPException(status_code=400, detail="Неизвестный корм")
    if not 1 <= quantity <= 100:
        raise HTTPException(status_code=400, detail="Можно купить от 1 до 100 порций за раз")
    total = prices[item_key] * quantity
    conn = db()
    cur = conn.cursor()
    if not change_balance(cur, user_id, -total):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно LDC")
    cur.execute(
        "INSERT INTO inventory_items (user_id, item_key, quantity) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, item_key) DO UPDATE SET quantity = quantity + excluded.quantity",
        (user_id, item_key, quantity),
    )
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {
        "ok": True, "message": f"Куплено: {quantity} порц. корма за {total:,} LDC.",
        "items": bot_db.get_inventory(user_id), "balance": balance,
    }


@app.post("/api/inventory/use-pet-food")
def use_pet_food_endpoint(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    pet_id = int(payload.get("pet_id") or 0)
    ok, message = bot_db.use_pet_food(user_id, pet_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "items": bot_db.get_inventory(user_id)}


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
    return {"current": current, "all": all_professions, "hire_cost": 0}


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
    # Ensure Mini App users receive today's tasks even if they haven't sent a bot message.
    bot_db.assign_daily_tasks(user_id)
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

WHEEL_TICKET_COST = 10
WHEEL_COOLDOWN = 10800  # 3 часа


@app.get("/api/wheel")
def get_wheel(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, label, chance FROM wheel_sectors")
    sectors = [dict(r) for r in cur.fetchall()]
    tickets = bot_db.get_wheel_tickets(user_id)

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

    return {"sectors": sectors, "ticket_cost": WHEEL_TICKET_COST, "tickets": tickets, "can_spin": can_spin, "remaining_seconds": remaining}


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

    cur.execute("INSERT INTO wheel_tickets (user_id, tickets) VALUES (?, 0) ON CONFLICT(user_id) DO NOTHING", (user_id,))
    cur.execute("SELECT tickets FROM wheel_tickets WHERE user_id = ? FOR UPDATE", (user_id,))
    ticket_row = cur.fetchone()
    if not ticket_row or int(ticket_row["tickets"] or 0) < WHEEL_TICKET_COST:
        conn.close()
        raise HTTPException(status_code=400, detail="Нужно 10 билетов колеса")
    cur.execute("UPDATE wheel_tickets SET tickets = tickets - ? WHERE user_id = ?", (WHEEL_TICKET_COST, user_id))

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
            amount=-WHEEL_TICKET_COST,
        )
    except Exception:
        # Успешный спин не должен завершаться ошибкой из-за логирования.
        pass
    return {"ok": True, "result": result_text, "tickets": bot_db.get_wheel_tickets(user_id)}


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
        _log_security_attempt(user_id, "transfer_invalid_amount_format", f"amount={payload.get('amount')!r}")
        raise HTTPException(status_code=400, detail="Сумма должна быть целым числом")
    if amount <= 0:
        _log_security_attempt(user_id, "transfer_invalid_amount", f"amount={amount}")
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля")
    if amount > 200_000:
        _log_security_attempt(user_id, "transfer_over_limit", f"amount={amount}; limit=200000")
        raise HTTPException(status_code=400, detail="Максимум за один перевод — 200 000 LDC.")
    if not target_username:
        _log_security_attempt(user_id, "transfer_missing_recipient", "username is empty")
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
        _log_security_attempt(user_id, "transfer_unknown_recipient", f"username={target_username[:64]}")
        raise HTTPException(status_code=404, detail="Пользователь с таким username не найден")

    target_id = int(target["user_id"])
    conn.close()
    # Shared atomic quota: at most five successful transfers per UTC day across
    # Telegram bot and Mini App. Quota increments only if the money moves.
    ok, reason, count = bot_db.transfer_coins_limited(user_id, target_id, amount, daily_limit=5)
    if not ok:
        if reason == "limit":
            _log_security_attempt(user_id, "transfer_daily_limit", f"Попытка перевода сверх лимита; target_id={target_id}; amount={amount}; count={count}")
            raise HTTPException(status_code=429, detail="Лимит переводов исчерпан: максимум 5 успешных переводов в сутки (UTC).")
        if reason == "balance":
            _log_security_attempt(user_id, "transfer_insufficient_balance", f"target_id={target_id}; amount={amount}")
            raise HTTPException(status_code=400, detail="Недостаточно монет")
        if reason == "recipient":
            raise HTTPException(status_code=404, detail="Пользователь-получатель не найден")
        _log_security_attempt(user_id, "transfer_rejected", f"reason={reason}; target_id={target_id}; amount={amount}")
        raise HTTPException(status_code=400, detail="Перевод не выполнен")
    try:
        bot_db.log_action(
            user_id, "transfer", f"Перевод в Mini App пользователю @{target_username}",
            amount=amount, target_user_id=target_id
        )
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")
    return {"ok": True, "message": f"Переведено {amount} 🪙 пользователю @{target_username}. Переводов сегодня: {count}/5."}


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
    if bet < 500:
        _log_security_attempt(user_id, "game_invalid_bet", f"game={game}; bet={bet}")
        raise HTTPException(status_code=400, detail="Минимальная ставка — 500 LDC")
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
    if not test_mode and require_balance(cur, user_id) < bet:
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    allowed, remaining = bot_db.register_game_play(user_id, game)
    if not allowed:
        conn.close()
        raise HTTPException(status_code=429, detail=f"Эта игра на перерыве ещё {remaining // 3600} ч {(remaining % 3600) // 60} мин")
    if not test_mode and not change_balance(cur, user_id, -bet):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    win = 0
    text = ""

    if game == "slot":
        result = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        text = f"{result[0]} {result[1]} {result[2]}"
        if result[0] == result[1] == result[2]:
            # Three matching symbols: return the stake plus an equal amount (x2 payout).
            win = bet * 2
        elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
            # Exactly two matching symbols: return only half of the original stake.
            win = bet // 2
    elif game == "dice":
        roll = random.randint(1, 6)
        text = f"🎲 Выпало: {roll}"
        if str(roll) == str(choice):
            win = bet * 5
    elif game == "coin":
        result = random.choice(["Орел", "Решка"])
        text = f"🪙 Выпало: {result}"
        if str(choice) == result:
            win = bet * 2
    elif game == "dart":
        score = random.randint(0, 15)
        text = f"🎯 Очков: {score}"
        if score == 15: win = bet * 5
        elif score >= 12: win = bet * 2
        elif score >= 8: win = bet
    elif game == "number":
        num = random.randint(1, 10)
        text = f"🃏 Число: {num}"
        if int(choice) == num:
            win = bet * 5
        elif abs(int(choice) - num) <= 2:
            win = bet
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Неизвестная игра")

    net_result = win - bet
    net_profit = max(0, net_result)
    if test_mode:
        text += "\n🧪 Тест-режим: баланс не изменён."
    elif win > 0:
        # A positive payout is credited even when it is smaller than the stake.
        change_balance(cur, user_id, win)
        if win > bet:
            text += f"\n✅ Валовая выплата: {win:,} 🪙 • Чистая прибыль: {net_profit:,} 🪙"
        elif win == bet:
            text += f"\n↩️ Ставка возвращена: {bet:,} 🪙"
        else:
            text += f"\n↩️ Возврат 50% ставки: {win:,} 🪙 • Потеряно: {bet - win:,} 🪙"
    else:
        text += f"\n❌ Проигрыш: {bet:,} 🪙"

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
        (1 if win > bet else 0, max(0, net_result), max(0, -net_result), user_id),
    )

    conn.commit()
    conn.close()
    try:
        bot_db.log_action(
            user_id, "game",
            f"Игра Mini App: {game}; ставка={bet}; выигрыш={win}; результат={text}",
            amount=net_result,
        )
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")
    return {"ok": True, "win": net_profit, "payout": win, "text": text}



# ========== ЖУРНАЛ ПОДОЗРИТЕЛЬНЫХ ДЕЙСТВИЙ ==========
def _log_security_attempt(user_id: int, event: str, details: str = ""):
    logger.warning("SECURITY event=%s user_id=%s details=%s", event, user_id, details)
    try:
        bot_db.log_action(user_id, "security", f"{event}: {details}"[:1000])
    except Exception:
        logger.exception("Не удалось записать security action user_id=%s event=%s", user_id, event)


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
    # Keep the same strong priority (win, block, center), but randomize
    # equally good fallback cells so the bot is not a fixed replay script.
    corners = [cell for cell in (0, 2, 6, 8) if cells[cell] == "_"]
    if corners:
        return random.choice(corners)
    edges = [cell for cell in (1, 3, 5, 7) if cells[cell] == "_"]
    if edges:
        return random.choice(edges)
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
        _log_security_attempt(user_id, "ttt_invalid_bet_format", f"bet={payload.get('bet')!r}")
        raise HTTPException(status_code=400, detail="Некорректная ставка")
    if bet < 500:
        _log_security_attempt(user_id, "ttt_invalid_bet", f"bet={bet}")
        raise HTTPException(status_code=400, detail="Минимальная ставка — 500 LDC")
    conn = db()
    cur = conn.cursor()
    # Make start requests idempotent: retries return the current active game
    # instead of charging the same player for multiple parallel games.
    cur.execute(
        "SELECT id, player_x, player_o, is_vs_bot, board, turn, bet, status, winner "
        "FROM ttt_games WHERE player_x = ? AND is_vs_bot = 1 AND status = 'active' "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    existing_game = cur.fetchone()
    if existing_game:
        balance = require_balance(cur, user_id)
        conn.close()
        try:
            bot_db.log_action(user_id, "game", f"TTT duplicate start reused active game_id={existing_game['id']}", amount=0)
        except Exception:
            logger.exception("Не удалось записать повторный старт TTT user_id=%s", user_id)
        return _ttt_game_payload(dict(existing_game), balance)
    if require_balance(cur, user_id) < bet:
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно LDC")
    allowed, remaining = bot_db.register_game_play(user_id, "ttt")
    if not allowed:
        conn.close()
        raise HTTPException(status_code=429, detail=f"Крестики-нолики на перерыве ещё {remaining // 3600} ч {(remaining % 3600) // 60} мин")
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
        _log_security_attempt(user_id, "ttt_invalid_cell", f"game_id={game_id}; cell={cell}")
        raise HTTPException(status_code=400, detail="Клетка вне поля")

    conn = db()
    cur = conn.cursor()
    # This backend uses PostgreSQL via db_compat; lock the game row before
    # reading it so concurrent/replayed requests cannot both settle the same move.
    cur.execute(
        "SELECT id, player_x, player_o, is_vs_bot, board, turn, bet, status, winner "
        "FROM ttt_games WHERE id = ? AND player_x = ? FOR UPDATE",
        (game_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        _log_security_attempt(user_id, "ttt_game_not_owned_or_missing", f"game_id={game_id}")
        raise HTTPException(status_code=404, detail="Игра не найдена")
    game = dict(row)
    if game["status"] != "active":
        conn.close()
        _log_security_attempt(user_id, "ttt_replayed_finished_game", f"game_id={game_id}; status={game['status']}")
        raise HTTPException(status_code=409, detail="Игра уже завершена")
    if game["turn"] != user_id:
        conn.close()
        _log_security_attempt(user_id, "ttt_wrong_turn", f"game_id={game_id}; expected={game['turn']}")
        raise HTTPException(status_code=400, detail="Сейчас не твой ход")
    board = list(game["board"])
    if board[cell] != "_":
        conn.close()
        _log_security_attempt(user_id, "ttt_replayed_or_occupied_cell", f"game_id={game_id}; cell={cell}; board={game['board']}")
        raise HTTPException(status_code=409, detail="Клетка уже занята")

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
    # Compare-and-set is the final anti-duplication guard: even if requests race
    # or a client replays the same move, only one request can settle this board.
    cur.execute(
        "UPDATE ttt_games SET board = ?, status = ?, winner = ?, turn = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND player_x = ? AND status = 'active' AND board = ? AND turn = ?",
        (final_board, status, winner_id, user_id, game_id, user_id, game["board"], user_id),
    )
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        _log_security_attempt(user_id, "ttt_duplicate_or_racing_move", f"game_id={game_id}; cell={cell}")
        raise HTTPException(status_code=409, detail="Этот ход уже обработан. Обнови поле и попробуй снова.")
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
    # Keep a server-side audit trail for every accepted move, not just settlements.
    try:
        bot_db.log_action(
            user_id, "game",
            f"TTT Mini App ход: game_id={game_id}; cell={cell}; bet={game['bet']}; "
            f"board_before={game['board']}; board_after={final_board}; status={status}; payout={payout}",
            amount=0,
        )
    except Exception:
        logger.exception("Не удалось записать аудит хода TTT user_id=%s game_id=%s", user_id, game_id)
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

# ========== ПРАВИЛА WEB MINI APP ==========

@app.get("/api/rules/read-status")
def get_rules_read_status(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS web_rules_read (
            user_id BIGINT PRIMARY KEY,
            read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("SELECT 1 FROM web_rules_read WHERE user_id = ?", (user_id,))
    read = cur.fetchone() is not None
    conn.commit()
    conn.close()
    return {"read": read}


@app.post("/api/rules/read")
def mark_rules_read(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS web_rules_read (
            user_id BIGINT PRIMARY KEY,
            read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute(
        "INSERT OR IGNORE INTO web_rules_read (user_id) VALUES (?)",
        (user_id,),
    )
    conn.commit()
    cur.execute("SELECT read_at FROM web_rules_read WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return {"ok": True, "read_at": row["read_at"] if row else None}


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
MS_BET = 500
MS_REWARD = 400


@app.post("/api/minesweeper/start")
def ms_start_api(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        bet = int(payload.get("bet") or MS_BET)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректная ставка")
    if bet < 500:
        raise HTTPException(status_code=400, detail="Минимальная ставка — 500 LDC")

    conn = db()
    cur = conn.cursor()
    if require_balance(cur, user_id) < bet:
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно LDC")
    allowed, remaining = bot_db.register_game_play(user_id, "minesweeper")
    if not allowed:
        conn.close()
        raise HTTPException(status_code=429, detail=f"Сапёр на перерыве ещё {remaining // 3600} ч {(remaining % 3600) // 60} мин")
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
        "reward": bet * 2,
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
        cur.execute("""
            UPDATE minesweeper_games SET revealed = ?, status = 'lose'
            WHERE id = ? AND status = 'active' AND revealed = ?
        """, (revealed_str, game_id, row["revealed"]))
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=409, detail="Партия уже обновилась. Обнови поле.")
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
        cur.execute("""
            UPDATE minesweeper_games SET revealed = ?, status = 'win'
            WHERE id = ? AND status = 'active' AND revealed = ?
        """, (revealed_str, game_id, row["revealed"]))
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=409, detail="Партия уже обновилась. Обнови поле.")
        if not change_balance(cur, user_id, int(row["bet"]) * 2):
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail="Не удалось начислить выплату.")
        cur.execute("INSERT OR IGNORE INTO game_stats (user_id) VALUES (?)", (user_id,))
        cur.execute(
            "UPDATE game_stats SET games_played = COALESCE(games_played, 0) + 1, "
            "games_won = COALESCE(games_won, 0) + 1, "
            "total_earned = COALESCE(total_earned, 0) + ? WHERE user_id = ?",
            (int(row["bet"]), user_id),
        )
    else:
        cur.execute("""
            UPDATE minesweeper_games SET revealed = ?
            WHERE id = ? AND status = 'active' AND revealed = ?
        """, (revealed_str, game_id, row["revealed"]))
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=409, detail="Партия уже обновилась. Обнови поле.")

    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance = cur.fetchone()["balance"]
    conn.commit()
    conn.close()

    if win:
        try:
            bot_db.log_action(user_id, "game", f"Сапёр: победа; ставка={row['bet']}", amount=int(row["bet"]))
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
        "reward": int(row["bet"]) * 2 if win else 0,
    }

# ========== API: МОРСКОЙ БОЙ ==========

BS_SIZE = 10
BS_BET_MIN = 500
BS_SHIPS = [4, 3, 3, 2, 2, 2, 1, 1, 1, 1]


BS_SEA_POINTS_PER_SURVIVING_SHIP = 2
BS_PLANE_COST = 25
BS_RADAR_COST = 20
BS_DEFENSE_COST = 15
BS_RADAR_SECONDS = 8
BS_DEFENSE_MAX = 3
BS_PLANE_MAX = 2
BS_BOT_DEFENSE_COUNT = 2


def _bs_ensure_special_schema(conn):
    """Additive migration for special weapons; existing games are preserved."""
    cur = conn.cursor()
    # Keep game startup self-contained: on a fresh or partially migrated database,
    # the base table must exist before the additive column migrations run.
    cur.execute("""CREATE TABLE IF NOT EXISTS battleship_games (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        player_field TEXT,
        bot_field TEXT,
        player_shots TEXT,
        bot_shots TEXT,
        bet BIGINT,
        status TEXT DEFAULT 'active',
        turn TEXT DEFAULT 'player',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS battleship_economy (
        user_id BIGINT PRIMARY KEY, sea_points INTEGER NOT NULL DEFAULT 0)""")
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'battleship_economy' AND column_name = 'user_id'"
    )
    economy_user_type = cur.fetchone()
    if economy_user_type and str(economy_user_type[0]).lower() not in {"bigint"}:
        cur.execute("ALTER TABLE battleship_economy ALTER COLUMN user_id TYPE BIGINT")
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'battleship_games'"
    )
    existing_columns = {str(row[0]).lower() for row in cur.fetchall()}
    for column, definition in (
        ("player_defenses", "TEXT DEFAULT NULL"), ("bot_defenses", "TEXT DEFAULT NULL"),
        ("plane_available", "INTEGER NOT NULL DEFAULT 0"), ("plane_buys", "INTEGER NOT NULL DEFAULT 0"), ("defenses_bought", "INTEGER NOT NULL DEFAULT 0"), ("radar_cells", "TEXT DEFAULT NULL"),
        ("radar_until", "INTEGER NOT NULL DEFAULT 0"), ("radar_bought", "INTEGER NOT NULL DEFAULT 0"), ("sea_reward_paid", "INTEGER NOT NULL DEFAULT 0"),
        ("bot_plane_buys", "INTEGER NOT NULL DEFAULT 0"), ("bot_radar_bought", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column.lower() not in existing_columns:
            cur.execute(f"ALTER TABLE battleship_games ADD COLUMN {column} {definition}")
            existing_columns.add(column.lower())
    conn.commit()


def _bs_points(cur, user_id: int) -> int:
    cur.execute("INSERT OR IGNORE INTO battleship_economy (user_id, sea_points) VALUES (?, 0)", (user_id,))
    cur.execute("SELECT sea_points FROM battleship_economy WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    return int(row["sea_points"] if row else 0)


def _bs_change_points(cur, user_id: int, delta: int) -> int:
    _bs_points(cur, user_id)
    if delta < 0:
        cur.execute(
            "UPDATE battleship_economy SET sea_points = sea_points + ? WHERE user_id = ? AND sea_points >= ?",
            (delta, user_id, -delta),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=400, detail="Недостаточно морских очков")
    else:
        cur.execute("UPDATE battleship_economy SET sea_points = sea_points + ? WHERE user_id = ?", (delta, user_id))
    cur.execute("SELECT sea_points FROM battleship_economy WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    return int(row["sea_points"] if row else 0)


def _bs_validate_defenses(defenses, field: str) -> bool:
    if (
        not isinstance(defenses, str)
        or len(defenses) != BS_SIZE * BS_SIZE
        or any(c not in {".", "D"} for c in defenses)
        or not _bs_validate_field(field)
    ):
        return False
    positions = [i for i, cell in enumerate(defenses) if cell == "D"]
    if len(positions) > BS_DEFENSE_MAX or any(field[idx] == "S" for idx in positions):
        return False
    # Each installation occupies one distinct cell; adjacent installations are allowed.
    return True


def _bs_generate_defenses(field: str, count: int = BS_BOT_DEFENSE_COUNT) -> str:
    count = max(0, min(BS_DEFENSE_MAX, int(count)))
    out = ["."] * (BS_SIZE * BS_SIZE)
    if count == 0:
        return "".join(out)
    candidates = [i for i, cell in enumerate(field) if cell == "."]
    random.shuffle(candidates)
    selected = []
    for idx in candidates:
        r, c = divmod(idx, BS_SIZE)
        if all(max(abs(r - divmod(o, BS_SIZE)[0]), abs(c - divmod(o, BS_SIZE)[1])) > 1 for o in selected):
            selected.append(idx)
            if len(selected) >= count:
                break
    for idx in selected:
        out[idx] = "D"
    return "".join(out)


def _bs_defense_at(defenses: str, idx: int):
    """Return the battery index if idx lies inside its 2x10 protected strip."""
    row, _ = divmod(idx, BS_SIZE)
    for pos, value in enumerate(defenses or "." * 100):
        if value == "D":
            base_row, _ = divmod(pos, BS_SIZE)
            protected_rows = {base_row, base_row + 1} if base_row < BS_SIZE - 1 else {base_row - 1, base_row}
            if row in protected_rows:
                return pos
    return None


def _bs_run_bot_turn(cur, game_id: int) -> str:
    """Resolve bot turns server-side; randomized radar/aircraft use and player AA interception."""
    bot_msg = ""
    radar_note = ""
    plane_checked_this_turn = False
    cur.execute(
        "SELECT status, turn, player_field, bot_shots, player_defenses, bot_plane_buys, bot_radar_bought "
        "FROM battleship_games WHERE id = ? FOR UPDATE",
        (game_id,),
    )
    state = cur.fetchone()
    while state and state["status"] == "active" and state["turn"] == "bot":
        field = state["player_field"]
        shots = list(state["bot_shots"])
        defenses = list(state["player_defenses"] or "." * 100)
        plane_buys = int(state["bot_plane_buys"] or 0)

        # A plane is a separate, randomized action and always ends the bot's turn.
        if not plane_checked_this_turn:
            plane_checked_this_turn = True
            if plane_buys < BS_PLANE_MAX and random.random() < 0.10:
                orientation = random.choice(("horizontal", "vertical"))
                if orientation == "horizontal":
                    ar, ac = random.randrange(BS_SIZE), random.randrange(BS_SIZE - 4)
                    line = [_bs_cell_index(ar, ac + n) for n in range(5)]
                else:
                    ar, ac = random.randrange(BS_SIZE - 4), random.randrange(BS_SIZE)
                    line = [_bs_cell_index(ar + n, ac) for n in range(5)]
                intercepted = next(
                    (battery for target in line
                     if (battery := _bs_defense_at("".join(defenses), target)) is not None),
                    None,
                )
                if intercepted is not None:
                    defenses[intercepted] = "."
                    cur.execute(
                        "UPDATE battleship_games SET player_defenses = ?, bot_plane_buys = bot_plane_buys + 1, turn = 'player' "
                        "WHERE id = ? AND status = 'active' AND turn = 'bot' AND bot_plane_buys < ?",
                        ("".join(defenses), game_id, BS_PLANE_MAX),
                    )
                    if cur.rowcount == 1:
                        bot_msg = "🛡️ ПВО перехватило самолёт бота; ход возвращается тебе."
                        break
                    cur.execute(
                        "SELECT status, turn, player_field, bot_shots, player_defenses, bot_plane_buys, bot_radar_bought "
                        "FROM battleship_games WHERE id = ? FOR UPDATE", (game_id,)
                    )
                    state = cur.fetchone()
                    continue

                for target in line:
                    if shots[target] in ("1", "2", "3"):
                        continue
                    if field[target] == "S":
                        shots[target] = "2"
                for target in line:
                    if shots[target] == "2":
                        rr, cc = divmod(target, BS_SIZE)
                        if _bs_check_sunk(field, "".join(shots), rr, cc):
                            _bs_mark_sunk(field, shots, rr, cc)
                encoded = "".join(shots)
                lose = all(field[i] != "S" or encoded[i] == "3" for i in range(BS_SIZE * BS_SIZE))
                cur.execute(
                    "UPDATE battleship_games SET bot_shots = ?, bot_plane_buys = bot_plane_buys + 1, "
                    "status = ?, turn = 'player' WHERE id = ? AND status = 'active' AND turn = 'bot' "
                    "AND bot_plane_buys < ? AND bot_shots = ?",
                    (encoded, "lose" if lose else "active", game_id, BS_PLANE_MAX, state["bot_shots"]),
                )
                if cur.rowcount == 1:
                    bot_msg = "✈️ Самолёт бота атаковал пять клеток: " + ("флот уничтожен!" if lose else "ход переходит к тебе.")
                    break
                cur.execute(
                    "SELECT status, turn, player_field, bot_shots, player_defenses, bot_plane_buys, bot_radar_bought "
                    "FROM battleship_games WHERE id = ? FOR UPDATE", (game_id,)
                )
                state = cur.fetchone()
                continue

        # Radar is used at most once per game and only occasionally.
        radar_target = None
        if not int(state["bot_radar_bought"] or 0) and random.random() < 0.12:
            radar_options = []
            for center in range(BS_SIZE * BS_SIZE):
                cr, cc = divmod(center, BS_SIZE)
                zone = [
                    _bs_cell_index(rr, co)
                    for rr in range(max(0, cr - 1), min(BS_SIZE, cr + 2))
                    for co in range(max(0, cc - 1), min(BS_SIZE, cc + 2))
                ]
                targets = [idx for idx in zone if field[idx] == "S" and shots[idx] not in ("1", "2", "3")]
                if targets:
                    radar_options.append((center, targets))
            if radar_options:
                _, targets = random.choice(radar_options)
                radar_target = random.choice(targets)
                cur.execute(
                    "UPDATE battleship_games SET bot_radar_bought = 1 "
                    "WHERE id = ? AND status = 'active' AND turn = 'bot' AND bot_radar_bought = 0",
                    (game_id,),
                )
                if cur.rowcount == 1:
                    radar_note = "📡 Бот использовал радар. "

        candidates = []
        if radar_target is None:
            for i, value in enumerate(shots):
                if value == "2":
                    r, co = divmod(i, BS_SIZE)
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nr, nc = r + dr, co + dc
                        if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE and shots[_bs_cell_index(nr, nc)] == "0":
                            candidates.append((nr, nc))
            if candidates:
                br, bc = random.choice(candidates)
            else:
                remaining = [i for i, value in enumerate(shots) if value == "0"]
                if not remaining:
                    break
                br, bc = divmod(random.choice(remaining), BS_SIZE)
            idx = _bs_cell_index(br, bc)
        else:
            idx = radar_target
            br, bc = divmod(idx, BS_SIZE)

        if field[idx] == "S":
            shots[idx] = "2"
            if _bs_check_sunk(field, "".join(shots), br, bc):
                _bs_mark_sunk(field, shots, br, bc)
                result = "sunk"
            else:
                result = "hit"
        else:
            shots[idx] = "1"
            result = "miss"
        encoded = "".join(shots)
        lose = all(field[i] != "S" or encoded[i] == "3" for i in range(BS_SIZE * BS_SIZE))
        if lose:
            cur.execute(
                "UPDATE battleship_games SET bot_shots = ?, status = 'lose', turn = 'player' "
                "WHERE id = ? AND status = 'active' AND turn = 'bot' AND bot_shots = ?",
                (encoded, game_id, state["bot_shots"]),
            )
            shot_message = f"Бот попал в ({br + 1}, {bc + 1}) и уничтожил твой флот!"
        else:
            cur.execute(
                "UPDATE battleship_games SET bot_shots = ?, turn = ? "
                "WHERE id = ? AND status = 'active' AND turn = 'bot' AND bot_shots = ?",
                (encoded, "bot" if result in ("hit", "sunk") else "player", game_id, state["bot_shots"]),
            )
            shot_message = f"Бот стреляет в ({br + 1}, {bc + 1}): {result}"
        if cur.rowcount != 1:
            cur.execute(
                "SELECT status, turn, player_field, bot_shots, player_defenses, bot_plane_buys, bot_radar_bought "
                "FROM battleship_games WHERE id = ? FOR UPDATE", (game_id,)
            )
            state = cur.fetchone()
            continue
        bot_msg = radar_note + shot_message
        cur.execute(
            "SELECT status, turn, player_field, bot_shots, player_defenses, bot_plane_buys, bot_radar_bought "
            "FROM battleship_games WHERE id = ? FOR UPDATE",
            (game_id,),
        )
        state = cur.fetchone()
        if result == "miss" or (state and state["status"] != "active"):
            break
    return bot_msg

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


def _bs_count_surviving_ships(field: str, shots: str) -> int:
    """Count own ships with at least one unhit cell after the bot's shots."""
    if not isinstance(field, str) or len(field) != BS_SIZE * BS_SIZE:
        return 0
    shots = shots if isinstance(shots, str) and len(shots) == BS_SIZE * BS_SIZE else "0" * (BS_SIZE * BS_SIZE)
    visited = set()
    surviving = 0
    for idx, cell in enumerate(field):
        if cell != "S" or idx in visited:
            continue
        stack = [idx]
        component = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            row, col = divmod(current, BS_SIZE)
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = row + dr, col + dc
                ni = _bs_cell_index(nr, nc) if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE else -1
                if ni >= 0 and field[ni] == "S" and ni not in visited:
                    stack.append(ni)
        if any(shots[pos] not in ("2", "3") for pos in component):
            surviving += 1
    return surviving


def _bs_validate_field(field) -> bool:
    """Проверяет пользовательскую расстановку флота: 1×4, 2×3, 3×2, 4×1."""
    if not isinstance(field, str) or len(field) != BS_SIZE * BS_SIZE:
        return False
    if any(cell not in {".", "S"} for cell in field):
        return False

    visited = set()
    lengths = []
    for idx, cell in enumerate(field):
        if cell != "S" or idx in visited:
            continue
        stack = [idx]
        component = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            row, col = divmod(current, BS_SIZE)
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = row + dr, col + dc
                ni = nr * BS_SIZE + nc
                if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE and field[ni] == "S" and ni not in visited:
                    stack.append(ni)

        rows = {i // BS_SIZE for i in component}
        cols = {i % BS_SIZE for i in component}
        if len(rows) > 1 and len(cols) > 1:
            return False
        ordered = sorted(component)
        if len(rows) == 1:
            if ordered != list(range(ordered[0], ordered[0] + len(ordered))):
                return False
        elif len(cols) == 1:
            if ordered != [ordered[0] + n * BS_SIZE for n in range(len(ordered))]:
                return False
        lengths.append(len(component))

    if sorted(lengths) != sorted(BS_SHIPS):
        return False

    # Запрещаем соприкосновение разных кораблей по диагонали.
    for idx, cell in enumerate(field):
        if cell != "S":
            continue
        row, col = divmod(idx, BS_SIZE)
        for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            nr, nc = row + dr, col + dc
            if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE and field[nr * BS_SIZE + nc] == "S":
                return False
    return True


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

    requested_field = payload.get("player_field")
    if requested_field is not None and not _bs_validate_field(requested_field):
        raise HTTPException(status_code=400, detail="Некорректная расстановка кораблей")

    player_field = requested_field if requested_field is not None else _bs_generate_field()
    requested_defenses = payload.get("player_defenses")
    if requested_defenses is not None and not _bs_validate_defenses(requested_defenses, player_field):
        raise HTTPException(status_code=400, detail="Можно разместить до 3 ПВО: вне кораблей и не рядом друг с другом")
    player_defenses = requested_defenses if requested_defenses is not None else _bs_generate_defenses(player_field)
    bot_field = _bs_generate_field()
    # The bot does not start with air defense every game; its defensive loadout is randomized.
    bot_defenses = _bs_generate_defenses(bot_field, count=random.choice([0, 0, 1, 2]))
    conn = db()
    _bs_ensure_special_schema(conn)
    cur = conn.cursor()
    if require_balance(cur, user_id) < bet:
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно LDC")
    allowed, remaining = bot_db.register_game_play(user_id, "battleship")
    if not allowed:
        conn.close()
        raise HTTPException(status_code=429, detail=f"Морской бой на перерыве ещё {remaining // 3600} ч {(remaining % 3600) // 60} мин")
    if not change_balance(cur, user_id, -bet):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    empty = "0" * (BS_SIZE * BS_SIZE)

    cur.execute("""
        INSERT INTO battleship_games
        (user_id, player_field, bot_field, player_shots, bot_shots, bet, turn, status,
         player_defenses, bot_defenses, plane_available, radar_cells, radar_until, sea_reward_paid)
        VALUES (?, ?, ?, ?, ?, ?, 'player', 'active', ?, ?, 0, NULL, 0, 0)
    """, (user_id, player_field, bot_field, empty, empty, bet, player_defenses, bot_defenses))
    game_id = cur.lastrowid
    conn.commit()

    balance = require_balance(cur, user_id)
    sea_points = _bs_points(cur, user_id)
    conn.commit()
    conn.close()
    try:
        bot_db.log_action(user_id, "game", f"Морской бой: старт game_id={game_id}; ставка={bet}; player_defenses={player_defenses.count('D')}", amount=0)
    except Exception:
        logger.exception("Не удалось записать старт морского боя user_id=%s game_id=%s", user_id, game_id)

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
        "sea_points": sea_points,
        "player_defenses": player_defenses,
        "plane_available": False,
        "plane_buys": 0,
        "defenses_bought": 0,
        "radar_bought": 0,
        "special_costs": {"plane": BS_PLANE_COST, "radar": BS_RADAR_COST, "defense": BS_DEFENSE_COST},
    }


@app.get("/api/battleship/state")
def bs_state(init_data: str = Query(..., alias="initData"), game_id: int = Query(...)):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    _bs_ensure_special_schema(conn)
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, player_field, bot_field, player_shots, bot_shots,
               bet, status, turn, player_defenses, bot_defenses, plane_available, plane_buys, defenses_bought,
               radar_cells, radar_until, radar_bought
        FROM battleship_games WHERE id = ?
    """, (game_id,))
    row = cur.fetchone()
    if not row or row["user_id"] != user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="Игра не найдена")

    balance = require_balance(cur, user_id)
    sea_points = _bs_points(cur, user_id)
    conn.commit()
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
        "sea_points": sea_points,
        "player_defenses": row["player_defenses"],
        "plane_available": bool(row["plane_available"]),
        "plane_buys": int(row["plane_buys"] or 0),
        "defenses_bought": int(row["defenses_bought"] or 0),
        "radar_cells": [i for i in range(100) if row["radar_cells"] and row["radar_cells"][i] == "1"] if int(row["radar_until"] or 0) >= int(time.time()) else [],
        "radar_until": int(row["radar_until"] or 0),
        "radar_bought": int(row["radar_bought"] or 0),
        "special_costs": {"plane": BS_PLANE_COST, "radar": BS_RADAR_COST, "defense": BS_DEFENSE_COST},
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
    _bs_ensure_special_schema(conn)
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, player_field, bot_field, player_shots, bot_shots,
               bet, status, turn, sea_reward_paid
        FROM battleship_games WHERE id = ? FOR UPDATE
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

    sea_reward = 0
    if win:
        cur.execute("""
            UPDATE battleship_games SET player_shots = ?, status = 'win', sea_reward_paid = 1
            WHERE id = ? AND status = 'active' AND turn = 'player' AND player_shots = ?
        """, (shots_str, game_id, g["player_shots"]))
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            _log_security_attempt(user_id, "battleship_duplicate_or_racing_fire", f"game_id={game_id}; row={row}; col={col}")
            raise HTTPException(status_code=409, detail="Выстрел уже обработан. Обнови поле.")
        if not change_balance(cur, user_id, g["bet"] * 2):
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=500, detail="Не удалось зачислить награду за победу")
        if not g["sea_reward_paid"]:
            sea_reward = BS_SEA_POINTS_PER_SURVIVING_SHIP * _bs_count_surviving_ships(g["player_field"], g["bot_shots"])
            _bs_change_points(cur, user_id, sea_reward)
    else:
        next_turn = "bot" if result == "miss" else "player"
        cur.execute("""
            UPDATE battleship_games SET player_shots = ?, turn = ?
            WHERE id = ? AND status = 'active' AND turn = 'player' AND player_shots = ?
        """, (shots_str, next_turn, game_id, g["player_shots"]))
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            _log_security_attempt(user_id, "battleship_duplicate_or_racing_fire", f"game_id={game_id}; row={row}; col={col}")
            raise HTTPException(status_code=409, detail="Выстрел уже обработан. Обнови поле.")
    conn.commit()

    bot_msg = ""
    cur.execute("SELECT status, turn FROM battleship_games WHERE id = ?", (game_id,))
    bot_turn_state = cur.fetchone()
    if bot_turn_state and bot_turn_state["status"] == "active" and bot_turn_state["turn"] == "bot":
        bot_msg = _bs_run_bot_turn(cur, game_id)

    # Финальное состояние
    cur.execute("""
        SELECT player_field, bot_field, player_shots, bot_shots, bet, status, turn,
               player_defenses, plane_available, plane_buys, defenses_bought, radar_cells, radar_until, radar_bought
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
    sea_points = _bs_points(cur, user_id)
    conn.commit()
    conn.close()
    try:
        bot_db.log_action(
            user_id, "game",
            f"Морской бой ход: game_id={game_id}; клетка=({row + 1},{col + 1}); result={result}; "
            f"status={completed_status}; turn={final['turn']}; sea_reward={sea_reward}",
            amount=0,
        )
    except Exception:
        logger.exception("Не удалось записать аудит хода морского боя user_id=%s game_id=%s", user_id, game_id)

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
        "sea_points": sea_points,
        "sea_reward": sea_reward,
        "player_defenses": final["player_defenses"],
        "plane_available": bool(final["plane_available"]),
        "plane_buys": int(final["plane_buys"] or 0),
        "defenses_bought": int(final["defenses_bought"] or 0),
        "radar_cells": [i for i in range(100) if final["radar_cells"] and final["radar_cells"][i] == "1"] if int(final["radar_until"] or 0) >= int(time.time()) else [],
        "radar_until": int(final["radar_until"] or 0),
        "radar_bought": int(final["radar_bought"] or 0),
        "defenses_bought": int(final["defenses_bought"] or 0),
        "plane_buys": int(final["plane_buys"] or 0),
        "special_costs": {"plane": BS_PLANE_COST, "radar": BS_RADAR_COST, "defense": BS_DEFENSE_COST},
    }


@app.post("/api/battleship/special")
def bs_special(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        game_id = int(payload.get("game_id"))
        action = str(payload.get("action") or "")
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный запрос")

    conn = db()
    _bs_ensure_special_schema(conn)
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, player_field, bot_field, player_shots, bot_shots, bet, status, turn,
               player_defenses, bot_defenses, plane_available, plane_buys, defenses_bought, sea_reward_paid, radar_bought
        FROM battleship_games WHERE id = ? FOR UPDATE
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

    result = action
    bot_msg = ""
    sea_reward = 0
    if action == "buy_plane":
        if g["plane_available"] or int(g["plane_buys"] or 0) >= BS_PLANE_MAX:
            conn.close()
            raise HTTPException(status_code=400, detail="Лимит самолётов за игру — 2")
        try:
            _bs_change_points(cur, user_id, -BS_PLANE_COST)
        except HTTPException:
            conn.rollback()
            conn.close()
            raise
        cur.execute("UPDATE battleship_games SET plane_available = 1, plane_buys = plane_buys + 1 WHERE id = ? AND plane_available = 0 AND plane_buys < ? AND status = 'active' AND turn = 'player'", (game_id, BS_PLANE_MAX))
        if cur.rowcount != 1:
            _bs_change_points(cur, user_id, BS_PLANE_COST)
            conn.commit()
            conn.close()
            raise HTTPException(status_code=400, detail="Самолёт уже куплен или сейчас нельзя его использовать")
    elif action == "place_defense":
        try:
            row, col = int(payload.get("row")), int(payload.get("col"))
        except (TypeError, ValueError):
            conn.close()
            raise HTTPException(status_code=400, detail="Выбери клетку для ПВО")
        if not (0 <= row < BS_SIZE and 0 <= col < BS_SIZE):
            conn.close()
            raise HTTPException(status_code=400, detail="Координаты вне поля")
        idx = _bs_cell_index(row, col)
        defenses = list(g["player_defenses"] or "." * 100)
        if g["player_field"][idx] == "S" or defenses[idx] == "D":
            conn.close()
            raise HTTPException(status_code=400, detail="ПВО нельзя ставить на корабль или поверх другой установки")
        if int(g["defenses_bought"] or 0) >= BS_DEFENSE_MAX:
            conn.close()
            raise HTTPException(status_code=400, detail="Максимум 3 установки ПВО за игру")
        try:
            _bs_change_points(cur, user_id, -BS_DEFENSE_COST)
        except HTTPException:
            conn.rollback()
            conn.close()
            raise
        defenses[idx] = "D"
        cur.execute("UPDATE battleship_games SET player_defenses = ?, defenses_bought = defenses_bought + 1 WHERE id = ?", ("".join(defenses), game_id))
        result = "defense_placed"
    elif action == "radar":
        if int(g["radar_bought"] or 0) >= 1:
            conn.close()
            raise HTTPException(status_code=400, detail="Радар можно использовать только один раз за игру")
        try:
            row, col = int(payload.get("row")), int(payload.get("col"))
        except (TypeError, ValueError):
            conn.close()
            raise HTTPException(status_code=400, detail="Выбери центр радара")
        if not (0 <= row < 10 and 0 <= col < 10):
            conn.close()
            raise HTTPException(status_code=400, detail="Координаты вне поля")
        try:
            _bs_change_points(cur, user_id, -BS_RADAR_COST)
        except HTTPException:
            conn.rollback()
            conn.close()
            raise
        mask = ["."] * 100
        for rr in range(max(0, row - 1), min(10, row + 2)):
            for cc in range(max(0, col - 1), min(10, col + 2)):
                idx = _bs_cell_index(rr, cc)
                if g["bot_field"][idx] == "S":
                    mask[idx] = "1"
        until = int(time.time()) + BS_RADAR_SECONDS
        cur.execute("UPDATE battleship_games SET radar_cells = ?, radar_until = ?, radar_bought = 1 WHERE id = ? AND radar_bought = 0", ("".join(mask), until, game_id))
        if cur.rowcount != 1:
            _bs_change_points(cur, user_id, BS_RADAR_COST)
            conn.commit()
            conn.close()
            raise HTTPException(status_code=400, detail="Радар уже использован")
    elif action == "plane":
        if not g["plane_available"]:
            conn.close()
            raise HTTPException(status_code=400, detail="Сначала купи самолёт")
        orientation = payload.get("orientation")
        try:
            row, col = int(payload.get("row")), int(payload.get("col"))
        except (TypeError, ValueError):
            conn.close()
            raise HTTPException(status_code=400, detail="Выбери клетку начала линии")
        if orientation not in ("horizontal", "vertical") or not (0 <= row < 10 and 0 <= col < 10):
            conn.close()
            raise HTTPException(status_code=400, detail="Некорректная линия атаки")
        if (orientation == "horizontal" and col + 5 > 10) or (orientation == "vertical" and row + 5 > 10):
            conn.close()
            raise HTTPException(status_code=400, detail="Линия из 5 клеток выходит за границы поля")
        cur.execute("UPDATE battleship_games SET plane_available = 0 WHERE id = ? AND plane_available = 1 AND status = 'active' AND turn = 'player'", (game_id,))
        if cur.rowcount != 1:
            conn.close()
            raise HTTPException(status_code=400, detail="Самолёт уже использован")
        line = [_bs_cell_index(row, col + n) if orientation == "horizontal" else _bs_cell_index(row + n, col) for n in range(5)]
        shots = list(g["player_shots"])
        defenses = list(g["bot_defenses"] or "." * 100)
        intercepted = next((battery for idx in line if (battery := _bs_defense_at("".join(defenses), idx)) is not None), None)
        hit_any = False
        if intercepted is not None:
            defenses[intercepted] = "."
            result = "intercepted"
            cur.execute("UPDATE battleship_games SET bot_defenses = ?, plane_available = 0, turn = 'bot' WHERE id = ?", ("".join(defenses), game_id))
        else:
            for idx in line:
                if shots[idx] in ("1", "2", "3"):
                    continue
                if g["bot_field"][idx] == "S":
                    hit_any = True
                    shots[idx] = "2"
                    rr, cc = divmod(idx, 10)
                    if _bs_check_sunk(g["bot_field"], "".join(shots), rr, cc):
                        _bs_mark_sunk(g["bot_field"], shots, rr, cc)
                else:
                    shots[idx] = "1"
            shots_str = "".join(shots)
            won = all(g["bot_field"][i] != "S" or shots_str[i] == "3" for i in range(100))
            if won:
                cur.execute(
                    "UPDATE battleship_games SET player_shots = ?, plane_available = 0, status = 'win', turn = 'player', sea_reward_paid = 1 "
                    "WHERE id = ? AND status = 'active' AND turn = 'player' AND player_shots = ? AND plane_available = 0",
                    (shots_str, game_id, g["player_shots"]),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    conn.close()
                    _log_security_attempt(user_id, "battleship_duplicate_or_racing_plane", f"game_id={game_id}")
                    raise HTTPException(status_code=409, detail="Атака самолёта уже обработана")
                if not change_balance(cur, user_id, int(g["bet"]) * 2):
                    conn.rollback()
                    conn.close()
                    raise HTTPException(status_code=500, detail="Не удалось зачислить награду за победу")
                if not g["sea_reward_paid"]:
                    sea_reward = BS_SEA_POINTS_PER_SURVIVING_SHIP * _bs_count_surviving_ships(g["player_field"], g["bot_shots"])
                    _bs_change_points(cur, user_id, sea_reward)
                result = "win"
            else:
                # An aircraft action consumes the player's turn even when it hits.
                cur.execute(
                    "UPDATE battleship_games SET player_shots = ?, plane_available = 0, turn = 'bot' "
                    "WHERE id = ? AND status = 'active' AND turn = 'player' AND player_shots = ? AND plane_available = 0",
                    (shots_str, game_id, g["player_shots"]),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    conn.close()
                    _log_security_attempt(user_id, "battleship_duplicate_or_racing_plane", f"game_id={game_id}")
                    raise HTTPException(status_code=409, detail="Атака самолёта уже обработана")
                result = "hit" if hit_any else "miss"
        conn.commit()
        if result in ("intercepted", "miss", "hit"):
            bot_msg = _bs_run_bot_turn(cur, game_id)
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Неизвестное спецдействие")

    cur.execute("""
        SELECT player_field, bot_field, player_shots, bot_shots, bet, status, turn,
               player_defenses, plane_available, plane_buys, defenses_bought, radar_cells, radar_until, radar_bought
        FROM battleship_games WHERE id = ?
    """, (game_id,))
    final = cur.fetchone()
    balance = require_balance(cur, user_id)
    sea_points = _bs_points(cur, user_id)
    radar_visible = [i for i in range(100) if final["radar_cells"] and final["radar_cells"][i] == "1"] if int(final["radar_until"] or 0) >= int(time.time()) else []
    conn.commit()
    conn.close()
    try:
        bot_db.log_action(
            user_id, "game",
            f"Морской бой спецдействие: game_id={game_id}; action={action}; result={result}; "
            f"status={final['status']}; turn={final['turn']}; sea_reward={sea_reward}",
            amount=0,
        )
    except Exception:
        logger.exception("Не удалось записать аудит спецдействия морского боя user_id=%s game_id=%s", user_id, game_id)
    return {
        "ok": True, "result": result, "bot_msg": bot_msg, "game_id": game_id,
        "bet": final["bet"], "reward": int(final["bet"]) * 2,
        "player_field": final["player_field"],
        **({"bot_field": final["bot_field"]} if final["status"] in {"win", "lose"} else {}),
        "player_shots": final["player_shots"], "bot_shots": final["bot_shots"],
        "turn": final["turn"], "status": final["status"], "balance": balance,
        "sea_points": sea_points, "sea_reward": sea_reward,
        "player_defenses": final["player_defenses"], "plane_available": bool(final["plane_available"]),
        "plane_buys": int(final["plane_buys"] or 0),
        "defenses_bought": int(final["defenses_bought"] or 0),
        "radar_bought": int(final["radar_bought"] or 0),
        "radar_cells": radar_visible, "radar_until": int(final["radar_until"] or 0),
        "special_costs": {"plane": BS_PLANE_COST, "radar": BS_RADAR_COST, "defense": BS_DEFENSE_COST},
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
ISLAND_BUILDINGS = {
    "farm": {"name": "🌾 Ферма", "price": 35000, "requires": "island", "materials": {"wood": 10, "fiber": 3}, "description": "Производство урожая"},
    "mine": {"name": "⛏️ Шахта", "price": 60000, "requires": "island", "materials": {"wood": 15, "stone": 10}, "description": "Камень, руда и шанс найти кристаллы"},
    "workshop": {"name": "🛠️ Мастерская", "price": 80000, "requires": "mine", "materials": {"wood": 20, "stone": 15, "ore": 5}, "description": "Нужна для производства инструментов и механизмов"},
    "market": {"name": "🛒 Рынок", "price": 90000, "requires": "pier", "materials": {"wood": 25, "fiber": 10}, "description": "Открывает торговые контракты в следующих обновлениях"},
    "lighthouse": {"name": "🗼 Маяк", "price": 150000, "requires": "ship", "materials": {"stone": 40, "ore": 15, "gears": 3}, "description": "Открывает подготовку к дальним экспедициям"},
}
ISLAND_RESOURCE_ACTION_COOLDOWN = 3 * 3600
ISLAND_RESOURCE_ACTIONS = {
    "farmer": {"outputs": {"crops": (4, 8), "fiber": (1, 3)}, "needs": {}, "message": "Ты собрал урожай и волокно."},
    "gardener": {"outputs": {"wood": (1, 3), "fiber": (2, 5), "seeds": (1, 3)}, "needs": {}, "message": "Ты собрал древесину, волокно и семена."},
    "fisher": {"outputs": {"fish": (2, 5)}, "needs": {}, "message": "Ты вернулся с уловом."},
    "miner": {"outputs": {"stone": (3, 6), "ore": (1, 3), "crystal": (0, 1)}, "needs": {}, "message": "Ты добыл камень и руду."},
    "blacksmith": {"outputs": {"tools": (1, 2)}, "needs": {"ore": 2, "stone": 1}, "message": "Ты изготовил инструменты."},
    "engineer": {"outputs": {"gears": (1, 2)}, "needs": {"ore": 1, "wood": 2}, "message": "Ты собрал механические детали."},
    "explorer": {"outputs": {"maps": (1, 1)}, "needs": {}, "message": "Ты нашёл карту для экспедиций."},
    "merchant": {"outputs": {"trade_goods": (2, 4)}, "needs": {}, "message": "Ты подготовил товары для торговли."},
    "cook": {"outputs": {"meals": (1, 3)}, "needs": {"crops": 2, "fish": 1}, "message": "Ты приготовил еду для экспедиций."},
    "artist": {"outputs": {"decor": (1, 2)}, "needs": {}, "message": "Ты создал предметы декора."},
    "photographer": {"outputs": {"photos": (1, 3)}, "needs": {}, "message": "Ты сделал снимки острова."},
    "vet": {"outputs": {"pet_treats": (1, 2)}, "needs": {"crops": 1}, "message": "Ты приготовил лакомства для питомцев."},
    "programmer": {"outputs": {"circuits": (1, 2)}, "needs": {"ore": 1}, "message": "Ты собрал электронные платы."},
}
ISLAND_RESOURCE_NAMES = {
    "wood": "🪵 Дерево", "stone": "🪨 Камень", "ore": "⛓️ Руда",
    "fiber": "🧵 Волокно", "crops": "🌾 Урожай", "fish": "🐟 Рыба",
    "seeds": "🌱 Семена", "tools": "🔧 Инструменты", "gears": "⚙️ Механизмы",
    "maps": "🗺️ Карты", "trade_goods": "📦 Торговые товары", "meals": "🍲 Еда",
    "decor": "🖼️ Декор",
    "photos": "📸 Фотографии", "pet_treats": "🦴 Лакомства", "circuits": "🔌 Платы",
    "crystal": "💎 Кристаллы", "artifacts": "🏺 Артефакты",
}
ISLAND_CONTRACTS = {
    "harvest": {"name": "Поставка урожая", "item": "crops", "quantity": 5, "reward": 4000, "experience": 10},
    "fishing": {"name": "Свежий улов", "item": "fish", "quantity": 3, "reward": 5000, "experience": 10},
    "stone": {"name": "Камень для стройки", "item": "stone", "quantity": 5, "reward": 6000, "experience": 12},
    "tools": {"name": "Инструменты для порта", "item": "tools", "quantity": 1, "reward": 8000, "experience": 15},
}
ISLAND_EXPEDITIONS = {
    "nearby": {"name": "Ближний маршрут", "duration": 2 * 3600, "requires": "ship", "cost": {"maps": 1, "meals": 1}},
    "distant": {"name": "Дальний маршрут", "duration": 8 * 3600, "requires": "lighthouse", "cost": {"maps": 3, "meals": 2, "gears": 1}},
}
ISLAND_DECORATIONS = {
    "palm": {"name": "Пальма", "emoji": "🌴", "price": 4000},
    "flower": {"name": "Цветы", "emoji": "🌸", "price": 2500},
    "lantern": {"name": "Фонарь", "emoji": "🏮", "price": 6000},
    "fountain": {"name": "Фонтан", "emoji": "⛲", "price": 20000},
    "statue": {"name": "Статуя", "emoji": "🗿", "price": 15000},
}
HOUSE_INCOME = 5000
HOUSE_COOLDOWN = 5 * 3600
SHIP_INCOME = 75000
SHIP_COOLDOWN = 48 * 3600



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

    cur.execute("SELECT building_key, level, built_at FROM island_buildings WHERE user_id = %s ORDER BY building_key", (user_id,))
    buildings = {r["building_key"]: {"level": int(r["level"]), "built_at": r["built_at"]} for r in cur.fetchall()}
    cur.execute("SELECT item_key, quantity FROM island_inventory WHERE user_id = %s AND quantity > 0 ORDER BY item_key", (user_id,))
    inventory = [{"key": r["item_key"], "name": ISLAND_RESOURCE_NAMES.get(r["item_key"], r["item_key"]), "quantity": int(r["quantity"])} for r in cur.fetchall()]
    cur.execute("SELECT island_level, experience, last_action_at FROM island_progress WHERE user_id = %s", (user_id,))
    progress_row = cur.fetchone()
    last_action_at = progress_row["last_action_at"] if progress_row else None
    action_remaining = 0
    if last_action_at:
        last_action_dt = datetime.fromisoformat(str(last_action_at))
        action_remaining = max(0, int(ISLAND_RESOURCE_ACTION_COOLDOWN - (datetime.now() - last_action_dt).total_seconds()))
    progress = {
        "level": int(progress_row["island_level"]) if progress_row else 1,
        "experience": int(progress_row["experience"]) if progress_row else 0,
        "last_action_at": last_action_at,
        "action_remaining_seconds": action_remaining,
        "action_cooldown": ISLAND_RESOURCE_ACTION_COOLDOWN,
    }
    cur.execute("SELECT profession, level FROM professions WHERE user_id = %s", (user_id,))
    profession_row = cur.fetchone()
    profession = None
    if profession_row and profession_row["profession"] in PROFESSION_INFO:
        profession = {"key": profession_row["profession"], "name": PROFESSION_INFO[profession_row["profession"]]["name"], "level": int(profession_row["level"] or 1)}

    cur.execute("SELECT id, decoration_key, x_pct, y_pct FROM island_decorations WHERE user_id = %s ORDER BY id", (user_id,))
    decorations = [
        {"id": int(r["id"]), "key": r["decoration_key"],
         "name": ISLAND_DECORATIONS.get(r["decoration_key"], {}).get("name", r["decoration_key"]),
         "emoji": ISLAND_DECORATIONS.get(r["decoration_key"], {}).get("emoji", "✨"),
         "x": float(r["x_pct"]), "y": float(r["y_pct"])}
        for r in cur.fetchall()
    ]
    today = datetime.now().date().isoformat()
    cur.execute("SELECT contract_key FROM island_contract_claims WHERE user_id = %s AND claimed_date = %s", (user_id, today))
    claimed_contracts = {r["contract_key"] for r in cur.fetchall()}
    contracts = [
        {"key": key, **value, "item_name": ISLAND_RESOURCE_NAMES.get(value["item"], value["item"]), "claimed": key in claimed_contracts}
        for key, value in ISLAND_CONTRACTS.items()
    ]
    cur.execute("SELECT expedition_key, started_at, completes_at, claimed_at FROM island_expeditions WHERE user_id = %s", (user_id,))
    expedition_row = cur.fetchone()
    expedition = None
    if expedition_row:
        expedition = {
            "key": expedition_row["expedition_key"],
            "started_at": expedition_row["started_at"],
            "completes_at": expedition_row["completes_at"],
            "claimed_at": expedition_row["claimed_at"],
            "active": expedition_row["claimed_at"] is None,
            "remaining_seconds": max(0, int((datetime.fromisoformat(str(expedition_row["completes_at"])) - datetime.now()).total_seconds())) if expedition_row["claimed_at"] is None else 0,
            "name": ISLAND_EXPEDITIONS.get(expedition_row["expedition_key"], {}).get("name", expedition_row["expedition_key"]),
        }
    conn.close()

    return {
        "state": state,
        "prices": ISLAND_PRICES,
        "names": ISLAND_NAMES,
        "buildings": buildings,
        "building_catalog": ISLAND_BUILDINGS,
        "inventory": inventory,
        "resource_names": ISLAND_RESOURCE_NAMES,
        "progress": progress,
        "profession": profession,
        "contracts": contracts,
        "expedition": expedition,
        "decorations": decorations,
        "decoration_catalog": ISLAND_DECORATIONS,
        "balance": balance,
        "house_income": HOUSE_INCOME,
        "house_cooldown": HOUSE_COOLDOWN,
        "ship_income": SHIP_INCOME,
        "ship_cooldown": SHIP_COOLDOWN,
    }


@app.get("/api/island/visit")
def visit_island(username: str = Query(...), init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    target_username = username.strip().lstrip("@")
    if not target_username or len(target_username) > 32 or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", target_username):
        raise HTTPException(status_code=400, detail="Укажи корректный Telegram username")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username FROM users WHERE LOWER(REPLACE(username, '@', '')) = LOWER(%s) LIMIT 1", (target_username,))
    target = cur.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Игрок с таким username не найден")
    target_id = int(target["user_id"])
    cur.execute("SELECT has_island, has_house, has_pier, has_ship FROM islands WHERE user_id = %s", (target_id,))
    island = cur.fetchone()
    state = {
        "has_island": bool(island and island["has_island"]),
        "has_house": bool(island and island["has_house"]),
        "has_pier": bool(island and island["has_pier"]),
        "has_ship": bool(island and island["has_ship"]),
    }
    cur.execute("SELECT building_key, level FROM island_buildings WHERE user_id = %s ORDER BY building_key", (target_id,))
    buildings = {r["building_key"]: {"level": int(r["level"]), "name": ISLAND_BUILDINGS[r["building_key"]]["name"]} for r in cur.fetchall() if r["building_key"] in ISLAND_BUILDINGS}
    cur.execute("SELECT decoration_key, x_pct, y_pct FROM island_decorations WHERE user_id = %s ORDER BY id", (target_id,))
    decorations = [
        {"key": r["decoration_key"], "name": ISLAND_DECORATIONS.get(r["decoration_key"], {}).get("name", r["decoration_key"]),
         "emoji": ISLAND_DECORATIONS.get(r["decoration_key"], {}).get("emoji", "✨"), "x": float(r["x_pct"]), "y": float(r["y_pct"])}
        for r in cur.fetchall()
    ]
    cur.execute("SELECT island_level FROM island_progress WHERE user_id = %s", (target_id,))
    level_row = cur.fetchone()
    conn.close()
    return {
        "username": target["username"] or target_username,
        "state": state,
        "buildings": buildings,
        "decorations": decorations,
        "level": int(level_row["island_level"]) if level_row else 1,
    }


@app.post("/api/island/build")
def build_island_building(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    building_key = str(payload.get("building", ""))
    config = ISLAND_BUILDINGS.get(building_key)
    if not config:
        raise HTTPException(status_code=400, detail="Неизвестная постройка")

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT has_island, has_pier, has_ship FROM islands WHERE user_id = %s FOR UPDATE", (user_id,))
    island = cur.fetchone()
    if not island or not island["has_island"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Сначала купи остров")
    if config["requires"] == "pier" and not island["has_pier"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Сначала построй причал")
    if config["requires"] == "ship" and not island["has_ship"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Сначала купи корабль")
    if config["requires"] in ISLAND_BUILDINGS:
        cur.execute("SELECT 1 FROM island_buildings WHERE user_id = %s AND building_key = %s", (user_id, config["requires"]))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail=f"Сначала построй {ISLAND_BUILDINGS[config['requires']]['name']}")
    cur.execute("SELECT 1 FROM island_buildings WHERE user_id = %s AND building_key = %s", (user_id, building_key))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Эта постройка уже есть")
    for resource_key, needed in config.get("materials", {}).items():
        cur.execute("SELECT quantity FROM island_inventory WHERE user_id = %s AND item_key = %s FOR UPDATE", (user_id, resource_key))
        resource = cur.fetchone()
        if not resource or int(resource["quantity"]) < needed:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Для постройки нужно: {ISLAND_RESOURCE_NAMES.get(resource_key, resource_key)} ×{needed}")
    if not change_balance(cur, user_id, -int(config["price"])):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Недостаточно LDC. Нужно: {config['price']:,}")
    for resource_key, needed in config.get("materials", {}).items():
        cur.execute("UPDATE island_inventory SET quantity = quantity - %s WHERE user_id = %s AND item_key = %s", (needed, user_id, resource_key))
    cur.execute("INSERT INTO island_buildings (user_id, building_key, level) VALUES (%s, %s, 1)", (user_id, building_key))
    cur.execute("""
        INSERT INTO island_progress (user_id, island_level, experience)
        VALUES (%s, 1, 25)
        ON CONFLICT(user_id) DO UPDATE SET experience = island_progress.experience + 25
    """, (user_id,))
    cur.execute("SELECT island_level, experience FROM island_progress WHERE user_id = %s FOR UPDATE", (user_id,))
    progress = cur.fetchone()
    next_level = min(6, max(int(progress["island_level"]), 1 + int(progress["experience"]) // 100))
    cur.execute("UPDATE island_progress SET island_level = %s WHERE user_id = %s", (next_level, user_id))
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {"ok": True, "message": f"Построено: {config['name']}", "balance": balance, "island_level": next_level}


@app.post("/api/island/upgrade")
def upgrade_island_building(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    building_key = str(payload.get("building", ""))
    config = ISLAND_BUILDINGS.get(building_key)
    if not config:
        raise HTTPException(status_code=400, detail="Неизвестная постройка")

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT has_island FROM islands WHERE user_id = %s FOR UPDATE", (user_id,))
    island = cur.fetchone()
    if not island or not island["has_island"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Сначала купи остров")
    cur.execute("SELECT level FROM island_buildings WHERE user_id = %s AND building_key = %s FOR UPDATE", (user_id, building_key))
    building = cur.fetchone()
    if not building:
        conn.close()
        raise HTTPException(status_code=400, detail="Сначала построй это здание")
    level = int(building["level"])
    if level >= 3:
        conn.close()
        raise HTTPException(status_code=400, detail="Здание уже достигло максимального уровня")
    price = max(1, int(config["price"] * 0.6 * level))
    materials = {"wood": level * 10, "stone": level * 5}
    if building_key in ("workshop", "lighthouse"):
        materials["ore"] = level * 3
    for resource_key, needed in materials.items():
        cur.execute("SELECT quantity FROM island_inventory WHERE user_id = %s AND item_key = %s FOR UPDATE", (user_id, resource_key))
        resource = cur.fetchone()
        if not resource or int(resource["quantity"]) < needed:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Для улучшения нужно: {ISLAND_RESOURCE_NAMES.get(resource_key, resource_key)} ×{needed}")
    if not change_balance(cur, user_id, -price):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Недостаточно LDC. Нужно: {price:,}")
    for resource_key, needed in materials.items():
        cur.execute("UPDATE island_inventory SET quantity = quantity - %s WHERE user_id = %s AND item_key = %s", (needed, user_id, resource_key))
    new_level = level + 1
    cur.execute("UPDATE island_buildings SET level = %s WHERE user_id = %s AND building_key = %s", (new_level, user_id, building_key))
    cur.execute("""
        INSERT INTO island_progress (user_id, island_level, experience)
        VALUES (%s, 1, 15)
        ON CONFLICT(user_id) DO UPDATE SET experience = island_progress.experience + 15
    """, (user_id,))
    cur.execute("SELECT island_level, experience FROM island_progress WHERE user_id = %s FOR UPDATE", (user_id,))
    progress = cur.fetchone()
    island_level = min(6, max(int(progress["island_level"]), 1 + int(progress["experience"]) // 100))
    cur.execute("UPDATE island_progress SET island_level = %s WHERE user_id = %s", (island_level, user_id))
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {"ok": True, "message": f"{config['name']} улучшено до уровня {new_level}", "balance": balance, "building_level": new_level, "island_level": island_level}


@app.post("/api/island/profession-action")
def island_profession_action(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT has_island FROM islands WHERE user_id = %s", (user_id,))
    island = cur.fetchone()
    if not island or not island["has_island"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Сначала купи остров")
    cur.execute("SELECT profession, level FROM professions WHERE user_id = %s FOR UPDATE", (user_id,))
    prof = cur.fetchone()
    if not prof or prof["profession"] not in ISLAND_RESOURCE_ACTIONS:
        conn.close()
        raise HTTPException(status_code=400, detail="Твоя профессия пока не может выполнять островные работы")
    profession_key = prof["profession"]
    action = ISLAND_RESOURCE_ACTIONS[profession_key]
    cur.execute("INSERT INTO island_progress (user_id) VALUES (%s) ON CONFLICT(user_id) DO NOTHING", (user_id,))
    cur.execute("SELECT last_action_at FROM island_progress WHERE user_id = %s FOR UPDATE", (user_id,))
    progress = cur.fetchone()
    now = datetime.now()
    if progress and progress["last_action_at"]:
        last_action = datetime.fromisoformat(str(progress["last_action_at"]))
        elapsed = (now - last_action).total_seconds()
        if elapsed < ISLAND_RESOURCE_ACTION_COOLDOWN:
            remaining = int(ISLAND_RESOURCE_ACTION_COOLDOWN - elapsed)
            conn.close()
            raise HTTPException(status_code=400, detail=f"Островная работа восстановится через {remaining // 3600} ч {(remaining % 3600) // 60} мин")
    for resource_key, needed in action["needs"].items():
        cur.execute("SELECT quantity FROM island_inventory WHERE user_id = %s AND item_key = %s FOR UPDATE", (user_id, resource_key))
        item = cur.fetchone()
        if not item or int(item["quantity"]) < needed:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Не хватает ресурса: {ISLAND_RESOURCE_NAMES.get(resource_key, resource_key)} × {needed}")
    for resource_key, needed in action["needs"].items():
        cur.execute("UPDATE island_inventory SET quantity = quantity - %s WHERE user_id = %s AND item_key = %s", (needed, user_id, resource_key))
    gains = {}
    for resource_key, bounds in action["outputs"].items():
        amount = random.randint(bounds[0], bounds[1])
        if resource_key == "crystal":
            amount = 1 if random.random() < 0.15 else 0
        if amount > 0:
            related_building = {
                "crops": "farm", "fiber": "farm",
                "stone": "mine", "ore": "mine", "crystal": "mine",
                "tools": "workshop", "gears": "workshop", "circuits": "workshop",
            }.get(resource_key)
            if related_building:
                cur.execute("SELECT level FROM island_buildings WHERE user_id = %s AND building_key = %s", (user_id, related_building))
                building_row = cur.fetchone()
                if building_row:
                    amount += max(0, int(building_row["level"]) - 1)
        if amount <= 0:
            continue
        cur.execute("""
            INSERT INTO island_inventory (user_id, item_key, quantity) VALUES (%s, %s, %s)
            ON CONFLICT(user_id, item_key) DO UPDATE SET quantity = island_inventory.quantity + EXCLUDED.quantity
        """, (user_id, resource_key, amount))
        gains[resource_key] = amount
    cur.execute("""
        INSERT INTO island_progress (user_id, island_level, experience, last_action_at)
        VALUES (%s, 1, 5, %s)
        ON CONFLICT(user_id) DO UPDATE SET experience = island_progress.experience + 5, last_action_at = EXCLUDED.last_action_at
    """, (user_id, now.isoformat()))
    cur.execute("SELECT island_level, experience FROM island_progress WHERE user_id = %s FOR UPDATE", (user_id,))
    prog = cur.fetchone()
    level = min(6, max(int(prog["island_level"]), 1 + int(prog["experience"]) // 100))
    cur.execute("UPDATE island_progress SET island_level = %s WHERE user_id = %s", (level, user_id))
    conn.commit()
    conn.close()
    gains_text = ", ".join(f"{ISLAND_RESOURCE_NAMES.get(k, k)} ×{v}" for k, v in gains.items())
    if not gains_text:
        gains_text = "редкие находки не попались"
    return {"ok": True, "message": f"{action['message']} Получено: {gains_text}", "gains": gains, "island_level": level, "next_action_seconds": ISLAND_RESOURCE_ACTION_COOLDOWN}


@app.post("/api/island/decorations/buy")
def buy_island_decoration(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    decoration_key = str(payload.get("decoration", ""))
    config = ISLAND_DECORATIONS.get(decoration_key)
    if not config:
        raise HTTPException(status_code=400, detail="Неизвестное украшение")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT has_island FROM islands WHERE user_id = %s FOR UPDATE", (user_id,))
    island = cur.fetchone()
    if not island or not island["has_island"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Сначала купи остров")
    if not change_balance(cur, user_id, -int(config["price"])):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Недостаточно LDC. Нужно: {config['price']:,}")
    cur.execute("SELECT COUNT(*) AS count FROM island_decorations WHERE user_id = %s", (user_id,))
    count = int(cur.fetchone()["count"])
    x = float(18 + (count % 4) * 18)
    y = float(72 if count % 2 == 0 else 82)
    cur.execute("INSERT INTO island_decorations (user_id, decoration_key, x_pct, y_pct) VALUES (%s, %s, %s, %s)", (user_id, decoration_key, x, y))
    cur.execute("SELECT currval(pg_get_serial_sequence('island_decorations', 'id'))")
    decoration_id = int(cur.fetchone()["currval"])
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {"ok": True, "message": f"Добавлено украшение: {config['emoji']} {config['name']}", "decoration_id": decoration_id, "balance": balance}


@app.post("/api/island/decorations/move")
def move_island_decoration(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        decoration_id = int(payload.get("decoration_id"))
        x = float(payload.get("x"))
        y = float(payload.get("y"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректные координаты украшения")
    if not math.isfinite(x) or not math.isfinite(y) or not (3 <= x <= 97 and 3 <= y <= 97):
        raise HTTPException(status_code=400, detail="Украшение должно оставаться в пределах острова")
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE island_decorations SET x_pct = %s, y_pct = %s WHERE id = %s AND user_id = %s", (x, y, decoration_id, user_id))
    if cur.rowcount != 1:
        conn.close()
        raise HTTPException(status_code=404, detail="Украшение не найдено на твоём острове")
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Расположение украшения сохранено"}


@app.get("/api/island/contracts")
def get_island_contracts(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM island_buildings WHERE user_id = %s AND building_key = 'market'", (user_id,))
    has_market = cur.fetchone() is not None
    today = datetime.now().date().isoformat()
    cur.execute("SELECT contract_key FROM island_contract_claims WHERE user_id = %s AND claimed_date = %s", (user_id, today))
    claimed = {r["contract_key"] for r in cur.fetchall()}
    conn.close()
    return {"has_market": has_market, "contracts": [
        {"key": key, **value, "item_name": ISLAND_RESOURCE_NAMES.get(value["item"], value["item"]), "claimed": key in claimed}
        for key, value in ISLAND_CONTRACTS.items()
    ]}


@app.post("/api/island/contracts/claim")
def claim_island_contract(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    contract_key = str(payload.get("contract", ""))
    contract = ISLAND_CONTRACTS.get(contract_key)
    if not contract:
        raise HTTPException(status_code=400, detail="Неизвестный контракт")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM island_buildings WHERE user_id = %s AND building_key = 'market'", (user_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Сначала построй рынок")
    today = datetime.now().date().isoformat()
    cur.execute("""
        INSERT INTO island_contract_claims (user_id, contract_key, claimed_date)
        VALUES (%s, %s, %s) ON CONFLICT(user_id, contract_key, claimed_date) DO NOTHING
    """, (user_id, contract_key, today))
    if cur.rowcount != 1:
        conn.close()
        raise HTTPException(status_code=400, detail="Этот контракт уже выполнен сегодня")
    cur.execute("SELECT quantity FROM island_inventory WHERE user_id = %s AND item_key = %s FOR UPDATE", (user_id, contract["item"]))
    item = cur.fetchone()
    if not item or int(item["quantity"]) < contract["quantity"]:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=400, detail=f"Не хватает ресурса: {contract['quantity']} × {ISLAND_RESOURCE_NAMES.get(contract['item'], contract['item'])}")
    cur.execute("UPDATE island_inventory SET quantity = quantity - %s WHERE user_id = %s AND item_key = %s", (contract["quantity"], user_id, contract["item"]))
    change_balance(cur, user_id, int(contract["reward"]))
    cur.execute("""
        INSERT INTO island_progress (user_id, island_level, experience)
        VALUES (%s, 1, %s)
        ON CONFLICT(user_id) DO UPDATE SET experience = island_progress.experience + EXCLUDED.experience
    """, (user_id, int(contract["experience"])))
    cur.execute("SELECT island_level, experience FROM island_progress WHERE user_id = %s FOR UPDATE", (user_id,))
    progress = cur.fetchone()
    level = min(6, max(int(progress["island_level"]), 1 + int(progress["experience"]) // 100))
    cur.execute("UPDATE island_progress SET island_level = %s WHERE user_id = %s", (level, user_id))
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    return {"ok": True, "message": f"Контракт выполнен: +{contract['reward']:,} LDC", "balance": balance, "island_level": level}


@app.post("/api/island/expeditions/start")
def start_island_expedition(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    expedition_key = str(payload.get("expedition", ""))
    expedition_config = ISLAND_EXPEDITIONS.get(expedition_key)
    if not expedition_config:
        raise HTTPException(status_code=400, detail="Неизвестная экспедиция")
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT has_island, has_ship FROM islands WHERE user_id = %s FOR UPDATE", (user_id,))
    island = cur.fetchone()
    if not island or not island["has_island"] or not island["has_ship"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Для экспедиций нужен остров и корабль")
    if expedition_config["requires"] == "lighthouse":
        cur.execute("SELECT 1 FROM island_buildings WHERE user_id = %s AND building_key = 'lighthouse'", (user_id,))
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="Для дальнего маршрута нужен маяк")
    cur.execute("SELECT expedition_key, completes_at, claimed_at FROM island_expeditions WHERE user_id = %s FOR UPDATE", (user_id,))
    current = cur.fetchone()
    if current and current["claimed_at"] is None:
        conn.close()
        raise HTTPException(status_code=400, detail="Сначала забери награду за предыдущую экспедицию")
    for resource_key, needed in expedition_config["cost"].items():
        cur.execute("SELECT quantity FROM island_inventory WHERE user_id = %s AND item_key = %s FOR UPDATE", (user_id, resource_key))
        resource = cur.fetchone()
        if not resource or int(resource["quantity"]) < needed:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Для экспедиции нужно: {ISLAND_RESOURCE_NAMES.get(resource_key, resource_key)} ×{needed}")
    for resource_key, needed in expedition_config["cost"].items():
        cur.execute("UPDATE island_inventory SET quantity = quantity - %s WHERE user_id = %s AND item_key = %s", (needed, user_id, resource_key))
    now = datetime.now()
    completes = now + timedelta(seconds=expedition_config["duration"])
    if current:
        cur.execute("""
            UPDATE island_expeditions SET expedition_key = %s, started_at = %s, completes_at = %s, claimed_at = NULL
            WHERE user_id = %s
        """, (expedition_key, now.isoformat(), completes.isoformat(), user_id))
    else:
        cur.execute("""
            INSERT INTO island_expeditions (user_id, expedition_key, started_at, completes_at, claimed_at)
            VALUES (%s, %s, %s, %s, NULL)
        """, (user_id, expedition_key, now.isoformat(), completes.isoformat()))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"Экспедиция «{expedition_config['name']}» отправлена", "completes_at": completes.isoformat()}


@app.post("/api/island/expeditions/claim")
def claim_island_expedition(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT expedition_key, completes_at, claimed_at FROM island_expeditions WHERE user_id = %s FOR UPDATE", (user_id,))
    row = cur.fetchone()
    if not row or row["claimed_at"] is not None:
        conn.close()
        raise HTTPException(status_code=400, detail="Нет готовой экспедиции для получения")
    completes_at = datetime.fromisoformat(str(row["completes_at"]))
    now = datetime.now()
    if now < completes_at:
        remaining = int((completes_at - now).total_seconds())
        conn.close()
        raise HTTPException(status_code=400, detail=f"Экспедиция завершится через {remaining // 3600} ч {(remaining % 3600) // 60} мин")
    key = row["expedition_key"]
    if key == "nearby":
        rewards = {"stone": random.randint(2, 5), "fish": random.randint(1, 3)}
        bonus_ldc = 3000
    else:
        rewards = {"ore": random.randint(3, 6), "crystal": 1 if random.random() < 0.25 else 0, "artifacts": 1}
        bonus_ldc = 10000
    for resource_key, amount in rewards.items():
        if amount <= 0:
            continue
        cur.execute("""
            INSERT INTO island_inventory (user_id, item_key, quantity) VALUES (%s, %s, %s)
            ON CONFLICT(user_id, item_key) DO UPDATE SET quantity = island_inventory.quantity + EXCLUDED.quantity
        """, (user_id, resource_key, amount))
    cur.execute("UPDATE island_expeditions SET claimed_at = %s WHERE user_id = %s AND claimed_at IS NULL", (now.isoformat(), user_id))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=409, detail="Награда уже получена")
    change_balance(cur, user_id, bonus_ldc)
    cur.execute("""
        INSERT INTO island_progress (user_id, island_level, experience)
        VALUES (%s, 1, 15)
        ON CONFLICT(user_id) DO UPDATE SET experience = island_progress.experience + 15
    """, (user_id,))
    cur.execute("SELECT island_level, experience FROM island_progress WHERE user_id = %s FOR UPDATE", (user_id,))
    progress = cur.fetchone()
    level = min(6, max(int(progress["island_level"]), 1 + int(progress["experience"]) // 100))
    cur.execute("UPDATE island_progress SET island_level = %s WHERE user_id = %s", (level, user_id))
    conn.commit()
    balance = require_balance(cur, user_id)
    conn.close()
    reward_text = ", ".join(f"{ISLAND_RESOURCE_NAMES.get(k, k)} ×{v}" for k, v in rewards.items() if v > 0)
    return {"ok": True, "message": f"Экспедиция завершена: {reward_text} и +{bonus_ldc:,} LDC", "balance": balance, "island_level": level}


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


# ========== API: RCC ==========

RCC_CLICK_AMOUNT = 0.00001
RCC_EXCHANGE_RATE = 50500
RCC_GAME_COOLDOWN = 7200
RCC_UPGRADE_CONFIG = {
    "click": {"column": "click_level", "base_cost": 1000, "max_level": 10},
    "timer": {"column": "timer_level", "base_cost": 2500, "max_level": 5},
    "freeze": {"column": "freeze_level", "base_cost": 1500, "max_level": 5},
    "reward": {"column": "reward_level", "base_cost": 5000, "max_level": 5},
}


def _rcc_get_progress(cur, user_id: int) -> dict:
    """Load LCC clicker progress and regenerate one energy per minute.

    Normalize timestamps before subtraction: PostgreSQL drivers can return an
    aware datetime while older rows / SQLite return a naive datetime. Mixing
    those forms raises TypeError and used to silently reset elapsed time to 0,
    preventing energy from regenerating.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rcc_progress (
            user_id BIGINT PRIMARY KEY,
            energy INTEGER NOT NULL DEFAULT 100,
            energy_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            click_level INTEGER NOT NULL DEFAULT 0,
            timer_level INTEGER NOT NULL DEFAULT 0,
            freeze_level INTEGER NOT NULL DEFAULT 0,
            reward_level INTEGER NOT NULL DEFAULT 0
        )
    """)
    cur.execute("INSERT OR IGNORE INTO rcc_progress (user_id) VALUES (?)", (user_id,))
    cur.execute(
        "SELECT energy, energy_updated_at, click_level, timer_level, freeze_level, reward_level "
        "FROM rcc_progress WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()

    # Use UTC-naive timestamps consistently with the TIMESTAMP column and
    # with existing rows written by the app on Render (UTC).
    now = datetime.utcnow()
    timestamp_was_invalid = False
    try:
        raw_updated_at = row["energy_updated_at"]
        if isinstance(raw_updated_at, datetime):
            updated_at = raw_updated_at
        else:
            raw_value = str(raw_updated_at or "").strip()
            if raw_value.endswith("Z"):
                raw_value = raw_value[:-1] + "+00:00"
            updated_at = datetime.fromisoformat(raw_value)
        if updated_at.tzinfo is not None:
            updated_at = updated_at.astimezone(timezone.utc).replace(tzinfo=None)
        elapsed = max(0.0, (now - updated_at).total_seconds())
    except (TypeError, ValueError, KeyError, OverflowError):
        # Repair malformed/missing timestamps so future requests can recover.
        elapsed = 0.0
        updated_at = now
        timestamp_was_invalid = True

    energy = max(0, min(100, int(row["energy"] or 0)))
    recovered = int(elapsed // 60)
    if recovered:
        energy = min(100, energy + recovered)
        updated_at = now - timedelta(seconds=elapsed % 60)

    if recovered or timestamp_was_invalid:
        cur.execute(
            "UPDATE rcc_progress SET energy = ?, energy_updated_at = ? WHERE user_id = ?",
            (energy, updated_at.isoformat(), user_id),
        )

    click_level = int(row["click_level"] or 0)
    timer_level = int(row["timer_level"] or 0)
    freeze_level = int(row["freeze_level"] or 0)
    reward_level = int(row["reward_level"] or 0)
    energy_recovery_in = max(1, 60 - int(elapsed % 60))
    return {
        "energy": energy,
        "energy_max": 100,
        "energy_recovery_in": energy_recovery_in,
        "click_level": click_level,
        "timer_level": timer_level,
        "freeze_level": freeze_level,
        "reward_level": reward_level,
        "click_amount": round(RCC_CLICK_AMOUNT * (1 + click_level), 8),
        "game_duration": 25 + timer_level * 5,
        "freeze_bonus": freeze_level,
        "reward_multiplier": round(1 + reward_level * 0.25, 2),
    }


def _rcc_upgrade_costs(progress: dict) -> dict:
    result = {}
    for key, config in RCC_UPGRADE_CONFIG.items():
        level = int(progress[f"{key}_level"])
        result[key] = None if level >= config["max_level"] else config["base_cost"] * (level + 1) ** 2
    return result


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
    progress = _rcc_get_progress(cur, user_id)
    conn.commit()
    conn.close()
    exchange_rate = bot_db.get_lcc_rate()

    return {
        "balance": round(balance, 8),
        "lcc_balance": round(balance, 8),
        "total_clicks": clicks,
        **progress,
        "upgrade_costs": _rcc_upgrade_costs(progress),
        "exchange_rate": exchange_rate,
        "lcc_rate": exchange_rate,
        "can_play": can_play,
        "remaining_seconds": remaining,
        "pc_balance": pc_balance,
    }


@app.post("/api/rcc/click")
def rcc_click(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO rcc_balance (user_id) VALUES (%s)", (user_id,))
    progress = _rcc_get_progress(cur, user_id)
    if progress["energy"] <= 0:
        wait = progress["energy_recovery_in"] or 60
        conn.close()
        raise HTTPException(status_code=429, detail=f"Энергия закончилась. Следующая единица через {wait} сек.")
    amount = progress["click_amount"]
    cur.execute(
        "UPDATE rcc_progress SET energy = energy - 1 WHERE user_id = ? AND energy > 0",
        (user_id,),
    )
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=429, detail="Энергия закончилась")
    cur.execute(
        "UPDATE rcc_balance SET balance = balance + ?, total_clicks = total_clicks + 1 WHERE user_id = %s",
        (amount, user_id)
    )
    cur.execute("SELECT balance FROM rcc_balance WHERE user_id = %s", (user_id,))
    new_balance = cur.fetchone()["balance"]
    conn.commit()
    progress["energy"] -= 1
    progress["energy_recovery_in"] = progress["energy_recovery_in"] or 60
    conn.close()
    return {
        "ok": True, "balance": round(new_balance, 8), "reward": amount,
        "energy": progress["energy"], "energy_recovery_in": progress["energy_recovery_in"],
    }


@app.post("/api/rcc/upgrade")
def rcc_upgrade(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    upgrade = str(payload.get("upgrade") or "")
    config = RCC_UPGRADE_CONFIG.get(upgrade)
    if not config:
        raise HTTPException(status_code=400, detail="Неизвестное улучшение")
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rcc_game_sessions (
            user_id BIGINT PRIMARY KEY,
            started_at TIMESTAMP NOT NULL
        )
    """)
    cur.execute("SELECT started_at FROM rcc_game_sessions WHERE user_id = ?", (user_id,))
    active_session = cur.fetchone()
    if active_session:
        try:
            started_at = active_session["started_at"] if isinstance(active_session["started_at"], datetime) else datetime.fromisoformat(str(active_session["started_at"]))
            if (datetime.now() - started_at).total_seconds() <= 90:
                conn.close()
                raise HTTPException(status_code=400, detail="Заверши текущий раунд перед покупкой улучшения")
        except (TypeError, ValueError, KeyError):
            pass
    progress = _rcc_get_progress(cur, user_id)
    level = int(progress[f"{upgrade}_level"])
    if level >= config["max_level"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Это улучшение уже максимального уровня")
    price = config["base_cost"] * (level + 1) ** 2
    if not change_balance(cur, user_id, -price):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Недостаточно LDC. Нужно {price:,} LDC.")
    column = config["column"]
    cur.execute(f"UPDATE rcc_progress SET {column} = {column} + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    balance = require_balance(cur, user_id)
    progress = _rcc_get_progress(cur, user_id)
    conn.commit()
    conn.close()
    return {
        "ok": True, "message": f"Улучшение куплено за {price:,} LDC.",
        "balance": balance, **progress, "upgrade_costs": _rcc_upgrade_costs(progress),
    }


@app.post("/api/rcc/game_start")
def rcc_game_start(payload: dict = Body(default={})):
    """Reserve one LCC mini-game round on the server before the client starts."""
    user_id = get_telegram_user_id(payload.get("initData", ""))
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rcc_game_sessions (
            user_id BIGINT PRIMARY KEY,
            started_at TIMESTAMP NOT NULL
        )
    """)
    cur.execute("INSERT OR IGNORE INTO rcc_balance (user_id) VALUES (%s)", (user_id,))
    progress = _rcc_get_progress(cur, user_id)
    cur.execute("SELECT last_game FROM rcc_balance WHERE user_id = %s FOR UPDATE", (user_id,))
    row = cur.fetchone()
    now = datetime.now()
    if row and row["last_game"]:
        try:
            last_game = row["last_game"] if isinstance(row["last_game"], datetime) else datetime.fromisoformat(str(row["last_game"]))
            remaining = int(RCC_GAME_COOLDOWN - (now - last_game).total_seconds())
        except (TypeError, ValueError):
            remaining = 0
        if remaining > 0:
            conn.close()
            raise HTTPException(status_code=400, detail=f"Мини-игра ещё недоступна: {remaining} сек.")
    # A round is reserved and the cooldown begins at start, not at finish.
    cur.execute(
        "INSERT INTO rcc_game_sessions (user_id, started_at) VALUES (%s, %s) "
        "ON CONFLICT(user_id) DO UPDATE SET started_at = excluded.started_at",
        (user_id, now.isoformat()),
    )
    cur.execute("UPDATE rcc_balance SET last_game = %s WHERE user_id = %s", (now.isoformat(), user_id))
    conn.commit()
    conn.close()
    return {
        "ok": True, "started_at": now.isoformat(),
        "duration_seconds": progress["game_duration"],
        "freeze_bonus": progress["freeze_bonus"],
        "reward_multiplier": progress["reward_multiplier"],
    }


@app.post("/api/rcc/game_finish")
def rcc_game_finish(payload: dict = Body(...)):
    """Завершение мини-игры. Принимает набранное количество RCC."""
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        amount = float(payload.get("amount") or 0)
    except (TypeError, ValueError, OverflowError):
        _log_security_attempt(user_id, "rcc_invalid_game_reward", f"amount={payload.get('amount')!r}")
        raise HTTPException(status_code=400, detail="Некорректная награда")

    if not math.isfinite(amount):
        _log_security_attempt(user_id, "rcc_non_finite_game_reward", f"amount={payload.get('amount')!r}")
        raise HTTPException(status_code=400, detail="Некорректная награда")

    conn = db()
    cur = conn.cursor()

    # The server must see a reserved round and enforce a plausible duration.
    progress = _rcc_get_progress(cur, user_id)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rcc_game_sessions (
            user_id BIGINT PRIMARY KEY,
            started_at TIMESTAMP NOT NULL
        )
    """)
    cur.execute("SELECT started_at FROM rcc_game_sessions WHERE user_id = %s FOR UPDATE", (user_id,))
    session = cur.fetchone()
    if not session:
        conn.close()
        _log_security_attempt(user_id, "rcc_finish_without_session", "No active LCC game session")
        raise HTTPException(status_code=400, detail="Сначала начни раунд кнопкой «Начать»")
    try:
        started_at = session["started_at"] if isinstance(session["started_at"], datetime) else datetime.fromisoformat(str(session["started_at"]))
        elapsed = (datetime.now() - started_at).total_seconds()
    except (TypeError, ValueError):
        elapsed = 0
    duration = progress["game_duration"]
    if elapsed < duration - 5:
        conn.close()
        raise HTTPException(status_code=400, detail="Раунд ещё не завершён")
    if elapsed > duration + 65:
        cur.execute("DELETE FROM rcc_game_sessions WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="Раунд истёк. Запусти мини-игру заново")

    # Claim the session exactly once. The row lock plus conditional DELETE prevents
    # concurrent/replayed finish requests from crediting the same round twice.
    cur.execute(
        "DELETE FROM rcc_game_sessions WHERE user_id = %s AND started_at = %s",
        (user_id, session["started_at"]),
    )
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        _log_security_attempt(user_id, "rcc_duplicate_game_finish", "Session already claimed")
        raise HTTPException(status_code=409, detail="Этот раунд уже был завершён")

    # The reward ceiling scales only with server-owned timer/reward upgrades.
    max_reward = 0.04 * (duration / 25) * progress["reward_multiplier"]
    if amount < 0 or amount > max_reward:
        _log_security_attempt(
            user_id, "rcc_game_reward_out_of_range",
            f"requested={amount}; allowed=0..{max_reward}; elapsed={elapsed:.2f}; duration={duration}",
        )
    amount = max(0.0, min(amount, max_reward))

    cur.execute("INSERT OR IGNORE INTO rcc_balance (user_id) VALUES (%s)", (user_id,))
    cur.execute(
        "UPDATE rcc_balance SET balance = balance + ? WHERE user_id = %s",
        (amount, user_id)
    )
    cur.execute(
        "INSERT INTO rcc_transactions (user_id, amount, type) VALUES (%s, %s, 'game')",
        (user_id, amount)
    )
    conn.commit()
    bot_db.update_lcc_rate("buy", amount)
    cur.execute("SELECT balance FROM rcc_balance WHERE user_id = %s", (user_id,))
    new_balance = cur.fetchone()["balance"]
    conn.close()
    try:
        bot_db.log_action(
            user_id, "game",
            f"LCC мини-игра завершена: elapsed={elapsed:.2f}; requested={payload.get('amount')!r}; "
            f"awarded={amount}; cap={max_reward}",
            amount=0,
        )
    except Exception:
        logger.exception("Не удалось записать аудит LCC-раунда user_id=%s", user_id)
    return {"ok": True, "balance": round(new_balance, 8), "earned": round(amount, 8)}


@app.post("/api/rcc/exchange")
def rcc_exchange_endpoint(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        amount = float(payload.get("amount") or 0)
    except (TypeError, ValueError, OverflowError):
        _log_security_attempt(user_id, "rcc_invalid_exchange_amount", f"amount={payload.get('amount')!r}")
        raise HTTPException(status_code=400, detail="Некорректная сумма LCC")

    if not math.isfinite(amount) or amount <= 0:
        _log_security_attempt(user_id, "rcc_invalid_exchange_amount", f"amount={payload.get('amount')!r}")
        raise HTTPException(status_code=400, detail="Сумма должна быть конечным числом больше нуля")

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT balance FROM rcc_balance WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row or (row["balance"] or 0) < amount:
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно LCC")

    exchange_rate = bot_db.get_lcc_rate()
    pc_amount = int(amount * exchange_rate)
    if pc_amount < 1:
        conn.close()
        raise HTTPException(status_code=400, detail="Минимум для обмена — 1 LDC")

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
    bot_db.update_lcc_rate("sell", amount)
    cur.execute("SELECT balance FROM rcc_balance WHERE user_id = %s", (user_id,))
    new_rcc = cur.fetchone()["balance"]
    pc_balance = require_balance(cur, user_id)
    conn.close()

    return {
        "ok": True,
        "message": f"Обменяно {amount:.8f} LCC → {pc_amount:,} LDC по курсу {exchange_rate:,.2f} LDC/LCC",
        "rcc_balance": round(new_rcc, 8),
        "lcc_balance": round(new_rcc, 8),
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

@app.post("/api/admin/users/clear_blocked")
def admin_clear_blocked(payload: dict = Body(...)):
    """Полностью удаляет из базы игроков, у которых отмечена блокировка бота."""
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE is_banned = 2")
    ids = [r["user_id"] for r in cur.fetchall()]

    deleted = 0
    errors = []
    for uid in ids:
        try:
            _delete_user_full_main(cur, uid)
            deleted += 1
        except Exception as e:
            errors.append(f"{uid}: {e}")
            print(f"[clear_blocked] Ошибка удаления {uid}: {e}")

    conn.commit()
    conn.close()

    msg = f"Удалено {deleted} из {len(ids)}"
    if errors:
        msg += f". Ошибки: {len(errors)}"
        print(f"[clear_blocked] Ошибки: {errors}")

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
    """Удаляет пользовательские данные, сохраняя журналы действий и безопасности."""
    def safe_query(query, params=(), fetch=False, context=""):
        table_match = re.search(r"\b(?:DELETE\s+FROM|FROM)\s+([a-zA-Z_][a-zA-Z0-9_]*)", query, re.I)
        if table_match:
            table = table_match.group(1).lower()
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = %s LIMIT 1",
                (table,),
            )
            if not cur.fetchone():
                return None
        try:
            cur.execute(query, params)
            return cur.fetchall() if fetch else None
        except Exception:
            logger.exception("Не удалось очистить пользовательские данные context=%s user_id=%s", context, user_id)
            return None

    related_deletes = [
        ("pet_care", "pet_id IN (SELECT id FROM pets WHERE user_id = %s)", (user_id,)),
        ("ttt_games", "(player_x = %s OR player_o = %s)", (user_id, user_id)),
        ("ttt_invites", "(from_user = %s OR to_user = %s)", (user_id, user_id)),
        ("gifts", "(from_user = %s OR to_user = %s)", (user_id, user_id)),
    ]
    for table, condition, params in related_deletes:
        safe_query(f"DELETE FROM {table} WHERE {condition}", params, context=table)

    tables_with_user_id = [
        "purchases", "warns", "game_stats", "new_members",
        "pets", "professions", "profession_progress", "daily_tasks", "promo_activations",
        "user_cosmetics", "user_badges", "user_cosmetics_owned", "user_settings", "islands",
        "rcc_balance", "rcc_transactions", "rcc_progress", "rcc_game_sessions",
        "wheel_tickets", "wheel_ticket_weekly_purchases", "game_play_limits",
        "daily_transfer_usage", "inventory_items", "minesweeper_games", "battleship_games",
        "season_progress", "season_task_progress", "season_level_claims",
        "achievement_progress", "user_titles", "user_collection_items",
        "world_event_claims", "world_event_participants",
    ]
    for table in tables_with_user_id:
        safe_query(f"DELETE FROM {table} WHERE user_id = %s", (user_id,), context=table)

    safe_query("DELETE FROM friends WHERE friend_id = %s", (user_id,), context="friends.friend_id")
    safe_query("DELETE FROM friend_requests WHERE to_user = %s", (user_id,), context="friend_requests.to_user")
    safe_query("DELETE FROM friend_requests WHERE from_user = %s", (user_id,), context="friend_requests.from_user")

    safe_query("DELETE FROM users WHERE user_id = %s", (user_id,), context="users")


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
            try:
                response_data = r.json()
            except ValueError:
                response_data = {}
            # Telegram Bot API может сообщить ошибку в JSON-ответе;
            # проверяем и HTTP-статус, и поле error_code.
            error_code = response_data.get("error_code")
            if r.status_code == 403 or error_code == 403:
                _mark_user_blocked(chat_id)
                return False
            return r.status_code == 200 and response_data.get("ok", True) is True
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
    transfer_count = None
    if gift_type == "money":
        try:
            amount = int(gift_data_raw)
        except (TypeError, ValueError, OverflowError):
            conn.close()
            _log_security_attempt(user_id, "money_gift_invalid_amount", f"amount={gift_data_raw!r}")
            raise HTTPException(status_code=400, detail="Сумма должна быть целым числом")
        if amount <= 0 or amount > 200_000:
            conn.close()
            _log_security_attempt(user_id, "money_gift_invalid_amount", f"amount={amount}; limit=200000")
            raise HTTPException(status_code=400, detail="Денежный подарок должен быть от 1 до 200 000 LDC.")
        slot_ok, transfer_count = bot_db.reserve_daily_transfer_slot(cur, user_id, daily_limit=5)
        if not slot_ok:
            conn.close()
            _log_security_attempt(user_id, "money_gift_daily_limit", f"target_id={target_id}; amount={amount}; count={transfer_count}")
            raise HTTPException(status_code=429, detail="Лимит исчерпан: максимум 5 денежных переводов LDC в сутки (UTC), включая денежные подарки.")
        if not change_balance(cur, user_id, -amount):
            conn.close()
            raise HTTPException(status_code=400, detail="Недостаточно монет")
        gift_data = json.dumps({"amount": amount})

    elif gift_type == "rcc":
        try:
            amount = float(gift_data_raw)
        except (TypeError, ValueError, OverflowError):
            conn.close()
            _log_security_attempt(user_id, "rcc_gift_invalid_amount", f"amount={gift_data_raw!r}")
            raise HTTPException(status_code=400, detail="Некорректная сумма LCC")
        if not math.isfinite(amount) or amount <= 0:
            conn.close()
            _log_security_attempt(user_id, "rcc_gift_invalid_amount", f"amount={gift_data_raw!r}")
            raise HTTPException(status_code=400, detail="Сумма должна быть конечным числом больше нуля")
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

    # Reserve a shared daily gift slot in the same transaction as the gift.
    # If the quota is exhausted, roll back any pending balance/inventory changes.
    gift_day = datetime.utcnow().date().isoformat()
    cur.execute(
        "INSERT INTO daily_gift_usage (user_id, gift_date, gift_count) "
        "VALUES (%s, %s, 0) ON CONFLICT(user_id, gift_date) DO NOTHING",
        (user_id, gift_day),
    )
    cur.execute(
        "SELECT gift_count FROM daily_gift_usage WHERE user_id = %s AND gift_date = %s FOR UPDATE",
        (user_id, gift_day),
    )
    gift_row = cur.fetchone()
    gift_count = int(gift_row["gift_count"] if gift_row else 0)
    if gift_count >= 5:
        conn.rollback()
        conn.close()
        _log_security_attempt(user_id, "daily_gift_limit", f"target_id={target_id}; type={gift_type}; count={gift_count}")
        raise HTTPException(status_code=429, detail="Лимит подарков исчерпан: максимум 5 успешных отправок в сутки (UTC), общий для бота и веба.")
    cur.execute(
        "UPDATE daily_gift_usage SET gift_count = gift_count + 1 "
        "WHERE user_id = %s AND gift_date = %s AND gift_count < 5",
        (user_id, gift_day),
    )
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=429, detail="Лимит подарков исчерпан: максимум 5 в сутки.")

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

    message = f"Подарок отправлен @{target_username}"
    if transfer_count is not None:
        message += f". Денежных переводов сегодня: {transfer_count}/5 (UTC)"
    return {"ok": True, "message": message}


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

# ========== ГЛОБАЛЬНЫЕ СОБЫТИЯ И СЕЗОНЫ ==========

def _broadcast_world_event(event: dict):
    """Notify registered players about a global event; failures for blocked bots are ignored."""
    try:
        users = bot_db.get_all_users()
    except Exception:
        logger.exception("Не удалось получить список игроков для объявления события")
        return
    message = (
        f"🌍 ГЛОБАЛЬНОЕ СОБЫТИЕ: {event.get('title', 'Событие')}\n\n"
        f"{event.get('description', '')}\n\n"
        f"⏳ Длительность: {max(1, int(event.get('remaining_seconds', 3600)) // 60)} мин.\n"
        f"🎁 Награда за участие: {int(event.get('reward_ldc', 0)):,} LDC и "
        f"{int(event.get('reward_xp', 0))} сезонного опыта.\n\n"
        "Открой Lavender Dream в боте или веб-приложении, чтобы участвовать."
    )
    for user in users:
        uid = user.get("user_id") if isinstance(user, dict) else None
        if not uid or (isinstance(user, dict) and user.get("is_banned")):
            continue
        _safe_bot_send(int(uid), message, reply_markup={"inline_keyboard": [[{"text": "🎁 Забрать награду", "callback_data": "world_event_claim"}]]})
        time.sleep(0.05)


@app.get("/api/world-events/current")
def world_event_current(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    event = world_events.get_current_event()
    if not event:
        return {"active": False, "event": None}
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM world_event_claims WHERE event_id = ? AND user_id = ?",
                (event["id"], user_id))
    claimed = cur.fetchone() is not None
    cur.execute("SELECT contribution FROM world_event_participants WHERE event_id = ? AND user_id = ?",
                (event["id"], user_id))
    participant = cur.fetchone()
    conn.close()
    user_progress = int(participant["contribution"] or 0) if participant else 0
    if event.get("event_type") == "community_goal":
        can_claim = not claimed and user_progress > 0 and int(event.get("progress") or 0) >= int(event.get("goal") or 1)
    else:
        can_claim = not claimed and user_progress >= int(event.get("goal") or 1)
    return {"active": True, "event": event, "claimed": claimed,
            "user_progress": user_progress, "user_goal": int(event.get("goal") or 1),
            "can_claim": can_claim}


@app.post("/api/world-events/claim")
def world_event_claim(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        result = world_events.claim_event(user_id)
        world_events.record_season_activity(user_id, "event", 1)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/seasons/current")
def season_current(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    return world_events.get_season_status(user_id)


@app.post("/api/seasons/claim-task")
def season_claim_task(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    task_key = str(payload.get("task_key") or "").strip()
    if not task_key or len(task_key) > 80:
        raise HTTPException(status_code=400, detail="Некорректное задание")
    try:
        return world_events.claim_season_task(user_id, task_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/world-events")
def admin_world_events(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    _require_admin(user_id)
    return {
        "active": world_events.get_current_event(),
        "settings": world_events.get_settings(),
        "events": world_events.list_events(30),
        "schedules": world_events.list_schedules(30),
        "types": [{"key": key, "title": value[0], "description": value[1]}
                  for key, value in world_events.EVENT_TYPES.items()],
    }


@app.post("/api/admin/world-events/start")
def admin_world_event_start(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    try:
        event = world_events.start_event(
            event_type=str(payload.get("event_type") or ""),
            duration_minutes=int(payload.get("duration_minutes") or 60),
            reward_ldc=int(payload.get("reward_ldc") or 0),
            reward_xp=int(payload.get("reward_xp") or 0),
            title=payload.get("title"), description=payload.get("description"),
            source="manual", created_by=user_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if background_tasks is not None:
        background_tasks.add_task(_broadcast_world_event, event)
    return {"ok": True, "event": event}


@app.post("/api/admin/world-events/end")
def admin_world_event_end(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    event_id = payload.get("event_id")
    try:
        ended = world_events.end_event(int(event_id) if event_id else None)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный ID события")
    if not ended:
        raise HTTPException(status_code=404, detail="Активное событие не найдено")
    return {"ok": True, "message": "Событие завершено"}


@app.post("/api/admin/world-events/settings")
def admin_world_event_settings(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    try:
        settings = world_events.update_settings(
            random_enabled=payload.get("random_enabled"),
            min_gap_minutes=payload.get("min_gap_minutes"),
            default_duration_minutes=payload.get("default_duration_minutes"),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Некорректные настройки события") from exc
    return {"ok": True, "settings": settings}


@app.post("/api/admin/world-events/schedule/cancel")
def admin_world_event_schedule_cancel(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    try:
        schedule_id = int(payload.get("schedule_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный ID расписания")
    if not world_events.cancel_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Ожидающее событие не найдено")
    return {"ok": True}


@app.post("/api/admin/world-events/schedule")
def admin_world_event_schedule(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    try:
        schedule_id = world_events.create_schedule(
            event_type=str(payload.get("event_type") or ""),
            scheduled_at=str(payload.get("scheduled_at") or ""),
            duration_minutes=int(payload.get("duration_minutes") or 60),
            reward_ldc=int(payload.get("reward_ldc") or 500),
            reward_xp=int(payload.get("reward_xp") or 100),
            title=payload.get("title"), description=payload.get("description"),
            created_by=user_id,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "schedule_id": schedule_id}


@app.post("/api/seasons/claim-level")
def season_claim_level(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    try:
        return world_events.claim_season_level(user_id, int(payload.get("level")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/achievements/current")
def achievements_current(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    return world_events.get_achievements(user_id)


@app.post("/api/achievements/claim")
def achievement_claim(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    key = str(payload.get("achievement_key") or "").strip()
    if not key or len(key) > 80:
        raise HTTPException(status_code=400, detail="Некорректное достижение")
    try:
        return world_events.claim_achievement(user_id, key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/achievements/title")
def achievement_set_title(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    title_key = str(payload.get("title_key") or "").strip()
    if not title_key or len(title_key) > 100:
        raise HTTPException(status_code=400, detail="Некорректный титул")
    try:
        return world_events.set_active_title(user_id, title_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/collection/current")
def collection_current(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    return world_events.get_collection(user_id)


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


@app.get("/api/tech/security-logs")
def tech_security_logs(init_data: str = Query(..., alias="initData"),
                       limit: int = Query(50)):
    """Rejected API requests and exceptions; restricted to tech admins."""
    requester_id = get_telegram_user_id(init_data)
    _require_tech_admin(requester_id)
    safe_limit = min(max(limit, 1), 200)
    try:
        events = bot_db.get_security_events(limit=safe_limit)
        action_logs = bot_db.get_action_log(filter_type="security", limit=safe_limit)
    except Exception as exc:
        logger.exception("Не удалось прочитать security events")
        raise HTTPException(status_code=500, detail="Ошибка чтения журнала безопасности") from exc
    logs = list(action_logs)
    for event in events:
        details = (
            f"{event.get('event_type', 'api_event')} | "
            f"{event.get('method', '')} {event.get('path', '')} | "
            f"HTTP {event.get('status_code', 0)} | IP {event.get('ip_address', 'unknown')} | "
            f"{event.get('details', '')}"
        )
        logs.append({
            "id": event.get("id"),
            "user_id": event.get("user_id") or "unknown",
            "action_type": "security",
            "details": details,
            "amount": 0,
            "target_user_id": None,
            "created_at": str(event.get("created_at") or ""),
        })
    logs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"logs": logs[:safe_limit], "types": bot_db.LOG_TYPES}


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
            limit=max(1, min(limit, 200)),
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
        island = bot_db.island_get(target_id)
    except Exception:
        island = {"has_island": False}

    return {
        "user_id": target_id,
        "username": row["username"],
        "balance": row["balance"],
        "purchases": [{"id": p[0], "item_type": p[1], "is_used": bool(p[2])} for p in purchases],
        "pets_count": len(pets),
        "island": island,
    }

@app.get("/")
@app.get("/health")
def health():
    """Render-friendly health check that also verifies the shared PostgreSQL database."""
    conn = None
    try:
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        conn.close()
        return {"status": "ok", "database": "ok"}
    except Exception as exc:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        logger.exception("Health check failed: database unavailable")
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

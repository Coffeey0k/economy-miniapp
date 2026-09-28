"""
Backend для Telegram Mini App бота Paradise Reef.

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
- Админ-панель — управление чужими балансами не должно быть доступно из
  веб-страницы без дополнительной защиты.
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
import os
import random
import db_compat as sqlite3
from datetime import datetime
from urllib.parse import parse_qsl
from db_compat import init_db
init_db()

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ========== НАСТРОЙКИ ==========

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_БОТА")
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
    "ruler": {"name": "👑 Правитель Paradise Reef", "salary": 10000, "cooldown_hours": 12},
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
    "activist": "💬 Активист", "resident": "🌴 Житель Paradise Reef",
    "friendly": "🤍 Дружелюбный", "joker": "😂 Весельчак",
    "favorite": "❤️ Любимчик", "photographer": "📸 Фотограф",
    "musician": "🎵 Музыкант", "night_owl": "🌙 Ночной житель",
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
    """Списывает/начисляет монеты. Возвращает False, если не хватает денег."""
    balance = require_balance(cur, user_id)
    new_balance = balance + delta
    if new_balance < 0:
        return False
    cur.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
    return True


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

    if not change_balance(cur, user_id, -item["price"]):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")

    cur.execute(
        "INSERT INTO purchases (user_id, item_type) VALUES (?, ?)", (user_id, item_type)
    )
    conn.commit()
    conn.close()

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
    is_admin = user_id in ADMIN_IDS

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
        "FROM professions WHERE user_id = ?",
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
        cooldown = info["cooldown_hours"] * 3600
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
    return {"ok": True, "message": f"Заработано {salary} 🪙! Уровень {new_level} ({new_days}/7 дней)"}


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
    cur.execute("SELECT last_wheel_time FROM user_settings WHERE user_id = ?", (user_id,))
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
    if row["last_bonus_time"]:
        delta = (datetime.now() - datetime.fromisoformat(row["last_bonus_time"])).total_seconds()
        if delta < BONUS_COOLDOWN:
            can_claim, remaining = False, int(BONUS_COOLDOWN - delta)
    return {"can_claim": can_claim, "remaining_seconds": remaining}


@app.post("/api/bonus/claim")
def claim_bonus(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT last_bonus_time FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if row["last_bonus_time"]:
        delta = (datetime.now() - datetime.fromisoformat(row["last_bonus_time"])).total_seconds()
        if delta < BONUS_COOLDOWN:
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
    return {"ok": True, "amount": bonus}


# ========== API: ПЕРЕВОДЫ ==========

@app.post("/api/transfer")
def transfer_coins(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    target_username = (payload.get("username") or "").lstrip("@")
    amount = int(payload.get("amount") or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля")

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id FROM users WHERE username = ? AND user_id != ?",
        (target_username, user_id),
    )
    target = cur.fetchone()
    if not target:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь с таким username не найден")

    if not change_balance(cur, user_id, -amount):
        conn.close()
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    change_balance(cur, target["user_id"], amount)
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"Переведено {amount} 🪙 пользователю @{target_username}"}

# ========== API: ИГРЫ ==========

SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]


@app.post("/api/games/play")
def play_game(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    game = payload.get("game")
    bet = int(payload.get("bet") or 0)
    choice = payload.get("choice")
    if bet <= 0:
        raise HTTPException(status_code=400, detail="Ставка должна быть больше нуля")

    conn = db()
    cur = conn.cursor()
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
        raise HTTPException(status_code=404, detail="Неизвестная игра")

    if win > 0:
        change_balance(cur, user_id, bet + win)
        text += f"\n✅ Выигрыш: {win} 🪙"
    else:
        text += f"\n❌ Проигрыш: {bet} 🪙"

    conn.commit()
    conn.close()
    return {"ok": True, "win": win, "text": text}


# ========== API: НАСТРОЙКИ ==========

ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]


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
        "is_admin": user_id in ADMIN_IDS,
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
    if user_id not in ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Нет доступа")


@app.post("/api/admin/give")
def admin_give(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    _require_admin(user_id)
    username = (payload.get("username") or "").lstrip("@")
    amount = int(payload.get("amount") or 0)
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
    amount = int(payload.get("amount") or 0)
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

import time


@app.get("/api/admin/ping")
def admin_ping(init_data: str = Query(..., alias="initData")):
    user_id = get_telegram_user_id(init_data)
    _require_admin(user_id)

    import httpx
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
    bet = int(payload.get("bet") or MS_BET)
    if bet <= 0:
        raise HTTPException(status_code=400, detail="Ставка должна быть больше нуля")

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
        "board": board_str,
        "revealed": revealed_str,
        "size": size,
        "mines": MS_MINES,
        "bet": bet,
        "reward": MS_REWARD,
    }


@app.post("/api/minesweeper/open")
def ms_open_api(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    game_id = int(payload.get("game_id"))
    cell = int(payload.get("cell"))

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
        conn.commit()
        conn.close()
        return {
            "ok": True, "result": "boom",
            "board": "".join(board), "revealed": revealed_str,
            "balance": require_balance(cur, user_id) if False else None,
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
    else:
        cur.execute("UPDATE minesweeper_games SET revealed = ? WHERE id = ?",
                    (revealed_str, game_id))

    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance = cur.fetchone()["balance"]
    conn.commit()
    conn.close()

    return {
        "ok": True,
        "result": "win" if win else "safe",
        "board": "".join(board),
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
    bet = int(payload.get("bet") or 500)

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
        "bot_field": bot_field,
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
        "bot_field": row["bot_field"],
        "player_shots": row["player_shots"],
        "bot_shots": row["bot_shots"],
        "turn": row["turn"],
        "status": row["status"],
        "balance": balance,
    }


@app.post("/api/battleship/fire")
def bs_fire(payload: dict = Body(...)):
    user_id = get_telegram_user_id(payload.get("initData", ""))
    game_id = int(payload.get("game_id"))
    row = int(payload.get("row"))
    col = int(payload.get("col"))

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
    balance = require_balance(cur, user_id)
    conn.close()

    return {
        "ok": True,
        "result": result,
        "bot_msg": bot_msg,
        "game_id": game_id,
        "bet": final["bet"],
        "reward": final["bet"] * 2,
        "player_field": final["player_field"],
        "bot_field": final["bot_field"],
        "player_shots": final["player_shots"],
        "bot_shots": final["bot_shots"],
        "turn": final["turn"],
        "status": final["status"],
        "balance": balance,
    }

# ========== API: ДРУЗЬЯ ==========

FRIEND_BONUS_PER = 2
FRIEND_BONUS_MAX = 20


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
    cur.execute("SELECT user_id FROM users WHERE LOWER(username) = %s", (username,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    target_id = row["user_id"]
    if target_id == user_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Нельзя добавить себя")

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
    "reef":     {"name": "🏝️ Paradise Reef", "price": 0},
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
    cur.execute("INSERT INTO user_cosmetics (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    cur.execute(f"UPDATE user_cosmetics SET {col} = %s WHERE user_id = %s", (item_key, user_id))
    conn.commit()
    conn.close()
    return {"ok":} True

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
            
@app.get("/")
def health():
    return {"status": "ok"}

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
    # "ruler" сюда не включаем — по правилам бота, эта профессия только для админов
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
    if row and row["profession"]:
        salary = PROFESSION_INFO[row["profession"]]["salary"] + (row["level"] - 1) * 50
        current = {
            "profession": row["profession"],
            "name": PROFESSION_INFO[row["profession"]]["name"],
            "level": row["level"],
            "salary": salary,
            "days_worked": row["days_worked"],
        }

    all_professions = [
        {"key": key, "name": p["name"], "salary": p["salary"], "cooldown_hours": p["cooldown_hours"]}
        for key, p in PROFESSION_INFO.items()
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

    info = PROFESSION_INFO[row["profession"]]
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


@app.get("/")
def health():
    return {"status": "ok"}

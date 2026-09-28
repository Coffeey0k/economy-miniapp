import db_compat as sqlite3
import random
from typing import Optional, List, Tuple
from datetime import datetime, timedelta

DB_NAME = "paradise.db"


def init_db():

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    # === КАТАЛОГ (настраиваемый) ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS catalog_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT UNIQUE,
            name TEXT,
            price INTEGER,
            description TEXT,
            is_hidden BOOLEAN DEFAULT 0
        )
    """)
    # Инициализация начальными товарами
    cur.execute("SELECT COUNT(*) FROM catalog_items")
    if cur.fetchone()[0] == 0:
        default_items = [
            ("change_role", "🔁 Смена роли", 1000, "Сменить роль в чате"),
            ("anti_warn", "🛡️ Антиварн", 5000, "Снимает 1 предупреждение"),
            ("immunity", "🛡️ Иммунитет на чистке", 10000, "Защита от чистки"),
            ("video", "📹 Видео с вами", 1000, "Попасть в видео"),
            ("unban", "🔓 Разбан", 200000, "Разбан (с одобрения владельца)"),
            ("admin", "👑 Админка", 50000, "Роль админа (с одобрения)"),
        ]
        for it in default_items:
            cur.execute(
                "INSERT INTO catalog_items (item_type, name, price, description) VALUES (?, ?, ?, ?)",
                it
            )
            
    # Пользователи
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            is_banned BOOLEAN DEFAULT 0,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Покупки
    cur.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_type TEXT,
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_used BOOLEAN DEFAULT 0
        )
    """)

    # Варны
    cur.execute("""
        CREATE TABLE IF NOT EXISTS warns (
            user_id INTEGER PRIMARY KEY,
            warn_count INTEGER DEFAULT 0
        )
    """)

    # Статистика игр
    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_stats (
            user_id INTEGER PRIMARY KEY,
            games_played INTEGER DEFAULT 0,
            games_won INTEGER DEFAULT 0,
            total_earned INTEGER DEFAULT 0,
            total_lost INTEGER DEFAULT 0
        )
    """)

    # Новые участники
    cur.execute("""
        CREATE TABLE IF NOT EXISTS new_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === ПИТОМЦЫ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            pet_name TEXT,
            pet_emoji TEXT,
            rarity TEXT,
            income INTEGER,
            is_active BOOLEAN DEFAULT 1,
            obtained_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === ПРОФЕССИИ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS professions (
            user_id INTEGER PRIMARY KEY,
            profession TEXT,
            level INTEGER DEFAULT 1,
            last_work TIMESTAMP,
            days_worked INTEGER DEFAULT 0,
            last_work_day TEXT,
            total_changes INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS profession_progress (
            user_id INTEGER,
            profession TEXT,
            level INTEGER DEFAULT 1,
            days_worked INTEGER DEFAULT 0,
            last_work TIMESTAMP,
            PRIMARY KEY (user_id, profession)
        )
    """)
    
    # === ЕЖЕДНЕВНЫЕ ЗАДАНИЯ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_type TEXT,
            progress INTEGER DEFAULT 0,
            target INTEGER,
            reward INTEGER,
            is_done BOOLEAN DEFAULT 0,
            is_claimed BOOLEAN DEFAULT 0,
            assigned_date TEXT
        )
    """)

    # === ПРОМОКОДЫ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS promocodes (
            code TEXT PRIMARY KEY,
            reward_type TEXT,
            reward_value TEXT,
            max_uses INTEGER DEFAULT 1,
            used_count INTEGER DEFAULT 0,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Активации промокодов (кто и когда активировал)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_activations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            user_id INTEGER,
            activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === КОЛЕСО ФОРТУНЫ (настройки) ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wheel_sectors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT,
            reward_type TEXT,
            reward_value TEXT,
            chance REAL
        )
    """)

    # === ТУТОРИАЛ / ПОДСКАЗКИ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            tutorial_done BOOLEAN DEFAULT 0,
            hints_enabled BOOLEAN DEFAULT 1
        )
    """)

# Миграция: last_wheel_time
    cur.execute("PRAGMA table_info(user_settings)")
    columns = [col[1] for col in cur.fetchall()]
    if "last_wheel_time" not in columns:
        cur.execute("ALTER TABLE user_settings ADD COLUMN last_wheel_time TIMESTAMP")

    # === ПРОКЛЯТОЕ ЯЙЦО ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cursed_egg (
            id INTEGER PRIMARY KEY,
            stock INTEGER DEFAULT 5,
            appeared_at TIMESTAMP,
            is_active BOOLEAN DEFAULT 0
        )
    """)

    # Миграция: last_bonus_time
    cur.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cur.fetchall()]
    if "last_bonus_time" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN last_bonus_time TIMESTAMP")

    # Инициализация проклятого яйца
    cur.execute("SELECT COUNT(*) FROM cursed_egg")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO cursed_egg (id, stock, is_active) VALUES (1, 5, 0)")

    # Инициализация секторов колеса
    cur.execute("SELECT COUNT(*) FROM wheel_sectors")
    if cur.fetchone()[0] == 0:
        sectors = [
            ("100 монет", "money", "100", 45.0),
            ("500 монет", "money", "500", 20.0),
            ("1000 монет", "money", "1000", 7.0),
            ("Питомец Хомячок", "pet", "🐹 Хомячок", 10.0),
            ("Тропическое яйцо", "egg", "tropical", 3.0),
            ("Рандомная покупка", "random_purchase", "", 1.0),
        ]
        for s in sectors:
            cur.execute(
                "INSERT INTO wheel_sectors (label, reward_type, reward_value, chance) VALUES (?, ?, ?, ?)",
                s
            )

    # === КРЕСТИКИ-НОЛИКИ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ttt_games (
            id SERIAL PRIMARY KEY,
            player_x BIGINT,
            player_o BIGINT,
            is_vs_bot BOOLEAN DEFAULT FALSE,
            board TEXT DEFAULT '_________',
            turn BIGINT,
            bet BIGINT DEFAULT 0,
            status TEXT DEFAULT 'waiting',
            winner BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ttt_invites (
            id SERIAL PRIMARY KEY,
            from_user BIGINT,
            to_user BIGINT,
            bet BIGINT,
            message_id BIGINT,
            chat_id BIGINT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === САПЁР ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS minesweeper_games (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            field_size INTEGER DEFAULT 8,
            mines_count INTEGER DEFAULT 6,
            board TEXT,
            revealed TEXT,
            bet BIGINT DEFAULT 200,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === МОРСКОЙ БОЙ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS battleship_games (
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
        )
    """)

    # === ДРУЗЬЯ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS friends (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            friend_id BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, friend_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS friend_requests (
            id SERIAL PRIMARY KEY,
            from_user BIGINT,
            to_user BIGINT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(from_user, to_user)
        )
    """)

    # === КОСМЕТИКА ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_cosmetics (
            user_id BIGINT PRIMARY KEY,
            nickname_color TEXT,
            frame TEXT,
            status TEXT,
            theme TEXT DEFAULT 'dark'
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_badges (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            badge TEXT,
            obtained_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, badge)
        )
    """)
    
    conn.commit()
    conn.close()

# === ПОЛЬЗОВАТЕЛИ ===

def get_user(user_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cur.fetchone()
    conn.close()
    if user:
        return {
            "user_id": user[0],
            "username": user[1],
            "balance": user[2],
            "is_banned": bool(user[3]),
            "registered_at": user[4],
        }
    return None


def create_user(user_id: int, username: str = None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
        (user_id, username)
    )
    cur.execute("INSERT OR IGNORE INTO warns (user_id) VALUES (?)", (user_id,))
    cur.execute("INSERT OR IGNORE INTO game_stats (user_id) VALUES (?)", (user_id,))
    cur.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


def update_balance(user_id: int, amount: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    if not result:
        conn.close()
        return False
    new_balance = result[0] + amount
    if new_balance < 0:
        conn.close()
        return False
    cur.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
    conn.commit()
    conn.close()
    return True


def get_balance(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return result[0] if result else 0


def get_top_users(limit: int = 10) -> List[Tuple]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, username, balance FROM users
        WHERE is_banned = 0 ORDER BY balance DESC LIMIT ?
    """, (limit,))
    result = cur.fetchall()
    conn.close()
    return result


def get_user_rank(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) + 1 FROM users
        WHERE balance > (SELECT balance FROM users WHERE user_id = ?)
        AND is_banned = 0
    """, (user_id,))
    rank = cur.fetchone()[0]
    conn.close()
    return rank


def set_user_ban(user_id: int, banned: bool):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (1 if banned else 0, user_id))
    conn.commit()
    conn.close()


def get_all_users() -> List[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, balance, is_banned FROM users")
    result = cur.fetchall()
    conn.close()
    return [
        {"user_id": r[0], "username": r[1], "balance": r[2], "is_banned": bool(r[3])}
        for r in result
    ]


def get_active_users(days: int = 7) -> List[int]:
    """Возвращает ID активных пользователей (тех, кто активен в game_stats или recently)"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id FROM users
        WHERE is_banned = 0 AND user_id IN (
            SELECT user_id FROM game_stats WHERE games_played > 0
        )
    """)
    result = [r[0] for r in cur.fetchall()]
    conn.close()
    return result


# === ПОКУПКИ ===

def get_price(item_type: str) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT price FROM catalog_items WHERE item_type = ?", (item_type,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0


def get_item_name(item_type: str) -> str:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT name FROM catalog_items WHERE item_type = ?", (item_type,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else item_type


def get_all_catalog_items() -> list:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, item_type, name, price, description FROM catalog_items WHERE is_hidden = 0")
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "item_type": r[1], "name": r[2], "price": r[3], "description": r[4]}
        for r in rows
    ]


def add_catalog_item(item_type: str, name: str, price: int, description: str = "") -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO catalog_items (item_type, name, price, description) VALUES (?, ?, ?, ?)",
            (item_type, name, price, description)
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def update_catalog_item(item_type: str, name: str = None, price: int = None, description: str = None) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    fields, params = [], []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if price is not None:
        fields.append("price = ?")
        params.append(price)
    if description is not None:
        fields.append("description = ?")
        params.append(description)
    if not fields:
        conn.close()
        return False
    params.append(item_type)
    cur.execute(f"UPDATE catalog_items SET {', '.join(fields)} WHERE item_type = ?", params)
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_catalog_item(item_type: str) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM catalog_items WHERE item_type = ?", (item_type,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def add_purchase(user_id: int, item_type: str) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    balance = get_balance(user_id)
    price = get_price(item_type)
    if balance < price:
        conn.close()
        return False
    if not update_balance(user_id, -price):
        conn.close()
        return False
    cur.execute(
        "INSERT INTO purchases (user_id, item_type) VALUES (?, ?)",
        (user_id, item_type)
    )
    conn.commit()
    conn.close()
    return True


def get_user_purchases(user_id: int) -> List[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT item_type, purchased_at, is_used FROM purchases
        WHERE user_id = ? ORDER BY purchased_at DESC
    """, (user_id,))
    result = cur.fetchall()
    conn.close()
    return [
        {"item_type": r[0], "purchased_at": r[1], "is_used": bool(r[2])}
        for r in result
    ]


def get_purchases_full(user_id: int):
    """Возвращает полные данные покупок с id (для аннулирования админом)"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, item_type, is_used FROM purchases
        WHERE user_id = ? ORDER BY purchased_at DESC
    """, (user_id,))
    result = cur.fetchall()
    conn.close()
    return result


def delete_purchase(purchase_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM purchases WHERE id = ?", (purchase_id,))
    conn.commit()
    conn.close()


# === ВАРНЫ ===

def get_warn_count(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT warn_count FROM warns WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return result[0] if result else 0


def clear_all_immunities() -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        UPDATE purchases SET is_used = 1
        WHERE item_type = 'immunity' AND is_used = 0
    """)
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected


# === ИГРЫ ===

def update_game_stats(user_id: int, won: bool, amount: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        UPDATE game_stats
        SET games_played = games_played + 1,
            games_won = games_won + ?,
            total_earned = total_earned + ?,
            total_lost = total_lost + ?
        WHERE user_id = ?
    """, (1 if won else 0, amount if won else 0, amount if not won else 0, user_id))
    conn.commit()
    conn.close()


# === СТАТИСТИКА ===

def get_statistics() -> dict:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT SUM(balance) FROM users")
    total_balance = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM purchases")
    total_purchases = cur.fetchone()[0]
    conn.close()
    return {
        "total_users": total_users,
        "total_balance": total_balance,
        "total_purchases": total_purchases,
    }


# === БОНУС ===

def can_claim_bonus(user_id: int) -> Tuple[bool, int]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT last_bonus_time FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False, 0
    last_time = row[0]
    if last_time is None:
        return True, 0
    now = datetime.now()
    delta = now - datetime.fromisoformat(last_time)
    if delta.total_seconds() >= 10800:
        return True, 0
    remaining = 10800 - int(delta.total_seconds())
    return False, remaining


def claim_bonus(user_id: int) -> int:
    bonus = random.randint(15, 100)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    now = datetime.now().isoformat()
    cur.execute(
        "UPDATE users SET balance = balance + ?, last_bonus_time = ? WHERE user_id = ?",
        (bonus, now, user_id)
    )
    conn.commit()
    conn.close()
    return bonus


# === НОВЫЕ УЧАСТНИКИ ===

def add_new_member(user_id: int, username: str = None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id FROM new_members WHERE user_id = ?", (user_id,))
    if cur.fetchone():
        conn.close()
        return
    cur.execute(
        "INSERT INTO new_members (user_id, username) VALUES (?, ?)",
        (user_id, username)
    )
    conn.commit()
    conn.close()


def get_new_members(days: int = 4, exclude_user_id: int = None) -> List[tuple]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    query = "SELECT user_id, username, joined_at FROM new_members WHERE joined_at >= datetime('now', ?)"
    params = [f"-{days} days"]
    if exclude_user_id is not None:
        query += " AND user_id != ?"
        params.append(exclude_user_id)
    query += " ORDER BY joined_at DESC"
    cur.execute(query, params)
    result = cur.fetchall()
    conn.close()
    return result


def get_today_new_members(exclude_user_id: int = None) -> List[tuple]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    query = "SELECT user_id, username, joined_at FROM new_members WHERE date(joined_at) = date('now')"
    params = []
    if exclude_user_id is not None:
        query += " AND user_id != ?"
        params.append(exclude_user_id)
    query += " ORDER BY joined_at DESC"
    cur.execute(query, params)
    result = cur.fetchall()
    conn.close()
    return result


def clear_old_new_members(days: int = 7):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM new_members WHERE joined_at < datetime('now', ?)", (f"-{days} days",))
    conn.commit()
    conn.close()


def delete_new_member(user_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM new_members WHERE user_id = ?", (user_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_all_new_members() -> List[tuple]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, joined_at FROM new_members ORDER BY joined_at DESC")
    result = cur.fetchall()
    conn.close()
    return result


def remove_bot_from_new_members(bot_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM new_members WHERE user_id = ?", (bot_id,))
    conn.commit()
    conn.close()


# === ПИТОМЦЫ ===

PET_EGGS = {
    "ordinary": {
        "price": 1000,
        "name": "Обычное яйцо",
        "pets": [
            ("🐹 Хомячок", "обычный", 100, 70),
            ("🐰 Кролик", "необычный", 150, 15),
            ("🐱 Котёнок", "редкий", 200, 7),
            ("🦊 Лисёнок", "эпический", 250, 6),
            ("🦄 Маленький единорог", "легендарный", 300, 2),
        ]
    },
    "sea": {
        "price": 30000,
        "name": "Морское яйцо",
        "pets": [
            ("🐠 Рыбка", "обычный", 100, 70),
            ("🐙 Осьминожек", "необычный", 150, 15),
            ("🦀 Крабик", "редкий", 200, 7),
            ("🐬 Дельфинёнок", "эпический", 250, 6),
            ("🧜‍♀️ Морской котёнок", "легендарный", 300, 2),
        ]
    },
    "tropical": {
        "price": 60000,
        "name": "Тропическое яйцо",
        "pets": [
            ("🦜 Попугайчик", "обычный", 100, 70),
            ("🦎 Ящерка", "необычный", 150, 15),
            ("🐒 Обезьянка", "редкий", 200, 7),
            ("🦩 Фламинго", "эпический", 250, 6),
            ("🐆 Радужный леопард", "легендарный", 300, 2),
        ]
    },
    "mystic": {
        "price": 150000,
        "name": "Мистическое яйцо",
        "pets": [
            ("🐈 Лунный кот", "обычный", 100, 70),
            ("🦋 Ночная бабочка", "необычный", 150, 15),
            ("🐺 Серебряный волк", "редкий", 200, 7),
            ("🦌 Лунный олень", "эпический", 250, 6),
            ("🐉 Дракон бездны", "легендарный", 300, 2),
        ]
    },
    "legendary": {
        "price": 500000,
        "name": "Легендарное яйцо",
        "pets": [
            ("🦊 Кристальный лис", "обычный", 100, 70),
            ("🐺 Звёздный волк", "необычный", 150, 15),
            ("🦄 Радужный единорог", "редкий", 200, 7),
            ("🐉 Небесный дракон", "эпический", 250, 6),
            ("🌌 Космический феникс", "легендарный", 300, 2),
        ]
    },
    "divine": {
        "price": 1000000,
        "name": "Божественное яйцо",
        "pets": [
            ("🐈‍⬛ Тёмная луна", "обычный", 100, 70),
            ("🦋 Звёздная бабочка", "необычный", 150, 15),
            ("🦌 Небесный олень", "редкий", 200, 7),
            ("🐉 Императорский дракон", "легендарный", 300, 30),
        ]
    },
}

CURSED_EGG_PETS = [
    ("🐈‍⬛ Кот без глаз", "легендарный", 300, 70),
    ("🦇 Кровавая летучая мышь", "легендарный", 300, 15),
    ("🕷️ Чёрная вдова", "легендарный", 300, 7),
    ("🐺 Оборотень", "легендарный", 300, 2),
]


def open_egg(user_id: int, egg_type: str) -> Optional[dict]:
    """Открывает яйцо, списывает монеты, добавляет питомца. Возвращает питомца или None."""
    if egg_type not in PET_EGGS:
        return None
    egg = PET_EGGS[egg_type]
    if not update_balance(user_id, -egg["price"]):
        return None
    # Выбор питомца
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
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, chosen[0], chosen[0].split()[0], chosen[1], chosen[2]))
    conn.commit()
    conn.close()
    return {
        "name": chosen[0],
        "rarity": chosen[1],
        "income": chosen[2],
    }


def open_cursed_egg(user_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT stock, is_active FROM cursed_egg WHERE id = 1")
    row = cur.fetchone()
    if not row or not row[1] or row[0] <= 0:
        conn.close()
        return None
    if not update_balance(user_id, -10000):
        conn.close()
        return None
    pets = CURSED_EGG_PETS
    total = sum(p[3] for p in pets)
    r = random.uniform(0, total)
    upto = 0
    chosen = pets[-1]
    for p in pets:
        if upto + p[3] >= r:
            chosen = p
            break
        upto += p[3]
    cur.execute("""
        INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, chosen[0], chosen[0].split()[0], chosen[1], chosen[2]))
    cur.execute("UPDATE cursed_egg SET stock = stock - 1 WHERE id = 1")
    stock_left = row[0] - 1
    if stock_left <= 0:
        cur.execute("UPDATE cursed_egg SET is_active = 0 WHERE id = 1")
    conn.commit()
    conn.close()
    return {"name": chosen[0], "rarity": chosen[1], "income": chosen[2], "stock_left": stock_left}


def get_user_pets(user_id: int) -> List[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, pet_name, rarity, income, is_active FROM pets WHERE user_id = ?", (user_id,))
    result = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "name": r[1], "rarity": r[2], "income": r[3], "is_active": bool(r[4])}
        for r in result
    ]


def count_active_pets(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pets WHERE user_id = ? AND is_active = 1", (user_id,))
    result = cur.fetchone()[0]
    conn.close()
    return result


def set_pet_active(pet_id: int, user_id: int, active: bool) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if active:
        cur.execute("SELECT COUNT(*) FROM pets WHERE user_id = ? AND is_active = 1", (user_id,))
        if cur.fetchone()[0] >= 2:
            conn.close()
            return False
    cur.execute("UPDATE pets SET is_active = ? WHERE id = ? AND user_id = ?",
                (1 if active else 0, pet_id, user_id))
    conn.commit()
    conn.close()
    return True


def transfer_pet(pet_id: int, from_user: int, to_user: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE pets SET user_id = ?, is_active = 0 WHERE id = ? AND user_id = ?",
                (to_user, pet_id, from_user))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_egg_stock() -> dict:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT stock, is_active FROM cursed_egg WHERE id = 1")
    row = cur.fetchone()
    conn.close()
    if row:
        return {"stock": row[0], "active": bool(row[1])}
    return {"stock": 0, "active": False}


def set_cursed_egg_active(stock: int = 5):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE cursed_egg SET stock = ?, is_active = 1, appeared_at = ? WHERE id = 1",
                (stock, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def deactivate_cursed_egg():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE cursed_egg SET is_active = 0 WHERE id = 1")
    conn.commit()
    conn.close()


# === ПРОФЕССИИ ===

PROFESSIONS = {
    "artist": {"name": "🎨 Художник", "salary": 500, "cooldown_hours": 2, "admin_only": False},
    "musician": {"name": "🎸 Музыкант", "salary": 500, "cooldown_hours": 2, "admin_only": False},
    "photographer": {"name": "📸 Фотограф", "salary": 1000, "cooldown_hours": 3, "admin_only": False},
    "gardener": {"name": "🌿 Садовник", "salary": 1000, "cooldown_hours": 3, "admin_only": False},
    "cook": {"name": "🍳 Повар", "salary": 1000, "cooldown_hours": 3, "admin_only": False},
    "vet": {"name": "🩺 Ветеринар", "salary": 1200, "cooldown_hours": 4, "admin_only": False},
    "programmer": {"name": "💻 Программист", "salary": 1400, "cooldown_hours": 5, "admin_only": False},
    "ruler": {"name": "👑 Правитель Paradise Reef", "salary": 10000, "cooldown_hours": 12, "admin_only": True},
}

HIRE_COST = 1000
FIRE_EXTRA_COST = 1000


def get_profession(user_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT profession, level, last_work, days_worked, total_changes FROM professions WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "profession": row[0],
        "level": row[1],
        "last_work": row[2],
        "days_worked": row[3],
        "total_changes": row[4],
    }


def get_profession_salary(profession: str, level: int) -> int:
    base = PROFESSIONS[profession]["salary"]
    return base + (level - 1) * 50


def hire_profession(user_id: int, profession: str) -> Tuple[bool, str]:
    if profession not in PROFESSIONS:
        return False, "Неизвестная профессия"
    current = get_profession(user_id)
    cost = HIRE_COST
    if current and current["profession"]:
        cost += FIRE_EXTRA_COST * (current["total_changes"] + 1)
    if get_balance(user_id) < cost:
        return False, f"Недостаточно монет. Нужно: {cost} 🪙"
    if not update_balance(user_id, -cost):
        return False, "Ошибка списания"

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    # Если уходим с профессии — сохраняем её прогресс
    if current and current["profession"]:
        cur.execute("""
            INSERT OR REPLACE INTO profession_progress
            (user_id, profession, level, days_worked, last_work)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, current["profession"], current["level"],
              current["days_worked"], current["last_work"]))

    # Если уже работали на этой профессии — восстанавливаем прогресс
    cur.execute("SELECT level, days_worked, last_work FROM profession_progress WHERE user_id = ? AND profession = ?",
                (user_id, profession))
    saved = cur.fetchone()
    level = saved[0] if saved else 1
    days = saved[1] if saved else 0
    last = saved[2] if saved else None

    changes = current["total_changes"] + 1 if current else 0
    cur.execute("""
        INSERT INTO professions (user_id, profession, level, days_worked, last_work, total_changes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            profession = excluded.profession,
            level = excluded.level,
            days_worked = excluded.days_worked,
            last_work = excluded.last_work,
            total_changes = ?
    """, (user_id, profession, level, days, last, changes, changes))
    conn.commit()
    conn.close()
    return True, f"Вы устроились: {PROFESSIONS[profession]['name']} (уровень {level})"


def work_profession(user_id: int) -> Tuple[bool, str]:
    prof = get_profession(user_id)
    if not prof or not prof["profession"]:
        return False, "У вас нет профессии"
    info = PROFESSIONS[prof["profession"]]
    now = datetime.now()
    last = prof["last_work"]
    if last:
        delta = now - datetime.fromisoformat(last)
        cooldown = info["cooldown_hours"] * 3600
        if delta.total_seconds() < cooldown:
            remaining = cooldown - int(delta.total_seconds())
            return False, f"Отдых ещё не закончен. Осталось: {remaining // 3600} ч {(remaining % 3600) // 60} мин"
    base_salary = get_profession_salary(prof["profession"], prof["level"])
    bonus_percent = friend_income_bonus(user_id)
    salary = base_salary + (base_salary * bonus_percent // 100)
    update_balance(user_id, salary)
    today = now.date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT last_work_day FROM professions WHERE user_id = ?", (user_id,))
    last_day_row = cur.fetchone()
    last_day = last_day_row[0] if last_day_row else None
    new_days = prof["days_worked"]
    new_level = prof["level"]
    if last_day != today:
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
    bonus_text = f" (+{bonus_percent}% от друзей)" if bonus_percent > 0 else ""
    return True, f"Вы заработали {salary} 🪙{bonus_text}! (уровень {new_level}, дней: {new_days}/7)"


# === ЕЖЕДНЕВНЫЕ ЗАДАНИЯ ===

DAILY_TASKS = {
    "activist": {"name": "💬 Активист", "target": 30, "reward": 100, "type": "messages"},
    "resident": {"name": "🌴 Житель Paradise Reef", "target": 50, "reward": 150, "type": "messages"},
    "friendly": {"name": "🤍 Дружелюбный", "target": 5, "reward": 200, "type": "replies"},
    "joker": {"name": "😂 Весельчак", "target": 5, "reward": 150, "type": "reactions_received"},
    "favorite": {"name": "❤️ Любимчик", "target": 10, "reward": 200, "type": "reactions_received"},
    "photographer": {"name": "📸 Фотограф", "target": 1, "reward": 50, "type": "photo"},
    "musician": {"name": "🎵 Музыкант", "target": 1, "reward": 50, "type": "audio"},
    "night_owl": {"name": "🌙 Ночной житель", "target": 1, "reward": 250, "type": "night_message"},
}


def get_user_tasks(user_id: int, date: str = None) -> List[dict]:
    if date is None:
        date = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, task_type, progress, target, reward, is_done, is_claimed
        FROM daily_tasks WHERE user_id = ? AND assigned_date = ?
    """, (user_id, date))
    result = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "task_type": r[1], "progress": r[2], "target": r[3],
         "reward": r[4], "is_done": bool(r[5]), "is_claimed": bool(r[6])}
        for r in result
    ]


def assign_daily_tasks(user_id: int):
    date = datetime.now().date().isoformat()
    existing = get_user_tasks(user_id, date)
    if existing:
        return
    task_keys = list(DAILY_TASKS.keys())
    chosen = random.sample(task_keys, 3)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    for key in chosen:
        t = DAILY_TASKS[key]
        cur.execute("""
            INSERT INTO daily_tasks (user_id, task_type, target, reward, assigned_date)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, key, t["target"], t["reward"], date))
    conn.commit()
    conn.close()


def update_task_progress(user_id: int, task_type: str, amount: int = 1):
    """task_type здесь — ключ задания (activist, resident и т.д.)"""
    date = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, progress, target, is_done FROM daily_tasks
        WHERE user_id = ? AND assigned_date = ? AND task_type = ?
    """, (user_id, date, task_type))
    row = cur.fetchone()
    if not row:
        conn.close()
        return
    task_id, progress, target, is_done = row
    if is_done:
        conn.close()
        return
    new_progress = min(progress + amount, target)
    done = new_progress >= target
    cur.execute("""
        UPDATE daily_tasks SET progress = ?, is_done = ? WHERE id = ?
    """, (new_progress, 1 if done else 0, task_id))
    conn.commit()
    conn.close()
    if done:
        reward = DAILY_TASKS[task_type]["reward"]
        update_balance(user_id, reward)
        cur = conn.cursor()
        cur.execute("UPDATE daily_tasks SET is_claimed = 1 WHERE id = ?", (task_id,))
        conn.commit()


def get_tasks_by_type(user_id: int, task_type: str) -> List[dict]:
    date = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, progress, target, is_done FROM daily_tasks
        WHERE user_id = ? AND assigned_date = ? AND task_type = ?
    """, (user_id, date, task_type))
    result = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "progress": r[1], "target": r[2], "is_done": bool(r[3])}
        for r in result
    ]


def reset_daily_tasks():
    """Вызывается в 5 утра — очищает старые и назначает новые"""
    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM daily_tasks WHERE assigned_date != ?", (datetime.now().date().isoformat(),))
    conn.commit()
    conn.close()
    # Назначаем всем пользователям новые
    all_users = get_all_users()
    for u in all_users:
        assign_daily_tasks(u["user_id"])


# === ПРОМОКОДЫ ===

def create_promo(code: str, reward_type: str, reward_value: str, max_uses: int, expires_at: str = None) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO promocodes (code, reward_type, reward_value, max_uses, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (code, reward_type, reward_value, max_uses, expires_at))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def get_promo(code: str) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT code, reward_type, reward_value, max_uses, used_count, expires_at FROM promocodes WHERE code = ?", (code,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "code": row[0],
        "reward_type": row[1],
        "reward_value": row[2],
        "max_uses": row[3],
        "used_count": row[4],
        "expires_at": row[5],
    }


def activate_promo(code: str, user_id: int) -> Tuple[bool, str]:
    promo = get_promo(code)
    if not promo:
        return False, "Промокод не найден"
    if promo["used_count"] >= promo["max_uses"]:
        return False, "Промокод уже использован максимальное количество раз"
    if promo["expires_at"]:
        try:
            exp = datetime.fromisoformat(promo["expires_at"])
            if datetime.now() > exp:
                return False, "Промокод истёк"
        except Exception:
            pass
    # Проверка: не активировал ли уже этот пользователь
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM promo_activations WHERE code = ? AND user_id = ?", (code, user_id))
    if cur.fetchone()[0] > 0:
        conn.close()
        return False, "Вы уже активировали этот промокод"
    # Активация
    if promo["reward_type"] == "money":
        update_balance(user_id, int(promo["reward_value"]))
        reward_text = f"{promo['reward_value']} 🪙"
    elif promo["reward_type"] == "pet":
        pet_name = promo["reward_value"]
        cur.execute("""
            INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, pet_name, pet_name.split()[0], "легендарный", 300))
        reward_text = f"питомец {pet_name}"
    elif promo["reward_type"] == "egg":
        egg_type = promo["reward_value"]
        if egg_type in PET_EGGS:
            egg = PET_EGGS[egg_type]
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
            cur.execute("""
                INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, chosen[0], chosen[0].split()[0], chosen[1], chosen[2]))
            reward_text = f"{egg['name']}: {chosen[0]}"
        else:
            reward_text = "яйцо"
    else:
        reward_text = promo["reward_value"]
    cur.execute("INSERT INTO promo_activations (code, user_id) VALUES (?, ?)", (code, user_id))
    cur.execute("UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()
    return True, reward_text


def delete_promo(code: str) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM promocodes WHERE code = ?", (code,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_all_promos() -> List[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT code, reward_type, reward_value, max_uses, used_count, expires_at FROM promocodes")
    result = cur.fetchall()
    conn.close()
    return [
        {"code": r[0], "reward_type": r[1], "reward_value": r[2],
         "max_uses": r[3], "used_count": r[4], "expires_at": r[5]}
        for r in result
    ]


def update_promo(code: str, reward_type: str = None, reward_value: str = None,
                 max_uses: int = None, expires_at: str = None) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    fields = []
    params = []
    if reward_type:
        fields.append("reward_type = ?")
        params.append(reward_type)
    if reward_value:
        fields.append("reward_value = ?")
        params.append(reward_value)
    if max_uses is not None:
        fields.append("max_uses = ?")
        params.append(max_uses)
    if expires_at is not None:
        fields.append("expires_at = ?")
        params.append(expires_at)
    if not fields:
        conn.close()
        return False
    params.append(code)
    cur.execute(f"UPDATE promocodes SET {', '.join(fields)} WHERE code = ?", params)
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# === КОЛЕСО ФОРТУНЫ ===

def get_wheel_sectors() -> List[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, label, reward_type, reward_value, chance FROM wheel_sectors")
    result = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "label": r[1], "reward_type": r[2], "reward_value": r[3], "chance": r[4]}
        for r in result
    ]


def update_wheel_sector(sector_id: int, label: str = None, reward_type: str = None,
                       reward_value: str = None, chance: float = None) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    fields = []
    params = []
    if label is not None:
        fields.append("label = ?")
        params.append(label)
    if reward_type is not None:
        fields.append("reward_type = ?")
        params.append(reward_type)
    if reward_value is not None:
        fields.append("reward_value = ?")
        params.append(reward_value)
    if chance is not None:
        fields.append("chance = ?")
        params.append(chance)
    if not fields:
        conn.close()
        return False
    params.append(sector_id)
    cur.execute(f"UPDATE wheel_sectors SET {', '.join(fields)} WHERE id = ?", params)
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def add_wheel_sector(label: str, reward_type: str, reward_value: str, chance: float) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO wheel_sectors (label, reward_type, reward_value, chance)
        VALUES (?, ?, ?, ?)
    """, (label, reward_type, reward_value, chance))
    conn.commit()
    conn.close()
    return True


def delete_wheel_sector(sector_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM wheel_sectors WHERE id = ?", (sector_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


# === НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ (ТУТОРИАЛ / ПОДСКАЗКИ) ===

def get_user_settings(user_id: int) -> dict:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT tutorial_done, hints_enabled FROM user_settings WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()
        row = (0, 1)
    conn.close()
    return {"tutorial_done": bool(row[0]), "hints_enabled": bool(row[1])}


def set_tutorial_done(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    cur.execute("UPDATE user_settings SET tutorial_done = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def set_hints_enabled(user_id: int, enabled: bool):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    cur.execute("UPDATE user_settings SET hints_enabled = ? WHERE user_id = ?", (1 if enabled else 0, user_id))
    conn.commit()
    conn.close()

def can_spin_wheel(user_id: int) -> tuple:
    """Проверяет, можно ли крутить колесо. Возвращает (можно, секунд_осталось)"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    cur.execute("SELECT last_wheel_time FROM user_settings WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.commit()
    conn.close()
    if not row or row[0] is None:
        return True, 0
    last = datetime.fromisoformat(row[0])
    delta = (datetime.now() - last).total_seconds()
    if delta >= 10800:  # 3 часа
        return True, 0
    return False, 10800 - int(delta)


def set_wheel_time(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    cur.execute("UPDATE user_settings SET last_wheel_time = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()


# === СУНДУКИ ===

CHEST_REWARDS = [
    ("money", "100", 40),      # 40%
    ("money", "500", 25),      # 25%
    ("money", "1000", 15),     # 15%
    ("money", "5000", 5),      # 5%
    ("pet_random", "", 10),    # 10% - случайный обычный питомец
    ("egg_ordinary", "", 4),   # 4% - обычное яйцо
    ("item_immunity", "", 1),  # 1% - иммунитет
]


def roll_chest_reward() -> dict:
    """Рандомная награда из сундука"""
    total = sum(r[2] for r in CHEST_REWARDS)
    r = random.uniform(0, total)
    upto = 0
    chosen = CHEST_REWARDS[-1]
    for rew in CHEST_REWARDS:
        if upto + rew[2] >= r:
            chosen = rew
            break
        upto += rew[2]
    return {"type": chosen[0], "value": chosen[1]}


def give_chest_reward(user_id: int, reward: dict) -> str:
    """Выдаёт награду, возвращает текст описания"""
    rtype = reward["type"]
    value = reward["value"]

    if rtype == "money":
        amount = int(value)
        update_balance(user_id, amount)
        return f"+{amount} 🪙"

    if rtype == "pet_random":
        # Случайный питомец из обычного яйца
        pets = PET_EGGS["ordinary"]["pets"]
        total = sum(p[3] for p in pets)
        r = random.uniform(0, total)
        upto = 0
        chosen = pets[-1]
        for p in pets:
            if upto + p[3] >= r:
                chosen = p
                break
            upto += p[3]
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income) VALUES (?, ?, ?, ?, ?)",
            (user_id, chosen[0], chosen[0].split()[0], chosen[1], chosen[2])
        )
        conn.commit()
        conn.close()
        return f"Питомец {chosen[0]} ({chosen[1]})"

    if rtype == "egg_ordinary":
        # Питомец из обычного яйца
        return give_chest_reward(user_id, {"type": "pet_random", "value": ""})

    if rtype == "item_immunity":
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO purchases (user_id, item_type) VALUES (?, ?)",
            (user_id, "immunity")
        )
        conn.commit()
        conn.close()
        return "Иммунитет на чистке 🛡️"

    return "что-то неизвестное"

# ==================== КРЕСТИКИ-НОЛИКИ ====================

TTT_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # ряды
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # столбцы
    (0, 4, 8), (2, 4, 6),             # диагонали
]


def ttt_check_winner(board: str) -> str:
    """Возвращает 'X', 'O', 'draw' или '' (если игра идёт)."""
    for a, b, c in TTT_LINES:
        if board[a] != '_' and board[a] == board[b] == board[c]:
            return board[a]
    if '_' not in board:
        return 'draw'
    return ''


def ttt_create_invite(from_user: int, to_user: int, bet: int, message_id: int, chat_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ttt_invites (from_user, to_user, bet, message_id, chat_id)
        VALUES (?, ?, ?, ?, ?)
    """, (from_user, to_user, bet, message_id, chat_id))
    conn.commit()
    invite_id = cur.lastrowid
    conn.close()
    return invite_id


def ttt_get_invite(invite_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, from_user, to_user, bet, status FROM ttt_invites WHERE id = ?", (invite_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "from_user": row[1], "to_user": row[2],
        "bet": row[3], "status": row[4],
    }


def ttt_update_invite_status(invite_id: int, status: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE ttt_invites SET status = ? WHERE id = ?", (status, invite_id))
    conn.commit()
    conn.close()


def ttt_create_game(player_x, player_o, bet, is_vs_bot=False):
    turn = player_x
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ttt_games (player_x, player_o, is_vs_bot, turn, bet, status)
        VALUES (?, ?, ?, ?, ?, 'active')
    """, (player_x, player_o, 1 if is_vs_bot else 0, turn, bet))
    game_id = cur.lastrowid    # <-- сначала lastrowid
    conn.commit()              # <-- потом commit
    conn.close()
    return game_id

def ttt_get_game(game_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, player_x, player_o, is_vs_bot, board, turn, bet, status, winner
        FROM ttt_games WHERE id = ?
    """, (game_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "player_x": row[1], "player_o": row[2],
        "is_vs_bot": bool(row[3]), "board": row[4], "turn": row[5],
        "bet": row[6], "status": row[7], "winner": row[8],
    }


def ttt_make_move(game_id: int, cell: int, player_id: int) -> tuple:
    """Возвращает (успех, сообщение)."""
    game = ttt_get_game(game_id)
    if not game or game["status"] != "active":
        return False, "Игра неактивна"
    if game["turn"] != player_id:
        return False, "Не ваш ход"

    board = list(game["board"])
    if board[cell] != "_":
        return False, "Клетка занята"

    symbol = "X" if player_id == game["player_x"] else "O"
    board[cell] = symbol
    new_board = "".join(board)

    winner = ttt_check_winner(new_board)

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    if winner == "draw":
        cur.execute("UPDATE ttt_games SET board = ?, status = 'draw' WHERE id = ?", (new_board, game_id))
    elif winner:
        winner_id = game["player_x"] if winner == "X" else game["player_o"]
        cur.execute("UPDATE ttt_games SET board = ?, status = 'finished', winner = ? WHERE id = ?",
                    (new_board, winner_id, game_id))
    else:
        # Передаём ход
        if game["is_vs_bot"]:
            next_turn = game["player_x"]  # для бота ход делает сам бот
        else:
            next_turn = game["player_o"] if player_id == game["player_x"] else game["player_x"]
        cur.execute("UPDATE ttt_games SET board = ?, turn = ? WHERE id = ?", (new_board, next_turn, game_id))

    conn.commit()
    conn.close()

    return True, winner or "next"


def ttt_bot_move(game_id: int) -> Optional[int]:
    """Простой бот: выигрывает, если может; блокирует; иначе центр/угол."""
    game = ttt_get_game(game_id)
    if not game:
        return None
    board = list(game["board"])

    def find_win(symbol):
        for a, b, c in TTT_LINES:
            line = [board[a], board[b], board[c]]
            if line.count(symbol) == 2 and line.count("_") == 1:
                return [a, b, c][line.index("_")]
        return None

    # 1. Выиграть
    move = find_win("O")
    if move is not None:
        return move
    # 2. Заблокировать
    move = find_win("X")
    if move is not None:
        return move
    # 3. Центр
    if board[4] == "_":
        return 4
    # 4. Угол
    for i in [0, 2, 6, 8]:
        if board[i] == "_":
            return i
    # 5. Любая
    for i in range(9):
        if board[i] == "_":
            return i
    return None


def ttt_finish_game(game_id: int, winner_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE ttt_games SET status = 'finished', winner = ? WHERE id = ?", (winner_id, game_id))
    conn.commit()
    conn.close()

# ==================== САПЁР ====================

MS_FIELD_SIZE = 8
MS_MINES = 6
MS_BET = 200
MS_REWARD = 400


def ms_create_game(user_id: int, bet: int = MS_BET) -> Optional[int]:
    """Создаёт игру, списывает ставку. Возвращает game_id или None."""
    if get_balance(user_id) < bet:
        return None
    if not update_balance(user_id, -bet):
        return None

    size = MS_FIELD_SIZE
    # Генерируем поле с минами
    cells = size * size
    mine_positions = random.sample(range(cells), MS_MINES)

    # board: 'M' — мина, число 0-8 — сколько мин вокруг
    board = []
    for i in range(cells):
        if i in mine_positions:
            board.append("M")
        else:
            # Считаем мины вокруг
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
    # revealed: '0' — закрыто, '1' — открыто
    revealed_str = "0" * cells

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO minesweeper_games (user_id, board, revealed, bet, status)
        VALUES (?, ?, ?, ?, 'active')
    """, (user_id, board_str, revealed_str, bet))
    game_id = cur.lastrowid
    conn.commit()
    conn.close()
    return game_id


def ms_get_game(game_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, user_id, board, revealed, bet, status, field_size, mines_count
        FROM minesweeper_games WHERE id = ?
    """, (game_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "user_id": row[1], "board": row[2],
        "revealed": row[3], "bet": row[4], "status": row[5],
        "field_size": row[6], "mines_count": row[7],
    }


def ms_open_cell(game_id: int, cell: int, user_id: int) -> tuple:
    """Открывает клетку. Возвращает (ok, result, message). result: 'boom' | 'safe' | 'win'."""
    game = ms_get_game(game_id)
    if not game or game["status"] != "active":
        return False, "error", "Игра неактивна"
    if game["user_id"] != user_id:
        return False, "error", "Не твоя игра"

    size = game["field_size"]
    board = list(game["board"])
    revealed = list(game["revealed"])

    if revealed[cell] == "1":
        return False, "error", "Клетка уже открыта"

    # === Попал на мину ===
    if board[cell] == "M":
        # Открываем все мины
        for i in range(size * size):
            if board[i] == "M":
                revealed[i] = "1"
        revealed_str = "".join(revealed)

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("""
            UPDATE minesweeper_games SET revealed = ?, status = 'lose' WHERE id = ?
        """, (revealed_str, game_id))
        conn.commit()
        conn.close()
        return True, "boom", "💥 Мина! Ты проиграл."

    # === Безопасная клетка ===
    # Авто-раскрытие пустых клеток (у которых 0 мин вокруг)
    to_reveal = [cell]
    while to_reveal:
        c = to_reveal.pop()
        if revealed[c] == "1":
            continue
        revealed[c] = "1"
        # Если 0 мин вокруг — открываем соседей
        if board[c] == "0":
            row, col = divmod(c, size)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = row + dr, col + dc
                    if 0 <= nr < size and 0 <= nc < size:
                        ni = nr * size + nc
                        if revealed[ni] == "0" and board[ni] != "M":
                            to_reveal.append(ni)

    revealed_str = "".join(revealed)

    # === Проверка победы: все не-минные клетки открыты ===
    win = True
    for i in range(size * size):
        if board[i] != "M" and revealed[i] == "0":
            win = False
            break

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if win:
        update_balance(game["user_id"], MS_REWARD)
        cur.execute("""
            UPDATE minesweeper_games SET revealed = ?, status = 'win' WHERE id = ?
        """, (revealed_str, game_id))
    else:
        cur.execute("""
            UPDATE minesweeper_games SET revealed = ? WHERE id = ?
        """, (revealed_str, game_id))
    conn.commit()
    conn.close()

    if win:
        return True, "win", f"🏆 Победа! Ты выиграл {MS_REWARD} 🪙"
    return True, "safe", ""

# ==================== МОРСКОЙ БОЙ ====================

BS_SIZE = 10
BS_BET_MIN = 500
BS_SHIPS = [4, 3, 3, 2, 2, 2, 1, 1, 1, 1]  # 4-палубный, два 3-палубных и т.д.


def bs_generate_field() -> str:
    """Генерирует случайное поле 10x10 с кораблями. Возвращает строку из 100 символов."""
    grid = [["." for _ in range(BS_SIZE)] for _ in range(BS_SIZE)]
    ships = list(BS_SHIPS)
    random.shuffle(ships)

    for size in ships:
        placed = False
        for _ in range(200):  # попытки
            horizontal = random.choice([True, False])
            if horizontal:
                row = random.randint(0, BS_SIZE - 1)
                col = random.randint(0, BS_SIZE - size)
                cells = [(row, col + i) for i in range(size)]
            else:
                row = random.randint(0, BS_SIZE - size)
                col = random.randint(0, BS_SIZE - 1)
                cells = [(row + i, col) for i in range(size)]

            # Проверка: вокруг клеток должно быть пусто (нельзя касаться)
            ok = True
            for (r, c) in cells:
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        nr, nc = r + dr, c + dc
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

            # Ставим корабль
            for (r, c) in cells:
                grid[r][c] = "S"
            placed = True
            break

        if not placed:
            return bs_generate_field()  # перегенерировать

    return "".join("".join(row) for row in grid)


def bs_create_game(user_id: int, bet: int) -> Optional[int]:
    if bet < BS_BET_MIN:
        return None
    if get_balance(user_id) < bet:
        return None
    if not update_balance(user_id, -bet):
        return None

    player_field = bs_generate_field()
    bot_field = bs_generate_field()
    empty = "0" * (BS_SIZE * BS_SIZE)

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO battleship_games
        (user_id, player_field, bot_field, player_shots, bot_shots, bet, turn, status)
        VALUES (?, ?, ?, ?, ?, ?, 'player', 'active')
    """, (user_id, player_field, bot_field, empty, empty, bet))
    game_id = cur.lastrowid
    conn.commit()
    conn.close()
    return game_id


def bs_get_game(game_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, user_id, player_field, bot_field, player_shots, bot_shots,
               bet, status, turn
        FROM battleship_games WHERE id = ?
    """, (game_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "user_id": row[1],
        "player_field": row[2], "bot_field": row[3],
        "player_shots": row[4], "bot_shots": row[5],
        "bet": row[6], "status": row[7], "turn": row[8],
    }


def bs_cell_index(row: int, col: int) -> int:
    return row * BS_SIZE + col


def bs_check_ship_sunk(field: str, shots: str, r: int, c: int) -> bool:
    """Проверяет, убит ли корабль, в который попали в (r, c)."""
    # Находим все клетки корабля, связанные с (r, c)
    visited = set()
    to_check = [(r, c)]
    ship_cells = []
    while to_check:
        cr, cc = to_check.pop()
        if (cr, cc) in visited:
            continue
        idx = bs_cell_index(cr, cc)
        if field[idx] != "S":
            continue
        visited.add((cr, cc))
        ship_cells.append((cr, cc))
        for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE:
                ni = bs_cell_index(nr, nc)
                if field[ni] == "S" and (nr, nc) not in visited:
                    to_check.append((nr, nc))

    # Все ли клетки корабля подбиты?
    for (sr, sc) in ship_cells:
        si = bs_cell_index(sr, sc)
        if shots[si] != "2" and shots[si] != "3":
            return False
    return True


def bs_mark_sunk(field: str, shots: list, r: int, c: int):
    """Помечает все клетки корабля как убитые (3) + окружающие как промах (1)."""
    visited = set()
    to_check = [(r, c)]
    ship_cells = []
    while to_check:
        cr, cc = to_check.pop()
        if (cr, cc) in visited:
            continue
        idx = bs_cell_index(cr, cc)
        if field[idx] != "S":
            continue
        visited.add((cr, cc))
        ship_cells.append((cr, cc))
        for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE:
                ni = bs_cell_index(nr, nc)
                if field[ni] == "S" and (nr, nc) not in visited:
                    to_check.append((nr, nc))

    for (sr, sc) in ship_cells:
        si = bs_cell_index(sr, sc)
        shots[si] = "3"
        # Помечаем вокруг как промах
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                nr, nc = sr + dr, sc + dc
                if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE:
                    ni = bs_cell_index(nr, nc)
                    if shots[ni] == "0":
                        shots[ni] = "1"


def bs_player_shot(game_id: int, row: int, col: int) -> tuple:
    """Ход игрока по полю бота. Возвращает (ok, result, message)."""
    game = bs_get_game(game_id)
    if not game or game["status"] != "active":
        return False, "error", "Игра неактивна"
    if game["turn"] != "player":
        return False, "error", "Не твой ход"

    idx = bs_cell_index(row, col)
    shots = list(game["player_shots"])
    if shots[idx] in ("1", "2", "3"):
        return False, "error", "Уже стрелял сюда"

    bot_field = game["bot_field"]
    if bot_field[idx] == "S":
        shots[idx] = "2"
        # Проверяем — убит ли корабль целиком
        if bs_check_ship_sunk(bot_field, "".join(shots), row, col):
            bs_mark_sunk(bot_field, shots, row, col)
            result = "sunk"
        else:
            result = "hit"
    else:
        shots[idx] = "1"
        result = "miss"

    shots_str = "".join(shots)

    # Проверка победы: все S-клетки поля бота подбиты
    win = all(
        bot_field[i] != "S" or shots[i] == "3"
        for i in range(BS_SIZE * BS_SIZE)
    )

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if win:
        update_balance(game["user_id"], game["bet"] * 2)
        cur.execute("""
            UPDATE battleship_games SET player_shots = ?, status = 'win' WHERE id = ?
        """, (shots_str, game_id))
    else:
        # Передаём ход боту, если промах
        next_turn = "bot" if result == "miss" else "player"
        cur.execute("""
            UPDATE battleship_games SET player_shots = ?, turn = ? WHERE id = ?
        """, (shots_str, next_turn, game_id))
    conn.commit()
    conn.close()

    if win:
        return True, "win", f"🏆 Победа! Ты выиграл {game['bet'] * 2} 🪙"
    return True, result, ""


def bs_bot_move(game_id: int) -> tuple:
    """Ход бота по полю игрока. Возвращает (result, message)."""
    game = bs_get_game(game_id)
    if not game or game["status"] != "active":
        return "error", "Игра неактивна"

    player_field = game["player_field"]
    bot_shots = list(game["bot_shots"])

    # Простой бот: ищет клетки вокруг попаданий, иначе случайно
    candidates = []
    # Ищем уже подбитые, но не убитые клетки
    for i in range(BS_SIZE * BS_SIZE):
        if bot_shots[i] == "2":
            r, c = divmod(i, BS_SIZE)
            for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < BS_SIZE and 0 <= nc < BS_SIZE:
                    ni = bs_cell_index(nr, nc)
                    if bot_shots[ni] == "0":
                        candidates.append((nr, nc))

    if not candidates:
        # Случайно по неоткрытым
        remaining = [i for i in range(BS_SIZE * BS_SIZE) if bot_shots[i] == "0"]
        if not remaining:
            return "error", "Нет доступных клеток"
        idx = random.choice(remaining)
        row, col = divmod(idx, BS_SIZE)
    else:
        row, col = random.choice(candidates)

    idx = bs_cell_index(row, col)

    if player_field[idx] == "S":
        bot_shots[idx] = "2"
        if bs_check_ship_sunk(player_field, "".join(bot_shots), row, col):
            bs_mark_sunk(player_field, bot_shots, row, col)
            result = "sunk"
        else:
            result = "hit"
    else:
        bot_shots[idx] = "1"
        result = "miss"

    shots_str = "".join(bot_shots)

    # Проверка поражения игрока
    lose = all(
        player_field[i] != "S" or bot_shots[i] == "3"
        for i in range(BS_SIZE * BS_SIZE)
    )

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if lose:
        cur.execute("""
            UPDATE battleship_games SET bot_shots = ?, status = 'lose' WHERE id = ?
        """, (shots_str, game_id))
    else:
        # Если бот попал — бот ходит ещё; если промах — ход игроку
        next_turn = "bot" if result in ("hit", "sunk") else "player"
        cur.execute("""
            UPDATE battleship_games SET bot_shots = ?, turn = ? WHERE id = ?
        """, (shots_str, next_turn, game_id))
    conn.commit()
    conn.close()

    if lose:
        return "lose", "💀 Бот уничтожил твой флот. Ты проиграл."
    return result, f"Бот стреляет в ({row + 1}, {col + 1}): {result}"

# ==================== ДРУЗЬЯ ====================

FRIEND_BONUS_PER = 2       # +2% за каждого друга
FRIEND_BONUS_MAX = 20      # максимум +20%


def friend_send_request(from_user: int, to_user: int) -> bool:
    """Отправляет заявку в друзья. Возвращает True, если создана."""
    if from_user == to_user:
        return False

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    # Уже друзья?
    cur.execute("""
        SELECT id FROM friends WHERE
        (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
    """, (from_user, to_user, to_user, from_user))
    if cur.fetchone():
        conn.close()
        return False

    # Уже есть заявка?
    cur.execute("""
        SELECT id FROM friend_requests
        WHERE from_user = ? AND to_user = ? AND status = 'pending'
    """, (from_user, to_user))
    if cur.fetchone():
        conn.close()
        return False

    # Обратная заявка уже есть? Тогда сразу принимаем обоих
    cur.execute("""
        SELECT id FROM friend_requests
        WHERE from_user = ? AND to_user = ? AND status = 'pending'
    """, (to_user, from_user))
    reverse = cur.fetchone()
    if reverse:
        # Принимаем
        cur.execute("UPDATE friend_requests SET status = 'accepted' WHERE id = ?", (reverse[0],))
        cur.execute("INSERT INTO friends (user_id, friend_id) VALUES (?, ?)", (from_user, to_user))
        cur.execute("INSERT INTO friends (user_id, friend_id) VALUES (?, ?)", (to_user, from_user))
        conn.commit()
        conn.close()
        return "mutual"

    # Обычная заявка
    try:
        cur.execute("""
            INSERT INTO friend_requests (from_user, to_user) VALUES (?, ?)
        """, (from_user, to_user))
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def friend_accept(request_id: int, to_user: int) -> bool:
    """Принимает заявку. Возвращает True, если принята."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT from_user, to_user, status FROM friend_requests WHERE id = ?
    """, (request_id,))
    row = cur.fetchone()
    if not row or row[2] != "pending":
        conn.close()
        return False
    if row[1] != to_user:
        conn.close()
        return False

    from_u = row[0]
    to_u = row[1]

    cur.execute("UPDATE friend_requests SET status = 'accepted' WHERE id = ?", (request_id,))
    try:
        cur.execute("INSERT INTO friends (user_id, friend_id) VALUES (?, ?)", (from_u, to_u))
        cur.execute("INSERT INTO friends (user_id, friend_id) VALUES (?, ?)", (to_u, from_u))
    except Exception:
        pass
    conn.commit()
    conn.close()
    return True


def friend_decline(request_id: int, to_user: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT to_user FROM friend_requests WHERE id = ? AND status = 'pending'
    """, (request_id,))
    row = cur.fetchone()
    if not row or row[0] != to_user:
        conn.close()
        return False
    cur.execute("UPDATE friend_requests SET status = 'declined' WHERE id = ?", (request_id,))
    conn.commit()
    conn.close()
    return True


def friend_remove(user_id: int, friend_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM friends WHERE
        (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)
    """, (user_id, friend_id, friend_id, user_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def friend_list(user_id: int) -> List[dict]:
    """Возвращает список друзей (без дублей)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT
            CASE WHEN user_id = ? THEN friend_id ELSE user_id END AS friend
        FROM friends
        WHERE user_id = ? OR friend_id = ?
    """, (user_id, user_id, user_id))
    ids = [r[0] for r in cur.fetchall()]

    result = []
    for fid in ids:
        cur.execute("SELECT user_id, username, balance FROM users WHERE user_id = ?", (fid,))
        row = cur.fetchone()
        if row:
            result.append({
                "user_id": row[0],
                "username": row[1],
                "balance": row[2],
            })
    conn.close()
    return result


def friend_count(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(DISTINCT CASE WHEN user_id = ? THEN friend_id ELSE user_id END)
        FROM friends WHERE user_id = ? OR friend_id = ?
    """, (user_id, user_id, user_id))
    count = cur.fetchone()[0] or 0
    conn.close()
    return count


def friend_income_bonus(user_id: int) -> int:
    """Возвращает процент бонуса к зарплате (макс FRIEND_BONUS_MAX)."""
    count = friend_count(user_id)
    bonus = count * FRIEND_BONUS_PER
    return min(bonus, FRIEND_BONUS_MAX)


def friend_pending_requests(user_id: int) -> List[dict]:
    """Входящие заявки в друзья."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, from_user FROM friend_requests
        WHERE to_user = ? AND status = 'pending'
    """, (user_id,))
    rows = cur.fetchall()
    result = []
    for r in rows:
        cur.execute("SELECT username FROM users WHERE user_id = ?", (r[1],))
        u = cur.fetchone()
        result.append({
            "request_id": r[0],
            "from_user": r[1],
            "from_username": u[0] if u else str(r[1]),
        })
    conn.close()
    return result

# ==================== КОСМЕТИКА ====================

# Палитра цветов для ника (название → hex)
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

# Рамки
FRAMES = {
    "circle":   {"name": "○", "price": 5000,  "rarity": "base", "display": "○ {name} ○"},
    "star":     {"name": "⭐", "price": 10000, "rarity": "base", "display": "⭐ {name} ⭐"},
    "sparkle":  {"name": "✨", "price": 20000, "rarity": "base", "display": "✨ {name} ✨"},
    "spiral":   {"name": "🌀", "price": 30000, "rarity": "base", "display": "🌀 {name} 🌀"},
    "diamond":  {"name": "💎", "price": 100000, "rarity": "rare", "display": "💎 {name} 💎"},
    "crown":    {"name": "👑", "price": 250000, "rarity": "rare", "display": "👑 {name} 👑"},
    "rainbow":  {"name": "🌈", "price": 500000, "rarity": "rare", "display": "🌈 {name} 🌈"},
}

# Статусы
STATUSES = {
    "online":  {"name": "🟢 Онлайн",         "price": 2000},
    "busy":    {"name": "🔴 Занят",           "price": 2000},
    "sleep":   {"name": "😴 Сплю",            "price": 2000},
    "dnd":     {"name": "⛔ Не беспокоить",   "price": 3000},
    "vacation":{"name": "🏖️ В отпуске",       "price": 3000},
    "dream":   {"name": "💭 Мечтаю",          "price": 5000},
}

# Темы
THEMES = {
    "reef":   {"name": "🏝️ Paradise Reef", "price": 0},
    "dark":   {"name": "🌙 Тёмная",        "price": 0},
    "light":  {"name": "☀️ Светлая",       "price": 0},
    "neon":   {"name": "🌈 Неоновая",      "price": 50000},
}

# Значки
BADGES = {
    "champion":     {"emoji": "🏆", "name": "Чемпион"},
    "millionaire":  {"emoji": "💰", "name": "Миллионер"},
    "multi_million":{"emoji": "💎", "name": "Мультимиллионер"},
    "sniper":       {"emoji": "🎯", "name": "Снайпер"},
    "gamer":        {"emoji": "🎮", "name": "Игрок"},
    "collector":    {"emoji": "🐾", "name": "Коллекционер"},
    "egg_hunter":   {"emoji": "🥚", "name": "Яйцелов"},
    "builder":      {"emoji": "🏝️", "name": "Строитель"},
    "social":       {"emoji": "👥", "name": "Душа компании"},
    "veteran":      {"emoji": "⚔️", "name": "Ветеран"},
    "artist":       {"emoji": "🎨", "name": "Художник"},
    "ruler":        {"emoji": "👑", "name": "Правитель"},
}


def get_cosmetics(user_id: int) -> dict:
    """Возвращает косметику пользователя (или дефолт)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT nickname_color, frame, status, theme FROM user_cosmetics WHERE user_id = ?
    """, (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO user_cosmetics (user_id) VALUES (?)", (user_id,))
        conn.commit()
        row = (None, None, None, "reef")
    conn.close()
    return {
        "nickname_color": row[0],
        "frame": row[1],
        "status": row[2],
        "theme": row[3] or "reef",
    }


def set_cosmetics(user_id: int, **fields) -> bool:
    """Обновляет поля косметики."""
    allowed = ["nickname_color", "frame", "status", "theme"]
    sets = []
    params = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            params.append(v)
    if not sets:
        return False
    params.append(user_id)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO user_cosmetics (user_id) VALUES (?)", (user_id,))
    cur.execute(f"UPDATE user_cosmetics SET {', '.join(sets)} WHERE user_id = ?", params)
    conn.commit()
    conn.close()
    return True


def purchase_cosmetics(user_id: int, item_type: str, item_key: str) -> tuple:
    """Покупает косметику. Возвращает (ok, message)."""
    if item_type == "color":
        price = NICK_COLOR_PRICE
        if item_key not in NICK_COLORS:
            return False, "Неизвестный цвет"
    elif item_type == "frame":
        if item_key not in FRAMES:
            return False, "Неизвестная рамка"
        price = FRAMES[item_key]["price"]
    elif item_type == "status":
        if item_key not in STATUSES:
            return False, "Неизвестный статус"
        price = STATUSES[item_key]["price"]
    elif item_type == "theme":
        if item_key not in THEMES:
            return False, "Неизвестная тема"
        price = THEMES[item_key]["price"]
    else:
        return False, "Неизвестный тип"

    if get_balance(user_id) < price:
        return False, f"Недостаточно монет. Нужно: {price} 🪙"

    if not update_balance(user_id, -price):
        return False, "Ошибка списания"

    # Применяем сразу
    if item_type == "color":
        set_cosmetics(user_id, nickname_color=item_key)
    elif item_type == "frame":
        set_cosmetics(user_id, frame=item_key)
    elif item_type == "status":
        set_cosmetics(user_id, status=item_key)
    elif item_type == "theme":
        set_cosmetics(user_id, theme=item_key)

    return True, f"Куплено! Списано {price} 🪙"


def get_display_name(user_id: int) -> str:
    """Возвращает отформатированное имя с косметикой."""
    user = get_user(user_id)
    if not user:
        return f"ID{user_id}"
    uname = user["username"] or str(user_id)
    cosmetics = get_cosmetics(user_id)

    # Рамка
    frame = cosmetics["frame"]
    if frame and frame in FRAMES:
        name = FRAMES[frame]["display"].format(name=uname)
    else:
        name = f"@{uname}"

    # Цвет — эмодзи-индикатор
    color = cosmetics["nickname_color"]
    if color and color in NICK_COLORS:
        # Берём первый символ из имени цвета (эмодзи кружок)
        color_emoji = NICK_COLORS[color]["name"].split()[0]
        name = f"{color_emoji} {name}"

    return name


def add_badge(user_id: int, badge: str) -> bool:
    if badge not in BADGES:
        return False
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("INSERT OR IGNORE INTO user_badges (user_id, badge) VALUES (?, ?)",
                    (user_id, badge))
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def get_badges(user_id: int) -> list:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT badge FROM user_badges WHERE user_id = ?", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]

def check_badges(user_id: int):
    """Проверяет и выдаёт значки по текущему прогрессу."""
    balance = get_balance(user_id)
    if balance >= 1_000_000:
        add_badge(user_id, "millionaire")
    if balance >= 10_000_000:
        add_badge(user_id, "multi_million")

    # Художник — купил цветной ник
    cosmetics = get_cosmetics(user_id)
    if cosmetics["nickname_color"]:
        add_badge(user_id, "artist")

    # Коллекционер — 10 питомцев
    pets = get_user_pets(user_id)
    if len(pets) >= 10:
        add_badge(user_id, "collector")

    # Строитель — всё на острове (когда добавим остров, вернёмся)

    # Душа компании — 20 друзей
    try:
        if friend_count(user_id) >= 20:
            add_badge(user_id, "social")
    except Exception:
        pass

# Инициализация при импорте
init_db()

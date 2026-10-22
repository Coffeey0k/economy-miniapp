import db_compat as sqlite3
import logging
import random
import re
from typing import Optional, List, Tuple
from datetime import datetime, timedelta

DB_NAME = "paradise.db"



logger = logging.getLogger(__name__)

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
    # В PostgreSQL sequence для SERIAL может отстать от уже существующих ID
    # (например, если строки каталога были перенесены с явными id). Синхронизируем
    # её до INSERT-ов, чтобы новый товар не получил уже занятый primary key.
    cur.execute("""
        SELECT setval(
            pg_get_serial_sequence('catalog_items', 'id'),
            COALESCE((SELECT MAX(id) FROM catalog_items), 1),
            EXISTS (SELECT 1 FROM catalog_items)
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

    # Набор билетов колеса доступен в каталоге раз в 7 дней.
    cur.execute(
        "INSERT INTO catalog_items (item_type, name, price, description) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(item_type) DO NOTHING",
        ("wheel_tickets_weekly", "🎡 10 билетов колеса (раз в неделю)", 25000,
         "10 билетов колеса фортуны; повторная покупка доступна через 7 дней"),
    )

    # Бусты по умолчанию. Они добавляются и в уже заполненный каталог.
    for boost in [
        ("boost_bonus_50", "🎁 Буст бонуса −50%", 1500, "Одноразово сокращает текущий или следующий кулдаун бонуса на 50%"),
        ("boost_work_50", "💼 Буст отдыха −50%", 2500, "Одноразово сокращает текущий или следующий кулдаун работы на 50%"),
    ]:
        cur.execute(
            "INSERT INTO catalog_items (item_type, name, price, description) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(item_type) DO NOTHING",
            boost,
        )

    # Пользователи
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Use a 64-bit balance column so large, valid LDC stakes are not capped at 32-bit.
    try:
        cur.execute("ALTER TABLE users ALTER COLUMN balance TYPE BIGINT")
    except Exception:
        # SQLite and already-compatible databases do not support/need this migration.
        pass

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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pet_slot_limits (
            user_id BIGINT PRIMARY KEY,
            slots INTEGER NOT NULL DEFAULT 4
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pet_care (
            pet_id BIGINT PRIMARY KEY,
            energy INTEGER NOT NULL DEFAULT 100,
            mood INTEGER NOT NULL DEFAULT 100,
            health INTEGER NOT NULL DEFAULT 100,
            level INTEGER NOT NULL DEFAULT 1,
            experience INTEGER NOT NULL DEFAULT 0,
            last_care TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inventory_items (
            user_id BIGINT NOT NULL,
            item_key TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, item_key)
        )
    """)

    cur.execute("""
        INSERT INTO pet_care (pet_id)
        SELECT p.id FROM pets p
        LEFT JOIN pet_care c ON c.pet_id = p.id
        WHERE c.pet_id IS NULL
        ON CONFLICT(pet_id) DO NOTHING
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS web_trades (
            id SERIAL PRIMARY KEY,
            from_user BIGINT NOT NULL,
            to_user BIGINT NOT NULL,
            offer_type TEXT NOT NULL,
            offer_key TEXT NOT NULL,
            request_type TEXT NOT NULL,
            request_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pet_trades (
            id SERIAL PRIMARY KEY,
            from_user BIGINT NOT NULL,
            to_user BIGINT NOT NULL,
            from_pet BIGINT NOT NULL,
            to_pet BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wheel_tickets (
            user_id BIGINT PRIMARY KEY,
            tickets INTEGER NOT NULL DEFAULT 0
        )
    """)

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

    # Купленные бусты — каждая покупка даёт одно использование.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_boosts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            boost_type TEXT NOT NULL,
            reduction_percent INTEGER NOT NULL,
            uses_remaining INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_cooldowns (
            user_id BIGINT NOT NULL,
            action_type TEXT NOT NULL,
            cooldown_seconds INTEGER NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, action_type)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_play_limits (
            user_id BIGINT NOT NULL,
            game_type TEXT NOT NULL,
            plays INTEGER NOT NULL DEFAULT 0,
            cooldown_until TIMESTAMP,
            PRIMARY KEY (user_id, game_type)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS halloween_claims (
            user_id BIGINT NOT NULL,
            event_key TEXT NOT NULL,
            claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, event_key)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seasonal_event_announcements (
            event_key TEXT PRIMARY KEY,
            announced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === ГЛОБАЛЬНЫЕ СОБЫТИЯ И СЕЗОНЫ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS world_event_settings (
            id INTEGER PRIMARY KEY,
            active_event_id BIGINT,
            random_enabled BOOLEAN DEFAULT TRUE,
            min_gap_minutes INTEGER DEFAULT 60,
            default_duration_minutes INTEGER DEFAULT 60,
            next_allowed_at TIMESTAMP,
            next_random_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        INSERT INTO world_event_settings (id, random_enabled, min_gap_minutes, default_duration_minutes)
        VALUES (1, TRUE, 60, 60) ON CONFLICT(id) DO NOTHING
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS world_events (
            id SERIAL PRIMARY KEY,
            event_key TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT 'random',
            starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ends_at TIMESTAMP NOT NULL,
            reward_ldc BIGINT NOT NULL DEFAULT 0,
            reward_xp INTEGER NOT NULL DEFAULT 0,
            goal INTEGER NOT NULL DEFAULT 1,
            progress INTEGER NOT NULL DEFAULT 0,
            created_by BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS world_event_claims (
            event_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (event_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS world_event_participants (
            event_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            contribution INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (event_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS world_event_schedules (
            id SERIAL PRIMARY KEY,
            event_type TEXT NOT NULL,
            scheduled_at TIMESTAMP NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 60,
            reward_ldc BIGINT NOT NULL DEFAULT 500,
            reward_xp INTEGER NOT NULL DEFAULT 100,
            title TEXT,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_by BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seasons (
            id SERIAL PRIMARY KEY,
            season_key TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            starts_at TIMESTAMP NOT NULL,
            ends_at TIMESTAMP NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS season_progress (
            season_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            xp BIGINT NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 0,
            claimed_levels TEXT NOT NULL DEFAULT '[]',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (season_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS season_tasks (
            season_id BIGINT NOT NULL,
            task_key TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            task_type TEXT NOT NULL,
            target INTEGER NOT NULL,
            reward_xp INTEGER NOT NULL DEFAULT 0,
            reward_ldc BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (season_id, task_key)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS season_task_progress (
            season_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            task_key TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            completed BOOLEAN NOT NULL DEFAULT FALSE,
            claimed BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (season_id, user_id, task_key)
        )
    """)

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
            bet BIGINT DEFAULT 500,
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

    # === КЛАНЫ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clans (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE,
            emoji TEXT,
            leader_id BIGINT,
            description TEXT,
            bank BIGINT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clan_members (
            user_id BIGINT PRIMARY KEY,
            clan_id INTEGER,
            role TEXT DEFAULT 'member',
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clan_requests (
            id SERIAL PRIMARY KEY,
            clan_id INTEGER,
            user_id BIGINT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(clan_id, user_id)
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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_cosmetics_owned (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            item_type TEXT,
            item_key TEXT,
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, item_type, item_key)
        )
    """)

    # === ОСТРОВ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS islands (
            user_id BIGINT PRIMARY KEY,
            has_island BOOLEAN DEFAULT FALSE,
            has_house BOOLEAN DEFAULT FALSE,
            has_pier BOOLEAN DEFAULT FALSE,
            has_ship BOOLEAN DEFAULT FALSE,
            last_house_income TIMESTAMP,
            last_ship_income TIMESTAMP,
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Additive island expansion tables: existing island ownership and income are untouched.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS island_buildings (
            user_id BIGINT NOT NULL,
            building_key TEXT NOT NULL,
            level INTEGER NOT NULL DEFAULT 1,
            built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, building_key)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS island_inventory (
            user_id BIGINT NOT NULL,
            item_key TEXT NOT NULL,
            quantity BIGINT NOT NULL DEFAULT 0 CHECK (quantity >= 0),
            PRIMARY KEY (user_id, item_key)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS island_progress (
            user_id BIGINT PRIMARY KEY,
            island_level INTEGER NOT NULL DEFAULT 1,
            experience INTEGER NOT NULL DEFAULT 0,
            last_action_at TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS island_contract_claims (
            user_id BIGINT NOT NULL,
            contract_key TEXT NOT NULL,
            claimed_date TEXT NOT NULL,
            PRIMARY KEY (user_id, contract_key, claimed_date)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS island_expeditions (
            user_id BIGINT PRIMARY KEY,
            expedition_key TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            completes_at TIMESTAMP NOT NULL,
            claimed_at TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS island_decorations (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            decoration_key TEXT NOT NULL,
            x_pct DOUBLE PRECISION NOT NULL DEFAULT 50,
            y_pct DOUBLE PRECISION NOT NULL DEFAULT 50,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === МУЗЫКА ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS music_personal (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            url TEXT,
            title TEXT,
            author TEXT,
            platform TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS music_shared (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            url TEXT,
            title TEXT,
            author TEXT,
            platform TEXT,
            status TEXT DEFAULT 'pending',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_by BIGINT,
            reviewed_at TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS music_slots (
            user_id BIGINT PRIMARY KEY,
            extra_slots INTEGER DEFAULT 0
        )
    """)

    # === RCC (ReefCriptoCoin) ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rcc_balance (
            user_id BIGINT PRIMARY KEY,
            balance REAL DEFAULT 0,
            total_clicks INTEGER DEFAULT 0,
            last_game TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lcc_market (
            id INTEGER PRIMARY KEY,
            rate REAL NOT NULL DEFAULT 50500,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute(
        "INSERT INTO lcc_market (id, rate) VALUES (1, 50500) ON CONFLICT(id) DO NOTHING"
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rcc_transactions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            amount REAL,
            type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === ПОДАРКИ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gifts (
            id SERIAL PRIMARY KEY,
            from_user BIGINT,
            to_user BIGINT,
            gift_type TEXT,
            gift_data TEXT,
            is_opened BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP
        )
    """)

    # === ЛОГ ДЕЙСТВИЙ (только для тех.админов) ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS action_log (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            action_type TEXT,
            details TEXT,
            amount BIGINT DEFAULT 0,
            target_user_id BIGINT,
            is_reverted BOOLEAN DEFAULT FALSE,
            reverted_by BIGINT,
            reverted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === ЛИМИТ ПЕРЕВОДОВ: 5 успешных переводов в UTC-день ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_transfer_usage (
            user_id BIGINT NOT NULL,
            transfer_date TEXT NOT NULL,
            transfer_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, transfer_date)
        )
    """)

    # === ЛИМИТ ПОДАРКОВ: 5 успешных отправок в UTC-день, общий для бота и Mini App ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_gift_usage (
            user_id BIGINT NOT NULL,
            gift_date TEXT NOT NULL,
            gift_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, gift_date)
        )
    """)

    # === ПОКУПКА БИЛЕТОВ КОЛЕСА РАЗ В НЕДЕЛЮ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wheel_ticket_weekly_purchases (
            user_id BIGINT PRIMARY KEY,
            purchased_at TIMESTAMP NOT NULL
        )
    """)

    # === АУДИТ ОТКЛОНЁННЫХ API-ЗАПРОСОВ ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS security_events (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            event_type TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            method TEXT,
            path TEXT,
            status_code INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === ТЕХ.АДМИНЫ (флаг тест-режима) ===
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tech_admins (
            user_id BIGINT PRIMARY KEY,
            test_mode BOOLEAN DEFAULT FALSE,
            added_by BIGINT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            # Keep 0=active, 1=admin-banned, 2=blocked the bot distinct.
            "is_banned": int(user[3] or 0),
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
    """Atomically change a balance without allowing concurrent overspending."""
    # Тест-режим: списание игнорируется, начисление идёт как обычно
    if amount < 0 and is_test_mode(user_id):
        return True

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE users SET balance = balance + ? "
            "WHERE user_id = ? AND balance + ? >= 0",
            (amount, user_id, amount),
        )
        updated = cur.rowcount > 0
        if updated:
            conn.commit()
        else:
            conn.rollback()
        conn.close()
        return updated
    except Exception:
        conn.rollback()
        conn.close()
        logger.exception("Не удалось изменить баланс user_id=%s amount=%s", user_id, amount)
        return False


def reserve_daily_transfer_slot(cur, user_id: int, daily_limit: int = 5) -> tuple:
    """Reserve a successful LDC transfer slot inside the caller's transaction."""
    today = datetime.utcnow().date().isoformat()
    cur.execute(
        "INSERT INTO daily_transfer_usage (user_id, transfer_date, transfer_count) "
        "VALUES (?, ?, 0) ON CONFLICT(user_id, transfer_date) DO NOTHING",
        (user_id, today),
    )
    cur.execute(
        "SELECT transfer_count FROM daily_transfer_usage "
        "WHERE user_id = ? AND transfer_date = ? FOR UPDATE",
        (user_id, today),
    )
    row = cur.fetchone()
    count = int(row[0] or 0) if row else 0
    if count >= daily_limit:
        return False, count
    cur.execute(
        "UPDATE daily_transfer_usage SET transfer_count = transfer_count + 1 "
        "WHERE user_id = ? AND transfer_date = ? AND transfer_count < ?",
        (user_id, today, daily_limit),
    )
    if cur.rowcount != 1:
        return False, count
    return True, count + 1


def transfer_coins_limited(sender_id: int, recipient_id: int, amount: int, daily_limit: int = 5) -> tuple:
    """Atomically transfer LDC and enforce one shared daily quota for bot + Mini App.

    Returns (success, reason, successful_transfers_today). The quota increments only
    if both balance updates succeed; concurrent transfers serialize on the user's
    (UTC date) usage row.
    """
    if amount <= 0 or amount > 9_223_372_036_854_775_807 or sender_id == recipient_id:
        return False, "invalid", 0
    if amount > 200_000:
        return False, "max_amount", 0
    today = datetime.utcnow().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO daily_transfer_usage (user_id, transfer_date, transfer_count) "
            "VALUES (?, ?, 0) ON CONFLICT(user_id, transfer_date) DO NOTHING",
            (sender_id, today),
        )
        cur.execute(
            "SELECT transfer_count FROM daily_transfer_usage "
            "WHERE user_id = ? AND transfer_date = ? FOR UPDATE",
            (sender_id, today),
        )
        row = cur.fetchone()
        count = int(row[0] or 0) if row else 0
        if count >= daily_limit:
            conn.rollback()
            conn.close()
            return False, "limit", count

        cur.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
            (amount, sender_id, amount),
        )
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return False, "balance", count

        cur.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, recipient_id),
        )
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return False, "recipient", count

        count += 1
        cur.execute(
            "UPDATE daily_transfer_usage SET transfer_count = ? "
            "WHERE user_id = ? AND transfer_date = ?",
            (count, sender_id, today),
        )
        conn.commit()
        conn.close()
        return True, "ok", count
    except Exception:
        conn.rollback()
        conn.close()
        logger.exception(
            "Transfer transaction failed sender_id=%s recipient_id=%s amount=%s",
            sender_id, recipient_id, amount,
        )
        return False, "error", 0


def reserve_daily_gift_slot(user_id: int, daily_limit: int = 5) -> tuple:
    """Atomically reserve one successful-gift slot shared by bot and Mini App."""
    today = datetime.utcnow().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO daily_gift_usage (user_id, gift_date, gift_count) "
            "VALUES (?, ?, 0) ON CONFLICT(user_id, gift_date) DO NOTHING",
            (user_id, today),
        )
        cur.execute(
            "SELECT gift_count FROM daily_gift_usage WHERE user_id = ? AND gift_date = ? FOR UPDATE",
            (user_id, today),
        )
        row = cur.fetchone()
        count = int(row[0] or 0) if row else 0
        if count >= daily_limit:
            conn.rollback()
            conn.close()
            return False, count
        cur.execute(
            "UPDATE daily_gift_usage SET gift_count = gift_count + 1 "
            "WHERE user_id = ? AND gift_date = ? AND gift_count < ?",
            (user_id, today, daily_limit),
        )
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return False, count
        conn.commit()
        conn.close()
        return True, count + 1
    except Exception:
        conn.rollback()
        conn.close()
        logger.exception("Failed to reserve daily gift slot user_id=%s", user_id)
        return False, 0


def release_daily_gift_slot(user_id: int) -> None:
    """Release a reserved gift slot if the gift operation itself failed."""
    today = datetime.utcnow().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE daily_gift_usage SET gift_count = CASE WHEN gift_count > 0 "
            "THEN gift_count - 1 ELSE 0 END WHERE user_id = ? AND gift_date = ?",
            (user_id, today),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Failed to release daily gift slot user_id=%s", user_id)
    finally:
        conn.close()


def transfer_coins(sender_id: int, recipient_id: int, amount: int) -> bool:
    """Атомарный перевод без комиссии: либо обе стороны обновлены, либо ни одна."""
    if amount <= 0 or sender_id == recipient_id:
        return False
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
        (amount, sender_id, amount),
    )
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, recipient_id))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False
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
        {"user_id": r[0], "username": r[1], "balance": r[2], "is_banned": int(r[3] or 0)}
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
    """Charge and record a purchase in one transaction."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("SELECT price FROM catalog_items WHERE item_type = ?", (item_type,))
        price_row = cur.fetchone()
        if not price_row:
            conn.close()
            return False
        price = price_row[0]

        # Keep the existing test-mode rule: purchases still require sufficient
        # balance, but test-mode users are not charged.
        cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        balance_row = cur.fetchone()
        if not balance_row or balance_row[0] < price:
            conn.rollback()
            conn.close()
            return False

        if not (price > 0 and is_test_mode(user_id)):
            cur.execute(
                "UPDATE users SET balance = balance - ? "
                "WHERE user_id = ? AND balance >= ?",
                (price, user_id, price),
            )
            if cur.rowcount == 0:
                conn.rollback()
                conn.close()
                return False

        cur.execute(
            "INSERT INTO purchases (user_id, item_type) VALUES (?, ?)",
            (user_id, item_type)
        )
        # Если кулдаун уже идёт, буст сокращает оставшееся время сразу.
        # Иначе буст применяется к следующему кулдауну после действия.
        _grant_catalog_boost_cursor(cur, user_id, item_type)
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.rollback()
        conn.close()
        logger.exception("Не удалось оформить покупку user_id=%s item_type=%s", user_id, item_type)
        return False


def parse_boost_item(item_type: str):
    """Разбирает ID вида boost_bonus_50 или boost_work_25."""
    if not isinstance(item_type, str):
        return None
    parts = item_type.split("_")
    if len(parts) != 3 or parts[0] != "boost" or parts[1] not in {"bonus", "work"}:
        return None
    try:
        reduction = int(parts[2])
    except ValueError:
        return None
    if not 1 <= reduction <= 90:
        return None
    return parts[1], reduction


def get_user_cooldown_seconds(user_id: int, action_type: str, default_seconds: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT cooldown_seconds FROM user_cooldowns WHERE user_id = ? AND action_type = ?",
        (user_id, action_type),
    )
    row = cur.fetchone()
    conn.close()
    return int(row[0]) if row else int(default_seconds)


def set_user_cooldown_seconds(user_id: int, action_type: str, seconds: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_cooldowns (user_id, action_type, cooldown_seconds, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(user_id, action_type) DO UPDATE SET "
        "cooldown_seconds = excluded.cooldown_seconds, updated_at = excluded.updated_at",
        (user_id, action_type, int(seconds), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def consume_user_boost(user_id: int, boost_type: str) -> int:
    """Активирует один буст и возвращает процент сокращения (0, если буста нет)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, reduction_percent FROM user_boosts "
        "WHERE user_id = ? AND boost_type = ? AND uses_remaining > 0 "
        "ORDER BY created_at, id LIMIT 1",
        (user_id, boost_type),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return 0
    cur.execute(
        "UPDATE user_boosts SET uses_remaining = uses_remaining - 1 WHERE id = ? AND uses_remaining > 0",
        (row[0],),
    )
    used = cur.rowcount > 0
    conn.commit()
    conn.close()
    return int(row[1]) if used else 0


def _grant_catalog_boost_cursor(cur, user_id: int, item_type: str) -> bool:
    boost = parse_boost_item(item_type)
    if not boost:
        return False
    boost_type, reduction = boost
    action_type = boost_type
    default_cooldown = 10800
    if boost_type == "work":
        cur.execute("SELECT last_work, profession FROM professions WHERE user_id = ?", (user_id,))
        action_row = cur.fetchone()
        profession = action_row[1] if action_row else None
        if profession:
            default_cooldown = int(PROFESSIONS.get(profession, {}).get("cooldown_hours", 3)) * 3600
        last_time = action_row[0] if action_row else None
    else:
        cur.execute("SELECT last_bonus_time FROM users WHERE user_id = ?", (user_id,))
        action_row = cur.fetchone()
        last_time = action_row[0] if action_row else None

    cur.execute(
        "SELECT cooldown_seconds FROM user_cooldowns WHERE user_id = ? AND action_type = ?",
        (user_id, action_type),
    )
    cooldown_row = cur.fetchone()
    cooldown = int(cooldown_row[0]) if cooldown_row else default_cooldown
    if last_time:
        last_dt = datetime.fromisoformat(str(last_time))
        elapsed = max(0, int((datetime.now() - last_dt).total_seconds()))
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            reduced_cooldown = elapsed + remaining * (100 - reduction) // 100
            cur.execute(
                "INSERT INTO user_cooldowns (user_id, action_type, cooldown_seconds, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(user_id, action_type) DO UPDATE SET "
                "cooldown_seconds = excluded.cooldown_seconds, updated_at = excluded.updated_at",
                (user_id, action_type, reduced_cooldown, datetime.now().isoformat()),
            )
            return True

    cur.execute(
        "INSERT INTO user_boosts (user_id, boost_type, reduction_percent, uses_remaining) "
        "VALUES (?, ?, ?, 1)",
        (user_id, boost_type, reduction),
    )
    return True


def grant_catalog_boost(user_id: int, item_type: str):
    """Применяет буст к текущему кулдауну или сохраняет его на следующее действие."""
    if not parse_boost_item(item_type):
        return False
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        ok = _grant_catalog_boost_cursor(cur, user_id, item_type)
        conn.commit()
        conn.close()
        return ok
    except Exception:
        conn.rollback()
        conn.close()
        logger.exception("Не удалось применить буст user_id=%s item_type=%s", user_id, item_type)
        return False


def event_was_announced(event_key: str) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM seasonal_event_announcements WHERE event_key = ?", (event_key,))
    found = cur.fetchone() is not None
    conn.close()
    return found


def mark_event_announced(event_key: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO seasonal_event_announcements (event_key) VALUES (?) "
        "ON CONFLICT(event_key) DO NOTHING",
        (event_key,),
    )
    conn.commit()
    conn.close()


def claim_halloween_reward(user_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO halloween_claims (user_id, event_key) VALUES (?, 'halloween_2026')",
            (user_id,),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.rollback()
        conn.close()
        return False


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

def can_play_game(user_id: int, game_type: str, limit: int = 5) -> tuple:
    """Read-only cooldown check, useful before accepting a multiplayer invitation."""
    now = datetime.now()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO game_play_limits (user_id, game_type, plays, cooldown_until) "
        "VALUES (?, ?, 0, NULL) ON CONFLICT(user_id, game_type) DO NOTHING",
        (user_id, game_type),
    )
    cur.execute(
        "SELECT plays, cooldown_until FROM game_play_limits WHERE user_id = ? AND game_type = ?",
        (user_id, game_type),
    )
    row = cur.fetchone()
    conn.commit()
    conn.close()
    if not row:
        return True, 0
    cooldown_until = row[1]
    if cooldown_until:
        try:
            until = cooldown_until if isinstance(cooldown_until, datetime) else datetime.fromisoformat(str(cooldown_until))
            remaining = int((until - now).total_seconds())
            if remaining > 0:
                return False, remaining
        except (TypeError, ValueError):
            pass
    return True, 0


def register_game_play(user_id: int, game_type: str, limit: int = 5, cooldown_seconds: int = 9000) -> tuple:
    """Atomically register a game play; after five plays, lock that game for 2h30m."""
    now = datetime.now()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO game_play_limits (user_id, game_type, plays, cooldown_until) "
        "VALUES (?, ?, 0, NULL) ON CONFLICT(user_id, game_type) DO NOTHING",
        (user_id, game_type),
    )
    cur.execute(
        "SELECT plays, cooldown_until FROM game_play_limits WHERE user_id = ? AND game_type = ?",
        (user_id, game_type),
    )
    row = cur.fetchone()
    plays = int(row[0] or 0)
    cooldown_until = row[1]
    if cooldown_until:
        try:
            until = cooldown_until if isinstance(cooldown_until, datetime) else datetime.fromisoformat(str(cooldown_until))
        except (TypeError, ValueError):
            until = now
        remaining = int((until - now).total_seconds())
        if remaining > 0:
            conn.close()
            return False, remaining
        plays = 0
        cooldown_until = None
    plays += 1
    if plays >= limit:
        until = now + timedelta(seconds=cooldown_seconds)
        cur.execute(
            "UPDATE game_play_limits SET plays = 0, cooldown_until = ? WHERE user_id = ? AND game_type = ?",
            (until.isoformat(), user_id, game_type),
        )
    else:
        cur.execute(
            "UPDATE game_play_limits SET plays = ?, cooldown_until = NULL WHERE user_id = ? AND game_type = ?",
            (plays, user_id, game_type),
        )
    conn.commit()
    conn.close()
    return True, 0


def get_lcc_rate() -> float:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS lcc_market (id INTEGER PRIMARY KEY, rate REAL NOT NULL DEFAULT 50500, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("INSERT INTO lcc_market (id, rate) VALUES (1, 50500) ON CONFLICT(id) DO NOTHING")
    cur.execute("SELECT rate FROM lcc_market WHERE id = 1")
    row = cur.fetchone()
    conn.commit()
    conn.close()
    return float(row[0] if row and row[0] else 50500.0)


def update_lcc_rate(direction: str, amount: float) -> float:
    """Small supply/demand movement around the initial 50,500 LDC/LCC quote."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS lcc_market (id INTEGER PRIMARY KEY, rate REAL NOT NULL DEFAULT 50500, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("INSERT INTO lcc_market (id, rate) VALUES (1, 50500) ON CONFLICT(id) DO NOTHING")
    cur.execute("SELECT rate FROM lcc_market WHERE id = 1")
    row = cur.fetchone()
    rate = float(row[0] if row and row[0] else 50500.0)
    # Each 1 LCC traded moves the quote by 0.1%, capped at 2% per transaction.
    fraction = min(0.02, max(0.00001, abs(float(amount)) * 0.001))
    if direction == "buy":
        rate *= 1 + fraction
    else:
        rate *= 1 - fraction
    rate = min(1000000.0, max(100.0, rate))
    cur.execute("UPDATE lcc_market SET rate = ?, updated_at = ? WHERE id = 1", (rate, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return rate


def update_game_stats(user_id: int, won: bool, amount: int):
    """Атомарно обновляет статистику, создавая строку для старых пользователей при необходимости."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO game_stats (user_id) VALUES (?)",
        (user_id,)
    )
    cur.execute("""
        UPDATE game_stats
        SET games_played = COALESCE(games_played, 0) + 1,
            games_won = COALESCE(games_won, 0) + ?,
            total_earned = COALESCE(total_earned, 0) + ?,
            total_lost = COALESCE(total_lost, 0) + ?
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
    cooldown = get_user_cooldown_seconds(user_id, "bonus", 10800)
    if delta.total_seconds() >= cooldown:
        return True, 0
    remaining = cooldown - int(delta.total_seconds())
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
    reduction = consume_user_boost(user_id, "bonus")
    cooldown = 10800 * (100 - reduction) // 100 if reduction else 10800
    set_user_cooldown_seconds(user_id, "bonus", cooldown)
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
    """Возвращает новых участников за последние N дней (совместимо с PostgreSQL)."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(sep=" ", timespec="seconds")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    query = "SELECT user_id, username, joined_at FROM new_members WHERE joined_at >= ?"
    params = [cutoff]
    if exclude_user_id is not None:
        query += " AND user_id != ?"
        params.append(exclude_user_id)
    query += " ORDER BY joined_at DESC"
    cur.execute(query, params)
    result = cur.fetchall()
    conn.close()
    return result


def get_today_new_members(exclude_user_id: int = None) -> List[tuple]:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    query = "SELECT user_id, username, joined_at FROM new_members WHERE joined_at >= ? AND joined_at < ?"
    params = [today_start.isoformat(sep=" "), tomorrow_start.isoformat(sep=" ")]
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


def get_pet_slot_limit(user_id: int) -> int:
    """4 слота по умолчанию; максимум 15."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT slots FROM pet_slot_limits WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return max(4, min(15, int(row[0]))) if row else 4


def buy_pet_slot(user_id: int) -> tuple:
    """Покупает слот транзакционно: параллельные нажатия не списывают деньги дважды за один слот."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pet_slot_limits (user_id, slots) VALUES (?, 4) ON CONFLICT(user_id) DO NOTHING",
        (user_id,),
    )
    cur.execute("SELECT slots FROM pet_slot_limits WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    slots = max(4, min(15, int(row[0]))) if row else 4
    if slots >= 15:
        conn.rollback()
        conn.close()
        return False, "У тебя уже максимум — 15 слотов."
    price = 5000 * (slots - 2)
    cur.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
        (price, user_id, price),
    )
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, f"Недостаточно LDC. Следующий слот стоит {price:,}."
    cur.execute("UPDATE pet_slot_limits SET slots = ? WHERE user_id = ? AND slots = ?",
                (slots + 1, user_id, slots))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, "Слоты изменились одновременно. Попробуй ещё раз."
    conn.commit()
    conn.close()
    return True, f"Куплен слот {slots + 1}/15 за {price:,} LDC."


def _add_pet_with_slot_check(user_id: int, pet: tuple) -> bool:
    """Новый питомец активируется только если есть свободный слот."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pets WHERE user_id = ? AND is_active = 1", (user_id,))
    active_count = cur.fetchone()[0]
    slots = get_pet_slot_limit(user_id)
    is_active = 1 if active_count < slots else 0
    cur.execute(
        "INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, pet[0], pet[0].split()[0], pet[1], pet[2], is_active),
    )
    pet_id = cur.lastrowid
    cur.execute(
        "INSERT INTO pet_care (pet_id) VALUES (?) ON CONFLICT(pet_id) DO NOTHING",
        (pet_id,),
    )
    conn.commit()
    conn.close()
    return bool(is_active)


def open_egg(user_id: int, egg_type: str) -> Optional[dict]:
    """Открывает яйцо; питомец сохраняется даже если все активные слоты заняты."""
    if egg_type not in PET_EGGS:
        return None
    egg = PET_EGGS[egg_type]
    if not update_balance(user_id, -egg["price"]):
        return None
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
    activated = _add_pet_with_slot_check(user_id, chosen)
    ticket_bonus = 1 if random.random() < 0.035 else 0
    if ticket_bonus:
        change_wheel_tickets(user_id, ticket_bonus)
    return {"name": chosen[0], "rarity": chosen[1], "income": chosen[2], "is_active": activated, "ticket_bonus": ticket_bonus}


def open_cursed_egg(user_id: int) -> Optional[dict]:
    """Открывает проклятое яйцо одной транзакцией, включая резервирование остатка и списание."""
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

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE cursed_egg SET stock = stock - 1 WHERE id = 1 AND stock > 0 AND is_active = 1")
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return None

    cur.execute("UPDATE users SET balance = balance - 10000 WHERE user_id = ? AND balance >= 10000", (user_id,))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return None

    cur.execute("SELECT slots FROM pet_slot_limits WHERE user_id = ?", (user_id,))
    slot_row = cur.fetchone()
    slots = max(4, min(15, int(slot_row[0]))) if slot_row else 4
    cur.execute("SELECT COUNT(*) FROM pets WHERE user_id = ? AND is_active = 1", (user_id,))
    active_count = cur.fetchone()[0]
    is_active = 1 if active_count < slots else 0

    cur.execute(
        "INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income, is_active) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, chosen[0], chosen[0].split()[0], chosen[1], chosen[2], is_active),
    )
    pet_id = cur.lastrowid
    cur.execute("INSERT INTO pet_care (pet_id) VALUES (?) ON CONFLICT(pet_id) DO NOTHING", (pet_id,))
    cur.execute("SELECT stock FROM cursed_egg WHERE id = 1")
    stock_left = cur.fetchone()[0]
    if stock_left <= 0:
        cur.execute("UPDATE cursed_egg SET is_active = 0 WHERE id = 1")
    conn.commit()
    conn.close()
    return {"name": chosen[0], "rarity": chosen[1], "income": chosen[2],
            "stock_left": stock_left, "is_active": bool(is_active)}


def get_pet_by_id(pet_id: int, user_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, pet_name, rarity, income, is_active FROM pets WHERE id = ? AND user_id = ?",
        (pet_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "rarity": row[2], "income": row[3], "is_active": bool(row[4])}


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
    cur.execute("SELECT is_active FROM pets WHERE id = ? AND user_id = ?", (pet_id, user_id))
    pet = cur.fetchone()
    if not pet:
        conn.close()
        return False
    if active and not pet[0]:
        cur.execute("SELECT COUNT(*) FROM pets WHERE user_id = ? AND is_active = 1", (user_id,))
        if cur.fetchone()[0] >= get_pet_slot_limit(user_id):
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




def create_pet_trade(from_user: int, to_user: int, from_pet: int) -> tuple:
    if from_user == to_user:
        return False, "Нельзя обмениваться с самим собой."
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id FROM pets WHERE id = ? AND user_id = ?", (from_pet, from_user))
    if not cur.fetchone():
        conn.close()
        return False, "Этого питомца у тебя нет."
    cur.execute("SELECT id FROM pets WHERE user_id = ? ORDER BY id", (to_user,))
    target_pets = [int(row[0]) for row in cur.fetchall()]
    if not target_pets:
        conn.close()
        return False, "У игрока нет питомца для обмена."
    # Choose the first pet and include its ID in the offer; recipient can decline if unsuitable.
    to_pet = target_pets[0]
    cur.execute(
        "INSERT INTO pet_trades (from_user, to_user, from_pet, to_pet, status) VALUES (?, ?, ?, ?, 'pending')",
        (from_user, to_user, from_pet, to_pet),
    )
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()
    return True, {"trade_id": trade_id, "to_pet": to_pet}


def resolve_pet_trade(trade_id: int, user_id: int, accept: bool) -> tuple:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT from_user, to_user, from_pet, to_pet, status FROM pet_trades WHERE id = ?", (trade_id,))
    row = cur.fetchone()
    if not row or row[4] != "pending":
        conn.close()
        return False, "Предложение уже недоступно."
    from_user, to_user, from_pet, to_pet, status = row
    if user_id != to_user:
        conn.close()
        return False, "Это предложение не для тебя."
    if not accept:
        cur.execute("UPDATE pet_trades SET status = 'declined', resolved_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'", (trade_id,))
        conn.commit()
        conn.close()
        return True, "Обмен отклонён."
    cur.execute("SELECT id FROM pets WHERE id = ? AND user_id = ?", (from_pet, from_user))
    sender_pet_exists = cur.fetchone() is not None
    cur.execute("SELECT id FROM pets WHERE id = ? AND user_id = ?", (to_pet, to_user))
    receiver_pet_exists = cur.fetchone() is not None
    if not sender_pet_exists or not receiver_pet_exists:
        conn.rollback()
        conn.close()
        return False, "Один из питомцев уже передан или продан."
    cur.execute("UPDATE pets SET user_id = ?, is_active = 0 WHERE id = ? AND user_id = ?", (to_user, from_pet, from_user))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, "Не удалось передать первого питомца."
    cur.execute("UPDATE pets SET user_id = ?, is_active = 0 WHERE id = ? AND user_id = ?", (from_user, to_pet, to_user))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, "Не удалось передать второго питомца."
    cur.execute("UPDATE pet_trades SET status = 'accepted', resolved_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'", (trade_id,))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, "Предложение уже обработано."
    conn.commit()
    conn.close()
    return True, "Обмен завершён! Оба питомца сохранены в коллекциях неактивными."


def get_pet_care(pet_id: int, user_id: int) -> Optional[dict]:
    """Return pet stats after applying time-based needs decay."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pet_care (pet_id) SELECT id FROM pets WHERE id = ? AND user_id = ? "
        "ON CONFLICT(pet_id) DO NOTHING",
        (pet_id, user_id),
    )
    cur.execute(
        "SELECT c.energy, c.mood, c.health, c.level, c.experience, c.last_care "
        "FROM pet_care c JOIN pets p ON p.id = c.pet_id "
        "WHERE c.pet_id = ? AND p.user_id = ?",
        (pet_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    energy, mood, health, level, experience, last_care = row
    now = datetime.now()
    if not last_care:
        cur.execute("UPDATE pet_care SET last_care = ? WHERE pet_id = ?", (now.isoformat(), pet_id))
    else:
        try:
            last = datetime.fromisoformat(last_care)
            elapsed_hours = max(0, int((now - last).total_seconds() // 3600))
        except (TypeError, ValueError):
            elapsed_hours = 0
            last = now
        if elapsed_hours:
            energy = max(0, int(energy) - elapsed_hours * 4)
            mood = max(0, int(mood) - elapsed_hours * 3)
            health = max(0, int(health) - elapsed_hours)
            last = last + timedelta(hours=elapsed_hours)
            cur.execute(
                "UPDATE pet_care SET energy = ?, mood = ?, health = ?, last_care = ? WHERE pet_id = ?",
                (energy, mood, health, last.isoformat(), pet_id),
            )
    conn.commit()
    conn.close()
    return {"energy": int(energy), "mood": int(mood), "health": int(health),
            "level": int(level), "experience": int(experience)}
PET_FOOD_BY_NAME = {
    "кот": "cat_food", "кошка": "cat_food", "котёнок": "cat_food",
    "собака": "dog_food", "щенок": "dog_food",
    "кролик": "rabbit_food", "заяц": "rabbit_food",
    "попугай": "bird_food", "птица": "bird_food",
    "рыб": "fish_food", "дельфин": "fish_food", "осьминог": "fish_food", "краб": "fish_food",
    "черепаха": "reptile_food", "ящериц": "reptile_food",
    "хомяк": "small_pet_food", "хомяч": "small_pet_food",
    "мышь": "small_pet_food", "крыса": "small_pet_food", "морская свинка": "small_pet_food",
}


def pet_food_key(pet_name: str) -> str:
    name = (pet_name or "").casefold()
    for prefix, key in PET_FOOD_BY_NAME.items():
        if prefix in name:
            return key
    return "universal_pet_food"


def get_inventory(user_id: int) -> list:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT item_key, quantity FROM inventory_items WHERE user_id = ? AND quantity > 0 ORDER BY item_key", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [{"item_key": row[0], "quantity": int(row[1])} for row in rows]


def add_inventory_item(user_id: int, item_key: str, quantity: int = 1) -> bool:
    if quantity <= 0:
        return False
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO inventory_items (user_id, item_key, quantity) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, item_key) DO UPDATE SET quantity = quantity + excluded.quantity",
        (user_id, item_key, quantity),
    )
    conn.commit()
    conn.close()
    return True


def use_pet_food(user_id: int, pet_id: int) -> tuple:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT pet_name FROM pets WHERE id = ? AND user_id = ?", (pet_id, user_id))
    pet = cur.fetchone()
    if not pet:
        conn.close()
        return False, "Питомец не найден."
    food_key = pet_food_key(pet[0])
    cur.execute("SELECT quantity FROM inventory_items WHERE user_id = ? AND item_key = ?", (user_id, food_key))
    item = cur.fetchone()
    if not item or int(item[0]) < 1:
        conn.close()
        return False, f"Нет корма: {food_key}. Купи его в магазине."
    cur.execute("UPDATE inventory_items SET quantity = quantity - 1 WHERE user_id = ? AND item_key = ? AND quantity >= 1", (user_id, food_key))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, "Корм уже использован."
    cur.execute("INSERT INTO pet_care (pet_id) VALUES (?) ON CONFLICT(pet_id) DO NOTHING", (pet_id,))
    cur.execute("UPDATE pet_care SET energy = MIN(100, energy + 35), health = MIN(100, health + 5), experience = experience + 5, last_care = ? WHERE pet_id = ?", (datetime.now().isoformat(), pet_id))
    conn.commit()
    conn.close()
    return True, f"Питомец накормлен подходящим кормом (+35 энергии): {food_key}."


def care_for_pet(user_id: int, pet_id: int, action: str) -> tuple:
    """Уход за своим питомцем; списание и обновление состояния выполняются одной транзакцией."""
    # Apply inactivity decay before an action can restore a stat.
    get_pet_care(pet_id, user_id)
    costs = {"feed": 100, "play": 50, "heal": 250, "upgrade": 1000}
    if action not in costs:
        return False, "Неизвестное действие."
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pet_care (pet_id) SELECT id FROM pets WHERE id = ? AND user_id = ? "
        "ON CONFLICT(pet_id) DO NOTHING",
        (pet_id, user_id),
    )
    cur.execute(
        "SELECT p.income, c.energy, c.mood, c.health, c.level, c.experience "
        "FROM pets p JOIN pet_care c ON c.pet_id = p.id "
        "WHERE p.id = ? AND p.user_id = ?",
        (pet_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return False, "Питомец не найден."
    income, energy, mood, health, level, experience = row
    # Для обновления баланса используем тот же connection, чтобы уход не мог быть бесплатным при ошибке.
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
                (costs[action], user_id, costs[action]))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, f"Недостаточно LDC. Нужно {costs[action]:,}."
    if action == "feed":
        energy, health, experience = min(100, energy + 25), min(100, health + 5), experience + 5
        msg = "🍖 Питомец накормлен: энергия +25."
    elif action == "play":
        energy, mood, experience = max(0, energy - 10), min(100, mood + 25), experience + 10
        msg = "🎾 Вы поиграли: настроение +25, энергия −10."
    elif action == "heal":
        health, mood, experience = min(100, health + 40), min(100, mood + 5), experience + 5
        msg = "🩺 Питомец вылечен: здоровье +40."
    else:
        if experience < level * 25:
            conn.rollback()
            conn.close()
            return False, f"Для улучшения нужен опыт: {level * 25} (сейчас {experience})."
        level += 1
        experience -= (level - 1) * 25
        income = int(income * 1.15)
        cur.execute("UPDATE pets SET income = ? WHERE id = ? AND user_id = ?", (income, pet_id, user_id))
        msg = f"⬆️ Уровень питомца повышен до {level}! Доход теперь {income} LDC/4ч."
    cur.execute(
        "UPDATE pet_care SET energy = ?, mood = ?, health = ?, level = ?, experience = ?, last_care = ? WHERE pet_id = ?",
        (energy, mood, health, level, experience, datetime.now().isoformat(), pet_id),
    )
    conn.commit()
    conn.close()
    return True, msg


def sell_pet_to_system(user_id: int, pet_id: int) -> tuple:
    """Продаёт питомца системе за половину базовой цены дохода, не затрагивая чужих питомцев."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT income, is_active FROM pets WHERE id = ? AND user_id = ?", (pet_id, user_id))
    pet = cur.fetchone()
    if not pet:
        conn.close()
        return False, "Питомец не найден."
    price = max(100, int(pet[0]) * 10)
    cur.execute("DELETE FROM pets WHERE id = ? AND user_id = ?", (pet_id, user_id))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, "Питомец уже передан или продан."
    cur.execute("DELETE FROM pet_care WHERE pet_id = ?", (pet_id,))
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (price, user_id))
    conn.commit()
    conn.close()
    return True, f"Питомец продан системе за {price:,} LDC."


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
    "farmer": {"name": "🌾 Фермер", "salary": 900, "cooldown_hours": 3, "admin_only": False},
    "fisher": {"name": "🎣 Рыбак", "salary": 950, "cooldown_hours": 3, "admin_only": False},
    "miner": {"name": "⛏️ Шахтёр", "salary": 1100, "cooldown_hours": 4, "admin_only": False},
    "blacksmith": {"name": "⚒️ Кузнец", "salary": 1200, "cooldown_hours": 4, "admin_only": False},
    "engineer": {"name": "⚙️ Инженер", "salary": 1350, "cooldown_hours": 5, "admin_only": False},
    "explorer": {"name": "🧭 Исследователь", "salary": 1250, "cooldown_hours": 5, "admin_only": False},
    "merchant": {"name": "🛒 Торговец", "salary": 1150, "cooldown_hours": 4, "admin_only": False},
    "ruler": {"name": "👑 Правитель Lavender Dream", "salary": 10000, "cooldown_hours": 12, "admin_only": True},
}

# Трудоустройство и смена профессии бесплатны.
HIRE_COST = 0
FIRE_EXTRA_COST = 0


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
        cooldown = get_user_cooldown_seconds(user_id, "work", info["cooldown_hours"] * 3600)
        if delta.total_seconds() < cooldown:
            remaining = cooldown - int(delta.total_seconds())
            return False, f"Отдых ещё не закончен. Осталось: {remaining // 3600} ч {(remaining % 3600) // 60} мин"
    base_salary = get_profession_salary(prof["profession"], prof["level"])
    friend_bonus = friend_income_bonus(user_id)
    clan_bonus = clan_income_bonus(user_id)
    total_bonus = friend_bonus + clan_bonus
    salary = base_salary + (base_salary * total_bonus // 100)
    update_balance(user_id, salary)
    reduction = consume_user_boost(user_id, "work")
    next_cooldown = info["cooldown_hours"] * 3600 * (100 - reduction) // 100 if reduction else info["cooldown_hours"] * 3600
    set_user_cooldown_seconds(user_id, "work", next_cooldown)
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
    bonus_parts = []
    if friend_bonus > 0: bonus_parts.append(f"+{friend_bonus}% друзья")
    if clan_bonus > 0: bonus_parts.append(f"+{clan_bonus}% клан")
    bonus_text = f" ({', '.join(bonus_parts)})" if bonus_parts else ""
    boost_text = f" Буст сократил следующий отдых на {reduction}%." if reduction else ""
    return True, f"Вы заработали {salary} 🪙{bonus_text}! (уровень {new_level}, дней: {new_days}/7){boost_text}"


# === ЕЖЕДНЕВНЫЕ ЗАДАНИЯ ===

DAILY_TASKS = {
    "activist": {"name": "💬 Активист", "target": 30, "reward": 100, "type": "messages", "desc": "Отправь 30 сообщений в чат"},
    "resident": {"name": "🌴 Житель Lavender Dream", "target": 50, "reward": 150, "type": "messages", "desc": "Отправь 50 сообщений в чат"},
    "friendly": {"name": "🤍 Дружелюбный", "target": 5, "reward": 200, "type": "replies", "desc": "Ответь на 5 разных сообщений"},
    "photographer": {"name": "📸 Фотограф", "target": 1, "reward": 50, "type": "photo", "desc": "Отправь фото в чат"},
    "musician": {"name": "🎵 Музыкант", "target": 1, "reward": 50, "type": "audio", "desc": "Отправь музыку или голосовое в чат"},
    "night_owl": {"name": "🌙 Ночной житель", "target": 1, "reward": 250, "type": "night_message", "desc": "Напиши сообщение после 20:00"},
    "early_bird": {"name": "🌅 Ранняя пташка", "target": 1, "reward": 150, "type": "morning_message", "desc": "Напиши сообщение до 09:00"},
    "storyteller": {"name": "📖 Рассказчик", "target": 10, "reward": 120, "type": "long_messages", "desc": "Отправь 10 сообщений длиной от 50 символов"},
    "emoji_master": {"name": "✨ Эмодзи-мастер", "target": 15, "reward": 100, "type": "emoji_messages", "desc": "Отправь 15 сообщений с эмодзи"},
    "questioner": {"name": "❓ Любопытный", "target": 10, "reward": 100, "type": "question_messages", "desc": "Отправь 10 сообщений с вопросительным знаком"},
    "game_player": {"name": "🎮 Игрок", "target": 3, "reward": 200, "type": "games", "desc": "Сыграй 3 игры"},
    "helper": {"name": "🤝 Помощник", "target": 10, "reward": 150, "type": "replies", "desc": "Ответь на 10 сообщений других участников"},
    "social_butterfly": {"name": "🦋 Душа компании", "target": 3, "reward": 150, "type": "active_hours", "desc": "Напиши в чате в течение 3 разных часов"},
    "voice_star": {"name": "🎙️ Голос чата", "target": 3, "reward": 100, "type": "audio", "desc": "Отправь 3 голосовых или музыкальных сообщений"},
    "link_sharer": {"name": "🔗 Делится полезным", "target": 3, "reward": 100, "type": "links", "desc": "Отправь 3 сообщения со ссылками"},
    "weekend_vibes": {"name": "🎉 Выходной вайб", "target": 25, "reward": 150, "type": "weekend_messages", "desc": "Напиши 25 сообщений в выходной"},
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
    """Advance assigned tasks by progress event type and reward each completion once.

    daily_tasks.task_type stores a task ID (such as activist), while callers
    report event types (such as messages). Match through DAILY_TASKS['type'];
    otherwise most daily tasks never advance.
    """
    if amount <= 0:
        return None
    date = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, task_type, progress, target, reward, is_done
        FROM daily_tasks WHERE user_id = ? AND assigned_date = ?
    """, (user_id, date))
    rows = cur.fetchall()
    completed = []
    completed_reward = 0
    completed_names = []
    ticket_rewards = 0
    for task_id, task_key, progress, target, reward, is_done in rows:
        task_info = DAILY_TASKS.get(task_key, {})
        if task_info.get("type") != task_type and task_key != task_type:
            continue
        if is_done:
            continue
        target = int(target or task_info.get("target", 0) or 0)
        new_progress = min(int(progress or 0) + amount, target)
        done = new_progress >= target
        cur.execute(
            "UPDATE daily_tasks SET progress = ?, is_done = ?, is_claimed = ? WHERE id = ? AND is_done = 0",
            (new_progress, int(done), int(done), task_id),
        )
        if done and cur.rowcount == 1:
            completed.append(task_id)
            completed_reward += int(reward or task_info.get("reward", 0) or 0)
            completed_names.append(task_info.get("name", task_key))
            if task_key in {"game_player", "storyteller", "weekend_vibes"}:
                ticket_rewards += 1
    conn.commit()
    conn.close()
    if completed_reward:
        update_balance(user_id, completed_reward)
    if ticket_rewards:
        change_wheel_tickets(user_id, ticket_rewards)
    if not completed:
        return None
    return {"task_type": task_type, "names": completed_names,
            "reward": completed_reward, "completed_count": len(completed)}
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
            logger.exception("Не удалось выполнить необязательную операцию")
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
    elif promo["reward_type"] == "tickets":
        tickets = max(1, min(100, int(promo["reward_value"] or 1)))
        change_wheel_tickets(user_id, tickets)
        reward_text = f"{tickets} билета(ов) колеса"
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

def buy_weekly_wheel_tickets(user_id: int) -> tuple:
    """Buy 10 wheel tickets once per seven days, atomically on PostgreSQL/SQLite."""
    quantity = 10
    now = datetime.now()
    conn = sqlite3.connect(DB_NAME)
    try:
        cur = conn.cursor()
        cur.execute("SELECT price FROM catalog_items WHERE item_type = ?", ("wheel_tickets_weekly",))
        price_row = cur.fetchone()
        price = int(price_row[0]) if price_row else 25000

        cur.execute("""
            CREATE TABLE IF NOT EXISTS wheel_ticket_weekly_purchases (
                user_id BIGINT PRIMARY KEY,
                purchased_at TIMESTAMP NOT NULL
            )
        """)

        # Insert a placeholder if this is the user's first attempt, then lock
        # the row. Unlike BEGIN IMMEDIATE, SELECT ... FOR UPDATE works on the
        # PostgreSQL database used by Render and serializes bot/web purchases.
        expired_placeholder = (now - timedelta(days=8)).isoformat()
        cur.execute(
            "INSERT INTO wheel_ticket_weekly_purchases (user_id, purchased_at) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO NOTHING",
            (user_id, expired_placeholder),
        )
        cur.execute(
            "SELECT purchased_at FROM wheel_ticket_weekly_purchases WHERE user_id = ? FOR UPDATE",
            (user_id,),
        )
        previous = cur.fetchone()
        if previous and previous[0]:
            try:
                raw_timestamp = previous[0]
                purchased_at = (
                    raw_timestamp if isinstance(raw_timestamp, datetime)
                    else datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
                )
                if purchased_at.tzinfo is not None:
                    purchased_at = purchased_at.astimezone().replace(tzinfo=None)
                remaining = int(7 * 86400 - (now - purchased_at).total_seconds())
            except (TypeError, ValueError, OverflowError):
                # A corrupt legacy timestamp must not permanently block a user.
                remaining = 0
            if remaining > 0:
                days, rem = divmod(remaining, 86400)
                hours = rem // 3600
                conn.rollback()
                return False, f"Повторная покупка доступна через {days} дн. {hours} ч."

        cur.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
            (price, user_id, price),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False, f"Нужно {price:,} LDC для покупки 10 билетов."

        cur.execute(
            "INSERT INTO wheel_tickets (user_id, tickets) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET tickets = wheel_tickets.tickets + EXCLUDED.tickets",
            (user_id, quantity),
        )
        cur.execute(
            "UPDATE wheel_ticket_weekly_purchases SET purchased_at = ? WHERE user_id = ?",
            (now.isoformat(), user_id),
        )
        conn.commit()
        return True, f"Куплено 10 билетов колеса за {price:,} LDC. Повторная покупка — через 7 дней."
    except Exception:
        conn.rollback()
        logger.exception("Failed to buy weekly wheel tickets for user_id=%s", user_id)
        raise
    finally:
        conn.close()

def get_wheel_tickets(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO wheel_tickets (user_id, tickets) VALUES (?, 0) ON CONFLICT(user_id) DO NOTHING", (user_id,))
    cur.execute("SELECT tickets FROM wheel_tickets WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.commit()
    conn.close()
    return int(row[0] if row else 0)


def change_wheel_tickets(user_id: int, delta: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT INTO wheel_tickets (user_id, tickets) VALUES (?, 0) ON CONFLICT(user_id) DO NOTHING", (user_id,))
    if delta < 0:
        cur.execute("UPDATE wheel_tickets SET tickets = tickets + ? WHERE user_id = ? AND tickets >= ?", (delta, user_id, -delta))
        if cur.rowcount != 1:
            conn.close()
            return False
    else:
        cur.execute("UPDATE wheel_tickets SET tickets = tickets + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()
    return True


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
    ("wheel_tickets", "1", 3), # tickets for the fortune wheel
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

    if rtype == "wheel_tickets":
        change_wheel_tickets(user_id, max(1, int(value or 1)))
        return f"🎟️ Билет колеса фортуны: +{max(1, int(value or 1))}"

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

def ttt_accept_invite_atomic(invite_id: int, accepter_id: int) -> tuple:
    """Одноразово принимает PvP-вызов, списывает обе ставки и создаёт игру одной транзакцией."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT from_user, to_user, bet, status FROM ttt_invites WHERE id = ?",
        (invite_id,),
    )
    invite = cur.fetchone()
    if not invite:
        conn.close()
        return False, "Приглашение не найдено."
    from_user, to_user, bet, status = invite
    if int(to_user) != int(accepter_id):
        conn.close()
        return False, "Это приглашение не для тебя."
    if status != "pending":
        conn.close()
        return False, "Приглашение уже обработано."
    cur.execute(
        "UPDATE ttt_invites SET status = 'accepted' WHERE id = ? AND status = 'pending'",
        (invite_id,),
    )
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, "Приглашение уже обработано."
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (bet, from_user, bet))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, "У вызывающего недостаточно монет."
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (bet, to_user, bet))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, "У тебя недостаточно монет."
    cur.execute(
        "INSERT INTO ttt_games (player_x, player_o, is_vs_bot, turn, bet, status) VALUES (?, ?, 0, ?, ?, 'active')",
        (from_user, to_user, from_user, bet),
    )
    game_id = cur.lastrowid
    conn.commit()
    conn.close()
    return True, {"game_id": game_id, "from_user": from_user, "to_user": to_user, "bet": bet}


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
    """Атомарно применяет ход; параллельные/повторные нажатия не могут применить один ход дважды."""
    if not isinstance(cell, int) or cell < 0 or cell > 8:
        return False, "Некорректная клетка"

    game = ttt_get_game(game_id)
    if not game or game["status"] != "active":
        return False, "Игра неактивна"
    if game["turn"] != player_id:
        return False, "Не ваш ход"

    board = list(game["board"])
    if len(board) != 9 or board[cell] != "_":
        return False, "Клетка занята"

    symbol = "X" if player_id == game["player_x"] else "O"
    if player_id not in (game["player_x"], game["player_o"]):
        return False, "Вы не участник игры"
    board[cell] = symbol
    new_board = "".join(board)
    winner = ttt_check_winner(new_board)

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    guard = "WHERE id = ? AND status = 'active' AND turn = ? AND board = ?"
    params_guard = (game_id, player_id, game["board"])

    if winner == "draw":
        cur.execute(
            "UPDATE ttt_games SET board = ?, status = 'draw' " + guard,
            (new_board, *params_guard),
        )
    elif winner:
        winner_id = game["player_x"] if winner == "X" else game["player_o"]
        cur.execute(
            "UPDATE ttt_games SET board = ?, status = 'finished', winner = ? " + guard,
            (new_board, winner_id, *params_guard),
        )
    else:
        next_turn = game["player_x"] if game["is_vs_bot"] else (
            game["player_o"] if player_id == game["player_x"] else game["player_x"]
        )
        cur.execute(
            "UPDATE ttt_games SET board = ?, turn = ? " + guard,
            (new_board, next_turn, *params_guard),
        )

    changed = cur.rowcount
    conn.commit()
    conn.close()
    if changed != 1:
        return False, "Ход уже обработан. Обнови игровое поле."
    return True, winner or "next"


def ttt_bot_move(game_id: int) -> Optional[int]:
    """Оптимальный minimax-бот: не проигрывает при корректной игре."""
    game = ttt_get_game(game_id)
    if not game or game["status"] != "active":
        return None

    board = list(game["board"])
    if len(board) != 9 or any(cell not in ("X", "O", "_") for cell in board):
        return None

    def minimax(state, bot_turn, depth=0):
        result = ttt_check_winner("".join(state))
        if result == "O":
            return 10 - depth
        if result == "X":
            return depth - 10
        if result == "draw":
            return 0

        scores = []
        symbol = "O" if bot_turn else "X"
        for index, value in enumerate(state):
            if value != "_":
                continue
            state[index] = symbol
            scores.append((minimax(state, not bot_turn, depth + 1), index))
            state[index] = "_"
        if not scores:
            return 0
        return (max if bot_turn else min)(score for score, _ in scores)

    best_move = None
    best_score = float("-inf")
    # Фиксированный порядок нужен только для выбора среди равноценных ходов.
    for index in (4, 0, 2, 6, 8, 1, 3, 5, 7):
        if board[index] != "_":
            continue
        board[index] = "O"
        score = minimax(board, False, 1)
        board[index] = "_"
        if score > best_score:
            best_score, best_move = score, index
    return best_move


def ttt_finish_game(game_id: int, winner_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE ttt_games SET status = 'finished', winner = ? WHERE id = ?", (winner_id, game_id))
    conn.commit()
    conn.close()

# ==================== САПЁР ====================

MS_FIELD_SIZE = 8
MS_MINES = 6
MS_BET = 500
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
    """Открывает клетку с compare-and-set, чтобы повторные клики не выплатили приз дважды."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, user_id, board, revealed, bet, status, field_size, mines_count
        FROM minesweeper_games WHERE id = ?
    """, (game_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False, "error", "Игра не найдена"
    game = {
        "id": row[0], "user_id": row[1], "board": row[2], "revealed": row[3],
        "bet": row[4], "status": row[5], "field_size": row[6], "mines_count": row[7],
    }
    if game["user_id"] != user_id:
        conn.close()
        return False, "error", "Не твоя игра"
    if game["status"] != "active":
        conn.close()
        return False, "error", "Игра неактивна"

    size = int(game["field_size"])
    if not isinstance(cell, int) or not 0 <= cell < size * size:
        conn.close()
        return False, "error", "Клетка вне поля"
    board = list(game["board"])
    revealed = list(game["revealed"])
    if revealed[cell] == "1":
        conn.close()
        return False, "error", "Клетка уже открыта"

    if board[cell] == "M":
        for i in range(size * size):
            if board[i] == "M":
                revealed[i] = "1"
        revealed_str = "".join(revealed)
        cur.execute("""
            UPDATE minesweeper_games SET revealed = ?, status = 'lose'
            WHERE id = ? AND status = 'active' AND revealed = ?
        """, (revealed_str, game_id, game["revealed"]))
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return False, "error", "Партия уже обновилась. Нажми ещё раз."
        conn.commit()
        conn.close()
        return True, "boom", "💥 Мина! Ты проиграл."

    to_reveal = [cell]
    while to_reveal:
        c = to_reveal.pop()
        if revealed[c] == "1":
            continue
        revealed[c] = "1"
        if board[c] == "0":
            row_idx, col_idx = divmod(c, size)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = row_idx + dr, col_idx + dc
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
        """, (revealed_str, game_id, game["revealed"]))
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return False, "error", "Партия уже обновилась. Нажми ещё раз."
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (int(game["bet"]) * 2, user_id))
        if cur.rowcount != 1:
            conn.rollback()
            conn.close()
            return False, "error", "Не удалось начислить выигрыш."
        conn.commit()
        conn.close()
        return True, "win", f"🏆 Победа! Валовая выплата {int(game['bet']) * 2:,} 🪙"
    cur.execute("""
        UPDATE minesweeper_games SET revealed = ?
        WHERE id = ? AND status = 'active' AND revealed = ?
    """, (revealed_str, game_id, game["revealed"]))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return False, "error", "Партия уже обновилась. Нажми ещё раз."
    conn.commit()
    conn.close()
    return True, "safe", ""

# Legacy Battleship gameplay removed; the active Mini App implementation lives in backend/main.py.

# ==================== ДРУЗЬЯ ====================

FRIEND_BONUS_PER = 2       # +2% за каждого друга
FRIEND_BONUS_MAX = 20      # максимум +20%


def friend_send_request(from_user: int, to_user: int):
    """Отправляет заявку в друзья. Возвращает True / 'mutual' / False."""
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

    # Уже есть заявка (pending)?
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

        # Добавляем в друзья обоих (с ON CONFLICT, чтобы не падало на дубликатах)
        cur.execute("""
            INSERT INTO friends (user_id, friend_id) VALUES (?, ?)
            ON CONFLICT (user_id, friend_id) DO NOTHING
        """, (from_user, to_user))
        cur.execute("""
            INSERT INTO friends (user_id, friend_id) VALUES (?, ?)
            ON CONFLICT (user_id, friend_id) DO NOTHING
        """, (to_user, from_user))

        conn.commit()
        conn.close()
        return "mutual"

    # Обычная заявка (upsert — если запись уже есть, обновляем статус)
    try:
        cur.execute("""
            INSERT INTO friend_requests (from_user, to_user, status)
            VALUES (?, ?, 'pending')
            ON CONFLICT (from_user, to_user) DO UPDATE SET status = 'pending'
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
        logger.exception("Не удалось выполнить необязательную операцию")
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
    """Покупает косметику (если ещё не куплена). Возвращает (ok, message)."""
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

    # Уже куплено?
    if owns_cosmetic(user_id, item_type, item_key):
        # просто применяем без оплаты
        if item_type == "color":
            set_cosmetics(user_id, nickname_color=item_key)
        elif item_type == "frame":
            set_cosmetics(user_id, frame=item_key)
        elif item_type == "status":
            set_cosmetics(user_id, status=item_key)
        elif item_type == "theme":
            set_cosmetics(user_id, theme=item_key)
        return True, "✅ Применено (уже куплено)"

    # Бесплатное
    if price == 0:
        mark_owned(user_id, item_type, item_key)
        if item_type == "color":
            set_cosmetics(user_id, nickname_color=item_key)
        elif item_type == "frame":
            set_cosmetics(user_id, frame=item_key)
        elif item_type == "status":
            set_cosmetics(user_id, status=item_key)
        elif item_type == "theme":
            set_cosmetics(user_id, theme=item_key)
        return True, "✅ Применено бесплатно"

    if get_balance(user_id) < price:
        return False, f"Недостаточно монет. Нужно: {price:,} 🪙"

    if not update_balance(user_id, -price):
        return False, "Ошибка списания"

    mark_owned(user_id, item_type, item_key)

    # Применяем сразу
    if item_type == "color":
        set_cosmetics(user_id, nickname_color=item_key)
    elif item_type == "frame":
        set_cosmetics(user_id, frame=item_key)
    elif item_type == "status":
        set_cosmetics(user_id, status=item_key)
    elif item_type == "theme":
        set_cosmetics(user_id, theme=item_key)

    return True, f"Куплено! Списано {price:,} 🪙"


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

def owns_cosmetic(user_id: int, item_type: str, item_key: str) -> bool:
    """Проверяет, куплена ли уже эта косметика."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM user_cosmetics_owned
        WHERE user_id = ? AND item_type = ? AND item_key = ?
    """, (user_id, item_type, item_key))
    exists = cur.fetchone() is not None
    conn.close()
    return exists


def mark_owned(user_id: int, item_type: str, item_key: str):
    """Отмечает косметику как купленную."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO user_cosmetics_owned (user_id, item_type, item_key)
        VALUES (?, ?, ?)
    """, (user_id, item_type, item_key))
    conn.commit()
    conn.close()


def get_owned_cosmetics(user_id: int) -> dict:
    """Возвращает dict {item_type: set(item_key)}."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT item_type, item_key FROM user_cosmetics_owned WHERE user_id = ?
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()

    result = {"color": set(), "frame": set(), "status": set(), "theme": set()}
    for item_type, item_key in rows:
        if item_type in result:
            result[item_type].add(item_key)
    return result

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
        logger.exception("Не удалось выполнить необязательную операцию")

# ==================== ОСТРОВ ====================

ISLAND_PRICE = 50000
HOUSE_PRICE = 30000
PIER_PRICE = 45000
SHIP_PRICE = 100000

HOUSE_INCOME = 5000
HOUSE_COOLDOWN = 5 * 3600      # 5 часов
SHIP_INCOME = 75000
SHIP_COOLDOWN = 48 * 3600      # 48 часов


def island_get(user_id: int) -> dict:
    """Возвращает состояние острова пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT has_island, has_house, has_pier, has_ship,
               last_house_income, last_ship_income
        FROM islands WHERE user_id = ?
    """, (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return {
            "has_island": False, "has_house": False,
            "has_pier": False, "has_ship": False,
            "last_house_income": None, "last_ship_income": None,
        }
    return {
        "has_island": bool(row[0]),
        "has_house": bool(row[1]),
        "has_pier": bool(row[2]),
        "has_ship": bool(row[3]),
        "last_house_income": row[4],
        "last_ship_income": row[5],
    }


def island_buy(user_id: int, item: str) -> tuple:
    """Покупает постройку. item: island / house / pier / ship.
    Возвращает (ok, message)."""
    state = island_get(user_id)

    prices = {
        "island": ISLAND_PRICE,
        "house": HOUSE_PRICE,
        "pier": PIER_PRICE,
        "ship": SHIP_PRICE,
    }
    names = {
        "island": "🏝️ Остров",
        "house": "🏠 Домик",
        "pier": "⚓ Причал",
        "ship": "🚢 Корабль",
    }

    if item not in prices:
        return False, "Неизвестная постройка"

    # Уже куплено?
    if state[f"has_{item}"]:
        return False, "Уже куплено"

    # Проверка порядка
    if item == "island" and state["has_island"]:
        return False, "Остров уже куплен"
    if item in ("house", "pier", "ship") and not state["has_island"]:
        return False, "Сначала купи остров"
    if item == "ship" and not state["has_pier"]:
        return False, "Сначала построй причал"

    price = prices[item]
    if get_balance(user_id) < price:
        return False, f"Недостаточно монет. Нужно: {price:,} 🪙"

    if not update_balance(user_id, -price):
        return False, "Ошибка списания"

    # Пишем в БД
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO islands (user_id) VALUES (?)", (user_id,))
    cur.execute(f"UPDATE islands SET has_{item} = 1 WHERE user_id = ?", (user_id,))

    # Первый доход считаем от момента покупки
    if item == "house":
        cur.execute("UPDATE islands SET last_house_income = ? WHERE user_id = ?",
                    (datetime.now().isoformat(), user_id))
    if item == "ship":
        cur.execute("UPDATE islands SET last_ship_income = ? WHERE user_id = ?",
                    (datetime.now().isoformat(), user_id))

    conn.commit()
    conn.close()
    return True, f"Куплено: {names[item]} за {price:,} 🪙"


def islands_income_tick():
    """Фоновая задача: начисляет доход с домиков и кораблей.
    Возвращает список (user_id, amount, type, item_name)."""
    now = datetime.now()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, has_house, has_ship, last_house_income, last_ship_income
        FROM islands WHERE has_house = 1 OR has_ship = 1
    """)
    rows = cur.fetchall()

    to_credit = []

    for user_id, has_house, has_ship, last_h, last_s in rows:
        # Домик
        if has_house:
            last = datetime.fromisoformat(last_h) if last_h else now
            if (now - last).total_seconds() >= HOUSE_COOLDOWN:
                to_credit.append((user_id, HOUSE_INCOME, "house", "🏠 Домик"))
                cur.execute("UPDATE islands SET last_house_income = ? WHERE user_id = ?",
                            (now.isoformat(), user_id))
        # Корабль
        if has_ship:
            last = datetime.fromisoformat(last_s) if last_s else now
            if (now - last).total_seconds() >= SHIP_COOLDOWN:
                to_credit.append((user_id, SHIP_INCOME, "ship", "🚢 Корабль"))
                cur.execute("UPDATE islands SET last_ship_income = ? WHERE user_id = ?",
                            (now.isoformat(), user_id))

    conn.commit()
    conn.close()

    # Начисляем
    for user_id, amount, _, _ in to_credit:
        update_balance(user_id, amount)

    return to_credit

# ==================== КЛАНЫ ====================

CLAN_CREATE_COST = 10000       # стоимость создания клана
CLAN_MAX_MEMBERS = 20           # максимум участников
CLAN_BONUS_PER_MEMBER = 1       # +1% к зарплате за каждого участника
CLAN_BONUS_MAX = 10             # максимум +10%


def clan_create(user_id: int, name: str, emoji: str, description: str = "") -> tuple:
    """Создаёт клан. Возвращает (ok, message)."""
    name = name.strip()
    if len(name) < 3 or len(name) > 20:
        return False, "Название должно быть 3–20 символов"
    if not emoji:
        emoji = "🏛️"

    # Уже в клане?
    if clan_get_user_clan(user_id):
        return False, "Ты уже в клане"

    if get_balance(user_id) < CLAN_CREATE_COST:
        return False, f"Недостаточно монет. Нужно: {CLAN_CREATE_COST:,} 🪙"

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id FROM clans WHERE LOWER(name) = ?", (name.lower(),))
    if cur.fetchone():
        conn.close()
        return False, "Такое название уже занято"

    if not update_balance(user_id, -CLAN_CREATE_COST):
        conn.close()
        return False, "Ошибка списания"

    cur.execute("""
        INSERT INTO clans (name, emoji, leader_id, description)
        VALUES (?, ?, ?, ?)
    """, (name, emoji, user_id, description))
    clan_id = cur.lastrowid

    cur.execute("""
        INSERT INTO clan_members (user_id, clan_id, role)
        VALUES (?, ?, 'leader')
    """, (user_id, clan_id))
    conn.commit()
    conn.close()
    return True, f"Клан {emoji} {name} создан!"


def clan_get_user_clan(user_id: int) -> Optional[dict]:
    """Возвращает клан пользователя или None."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, c.emoji, c.leader_id, c.description, c.bank, cm.role
        FROM clans c
        JOIN clan_members cm ON cm.clan_id = c.id
        WHERE cm.user_id = ?
    """, (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "name": row[1], "emoji": row[2],
        "leader_id": row[3], "description": row[4],
        "bank": row[5], "role": row[6],
    }


def clan_get(clan_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, emoji, leader_id, description, bank, created_at
        FROM clans WHERE id = ?
    """, (clan_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "name": row[1], "emoji": row[2],
        "leader_id": row[3], "description": row[4],
        "bank": row[5], "created_at": row[6],
    }


def clan_get_members(clan_id: int) -> list:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT cm.user_id, cm.role, u.username, u.balance
        FROM clan_members cm
        JOIN users u ON u.user_id = cm.user_id
        WHERE cm.clan_id = ?
        ORDER BY
            CASE cm.role WHEN 'leader' THEN 1 WHEN 'deputy' THEN 2 ELSE 3 END,
            cm.joined_at
    """, (clan_id,))
    rows = cur.fetchall()
    conn.close()
    return [
        {"user_id": r[0], "role": r[1], "username": r[2], "balance": r[3]}
        for r in rows
    ]


def clan_member_count(clan_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM clan_members WHERE clan_id = ?", (clan_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count


def clan_send_request(clan_id: int, user_id: int) -> tuple:
    if clan_get_user_clan(user_id):
        return False, "Ты уже в клане"
    if clan_member_count(clan_id) >= CLAN_MAX_MEMBERS:
        return False, "В клане нет мест"

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id FROM clan_requests WHERE clan_id = ? AND user_id = ? AND status = 'pending'",
                (clan_id, user_id))
    if cur.fetchone():
        conn.close()
        return False, "Заявка уже отправлена"

    cur.execute("""
        INSERT INTO clan_requests (clan_id, user_id) VALUES (?, ?)
        ON CONFLICT(clan_id, user_id) DO UPDATE SET status = 'pending'
    """, (clan_id, user_id))
    conn.commit()
    conn.close()
    return True, "Заявка отправлена"


def clan_get_pending_requests(clan_id: int) -> list:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT cr.id, cr.user_id, u.username
        FROM clan_requests cr
        JOIN users u ON u.user_id = cr.user_id
        WHERE cr.clan_id = ? AND cr.status = 'pending'
    """, (clan_id,))
    rows = cur.fetchall()
    conn.close()
    return [{"request_id": r[0], "user_id": r[1], "username": r[2]} for r in rows]


def clan_accept_request(request_id: int, reviewer_id: int) -> tuple:
    """Принимает заявку. reviewer_id — тот, кто принимает (лидер/заместитель)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT clan_id, user_id, status FROM clan_requests WHERE id = ?", (request_id,))
    row = cur.fetchone()
    if not row or row[2] != "pending":
        conn.close()
        return False, "Заявка не найдена"

    clan_id, target_user, _ = row

    # Проверяем, что reviewer — лидер или заместитель этого клана
    cur.execute("SELECT role FROM clan_members WHERE user_id = ? AND clan_id = ?",
                (reviewer_id, clan_id))
    reviewer_row = cur.fetchone()
    if not reviewer_row or reviewer_row[0] not in ("leader", "deputy"):
        conn.close()
        return False, "Нет прав"

    if clan_member_count(clan_id) >= CLAN_MAX_MEMBERS:
        conn.close()
        return False, "В клане нет мест"

    if clan_get_user_clan(target_user):
        cur.execute("UPDATE clan_requests SET status = 'rejected' WHERE id = ?", (request_id,))
        conn.commit()
        conn.close()
        return False, "Пользователь уже в клане"

    cur.execute("UPDATE clan_requests SET status = 'accepted' WHERE id = ?", (request_id,))
    cur.execute("INSERT INTO clan_members (user_id, clan_id, role) VALUES (?, ?, 'member')",
                (target_user, clan_id))
    conn.commit()
    conn.close()
    return True, "Принят в клан"


def clan_decline_request(request_id: int, reviewer_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT clan_id FROM clan_requests WHERE id = ? AND status = 'pending'", (request_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    clan_id = row[0]

    cur.execute("SELECT role FROM clan_members WHERE user_id = ? AND clan_id = ?",
                (reviewer_id, clan_id))
    reviewer_row = cur.fetchone()
    if not reviewer_row or reviewer_row[0] not in ("leader", "deputy"):
        conn.close()
        return False

    cur.execute("UPDATE clan_requests SET status = 'rejected' WHERE id = ?", (request_id,))
    conn.commit()
    conn.close()
    return True


def clan_leave(user_id: int) -> tuple:
    clan = clan_get_user_clan(user_id)
    if not clan:
        return False, "Ты не в клане"
    if clan["role"] == "leader":
        return False, "Лидер не может выйти. Передай лидерство или распусти клан."

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM clan_members WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return True, "Ты вышел из клана"


def clan_kick(leader_id: int, target_user_id: int) -> tuple:
    clan = clan_get_user_clan(leader_id)
    if not clan:
        return False, "Ты не в клане"
    if clan["role"] not in ("leader", "deputy"):
        return False, "Нет прав"

    target_clan = clan_get_user_clan(target_user_id)
    if not target_clan or target_clan["id"] != clan["id"]:
        return False, "Игрок не из твоего клана"
    if target_user_id == clan["leader_id"]:
        return False, "Нельзя кикнуть лидера"
    if target_clan["role"] == "deputy" and clan["role"] != "leader":
        return False, "Зам не может кикнуть зама"

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM clan_members WHERE user_id = ?", (target_user_id,))
    conn.commit()
    conn.close()
    return True, "Игрок кикнут"


def clan_deposit(user_id: int, amount: int) -> tuple:
    clan = clan_get_user_clan(user_id)
    if not clan:
        return False, "Ты не в клане"
    if amount <= 0:
        return False, "Сумма должна быть больше 0"
    if get_balance(user_id) < amount:
        return False, f"Недостаточно монет. У тебя: {get_balance(user_id):,}"

    if not update_balance(user_id, -amount):
        return False, "Ошибка списания"

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE clans SET bank = bank + ? WHERE id = ?", (amount, clan["id"]))
    conn.commit()
    conn.close()
    return True, f"Внесено {amount:,} 🪙 в казну клана"


def clan_disband(leader_id: int) -> tuple:
    clan = clan_get_user_clan(leader_id)
    if not clan:
        return False, "Ты не в клане"
    if clan["role"] != "leader":
        return False, "Только лидер может распустить клан"

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    # Остаток казны отдаём лидеру
    if clan["bank"] > 0:
        update_balance(leader_id, clan["bank"])
    cur.execute("DELETE FROM clan_members WHERE clan_id = ?", (clan["id"],))
    cur.execute("DELETE FROM clan_requests WHERE clan_id = ?", (clan["id"],))
    cur.execute("DELETE FROM clans WHERE id = ?", (clan["id"],))
    conn.commit()
    conn.close()
    return True, f"Клан распущен. Казна ({clan['bank']:,} 🪙) возвращена лидеру."


def clan_top(limit: int = 10) -> list:
    """Топ кланов по казне."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, c.emoji, c.bank,
               (SELECT COUNT(*) FROM clan_members WHERE clan_id = c.id) AS members
        FROM clans c
        ORDER BY c.bank DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "name": r[1], "emoji": r[2], "bank": r[3], "members": r[4]}
        for r in rows
    ]


def clan_income_bonus(user_id: int) -> int:
    """Возвращает % бонуса к зарплате от клана."""
    clan = clan_get_user_clan(user_id)
    if not clan:
        return 0
    members = clan_member_count(clan["id"])
    bonus = members * CLAN_BONUS_PER_MEMBER
    return min(bonus, CLAN_BONUS_MAX)

def track_message_tasks(user_id: int, is_reply: bool = False,
                        is_photo: bool = False, is_audio: bool = False,
                        is_night: bool = False):
    """Обновляет прогресс и возвращает список только что завершённых заданий."""
    completed = []

    def track(task_type: str):
        result = update_task_progress(user_id, task_type, 1)
        if result:
            completed.append(result)

    # Активист / Житель — каждое сообщение
    track("activist")
    track("resident")

    if is_reply:
        track("friendly")
    if is_photo:
        track("photographer")
    if is_audio:
        track("musician")
    if is_night:
        track("night_owl")

    return completed

# ==================== МУЗЫКА ====================

MUSIC_PERSONAL_BASE = 15       # базовые слоты личного плейлиста
MUSIC_SHARED_MAX = 100         # максимум треков в общем
MUSIC_SLOT_PRICE = 2000        # цена одного слота


def music_personal_limit(user_id: int) -> int:
    """Лимит личного плейлиста с учётом купленных слотов."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT extra_slots FROM music_slots WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    extra = row[0] if row else 0
    return MUSIC_PERSONAL_BASE + extra


def music_buy_slot(user_id: int) -> tuple:
    """Покупает +1 слот для личного плейлиста."""
    if get_balance(user_id) < MUSIC_SLOT_PRICE:
        return False, f"Недостаточно монет. Нужно: {MUSIC_SLOT_PRICE:,} 🪙"

    if not update_balance(user_id, -MUSIC_SLOT_PRICE):
        return False, "Ошибка списания"

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO music_slots (user_id, extra_slots) VALUES (?, 1)
        ON CONFLICT(user_id) DO UPDATE SET extra_slots = music_slots.extra_slots + 1
    """, (user_id,))
    conn.commit()
    conn.close()
    return True, f"Куплен 1 слот за {MUSIC_SLOT_PRICE:,} 🪙"


def music_personal_add(user_id: int, url: str, title: str, author: str, platform: str) -> tuple:
    """Добавляет трек в личный плейлист."""
    limit = music_personal_limit(user_id)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM music_personal WHERE user_id = ?", (user_id,))
    count = cur.fetchone()[0]
    if count >= limit:
        conn.close()
        return False, f"Плейлист полон ({count}/{limit}). Купи слот."

    cur.execute("""
        INSERT INTO music_personal (user_id, url, title, author, platform)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, url, title, author, platform))
    conn.commit()
    conn.close()
    return True, "Трек добавлен в личный плейлист"


def music_personal_list(user_id: int) -> list:
    """Возвращает личный плейлист."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, url, title, author, platform, added_at
        FROM music_personal WHERE user_id = ?
        ORDER BY added_at DESC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "url": r[1], "title": r[2], "author": r[3],
         "platform": r[4], "added_at": r[5]}
        for r in rows
    ]


def music_personal_delete(user_id: int, track_id: int) -> bool:
    """Удаляет трек из личного плейлиста (только свой)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM music_personal WHERE id = ? AND user_id = ?", (track_id, user_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def music_shared_add(user_id: int, url: str, title: str, author: str, platform: str) -> tuple:
    """Добавляет трек в общий плейлист (на модерацию)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM music_shared WHERE status = 'approved'")
    count = cur.fetchone()[0]
    if count >= MUSIC_SHARED_MAX:
        conn.close()
        return False, f"Общий плейлист полон ({count}/{MUSIC_SHARED_MAX})"

    cur.execute("""
        INSERT INTO music_shared (user_id, url, title, author, platform, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    """, (user_id, url, title, author, platform))
    conn.commit()
    conn.close()
    return True, "Трек отправлен на модерацию"


def music_shared_list() -> list:
    """Возвращает только одобренные треки."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT ms.id, ms.url, ms.title, ms.author, ms.platform, ms.added_at, u.username
        FROM music_shared ms
        LEFT JOIN users u ON u.user_id = ms.user_id
        WHERE ms.status = 'approved'
        ORDER BY ms.reviewed_at DESC
    """, ())
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "url": r[1], "title": r[2], "author": r[3],
         "platform": r[4], "added_at": r[5], "added_by": r[6]}
        for r in rows
    ]


def music_shared_pending() -> list:
    """Возвращает треки на модерации (для админов)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT ms.id, ms.url, ms.title, ms.author, ms.platform, ms.added_at, u.username, ms.user_id
        FROM music_shared ms
        LEFT JOIN users u ON u.user_id = ms.user_id
        WHERE ms.status = 'pending'
        ORDER BY ms.added_at ASC
    """, ())
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "url": r[1], "title": r[2], "author": r[3],
         "platform": r[4], "added_at": r[5], "added_by": r[6], "user_id": r[7]}
        for r in rows
    ]


def music_shared_review(track_id: int, reviewer_id: int, approve: bool) -> tuple:
    """Одобряет или отклоняет трек. Проверяет, что reviewer не автор."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM music_shared WHERE id = ? AND status = 'pending'", (track_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False, "Трек не найден или уже рассмотрен"

    if row[0] == reviewer_id:
        conn.close()
        return False, "Нельзя одобрить свой же трек"

    new_status = "approved" if approve else "rejected"
    cur.execute("""
        UPDATE music_shared SET status = ?, reviewed_by = ?, reviewed_at = ?
        WHERE id = ?
    """, (new_status, reviewer_id, datetime.now().isoformat(), track_id))
    conn.commit()
    conn.close()
    return True, "Трек одобрен" if approve else "Трек отклонён"


def music_shared_delete(track_id: int) -> bool:
    """Удаляет трек из общего плейлиста (для админов)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM music_shared WHERE id = ?", (track_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def music_get_info(url: str) -> Optional[dict]:
    """Получает информацию о треке через oEmbed. Возвращает dict или None."""
    import httpx
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


# ==================== RCC (ReefCriptoCoin) ====================

RCC_CLICK_AMOUNT = 0.01          # за клик
RCC_EXCHANGE_RATE = 5            # 1 RCC = 5 LDC
RCC_GAME_COOLDOWN = 7200         # 2 часа
RCC_GAME_DURATION = 25           # 25 секунд


def rcc_get_balance(user_id: int) -> float:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM rcc_balance WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0.0


def rcc_get_stats(user_id: int) -> dict:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT balance, total_clicks, last_game FROM rcc_balance WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"balance": 0.0, "total_clicks": 0, "last_game": None}
    return {"balance": row[0], "total_clicks": row[1], "last_game": row[2]}


def rcc_add(user_id: int, amount: float, type_: str = "click"):
    """Начисляет RCC (или списывает, если amount < 0)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO rcc_balance (user_id) VALUES (?)", (user_id,))
    cur.execute("UPDATE rcc_balance SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    if type_ == "click":
        cur.execute("UPDATE rcc_balance SET total_clicks = total_clicks + 1 WHERE user_id = ?", (user_id,))
    cur.execute(
        "INSERT INTO rcc_transactions (user_id, amount, type) VALUES (?, ?, ?)",
        (user_id, amount, type_)
    )
    conn.commit()
    conn.close()


def rcc_exchange(user_id: int, amount: float) -> tuple:
    """Обмен RCC на Lavender Dream Coin."""
    if amount <= 0:
        return False, "Сумма должна быть больше 0"
    balance = rcc_get_balance(user_id)
    if balance < amount:
        return False, f"Недостаточно RCC. У тебя: {balance:.2f}"

    pc_amount = int(amount * RCC_EXCHANGE_RATE)

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE rcc_balance SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    cur.execute(
        "INSERT INTO rcc_transactions (user_id, amount, type) VALUES (?, ?, 'exchange')",
        (user_id, -amount)
    )
    conn.commit()
    conn.close()

    update_balance(user_id, pc_amount)
    return True, f"Обменяно {amount:.2f} RCC → {pc_amount:,} LDC"


def rcc_can_play(user_id: int) -> tuple:
    """Можно ли играть в мини-игру. (можно, секунд_осталось)"""
    stats = rcc_get_stats(user_id)
    last = stats["last_game"]
    if not last:
        return True, 0
    delta = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
    if delta >= RCC_GAME_COOLDOWN:
        return True, 0
    return False, int(RCC_GAME_COOLDOWN - delta)


def rcc_set_last_game(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO rcc_balance (user_id) VALUES (?)", (user_id,))
    cur.execute(
        "UPDATE rcc_balance SET last_game = ? WHERE user_id = ?",
        (datetime.now().isoformat(), user_id)
    )
    conn.commit()
    conn.close()

# ==================== СПИСКИ ИГРОКОВ И АВТОУДАЛЕНИЕ ====================

def get_users_list(filter_: str = "all") -> list:
    """Возвращает список игроков по фильтру.
    filter_: all / active / banned / blocked_bot
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    if filter_ == "banned":
        cur.execute("""
            SELECT user_id, username, balance, is_banned
            FROM users WHERE is_banned = 1 ORDER BY balance DESC
        """)
    elif filter_ == "active":
        cur.execute("""
            SELECT user_id, username, balance, is_banned
            FROM users WHERE is_banned = 0 ORDER BY balance DESC
        """)
    elif filter_ == "blocked_bot":
        cur.execute("""
            SELECT user_id, username, balance, is_banned
            FROM users WHERE is_banned = 2 ORDER BY balance DESC
        """)
    else:  # all
        cur.execute("""
            SELECT user_id, username, balance, is_banned
            FROM users ORDER BY balance DESC LIMIT 200
        """)

    rows = cur.fetchall()
    conn.close()

    return [
        {"user_id": r[0], "username": r[1], "balance": r[2], "status": r[3]}
        for r in rows
    ]


def clear_banned_users() -> int:
    """Удаляет всех забаненных (полностью из БД)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE is_banned = 1")
    ids = [r[0] for r in cur.fetchall()]

    for uid in ids:
        _delete_user_full(cur, uid)

    conn.commit()
    conn.close()
    return len(ids)

def clear_blocked_users() -> int:
    """Удаляет всех игроков, заблокировавших бота (is_banned = 2), полностью из БД."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE is_banned = 2")
    ids = [r[0] for r in cur.fetchall()]

    for uid in ids:
        _delete_user_full(cur, uid)

    conn.commit()
    conn.close()
    return len(ids)


def delete_user_full(user_id: int) -> bool:
    """Удаляет пользователя полностью из всех таблиц."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cur.fetchone():
        conn.close()
        return False

    _delete_user_full(cur, user_id)
    conn.commit()
    conn.close()
    return True


def _safe_cleanup_query(cur, query: str, params=(), fetch: bool = False, context: str = ""):
    """Skip absent optional tables before issuing SQL; db_compat rolls back on SQL errors."""
    table_match = re.search(r"\b(?:DELETE\s+FROM|FROM)\s+([a-zA-Z_][a-zA-Z0-9_]*)", query, re.I)
    if table_match:
        table = table_match.group(1).lower()
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = ? LIMIT 1",
            (table,),
        )
        if not cur.fetchone():
            return None
    try:
        cur.execute(query, params)
        return cur.fetchall() if fetch else None
    except Exception:
        logger.exception("Не удалось очистить пользовательские данные context=%s", context)
        return None


def _delete_user_full(cur, user_id: int):
    """Внутренняя — удаляет пользовательские данные, сохраняя аудит безопасности."""
    # Pet care uses pet_id rather than user_id, so remove it before deleting pets.
    related_deletes = [
        ("pet_care", "pet_id IN (SELECT id FROM pets WHERE user_id = ?)", (user_id,)),
        ("ttt_games", "(player_x = ? OR player_o = ?)", (user_id, user_id)),
        ("ttt_invites", "(from_user = ? OR to_user = ?)", (user_id, user_id)),
        ("web_trades", "(from_user = ? OR to_user = ?)", (user_id, user_id)),
        ("pet_trades", "(from_user = ? OR to_user = ?)", (user_id, user_id)),
        ("gifts", "(from_user = ? OR to_user = ?)", (user_id, user_id)),
    ]
    for table, condition, params in related_deletes:
        _safe_cleanup_query(cur, f"DELETE FROM {table} WHERE {condition}", params, context=table)

    tables_with_user_id = [
        "purchases", "warns", "game_stats", "new_members",
        "pets", "professions", "profession_progress", "daily_tasks", "promo_activations",
        "user_cosmetics", "user_badges", "user_cosmetics_owned", "user_settings", "islands",
        "friends", "friend_requests", "clan_members", "clan_requests",
        "music_personal", "music_shared", "music_slots",
        "rcc_balance", "rcc_transactions", "rcc_progress", "rcc_game_sessions",
        "wheel_tickets", "wheel_ticket_weekly_purchases", "game_play_limits",
        "daily_transfer_usage", "inventory_items", "minesweeper_games", "battleship_games",
    ]
    for table in tables_with_user_id:
        _safe_cleanup_query(
            cur, f"DELETE FROM {table} WHERE user_id = ?", (user_id,), context=table
        )

    # Remove clan memberships and dissolve clans led by this user.
    _safe_cleanup_query(cur, "DELETE FROM friends WHERE friend_id = ?", (user_id,), context="friends.friend_id")
    _safe_cleanup_query(cur, "DELETE FROM friend_requests WHERE to_user = ?", (user_id,), context="friend_requests.to_user")
    clan_rows = _safe_cleanup_query(
        cur, "SELECT id FROM clans WHERE leader_id = ?", (user_id,), fetch=True, context="clans.leader_id"
    ) or []
    for row in clan_rows:
        clan_id = row[0]
        for table in ("clan_members", "clan_requests"):
            _safe_cleanup_query(cur, f"DELETE FROM {table} WHERE clan_id = ?", (clan_id,), context=f"{table}.clan_id")
        _safe_cleanup_query(cur, "DELETE FROM clans WHERE id = ?", (clan_id,), context="clans.id")

    _safe_cleanup_query(cur, "DELETE FROM users WHERE user_id = ?", (user_id,), context="users")

def mark_user_blocked_bot(user_id: int):
    """Помечает пользователя как заблокировавшего бота (is_banned = 2)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_banned = 2 WHERE user_id = ? AND is_banned = 0", (user_id,))
    conn.commit()
    conn.close()


def unmark_user_blocked_bot(user_id: int):
    """Снимает только статус блокировки бота, не затрагивая административный бан."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_banned = 0 WHERE user_id = ? AND is_banned = 2", (user_id,))
    conn.commit()
    conn.close()

# ==================== ПОДАРКИ ====================

GIFT_EXPIRES_DAYS = 5


def gift_create(from_user: int, to_user: int, gift_type: str, gift_data: str) -> int:
    """Создаёт подарок. gift_data — JSON-строка с данными."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO gifts (from_user, to_user, gift_type, gift_data, expires_at)
        VALUES (?, ?, ?, ?, ?)
    """, (from_user, to_user, gift_type, gift_data,
          (datetime.now() + timedelta(days=GIFT_EXPIRES_DAYS)).isoformat()))
    conn.commit()
    gift_id = cur.lastrowid
    conn.close()
    return gift_id


def gift_get(gift_id: int) -> Optional[dict]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, from_user, to_user, gift_type, gift_data, is_opened, created_at, expires_at
        FROM gifts WHERE id = ?
    """, (gift_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0], "from_user": row[1], "to_user": row[2],
        "gift_type": row[3], "gift_data": row[4],
        "is_opened": bool(row[5]),
        "created_at": row[6], "expires_at": row[7],
    }


def gift_list_incoming(user_id: int) -> list:
    """Входящие подарки (не открытые, не истёкшие)."""
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT g.id, g.from_user, u.username, g.gift_type, g.gift_data, g.created_at, g.expires_at
        FROM gifts g
        LEFT JOIN users u ON u.user_id = g.from_user
        WHERE g.to_user = ? AND g.is_opened = 0 AND g.expires_at > ?
        ORDER BY g.created_at DESC
    """, (user_id, now))
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "from_user": r[1], "from_username": r[2],
         "gift_type": r[3], "gift_data": r[4],
         "created_at": r[5], "expires_at": r[6]}
        for r in rows
    ]


def gift_mark_opened(gift_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE gifts SET is_opened = 1 WHERE id = ?", (gift_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def gift_cleanup_expired() -> int:
    """Удаляет просроченные подарки (не открытые)."""
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM gifts WHERE is_opened = 0 AND expires_at <= ?", (now,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected

# ==================== ЛОГ ДЕЙСТВИЙ ====================

LOG_TYPES = {
    "security":      "🚨 Подозрительное действие",
    "feedback":      "✉️ Обратная связь",
    "purchase":      "🛍️ Покупка",
    "transfer":      "💸 Перевод",
    "game":          "🎮 Игра",
    "bonus":         "🎁 Бонус",
    "work":          "💼 Зарплата",
    "wheel":         "🎡 Колесо",
    "egg":           "🥚 Яйцо",
    "promo":         "🎟️ Промокод",
    "clan_create":   "🏛️ Создание клана",
    "clan_deposit":  "💰 Взнос в казну",
    "clan_wipe":     "💥 Роспуск клана",
    "island_buy":    "🏝️ Покупка острова",
    "island_income": "💰 Доход острова",
    "admin_give":    "➕ Выдача монет",
    "admin_take":    "➖ Снятие монет",
    "tech_action":   "🧪 Действие тех.админа",
    "cancel":        "↩️ Отмена",
    "purchase_cancel": "🛑 Отмена покупки",
    "purchase_reject": "❌ Отклонение покупки",
}


def log_action(user_id: int, action_type: str, details: str = "",
               amount: int = 0, target_user_id: int = None):
    """Записывает значимое действие игрока. Вызывается из бота и из API."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO action_log (user_id, action_type, details, amount, target_user_id)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, action_type, details, amount, target_user_id))
        conn.commit()
    except Exception:
        # Не ломаем игровое действие из-за проблем логирования, но оставляем
        # ошибку в системном логе, чтобы её можно было диагностировать.
        logger.exception(
            "Не удалось записать action_log: user_id=%s action_type=%s",
            user_id, action_type
        )
    finally:
        conn.close()


def log_security_event(event_type: str, details: str = "", ip_address: str = "",
                       method: str = "", path: str = "", status_code: int = 0,
                       user_id: int = None):
    """Persist API rejections/exceptions for review by tech admins."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO security_events (user_id, event_type, details, ip_address, method, path, status_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, str(event_type)[:100], str(details)[:2000], str(ip_address)[:100],
             str(method)[:16], str(path)[:500], int(status_code or 0)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Не удалось записать security event type=%s path=%s", event_type, path)
    finally:
        conn.close()


def get_security_events(limit: int = 100) -> list:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, user_id, event_type, details, ip_address, method, path, status_code, created_at "
        "FROM security_events ORDER BY id DESC LIMIT ?",
        (max(1, min(int(limit), 200)),),
    )
    rows = cur.fetchall()
    columns = ["id", "user_id", "event_type", "details", "ip_address", "method", "path", "status_code", "created_at"]
    result = [dict(zip(columns, tuple(row))) for row in rows]
    conn.close()
    return result


def get_action_log(filter_user: int = None, filter_type: str = None,
                   only_last_hours: int = None, limit: int = 50) -> list:
    """Возвращает лог действий с фильтрами. Только для тех.админов."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    query = "SELECT id, user_id, action_type, details, amount, target_user_id, created_at FROM action_log WHERE 1=1"
    params = []

    if filter_user is not None:
        query += " AND (user_id = ? OR target_user_id = ?)"
        params.extend([filter_user, filter_user])

    if filter_type:
        query += " AND action_type = ?"
        params.append(filter_type)

    if only_last_hours:
        query += " AND created_at >= datetime('now', ?)"
        params.append(f"-{only_last_hours} hours")

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    return [
        {
            "id": r[0], "user_id": r[1], "action_type": r[2],
            "details": r[3], "amount": r[4], "target_user_id": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def get_user_actions(user_id: int, limit: int = 30) -> list:
    """Все действия конкретного игрока (включая переводы ему)."""
    return get_action_log(filter_user=user_id, limit=limit)


# ==================== ТЕХ.АДМИНЫ ====================

def is_test_mode(user_id: int) -> bool:
    """Включён ли у пользователя тест-режим (баланс не тратится)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("SELECT test_mode FROM tech_admins WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return bool(row[0]) if row else False
    except Exception:
        conn.close()
        return False


def set_test_mode(user_id: int, enabled: bool, added_by: int = None):
    """Включить/выключить тест-режим для тех.админа."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO tech_admins (user_id, test_mode, added_by)
            VALUES (?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET test_mode = ?
        """, (user_id, 1 if enabled else 0, added_by, 1 if enabled else 0))
        conn.commit()
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")
    conn.close()


def get_all_tech_admins() -> list:
    """Список тех.админов с их флагами (из БД, не из env)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id, test_mode, added_at FROM tech_admins")
        rows = cur.fetchall()
        conn.close()
        return [{"user_id": r[0], "test_mode": bool(r[1]), "added_at": r[2]} for r in rows]
    except Exception:
        conn.close()
        return []


# ==================== АННУЛИРОВАНИЕ (для тех.админа) ====================

def admin_wipe_purchase(purchase_id: int, refund: bool = False) -> bool:
    """Аннулировать покупку. Если refund=True — вернуть монеты игроку."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id, item_type FROM purchases WHERE id = ?", (purchase_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return False

        user_id, item_type = row

        if refund:
            price = get_price(item_type)
            update_balance(user_id, price)

        cur.execute("DELETE FROM purchases WHERE id = ?", (purchase_id,))
        conn.commit()
        conn.close()

        log_action(user_id, "tech_action", f"Аннулирована покупка #{purchase_id} ({item_type}), refund={refund}")
        return True
    except Exception:
        conn.close()
        return False


def admin_wipe_user_purchases(user_id: int, refund: bool = False) -> int:
    """Аннулировать ВСЕ покупки игрока. Возвращает количество удалённых."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, item_type FROM purchases WHERE user_id = ?", (user_id,))
        rows = cur.fetchall()

        total_refund = 0
        for pid, itype in rows:
            if refund:
                total_refund += get_price(itype)

        if refund and total_refund > 0:
            update_balance(user_id, total_refund)

        cur.execute("DELETE FROM purchases WHERE user_id = ?", (user_id,))
        count = cur.rowcount
        conn.commit()
        conn.close()

        log_action(user_id, "tech_action",
                   f"Аннулированы все покупки ({count} шт), refund={refund}, возвращено {total_refund}")
        return count
    except Exception:
        conn.close()
        return 0


def admin_wipe_last_island_income(user_id: int) -> bool:
    """Списывает последний доход с острова (домик + корабль) и обнуляет таймеры."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT has_house, has_ship, last_house_income, last_ship_income
            FROM islands WHERE user_id = ?
        """, (user_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return False

        has_house, has_ship, last_h, last_s = row
        taken = 0
        if has_house and last_h:
            taken += HOUSE_INCOME
        if has_ship and last_s:
            taken += SHIP_INCOME

        # Списываем и обнуляем таймеры
        update_balance(user_id, -taken)
        cur.execute("""
            UPDATE islands SET last_house_income = NULL, last_ship_income = NULL
            WHERE user_id = ?
        """, (user_id,))
        conn.commit()
        conn.close()

        log_action(user_id, "tech_action", f"Снят последний доход с острова: -{taken}")
        return True
    except Exception:
        conn.close()
        return False


def admin_wipe_island(user_id: int, item: str = None) -> bool:
    """Удалить остров целиком (item=None) или конкретную постройку.
    item: 'island', 'house', 'pier', 'ship'."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        if item is None or item == "island":
            cur.execute("DELETE FROM islands WHERE user_id = ?", (user_id,))
            log_action(user_id, "tech_action", "Полностью удалён остров")
        elif item in ("house", "pier", "ship"):
            cur.execute(f"UPDATE islands SET has_{item} = 0 WHERE user_id = ?", (user_id,))
            log_action(user_id, "tech_action", f"Удалена постройка острова: {item}")
        else:
            conn.close()
            return False
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def admin_wipe_clan(clan_id: int, give_bank_to_leader: bool = True) -> bool:
    """Распустить клан. Если give_bank_to_leader — вернуть казну лидеру."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("SELECT leader_id, bank, name FROM clans WHERE id = ?", (clan_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return False

        leader_id, bank, name = row

        if give_bank_to_leader and bank > 0:
            update_balance(leader_id, bank)

        cur.execute("DELETE FROM clan_members WHERE clan_id = ?", (clan_id,))
        cur.execute("DELETE FROM clan_requests WHERE clan_id = ?", (clan_id,))
        cur.execute("DELETE FROM clans WHERE id = ?", (clan_id,))
        conn.commit()
        conn.close()

        log_action(leader_id, "tech_action",
                   f"Клан «{name}» распущен тех.админом, казна {bank} → лидеру: {give_bank_to_leader}")
        return True
    except Exception:
        conn.close()
        return False


def admin_take_clan_bank(clan_id: int) -> bool:
    """Забрать казну клана (обнулить)."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    try:
        cur.execute("SELECT bank, name FROM clans WHERE id = ?", (clan_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return False

        bank, name = row
        cur.execute("UPDATE clans SET bank = 0 WHERE id = ?", (clan_id,))
        conn.commit()
        conn.close()

        log_action(0, "tech_action", f"Забрана казна клана «{name}»: {bank}")
        return True
    except Exception:
        conn.close()
        return False


# ==================== ПАТЧ update_balance ДЛЯ ТЕСТ-РЕЖИМА ====================

def update_balance_test_aware(user_id: int, amount: int) -> bool:
    """Обёртка над update_balance с учётом тест-режима.
    Если у юзера включён тест-режим и amount < 0 — списание игнорируется."""
    if amount < 0 and is_test_mode(user_id):
        return True
    return update_balance(user_id, amount)

# Инициализация при импорте
init_db()

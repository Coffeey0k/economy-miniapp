"""
db_compat.py — совместимый со sqlite3 API модуль, который на самом деле
работает через PostgreSQL.

ЗАЧЕМ ЭТОТ ФАЙЛ:
Весь существующий код бота (database.py, bot.py, admin.py, main.py) написан
под sqlite3: `sqlite3.connect(...)`, плейсхолдеры `?`, `row[0]` и `row["col"]`
одновременно, `PRAGMA table_info`, `INSERT OR IGNORE` и т.д.

Переписывать вручную каждый SQL-запрос в 1400+ строках database.py — долго
и рискованно (легко где-то ошибиться). Вместо этого мы меняем только ОДНУ
строку в каждом файле:

    import sqlite3          →     import db_compat as sqlite3

и весь остальной код продолжает работать без изменений, потому что этот
модуль сам переводит SQLite-синтаксис в PostgreSQL "на лету":

    ?                                    → %s   (плейсхолдеры)
    INTEGER PRIMARY KEY AUTOINCREMENT    → SERIAL PRIMARY KEY
    BOOLEAN                              → INTEGER   (0/1, как и было в sqlite)
    TIMESTAMP                            → TEXT   (храним даты как строки,
                                             как sqlite и делал по факту —
                                             иначе весь код с
                                             datetime.fromisoformat(row[...])
                                             сломался бы, т.к. Postgres
                                             вернул бы готовый datetime,
                                             а не строку)
    INSERT OR IGNORE INTO ...            → INSERT INTO ... ON CONFLICT DO NOTHING
    PRAGMA table_info(table)             → запрос к information_schema,
                                             возвращает результат в том же
                                             виде (col[1] = имя колонки)

Класс Row ведёт себя как sqlite3.Row: к результату можно обращаться
и как row[0] (по номеру), и как row["colname"] (по имени) — код бота
использует оба варианта в разных местах.
"""

import os
import re
import psycopg2


# ========== Row: как sqlite3.Row, но и по индексу, и по имени ==========

class Row:
    __slots__ = ("_data", "_columns")

    def __init__(self, data, columns):
        self._data = tuple(data)
        self._columns = columns

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._data[self._columns.index(key)]
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def keys(self):
        return list(self._columns)

    def __repr__(self):
        return repr(dict(zip(self._columns, self._data)))


# ========== Перевод SQLite-синтаксиса в PostgreSQL ==========

_PRAGMA_RE = re.compile(r"^\s*PRAGMA\s+table_info\(([\w]+)\)\s*;?\s*$", re.IGNORECASE)


def _translate(query: str) -> str:
    q = query

    # AUTOINCREMENT
    q = re.sub(
        r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "SERIAL PRIMARY KEY",
        q, flags=re.IGNORECASE,
    )

    # BOOLEAN -> INTEGER (храним как 0/1, как и раньше в sqlite)
    q = re.sub(r"\bBOOLEAN\b", "INTEGER", q, flags=re.IGNORECASE)

    # TIMESTAMP DEFAULT CURRENT_TIMESTAMP -> TEXT с текстовым дефолтом
    q = re.sub(
        r"\bTIMESTAMP\s+DEFAULT\s+CURRENT_TIMESTAMP\b",
        "TEXT DEFAULT (CURRENT_TIMESTAMP)::text",
        q, flags=re.IGNORECASE,
    )
    # Оставшиеся одиночные TIMESTAMP -> TEXT
    q = re.sub(r"\bTIMESTAMP\b", "TEXT", q, flags=re.IGNORECASE)

    # INSERT OR IGNORE INTO ... -> INSERT INTO ... ON CONFLICT DO NOTHING
    if re.match(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", q, flags=re.IGNORECASE):
        q = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", q, flags=re.IGNORECASE)
        if "ON CONFLICT" not in q.upper():
            q = q.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    # Плейсхолдеры: ? -> %s
    q = q.replace("?", "%s")

    return q


# ========== Cursor / Connection ==========

class Cursor:
    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor()
        self._pragma_columns = None  # если последний execute был PRAGMA table_info

    def execute(self, query, params=()):
        pragma_match = _PRAGMA_RE.match(query)
        if pragma_match:
            table = pragma_match.group(1)
            self._cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            self._pragma_columns = [r[0] for r in self._cur.fetchall()]
            return self

        self._pragma_columns = None
        try:
            self._cur.execute(_translate(query), tuple(params) if params else None)
        except Exception:
            self._conn.rollback()
            raise
        return self

    def _wrap(self, raw_row):
        if raw_row is None:
            return None
        columns = [d[0] for d in self._cur.description]
        return Row(raw_row, columns)

    def fetchone(self):
        if self._pragma_columns is not None:
            if not self._pragma_columns:
                return None
            name = self._pragma_columns.pop(0)
            return Row((None, name, None, None, None, None),
                       ["cid", "name", "type", "notnull", "dflt_value", "pk"])
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        if self._pragma_columns is not None:
            cols = ["cid", "name", "type", "notnull", "dflt_value", "pk"]
            rows = [Row((None, name, None, None, None, None), cols) for name in self._pragma_columns]
            self._pragma_columns = None
            return rows
        return [self._wrap(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()


class Connection:
    def __init__(self, dsn):
        self._conn = psycopg2.connect(dsn)
        self.row_factory = None  # для совместимости с "conn.row_factory = sqlite3.Row" (не используется)

    def cursor(self):
        return Cursor(self._conn)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connect(_ignored_sqlite_path=None):
    """Сигнатура как у sqlite3.connect(path) — путь к файлу игнорируется,
    вместо этого подключаемся к Postgres по DATABASE_URL из окружения."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "Не задана переменная окружения DATABASE_URL — укажи её в настройках "
            "хостинга (строка подключения к твоей Postgres-базе)."
        )
    return Connection(dsn)

def init_db():
    """Создаёт все таблицы в PostgreSQL, если их нет."""
    conn = connect()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            balance BIGINT DEFAULT 0,
            is_banned BOOLEAN DEFAULT FALSE,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_bonus_time TIMESTAMP
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            item_type TEXT,
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_used BOOLEAN DEFAULT FALSE
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS warns (
            user_id BIGINT PRIMARY KEY,
            warn_count INTEGER DEFAULT 0
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_stats (
            user_id BIGINT PRIMARY KEY,
            games_played INTEGER DEFAULT 0,
            games_won INTEGER DEFAULT 0,
            total_earned BIGINT DEFAULT 0,
            total_lost BIGINT DEFAULT 0
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS new_members (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            username TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pets (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            pet_name TEXT,
            pet_emoji TEXT,
            rarity TEXT,
            income INTEGER,
            is_active BOOLEAN DEFAULT TRUE,
            obtained_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS professions (
            user_id BIGINT PRIMARY KEY,
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
            user_id BIGINT,
            profession TEXT,
            level INTEGER DEFAULT 1,
            days_worked INTEGER DEFAULT 0,
            last_work TIMESTAMP,
            PRIMARY KEY (user_id, profession)
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_tasks (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            task_type TEXT,
            progress INTEGER DEFAULT 0,
            target INTEGER,
            reward INTEGER,
            is_done BOOLEAN DEFAULT FALSE,
            is_claimed BOOLEAN DEFAULT FALSE,
            assigned_date TEXT
        )
    """)
    
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
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_activations (
            id SERIAL PRIMARY KEY,
            code TEXT,
            user_id BIGINT,
            activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wheel_sectors (
            id SERIAL PRIMARY KEY,
            label TEXT,
            reward_type TEXT,
            reward_value TEXT,
            chance REAL
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id BIGINT PRIMARY KEY,
            tutorial_done BOOLEAN DEFAULT FALSE,
            hints_enabled BOOLEAN DEFAULT TRUE,
            last_wheel_time TIMESTAMP
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cursed_egg (
            id INTEGER PRIMARY KEY,
            stock INTEGER DEFAULT 5,
            appeared_at TIMESTAMP,
            is_active BOOLEAN DEFAULT FALSE
        )
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS catalog_items (
            id SERIAL PRIMARY KEY,
            item_type TEXT UNIQUE,
            name TEXT,
            price BIGINT,
            description TEXT,
            is_hidden BOOLEAN DEFAULT FALSE
        )
    """)
    
    conn.commit()
    conn.close()

# Совместимость с кодом вида `conn.row_factory = sqlite3.Row`
Row_marker = Row

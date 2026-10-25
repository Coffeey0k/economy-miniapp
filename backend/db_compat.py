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
import time
import logging
import threading
import psycopg2
from psycopg2.pool import ThreadedConnectionPool, PoolError
from psycopg2.extensions import TRANSACTION_STATUS_IDLE


logger = logging.getLogger(__name__)
_POOL_LOCK = threading.Lock()
_CONNECTION_POOL = None
_CONNECTION_POOL_DSN = None


def _positive_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.environ.get(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _get_connection_pool(dsn: str, connect_timeout: int):
    global _CONNECTION_POOL, _CONNECTION_POOL_DSN
    with _POOL_LOCK:
        if _CONNECTION_POOL is None or _CONNECTION_POOL_DSN != dsn:
            if _CONNECTION_POOL is not None:
                try:
                    _CONNECTION_POOL.closeall()
                except Exception:
                    logger.exception("Не удалось закрыть старый пул PostgreSQL")
            minconn = _positive_int_env("DB_POOL_MIN", 1, 1, 4)
            maxconn = _positive_int_env("DB_POOL_MAX", 24, 2, 40)
            minconn = min(minconn, maxconn)
            # Bound SQL execution and lock waits so a stalled/contended query
            # cannot hold a Telegram update for minutes. Values are configurable
            # per deployment while remaining within safe operational limits.
            try:
                statement_timeout_ms = max(
                    1000, min(120000, int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "20000")))
                )
            except (TypeError, ValueError):
                statement_timeout_ms = 20000
            try:
                lock_timeout_ms = max(
                    500, min(60000, int(os.environ.get("DB_LOCK_TIMEOUT_MS", "5000")))
                )
            except (TypeError, ValueError):
                lock_timeout_ms = 5000

            _CONNECTION_POOL = ThreadedConnectionPool(
                minconn,
                maxconn,
                dsn,
                connect_timeout=connect_timeout,
                application_name="lavender_dream_bot",
                options=(
                    f"-c statement_timeout={statement_timeout_ms} "
                    f"-c lock_timeout={lock_timeout_ms} "
                    "-c idle_in_transaction_session_timeout=30000"
                ),
            )
            _CONNECTION_POOL_DSN = dsn
        return _CONNECTION_POOL


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

_PRAGMA_RE = re.compile(r"PRAGMA\s+table_info\s*\(\s*['\"]?(\w+)['\"]?\s*\)", re.IGNORECASE)


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
    # TRUE/FALSE -> 1/0 (для совместимости с INTEGER)
    q = re.sub(r"\bTRUE\b", "1", q, flags=re.IGNORECASE)
    q = re.sub(r"\bFALSE\b", "0", q, flags=re.IGNORECASE)
    
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
        safe_params = tuple(
            (1 if p is True else 0 if p is False else p)
            for p in params
        ) if params else ()

        # Запоминаем таблицу, если это INSERT — пригодится для lastrowid
        import re as _re
        m = _re.match(r"\s*INSERT\s+INTO\s+(\w+)", query, _re.IGNORECASE)
        self._last_insert_table = m.group(1) if m else None

        started = time.perf_counter()
        try:
            self._cur.execute(_translate(query), safe_params if safe_params else None)
        except Exception:
            self._conn.rollback()
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            try:
                slow_threshold = float(os.environ.get("DB_SLOW_QUERY_MS", "300"))
            except (TypeError, ValueError):
                slow_threshold = 300.0
            if elapsed_ms >= slow_threshold:
                operation = query.strip().split(None, 1)[0].upper() if query.strip() else "UNKNOWN"
                table_match = re.search(r"\b(?:FROM|INTO|UPDATE|JOIN)\s+([A-Za-z_][\w.]*)", query, re.IGNORECASE)
                table = table_match.group(1) if table_match else "unknown"
                logger.warning("SLOW_DB_QUERY elapsed_ms=%.0f operation=%s table=%s", elapsed_ms, operation, table)
        return self

    def _wrap(self, raw_row):
        if raw_row is None:
            return None
        columns = [d[0] for d in self._cur.description]
        return Row(raw_row, columns)

    @property
    def rowcount(self):
        """Number of rows affected by the most recent SQL statement."""
        return self._cur.rowcount

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
    def lastrowid(self):
        """Эмуляция sqlite3.Cursor.lastrowid для Postgres.
        Postgres не возвращает id автоматически — приходится делать
        отдельный SELECT currval(pg_get_serial_sequence(...)).
        Работает сразу после INSERT, пока не сделан следующий запрос.
        """
        try:
            # Узнаём имя таблицы из последнего INSERT
            query = getattr(self, "_last_insert_table", None)
            if not query:
                return None
            self._cur.execute(
                "SELECT currval(pg_get_serial_sequence(%s, 'id'))",
                (query,)
            )
            return self._cur.fetchone()[0]
        except Exception:
            return None

class Connection:
    def __init__(self, dsn):
        # Fail quickly when PostgreSQL is unreachable instead of blocking an
        # aiogram handler for the driver's long default connection timeout.
        try:
            connect_timeout = max(2, min(15, int(os.environ.get("DB_CONNECT_TIMEOUT", "5"))))
        except (TypeError, ValueError):
            connect_timeout = 5
        self._pool = _get_connection_pool(dsn, connect_timeout)
        pool_wait_started = time.perf_counter()
        deadline = time.monotonic() + 5.0
        while True:
            try:
                self._conn = self._pool.getconn()
                break
            except PoolError:
                # Wait in the calling worker instead of failing an otherwise
                # valid update merely because all pooled connections are busy.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)
        pool_wait_ms = (time.perf_counter() - pool_wait_started) * 1000
        try:
            pool_wait_threshold = float(os.environ.get("DB_SLOW_POOL_WAIT_MS", "250"))
        except (TypeError, ValueError):
            pool_wait_threshold = 250.0
        if pool_wait_ms >= pool_wait_threshold:
            logger.warning("SLOW_DB_POOL_WAIT elapsed_ms=%.0f", pool_wait_ms)
        self._closed = False
        self.row_factory = None  # совместимость с conn.row_factory = sqlite3.Row

    def cursor(self):
        return Cursor(self._conn)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        close_connection = bool(self._conn.closed)
        if not close_connection:
            try:
                if self._conn.get_transaction_status() != TRANSACTION_STATUS_IDLE:
                    self._conn.rollback()
            except Exception:
                close_connection = True
                logger.exception("Ошибка сброса соединения PostgreSQL перед возвратом в пул")
        try:
            self._pool.putconn(self._conn, close=close_connection)
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass
            logger.exception("Не удалось вернуть соединение PostgreSQL в пул")

    def __del__(self):
        # Return borrowed connections even when legacy code forgets close().
        try:
            self.close()
        except Exception:
            pass


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
            is_banned INTEGER DEFAULT 0,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_bonus_time TIMESTAMP
        )
    """)
    
    # is_banned используется как статус: 0=активен, 1=забанен,
    # 2=пользователь заблокировал бота. BOOLEAN не может хранить значение 2.
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'users' "
        "AND column_name = 'is_banned'"
    )
    status_type = cur.fetchone()
    if status_type and status_type[0] == "boolean":
        cur.execute("ALTER TABLE users ALTER COLUMN is_banned DROP DEFAULT")
        cur.execute(
            "ALTER TABLE users ALTER COLUMN is_banned TYPE INTEGER "
            "USING CASE WHEN is_banned THEN 1 ELSE 0 END"
        )
    cur.execute("ALTER TABLE users ALTER COLUMN is_banned SET DEFAULT 0")

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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS web_rules_read (
            user_id BIGINT PRIMARY KEY,
            read_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Older deployments may have created this table with INTEGER, which
    # cannot store all Telegram user IDs. Widen the existing column safely.
    cur.execute(
        "ALTER TABLE web_rules_read ALTER COLUMN user_id TYPE BIGINT"
    )

    # Remove obsolete feature tables; their dependent application code has been
    # removed and the historical records are explicitly no longer retained.
    for obsolete_table in (
        "clan_requests", "clan_members", "clans",
        "web_trades", "pet_trades",
        "music_personal", "music_shared", "music_slots",
    ):
        cur.execute(f"DROP TABLE IF EXISTS {obsolete_table} CASCADE")

    conn.commit()
    conn.close()

# Совместимость с кодом вида `conn.row_factory = sqlite3.Row`
Row_marker = Row

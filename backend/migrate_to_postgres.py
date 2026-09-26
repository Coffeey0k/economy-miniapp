"""
migrate_to_postgres.py — переносит ВСЕ текущие данные (балансы, питомцы,
покупки и т.д.) из старой базы paradise.db (SQLite) в новую базу PostgreSQL.

Запускать ОДИН РАЗ, после того как:
1. Завёл базу на Neon (или другом Postgres-хостинге) и получил connection string
2. Один раз запустил backend с новой переменной DATABASE_URL — это создаст
   пустые таблицы в Postgres (см. database.py, init_db() вызывается сам
   при старте)

КАК ЗАПУСТИТЬ (с любого компьютера с Python):
    pip install psycopg2-binary
    python migrate_to_postgres.py путь/до/paradise.db "postgresql://user:pass@host/dbname"

Если не передать аргументы — возьмёт "paradise.db" из текущей папки и
переменную окружения DATABASE_URL.

Скрипт безопасно пропускает строки, которые уже есть в Postgres (на случай,
если запустишь его повторно), и ничего не удаляет из исходной SQLite базы.
"""

import os
import sys
import sqlite3
import psycopg2

TABLES = [
    "catalog_items", "users", "purchases", "warns", "game_stats",
    "new_members", "pets", "professions", "daily_tasks",
    "promocodes", "promo_activations", "wheel_sectors",
    "user_settings", "cursed_egg",
]


def migrate(sqlite_path: str, dsn: str):
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    dst = psycopg2.connect(dsn)
    dst_cur = dst.cursor()

    for table in TABLES:
        try:
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            print(f"⏭  Таблицы {table} нет в старой базе — пропускаю")
            continue

        if not rows:
            print(f"⏭  {table}: пусто, нечего переносить")
            continue

        columns = rows[0].keys()
        placeholders = ", ".join(["%s"] * len(columns))
        col_list = ", ".join(columns)
        query = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT DO NOTHING"
        )

        copied = 0
        for row in rows:
            values = tuple(row[c] for c in columns)
            try:
                dst_cur.execute(query, values)
                copied += 1
            except Exception as e:
                dst.rollback()
                print(f"⚠️  {table}: не удалось перенести строку {dict(row)} — {e}")
                continue

        dst.commit()
        print(f"✅ {table}: перенесено {copied} из {len(rows)} строк")

    src.close()
    dst_cur.close()
    dst.close()
    print("\nГотово! Все данные перенесены.")


if __name__ == "__main__":
    sqlite_path = sys.argv[1] if len(sys.argv) > 1 else "paradise.db"
    dsn = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DATABASE_URL")

    if not dsn:
        print("Укажи адрес Postgres-базы вторым аргументом или через DATABASE_URL")
        sys.exit(1)
    if not os.path.exists(sqlite_path):
        print(f"Файл {sqlite_path} не найден")
        sys.exit(1)

    migrate(sqlite_path, dsn)

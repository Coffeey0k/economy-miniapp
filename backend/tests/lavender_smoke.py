"""PostgreSQL smoke test for the Lavender Dream feature update.

Run by CI against a disposable PostgreSQL database. No production credentials or
external services are required.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
import db_compat
import world_events


def main():
    # Simulate legacy INTEGER columns from an older production schema.
    conn = db_compat.connect(database.DB_NAME)
    cur = conn.cursor()
    cur.execute("CREATE TABLE web_rules_read (user_id INTEGER PRIMARY KEY, read_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cur.execute("""CREATE TABLE professions (
        user_id INTEGER PRIMARY KEY, profession TEXT, level INTEGER DEFAULT 1,
        last_work TIMESTAMP, days_worked INTEGER DEFAULT 0, last_work_day TEXT,
        total_changes INTEGER DEFAULT 0)""")
    cur.execute("""CREATE TABLE profession_progress (
        user_id INTEGER, profession TEXT, level INTEGER DEFAULT 1,
        days_worked INTEGER DEFAULT 0, last_work TIMESTAMP,
        PRIMARY KEY (user_id, profession))""")
    cur.execute("""CREATE TABLE daily_tasks (
        id SERIAL PRIMARY KEY, user_id INTEGER, task_type TEXT, progress INTEGER DEFAULT 0,
        target INTEGER, reward INTEGER, is_done BOOLEAN DEFAULT FALSE,
        is_claimed BOOLEAN DEFAULT FALSE, assigned_date TEXT)""")
    conn.commit()
    conn.close()

    db_compat.init_db()
    database.init_db()
    conn = db_compat.connect(database.DB_NAME)
    cur = conn.cursor()
    for table in ("web_rules_read", "professions", "profession_progress", "daily_tasks"):
        cur.execute("""SELECT data_type FROM information_schema.columns
            WHERE table_name = ? AND column_name = 'user_id'""", (table,))
        row = cur.fetchone()
        assert row and row["data_type"] == "bigint", f"{table}.user_id was not migrated to BIGINT"
    conn.close()
    conn = database.sqlite3.connect(database.DB_NAME)
    cur = conn.cursor()
    user_id = 9_000_000_001  # Exercise BIGINT Telegram IDs.
    cur.execute("INSERT INTO web_rules_read (user_id) VALUES (?)", (user_id,))
    cur.execute(
        """INSERT INTO users (user_id, username, balance)
           VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
           username = excluded.username, balance = excluded.balance""",
        (user_id, "lavender-smoke", 100_000),
    )
    cur.execute(
        """INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income, is_active)
           VALUES (?, ?, ?, ?, ?, TRUE)""",
        (user_id, "🐾 Smoke Pet", "🐾", "обычный", 100),
    )
    pet_id = cur.lastrowid
    cur.execute(
        """CREATE TABLE IF NOT EXISTS island_inventory (
            user_id BIGINT NOT NULL,
            item_key TEXT NOT NULL,
            quantity BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, item_key)
        )"""
    )
    cur.execute(
        """INSERT INTO island_inventory (user_id, item_key, quantity)
           VALUES (?, 'wood', 10)
           ON CONFLICT(user_id, item_key) DO UPDATE SET quantity = excluded.quantity""",
        (user_id,),
    )
    conn.commit()
    conn.close()

    # Pet food inventory increments must work on PostgreSQL and feeding must cap
    # stats with LEAST(), not SQLite's scalar MIN(x, y).
    assert database.add_inventory_item(user_id, "universal_pet_food", 2), "Adding pet food failed"
    ok, message = database.use_pet_food(user_id, pet_id)
    assert ok, f"Feeding pet with purchased food failed: {message}"
    inventory = {item["item_key"]: item["quantity"] for item in database.get_inventory(user_id)}
    assert inventory.get("universal_pet_food") == 1, "Feeding did not consume exactly one portion"

    # The cleanup helper must use PostgreSQL-compatible date arithmetic.
    conn = database.sqlite3.connect(database.DB_NAME)
    cur = conn.cursor()
    old_joined_at = (datetime.now() - timedelta(days=14)).isoformat(sep=" ")
    cur.execute(
        "INSERT INTO new_members (user_id, username, joined_at) VALUES (?, ?, ?)",
        (user_id + 1, "old-smoke-member", old_joined_at),
    )
    conn.commit()
    conn.close()
    database.clear_old_new_members(days=7)
    conn = database.sqlite3.connect(database.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM new_members WHERE user_id = ?", (user_id + 1,))
    assert cur.fetchone() is None, "Old new-member record was not removed"
    conn.close()

    # Hiring is free and large Telegram IDs survive all legacy table migrations.
    balance_before = 100_000
    ok, _ = database.hire_profession(user_id, "artist")
    assert ok, "Free artist hiring failed"
    conn = database.sqlite3.connect(database.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    assert int(cur.fetchone()["balance"]) == balance_before, "Hiring unexpectedly charged LDC"
    conn.close()

    # Switching professions must preserve the old profession without relying on
    # unsupported SQLite-only INSERT OR REPLACE syntax.
    next_profession = next(key for key in database.PROFESSIONS if key != "artist")
    ok, message = database.hire_profession(user_id, next_profession)
    assert ok, f"Switching profession failed: {message}"

    world_events.ensure_current_season()
    world_events.record_season_activity(user_id, "game", 3)
    status = world_events.get_season_status(user_id)
    daily_game = next(
        task for task in status["tasks"]
        if task["period_type"] == "daily" and task["task_type"] == "game"
    )
    assert daily_game["completed"], "Daily season task did not progress"
    world_events.claim_season_task(user_id, daily_game["task_key"])
    world_events.claim_achievement(user_id, "first_game")

    event = world_events.start_event(
        "luck_hour", duration_minutes=60, reward_ldc=500, reward_xp=100, source="manual"
    )
    world_events.record_season_activity(user_id, "game", 1)
    claimed = world_events.claim_event(user_id)
    assert claimed["reward_ldc"] == 500, "Global event reward claim failed"
    assert world_events.end_event(event["id"]), "Global event did not end cleanly"

    market = world_events.get_system_market(user_id)
    assert any(offer["key"] == "seed_bundle" for offer in market["offers"])
    sale = world_events.sell_system_market_item(user_id, "wood", 5)
    assert sale["reward_ldc"] == 200, "NPC resource sale failed"
    purchase = world_events.buy_system_market_offer(user_id, "seed_bundle")
    assert purchase["price"] == 500, "NPC market purchase failed"

    expedition = world_events.start_pet_expedition(user_id, pet_id, "forest")
    conn = database.sqlite3.connect(database.DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "UPDATE pet_expeditions SET ends_at = ? WHERE id = ?",
        ((datetime.utcnow() - timedelta(minutes=1)).isoformat(), expedition["id"]),
    )
    conn.commit()
    conn.close()
    # The same pet cannot be gifted while away.
    assert not database.transfer_pet(pet_id, user_id, user_id + 1), "Expedition pet gift guard failed"
    reward = world_events.claim_pet_expedition(user_id, expedition["id"])
    assert reward["reward_ldc"] > 0, "Pet expedition reward claim failed"

    print("Lavender Dream PostgreSQL smoke test passed")


if __name__ == "__main__":
    main()

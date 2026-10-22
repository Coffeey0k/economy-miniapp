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
import world_events


def main():
    database.init_db()
    conn = database.sqlite3.connect(database.DB_NAME)
    cur = conn.cursor()
    user_id = 9_000_000_001  # Exercise BIGINT Telegram IDs.
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

    # Hiring is free and large Telegram IDs survive all legacy table migrations.
    balance_before = 100_000
    ok, _ = database.hire_profession(user_id, "artist")
    assert ok, "Free artist hiring failed"
    conn = database.sqlite3.connect(database.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    assert int(cur.fetchone()["balance"]) == balance_before, "Hiring unexpectedly charged LDC"
    conn.close()

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

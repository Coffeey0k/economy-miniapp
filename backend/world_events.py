"""Database-backed global events and 30-day seasons for Lavender Dream."""
import json
import os
import random
from datetime import datetime, timedelta
import db_compat as sqlite3
import database as bot_db

EVENT_TYPES = {
    "treasure_hunt": ("💎 Охота за сокровищами", "Выполни условия события и забери награду."),
    "luck_hour": ("🍀 Час удачи", "Временное событие с дополнительными поручениями и сезонным опытом."),
    "lavender_bloom": ("💜 Цветение лаванды", "Собирай сезонные достижения и получай награды."),
    "traveling_merchant": ("🛒 Странствующий торговец", "Временная возможность получить особые предметы и награды."),
    "rift": ("🌀 Разлом измерений", "Пройди испытание события и забери награду."),
    "community_goal": ("🌍 Общее достижение", "Участвуй в общем событии Lavender Dream."),
    "daily_mystery": ("🔮 Тайна дня", "Разгадай загадку и выполни особое поручение."),
    "magical_storm": ("⚡ Магическая буря", "Выполняй особые поручения, пока событие активно."),
}
DEFAULT_TASKS = [
    ("work_3", "Рабочая неделя", "Выполни 3 рабочих смены в течение сезона.", "work", 3, 150, 300),
    ("games_10", "Любитель игр", "Сыграй 10 раз в мини-игры.", "game", 10, 200, 400),
    ("pet_3", "Забота о питомце", "Выполни 3 действия с питомцем или экспедицией.", "pet", 3, 150, 300),
    ("daily_5", "Постоянство", "Заверши 5 ежедневных заданий.", "daily_task", 5, 250, 500),
    ("events_2", "Участник событий", "Прими участие в 2 глобальных событиях.", "event", 2, 250, 500),
    ("collect_3", "Коллекционер", "Получи 3 предмета коллекции или косметики.", "collect", 3, 250, 500),
    ("visit_3", "Исследователь мира", "Посети 3 поместья или особых локации.", "visit", 3, 150, 300),
    ("craft_2", "Мастерская", "Создай 2 предмета.", "craft", 2, 200, 400),
]

def _db():
    return sqlite3.connect(os.environ.get("DB_PATH", bot_db.DB_NAME))

def _dt(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)

def _row_dict(row):
    return dict(row) if row else None

def ensure_settings():
    conn = _db()
    cur = conn.cursor()
    cur.execute("""INSERT INTO world_event_settings
        (id, random_enabled, min_gap_minutes, default_duration_minutes)
        VALUES (1, TRUE, 60, 60) ON CONFLICT(id) DO NOTHING""")
    conn.commit()
    conn.close()

def get_settings():
    ensure_settings()
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM world_event_settings WHERE id = 1")
    row = _row_dict(cur.fetchone()) or {}
    conn.close()
    return row

def get_current_event():
    ensure_settings()
    now = datetime.utcnow()
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM world_event_settings WHERE id = 1")
    settings = cur.fetchone()
    active_id = settings["active_event_id"] if settings else None
    if active_id:
        cur.execute("SELECT * FROM world_events WHERE id = ?", (active_id,))
        event = cur.fetchone()
        if event and _dt(event["ends_at"]) and _dt(event["ends_at"]) <= now:
            cur.execute("UPDATE world_events SET status = 'ended', ended_at = ? WHERE id = ? AND status = 'active'",
                        (now.isoformat(), active_id))
            next_allowed = now + timedelta(minutes=max(0, int(settings["min_gap_minutes"] or 60)))
            cur.execute("""UPDATE world_event_settings SET active_event_id = NULL,
                next_allowed_at = ?, next_random_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1""",
                (next_allowed.isoformat(), (next_allowed + timedelta(minutes=random.randint(60, 240))).isoformat()))
            active_id = None
            event = None
        if event and event["status"] == "active":
            result = dict(event)
            result["remaining_seconds"] = max(0, int((_dt(event["ends_at"]) - now).total_seconds()))
            conn.commit()
            conn.close()
            return result
    conn.commit()
    conn.close()
    return None

def start_event(event_type, duration_minutes=60, reward_ldc=500, reward_xp=100,
                title=None, description=None, source="random", created_by=None,
                scheduled_id=None):
    if event_type not in EVENT_TYPES:
        raise ValueError("Неизвестный тип события")
    duration = min(240, max(5, int(duration_minutes)))
    reward_ldc = min(50000, max(0, int(reward_ldc)))
    reward_xp = min(5000, max(0, int(reward_xp)))
    get_current_event()  # Expire stale events and enforce the post-event cooldown.
    now = datetime.utcnow()
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM world_event_settings WHERE id = 1 FOR UPDATE")
        settings = cur.fetchone()
        if not settings:
            cur.execute("""INSERT INTO world_event_settings
                (id, random_enabled, min_gap_minutes, default_duration_minutes)
                VALUES (1, TRUE, 60, 60) ON CONFLICT(id) DO NOTHING""")
            cur.execute("SELECT * FROM world_event_settings WHERE id = 1 FOR UPDATE")
            settings = cur.fetchone()
        active_id = settings["active_event_id"]
        if active_id:
            cur.execute("SELECT status, ends_at FROM world_events WHERE id = ?", (active_id,))
            active = cur.fetchone()
            if active and active["status"] == "active" and _dt(active["ends_at"]) > now:
                raise RuntimeError("Событие уже активно")
            cur.execute("UPDATE world_event_settings SET active_event_id = NULL WHERE id = 1")
        next_allowed = _dt(settings["next_allowed_at"])
        if next_allowed and next_allowed > now:
            raise RuntimeError("Между событиями должна пройти пауза")
        default_title, default_description = EVENT_TYPES[event_type]
        end_at = now + timedelta(minutes=duration)
        event_key = f"{event_type}_{int(now.timestamp())}"
        goal = 20 if event_type == "community_goal" else 1
        cur.execute("""INSERT INTO world_events
            (event_key, title, description, event_type, status, source, starts_at, ends_at,
             reward_ldc, reward_xp, goal, progress, created_by)
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, 0, ?)""",
            (event_key, (title or default_title)[:120], (description or default_description)[:1000],
             event_type, source, now.isoformat(), end_at.isoformat(), reward_ldc, reward_xp, goal, created_by))
        event_id = cur.lastrowid
        cur.execute("""UPDATE world_event_settings SET active_event_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1""", (event_id,))
        if scheduled_id:
            cur.execute("UPDATE world_event_schedules SET status = 'started' WHERE id = ? AND status = 'pending'",
                        (scheduled_id,))
        conn.commit()
        return {"id": event_id, "event_key": event_key, "title": title or default_title,
                "description": description or default_description, "event_type": event_type,
                "status": "active", "source": source, "starts_at": now.isoformat(),
                "ends_at": end_at.isoformat(), "remaining_seconds": duration * 60,
                "reward_ldc": reward_ldc, "reward_xp": reward_xp}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def end_event(event_id=None):
    now = datetime.utcnow()
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT active_event_id, min_gap_minutes FROM world_event_settings WHERE id = 1 FOR UPDATE")
    settings = cur.fetchone()
    active_id = event_id or (settings["active_event_id"] if settings else None)
    if not active_id:
        conn.close()
        return False
    cur.execute("UPDATE world_events SET status = 'ended', ended_at = ? WHERE id = ? AND status = 'active'",
                (now.isoformat(), active_id))
    changed = cur.rowcount > 0
    if changed:
        gap = max(0, int(settings["min_gap_minutes"] or 60))
        next_allowed = now + timedelta(minutes=gap)
        cur.execute("""UPDATE world_event_settings SET active_event_id = NULL, next_allowed_at = ?,
            next_random_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1""",
            (next_allowed.isoformat(), (next_allowed + timedelta(minutes=random.randint(60, 240))).isoformat()))
    conn.commit()
    conn.close()
    return changed

def update_settings(random_enabled=None, min_gap_minutes=None, default_duration_minutes=None):
    ensure_settings()
    fields, values = [], []
    if random_enabled is not None:
        fields.append("random_enabled = ?")
        values.append(bool(random_enabled))
    if min_gap_minutes is not None:
        fields.append("min_gap_minutes = ?")
        values.append(min(1440, max(60, int(min_gap_minutes))))
    if default_duration_minutes is not None:
        fields.append("default_duration_minutes = ?")
        values.append(min(240, max(5, int(default_duration_minutes))))
    if not fields:
        return get_settings()
    fields.append("updated_at = CURRENT_TIMESTAMP")
    conn = _db()
    cur = conn.cursor()
    cur.execute("UPDATE world_event_settings SET " + ", ".join(fields) + " WHERE id = 1", tuple(values))
    conn.commit()
    conn.close()
    return get_settings()

def create_schedule(event_type, scheduled_at, duration_minutes=60, reward_ldc=500, reward_xp=100,
                    title=None, description=None, created_by=None):
    if event_type not in EVENT_TYPES:
        raise ValueError("Неизвестный тип события")
    when = _dt(scheduled_at)
    if not when or when <= datetime.utcnow():
        raise ValueError("Время запуска должно быть в будущем")
    conn = _db()
    cur = conn.cursor()
    cur.execute("""INSERT INTO world_event_schedules
        (event_type, scheduled_at, duration_minutes, reward_ldc, reward_xp, title, description, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_type, when.isoformat(), min(240, max(5, int(duration_minutes))),
         min(50000, max(0, int(reward_ldc))), min(5000, max(0, int(reward_xp))),
         (title or "")[:120] or None, (description or "")[:1000] or None, created_by))
    schedule_id = cur.lastrowid
    conn.commit()
    conn.close()
    return schedule_id

def get_due_schedule():
    conn = _db()
    cur = conn.cursor()
    cur.execute("""SELECT * FROM world_event_schedules WHERE status = 'pending'
        AND scheduled_at <= ? ORDER BY scheduled_at ASC LIMIT 1""", (datetime.utcnow().isoformat(),))
    row = _row_dict(cur.fetchone())
    conn.close()
    return row

def list_schedules(limit=20):
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM world_event_schedules ORDER BY scheduled_at DESC LIMIT ?", (min(100, max(1, int(limit))),))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows

def list_events(limit=20):
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM world_events ORDER BY id DESC LIMIT ?", (min(100, max(1, int(limit))),))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows

def claim_event(user_id):
    event = get_current_event()
    if not event:
        raise ValueError("Сейчас нет активного события")
    if event["event_type"] == "community_goal":
        if int(event.get("progress") or 0) < int(event.get("goal") or 1):
            raise ValueError("Общая цель события ещё не достигнута")
        conn = _db()
        cur = conn.cursor()
        cur.execute("SELECT contribution FROM world_event_participants WHERE event_id = ? AND user_id = ?",
                    (event["id"], user_id))
        participant = cur.fetchone()
        conn.close()
        if not participant or int(participant["contribution"] or 0) < 1:
            raise ValueError("Сначала внеси вклад в общее событие")
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO world_event_claims (event_id, user_id) VALUES (?, ?)",
                    (event["id"], user_id))
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (int(event["reward_ldc"]), user_id))
        if cur.rowcount != 1:
            raise ValueError("Профиль игрока не найден")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise ValueError("Награда за это событие уже получена")
        raise
    finally:
        conn.close()
    add_season_xp(user_id, int(event["reward_xp"]))
    return {"ok": True, "reward_ldc": int(event["reward_ldc"]), "reward_xp": int(event["reward_xp"])}

def contribute_to_event(user_id, amount=1):
    event = get_current_event()
    if not event or event["event_type"] != "community_goal":
        return None
    conn = _db()
    cur = conn.cursor()
    contribution = max(1, min(10, int(amount)))
    cur.execute("UPDATE world_events SET progress = LEAST(goal, progress + ?) WHERE id = ? AND status = 'active'",
                (contribution, event["id"]))
    cur.execute("""INSERT INTO world_event_participants (event_id, user_id, contribution)
        VALUES (?, ?, ?) ON CONFLICT(event_id, user_id) DO UPDATE SET
        contribution = world_event_participants.contribution + excluded.contribution,
        updated_at = CURRENT_TIMESTAMP""", (event["id"], user_id, contribution))
    conn.commit()
    conn.close()
    return True

def ensure_current_season():
    now = datetime.utcnow()
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM seasons WHERE status = 'active' AND starts_at <= ? AND ends_at > ? ORDER BY id DESC LIMIT 1",
                (now.isoformat(), now.isoformat()))
    season = cur.fetchone()
    if not season:
        cur.execute("UPDATE seasons SET status = 'ended' WHERE status = 'active' AND ends_at <= ?", (now.isoformat(),))
        cur.execute("SELECT COUNT(*) AS total FROM seasons")
        number = int(cur.fetchone()["total"] or 0) + 1
        start = now
        end = start + timedelta(days=30)
        season_key = f"season_{number}_{start.strftime('%Y%m%d')}"
        title = "Пробуждение лаванды" if number == 1 else f"Сезон {number}: Новая глава"
        cur.execute("""INSERT INTO seasons (season_key, title, starts_at, ends_at, status)
            VALUES (?, ?, ?, ?, 'active') ON CONFLICT(season_key) DO NOTHING""",
            (season_key, title, start.isoformat(), end.isoformat()))
        cur.execute("SELECT * FROM seasons WHERE season_key = ?", (season_key,))
        season = cur.fetchone()
        for task in DEFAULT_TASKS:
            cur.execute("""INSERT INTO season_tasks
                (season_id, task_key, title, description, task_type, target, reward_xp, reward_ldc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(season_id, task_key) DO NOTHING""",
                (season["id"],) + task)
    conn.commit()
    result = dict(season) if season else None
    conn.close()
    return result

def add_season_xp(user_id, amount):
    season = ensure_current_season()
    if not season or amount <= 0:
        return
    conn = _db()
    cur = conn.cursor()
    cur.execute("""INSERT INTO season_progress (season_id, user_id, xp, level, claimed_levels)
        VALUES (?, ?, ?, 0, '[]') ON CONFLICT(season_id, user_id) DO UPDATE SET
        xp = season_progress.xp + excluded.xp, updated_at = CURRENT_TIMESTAMP""",
        (season["id"], user_id, int(amount)))
    cur.execute("UPDATE season_progress SET level = xp / 1000 WHERE season_id = ? AND user_id = ?",
                (season["id"], user_id))
    conn.commit()
    conn.close()

def record_season_activity(user_id, task_type, amount=1):
    season = ensure_current_season()
    if not season:
        return
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT task_key, target FROM season_tasks WHERE season_id = ? AND task_type = ?",
                (season["id"], task_type))
    tasks = cur.fetchall()
    for task in tasks:
        cur.execute("""INSERT INTO season_task_progress
            (season_id, user_id, task_key, progress, completed, claimed)
            VALUES (?, ?, ?, 0, FALSE, FALSE) ON CONFLICT(season_id, user_id, task_key) DO NOTHING""",
            (season["id"], user_id, task["task_key"]))
        cur.execute("""UPDATE season_task_progress SET progress = LEAST(?, progress + ?),
            completed = CASE WHEN progress + ? >= ? THEN TRUE ELSE completed END,
            updated_at = CURRENT_TIMESTAMP
            WHERE season_id = ? AND user_id = ? AND task_key = ? AND claimed = FALSE""",
            (task["target"], max(1, int(amount)), max(1, int(amount)), task["target"],
             season["id"], user_id, task["task_key"]))
    conn.commit()
    conn.close()
    # Every successful activity also contributes to a live global community goal.
    contribute_to_event(user_id, min(10, max(1, int(amount))))

def get_season_status(user_id):
    season = ensure_current_season()
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM season_progress WHERE season_id = ? AND user_id = ?", (season["id"], user_id))
    progress = cur.fetchone()
    cur.execute("""SELECT t.task_key, t.title, t.description, t.task_type, t.target, t.reward_xp,
        t.reward_ldc, COALESCE(p.progress, 0) AS progress, COALESCE(p.completed, FALSE) AS completed,
        COALESCE(p.claimed, FALSE) AS claimed
        FROM season_tasks t LEFT JOIN season_task_progress p
        ON p.season_id = t.season_id AND p.user_id = ? AND p.task_key = t.task_key
        WHERE t.season_id = ? ORDER BY t.task_key""", (user_id, season["id"]))
    tasks = [dict(row) for row in cur.fetchall()]
    cur.execute("""SELECT u.user_id, u.username, p.xp, p.level FROM season_progress p
        JOIN users u ON u.user_id = p.user_id WHERE p.season_id = ?
        ORDER BY p.xp DESC, p.updated_at ASC LIMIT 20""", (season["id"],))
    leaderboard = [dict(row) for row in cur.fetchall()]
    conn.close()
    p = dict(progress) if progress else {"xp": 0, "level": 0}
    p["level"] = int(p.get("level") or 0)
    p["xp"] = int(p.get("xp") or 0)
    return {"season": season, "progress": p, "tasks": tasks, "leaderboard": leaderboard}

def claim_season_task(user_id, task_key):
    season = ensure_current_season()
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("""SELECT t.reward_xp, t.reward_ldc, p.progress, p.completed, p.claimed
            FROM season_tasks t JOIN season_task_progress p
            ON p.season_id = t.season_id AND p.task_key = t.task_key
            WHERE t.season_id = ? AND t.task_key = ? AND p.user_id = ? FOR UPDATE""",
            (season["id"], task_key, user_id))
        row = cur.fetchone()
        if not row or not row["completed"]:
            raise ValueError("Задание ещё не выполнено")
        if row["claimed"]:
            raise ValueError("Награда уже получена")
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (int(row["reward_ldc"]), user_id))
        if cur.rowcount != 1:
            raise ValueError("Профиль игрока не найден")
        cur.execute("UPDATE season_task_progress SET claimed = TRUE WHERE season_id = ? AND user_id = ? AND task_key = ? AND claimed = FALSE",
                    (season["id"], user_id, task_key))
        if cur.rowcount != 1:
            raise ValueError("Награда уже получена")
        cur.execute("""INSERT INTO season_progress (season_id, user_id, xp, level, claimed_levels)
            VALUES (?, ?, ?, 0, '[]') ON CONFLICT(season_id, user_id) DO UPDATE SET
            xp = season_progress.xp + excluded.xp, updated_at = CURRENT_TIMESTAMP""",
            (season["id"], user_id, int(row["reward_xp"])))
        cur.execute("UPDATE season_progress SET level = xp / 1000 WHERE season_id = ? AND user_id = ?",
                    (season["id"], user_id))
        conn.commit()
        return {"ok": True, "reward_ldc": int(row["reward_ldc"]), "reward_xp": int(row["reward_xp"])}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

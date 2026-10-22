"""Database-backed global events and 30-day seasons for Lavender Dream."""
import json
import os
import random
from datetime import datetime, timedelta
import db_compat as sqlite3
import database as bot_db

EVENT_TYPES = {
    "treasure_hunt": ("💎 Охота за сокровищами", "Сыграй в мини-игры, посещай остров и собирай находки — 3 действия приведут к кладу."),
    "luck_hour": ("🍀 Час удачи", "Выполняй игровые действия: каждое даёт дополнительный сезонный опыт, а за одно действие можно получить награду события."),
    "lavender_bloom": ("💜 Цветение лаванды", "Ухаживай за питомцами, собирай косметику или создавай предметы — выполни 3 подходящих действия."),
    "traveling_merchant": ("🛒 Странствующий торговец", "Пока торговец в мире, покупки припасов в системном магазине дешевле на 15%. Посети остров или сделай покупку, чтобы получить награду."),
    "rift": ("🌀 Разлом измерений", "Сыграй в 3 мини-игры или выполни производственные действия, чтобы стабилизировать разлом."),
    "community_goal": ("🌍 Общее достижение", "Внеси вклад игровыми действиями в общую цель всего мира. Когда сообщество наберёт 20 действий, участники смогут забрать награду."),
    "daily_mystery": ("🔮 Тайна дня", "Заверши ежедневное задание или посети остров, чтобы раскрыть тайну."),
    "magical_storm": ("⚡ Магическая буря", "Сыграй в мини-игры или выполни рабочие смены — нужно 3 действия, чтобы пережить бурю."),
}
EVENT_ACTIVITY_RULES = {
    "treasure_hunt": {"game", "visit", "collect"},
    "luck_hour": {"game", "work", "pet", "craft", "collect", "visit", "daily_task"},
    "lavender_bloom": {"collect", "pet", "craft"},
    "traveling_merchant": {"collect", "visit"},
    "rift": {"game", "craft"},
    "community_goal": {"game", "work", "pet", "craft", "collect", "visit", "daily_task"},
    "daily_mystery": {"daily_task", "visit"},
    "magical_storm": {"game", "work"},
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

ACHIEVEMENTS = [
    {"key":"first_game","title":"Первый запуск","description":"Сыграй в первую мини-игру.","activity":"game","target":1,"reward_ldc":100,"title_key":"newcomer","title_name":"Новичок"},
    {"key":"gamer_25","title":"Игрок Lavender Dream","description":"Сыграй 25 мини-игр.","activity":"game","target":25,"reward_ldc":500,"title_key":"dream_gamer","title_name":"Игрок мечты"},
    {"key":"worker_10","title":"Трудяга","description":"Выполни 10 рабочих смен.","activity":"work","target":10,"reward_ldc":500,"title_key":"hard_worker","title_name":"Трудяга"},
    {"key":"pet_friend","title":"Друг питомцев","description":"Выполни 10 действий по уходу или экспедициям.","activity":"pet","target":10,"reward_ldc":300,"title_key":"pet_friend","title_name":"Друг питомцев"},
    {"key":"daily_10","title":"Постоянство","description":"Заверши 10 ежедневных заданий.","activity":"daily_task","target":10,"reward_ldc":500,"title_key":"steady_player","title_name":"Постоянный игрок"},
    {"key":"event_3","title":"Охотник за событиями","description":"Участвуй в 3 глобальных событиях.","activity":"event","target":3,"reward_ldc":400,"title_key":"event_hunter","title_name":"Охотник за событиями"},
    {"key":"secret_event","title":"Тайный участник","description":"Секретное достижение. Найди его сам.","activity":"event","target":1,"reward_ldc":300,"title_key":"secret_finder","title_name":"Искатель тайн","secret":True},
    {"key":"collector_5","title":"Коллекционер","description":"Получи 5 предметов коллекции или косметики.","activity":"collect","target":5,"reward_ldc":400,"title_key":"collector","title_name":"Коллекционер"},
    {"key":"explorer_5","title":"Исследователь","description":"Посети 5 островов или поместий.","activity":"visit","target":5,"reward_ldc":300,"title_key":"explorer","title_name":"Исследователь"},
    {"key":"craft_5","title":"Мастерская","description":"Выполни 5 островных производственных действий.","activity":"craft","target":5,"reward_ldc":350,"title_key":"craft_master","title_name":"Мастер"},
]
LEVEL_REWARDS = [
    (1,"ldc","",500,"500 LDC"),
    (2,"collection","lavender_shard",1,"Осколок лаванды"),
    (3,"title","season_scout",0,"Титул «Исследователь сезона»"),
    (5,"cosmetic","season_lavender_frame",1,"Сезонная рамка «Лаванда»"),
    (7,"ldc","",1000,"1 000 LDC"),
    (10,"collection","first_bloom",1,"Цветок первого сезона"),
    (15,"title","lavender_guardian",0,"Титул «Хранитель лаванды»"),
    (20,"ldc","",2500,"2 500 LDC"),
    (25,"cosmetic","seasonal_glow",1,"Эффект «Лавандовое сияние»"),
    (30,"collection","lavender_crown",1,"Корона сезона"),
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
        goal = {"community_goal": 20, "treasure_hunt": 3, "luck_hour": 1,
                "lavender_bloom": 3, "traveling_merchant": 1, "rift": 3,
                "daily_mystery": 1, "magical_storm": 3}.get(event_type, 1)
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

def cancel_schedule(schedule_id):
    conn = _db()
    cur = conn.cursor()
    cur.execute("UPDATE world_event_schedules SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
                (int(schedule_id),))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


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
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT contribution FROM world_event_participants WHERE event_id = ? AND user_id = ?",
                (event["id"], user_id))
    participant = cur.fetchone()
    conn.close()
    personal_progress = int(participant["contribution"] or 0) if participant else 0
    if event["event_type"] == "community_goal":
        if int(event.get("progress") or 0) < int(event.get("goal") or 1):
            raise ValueError("Общая цель события ещё не достигнута")
        if personal_progress < 1:
            raise ValueError("Сначала внеси вклад в общее событие")
    elif personal_progress < int(event.get("goal") or 1):
        raise ValueError("Сначала выполни действия события: прогресс ещё не достигнут")
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

def contribute_to_event(user_id, amount=1, activity_type=None):
    event = get_current_event()
    if not event:
        return None
    allowed = EVENT_ACTIVITY_RULES.get(event["event_type"], set())
    if activity_type not in allowed:
        return None
    contribution = max(1, min(10, int(amount)))
    conn = _db()
    cur = conn.cursor()
    if event["event_type"] == "community_goal":
        cur.execute("UPDATE world_events SET progress = LEAST(goal, progress + ?) WHERE id = ? AND status = 'active'",
                    (contribution, event["id"]))
    else:
        # Per-player progress is tracked separately; the event remains global but
        # each player must complete its own objective before claiming the reward.
        cur.execute("UPDATE world_events SET progress = progress + ? WHERE id = ? AND status = 'active'",
                    (contribution, event["id"]))
    cur.execute("""INSERT INTO world_event_participants (event_id, user_id, contribution)
        VALUES (?, ?, ?) ON CONFLICT(event_id, user_id) DO UPDATE SET
        contribution = CASE WHEN world_event_participants.contribution >= ?
            THEN world_event_participants.contribution
            ELSE LEAST(?, world_event_participants.contribution + excluded.contribution) END,
        updated_at = CURRENT_TIMESTAMP""",
        (event["id"], user_id, contribution, int(event.get("goal") or 1),
         int(event.get("goal") or 1)))
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
    day_key = now.strftime("%Y%m%d")
    week_key = now.strftime("%G%V")
    periodic_tasks = [
        (f"daily_game_{day_key}", "🎮 Ежедневная игра", "Сыграй 3 мини-игры сегодня.", "game", 3, 50, 100, "daily", day_key),
        (f"daily_work_{day_key}", "💼 Ежедневная смена", "Выполни одну рабочую смену сегодня.", "work", 1, 60, 100, "daily", day_key),
        (f"daily_pet_{day_key}", "🐾 Забота дня", "Позаботься о питомце или забери экспедицию.", "pet", 1, 40, 75, "daily", day_key),
        (f"weekly_games_{week_key}", "🎯 Игровая неделя", "Сыграй 20 мини-игр за неделю.", "game", 20, 200, 500, "weekly", week_key),
        (f"weekly_work_{week_key}", "🧰 Рабочая неделя", "Выполни 5 рабочих смен за неделю.", "work", 5, 150, 400, "weekly", week_key),
        (f"weekly_events_{week_key}", "🌍 Событийная неделя", "Забери награды за 2 глобальных события.", "event", 2, 200, 500, "weekly", week_key),
        (f"weekly_pets_{week_key}", "🧭 Путешественник недели", "Заверши 5 действий с питомцами или экспедициями.", "pet", 5, 150, 350, "weekly", week_key),
    ]
    for task_key, title, description, task_type, target, reward_xp, reward_ldc, period_type, period_key in periodic_tasks:
        cur.execute("""INSERT INTO season_tasks
            (season_id, task_key, title, description, task_type, target, reward_xp, reward_ldc, period_type, period_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(season_id, task_key) DO NOTHING""",
            (season["id"], task_key, title, description, task_type, target, reward_xp, reward_ldc, period_type, period_key))
    for level, reward_type, reward_key, amount, description in LEVEL_REWARDS:
        cur.execute("""INSERT INTO season_level_rewards
            (season_id, level, reward_type, reward_key, amount, description)
            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(season_id, level) DO NOTHING""",
            (season["id"], level, reward_type, reward_key, amount, description))
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
    cur.execute("UPDATE season_progress SET level = xp / 500 WHERE season_id = ? AND user_id = ?",
                (season["id"], user_id))
    conn.commit()
    conn.close()

def record_season_activity(user_id, task_type, amount=1):
    season = ensure_current_season()
    if not season:
        return
    conn = _db()
    cur = conn.cursor()
    day_key = datetime.utcnow().strftime("%Y%m%d")
    week_key = datetime.utcnow().strftime("%G%V")
    cur.execute("""SELECT task_key, target FROM season_tasks
        WHERE season_id = ? AND task_type = ?
        AND (period_type = 'season' OR (period_type = 'daily' AND period_key = ?)
             OR (period_type = 'weekly' AND period_key = ?))""",
        (season["id"], task_type, day_key, week_key))
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
    safe_amount = min(100, max(1, int(amount)))
    record_achievement_progress(user_id, task_type, safe_amount)
    # Small XP awards make normal play useful for the 30-day season track.
    if task_type != "event":
        add_season_xp(user_id, safe_amount * 5)
    # Every successful activity also contributes to a live global community goal.
    active_event = get_current_event()
    if active_event and active_event.get("event_type") == "luck_hour" and task_type in EVENT_ACTIVITY_RULES["luck_hour"]:
        add_season_xp(user_id, 25 * safe_amount)
    contribute_to_event(user_id, min(10, safe_amount), task_type)

def get_season_status(user_id):
    season = ensure_current_season()
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM season_progress WHERE season_id = ? AND user_id = ?", (season["id"], user_id))
    progress = cur.fetchone()
    cur.execute("""SELECT t.task_key, t.title, t.description, t.task_type, t.target, t.reward_xp,
        t.reward_ldc, t.period_type, COALESCE(p.progress, 0) AS progress, COALESCE(p.completed, FALSE) AS completed,
        COALESCE(p.claimed, FALSE) AS claimed
        FROM season_tasks t LEFT JOIN season_task_progress p
        ON p.season_id = t.season_id AND p.user_id = ? AND p.task_key = t.task_key
        WHERE t.season_id = ? AND
        (t.period_type = 'season' OR (t.period_type = 'daily' AND t.period_key = ?)
         OR (t.period_type = 'weekly' AND t.period_key = ?))
        ORDER BY t.period_type, t.task_key""", (user_id, season["id"],
        datetime.utcnow().strftime("%Y%m%d"), datetime.utcnow().strftime("%G%V")))
    tasks = [dict(row) for row in cur.fetchall()]
    cur.execute("""SELECT u.user_id, u.username, p.xp, p.level FROM season_progress p
        JOIN users u ON u.user_id = p.user_id WHERE p.season_id = ?
        ORDER BY p.xp DESC, p.updated_at ASC LIMIT 20""", (season["id"],))
    leaderboard = [dict(row) for row in cur.fetchall()]
    cur.execute("""SELECT r.level, r.reward_type, r.reward_key, r.amount, r.description,
        CASE WHEN c.level IS NULL THEN FALSE ELSE TRUE END AS claimed
        FROM season_level_rewards r LEFT JOIN season_level_claims c
        ON c.season_id = r.season_id AND c.user_id = ? AND c.level = r.level
        WHERE r.season_id = ? ORDER BY r.level""", (user_id, season["id"]))
    level_rewards = [dict(row) for row in cur.fetchall()]
    conn.close()
    p = dict(progress) if progress else {"xp": 0, "level": 0}
    p["level"] = int(p.get("level") or 0)
    p["xp"] = int(p.get("xp") or 0)
    return {"season": season, "progress": p, "tasks": tasks, "leaderboard": leaderboard, "level_rewards": level_rewards}

def claim_season_task(user_id, task_key):
    season = ensure_current_season()
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("""SELECT t.reward_xp, t.reward_ldc, t.period_type, t.period_key,
            p.progress, p.completed, p.claimed
            FROM season_tasks t JOIN season_task_progress p
            ON p.season_id = t.season_id AND p.task_key = t.task_key
            WHERE t.season_id = ? AND t.task_key = ? AND p.user_id = ? FOR UPDATE""",
            (season["id"], task_key, user_id))
        row = cur.fetchone()
        if not row or not row["completed"]:
            raise ValueError("Задание ещё не выполнено")
        current_day = datetime.utcnow().strftime("%Y%m%d")
        current_week = datetime.utcnow().strftime("%G%V")
        if row["period_type"] == "daily" and row["period_key"] != current_day:
            raise ValueError("Время ежедневного задания уже закончилось")
        if row["period_type"] == "weekly" and row["period_key"] != current_week:
            raise ValueError("Время еженедельного задания уже закончилось")
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
        cur.execute("UPDATE season_progress SET level = xp / 500 WHERE season_id = ? AND user_id = ?",
                    (season["id"], user_id))
        conn.commit()
        return {"ok": True, "reward_ldc": int(row["reward_ldc"]), "reward_xp": int(row["reward_xp"])}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_achievement_progress(user_id, activity, amount=1):
    amount = max(1, min(100, int(amount)))
    conn = _db()
    cur = conn.cursor()
    try:
        for item in ACHIEVEMENTS:
            if item["activity"] != activity:
                continue
            cur.execute("""INSERT INTO achievement_progress (user_id, achievement_key, progress)
                VALUES (?, ?, 0) ON CONFLICT(user_id, achievement_key) DO NOTHING""",
                (user_id, item["key"]))
            cur.execute("SELECT progress, completed_at FROM achievement_progress WHERE user_id = ? AND achievement_key = ?",
                        (user_id, item["key"]))
            row = cur.fetchone()
            if not row or row["completed_at"]:
                continue
            new_progress = min(int(item["target"]), int(row["progress"] or 0) + amount)
            completed = new_progress >= int(item["target"])
            cur.execute("""UPDATE achievement_progress SET progress = ?, completed_at = ?
                WHERE user_id = ? AND achievement_key = ? AND completed_at IS NULL""",
                (new_progress, datetime.utcnow().isoformat() if completed else None, user_id, item["key"]))
            if completed and item.get("title_key"):
                cur.execute("""INSERT INTO user_titles (user_id, title_key, title_name, source)
                    VALUES (?, ?, ?, 'achievement') ON CONFLICT(user_id, title_key) DO NOTHING""",
                    (user_id, item["title_key"], item["title_name"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_achievements(user_id):
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT achievement_key, progress, completed_at, claimed_at FROM achievement_progress WHERE user_id = ?",
                (user_id,))
    existing = {row["achievement_key"]: dict(row) for row in cur.fetchall()}
    cur.execute("SELECT title_key, title_name, is_active FROM user_titles WHERE user_id = ? ORDER BY acquired_at DESC",
                (user_id,))
    titles = [dict(row) for row in cur.fetchall()]
    conn.close()
    result = []
    for item in ACHIEVEMENTS:
        row = existing.get(item["key"], {})
        completed = bool(row.get("completed_at"))
        hidden = bool(item.get("secret")) and not completed
        result.append({
            "key": item["key"],
            "title": "???" if hidden else item["title"],
            "description": "Секретное достижение. Найди его сам." if hidden else item["description"],
            "target": int(item["target"]),
            "reward_ldc": 0 if hidden else int(item["reward_ldc"]),
            "progress": 0 if hidden else int(row.get("progress") or 0),
            "completed": completed,
            "claimed": bool(row.get("claimed_at")),
            "secret": bool(item.get("secret")),
        })
    return {"achievements": result, "titles": titles}


def claim_achievement(user_id, achievement_key):
    item = next((x for x in ACHIEVEMENTS if x["key"] == achievement_key), None)
    if not item:
        raise ValueError("Достижение не найдено")
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("""SELECT progress, completed_at, claimed_at FROM achievement_progress
            WHERE user_id = ? AND achievement_key = ? FOR UPDATE""", (user_id, achievement_key))
        row = cur.fetchone()
        if not row or not row["completed_at"]:
            raise ValueError("Достижение ещё не выполнено")
        if row["claimed_at"]:
            raise ValueError("Награда уже получена")
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (int(item["reward_ldc"]), user_id))
        if cur.rowcount != 1:
            raise ValueError("Профиль игрока не найден")
        cur.execute("""UPDATE achievement_progress SET claimed_at = ?
            WHERE user_id = ? AND achievement_key = ? AND claimed_at IS NULL""",
            (datetime.utcnow().isoformat(), user_id, achievement_key))
        if cur.rowcount != 1:
            raise ValueError("Награда уже получена")
        conn.commit()
        return {"ok": True, "reward_ldc": int(item["reward_ldc"]), "title": item.get("title_name")}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_active_title(user_id, title_key):
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT title_name FROM user_titles WHERE user_id = ? AND title_key = ?", (user_id, title_key))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError("Этот титул ещё не открыт")
    cur.execute("UPDATE user_titles SET is_active = FALSE WHERE user_id = ?", (user_id,))
    cur.execute("UPDATE user_titles SET is_active = TRUE WHERE user_id = ? AND title_key = ?", (user_id, title_key))
    conn.commit()
    conn.close()
    return {"ok": True, "title_key": title_key, "title_name": row["title_name"]}


def get_collection(user_id):
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT item_key, item_type, item_name, source, acquired_at FROM user_collection_items WHERE user_id = ? ORDER BY acquired_at DESC",
                (user_id,))
    items = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT title_key, title_name, is_active, acquired_at FROM user_titles WHERE user_id = ? ORDER BY acquired_at DESC",
                (user_id,))
    titles = [dict(row) for row in cur.fetchall()]
    conn.close()
    try:
        pets_raw = bot_db.get_user_pets(user_id)
    except Exception:
        pets_raw = []
    pets = []
    for pet in pets_raw:
        if isinstance(pet, dict):
            pets.append({"name": pet.get("name") or pet.get("pet_name") or "Питомец",
                         "rarity": pet.get("rarity") or "обычный"})
        elif isinstance(pet, (tuple, list)):
            pets.append({"name": str(pet[1] if len(pet) > 1 else "Питомец"),
                         "rarity": str(pet[3] if len(pet) > 3 else "обычный")})
    owned_names = {p["name"] for p in pets}
    bestiary_by_name = {}
    for egg in getattr(bot_db, "PET_EGGS", {}).values():
        for pet in egg.get("pets", []):
            name, rarity = str(pet[0]), str(pet[1])
            bestiary_by_name[name] = {"name": name, "rarity": rarity, "discovered": name in owned_names}
    bestiary = sorted(bestiary_by_name.values(), key=lambda p: (not p["discovered"], p["rarity"], p["name"]))
    return {"items": items, "titles": titles, "pets": pets, "bestiary": bestiary,
            "summary": {"items": len(items), "titles": len(titles), "pets": len(pets),
                        "species_discovered": sum(1 for p in bestiary if p["discovered"]),
                        "species_total": len(bestiary)}}


def claim_season_level(user_id, level):
    level = int(level)
    season = ensure_current_season()
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT xp, level FROM season_progress WHERE season_id = ? AND user_id = ? FOR UPDATE",
                    (season["id"], user_id))
        progress = cur.fetchone()
        if not progress or int(progress["level"] or 0) < level:
            raise ValueError("Этот уровень сезона ещё не достигнут")
        cur.execute("SELECT reward_type, reward_key, amount, description FROM season_level_rewards WHERE season_id = ? AND level = ?",
                    (season["id"], level))
        reward = cur.fetchone()
        if not reward:
            raise ValueError("Для этого уровня нет отдельной награды")
        cur.execute("INSERT INTO season_level_claims (season_id, user_id, level) VALUES (?, ?, ?)",
                    (season["id"], user_id, level))
        reward_type = reward["reward_type"]
        reward_key = reward["reward_key"] or ""
        amount = int(reward["amount"] or 0)
        description = reward["description"]
        if reward_type == "ldc":
            cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
            if cur.rowcount != 1:
                raise ValueError("Профиль игрока не найден")
        elif reward_type == "title":
            title_key = f"{season['season_key']}_{reward_key}"
            cur.execute("""INSERT INTO user_titles (user_id, title_key, title_name, source)
                VALUES (?, ?, ?, 'season') ON CONFLICT(user_id, title_key) DO NOTHING""",
                (user_id, title_key, description.replace("Титул ", "").strip("«»")))
        else:
            item_key = f"{season['season_key']}_{reward_key}"
            if reward_type == "cosmetic":
                cur.execute("""INSERT INTO user_cosmetics_owned (user_id, item_type, item_key)
                    VALUES (?, 'frame', ?) ON CONFLICT(user_id, item_type, item_key) DO NOTHING""",
                    (user_id, reward_key))
            cur.execute("""INSERT INTO user_collection_items (user_id, item_key, item_type, item_name, source)
                VALUES (?, ?, ?, ?, 'season') ON CONFLICT(user_id, item_key) DO NOTHING""",
                (user_id, item_key, reward_type, description))
        conn.commit()
        return {"ok": True, "level": level, "reward_type": reward_type,
                "reward": description, "amount": amount if reward_type == "ldc" else 1}
    except Exception as exc:
        conn.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise ValueError("Награда за этот уровень уже получена") from exc
        raise
    finally:
        conn.close()


PET_EXPEDITION_DESTINATIONS = {
    "forest": {"name": "🌲 Лавандовый лес", "minutes": 30, "min_ldc": 200, "max_ldc": 500,
               "items": ["wildflower", "lavender_leaf"], "description": "Короткая прогулка с небольшими находками."},
    "cave": {"name": "💎 Кристальная пещера", "minutes": 60, "min_ldc": 500, "max_ldc": 1200,
             "items": ["cave_crystal", "old_coin"], "description": "Опасный маршрут с шансом найти редкий предмет."},
    "island": {"name": "🏝️ Затерянный остров", "minutes": 120, "min_ldc": 1000, "max_ldc": 2500,
               "items": ["sea_pearl", "ancient_relic"], "description": "Долгая экспедиция за ценными находками."},
}
PET_EXPEDITION_ITEM_NAMES = {
    "wildflower": "Лесной цветок",
    "lavender_leaf": "Лавандовый лист",
    "cave_crystal": "Кристалл пещеры",
    "old_coin": "Старинная монета",
    "sea_pearl": "Морская жемчужина",
    "ancient_relic": "Древняя реликвия",
}


def list_pet_expeditions(user_id):
    now = datetime.utcnow()
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT id, pet_name, pet_emoji, rarity FROM pets WHERE user_id = ? AND is_active = TRUE ORDER BY id",
                (user_id,))
    pets = [dict(row) for row in cur.fetchall()]
    cur.execute("""SELECT e.*, p.pet_name, p.pet_emoji, p.rarity
        FROM pet_expeditions e JOIN pets p ON p.id = e.pet_id
        WHERE e.user_id = ? ORDER BY e.id DESC LIMIT 20""", (user_id,))
    expeditions = []
    for row in cur.fetchall():
        item = dict(row)
        ends = _dt(item.get("ends_at"))
        item["remaining_seconds"] = max(0, int((ends - now).total_seconds())) if ends else 0
        item["can_claim"] = item.get("status") == "active" and item["remaining_seconds"] == 0
        expeditions.append(item)
    conn.close()
    return {
        "pets": pets,
        "expeditions": expeditions,
        "destinations": [{"key": key, **value} for key, value in PET_EXPEDITION_DESTINATIONS.items()],
    }


def start_pet_expedition(user_id, pet_id, destination):
    destination = str(destination or "")
    if destination not in PET_EXPEDITION_DESTINATIONS:
        raise ValueError("Неизвестный маршрут экспедиции")
    try:
        pet_id = int(pet_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректный питомец") from exc
    destination_info = PET_EXPEDITION_DESTINATIONS[destination]
    now = datetime.utcnow()
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, pet_name FROM pets WHERE id = ? AND user_id = ? AND is_active = TRUE FOR UPDATE",
                    (pet_id, user_id))
        pet = cur.fetchone()
        if not pet:
            raise ValueError("Питомец не найден или не принадлежит тебе")
        cur.execute("SELECT id FROM pet_expeditions WHERE user_id = ? AND status = 'active' LIMIT 1 FOR UPDATE",
                    (user_id,))
        active = cur.fetchone()
        if active:
            raise ValueError("Сначала забери награду за предыдущую экспедицию")
        minutes = int(destination_info["minutes"])
        reward_ldc = random.randint(int(destination_info["min_ldc"]), int(destination_info["max_ldc"]))
        reward_item = random.choice(destination_info["items"]) if random.random() < 0.35 else None
        outcome = random.choice([
            "Питомец нашёл следы древней магии.",
            "Питомец вернулся с необычной находкой.",
            "Питомец исследовал новый маршрут.",
            "Питомец удачно избежал опасности.",
        ])
        end_at = now + timedelta(minutes=minutes)
        cur.execute("""INSERT INTO pet_expeditions
            (user_id, pet_id, destination, status, started_at, ends_at, reward_ldc, reward_item, outcome)
            VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
            (user_id, pet_id, destination, now.isoformat(), end_at.isoformat(), reward_ldc, reward_item, outcome))
        expedition_id = cur.lastrowid
        conn.commit()
        return {"ok": True, "id": expedition_id, "pet_name": pet["pet_name"],
                "destination": destination_info["name"], "duration_minutes": minutes,
                "ends_at": end_at.isoformat()}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_pet_expedition(user_id, expedition_id):
    try:
        expedition_id = int(expedition_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректная экспедиция") from exc
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("""SELECT * FROM pet_expeditions WHERE id = ? AND user_id = ? FOR UPDATE""",
                    (expedition_id, user_id))
        row = cur.fetchone()
        if not row:
            raise ValueError("Экспедиция не найдена")
        if row["status"] != "active":
            raise ValueError("Награда за экспедицию уже получена")
        if not _dt(row["ends_at"]) or _dt(row["ends_at"]) > datetime.utcnow():
            raise ValueError("Питомец ещё в пути")
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (int(row["reward_ldc"]), user_id))
        if cur.rowcount != 1:
            raise ValueError("Профиль игрока не найден")
        item_name = None
        if row["reward_item"]:
            item_key = f"pet_expedition_{expedition_id}_{row['reward_item']}"
            item_name = PET_EXPEDITION_ITEM_NAMES.get(row["reward_item"], "Редкая находка")
            cur.execute("""INSERT INTO user_collection_items (user_id, item_key, item_type, item_name, source)
                VALUES (?, ?, 'expedition', ?, 'pet_expedition') ON CONFLICT(user_id, item_key) DO NOTHING""",
                (user_id, item_key, item_name))
        cur.execute("""UPDATE pet_expeditions SET status = 'claimed', claimed_at = ?
            WHERE id = ? AND user_id = ? AND status = 'active'""",
            (datetime.utcnow().isoformat(), expedition_id, user_id))
        if cur.rowcount != 1:
            raise ValueError("Награда за экспедицию уже получена")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    add_season_xp(user_id, 100)
    record_season_activity(user_id, "pet", 1)
    return {"ok": True, "reward_ldc": int(row["reward_ldc"]), "reward_item": item_name,
            "outcome": row["outcome"]}


SYSTEM_ITEM_NAMES = {
    "wood": "🪵 Дерево", "stone": "🪨 Камень", "ore": "⛓️ Руда",
    "fiber": "🧵 Волокно", "crops": "🌾 Урожай", "fish": "🐟 Рыба",
    "seeds": "🌱 Семена", "tools": "🔧 Инструменты", "gears": "⚙️ Механизмы",
    "maps": "🗺️ Карты", "trade_goods": "📦 Торговые товары", "meals": "🍲 Еда",
    "decor": "🖼️ Декор", "photos": "📸 Фотографии", "pet_treats": "🦴 Лакомства",
    "circuits": "🔌 Платы", "crystal": "💎 Кристаллы", "artifacts": "🏺 Артефакты",
}
SYSTEM_MARKET_PRICES = {
    "wood": 40, "stone": 35, "ore": 80, "fiber": 30, "crops": 25, "fish": 60,
    "seeds": 30, "tools": 250, "gears": 200, "maps": 300, "trade_goods": 120,
    "meals": 150, "decor": 100, "photos": 75, "pet_treats": 100,
    "circuits": 220, "crystal": 500, "artifacts": 750,
}
SYSTEM_MARKET_OFFERS = {
    "seed_bundle": {"name": "🌱 Набор семян", "price": 500, "items": {"seeds": 5}},
    "meal_bundle": {"name": "🍲 Провизия", "price": 1200, "items": {"meals": 3}},
    "map_bundle": {"name": "🗺️ Набор карт", "price": 2000, "items": {"maps": 2}},
    "pet_treat_bundle": {"name": "🦴 Лакомства питомцам", "price": 800, "items": {"pet_treats": 3}},
    "crystal_bundle": {"name": "💎 Кристалл экспедиции", "price": 5000, "items": {"crystal": 1}},
}


def get_system_market(user_id):
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT item_key, quantity FROM inventory_items WHERE user_id = ? AND quantity > 0",
                (user_id,))
    quantities = {row["item_key"]: int(row["quantity"]) for row in cur.fetchall()}
    conn.close()
    inventory = [
        {"key": key, "name": name, "quantity": quantities.get(key, 0),
         "sell_price": SYSTEM_MARKET_PRICES[key]}
        for key, name in SYSTEM_ITEM_NAMES.items() if quantities.get(key, 0) > 0
    ]
    offers = [{"key": key, "name": value["name"], "price": value["price"],
               "items": [{"name": SYSTEM_ITEM_NAMES.get(item, item), "quantity": amount}
                         for item, amount in value["items"].items()]}
              for key, value in SYSTEM_MARKET_OFFERS.items()]
    return {"inventory": inventory, "offers": offers}


def buy_system_market_offer(user_id, offer_key):
    offer = SYSTEM_MARKET_OFFERS.get(str(offer_key or ""))
    if not offer:
        raise ValueError("Предложение не найдено")
    active_event = get_current_event()
    discount = 15 if active_event and active_event.get("event_type") == "traveling_merchant" else 0
    price = int(offer["price"] * (100 - discount) / 100)
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT balance FROM users WHERE user_id = ? FOR UPDATE", (user_id,))
        user = cur.fetchone()
        if not user:
            raise ValueError("Профиль игрока не найден")
        if int(user["balance"] or 0) < price:
            raise ValueError(f"Недостаточно LDC. Нужно {price:,}")
        cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
                    (price, user_id, price))
        if cur.rowcount != 1:
            raise ValueError("Баланс изменился. Попробуй ещё раз")
        for item_key, quantity in offer["items"].items():
            cur.execute("""INSERT INTO inventory_items (user_id, item_key, quantity)
                VALUES (?, ?, ?) ON CONFLICT(user_id, item_key) DO UPDATE SET
                quantity = inventory_items.quantity + excluded.quantity""",
                (user_id, item_key, int(quantity)))
        conn.commit()
        record_season_activity(user_id, "collect", 1)
        return {"ok": True, "offer": offer["name"], "price": price, "discount_percent": discount}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sell_system_market_item(user_id, item_key, amount=1):
    item_key = str(item_key or "")
    if item_key not in SYSTEM_MARKET_PRICES:
        raise ValueError("Этот предмет нельзя продать системе")
    try:
        amount = int(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректное количество") from exc
    if amount < 1 or amount > 100:
        raise ValueError("За одну операцию можно продать от 1 до 100 предметов")
    price = SYSTEM_MARKET_PRICES[item_key] * amount
    conn = _db()
    cur = conn.cursor()
    try:
        cur.execute("""UPDATE inventory_items SET quantity = quantity - ?
            WHERE user_id = ? AND item_key = ? AND quantity >= ?""",
            (amount, user_id, item_key, amount))
        if cur.rowcount != 1:
            raise ValueError("Недостаточно предметов для продажи")
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (price, user_id))
        if cur.rowcount != 1:
            raise ValueError("Профиль игрока не найден")
        cur.execute("DELETE FROM inventory_items WHERE user_id = ? AND item_key = ? AND quantity <= 0",
                    (user_id, item_key))
        conn.commit()
        return {"ok": True, "item": SYSTEM_ITEM_NAMES[item_key], "amount": amount, "reward_ldc": price}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

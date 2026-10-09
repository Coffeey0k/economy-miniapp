"""
Тех.админ-панель ParadiseCoin.

Доступ только для TECH_ADMIN_IDS.
Тех.админы — «пизже влада»: всё могут, всё видят, ничего не подтверждают.
Их действия логируются, но логи видят ТОЛЬКО они между собой.
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
import database as db

router = Router()


# ==================== FSM ====================

class TechStates(StatesGroup):
    waiting_log_user = State()
    waiting_log_type = State()
    waiting_balance_user = State()
    waiting_balance_amount = State()
    waiting_purchase_user = State()
    waiting_clan_id = State()
    waiting_island_user = State()
    waiting_income_user = State()


# ==================== ПРОВЕРКА ====================

def _guard(callback_or_message):
    """Пропускает только тех.админов."""
    uid = callback_or_message.from_user.id
    return config.is_tech_admin(uid)


# ==================== ГЛАВНАЯ ====================

def tech_panel_menu() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🧪 Тест-режим", callback_data="tech_test_mode"))
    builder.row(InlineKeyboardButton(text="📜 Логи действий", callback_data="tech_logs_menu"))
    builder.row(InlineKeyboardButton(text="🔍 Найти игрока", callback_data="tech_find_user"))
    builder.row(InlineKeyboardButton(text="🛍️ Покупки игрока", callback_data="tech_purchases_start"))
    builder.row(InlineKeyboardButton(text="💰 Баланс игрока", callback_data="tech_balance_start"))
    builder.row(InlineKeyboardButton(text="🏛️ Кланы", callback_data="tech_clans_menu"))
    builder.row(InlineKeyboardButton(text="🏝️ Острова", callback_data="tech_islands_menu"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder


@router.callback_query(F.data == "tech_panel")
async def tech_panel(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    uid = callback.from_user.id
    test = db.is_test_mode(uid)
    test_status = "🟢 ВКЛ" if test else "🔴 ВЫКЛ"

    await callback.message.edit_text(
        f"🧪 ТЕХ.АДМИН-ПАНЕЛЬ\n\n"
        f"👤 Вы: {uid}\n"
        f"🧪 Тест-режим: {test_status}\n\n"
        f"Выберите действие:",
        reply_markup=tech_panel_menu().as_markup(),
        parse_mode=None
    )
    await callback.answer()


# ==================== ТЕСТ-РЕЖИМ ====================

@router.callback_query(F.data == "tech_test_mode")
async def tech_test_mode(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    uid = callback.from_user.id
    current = db.is_test_mode(uid)
    new_value = not current
    db.set_test_mode(uid, new_value, added_by=uid)

    await callback.answer(
        f"🧪 Тест-режим {'ВКЛЮЧЁН' if new_value else 'ВЫКЛЮЧЕН'}\n"
        f"{'Монеты не тратятся.' if new_value else 'Монеты снова тратятся.'}",
        show_alert=True
    )
    await tech_panel(callback)


# ==================== ЛОГИ ====================

def tech_logs_menu() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📜 Все (50 последних)", callback_data="tech_logs_all"))
    builder.row(InlineKeyboardButton(text="🛍️ Покупки", callback_data="tech_logs_type_purchase"))
    builder.row(InlineKeyboardButton(text="💸 Переводы", callback_data="tech_logs_type_transfer"))
    builder.row(InlineKeyboardButton(text="💰 Доходы (работа/остров)", callback_data="tech_logs_type_income"))
    builder.row(InlineKeyboardButton(text="🎡/🥚 Колесо и яйца", callback_data="tech_logs_type_wheel"))
    builder.row(InlineKeyboardButton(text="🔍 По игроку", callback_data="tech_logs_user"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_panel"))
    return builder


@router.callback_query(F.data == "tech_logs_menu")
async def tech_logs_menu_handler(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "📜 Логи действий\n\nВыберите фильтр:",
        reply_markup=tech_logs_menu().as_markup(),
        parse_mode=None
    )
    await callback.answer()


def _format_log_entry(entry: dict) -> str:
    """Одна строка лога."""
    uid = entry["user_id"]
    uname = f"@{uid}"
    try:
        u = db.get_user(uid)
        if u and u.get("username"):
            uname = f"@{u['username']}"
    except Exception:
        pass

    ts = (entry.get("created_at") or "")[:16]
    atype = db.LOG_TYPES.get(entry["action_type"], entry["action_type"])
    amt = entry.get("amount", 0)
    amt_str = f" [{amt:+,} 🪙]" if amt else ""
    det = entry.get("details") or ""

    line = f"• [{ts}] {uname} — {atype}{amt_str}"
    if det:
        line += f"\n   _{det}_"
    return line


@router.callback_query(F.data == "tech_logs_all")
async def tech_logs_all(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    entries = db.get_action_log(limit=50)
    if not entries:
        text = "📜 Логи пусты."
    else:
        text = f"📜 Последние {len(entries)} действий:\n\n"
        for e in entries:
            text += _format_log_entry(e) + "\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="tech_logs_all"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_logs_menu"))

    await callback.message.edit_text(text[:4000], reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("tech_logs_type_"))
async def tech_logs_type(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    t = callback.data.replace("tech_logs_type_", "")

    # Группы типов
    groups = {
        "purchase": ["purchase"],
        "transfer": ["transfer"],
        "income":   ["work", "bonus", "island_income"],
        "wheel":    ["wheel", "egg", "promo"],
    }

    types = groups.get(t, [t])
    entries = []
    for tt in types:
        entries += db.get_action_log(filter_type=tt, limit=30)

    entries.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    entries = entries[:50]

    if not entries:
        text = f"📜 По фильтру «{t}» пусто."
    else:
        text = f"📜 {db.LOG_TYPES.get(t, t)} — {len(entries)} записей:\n\n"
        for e in entries:
            text += _format_log_entry(e) + "\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_logs_menu"))

    await callback.message.edit_text(text[:4000], reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "tech_logs_user")
async def tech_logs_user_start(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "🔍 Введите username (без @) или ID игрока:",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ Отмена", callback_data="tech_panel")
        ).as_markup()
    )
    await state.set_state(TechStates.waiting_log_user)
    await callback.answer()


@router.message(TechStates.waiting_log_user)
async def tech_logs_user_show(message: Message, state: FSMContext):
    if not config.is_tech_admin(message.from_user.id):
        return

    query = message.text.strip().lstrip("@")
    user = _find_user(query)
    if not user:
        await message.answer("❌ Не найден")
        await state.clear()
        return

    uid = user["user_id"]
    entries = db.get_user_actions(uid, limit=30)

    uname = f"@{user['username']}" if user["username"] else f"ID {uid}"
    if not entries:
        text = f"📜 У {uname} нет действий в логе."
    else:
        text = f"📜 Действия {uname} ({len(entries)}):\n\n"
        for e in entries:
            text += _format_log_entry(e) + "\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 В логи", callback_data="tech_logs_menu"))

    await message.answer(text[:4000], reply_markup=builder.as_markup(), parse_mode="Markdown")
    await state.clear()


# ==================== ПОИСК ИГРОКА ====================

@router.callback_query(F.data == "tech_find_user")
async def tech_find_user_start(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "🔍 Введите username (без @) или ID игрока:",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ Отмена", callback_data="tech_panel")
        ).as_markup()
    )
    await state.set_state(TechStates.waiting_balance_user)
    await callback.answer()


@router.message(TechStates.waiting_balance_user)
async def tech_show_user(message: Message, state: FSMContext):
    if not config.is_tech_admin(message.from_user.id):
        return

    query = message.text.strip().lstrip("@")
    user = _find_user(query)
    if not user:
        await message.answer("❌ Не найден")
        await state.clear()
        return

    uid = user["user_id"]
    uname = f"@{user['username']}" if user["username"] else f"ID {uid}"

    purchases = db.get_user_purchases(uid)
    pets = db.get_user_pets(uid)
    prof = db.get_profession(uid)
    clan = db.clan_get_user_clan(uid)
    island = db.island_get(uid)
    rcc_stats = db.rcc_get_stats(uid)

    text = (
        f"👤 {uname}\n"
        f"💰 Баланс: {user['balance']:,} 🪙\n"
        f"⚠️ Варны: {db.get_warn_count(uid)}\n"
        f"🎮 Игр: {_count_games(uid)}\n"
        f"🐾 Питомцев: {len(pets)}\n"
    )
    if prof and prof["profession"]:
        text += f"💼 Профессия: {prof['profession']} (ур. {prof['level']})\n"
    if clan:
        text += f"🏛️ Клан: {clan['emoji']} {clan['name']} ({clan['role']})\n"
    if island["has_island"]:
        builds = []
        if island["has_house"]: builds.append("🏠")
        if island["has_pier"]: builds.append("⚓")
        if island["has_ship"]: builds.append("🚢")
        text += f"🏝️ Остров: {' '.join(builds) if builds else 'пусто'}\n"
    text += f"💎 RCC: {rcc_stats['balance']:.2f}\n"
    if purchases:
        text += f"\n🛍️ Покупок: {len(purchases)}\n"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Изменить баланс", callback_data=f"tech_balance_edit_{uid}"),
        InlineKeyboardButton(text="🛍️ Покупки", callback_data=f"tech_purchases_user_{uid}")
    )
    builder.row(
        InlineKeyboardButton(text="📜 Логи игрока", callback_data=f"tech_logs_user_id_{uid}"),
        InlineKeyboardButton(text="🗑️ Аннулировать всё", callback_data=f"tech_wipe_all_{uid}")
    )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_panel"))

    await message.answer(text, reply_markup=builder.as_markup())
    await state.clear()


# ==================== БАЛАНС ====================

@router.callback_query(F.data == "tech_balance_start")
async def tech_balance_start(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "💰 Введите username (без @) или ID игрока:",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ Отмена", callback_data="tech_panel")
        ).as_markup()
    )
    await state.set_state(TechStates.waiting_balance_amount)
    await callback.answer()


@router.message(TechStates.waiting_balance_amount)
async def tech_balance_user(message: Message, state: FSMContext):
    if not config.is_tech_admin(message.from_user.id):
        return

    query = message.text.strip().lstrip("@")
    user = _find_user(query)
    if not user:
        await message.answer("❌ Не найден")
        await state.clear()
        return

    await state.update_data(target_id=user["user_id"])
    await message.answer(
        f"💰 Баланс @{user['username'] or user['user_id']}: {user['balance']:,} 🪙\n\n"
        f"Введите новое значение (просто число):"
    )
    await state.set_state(TechStates.waiting_balance_user)


@router.message(TechStates.waiting_balance_user)
async def tech_balance_set(message: Message, state: FSMContext):
    if not config.is_tech_admin(message.from_user.id):
        return

    try:
        new_balance = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите целое число")
        return

    if new_balance < 0:
        await message.answer("❌ Баланс не может быть отрицательным")
        return

    data = await state.get_data()
    target_id = data["target_id"]

    # Устанавливаем напрямую
    conn = db.sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, target_id))
    conn.commit()
    conn.close()

    db.log_action(target_id, "tech_action",
                  f"Баланс установлен вручную тех.админом {message.from_user.id}: {new_balance}",
                  amount=new_balance)

    await message.answer(
        f"✅ Баланс установлен: {new_balance:,} 🪙",
        reply_markup=tech_panel_menu().as_markup()
    )
    await state.clear()


@router.callback_query(F.data.startswith("tech_balance_edit_"))
async def tech_balance_edit(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    target_id = int(callback.data.replace("tech_balance_edit_", ""))
    user = db.get_user(target_id)
    if not user:
        await callback.answer("❌ Не найден", show_alert=True)
        return

    await state.update_data(target_id=target_id)
    await callback.message.edit_text(
        f"💰 Баланс @{user['username'] or target_id}: {user['balance']:,} 🪙\n\n"
        f"Введите новое значение:"
    )
    await state.set_state(TechStates.waiting_balance_user)
    await callback.answer()


# ==================== ПОКУПКИ ====================

@router.callback_query(F.data == "tech_purchases_start")
async def tech_purchases_start(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "🛍️ Введите username (без @) или ID игрока:",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ Отмена", callback_data="tech_panel")
        ).as_markup()
    )
    await state.set_state(TechStates.waiting_purchase_user)
    await callback.answer()


@router.message(TechStates.waiting_purchase_user)
async def tech_purchases_user(message: Message, state: FSMContext):
    if not config.is_tech_admin(message.from_user.id):
        return

    query = message.text.strip().lstrip("@")
    user = _find_user(query)
    if not user:
        await message.answer("❌ Не найден")
        await state.clear()
        return

    await _show_purchases(message, user["user_id"])
    await state.clear()


@router.callback_query(F.data.startswith("tech_purchases_user_"))
async def tech_purchases_user_cb(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    target_id = int(callback.data.replace("tech_purchases_user_", ""))
    await _show_purchases(callback.message, target_id)
    await callback.answer()


async def _show_purchases(message: Message, user_id: int):
    purchases = db.get_purchases_full(user_id)
    if not purchases:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_panel"))
        await message.edit_text("🛍️ У игрока нет покупок.", reply_markup=builder.as_markup())
        return

    builder = InlineKeyboardBuilder()
    text = f"🛍️ Покупки игрока (id={user_id}):\n\n"
    for pid, itype, is_used in purchases:
        status = "✅" if is_used else "⏳"
        name = db.get_item_name(itype)
        text += f"{status} {name} (id={pid})\n"
        builder.row(
            InlineKeyboardButton(text=f"🗑️ Аннулировать {name}", callback_data=f"tech_wipe_purchase_{pid}"),
        )
    builder.row(InlineKeyboardButton(text="🗑️ Все без возврата", callback_data=f"tech_wipe_all_purch_{user_id}_0"))
    builder.row(InlineKeyboardButton(text="♻️ Все с возвратом", callback_data=f"tech_wipe_all_purch_{user_id}_1"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_panel"))

    await message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("tech_wipe_purchase_"))
async def tech_wipe_purchase(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    pid = int(callback.data.replace("tech_wipe_purchase_", ""))

    # Спрашиваем про возврат
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🗑️ Без возврата", callback_data=f"tech_wipe_purch_confirm_{pid}_0"),
        InlineKeyboardButton(text="♻️ С возвратом", callback_data=f"tech_wipe_purch_confirm_{pid}_1")
    )
    builder.row(InlineKeyboardButton(text="🔙 Отмена", callback_data="tech_panel"))

    await callback.message.edit_text(
        f"Аннулировать покупку #{pid}?\n\nС возвратом монет игроку или без?",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tech_wipe_purch_confirm_"))
async def tech_wipe_purch_confirm(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    parts = callback.data.replace("tech_wipe_purch_confirm_", "").split("_")
    pid = int(parts[0])
    refund = bool(int(parts[1]))

    ok = db.admin_wipe_purchase(pid, refund=refund)
    if ok:
        await callback.answer("✅ Аннулировано", show_alert=True)
    else:
        await callback.answer("❌ Ошибка", show_alert=True)

    await callback.message.edit_text(
        "Готово.",
        reply_markup=tech_panel_menu().as_markup()
    )


@router.callback_query(F.data.startswith("tech_wipe_all_purch_"))
async def tech_wipe_all_purch(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    parts = callback.data.replace("tech_wipe_all_purch_", "").split("_")
    uid = int(parts[0])
    refund = bool(int(parts[1]))

    count = db.admin_wipe_user_purchases(uid, refund=refund)
    await callback.answer(f"✅ Удалено: {count}", show_alert=True)
    await callback.message.edit_text(
        f"Удалено покупок: {count}",
        reply_markup=tech_panel_menu().as_markup()
    )


# ==================== ПОЛНЫЙ СБРОС ====================

@router.callback_query(F.data.startswith("tech_wipe_all_"))
async def tech_wipe_all(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    uid = int(callback.data.replace("tech_wipe_all_", ""))

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🛍️ Только покупки", callback_data=f"tech_wipe_all_purch_{uid}_0"))
    builder.row(InlineKeyboardButton(text="🏝️ Остров", callback_data=f"tech_island_wipe_{uid}_all"))
    builder.row(InlineKeyboardButton(text="💰 Снять доход острова", callback_data=f"tech_income_wipe_{uid}"))
    builder.row(InlineKeyboardButton(text="🏛️ Выйти из клана", callback_data=f"tech_clan_kick_{uid}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_panel"))

    await callback.message.edit_text(
        f"🗑️ Что аннулировать у игрока ID {uid}?",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


# ==================== КЛАНЫ ====================

def tech_clans_menu() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📋 Все кланы", callback_data="tech_clans_all"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_panel"))
    return builder


@router.callback_query(F.data == "tech_clans_menu")
async def tech_clans_menu_h(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🏛️ Управление кланами",
        reply_markup=tech_clans_menu().as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "tech_clans_all")
async def tech_clans_all(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    top = db.clan_top(30)
    if not top:
        await callback.message.edit_text(
            "🏛️ Кланов нет.",
            reply_markup=tech_clans_menu().as_markup()
        )
        await callback.answer()
        return

    text = "🏛️ Все кланы:\n\n"
    builder = InlineKeyboardBuilder()
    for c in top:
        text += f"{c['emoji']} {c['name']} (id={c['id']}) — {c['bank']:,} 🪙, {c['members']} чел.\n"
        builder.row(InlineKeyboardButton(
            text=f"🗑️ Распустить {c['name']}",
            callback_data=f"tech_clan_disband_{c['id']}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_clans_menu"))

    await callback.message.edit_text(text[:4000], reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("tech_clan_disband_"))
async def tech_clan_disband(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    clan_id = int(callback.data.replace("tech_clan_disband_", ""))

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🗑️ Удалить, казна лидеру", callback_data=f"tech_clan_wipe_{clan_id}_1"),
        InlineKeyboardButton(text="💥 Удалить, казна в никуда", callback_data=f"tech_clan_wipe_{clan_id}_0")
    )
    builder.row(InlineKeyboardButton(text="💰 Просто забрать казну", callback_data=f"tech_clan_bank_{clan_id}"))
    builder.row(InlineKeyboardButton(text="🔙 Отмена", callback_data="tech_clans_all"))

    await callback.message.edit_text(
        f"Что сделать с кланом #{clan_id}?",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tech_clan_wipe_"))
async def tech_clan_wipe(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    parts = callback.data.replace("tech_clan_wipe_", "").split("_")
    clan_id = int(parts[0])
    to_leader = bool(int(parts[1]))

    ok = db.admin_wipe_clan(clan_id, give_bank_to_leader=to_leader)
    await callback.answer("✅ Клан распущен" if ok else "❌ Ошибка", show_alert=True)
    await callback.message.edit_text(
        "Готово.",
        reply_markup=tech_panel_menu().as_markup()
    )


@router.callback_query(F.data.startswith("tech_clan_bank_"))
async def tech_clan_bank(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    clan_id = int(callback.data.replace("tech_clan_bank_", ""))
    ok = db.admin_take_clan_bank(clan_id)
    await callback.answer("✅ Казна забрана" if ok else "❌ Ошибка", show_alert=True)
    await callback.message.edit_text(
        "Готово.",
        reply_markup=tech_panel_menu().as_markup()
    )


@router.callback_query(F.data.startswith("tech_clan_kick_"))
async def tech_clan_kick_user(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    uid = int(callback.data.replace("tech_clan_kick_", ""))
    clan = db.clan_get_user_clan(uid)
    if not clan:
        await callback.answer("❌ Игрок не в клане", show_alert=True)
        return

    # Просто выкидываем из клана
    conn = db.sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM clan_members WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()

    db.log_action(uid, "tech_action", f"Тех.админ выкинул из клана {clan['name']}")
    await callback.answer(f"✅ Кикнут из {clan['name']}", show_alert=True)
    await callback.message.edit_text(
        "Готово.",
        reply_markup=tech_panel_menu().as_markup()
    )


# ==================== ОСТРОВА ====================

def tech_islands_menu() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔍 Найти остров игрока", callback_data="tech_island_find"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_panel"))
    return builder


@router.callback_query(F.data == "tech_islands_menu")
async def tech_islands_menu_h(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🏝️ Управление островами",
        reply_markup=tech_islands_menu().as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "tech_island_find")
async def tech_island_find(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "🏝️ Введите username (без @) или ID игрока:",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ Отмена", callback_data="tech_islands_menu")
        ).as_markup()
    )
    await state.set_state(TechStates.waiting_island_user)
    await callback.answer()


@router.message(TechStates.waiting_island_user)
async def tech_island_show(message: Message, state: FSMContext):
    if not config.is_tech_admin(message.from_user.id):
        return

    query = message.text.strip().lstrip("@")
    user = _find_user(query)
    if not user:
        await message.answer("❌ Не найден")
        await state.clear()
        return

    uid = user["user_id"]
    island = db.island_get(uid)

    if not island["has_island"]:
        await message.answer("❌ У игрока нет острова.")
        await state.clear()
        return

    text = (
        f"🏝️ Остров игрока @{user['username'] or uid}\n\n"
        f"🏝️ Остров: {'✅' if island['has_island'] else '❌'}\n"
        f"🏠 Домик: {'✅' if island['has_house'] else '❌'}\n"
        f"⚓ Причал: {'✅' if island['has_pier'] else '❌'}\n"
        f"🚢 Корабль: {'✅' if island['has_ship'] else '❌'}\n"
    )

    builder = InlineKeyboardBuilder()
    if island["has_house"]:
        builder.row(InlineKeyboardButton(text="🗑️ Удалить домик", callback_data=f"tech_island_wipe_{uid}_house"))
    if island["has_pier"]:
        builder.row(InlineKeyboardButton(text="🗑️ Удалить причал", callback_data=f"tech_island_wipe_{uid}_pier"))
    if island["has_ship"]:
        builder.row(InlineKeyboardButton(text="🗑️ Удалить корабль", callback_data=f"tech_island_wipe_{uid}_ship"))
    builder.row(InlineKeyboardButton(text="💰 Снять последний доход", callback_data=f"tech_income_wipe_{uid}"))
    builder.row(InlineKeyboardButton(text="💥 Удалить остров полностью", callback_data=f"tech_island_wipe_{uid}_island"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_islands_menu"))

    await message.answer(text, reply_markup=builder.as_markup())
    await state.clear()


@router.callback_query(F.data.startswith("tech_island_wipe_"))
async def tech_island_wipe(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    parts = callback.data.replace("tech_island_wipe_", "").split("_")
    uid = int(parts[0])
    item = parts[1]

    ok = db.admin_wipe_island(uid, item=None if item == "all" else item)
    await callback.answer("✅ Удалено" if ok else "❌ Ошибка", show_alert=True)
    await callback.message.edit_text(
        "Готово.",
        reply_markup=tech_panel_menu().as_markup()
    )


@router.callback_query(F.data.startswith("tech_income_wipe_"))
async def tech_income_wipe(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    uid = int(callback.data.replace("tech_income_wipe_", ""))
    ok = db.admin_wipe_last_island_income(uid)
    await callback.answer("✅ Доход снят" if ok else "❌ Ошибка", show_alert=True)
    await callback.message.edit_text(
        "Готово.",
        reply_markup=tech_panel_menu().as_markup()
    )


# ==================== ЛОГИ ИГРОКА ПО ID ====================

@router.callback_query(F.data.startswith("tech_logs_user_id_"))
async def tech_logs_user_id(callback: CallbackQuery):
    if not _guard(callback):
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    uid = int(callback.data.replace("tech_logs_user_id_", ""))
    entries = db.get_user_actions(uid, limit=30)

    if not entries:
        text = "📜 Действий нет."
    else:
        text = f"📜 Действия игрока ID {uid}:\n\n"
        for e in entries:
            text += _format_log_entry(e) + "\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tech_panel"))

    await callback.message.edit_text(text[:4000], reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================

def _find_user(query: str):
    """Ищет по ID или username (без @)."""
    conn = db.sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    if query.isdigit():
        cur.execute("SELECT user_id, username, balance FROM users WHERE user_id = ?", (int(query),))
    else:
        cur.execute("SELECT user_id, username, balance FROM users WHERE username = ?", (query,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"user_id": row[0], "username": row[1], "balance": row[2]}
    return None


def _count_games(user_id: int) -> int:
    conn = db.sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT games_played FROM game_stats WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0

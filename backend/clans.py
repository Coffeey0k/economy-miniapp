"""
Кланы ParadiseCoin — бот.

Команды:
- `клан` — открывает меню клана
- `создать клан Название | эмодзи | описание` — создать клан
- `кланы` — топ кланов
"""

import sqlite3
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
import database as db

router = Router()


class ClanStates(StatesGroup):
    waiting_name = State()
    waiting_emoji = State()
    waiting_description = State()
    waiting_deposit = State()
    waiting_kick = State()


# ==================== ГЛАВНОЕ МЕНЮ ====================

def clan_main_menu(clan: dict) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💰 Внести в казну", callback_data="clan_deposit"))
    if clan["role"] in ("leader", "deputy"):
        builder.row(InlineKeyboardButton(text="👥 Участники", callback_data="clan_members"))
        builder.row(InlineKeyboardButton(text="📩 Заявки", callback_data="clan_requests"))
    if clan["role"] == "leader":
        builder.row(InlineKeyboardButton(text="🥾 Кикнуть игрока", callback_data="clan_kick_start"))
        builder.row(InlineKeyboardButton(text="💥 Распустить клан", callback_data="clan_disband"))
    else:
        builder.row(InlineKeyboardButton(text="🚪 Выйти из клана", callback_data="clan_leave"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder


@router.message(F.text.lower() == "клан")
async def clan_word(message: Message):
    user_id = message.from_user.id
    clan = db.clan_get_user_clan(user_id)

    if not clan:
        # Нет клана — предложить создать или вступить
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text=f"🏛️ Создать клан — {db.CLAN_CREATE_COST:,} 🪙",
            callback_data="clan_create_start"
        ))
        builder.row(InlineKeyboardButton(text="📋 Список кланов", callback_data="clan_list"))
        builder.row(InlineKeyboardButton(text="🏆 Топ кланов", callback_data="clan_top"))

        await message.answer(
            f"🏛️ Ты пока не в клане.\n\n"
            f"Создай свой клан за {db.CLAN_CREATE_COST:,} 🪙 "
            f"или вступи в существующий.",
            reply_markup=builder.as_markup()
        )
        return

    members = db.clan_get_members(clan["id"])
    role_names = {"leader": "👑 Лидер", "deputy": "⭐ Зам", "member": "👤 Участник"}

    text = (
        f"{clan['emoji']} Клан «{clan['name']}»\n\n"
        f"💰 Казна: {clan['bank']:,} 🪙\n"
        f"👥 Участников: {len(members)}/{db.CLAN_MAX_MEMBERS}\n"
        f"🎖 Твоя роль: {role_names.get(clan['role'], clan['role'])}\n"
    )
    if clan["description"]:
        text += f"\n📖 {clan['description']}\n"

    await message.answer(text, reply_markup=clan_main_menu(clan).as_markup())


# ==================== СОЗДАНИЕ КЛАНА ====================

@router.callback_query(F.data == "clan_create_start")
async def clan_create_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if db.get_balance(user_id) < db.CLAN_CREATE_COST:
        await callback.answer(
            f"❌ Недостаточно монет. Нужно: {db.CLAN_CREATE_COST:,} 🪙",
            show_alert=True
        )
        return
    if db.clan_get_user_clan(user_id):
        await callback.answer("Ты уже в клане", show_alert=True)
        return

    await callback.message.edit_text(
        f"🏛️ Создание клана\n\n"
        f"Стоимость: {db.CLAN_CREATE_COST:,} 🪙\n\n"
        f"Введи название клана (3–20 символов):"
    )
    await state.set_state(ClanStates.waiting_name)
    await callback.answer()


@router.message(ClanStates.waiting_name)
async def clan_name_entered(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 3 or len(name) > 20:
        await message.answer("❌ Название должно быть 3–20 символов. Попробуй снова:")
        return
    await state.update_data(name=name)
    await message.answer("Теперь отправь эмодзи для клана (например: 🏛️, 🦁, 🔥):")
    await state.set_state(ClanStates.waiting_emoji)


@router.message(ClanStates.waiting_emoji)
async def clan_emoji_entered(message: Message, state: FSMContext):
    emoji = message.text.strip()
    if len(emoji) > 10:  # эмодзи обычно короткие
        emoji = emoji[:10]
    await state.update_data(emoji=emoji)
    await message.answer("Теперь напиши описание клана (или напиши «нет»):")
    await state.set_state(ClanStates.waiting_description)


@router.message(ClanStates.waiting_description)
async def clan_description_entered(message: Message, state: FSMContext):
    description = message.text.strip()
    if description.lower() == "нет":
        description = ""

    data = await state.get_data()
    ok, msg = db.clan_create(
        message.from_user.id,
        data["name"],
        data["emoji"],
        description
    )

    if ok:
        await message.answer(f"✅ {msg}", reply_markup=clan_main_menu(
            db.clan_get_user_clan(message.from_user.id)
        ).as_markup())
    else:
        await message.answer(f"❌ {msg}")
    await state.clear()


# ==================== СПИСОК И ТОП КЛАНОВ ====================

@router.callback_query(F.data == "clan_list")
async def clan_list(callback: CallbackQuery):
    conn = sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, c.emoji, c.bank,
               (SELECT COUNT(*) FROM clan_members WHERE clan_id = c.id) AS members
        FROM clans c ORDER BY c.created_at DESC LIMIT 20
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await callback.message.edit_text(
            "📋 Пока нет ни одного клана. Создай первый!",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text="🏛️ Создать клан", callback_data="clan_create_start")
            ).row(
                InlineKeyboardButton(text="🔙 Назад", callback_data="clan_word_back")
            ).as_markup()
        )
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    text = "📋 Все кланы:\n\n"
    for r in rows:
        cid, name, emoji, bank, members = r
        text += f"{emoji} {name} — {members} чел., казна {bank:,} 🪙\n"
        builder.row(InlineKeyboardButton(
            text=f"Вступить в {emoji} {name}",
            callback_data=f"clan_join_{cid}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="clan_word_back"))

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "clan_top")
async def clan_top(callback: CallbackQuery):
    top = db.clan_top(10)
    if not top:
        await callback.message.edit_text(
            "🏆 Пока нет кланов.",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text="🔙 Назад", callback_data="clan_word_back")
            ).as_markup()
        )
        await callback.answer()
        return

    medals = ["🥇", "🥈", "🥉"]
    text = "🏆 Топ кланов:\n\n"
    for i, c in enumerate(top, 1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        text += f"{medal} {c['emoji']} {c['name']} — {c['bank']:,} 🪙 ({c['members']} чел.)\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="clan_word_back"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "clan_word_back")
async def clan_word_back(callback: CallbackQuery):
    # Просто вызываем ворд-триггер заново — эмулируем
    user_id = callback.from_user.id
    clan = db.clan_get_user_clan(user_id)
    if clan:
        members = db.clan_get_members(clan["id"])
        role_names = {"leader": "👑 Лидер", "deputy": "⭐ Зам", "member": "👤 Участник"}
        text = (
            f"{clan['emoji']} Клан «{clan['name']}»\n\n"
            f"💰 Казна: {clan['bank']:,} 🪙\n"
            f"👥 Участников: {len(members)}/{db.CLAN_MAX_MEMBERS}\n"
            f"🎖 Твоя роль: {role_names.get(clan['role'], clan['role'])}\n"
        )
        await callback.message.edit_text(text, reply_markup=clan_main_menu(clan).as_markup())
    else:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text=f"🏛️ Создать клан — {db.CLAN_CREATE_COST:,} 🪙",
            callback_data="clan_create_start"
        ))
        builder.row(InlineKeyboardButton(text="📋 Список кланов", callback_data="clan_list"))
        builder.row(InlineKeyboardButton(text="🏆 Топ кланов", callback_data="clan_top"))
        await callback.message.edit_text(
            "🏛️ Ты пока не в клане.",
            reply_markup=builder.as_markup()
        )
    await callback.answer()


# ==================== ВСТУПЛЕНИЕ В КЛАН ====================

@router.callback_query(F.data.startswith("clan_join_"))
async def clan_join(callback: CallbackQuery):
    clan_id = int(callback.data.replace("clan_join_", ""))
    ok, msg = db.clan_send_request(clan_id, callback.from_user.id)
    await callback.answer(msg, show_alert=True)

    # Уведомим лидера и замов
    clan = db.clan_get(clan_id)
    if clan:
        members = db.clan_get_members(clan_id)
        for m in members:
            if m["role"] in ("leader", "deputy"):
                try:
                    await callback.bot.send_message(
                        m["user_id"],
                        f"📩 Новая заявка в клан «{clan['name']}» от "
                        f"@{callback.from_user.username or callback.from_user.id}\n\n"
                        f"Открой меню клана, чтобы принять или отклонить."
                    )
                except Exception:
                    pass


# ==================== ВНЕСТИ В КАЗНУ ====================

@router.callback_query(F.data == "clan_deposit")
async def clan_deposit_start(callback: CallbackQuery, state: FSMContext):
    clan = db.clan_get_user_clan(callback.from_user.id)
    if not clan:
        await callback.answer("Ты не в клане", show_alert=True)
        return

    await callback.message.edit_text(
        f"💰 Внесение в казну «{clan['name']}»\n\n"
        f"Твой баланс: {db.get_balance(callback.from_user.id):,} 🪙\n\n"
        f"Введи сумму:"
    )
    await state.set_state(ClanStates.waiting_deposit)
    await callback.answer()


@router.message(ClanStates.waiting_deposit)
async def clan_deposit_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
    except ValueError:
        await message.answer("❌ Введи число")
        return

    ok, msg = db.clan_deposit(message.from_user.id, amount)
    if ok:
        clan = db.clan_get_user_clan(message.from_user.id)
        db.log_action(message.from_user.id, "clan_create", f"Клан «{data['name']}»", amount=-db.CLAN_CREATE_COST)
        await message.answer(f"✅ {msg}", reply_markup=clan_main_menu(clan).as_markup())
    else:
        await message.answer(f"❌ {msg}")
    await state.clear()


# ==================== УЧАСТНИКИ ====================

@router.callback_query(F.data == "clan_members")
async def clan_members(callback: CallbackQuery):
    clan = db.clan_get_user_clan(callback.from_user.id)
    if not clan:
        await callback.answer("Ты не в клане", show_alert=True)
        return

    members = db.clan_get_members(clan["id"])
    role_icons = {"leader": "👑", "deputy": "⭐", "member": "👤"}
    text = f"👥 Участники клана «{clan['name']}»:\n\n"
    for m in members:
        icon = role_icons.get(m["role"], "👤")
        name = f"@{m['username']}" if m["username"] else f"ID{m['user_id']}"
        text += f"{icon} {name} — {m['balance']:,} 🪙\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="clan_word_back"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


# ==================== ЗАЯВКИ ====================

@router.callback_query(F.data == "clan_requests")
async def clan_requests(callback: CallbackQuery):
    clan = db.clan_get_user_clan(callback.from_user.id)
    if not clan or clan["role"] not in ("leader", "deputy"):
        await callback.answer("Нет прав", show_alert=True)
        return

    pending = db.clan_get_pending_requests(clan["id"])
    if not pending:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="clan_word_back"))
        await callback.message.edit_text(
            "📩 Заявок пока нет.",
            reply_markup=builder.as_markup()
        )
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    text = "📩 Заявки:\n\n"
    for p in pending:
        name = f"@{p['username']}" if p["username"] else f"ID{p['user_id']}"
        text += f"• {name}\n"
        builder.row(
            InlineKeyboardButton(
                text=f"✅ Принять {name}",
                callback_data=f"clan_accept_{p['request_id']}"
            ),
            InlineKeyboardButton(
                text=f"❌",
                callback_data=f"clan_decline_{p['request_id']}"
            )
        )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="clan_word_back"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("clan_accept_"))
async def clan_accept(callback: CallbackQuery):
    req_id = int(callback.data.replace("clan_accept_", ""))
    ok, msg = db.clan_accept_request(req_id, callback.from_user.id)
    await callback.answer(msg, show_alert=True)
    if ok:
        await clan_requests(callback)


@router.callback_query(F.data.startswith("clan_decline_"))
async def clan_decline(callback: CallbackQuery):
    req_id = int(callback.data.replace("clan_decline_", ""))
    if db.clan_decline_request(req_id, callback.from_user.id):
        await callback.answer("Заявка отклонена")
        await clan_requests(callback)
    else:
        await callback.answer("Ошибка", show_alert=True)


# ==================== ВЫХОД ====================

@router.callback_query(F.data == "clan_leave")
async def clan_leave(callback: CallbackQuery):
    ok, msg = db.clan_leave(callback.from_user.id)
    await callback.answer(msg, show_alert=True)
    if ok:
        await callback.message.edit_text(
            "🚪 Ты вышел из клана.",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text="🏛️ Создать клан", callback_data="clan_create_start")
            ).row(
                InlineKeyboardButton(text="📋 Список кланов", callback_data="clan_list")
            ).as_markup()
        )


# ==================== КИК ====================

@router.callback_query(F.data == "clan_kick_start")
async def clan_kick_start(callback: CallbackQuery, state: FSMContext):
    clan = db.clan_get_user_clan(callback.from_user.id)
    if not clan or clan["role"] not in ("leader", "deputy"):
        await callback.answer("Нет прав", show_alert=True)
        return

    await callback.message.edit_text(
        "🥾 Введи username игрока для кика:"
    )
    await state.set_state(ClanStates.waiting_kick)
    await callback.answer()


@router.message(ClanStates.waiting_kick)
async def clan_kick_target(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    conn = sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE LOWER(username) = ?", (username.lower(),))
    row = cur.fetchone()
    conn.close()
    if not row:
        await message.answer("❌ Игрок не найден")
        return

    target_id = row[0]
    ok, msg = db.clan_kick(message.from_user.id, target_id)
    if ok:
        await message.answer(f"✅ {msg}")
    else:
        await message.answer(f"❌ {msg}")
    await state.clear()


# ==================== РОСПУСК ====================

@router.callback_query(F.data == "clan_disband")
async def clan_disband(callback: CallbackQuery):
    # Запрос подтверждения
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, распустить", callback_data="clan_disband_confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="clan_word_back")
    )
    await callback.message.edit_text(
        "⚠️ Ты уверен, что хочешь распустить клан?\n"
        "Казна вернётся лидеру, все участники будут исключены.",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "clan_disband_confirm")
async def clan_disband_confirm(callback: CallbackQuery):
    ok, msg = db.clan_disband(callback.from_user.id)
    await callback.answer(msg, show_alert=True)
    if ok:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text=f"🏛️ Создать клан — {db.CLAN_CREATE_COST:,} 🪙",
            callback_data="clan_create_start"
        ))
        builder.row(InlineKeyboardButton(text="📋 Список кланов", callback_data="clan_list"))
        await callback.message.edit_text(
            f"💥 {msg}",
            reply_markup=builder.as_markup()
        )

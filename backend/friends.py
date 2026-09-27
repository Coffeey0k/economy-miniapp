"""
Друзья для ParadiseCoin.

- `друг @username` — отправить заявку в друзья.
- Кнопки «Принять / Отклонить» у получателя.
- Ворд-триггер `друзья` — список друзей и бонус.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import database as db

router = Router()


# ==================== ОТПРАВКА ЗАЯВКИ ====================

@router.message(F.text.lower().startswith("друг "))
async def friend_request(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "❌ Укажи пользователя.\n"
            "Пример: `друг @username`",
            parse_mode="Markdown"
        )
        return

    target_raw = parts[1].strip().lstrip("@").lower()
    from_user = message.from_user.id

    if target_raw.isdigit():
        target_id = int(target_raw)
    else:
        conn = db.__dict__  # не используется
        from db_compat import connect
        conn = connect(db.DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE LOWER(username) = ?", (target_raw,))
        row = cur.fetchone()
        conn.close()
        if not row:
            await message.answer(f"❌ Пользователь @{target_raw} не найден.")
            return
        target_id = row[0]

    if target_id == from_user:
        await message.answer("❌ Нельзя добавить себя в друзья.")
        return

    result = db.friend_send_request(from_user, target_id)

    if result == "mutual":
        await message.answer(
            f"✅ Вы и @{target_raw} теперь друзья (у вас была встречная заявка)!"
        )
        try:
            await message.bot.send_message(
                target_id,
                f"✅ @{message.from_user.username or from_user} принял твою заявку — вы теперь друзья!"
            )
        except Exception:
            pass
        return

    if result:
        await message.answer(
            f"📩 Заявка отправлена @{target_raw}."
        )
        # Отправляем ему заявку с кнопками
        builder = InlineKeyboardBuilder()
        # Найдём id заявки
        from db_compat import connect
        conn = connect(db.DB_NAME)
        cur = conn.cursor()
        cur.execute("""
            SELECT id FROM friend_requests
            WHERE from_user = ? AND to_user = ? AND status = 'pending'
            ORDER BY id DESC LIMIT 1
        """, (from_user, target_id))
        row = cur.fetchone()
        conn.close()
        if row:
            req_id = row[0]
            builder.row(
                InlineKeyboardButton(text="✅ Принять", callback_data=f"friend_accept_{req_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"friend_decline_{req_id}"),
            )
            try:
                await message.bot.send_message(
                    target_id,
                    f"👥 Заявка в друзья от @{message.from_user.username or from_user}.\n\nПринять?",
                    reply_markup=builder.as_markup()
                )
            except Exception:
                pass
    else:
        await message.answer(
            "❌ Не удалось отправить заявку. Возможно, вы уже друзья или заявка уже отправлена."
        )


# ==================== ПРИНЯТИЕ / ОТКЛОНЕНИЕ ====================

@router.callback_query(F.data.startswith("friend_accept_"))
async def friend_accept(callback: CallbackQuery):
    req_id = int(callback.data.replace("friend_accept_", ""))
    user_id = callback.from_user.id

    if db.friend_accept(req_id, user_id):
        await callback.message.edit_text("✅ Заявка принята! Вы теперь друзья.")
        await callback.answer("Друзья!")
    else:
        await callback.answer("❌ Не удалось принять.", show_alert=True)


@router.callback_query(F.data.startswith("friend_decline_"))
async def friend_decline(callback: CallbackQuery):
    req_id = int(callback.data.replace("friend_decline_", ""))
    user_id = callback.from_user.id

    if db.friend_decline(req_id, user_id):
        await callback.message.edit_text("❌ Заявка отклонена.")
        await callback.answer()
    else:
        await callback.answer("❌ Не удалось отклонить.", show_alert=True)


# ==================== ВОРД-ТРИГГЕР «ДРУЗЬЯ» ====================

@router.message(F.text.lower() == "друзья")
async def friends_list_word(message: Message):
    user_id = message.from_user.id

    # Входящие заявки
    pending = db.friend_pending_requests(user_id)

    # Список друзей
    friends = db.friend_list(user_id)
    bonus = db.friend_income_bonus(user_id)

    text = "👥 Друзья\n\n"
    text += f"👤 Друзей: {len(friends)}\n"
    text += f"💰 Бонус к зарплате: +{bonus}%\n\n"

    if pending:
        text += "📩 Входящие заявки:\n"
        for p in pending:
            text += f"• @{p['from_username']}\n"
        text += "\n(Прими или отклони через кнопки в личных сообщениях)\n\n"

    if friends:
        text += "Твои друзья:\n"
        for f in friends[:20]:
            name = f"@{f['username']}" if f['username'] else f"ID {f['user_id']}"
            text += f"• {name} — {f['balance']:,} 🪙\n"
        if len(friends) > 20:
            text += f"...и ещё {len(friends) - 20}\n"
    else:
        text += "У тебя пока нет друзей.\nОтправь заявку: `друг @username`"

    await message.answer(text)


# ==================== СПИСОК ДРУЗЕЙ ПО КНОПКЕ (для веба и callback) ====================

@router.callback_query(F.data == "friends_menu")
async def friends_menu_cb(callback: CallbackQuery):
    """Кнопка 'Друзья' в главном меню."""
    await callback.message.edit_text(
        "👥 Друзья\n\nВведи `друг @username`, чтобы отправить заявку, "
        "или напиши `друзья` в чат, чтобы посмотреть список.",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ).as_markup()
    )
    await callback.answer()

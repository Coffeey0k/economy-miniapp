"""
Крестики-нолики для ParadiseCoin.

Два режима:
1. PvP — вызов игрока командой "кн @username <ставка>".
2. С ботом — команда "кн бот <ставка>".

Поле 3x3 хранится как строка из 9 символов: '_' пусто, 'X', 'O'.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import database as db

router = Router()


# ==================== КЛАВИАТУШКИ ====================

def board_keyboard(game_id: int, board: str) -> InlineKeyboardBuilder:
    """Строит инлайн-клавиатуру 3x3 для текущей доски."""
    builder = InlineKeyboardBuilder()
    symbols = {"X": "❌", "O": "⭕", "_": "⬜"}
    rows = []
    for i in range(3):
        row = []
        for j in range(3):
            cell = i * 3 + j
            row.append(InlineKeyboardButton(
                text=symbols.get(board[cell], "⬜"),
                callback_data=f"ttt_move_{game_id}_{cell}"
            ))
        rows.append(row)
    for row in rows:
        builder.row(*row)
    return builder


def invite_keyboard(invite_id: int) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Принять", callback_data=f"ttt_accept_{invite_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"ttt_decline_{invite_id}"),
    )
    return builder


# ==================== СОЗДАНИЕ ИГРЫ ====================

@router.message(F.text.lower().startswith("кн "))
async def ttt_create(message: Message):
    """Команда: 'кн @username 500' или 'кн бот 500'."""
    print(f">>> ttt_create вызван! text={message.text!r}")
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer(
            "❌ Неверный формат.\n"
            "Используй: `кн @username 500` или `кн бот 500`",
            parse_mode="Markdown"
        )
        return

    target_raw = parts[1].strip().lstrip("@").lower()
    try:
        bet = int(parts[2])
    except ValueError:
        await message.answer("❌ Ставка должна быть числом.")
        return

    if bet <= 0:
        await message.answer("❌ Ставка должна быть больше нуля.")
        return

    from_user = message.from_user.id

    # Проверка баланса
    if db.get_balance(from_user) < bet:
        await message.answer(f"❌ Недостаточно монет. У тебя {db.get_balance(from_user)} 🪙.")
        return

    # === Игра с ботом ===
    if target_raw in ("бот", "bot"):
        game_id = db.ttt_create_game(
            player_x=from_user,
            player_o=None,
            bet=bet,
            is_vs_bot=True
        )
        game = db.ttt_get_game(game_id)
        text = (
            f"🤖 Игра с ботом\n"
            f"💰 Ставка: {bet} 🪙\n"
            f"Ты — ❌, бот — ⭕\n\n"
            f"Твой ход!"
        )
        kb = board_keyboard(game_id, game["board"]).as_markup()
        await message.answer(text, reply_markup=kb)
        return

    # === Игра с игроком ===
    # Ищем того, кого вызывают
    conn_import_sqlite3 = __import__("db_compat")
    conn = conn_import_sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE username = ?", (target_raw,))
    row = cur.fetchone()
    conn.close()

    if not row:
        await message.answer(f"❌ Пользователь @{target_raw} не найден.")
        return

    to_user = row[0]
    if to_user == from_user:
        await message.answer("❌ Нельзя вызвать самого себя.")
        return

    if db.get_balance(to_user) < bet:
        await message.answer(f"❌ У @{target_raw} недостаточно монет для такой ставки.")
        return

    # Создаём приглашение
    invite_id = db.ttt_create_invite(
        from_user=from_user,
        to_user=to_user,
        bet=bet,
        message_id=message.message_id,
        chat_id=message.chat.id
    )

    text = (
        f"🎮 Вызов на крестики-нолики!\n\n"
        f"👤 От: @{message.from_user.username or from_user}\n"
        f"👤 Кому: @{target_raw}\n"
        f"💰 Ставка: {bet} 🪙\n\n"
        f"@{target_raw}, принимаешь вызов?"
    )
    await message.answer(text, reply_markup=invite_keyboard(invite_id).as_markup())


# ==================== ПРИНЯТИЕ / ОТКАЗ ====================

@router.callback_query(F.data.startswith("ttt_accept_"))
async def ttt_accept(callback: CallbackQuery):
    invite_id = int(callback.data.replace("ttt_accept_", ""))
    invite = db.ttt_get_invite(invite_id)

    if not invite:
        await callback.answer("❌ Приглашение не найдено.", show_alert=True)
        return
    if invite["status"] != "pending":
        await callback.answer("❌ Приглашение уже неактивно.", show_alert=True)
        return
    if callback.from_user.id != invite["to_user"]:
        await callback.answer("❌ Это приглашение не для тебя.", show_alert=True)
        return

    # Проверяем ставки ещё раз
    bet = invite["bet"]
    if db.get_balance(invite["from_user"]) < bet:
        await callback.answer("❌ У вызывающего уже нет столько монет.", show_alert=True)
        return
    if db.get_balance(invite["to_user"]) < bet:
        await callback.answer("❌ У тебя недостаточно монет.", show_alert=True)
        return

    db.ttt_update_invite_status(invite_id, "accepted")

    # Создаём игру: X — вызывающий, O — принявший
    game_id = db.ttt_create_game(
        player_x=invite["from_user"],
        player_o=invite["to_user"],
        bet=bet,
        is_vs_bot=False
    )
    game = db.ttt_get_game(game_id)

    text = (
        f"🎮 Игра началась!\n\n"
        f"❌ X: @{_get_username(invite['from_user'])}\n"
        f"⭕ O: @{_get_username(invite['to_user'])}\n"
        f"💰 Ставка: {bet} 🪙\n\n"
        f"Ход X!"
    )
    kb = board_keyboard(game_id, game["board"]).as_markup()
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("Игра началась!")


@router.callback_query(F.data.startswith("ttt_decline_"))
async def ttt_decline(callback: CallbackQuery):
    invite_id = int(callback.data.replace("ttt_decline_", ""))
    invite = db.ttt_get_invite(invite_id)

    if not invite or invite["status"] != "pending":
        await callback.answer("❌ Приглашение уже неактивно.", show_alert=True)
        return
    if callback.from_user.id != invite["to_user"]:
        await callback.answer("❌ Это не твоё приглашение.", show_alert=True)
        return

    db.ttt_update_invite_status(invite_id, "declined")
    await callback.message.edit_text(
        f"❌ @{_get_username(invite['to_user'])} отклонил вызов."
    )
    await callback.answer()


# ==================== ХОД ====================

@router.callback_query(F.data.startswith("ttt_move_"))
async def ttt_move(callback: CallbackQuery):
    parts = callback.data.split("_")
    game_id = int(parts[2])
    cell = int(parts[3])

    game = db.ttt_get_game(game_id)
    if not game:
        await callback.answer("❌ Игра не найдена.", show_alert=True)
        return
    if game["status"] != "active":
        await callback.answer("❌ Игра уже завершена.", show_alert=True)
        return

    user_id = callback.from_user.id

    # Проверка: это участник игры?
    if user_id not in (game["player_x"], game["player_o"]):
        await callback.answer("❌ Ты не участник этой игры.", show_alert=True)
        return

    if game["turn"] != user_id:
        await callback.answer("⏳ Не твой ход.", show_alert=True)
        return

    ok, result = db.ttt_make_move(game_id, cell, user_id)
    if not ok:
        await callback.answer(f"❌ {result}", show_alert=True)
        return

    # === Если с ботом — делаем ход бота ===
    if game["is_vs_bot"] and result == "next":
        bot_cell = db.ttt_bot_move(game_id)
        if bot_cell is not None:
            db.ttt_make_move(game_id, bot_cell, game["player_o"] or 0)

    # Обновляем сообщение
    updated = db.ttt_get_game(game_id)
    if updated["status"] == "active":
        text = (
            f"🎮 Крестики-нолики\n"
            f"💰 Ставка: {updated['bet']} 🪙\n\n"
            f"Ход: {'❌ X' if updated['turn'] == updated['player_x'] else '⭕ O'}"
        )
        kb = board_keyboard(game_id, updated["board"]).as_markup()
        await callback.message.edit_text(text, reply_markup=kb)
    elif updated["status"] == "draw":
        await _finish_draw(callback, updated)
    else:
        await _finish_win(callback, updated)

    await callback.answer()


# ==================== ЗАВЕРШЕНИЕ ====================

async def _finish_draw(callback: CallbackQuery, game: dict):
    await callback.message.edit_text(
        f"🎮 Ничья!\n\n"
        f"💰 Ставка {game['bet']} 🪙 возвращена обоим игрокам."
    )


async def _finish_win(callback: CallbackQuery, game: dict):
    winner = game["winner"]
    loser = game["player_o"] if winner == game["player_x"] else game["player_x"]

    # Переводим ставку
    if winner and loser:
        db.update_balance(loser, -game["bet"])
        db.update_balance(winner, game["bet"])

    winner_name = _get_username(winner) if winner else "?"
    text = (
        f"🏆 Победа!\n\n"
        f"Победил: @{winner_name}\n"
        f"💰 Выигрыш: {game['bet']} 🪙"
    )
    await callback.message.edit_text(text)


# ==================== ВСПОМОГАТЕЛЬНОЕ ====================

def _get_username(user_id: int) -> str:
    conn = __import__("db_compat").connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return row[0] or str(user_id)
    return str(user_id)

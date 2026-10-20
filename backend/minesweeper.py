"""
Сапёр для Lavender Dream Coin.

Поле 8x8, 6 мин. Минимальная ставка — 500 LDC.
Победа: открыть все безопасные клетки → выплата 2× ставки.
Проигрыш: попасть на мину → теряешь ставку.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import re
import database as db

router = Router()


# ==================== КЛАВИАТУШКА ====================

def ms_keyboard(game_id: int, board: str, revealed: str, size: int) -> InlineKeyboardBuilder:
    """Строит инлайн-клавиатуру 8x8 для поля."""
    builder = InlineKeyboardBuilder()
    # Символы для цифр (число мин вокруг)
    num_emoji = {
        "0": "⬜", "1": "1️⃣", "2": "2️⃣", "3": "3️⃣",
        "4": "4️⃣", "5": "5️⃣", "6": "6️⃣", "7": "7️⃣", "8": "8️⃣",
    }
    for row in range(size):
        btns = []
        for col in range(size):
            idx = row * size + col
            if revealed[idx] == "1":
                cell = board[idx]
                if cell == "M":
                    text = "💥"
                else:
                    text = num_emoji.get(cell, "⬜")
            else:
                text = "⬛"
            btns.append(InlineKeyboardButton(
                text=text,
                callback_data=f"ms_open_{game_id}_{idx}"
            ))
        builder.row(*btns)
    return builder


# ==================== СТАРТ ИГРЫ ====================

@router.message(F.text.lower().startswith("сапёр") | F.text.lower().startswith("сапер"))
async def ms_start(message: Message):
    """Команда: 'сапёр' или 'сапёр 200' — начать игру."""
    parts = message.text.split()
    bet = db.MS_BET
    if len(parts) >= 2:
        match = re.fullmatch(r"([0-9]+(?:[.,][0-9]+)?)(к{1,3})?", parts[1].lower())
        if not match:
            db.log_action(message.from_user.id, "security", f"Некорректная ставка Сапёра: {message.text[:180]!r}")
            await message.answer("❌ Ставка должна быть числом (например, «сапёр 1к»).")
            return
        try:
            number = float(match.group(1).replace(",", "."))
            multiplier = {"": 1, "к": 1_000, "кк": 1_000_000, "ккк": 1_000_000_000}[match.group(2) or ""]
            scaled = number * multiplier
            if not scaled.is_integer() or scaled > 9_223_372_036_854_775_807:
                raise ValueError
            bet = int(scaled)
        except (ValueError, OverflowError):
            db.log_action(message.from_user.id, "security", f"Ставка Сапёра вне диапазона: {message.text[:180]!r}")
            await message.answer("❌ Некорректная ставка.")
            return

    if bet < 500:
        db.log_action(message.from_user.id, "security", f"Ставка Сапёра ниже минимума: {bet}")
        await message.answer("❌ Минимальная ставка — 500 LDC.")
        return

    user_id = message.from_user.id
    balance = db.get_balance(user_id)
    if balance < bet:
        db.log_action(user_id, "security", f"Ставка Сапёра превышает баланс: bet={bet}; balance={balance}")
        await message.answer(f"❌ Недостаточно LDC. У тебя {balance:,} LDC.")
        return
    allowed, remaining = db.register_game_play(user_id, "minesweeper")
    if not allowed:
        db.log_action(user_id, "security", f"Попытка Сапёра во время кулдауна: remaining={remaining}s")
        hours, rem = divmod(remaining, 3600)
        minutes = rem // 60
        await message.answer(f"⏳ Сапёр на перерыве ещё {hours} ч {minutes} мин.")
        return

    game_id = db.ms_create_game(user_id, bet)
    if not game_id:
        await message.answer("❌ Не удалось создать игру.")
        return

    game = db.ms_get_game(game_id)
    chat_hint = (
        "\n\n👥 В групповом чате у каждого игрока своя отдельная партия. "
        "Ставка и выигрыш принадлежат тому, кто запустил эту игру."
        if message.chat.type in {"group", "supergroup"} else ""
    )
    text = (
        f"💣 Сапёр\n\n"
        f"💰 Ставка: {bet:,} 🪙\n"
        f"🎁 Выплата при победе: {bet * 2:,} 🪙 (чистая прибыль {bet:,} 🪙)\n\n"
        f"Открывай клетки. Не попади на мину!"
        f"{chat_hint}"
    )
    kb = ms_keyboard(game_id, game["board"], game["revealed"], game["field_size"]).as_markup()
    await message.answer(text, reply_markup=kb)


# ==================== ОТКРЫТИЕ КЛЕТКИ ====================

@router.callback_query(F.data.startswith("ms_open_"))
async def ms_open(callback: CallbackQuery):
    parts = callback.data.split("_")
    game_id = int(parts[2])
    cell = int(parts[3])

    user_id = callback.from_user.id
    ok, result, msg = db.ms_open_cell(game_id, cell, user_id)

    if not ok:
        await callback.answer(f"❌ {msg}", show_alert=True)
        return

    game = db.ms_get_game(game_id)

    # === Проигрыш ===
    if game["status"] == "lose":
        db.update_game_stats(user_id, False, game["bet"])
        kb = ms_keyboard(game_id, game["board"], game["revealed"], game["field_size"]).as_markup()
        await callback.message.edit_text(
            f"💥 Мина! Ты проиграл.\n\n"
            f"💰 Потеряно: {game['bet']} 🪙\n"
            f"📊 Баланс: {db.get_balance(user_id)} 🪙",
            reply_markup=kb
        )
        await callback.answer("💥 Бум!")
        return

    # === Победа ===
    if game["status"] == "win":
        db.update_game_stats(user_id, True, game["bet"])
        kb = ms_keyboard(game_id, game["board"], game["revealed"], game["field_size"]).as_markup()
        await callback.message.edit_text(
            f"🏆 Победа!\n\n"
            f"💰 Выплата: {game['bet'] * 2:,} 🪙 (чистая прибыль {game['bet']:,} 🪙)\n"
            f"📊 Баланс: {db.get_balance(user_id)} 🪙",
            reply_markup=kb
        )
        await callback.answer("🎉 Победа!")
        return

    # === Игра продолжается ===
    kb = ms_keyboard(game_id, game["board"], game["revealed"], game["field_size"]).as_markup()
    remaining = game["revealed"].count("0")
    mines_hidden = game["mines_count"]
    await callback.message.edit_text(
        f"💣 Сапёр\n\n"
        f"💰 Ставка: {game['bet']} 🪙\n"
        f"💥 Мин: {mines_hidden}\n"
        f"⬛ Осталось открыть: ~{remaining - mines_hidden}",
        reply_markup=kb
    )
    await callback.answer()

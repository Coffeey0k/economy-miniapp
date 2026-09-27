"""
Сапёр для ParadiseCoin.

Поле 8x8, 6 мин. Ставка 200 монет.
Победа: открыть все безопасные клетки → +400 монет.
Проигрыш: попасть на мину → теряешь ставку.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
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
    bet = db.MS_BET  # 200 по умолчанию
    if len(parts) >= 2:
        try:
            bet = int(parts[1])
        except ValueError:
            await message.answer("❌ Ставка должна быть числом.")
            return

    if bet <= 0:
        await message.answer("❌ Ставка должна быть больше нуля.")
        return

    user_id = message.from_user.id
    if db.get_balance(user_id) < bet:
        await message.answer(f"❌ Недостаточно монет. У тебя {db.get_balance(user_id)} 🪙.")
        return

    game_id = db.ms_create_game(user_id, bet)
    if not game_id:
        await message.answer("❌ Не удалось создать игру.")
        return

    game = db.ms_get_game(game_id)
    text = (
        f"💣 Сапёр\n\n"
        f"💰 Ставка: {bet} 🪙\n"
        f"🎁 Награда: {db.MS_REWARD} 🪙\n\n"
        f"Открывай клетки. Не попади на мину!"
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
        kb = ms_keyboard(game_id, game["board"], game["revealed"], game["field_size"]).as_markup()
        await callback.message.edit_text(
            f"🏆 Победа!\n\n"
            f"💰 Выигрыш: {db.MS_REWARD} 🪙\n"
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

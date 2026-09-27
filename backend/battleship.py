"""
Морской бой для ParadiseCoin.

Поле 10×10, стандартный набор кораблей.
Ставка от 500 монет, победа = x2.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import database as db

router = Router()


# ==================== КЛАВИАТУШКИ ====================

def bs_keyboard(game_id: int, field: str, shots: str, viewing_enemy: bool) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()

    for row in range(db.BS_SIZE):
        btns = []
        for col in range(db.BS_SIZE):
            idx = row * db.BS_SIZE + col
            shot = shots[idx]

            # Единый символ-заполнитель для пустой клетки
            EMPTY = "•"

            if viewing_enemy:
                if shot == "1":
                    text = "✖"
                elif shot == "2":
                    text = "🔥"
                elif shot == "3":
                    text = "💀"
                else:
                    text = EMPTY
                cb = f"bs_fire_{game_id}_{row}_{col}"
            else:
                if shot == "1":
                    text = "✖"
                elif shot == "2":
                    text = "🔥"
                elif shot == "3":
                    text = "💀"
                elif field[idx] == "S":
                    text = "🚢"
                else:
                    text = EMPTY
                cb = f"bs_nothing_{game_id}_{row}_{col}"   # уникальный cb

            btns.append(InlineKeyboardButton(text=text, callback_data=cb))
        builder.row(*btns)

    # Кнопка переключения
    if viewing_enemy:
        builder.row(InlineKeyboardButton(
            text="👁️ Показать моё поле",
            callback_data=f"bs_view_own_{game_id}"
        ))
    else:
        builder.row(InlineKeyboardButton(
            text="🎯 Показать поле врага",
            callback_data=f"bs_view_enemy_{game_id}"
        ))

    return builder


# ==================== СТАРТ ИГРЫ ====================

@router.message(F.text.lower().startswith("мб ") | F.text.lower().startswith("морской бой "))
async def bs_start(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            f"❌ Укажи ставку.\n"
            f"Пример: `мб 500`\n"
            f"Минимум: {db.BS_BET_MIN} 🪙",
            parse_mode="Markdown"
        )
        return

    try:
        bet = int(parts[-1])
    except ValueError:
        await message.answer("❌ Ставка должна быть числом.")
        return

    if bet < db.BS_BET_MIN:
        await message.answer(f"❌ Минимальная ставка: {db.BS_BET_MIN} 🪙.")
        return

    user_id = message.from_user.id
    if db.get_balance(user_id) < bet:
        await message.answer(f"❌ Недостаточно монет. У тебя {db.get_balance(user_id)} 🪙.")
        return

    game_id = db.bs_create_game(user_id, bet)
    if not game_id:
        await message.answer("❌ Не удалось создать игру.")
        return

    game = db.bs_get_game(game_id)
    text = (
        f"⚓ Морской бой\n\n"
        f"💰 Ставка: {bet} 🪙\n"
        f"🎁 Победа: {bet * 2} 🪙\n\n"
        f"🎯 Ты стреляешь по полю врага. Нажми на клетку, чтобы выстрелить.\n"
        f"Символы: ⬛ не стрелял, ▫️ промах, 🔥 попал, 💀 убил."
    )
    kb = bs_keyboard(game_id, game["bot_field"], game["player_shots"], True).as_markup()
    await message.answer(text, reply_markup=kb)


# ==================== ВЫСТРЕЛ ИГРОКА ====================

@router.callback_query(F.data.startswith("bs_fire_"))
async def bs_fire(callback: CallbackQuery):
    parts = callback.data.split("_")
    game_id = int(parts[2])
    row = int(parts[3])
    col = int(parts[4])

    game = db.bs_get_game(game_id)
    if not game or game["status"] != "active":
        await callback.answer("❌ Игра неактивна", show_alert=True)
        return
    if game["user_id"] != callback.from_user.id:
        await callback.answer("❌ Не твоя игра", show_alert=True)
        return
    if game["turn"] != "player":
        await callback.answer("⏳ Не твой ход", show_alert=True)
        return

    ok, result, msg = db.bs_player_shot(game_id, row, col)
    if not ok:
        await callback.answer(f"❌ {msg}", show_alert=True)
        return

    # === Бот отвечает, если игрок промахнулся или игра идёт ===
    game_after = db.bs_get_game(game_id)
    bot_msg = ""
    if game_after["status"] == "active" and game_after["turn"] == "bot":
        # Делаем несколько ходов бота, пока он попадает
        while True:
            bot_result, bot_msg = db.bs_bot_move(game_id)
            if bot_result in ("hit", "sunk"):
                continue
            break

    # === Обновляем сообщение ===
    game_final = db.bs_get_game(game_id)

    if game_final["status"] == "win":
        kb = bs_keyboard(game_id, game_final["bot_field"], game_final["player_shots"], True).as_markup()
        await callback.message.edit_text(
            f"🏆 Победа!\n\n"
            f"💰 Ты выиграл {game_final['bet'] * 2} 🪙\n"
            f"📊 Баланс: {db.get_balance(callback.from_user.id)} 🪙",
            reply_markup=kb
        )
        await callback.answer("🎉 Победа!")
        return

    if game_final["status"] == "lose":
        kb = bs_keyboard(game_id, game_final["player_field"], game_final["bot_shots"], False).as_markup()
        await callback.message.edit_text(
            f"💀 Поражение.\n\n"
            f"Бот уничтожил твой флот.\n"
            f"📊 Баланс: {db.get_balance(callback.from_user.id)} 🪙",
            reply_markup=kb
        )
        await callback.answer("💀 Проигрыш")
        return

    # Игра идёт
    result_emoji = {
        "hit": "🔥 Попал!",
        "miss": "▫️ Промах",
        "sunk": "💀 Убил!",
    }.get(result, "")

    text = (
        f"⚓ Морской бой\n\n"
        f"💰 Ставка: {game_final['bet']} 🪙\n\n"
        f"Твой выстрел ({row + 1}, {col + 1}): {result_emoji}\n"
    )
    if bot_msg:
        text += f"\n{bot_msg}\n"

    if game_final["turn"] == "player":
        text += "\n🎯 Твой ход!"

    kb = bs_keyboard(game_id, game_final["bot_field"], game_final["player_shots"], True).as_markup()
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ==================== ПЕРЕКЛЮЧЕНИЕ ПОЛЕЙ ====================

@router.callback_query(F.data.startswith("bs_view_own_"))
async def bs_view_own(callback: CallbackQuery):
    game_id = int(callback.data.replace("bs_view_own_", ""))
    game = db.bs_get_game(game_id)
    if not game:
        await callback.answer("❌ Игра не найдена", show_alert=True)
        return

    text = (
        f"⚓ Твоё поле\n\n"
        f"🚢 — твои корабли\n"
        f"▫️ — промах бота\n"
        f"🔥 — бот попал\n"
        f"💀 — бот убил корабль"
    )
    kb = bs_keyboard(game_id, game["player_field"], game["bot_shots"], False).as_markup()
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("bs_view_enemy_"))
async def bs_view_enemy(callback: CallbackQuery):
    game_id = int(callback.data.replace("bs_view_enemy_", ""))
    game = db.bs_get_game(game_id)
    if not game:
        await callback.answer("❌ Игра не найдена", show_alert=True)
        return

    text = (
        f"⚓ Поле врага\n\n"
        f"⬛ — не стрелял\n"
        f"▫️ — промах\n"
        f"🔥 — попал\n"
        f"💀 — убил корабль"
    )
    kb = bs_keyboard(game_id, game["bot_field"], game["player_shots"], True).as_markup()
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ==================== НАЖАТИЕ НА СВОЁ ПОЛЕ ====================

@router.callback_query(F.data.startswith("bs_nothing_"))
async def bs_nothing(callback: CallbackQuery):
    await callback.answer("Это твоё поле — по нему не стреляешь. Переключись на поле врага.", show_alert=True)

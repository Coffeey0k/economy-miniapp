from banner import print_banner
import asyncio
from email.mime import message
from email import message
import logging
import sqlite3
import random
from datetime import datetime, timedelta
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ChatMemberUpdated
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery
from aiogram.types import InlineKeyboardButton

import config
import database as db
from keyboards import (
    main_menu, home_menu, games_menu, catalog_menu,
    transfer_menu, admin_menu, admin_catalog_menu,
    game_bet_menu, cancel_button,
    pets_menu, my_pets_menu, pet_actions_menu,
    professions_menu, professions_list_menu,
    tasks_menu, wheel_menu, eggs_menu,
    tutorial_nav_menu, hints_menu,
    back_to_main_menu, back_to_catalog_keyboard,
    confirm_transfer_keyboard, coin_choice_keyboard,
    confirm_admin_purchase, game_result_keyboard
)
from games import play_slot, play_dice, play_coin, play_dart, play_number
from admin import router as admin_router


def format_time(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

user_messages = defaultdict(list)
# Активные сундуки: {message_id: {"chat_id": ..., "claimed": bool}}
active_chests = {}

dp.include_router(admin_router)


class TransferStates(StatesGroup):
    waiting_recipient = State()
    waiting_amount = State()
    waiting_confirm = State()


class GameStates(StatesGroup):
    waiting_dice_guess = State()
    waiting_coin_choice = State()
    waiting_number_guess = State()


class CatalogStates(StatesGroup):
    waiting_confirm_admin = State()


class PromoStates(StatesGroup):
    waiting_code = State()


class GiftPetStates(StatesGroup):
    waiting_recipient = State()


class GiftCoinStates(StatesGroup):
    waiting_recipient = State()
    waiting_amount = State()

# Хранилище: message_id -> user_id владельца
message_owners = {}


class OwnerCheckMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery) and event.message:
            owner = message_owners.get(event.message.message_id)
            if owner is not None and owner != event.from_user.id:
                await event.answer("❌ Это меню вызвано не вами!", show_alert=True)
                return
        return await handler(event, data)


# Регистрируем middleware для callback-запросов
dp.callback_query.middleware(OwnerCheckMiddleware())

# ==================== СТАРТ + ТУТОРИАЛ ====================

TUTORIAL_STEPS = [
    "🌊 Добро пожаловать в Paradise Reef!\n\n"
    "Привет! 🐚\n"
    "Ты попал(а) в бота Paradise Reef — здесь можно общаться, выполнять задания, играть и зарабатывать ParadiseCoin! 🪙\n\n"
    "Давай быстро разберёмся, как всё работает.",

    "🏝️ 1. Главное меню\n\n"
    "Здесь находятся основные разделы бота:\n\n"
    "🏠 Главная — основная информация и быстрый доступ к функциям.\n"
    "🎮 Игры — мини-игры, в которых можно заработать ParadiseCoin.\n"
    "🛍️ Каталог — товары и услуги, которые можно приобрести за ParadiseCoin.\n"
    "💸 Переводы — отправляй ParadiseCoin другим участникам.\n"
    "👤 Профиль — твой баланс, статистика и другая информация.",

    "🪙 2. ParadiseCoin\n\n"
    "ParadiseCoin — главная валюта нашего флуда.\n\n"
    "Зарабатывать её можно разными способами:\n"
    "• участвовать в играх;\n"
    "• выполнять ежедневные задания;\n"
    "• проявлять активность во флуде;\n"
    "• пользоваться другими функциями бота.\n"
    "А тратить ParadiseCoin можно в Каталоге или отправлять другим участникам.",

    "📋 3. Ежедневные задания\n\n"
    "Каждый день бот выбирает несколько заданий специально для участников флуда.\n\n"
    "Выполняй их, чтобы получать награды и увеличивать свой баланс! 🪙",

    "🛍️ 4. Каталог\n\n"
    "В каталоге можно найти различные товары и услуги за ParadiseCoin.\n\n"
    "Но будь внимателен(на)… 👀\n"
    "Иногда в магазине могут появляться секретные товары, которых обычно там нет.",

    "🌊 5. Самое главное\n\n"
    "Не бойся экспериментировать!\n\n"
    "Заходи в разделы бота, выполняй задания, играй, зарабатывай ParadiseCoin и следи за новостями.\n\n"
    "Добро пожаловать в Paradise Reef 🤍\n"
    "Удачи… она тебе пригодится."
]


@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or str(user_id)

    is_new = not db.get_user(user_id)
    if is_new:
        db.create_user(user_id, username)

    user = db.get_user(user_id)
    if user and user["is_banned"]:
        await message.answer("🚫 Вы заблокированы. Обратитесь к администратору.")
        return

    settings = db.get_user_settings(user_id)

    # Очистка старых записей (наивная, по размеру)
    if len(message_owners) > 5000:
        message_owners.clear()

    # Назначаем ежедневные задания (если ещё не назначены)
    db.assign_daily_tasks(user_id)

    # Туториал
    sent = await message.answer(
        TUTORIAL_STEPS[0],
        reply_markup=tutorial_nav_menu(1, len(TUTORIAL_STEPS) - 1)
    )
    message_owners[sent.message_id] = user_id
    return

    welcome_text = (
        f"🏝️ ParadiseCoin\n\n"
        f"💰 Баланс: {db.get_balance(user_id)} 🪙\n"
        f"🏆 Место в топе: #{db.get_user_rank(user_id)}\n\n"
        "Выберите раздел:"
    )
    sent = await message.answer(welcome_text, reply_markup=main_menu(user_id), parse_mode=None)
    message_owners[sent.message_id] = user_id


@dp.callback_query(F.data.startswith("tutorial_next_"))
async def tutorial_next(callback: CallbackQuery):
    step = int(callback.data.split("_")[2])
    if step >= len(TUTORIAL_STEPS):
        await tutorial_finish(callback)
        return
    await callback.message.edit_text(
        TUTORIAL_STEPS[step],
        reply_markup=tutorial_nav_menu(step + 1, len(TUTORIAL_STEPS) - 1)
    )
    await callback.answer()


@dp.callback_query(F.data == "tutorial_finish")
async def tutorial_finish(callback: CallbackQuery):
    user_id = callback.from_user.id
    db.set_tutorial_done(user_id)
    await callback.message.edit_text(
        "✅ Туториал завершён!\n\nУдачи в Paradise Reef! 🤍",
        reply_markup=main_menu(user_id)
    )
    await callback.answer()


@dp.callback_query(F.data == "tutorial_skip")
async def tutorial_skip(callback: CallbackQuery):
    user_id = callback.from_user.id
    db.set_tutorial_done(user_id)
    await callback.message.edit_text(
        "Окей, туториал пропущен. Если что — ты всегда можешь вернуться в меню командой /start.",
        reply_markup=main_menu(user_id)
    )
    await callback.answer()


# ==================== ПОДСКАЗКИ ====================

@dp.callback_query(F.data == "hints_disable")
async def hints_disable(callback: CallbackQuery):
    db.set_hints_enabled(callback.from_user.id, False)
    await callback.message.edit_text(
        "❌ Подсказки отключены.",
        reply_markup=back_to_main_menu()
    )
    await callback.answer()


# ==================== КОМАНДЫ ====================

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "ℹ️ Помощь ParadiseCoin\n\n"
        "🏠 Главная — топ игроков\n"
        "🎮 Игры — зарабатывай монеты\n"
        "📦 Каталог — покупай услуги и яйца\n"
        "💸 Переводы — переводи монеты\n"
        "👤 Профиль — статистика\n"
        "🐾 Питомцы — коллекция\n"
        "💼 Профессии — работа\n"
        "📋 Задания — ежедневные квесты\n"
        "🎡 Колесо — испытай удачу\n\n"
        "Полезные слова: 'банк', 'баланс', 'промокод'"
    )


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("❌ Нет доступа")
        return
    sent = await message.answer("👑 Админ-панель", reply_markup=admin_menu())
    message_owners[sent.message_id] = message.from_user.id

# ==================== ГЛАВНОЕ МЕНЮ ====================

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    if user and user["is_banned"]:
        await callback.answer("🚫 Вы заблокированы", show_alert=True)
        return
    await callback.message.edit_text(
        f"🏝️ ParadiseCoin\n\n"
        f"💰 Баланс: {db.get_balance(user_id)} 🪙\n"
        f"🏆 Место в топе: #{db.get_user_rank(user_id)}",
        reply_markup=main_menu(user_id),
        parse_mode=None
    )
    await callback.answer()


# ==================== ТОП ====================

@dp.callback_query(F.data == "main_home")
async def show_home(callback: CallbackQuery):
    top_users = db.get_top_users(10)
    if not top_users:
        await callback.message.edit_text("📊 Топ пуст.", reply_markup=home_menu())
        await callback.answer()
        return
    medals = ["🥇", "🥈", "🥉"]
    text = "🏆 Топ игроков ParadiseCoin\n\n"
    for i, (uid, uname, bal) in enumerate(top_users, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        name = f"@{uname}" if uname else f"ID:{uid}"
        text += f"{medal} {name} — {bal:,} 🪙\n"
    await callback.message.edit_text(text, reply_markup=home_menu(), parse_mode=None)
    await callback.answer()


@dp.callback_query(F.data == "refresh_top")
async def refresh_top(callback: CallbackQuery):
    await show_home(callback)


# ==================== ПРОФИЛЬ ====================

@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = db.get_user(user_id)
    if not user:
        await callback.answer("❌ Не найден", show_alert=True)
        return
    uname = user["username"] or str(user_id)
    balance = user["balance"]
    rank = db.get_user_rank(user_id)
    warns = db.get_warn_count(user_id)
    purchases = db.get_user_purchases(user_id)
    pets = db.get_user_pets(user_id)
    stats = _get_game_stats(user_id)
    prof = db.get_profession(user_id)

    text = (
        f"👤 Профиль\n@{uname}\n\n"
        f"🪙 Баланс: {balance:,}\n"
        f"🏆 Место: #{rank}\n"
        f"⚠️ Предупреждения: {warns}\n\n"
        f"📊 Игры:\n"
        f"🎮 Сыграно: {stats['played']}\n"
        f"🏆 Побед: {stats['won']}\n\n"
    )
    if prof and prof["profession"]:
        from database import PROFESSIONS
        pname = PROFESSIONS[prof["profession"]]["name"]
        text += f"💼 Профессия: {pname} (ур. {prof['level']})\n\n"
    if pets:
        text += f"🐾 Питомцев: {len(pets)} (активных: {sum(1 for p in pets if p['is_active'])})\n\n"
    if purchases:
        text += "🎁 Куплено:\n"
        names = {"change_role": "Смена роли", "anti_warn": "Антиварн",
                 "immunity": "Иммунитет", "video": "Видео", "unban": "Разбан", "admin": "Админка"}
        for p in purchases[:5]:
            text += f"• {names.get(p['item_type'], p['item_type'])}\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_profile"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode=None)
    await callback.answer()


@dp.callback_query(F.data == "refresh_profile")
async def refresh_profile(callback: CallbackQuery):
    await show_profile(callback)


def _get_game_stats(user_id: int) -> dict:
    conn = sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT games_played, games_won, total_earned, total_lost FROM game_stats WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    conn.close()
    if r:
        return {"played": r[0], "won": r[1], "earned": r[2], "lost": r[3]}
    return {"played": 0, "won": 0, "earned": 0, "lost": 0}


# ==================== ИГРЫ ====================

@dp.callback_query(F.data == "games_menu")
async def show_games_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎮 Выберите игру:",
        reply_markup=games_menu()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("game_"))
async def select_game(callback: CallbackQuery):
    game_type = callback.data.replace("game_", "")
    names = {"slot": "🎰 Слот", "dice": "🎲 Кубик", "coin": "🪙 Монетка",
             "dart": "🎯 Дартс", "number": "🃏 Угадай число"}
    await callback.message.edit_text(
        f"{names.get(game_type, game_type)}\n\nВыберите ставку:",
        reply_markup=game_bet_menu(game_type)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("bet_"))
async def handle_bet(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    _, game_type, bet_str = callback.data.split("_")
    bet = int(bet_str)
    if db.get_balance(user_id) < bet:
        await callback.answer(f"❌ Недостаточно монет", show_alert=True)
        return
    if game_type == "slot":
        win, msg = play_slot(bet)
        await _process_game(callback, user_id, win, msg)
    elif game_type == "dice":
        await callback.message.edit_text("🎲 Введите число 1–6:", reply_markup=cancel_button())
        await state.update_data(bet=bet)
        await state.set_state(GameStates.waiting_dice_guess)
        await callback.answer()
    elif game_type == "coin":
        await callback.message.edit_text("🪙 Выберите:", reply_markup=coin_choice_keyboard(bet))
        await callback.answer()
    elif game_type == "dart":
        win, msg = play_dart(bet)
        await _process_game(callback, user_id, win, msg)
    elif game_type == "number":
        await callback.message.edit_text("🃏 Введите число 1–10:", reply_markup=cancel_button())
        await state.update_data(bet=bet)
        await state.set_state(GameStates.waiting_number_guess)
        await callback.answer()


async def _process_game(callback: CallbackQuery, user_id: int, win: int, msg: str):
    if win > 0:
        db.update_balance(user_id, win)
        db.update_game_stats(user_id, True, win)
    else:
        db.update_balance(user_id, win)
        db.update_game_stats(user_id, False, abs(win))
    await callback.message.edit_text(
        f"{msg}\n\n💰 Баланс: {db.get_balance(user_id)} 🪙",
        reply_markup=game_result_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("coin_choice_"))
async def handle_coin_choice(callback: CallbackQuery):
    data = callback.data[len("coin_choice_"):]
    choice, bet_str = data.rsplit("_", 1)
    bet = int(bet_str)
    win, msg = play_coin(bet, choice)
    await _process_game(callback, callback.from_user.id, win, msg)


@dp.message(GameStates.waiting_dice_guess)
async def handle_dice(message: Message, state: FSMContext):
    try:
        guess = int(message.text)
        if not 1 <= guess <= 6:
            await message.answer("❌ Число от 1 до 6")
            return
        bet = (await state.get_data()).get("bet")
        win, msg = play_dice(bet, guess)
        uid = message.from_user.id
        if win > 0:
            db.update_balance(uid, win)
            db.update_game_stats(uid, True, win)
        else:
            db.update_balance(uid, win)
            db.update_game_stats(uid, False, abs(win))
        await message.answer(f"{msg}\n\n💰 Баланс: {db.get_balance(uid)} 🪙", reply_markup=game_result_keyboard())
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите число")


@dp.message(GameStates.waiting_number_guess)
async def handle_number(message: Message, state: FSMContext):
    try:
        guess = int(message.text)
        if not 1 <= guess <= 10:
            await message.answer("❌ Число от 1 до 10")
            return
        bet = (await state.get_data()).get("bet")
        win, msg = play_number(bet, guess)
        uid = message.from_user.id
        if win > 0:
            db.update_balance(uid, win)
            db.update_game_stats(uid, True, win)
        else:
            db.update_balance(uid, win)
            db.update_game_stats(uid, False, abs(win))
        await message.answer(f"{msg}\n\n💰 Баланс: {db.get_balance(uid)} 🪙", reply_markup=game_result_keyboard())
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите число")


# ==================== КАТАЛОГ ====================

@dp.callback_query(F.data == "catalog")
async def show_catalog(callback: CallbackQuery):
    user_id = callback.from_user.id
    balance = db.get_balance(user_id)
    items = db.get_all_catalog_items()
    text = f"📦 Каталог ParadiseCoin\n\n💰 Баланс: {balance:,} 🪙\n\n"
    for it in items:
        text += f"{it['name']} — {it['price']:,} 🪙\n"
        if it["description"]:
            text += f"   {it['description']}\n"
        text += "\n"
    text += "🥚 Яйца питомцев — в отдельном разделе\n\nВыберите товар:"
    await callback.message.edit_text(
        text,
        reply_markup=catalog_menu(),
        parse_mode=None
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("buy_"))
async def buy_item(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    item_type = callback.data.replace("buy_", "")
    price = db.get_price(item_type)
    if db.get_balance(user_id) < price:
        await callback.answer(f"❌ Нужно {price} 🪙", show_alert=True)
        return
    if item_type in ["admin", "unban"]:
        names = {"admin": "Админка", "unban": "Разбан"}
        await callback.message.edit_text(
            f"⚠️ Покупка {names[item_type]}\n\nЦена: {price:,} 🪙\n\nПодтвердить?",
            reply_markup=confirm_admin_purchase()
        )
        await state.set_state(CatalogStates.waiting_confirm_admin)
        await state.update_data(item_type=item_type)
        await callback.answer()
        return
    if db.add_purchase(user_id, item_type):
        await callback.message.edit_text(
            f"✅ Покупка успешна!\nСписано: {price:,} 🪙\nБаланс: {db.get_balance(user_id)} 🪙",
            reply_markup=back_to_catalog_keyboard()
        )
    else:
        await callback.answer("❌ Ошибка", show_alert=True)
    await callback.answer()


@dp.callback_query(F.data == "confirm_admin_buy")
async def confirm_admin_buy(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    item_type = (await state.get_data()).get("item_type", "admin")
    price = db.get_price(item_type)
    names = {"admin": "Админка", "unban": "Разбан"}
    await bot.send_message(
        config.OWNER_ID,
        f"🔔 Запрос на покупку {names[item_type]}\n"
        f"👤 @{callback.from_user.username or user_id}\n"
        f"💰 {price:,} 🪙\n"
        f"Подтвердить: /approve_{item_type}_{user_id}\n"
        f"Отклонить: /reject_{item_type}_{user_id}"
    )
    await callback.message.edit_text(
        "✅ Запрос отправлен владельцу.",
        reply_markup=back_to_catalog_keyboard()
    )
    await state.clear()
    await callback.answer()


# ==================== ЯЙЦА ====================

@dp.callback_query(F.data == "eggs_menu")
async def eggs_menu_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "🥚 Яйца питомцев\n\nВыберите яйцо:",
        reply_markup=eggs_menu()
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("egg_"))
async def open_egg_handler(callback: CallbackQuery):
    egg_type = callback.data.replace("egg_", "")
    # Проклятое яйцо — отдельно
    if egg_type == "cursed":
        await open_cursed_egg_handler(callback)
        return
    if egg_type not in db.PET_EGGS:
        await callback.answer("❌ Неизвестное яйцо", show_alert=True)
        return
    egg = db.PET_EGGS[egg_type]
    user_id = callback.from_user.id
    if db.get_balance(user_id) < egg["price"]:
        await callback.answer(f"❌ Нужно {egg['price']:,} 🪙", show_alert=True)
        return
    result = db.open_egg(user_id, egg_type)
    if not result:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    await callback.message.edit_text(
        f"🥚 {egg['name']} открыто!\n\n"
        f"Внутри: {result['name']}\n"
        f"Редкость: {result['rarity']}\n"
        f"Доход: {result['income']} 🪙 раз в 4 часа\n\n"
        f"💰 Баланс: {db.get_balance(user_id)} 🪙",
        reply_markup=back_to_catalog_keyboard()
    )
    await callback.answer("🎉 Питомец получен!")


@dp.callback_query(F.data.startswith("open_cursed"))
async def cursed_egg_open_confirm(callback: CallbackQuery):
    """Открытие проклятого яйца — сначала подтверждение"""
    stock = db.get_egg_stock()
    if not stock["active"] or stock["stock"] <= 0:
        await callback.answer("❌ Проклятое яйцо недоступно", show_alert=True)
        return
    user_id = callback.from_user.id
    if db.get_balance(user_id) < 10000:
        await callback.answer("❌ Нужно 10,000 🪙", show_alert=True)
        return
    await callback.message.edit_text(
        "🕯️ Проклятое яйцо\n\n"
        "Никто не знает, что находится внутри.\n"
        "Цена: 10,000 🪙\n\n"
        "Ты уверен?",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="✅ Открыть", callback_data="cursed_open_yes"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="catalog")
        ).as_markup()
    )
    await callback.answer()


@dp.callback_query(F.data == "cursed_open_yes")
async def cursed_open_yes(callback: CallbackQuery):
    user_id = callback.from_user.id
    result = db.open_cursed_egg(user_id)
    if not result:
        await callback.answer("❌ Ошибка или яйца кончились", show_alert=True)
        return
    await callback.message.edit_text(
        f"🕯️ Проклятое яйцо открыто...\n\n"
        f"Из него выполз: {result['name']}\n"
        f"Редкость: {result['rarity']}\n"
        f"Доход: {result['income']} 🪙 раз в 4 часа\n\n"
        f"Осталось яиц: {result['stock_left']}",
        reply_markup=back_to_catalog_keyboard()
    )
    # Оповещение о распродаже
    if result["stock_left"] <= 0:
        await announce_cursed_egg_sold_out()
    await callback.answer("👁️ Оно выбрало тебя")

@dp.callback_query(F.data.startswith("open_cursed"))
async def cursed_egg_open_confirm(callback: CallbackQuery):
    """Открытие проклятого яйца — сначала подтверждение"""
    stock = db.get_egg_stock()
    if not stock["active"] or stock["stock"] <= 0:
        await callback.answer("❌ Проклятое яйцо недоступно", show_alert=True)
        return
    user_id = callback.from_user.id
    if db.get_balance(user_id) < 10000:
        await callback.answer("❌ Нужно 10,000 🪙", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Открыть", callback_data="cursed_open_yes"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="catalog")
    )
    await callback.message.edit_text(
        "🕯️ Проклятое яйцо\n\n"
        "Никто не знает, что находится внутри.\n"
        "Цена: 10,000 🪙\n\n"
        "Ты уверен?",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@dp.callback_query(F.data == "cursed_open_yes")
async def cursed_open_yes(callback: CallbackQuery):
    user_id = callback.from_user.id
    result = db.open_cursed_egg(user_id)
    if not result:
        await callback.answer("❌ Ошибка или яйца кончились", show_alert=True)
        return
    await callback.message.edit_text(
        f"🕯️ Проклятое яйцо открыто...\n\n"
        f"Из него выполз: {result['name']}\n"
        f"Редкость: {result['rarity']}\n"
        f"Доход: {result['income']} 🪙 раз в 4 часа\n\n"
        f"Осталось яиц: {result['stock_left']}",
        reply_markup=back_to_catalog_keyboard()
    )
    # Оповещение о распродаже
    if result["stock_left"] <= 0:
        await announce_cursed_egg_sold_out()
    await callback.answer("👁️ Оно выбрало тебя")

# ==================== ПРОКЛЯТОЕ ЯЙЦО: ОПОВЕЩЕНИЯ ====================

async def announce_cursed_egg():
    """Оповещение о появлении проклятого яйца"""
    stock = db.get_egg_stock()
    if not stock["active"]:
        return
    text = (
        "🕯️ ВНИМАНИЕ.\n"
        "Сегодня в магазине ParadiseCoin появилось то, чего там быть не должно...\n\n"
        "🥚 Проклятое яйцо\n"
        "Никто не знает, что находится внутри.\n"
        f"🪙 Цена: 10,000 ParadiseCoin\n"
        f"📦 Осталось: {stock['stock']} яиц..\n\n"
        "⚠️ Когда они исчезнут из магазина, вернуть их будет невозможно.\n"
        "Если ты достаточно смелый — оно уже ждёт тебя."
    )
    # Всем пользователям в ЛС
    for u in db.get_all_users():
        try:
            await bot.send_message(u["user_id"], text)
        except Exception:
            pass
    # В чат флуда
    try:
        await bot.send_message(config.FLUD_CHAT_ID, text)
    except Exception:
        pass


async def announce_cursed_egg_sold_out():
    text = (
        "🕯️ Оно исчезло.\n"
        "Пять яиц проданы.\n"
        "Больше их сегодня не будет.\n"
        "...до следующей субботы. 👁️"
    )
    for u in db.get_all_users():
        try:
            await bot.send_message(u["user_id"], text)
        except Exception:
            pass
    try:
        await bot.send_message(config.FLUD_CHAT_ID, text)
    except Exception:
        pass


# ==================== ПИТОМЦЫ ====================

@dp.callback_query(F.data == "pets_menu")
async def pets_menu_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "🐾 Питомцы\n\nЧто будем делать?",
        reply_markup=pets_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "my_pets")
async def my_pets(callback: CallbackQuery):
    pets = db.get_user_pets(callback.from_user.id)
    if not pets:
        await callback.message.edit_text(
            "🐾 У вас пока нет питомцев.\nОткройте яйцо в каталоге!",
            reply_markup=pets_menu()
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        f"🐾 Ваши питомцы ({len(pets)}):\n\n"
        "✅ — активен (приносит доход)\n"
        "💤 — спит (не приносит доход)",
        reply_markup=my_pets_menu(pets)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("pet_info_"))
async def pet_info(callback: CallbackQuery):
    pet_id = int(callback.data.split("_")[2])
    pets = db.get_user_pets(callback.from_user.id)
    pet = next((p for p in pets if p["id"] == pet_id), None)
    if not pet:
        await callback.answer("❌ Не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"🐾 {pet['name']}\n"
        f"Редкость: {pet['rarity']}\n"
        f"Доход: {pet['income']} 🪙 раз в 4 часа\n"
        f"Статус: {'✅ активен' if pet['is_active'] else '💤 спит'}",
        reply_markup=pet_actions_menu(pet_id, pet["is_active"])
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("pet_activate_"))
async def pet_activate(callback: CallbackQuery):
    pet_id = int(callback.data.split("_")[2])
    ok = db.set_pet_active(pet_id, callback.from_user.id, True)
    if ok:
        await callback.answer("✅ Активирован")
    else:
        await callback.answer("❌ Максимум 2 активных питомца", show_alert=True)
    await pet_info_by_id(callback, pet_id)


@dp.callback_query(F.data.startswith("pet_deactivate_"))
async def pet_deactivate(callback: CallbackQuery):
    pet_id = int(callback.data.split("_")[2])
    db.set_pet_active(pet_id, callback.from_user.id, False)
    await callback.answer("💤 Деактивирован")
    await pet_info_by_id(callback, pet_id)


async def pet_info_by_id(callback: CallbackQuery, pet_id: int):
    pets = db.get_user_pets(callback.from_user.id)
    pet = next((p for p in pets if p["id"] == pet_id), None)
    if not pet:
        return
    await callback.message.edit_text(
        f"🐾 {pet['name']}\n"
        f"Редкость: {pet['rarity']}\n"
        f"Доход: {pet['income']} 🪙 раз в 4 часа\n"
        f"Статус: {'✅ активен' if pet['is_active'] else '💤 спит'}",
        reply_markup=pet_actions_menu(pet_id, pet["is_active"])
    )


@dp.callback_query(F.data.startswith("pet_gift_"))
async def pet_gift_start(callback: CallbackQuery, state: FSMContext):
    pet_id = int(callback.data.split("_")[2])
    await state.update_data(pet_id=pet_id)
    await callback.message.edit_text(
        "🎁 Введите username получателя (без @):",
        reply_markup=cancel_button()
    )
    await state.set_state(GiftPetStates.waiting_recipient)
    await callback.answer()


@dp.message(GiftPetStates.waiting_recipient)
async def pet_gift_send(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    conn = sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE username = ?", (target,))
    row = cur.fetchone()
    conn.close()
    if not row:
        await message.answer("❌ Пользователь не найден")
        return
    target_id = row[0]
    pet_id = (await state.get_data()).get("pet_id")
    if db.transfer_pet(pet_id, message.from_user.id, target_id):
        await message.answer(f"✅ Питомец подарен @{target}!")
        try:
            await bot.send_message(target_id, f"🎁 Вам подарили питомца!")
        except Exception:
            pass
    else:
        await message.answer("❌ Не удалось подарить")
    await state.clear()


@dp.callback_query(F.data == "gift_pet_start")
async def gift_pet_start(callback: CallbackQuery):
    pets = db.get_user_pets(callback.from_user.id)
    if not pets:
        await callback.answer("❌ У вас нет питомцев", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for p in pets:
        builder.row(InlineKeyboardButton(
            text=f"🎁 {p['name']}",
            callback_data=f"pet_gift_{p['id']}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="pets_menu"))
    await callback.message.edit_text("Кого подарить?", reply_markup=builder.as_markup())
    await callback.answer()


# ==================== ПРОФЕССИИ ====================

@dp.callback_query(F.data == "professions_menu")
async def professions_menu_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "💼 Профессии\n\nВыберите действие:",
        reply_markup=professions_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "my_profession")
async def my_profession(callback: CallbackQuery):
    prof = db.get_profession(callback.from_user.id)
    if not prof or not prof["profession"]:
        await callback.message.edit_text(
            "💼 У вас нет профессии.\nУстроиться стоит 1000 🪙.",
            reply_markup=professions_menu()
        )
        await callback.answer()
        return
    from database import PROFESSIONS
    info = PROFESSIONS[prof["profession"]]
    salary = db.get_profession_salary(prof["profession"], prof["level"])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💼 Работать", callback_data="work_now"))
    builder.row(InlineKeyboardButton(text="❌ Уволиться", callback_data="fire_profession"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="professions_menu"))
    await callback.message.edit_text(
        f"💼 {info['name']}\n\n"
        f"Уровень: {prof['level']}/5\n"
        f"Зарплата: {salary} 🪙\n"
        f"Кулдаун: {info['cooldown_hours']} ч\n"
        f"Дней отработано: {prof['days_worked']}/7 до повышения",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@dp.callback_query(F.data == "list_professions")
async def list_professions(callback: CallbackQuery):
    await callback.message.edit_text(
        "📋 Список профессий\n\nУстройство стоит 1000 🪙 (каждая смена после увольнения дороже на 1000):",
        reply_markup=professions_list_menu(callback.from_user.id)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("hire_"))
async def hire(callback: CallbackQuery):
    prof_key = callback.data.replace("hire_", "")
    # Проверка: Правитель только для админов/владельца
    if prof_key == "ruler":
        if callback.from_user.id not in config.ADMIN_IDS and callback.from_user.id != config.OWNER_ID:
            await callback.answer("❌ Только для администрации", show_alert=True)
            return
        # Максимум 3 правителя
        conn = sqlite3.connect(db.DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM professions WHERE profession = 'ruler'")
        count = cur.fetchone()[0]
        conn.close()
        if count >= 3:
            await callback.answer("❌ Уже 3 правителя. Больше нельзя.", show_alert=True)
            return
    ok, msg = db.hire_profession(callback.from_user.id, prof_key)
    await callback.answer(msg, show_alert=True)
    if ok:
        await my_profession(callback)


@dp.callback_query(F.data == "work_now")
async def work_now(callback: CallbackQuery):
    ok, msg = db.work_profession(callback.from_user.id)
    await callback.answer(msg, show_alert=not ok)
    if ok:
        await my_profession(callback)


@dp.callback_query(F.data == "fire_profession")
async def fire_profession(callback: CallbackQuery):
    conn = sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM professions WHERE user_id = ?", (callback.from_user.id,))
    conn.commit()
    conn.close()
    await callback.answer("✅ Вы уволены", show_alert=True)
    await callback.message.edit_text(
        "Вы уволились. Следующее трудоустройство будет дороже на 1000 🪙.",
        reply_markup=professions_menu()
    )


# ==================== ЕЖЕДНЕВНЫЕ ЗАДАНИЯ ====================

@dp.callback_query(F.data == "tasks_menu")
async def tasks_menu_handler(callback: CallbackQuery):
    db.assign_daily_tasks(callback.from_user.id)
    await callback.message.edit_text(
        "📋 Ваши ежедневные задания:",
        reply_markup=tasks_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "my_tasks")
async def my_tasks(callback: CallbackQuery):
    tasks = db.get_user_tasks(callback.from_user.id)
    if not tasks:
        await callback.message.edit_text(
            "📋 Заданий пока нет. Загляни позже!",
            reply_markup=tasks_menu()
        )
        await callback.answer()
        return
    from database import DAILY_TASKS
    text = "📋 Ежедневные задания:\n\n"
    for t in tasks:
        info = DAILY_TASKS[t["task_type"]]
        status = "✅" if t["is_done"] else f"{t['progress']}/{t['target']}"
        text += f"{info['name']} — {status} (+{t['reward']} 🪙)\n"
    await callback.message.edit_text(text, reply_markup=tasks_menu())
    await callback.answer()


# ==================== КОЛЕСО ФОРТУНЫ ====================

@dp.callback_query(F.data == "wheel_menu")
async def wheel_menu_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎡 Колесо фортуны\n\n"
        "Стоимость вращения: 200 🪙\n"
        "Кулдаун: раз в 3 часа\n\n"
        "Испытай удачу!",
        reply_markup=wheel_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "wheel_spin")
async def wheel_spin(callback: CallbackQuery):
    user_id = callback.from_user.id
    if db.get_balance(user_id) < 200:
        await callback.answer("❌ Нужно 200 🪙", show_alert=True)
        return

    can, remaining = db.can_spin_wheel(user_id)
    if not can:
        await callback.answer(
            f"⏳ Колесо будет доступно через {format_time(remaining)}",
            show_alert=True
        )
        return

    db.update_balance(user_id, -200)
    db.set_wheel_time(user_id)

    sectors = db.get_wheel_sectors()
    total = sum(s["chance"] for s in sectors)
    r = random.uniform(0, total)
    upto = 0
    chosen = sectors[-1]
    for s in sectors:
        if upto + s["chance"] >= r:
            chosen = s
            break
        upto += s["chance"]

    reward_text = ""
    if chosen["reward_type"] == "money":
        amt = int(chosen["reward_value"])
        db.update_balance(user_id, amt)
        reward_text = f"+{amt} 🪙"
    elif chosen["reward_type"] == "pet":
        pet_name = chosen["reward_value"]
        conn = sqlite3.connect(db.DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income) VALUES (?, ?, ?, ?, ?)",
            (user_id, pet_name, pet_name.split()[0], "обычный", 100)
        )
        conn.commit()
        conn.close()
        reward_text = f"Питомец {pet_name}"
    elif chosen["reward_type"] == "egg":
        egg_type = chosen["reward_value"]
        if egg_type in db.PET_EGGS:
            egg = db.PET_EGGS[egg_type]
            pets_list = egg["pets"]
            total_p = sum(p[3] for p in pets_list)
            rp = random.uniform(0, total_p)
            upto_p = 0
            chosen_p = pets_list[-1]
            for p in pets_list:
                if upto_p + p[3] >= rp:
                    chosen_p = p
                    break
                upto_p += p[3]
            conn = sqlite3.connect(db.DB_NAME)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO pets (user_id, pet_name, pet_emoji, rarity, income) VALUES (?, ?, ?, ?, ?)",
                (user_id, chosen_p[0], chosen_p[0].split()[0], chosen_p[1], chosen_p[2])
            )
            conn.commit()
            conn.close()
            reward_text = f"{egg['name']}: {chosen_p[0]}"
        else:
            reward_text = "яйцо"
    elif chosen["reward_type"] == "random_purchase":
        items = ["change_role", "anti_warn", "immunity", "video"]
        chosen_item = random.choice(items)
        conn = sqlite3.connect(db.DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO purchases (user_id, item_type) VALUES (?, ?)",
            (user_id, chosen_item)
        )
        conn.commit()
        conn.close()
        names = {"change_role": "Смена роли", "anti_warn": "Антиварн",
                 "immunity": "Иммунитет", "video": "Видео с вами"}
        reward_text = f"Покупка: {names[chosen_item]}"

    await callback.message.edit_text(
        f"🎡 Колесо крутится...\n\n"
        f"Выпало: {chosen['label']}\n"
        f"Приз: {reward_text}\n\n"
        f"💰 Баланс: {db.get_balance(user_id)} 🪙",
        reply_markup=wheel_menu()
    )
    await callback.answer("🎉")
    
# ==================== ПРОМОКОД ====================

@dp.message(F.text.lower() == "промокод")
async def promo_word(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("🎟️ Промокоды активируются только в личке бота.")
        return
    await message.answer("🎟️ Введите промокод:")
    await state.set_state(PromoStates.waiting_code)


@dp.message(PromoStates.waiting_code)
async def promo_activate(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    ok, result = db.activate_promo(code, message.from_user.id)
    if ok:
        await message.answer(f"✅ Промокод активирован!\nНаграда: {result}")
    else:
        await message.answer(f"❌ {result}")
    await state.clear()


# ==================== ВОРД-ТРИГГЕРЫ ====================

@dp.message(F.text.lower() == "банк")
async def bank_word(message: Message):
    uid = message.from_user.id
    can, remaining = db.can_claim_bonus(uid)
    if can:
        bonus = db.claim_bonus(uid)
        await message.answer(
            f"🏦 Вы получили бонус {bonus} 🪙!\n"
            f"💰 Баланс: {db.get_balance(uid)} 🪙\n"
            "Следующий бонус через 3 часа."
        )
    else:
        await message.answer(f"⏳ Бонус ещё не доступен. Осталось: {format_time(remaining)}.")


@dp.message(F.text.lower() == "баланс")
async def balance_word(message: Message):
    uid = message.from_user.id
    await message.answer(
        f"💰 Ваш баланс: {db.get_balance(uid):,} ParadiseCoin\n"
        f"🏆 Место: #{db.get_user_rank(uid)}"
    )


@dp.message(F.text.lower() == "чистка окончена")
async def clean_finished(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("❌ Нет прав")
        return
    count = db.clear_all_immunities()
    await message.answer(f"🧹 Чистка окончена! Аннулировано иммунитетов: {count}")


@dp.message(F.text.lower() == "топ нью")
async def top_new(message: Message):
    users = db.get_today_new_members(exclude_user_id=bot.id)
    if not users:
        await message.answer("📊 Топ Нью за сегодня: пусто")
        return
    text = "📊 Топ Нью за сегодня:\n\n"
    for i, (uid, uname, _) in enumerate(users, 1):
        text += f"{i}. @{uname or uid}\n"
    await message.answer(text)


@dp.message(F.text.lower() == "кто нью")
async def who_new(message: Message):
    users = db.get_new_members(4, exclude_user_id=bot.id)
    if not users:
        await message.answer("📊 Кто Нью (4 дня): пусто")
        return
    text = "📊 Кто Нью (4 дня):\n\n"
    for i, (uid, uname, joined) in enumerate(users, 1):
        text += f"{i}. @{uname or uid} — {joined[:16]}\n"
    await message.answer(text)


@dp.message(F.text.lower() == "калл нью")
async def call_new(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("❌ Только админы")
        return
    users = db.get_new_members(1, exclude_user_id=bot.id)
    if not users:
        await message.answer("📢 Нет новых")
        return
    uid, uname, joined = users[0]
    await message.answer(f"📢 Нью в чате: @{uname or uid} ({joined[:16]})")

@dp.message(F.text.lower() == "работать")
async def work_word_handler(message: Message):
    """Ворд-триггер 'работать' — получить зарплату по профессии"""
    user_id = message.from_user.id

    # Проверяем, есть ли пользователь
    if not db.get_user(user_id):
        username = message.from_user.username or str(user_id)
        db.create_user(user_id, username)

    # Проверяем бан
    user = db.get_user(user_id)
    if user and user["is_banned"]:
        await message.answer("🚫 Вы заблокированы.")
        return

    prof = db.get_profession(user_id)
    if not prof or not prof["profession"]:
        await message.answer(
            "💼 У вас нет профессии.\n"
            "Зайдите в раздел «💼 Профессии» в боте и устройтесь на работу."
        )
        return

    ok, msg = db.work_profession(user_id)
    if ok:
        await message.answer(
            f"💼 {msg}\n"
            f"💰 Баланс: {db.get_balance(user_id)} 🪙"
        )
    else:
        await message.answer(f"⏳ {msg}")

# ==================== ВХОД НОВЫХ УЧАСТНИКОВ ====================

@dp.message(F.new_chat_members)
async def on_user_join(message: Message):
    for user in message.new_chat_members:
        if user.id == bot.id:
            continue
        uname = user.username or user.full_name or str(user.id)
        db.add_new_member(user.id, uname)
        logger.info(f"👤 Новый: @{uname}")


# ==================== ПЕРЕВОДЫ ====================

@dp.callback_query(F.data == "transfer_menu")
async def transfer_menu_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "💸 Переводы\n\nВыберите действие:",
        reply_markup=transfer_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "transfer_start")
async def transfer_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "💸 Введите username получателя (без @):",
        reply_markup=cancel_button()
    )
    await state.set_state(TransferStates.waiting_recipient)
    await callback.answer()


@dp.message(TransferStates.waiting_recipient)
async def transfer_recipient(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    conn = sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username FROM users WHERE username = ?", (target,))
    row = cur.fetchone()
    conn.close()
    if not row:
        await message.answer("❌ Не найден. Попробуйте снова:")
        return
    if row[0] == message.from_user.id:
        await message.answer("❌ Нельзя себе")
        return
    await state.update_data(recipient_id=row[0], recipient_name=target)
    await message.answer(
        f"💸 Получатель: @{target}\nВаш баланс: {db.get_balance(message.from_user.id)} 🪙\n\nВведите сумму:"
    )
    await state.set_state(TransferStates.waiting_amount)


@dp.message(TransferStates.waiting_amount)
async def transfer_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
        if amount <= 0:
            await message.answer("❌ Сумма > 0")
            return
        uid = message.from_user.id
        if amount > db.get_balance(uid):
            await message.answer("❌ Недостаточно")
            return
        await state.update_data(amount=amount)
        await message.answer(
            f"✅ Подтвердите перевод {amount} 🪙?",
            reply_markup=confirm_transfer_keyboard()
        )
        await state.set_state(TransferStates.waiting_confirm)
    except ValueError:
        await message.answer("❌ Число")


@dp.callback_query(F.data == "confirm_transfer")
async def confirm_transfer(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    rid = data["recipient_id"]
    rname = data["recipient_name"]
    amount = data["amount"]
    sid = callback.from_user.id
    if amount > db.get_balance(sid):
        await callback.message.edit_text("❌ Недостаточно", reply_markup=back_to_main_menu())
        await state.clear()
        return
    db.update_balance(sid, -amount)
    db.update_balance(rid, amount)
    await callback.message.edit_text(
        f"✅ Переведено {amount} 🪙 @{rname}\nБаланс: {db.get_balance(sid)} 🪙",
        reply_markup=back_to_main_menu()
    )
    try:
        await bot.send_message(rid, f"💰 Вам перевели {amount} 🪙 от @{callback.from_user.username or sid}")
    except Exception:
        pass
    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == "cancel_transfer")
async def cancel_transfer(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено", reply_markup=back_to_main_menu())
    await callback.answer()


# ==================== ПОДТВЕРЖДЕНИЕ ПОКУПОК ====================

@dp.message(F.text.startswith("/approve_"))
async def approve_purchase(message: Message):
    if message.from_user.id != config.OWNER_ID:
        await message.answer("❌ Нет прав")
        return
    parts = message.text.split("_")
    if len(parts) < 3:
        return
    item_type = parts[1]
    uid = int(parts[2])
    user = db.get_user(uid)
    if not user:
        await message.answer("❌ Не найден")
        return
    price = db.get_price(item_type)
    if user["balance"] < price:
        await message.answer(f"❌ Мало монет")
        return
    if db.add_purchase(uid, item_type):
        await message.answer(f"✅ Покупка подтверждена")
        try:
            await bot.send_message(uid, f"✅ Покупка {item_type} подтверждена!")
        except Exception:
            pass


@dp.message(F.text.startswith("/reject_"))
async def reject_purchase(message: Message):
    if message.from_user.id != config.OWNER_ID:
        return
    await message.answer("❌ Отклонено")


# ==================== ОТМЕНА ====================

@dp.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено", reply_markup=back_to_main_menu())
    await callback.answer()


@dp.callback_query(F.data == "claim_bonus")
async def claim_bonus_cb(callback: CallbackQuery):
    uid = callback.from_user.id
    can, remaining = db.can_claim_bonus(uid)
    if can:
        bonus = db.claim_bonus(uid)
        await callback.message.edit_text(
            f"🎁 Бонус: +{bonus} 🪙\nБаланс: {db.get_balance(uid)} 🪙",
            reply_markup=back_to_main_menu()
        )
    else:
        await callback.answer(f"⏳ Осталось: {format_time(remaining)}", show_alert=True)
    await callback.answer()

# ==================== СУНДУКИ ====================

@dp.callback_query(F.data.startswith("open_chest_"))
async def open_chest(callback: CallbackQuery):
    message_id = int(callback.data.replace("open_chest_", ""))
    chest = active_chests.get(message_id)
    
    if not chest:
        await callback.answer("❌ Сундук уже открыт или исчез", show_alert=True)
        return
    
    if chest["claimed"]:
        await callback.answer("❌ Кто-то уже забрал сундук!", show_alert=True)
        return
    
    # Отмечаем как забранный
    chest["claimed"] = True
    user_id = callback.from_user.id
    username = callback.from_user.username or str(user_id)
    
    # Выдаём награду
    reward = db.roll_chest_reward()
    reward_text = db.give_chest_reward(user_id, reward)
    
    # Обновляем сообщение
    try:
        await callback.message.edit_text(
            f"🎁 Сундук открыт!\n\n"
            f"🏆 @{username} первым успел и получил:\n"
            f"✨ {reward_text}",
            parse_mode=None
        )
    except Exception:
        pass
    
    await callback.answer(f"🎉 Ты получил: {reward_text}", show_alert=True)
    
    # Удаляем из активных
    active_chests.pop(message_id, None)


@dp.message(Command("test_chest"))
async def test_chest(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    await spawn_chest()
    await message.answer("✅ Сундук заспавнен в чате флуда")

# ==================== ФОНОВЫЕ ЗАДАЧИ ====================

async def weekly_clean_reminder():
    while True:
        now = datetime.now()
        days = (6 - now.weekday()) % 7
        target = now + timedelta(days=days)
        target = target.replace(hour=12, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=7)
        await asyncio.sleep((target - now).total_seconds())
        for aid in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    aid,
                    "🔔 Напоминание о чистке!\n\nПосле завершения напишите 'чистка окончена'."
                )
            except Exception:
                pass


async def clear_old_members():
    while True:
        await asyncio.sleep(86400)
        db.clear_old_new_members(7)


async def daily_tasks_reset():
    """Сброс ежедневных заданий в 5:00 МСК"""
    while True:
        now = datetime.now()
        target = now.replace(hour=2, minute=0, second=0, microsecond=0)  # 5 МСК = 2 UTC
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        db.reset_daily_tasks()
        logger.info("📋 Ежедневные задания обновлены")


async def pets_income():
    """Доход питомцев раз в 4 часа"""
    while True:
        await asyncio.sleep(14400)  # 4 часа
        users = db.get_all_users()
        for u in users:
            if u["is_banned"]:
                continue
            pets = db.get_user_pets(u["user_id"])
            total = sum(p["income"] for p in pets if p["is_active"])
            if total > 0:
                db.update_balance(u["user_id"], total)
        logger.info("🐾 Доход питомцев начислен")


async def cursed_egg_scheduler():
    """Случайное появление проклятого яйца в субботу вечером"""
    while True:
        now = datetime.now()
        # Проверяем каждые 30 минут
        await asyncio.sleep(1800)
        # Если суббота (weekday=5), вечер (17:00–22:00) и яйцо неактивно
        if now.weekday() == 5 and 17 <= now.hour <= 22:
            stock = db.get_egg_stock()
            if not stock["active"]:
                # 10% шанс, что активируется в этот получасовой слот
                if random.random() < 0.1:
                    db.set_cursed_egg_active(5)
                    await announce_cursed_egg()
                    logger.info("🕯️ Проклятое яйцо активировано")

async def spawn_chest():
    """Появление сундука в чате флуда"""
    try:
        builder = InlineKeyboardBuilder()
        # message_id узнаем после отправки, поэтому сначала без кнопки
        msg = await bot.send_message(
            config.FLUD_CHAT_ID,
            "🎁 СУНДУК С СОКРОВИЩАМИ!\n\n"
            "В Paradise Reef появился сундук. Кто первый откроет — тому приз!\n\n"
            "⏳ Успей, пока он не исчез!"
        )
        # Теперь добавляем кнопку с message_id
        builder.row(InlineKeyboardButton(
            text="🎁 Открыть сундук",
            callback_data=f"open_chest_{msg.message_id}"
        ))
        await msg.edit_reply_markup(reply_markup=builder.as_markup())
        
        active_chests[msg.message_id] = {"chat_id": config.FLUD_CHAT_ID, "claimed": False}
        
        # Через 10 минут удаляем, если не открыт
        await asyncio.sleep(600)
        if msg.message_id in active_chests and not active_chests[msg.message_id]["claimed"]:
            active_chests.pop(msg.message_id, None)
            try:
                await msg.edit_text(
                    "🎁 Сундук исчез... никто не успел его открыть 😢"
                )
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Ошибка спавна сундука: {e}")


async def chest_scheduler():
    """Спавнит сундуки раз в 5–8 часов"""
    while True:
        # Случайное время ожидания от 5 до 8 часов
        wait = random.randint(18000, 28800)
        await asyncio.sleep(wait)
        await spawn_chest()
    
# ==================== MAIN ====================

async def main():
    print_banner()
    logger.info("🚀 ParadiseCoin запускается...")
    db.remove_bot_from_new_members(bot.id)
    await bot.set_my_commands([
        {"command": "start", "description": "🏠 Запустить бота"},
        {"command": "help", "description": "ℹ️ Помощь"},
        {"command": "admin", "description": "👑 Админ-панель"},
    ])
    asyncio.create_task(weekly_clean_reminder())
    asyncio.create_task(clear_old_members())
    asyncio.create_task(daily_tasks_reset())
    asyncio.create_task(pets_income())
    asyncio.create_task(cursed_egg_scheduler())
    asyncio.create_task(chest_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

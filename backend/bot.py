from PIL import Image
import re
import os
from banner import print_banner
import asyncio
import logging
import db_compat as sqlite3
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from tictactoe import router as ttt_router
from minesweeper import router as ms_router
from friends import router as friends_router
from cosmetics import router as cos_router
from tech_admin import router as tech_admin_router

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ChatMemberUpdated
from aiogram import BaseMiddleware
from aiogram.types import FSInputFile

import config
import database as db
import world_events as world_events
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


RULES_TEXT = (
    "📜 Правила Lavender Dream\n\n"
    "1. Используй бота и Mini App только по назначению и соблюдай правила Telegram.\n"
    "2. Запрещены дюпы, эксплуатация багов, подмена запросов, накрутка наград и обход лимитов. "
    "Если нашёл баг — сообщи через «Обратную связь», не используй его для выгоды.\n"
    "3. Запрещены спам, угрозы, оскорбления и попытки мешать работе бота.\n"
    "4. Максимум одного перевода или денежного подарка — 200 000 LDC. Денежные переводы ограничены пятью успешными операциями в сутки (UTC), а любые подарки — пятью успешными отправками в сутки; лимиты общие для бота и Mini App.\n"
    "5. Не пытайся обходить ограничения игр, ставок, покупок и аккаунтов. Подозрительные действия могут проверяться по техническим журналам.\n"
    "6. За нарушения администрация может отменить неправомерные операции, ограничить доступ или заблокировать аккаунт.\n"
    "7. LDC и LCC — внутриигровые валюты Lavender Dream; они не являются реальными деньгами и не гарантируют денежную стоимость.\n\n"
    "Продолжая пользоваться ботом, ты соглашаешься соблюдать эти правила. "
    "Вопросы и жалобы отправляй через /feedback."
)


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

# Активные сундуки: {message_id: {"chat_id": ..., "claimed": bool}}
active_chests = {}

dp.include_router(admin_router)
dp.include_router(ttt_router)
dp.include_router(ms_router)
dp.include_router(friends_router)
dp.include_router(cos_router)
dp.include_router(tech_admin_router)

class TransferStates(StatesGroup):
    waiting_recipient = State()
    waiting_amount = State()
    waiting_confirm = State()

class FeedbackStates(StatesGroup):
    waiting_message = State()


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
_task_active_hour_seen = set()


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


def _apply_daily_task_progress(user_id: int, task_types: list[str]) -> None:
    """Apply a batch of daily-task updates off the asyncio event loop."""
    db.assign_daily_tasks(user_id)
    for task_type in task_types:
        db.update_task_progress(user_id, task_type, 1)


class DailyTaskProgressMiddleware(BaseMiddleware):
    """Tracks daily-task progress without blocking other bot updates on DB I/O."""
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.from_user and not event.from_user.is_bot:
            try:
                uid = event.from_user.id
                text_value = event.text or event.caption or ""
                now = datetime.now(ZoneInfo("Europe/Moscow"))
                task_types = []
                if event.chat.type in {"group", "supergroup"}:
                    # Basic message/reply/photo/audio/night tasks are handled by
                    # track_daily_tasks below. This middleware handles newer
                    # task types only, avoiding double progress on the same message.
                    if len(text_value) >= 50:
                        task_types.append("long_messages")
                    if "?" in text_value or "？" in text_value:
                        task_types.append("question_messages")
                    if re.search(r"https?://|t\.me/", text_value, re.I):
                        task_types.append("links")
                    if re.search(r"[😀-🙏🌀-🗿]", text_value):
                        task_types.append("emoji_messages")
                    if now.hour < 9:
                        task_types.append("morning_message")
                    if now.weekday() >= 5:
                        task_types.append("weekend_messages")

                    hour_key = (uid, db.daily_task_date(), now.hour)
                    if hour_key not in _task_active_hour_seen:
                        _task_active_hour_seen.add(hour_key)
                        task_types.append("active_hours")
                    if len(_task_active_hour_seen) > 50000:
                        # Drop expired hour keys only; never clear current-hour
                        # keys or active users could earn the same task repeatedly.
                        current_date = db.daily_task_date()
                        current_hour_keys = {
                            key for key in _task_active_hour_seen
                            if key[1] == current_date and key[2] == now.hour
                        }
                        _task_active_hour_seen.intersection_update(current_hour_keys)
                await asyncio.to_thread(_apply_daily_task_progress, uid, task_types)
            except Exception:
                logger.exception("Не удалось обновить прогресс ежедневного задания")
        return await handler(event, data)


dp.message.middleware(DailyTaskProgressMiddleware())


@dp.my_chat_member()
async def track_bot_block_status(event: ChatMemberUpdated):
    """Отслеживает блокировку/разблокировку бота в личных сообщениях."""
    if event.chat.type != "private":
        return

    user_id = event.chat.id
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    try:
        if new_status == "kicked":
            db.mark_user_blocked_bot(user_id)
        elif old_status == "kicked" and new_status in {"member", "administrator"}:
            db.unmark_user_blocked_bot(user_id)
    except Exception:
        logger.exception("Не удалось обновить статус блокировки бота user_id=%s", user_id)


# ==================== СТАРТ + ТУТОРИАЛ ====================

TUTORIAL_STEPS = [
    "🌊 Добро пожаловать в Lavender Dream!\n\n"
    "Привет! 🐚\n"
    "Ты попал(а) в бота Lavender Dream — здесь можно общаться, выполнять задания, играть и зарабатывать Lavender Dream Coin! 🪙\n\n"
    "Давай быстро разберёмся, как всё работает.",

    "🏝️ 1. Главное меню\n\n"
    "Здесь находятся основные разделы бота:\n\n"
    "🏠 Главная — основная информация и быстрый доступ к функциям.\n"
    "🎮 Игры — мини-игры, в которых можно заработать Lavender Dream Coin.\n"
    "🛍️ Каталог — товары и услуги, которые можно приобрести за Lavender Dream Coin.\n"
    "💸 Переводы — отправляй Lavender Dream Coin другим участникам.\n"
    "👤 Профиль — твой баланс, статистика и другая информация.",

    "🪙 2. Lavender Dream Coin\n\n"
    "Lavender Dream Coin — главная валюта нашего флуда.\n\n"
    "Зарабатывать её можно разными способами:\n"
    "• участвовать в играх;\n"
    "• выполнять ежедневные задания;\n"
    "• проявлять активность во флуде;\n"
    "• пользоваться другими функциями бота.\n"
    "А тратить Lavender Dream Coin можно в Каталоге или отправлять другим участникам.",

    "📋 3. Ежедневные задания\n\n"
    "Каждый день бот выбирает несколько заданий специально для участников флуда.\n\n"
    "Выполняй их, чтобы получать награды и увеличивать свой баланс! 🪙",

    "🛍️ 4. Каталог\n\n"
    "В каталоге можно найти различные товары и услуги за Lavender Dream Coin.\n\n"
    "Но будь внимателен(на)… 👀\n"
    "Иногда в магазине могут появляться секретные товары, которых обычно там нет.",

    "🌊 5. Самое главное\n\n"
    "Не бойся экспериментировать!\n\n"
    "Заходи в разделы бота, выполняй задания, играй, зарабатывай Lavender Dream Coin и следи за новостями.\n\n"
    "Добро пожаловать в Lavender Dream 🤍\n"
    "Удачи… она тебе пригодится."
]

def _load_start_state(user_id: int, username: str):
    """Выполняет синхронные PostgreSQL-операции /start вне event loop."""
    user = db.get_user(user_id)
    is_new = user is None
    if is_new:
        db.create_user(user_id, username)
        user = db.get_user(user_id)

    if user and user["is_banned"] == 1:
        return is_new, user, None, None, None
    if user and user["is_banned"] == 2:
        db.set_user_ban(user_id, False)
        user["is_banned"] = 0

    settings = db.get_user_settings(user_id)
    db.assign_daily_tasks(user_id)
    balance = user["balance"] if user else db.get_balance(user_id)
    rank = db.get_user_rank(user_id) if settings["tutorial_done"] else None
    return is_new, user, settings, balance, rank


@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or str(user_id)

    try:
        is_new, user, settings, balance, rank = await asyncio.to_thread(
            _load_start_state, user_id, username
        )
    except Exception:
        logger.exception("Не удалось обработать /start user_id=%s", user_id)
        await message.answer(
            "⚠️ Сейчас не удалось подключиться к базе данных. Попробуй ещё раз через несколько секунд."
        )
        return

    if user and user["is_banned"] == 1:
        await message.answer("🚫 Вы заблокированы. Обратитесь к администратору.")
        return

    # Keep the most recent owner mappings instead of clearing all protections at once.
    while len(message_owners) > 5000:
        message_owners.pop(next(iter(message_owners)), None)

    # Если туториал ещё не пройден — показываем его
    if not settings["tutorial_done"]:
        sent = await message.answer(
            TUTORIAL_STEPS[0],
            reply_markup=tutorial_nav_menu(1, len(TUTORIAL_STEPS) - 1)
        )
        message_owners[sent.message_id] = user_id
        return

    # Иначе — сразу главное меню + подсказки (одним сообщением)
    welcome_text = (
        f"🏝️ Lavender Dream Coin\n\n"
        f"💰 Баланс: {balance} 🪙\n"
        f"🏆 Место в топе: #{rank}\n\n"
    )

    if settings["hints_enabled"] and is_new:
        welcome_text += (
            "💡 **Подсказки для новичка:**\n"
            "🎁 Раздел «Бонус» — от 15 до 100 🪙\n"
            "💬 Слово `банк` в чате — тоже бонус\n"
            "🎮 Игры — заработок монет\n"
            "📋 Ежедневные задания — до 250 🪙\n\n"
        )

    welcome_text += "Выберите раздел:"

    sent = await message.answer(
        welcome_text,
        reply_markup=main_menu(user_id),
        parse_mode="Markdown"
    )
    message_owners[sent.message_id] = user_id


@dp.message(Command("rules"))
async def rules_command(message: Message):
    sent = await message.answer(RULES_TEXT, reply_markup=back_to_main_menu())
    message_owners[sent.message_id] = message.from_user.id


@dp.callback_query(F.data == "show_rules")
async def rules_callback(callback: CallbackQuery):
    sent = await callback.message.answer(RULES_TEXT, reply_markup=back_to_main_menu())
    message_owners[sent.message_id] = callback.from_user.id
    await callback.answer()


@dp.message(Command("feedback"))
async def feedback_command(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("✉️ Для обратной связи напиши мне в личные сообщения и используй /feedback.")
        return
    await state.set_state(FeedbackStates.waiting_message)
    await message.answer("✉️ Напиши одним сообщением, что хочешь передать владельцу бота. Можно приложить фото или файл. Для отмены отправь /cancel.")


@dp.callback_query(F.data == "feedback_start")
async def feedback_start_callback(callback: CallbackQuery, state: FSMContext):
    if not callback.message or callback.message.chat.type != "private":
        await callback.answer("Открой бота в личных сообщениях, чтобы отправить обращение.", show_alert=True)
        return
    await state.set_state(FeedbackStates.waiting_message)
    await callback.message.answer("✉️ Напиши одним сообщением, что хочешь передать владельцу бота. Можно приложить фото или файл. Для отмены отправь /cancel.")
    await callback.answer()


@dp.message(FeedbackStates.waiting_message)
async def feedback_receive(message: Message, state: FSMContext):
    if message.text and message.text.strip().lower() in {"/cancel", "отмена"}:
        await state.clear()
        sent = await message.answer("Обращение отменено.", reply_markup=main_menu(message.from_user.id))
        message_owners[sent.message_id] = message.from_user.id
        return
    user = message.from_user
    username = f"@{user.username}" if user.username else "(без username)"
    header = (
        "✉️ Новое обращение Lavender Dream\n"
        f"От: {user.full_name} {username}\n"
        f"User ID: {user.id}\n"
        f"Chat ID: {message.chat.id}"
    )
    try:
        await bot.send_message(config.OWNER_ID, header)
        await bot.copy_message(config.OWNER_ID, message.chat.id, message.message_id)
        db.log_action(user.id, "feedback", f"Обращение отправлено владельцу; username={username}", amount=0)
        sent = await message.answer("✅ Сообщение отправлено владельцу Lavender Dream. Спасибо!", reply_markup=main_menu(user.id))
        message_owners[sent.message_id] = user.id
    except Exception:
        logger.exception("Не удалось доставить обратную связь user_id=%s", user.id)
        await message.answer("❌ Не получилось доставить сообщение. Попробуй позже или отправь /feedback ещё раз.")
    finally:
        await state.clear()


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
        "✅ Туториал завершён!\n\nУдачи в Lavender Dream! 🤍",
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
        "ℹ️ Помощь Lavender Dream Coin\n\n"
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
        f"🏝️ Lavender Dream Coin\n\n"
        f"💰 Баланс: {db.get_balance(user_id)} 🪙\n"
        f"🏆 Место в топе: #{db.get_user_rank(user_id)}",
        reply_markup=main_menu(user_id),
        parse_mode=None
    )
    await callback.answer()

# ==================== ПОДСКАЗКИ ====================

@dp.callback_query(F.data == "hints_menu")
async def hints_menu_handler(callback: CallbackQuery, answer_callback: bool = True):
    user_id = callback.from_user.id
    settings = db.get_user_settings(user_id)
    enabled = settings["hints_enabled"]

    builder = InlineKeyboardBuilder()
    if enabled:
        builder.row(InlineKeyboardButton(
            text="❌ Отключить подсказки",
            callback_data="hints_toggle"
        ))
    else:
        builder.row(InlineKeyboardButton(
            text="✅ Включить подсказки",
            callback_data="hints_toggle"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))

    status = "🟢 включены" if enabled else "🔴 отключены"
    await callback.message.edit_text(
        f"💡 Подсказки для новичков\n\n"
        f"Статус: {status}\n\n"
        f"Когда подсказки включены, бот при входе присылает советы — "
        f"где взять бонус и как заработать Lavender Dream Coin.",
        reply_markup=builder.as_markup()
    )
    if answer_callback:
        await callback.answer()


@dp.callback_query(F.data == "hints_toggle")
async def hints_toggle(callback: CallbackQuery):
    user_id = callback.from_user.id
    settings = db.get_user_settings(user_id)
    new_value = not settings["hints_enabled"]
    db.set_hints_enabled(user_id, new_value)
    await callback.answer("✅ Подсказки включены" if new_value else "❌ Подсказки отключены")
    await hints_menu_handler(callback, answer_callback=False)

# ==================== ТОП ====================

@dp.callback_query(F.data == "main_home")
async def show_home(callback: CallbackQuery):
    top_users = db.get_top_users(10)
    if not top_users:
        await callback.message.edit_text("📊 Топ пуст.", reply_markup=home_menu())
        await callback.answer()
        return
    medals = ["🥇", "🥈", "🥉"]
    text = "🏆 Топ игроков Lavender Dream Coin\n\n"
    for i, (uid, uname, bal) in enumerate(top_users, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        # Используем display_name с косметикой
        display_name = db.get_display_name(uid)
        text += f"{medal} {display_name} — {bal:,} 🪙\n"
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
    db.check_badges(user_id)
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
    cosmetics = db.get_cosmetics(user_id)
    badges = db.get_badges(user_id)

    # === Формируем отображаемое имя с косметикой ===
    display_name = db.get_display_name(user_id)
    # Статус
    status_text = ""
    if cosmetics["status"] and cosmetics["status"] in db.STATUSES:
        status_text = f"{db.STATUSES[cosmetics['status']]['name']}\n"
    # Значки
    badges_text = ""
    if badges:
        badges_text = " ".join(db.BADGES[b]["emoji"] for b in badges if b in db.BADGES) + "\n"

    text = (
        f"👤 Профиль\n"
        f"{display_name}\n"
        f"{status_text}"
        f"{badges_text}\n"
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
    if bet < 500:
        await callback.answer("❌ Минимальная ставка — 500 LDC.", show_alert=True)
        return
    if db.get_balance(user_id) < bet:
        await callback.answer(f"❌ Недостаточно монет", show_alert=True)
        return
    if game_type == "slot":
        win, msg = play_slot(bet)
        await _process_game(callback, user_id, win, msg, game_type)
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
        await _process_game(callback, user_id, win, msg, game_type)
    elif game_type == "number":
        await callback.message.edit_text("🃏 Введите число 1–10:", reply_markup=cancel_button())
        await state.update_data(bet=bet)
        await state.set_state(GameStates.waiting_number_guess)
        await callback.answer()


async def _process_game(callback: CallbackQuery, user_id: int, net_change: int, msg: str, game_type: str = "slot"):
    allowed, remaining = db.register_game_play(user_id, game_type)
    if not allowed:
        await callback.answer(f"⏳ Эта игра на перерыве ещё {format_time(remaining)}", show_alert=True)
        return
    # Игровые функции возвращают чистое изменение баланса.
    if not db.update_balance(user_id, net_change):
        await callback.answer("❌ Не удалось обновить баланс.", show_alert=True)
        return
    db.update_game_stats(user_id, net_change > 0, abs(net_change))
    try:
        db.assign_daily_tasks(user_id)
        db.update_task_progress(user_id, "games", 1)
        try:
            await asyncio.to_thread(world_events.record_season_activity, user_id, "game", 1)
        except Exception:
            logger.exception("Не удалось обновить сезонный прогресс игры")
    except Exception:
        logger.exception("Не удалось обновить задание за сыгранную игру")
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
    if bet < 500:
        await callback.answer("❌ Минимальная ставка — 500 LDC.", show_alert=True)
        return
    if db.get_balance(callback.from_user.id) < bet:
        await callback.answer("❌ Недостаточно LDC.", show_alert=True)
        return
    win, msg = play_coin(bet, choice)
    await _process_game(callback, callback.from_user.id, win, msg, "coin")


@dp.message(GameStates.waiting_dice_guess)
async def handle_dice(message: Message, state: FSMContext):
    try:
        guess = int(message.text)
        if not 1 <= guess <= 6:
            await message.answer("❌ Число от 1 до 6")
            return
        bet = (await state.get_data()).get("bet")
        uid = message.from_user.id
        allowed, remaining = db.register_game_play(uid, "dice")
        if not allowed:
            await message.answer(f"⏳ Кубик на перерыве ещё {format_time(remaining)}")
            await state.clear()
            return
        win, msg = play_dice(bet, guess)
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
        uid = message.from_user.id
        allowed, remaining = db.register_game_play(uid, "number")
        if not allowed:
            await message.answer(f"⏳ Угадай число на перерыве ещё {format_time(remaining)}")
            await state.clear()
            return
        win, msg = play_number(bet, guess)
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
    text = f"📦 Каталог Lavender Dream Coin\n\n💰 Баланс: {balance:,} 🪙\n\n"
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
    if item_type == "wheel_tickets_weekly":
        ok, result_text = db.buy_weekly_wheel_tickets(user_id)
        if not ok:
            await callback.answer(result_text, show_alert=True)
            return
        db.log_action(user_id, "purchase", "Куплено: wheel_tickets_weekly", amount=-price)
        await callback.message.edit_text(
            f"✅ {result_text}\n\n🎟️ Билетов сейчас: {db.get_wheel_tickets(user_id)}\n💰 Баланс: {db.get_balance(user_id)} LDC",
            reply_markup=back_to_catalog_keyboard(),
        )
        await callback.answer()
        return
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
        db.log_action(user_id, "purchase", f"Куплено: {item_type}", amount=price)

        await callback.message.edit_text(
            f"✅ Покупка успешна!\nСписано: {price:,} 🪙\nБаланс: {db.get_balance(user_id)} 🪙",
            reply_markup=back_to_catalog_keyboard()
        )

        # Уведомление всем админам/владельцу/тех.админам
        await notify_admins_purchase(user_id, item_type, price)
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
        await cursed_egg_open_confirm(callback)
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
        f"Доход: {result['income']} 🪙 раз в 4 часа\n"
        f"Статус: {'✅ активирован' if result.get('is_active') else '💤 сохранён в коллекции — нет свободного слота'}\n"
        f"{'🎟️ Бонус: +1 билет колеса!' if result.get('ticket_bonus') else ''}\n"
        f"💰 Баланс: {db.get_balance(user_id)} 🪙",
        reply_markup=back_to_catalog_keyboard()
    )
    db.log_action(user_id, "egg", f"Открыто яйцо {egg_type}: {result['name']}", amount=-egg['price'])
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
        f"Доход: {result['income']} 🪙 раз в 4 часа\n"
        f"Статус: {'✅ активирован' if result.get('is_active') else '💤 сохранён в коллекции — нет свободного слота'}\n\n"
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
        "Сегодня в магазине Lavender Dream Coin появилось то, чего там быть не должно...\n\n"
        "🥚 Проклятое яйцо\n"
        "Никто не знает, что находится внутри.\n"
        f"🪙 Цена: 10,000 Lavender Dream Coin\n"
        f"📦 Осталось: {stock['stock']} яиц..\n\n"
        "⚠️ Когда они исчезнут из магазина, вернуть их будет невозможно.\n"
        "Если ты достаточно смелый — оно уже ждёт тебя."
    )
    # Всем пользователям в ЛС
    for u in db.get_all_users():
        try:
            await bot.send_message(u["user_id"], text)
        except Exception:
            logger.exception("Не удалось выполнить необязательную операцию")
    # В чат флуда
    try:
        await bot.send_message(config.FLUD_CHAT_ID, text)
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")


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
            logger.exception("Не удалось выполнить необязательную операцию")
    try:
        await bot.send_message(config.FLUD_CHAT_ID, text)
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")


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
    care = db.get_pet_care(pet_id, callback.from_user.id) or {"energy": 100, "mood": 100, "health": 100, "level": 1, "experience": 0}
    await callback.message.edit_text(
        f"🐾 {pet['name']}\n"
        f"Редкость: {pet['rarity']}\n"
        f"Уровень: {care['level']} | Опыт: {care['experience']}\n"
        f"⚡ Энергия: {care['energy']}/100\n"
        f"😊 Настроение: {care['mood']}/100\n"
        f"❤️ Здоровье: {care['health']}/100\n"
        f"Доход: {pet['income']} 🪙 раз в 4 часа\n"
        f"Статус: {'✅ активен' if pet['is_active'] else '💤 неактивен'}",
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
        await callback.answer("❌ Все слоты заняты. Купи дополнительный слот или освободи один.", show_alert=True)
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
    care = db.get_pet_care(pet_id, callback.from_user.id) or {"energy": 100, "mood": 100, "health": 100, "level": 1, "experience": 0}
    await callback.message.edit_text(
        f"🐾 {pet['name']}\n"
        f"Редкость: {pet['rarity']}\n"
        f"Уровень: {care['level']} | Опыт: {care['experience']}\n"
        f"⚡ Энергия: {care['energy']}/100\n"
        f"😊 Настроение: {care['mood']}/100\n"
        f"❤️ Здоровье: {care['health']}/100\n"
        f"Доход: {pet['income']} 🪙 раз в 4 часа\n"
        f"Статус: {'✅ активен' if pet['is_active'] else '💤 неактивен'}",
        reply_markup=pet_actions_menu(pet_id, pet["is_active"])
    )


@dp.callback_query(F.data.startswith("pet_care_"))
async def pet_care_handler(callback: CallbackQuery):
    parts = callback.data.split("_")
    if len(parts) != 4:
        await callback.answer("Некорректное действие.", show_alert=True)
        return
    action, pet_id_raw = parts[2], parts[3]
    try:
        pet_id = int(pet_id_raw)
    except ValueError:
        await callback.answer("Некорректный питомец.", show_alert=True)
        return
    ok, message_text = db.care_for_pet(callback.from_user.id, pet_id, action)
    if not ok:
        await callback.answer(message_text, show_alert=True)
        return
    await callback.answer(message_text, show_alert=False)
    await pet_info_by_id(callback, pet_id)


@dp.callback_query(F.data.startswith("pet_sell_"))
async def pet_sell_handler(callback: CallbackQuery):
    try:
        pet_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        await callback.answer("Некорректный питомец.", show_alert=True)
        return
    ok, message_text = db.sell_pet_to_system(callback.from_user.id, pet_id)
    if not ok:
        await callback.answer(message_text, show_alert=True)
        return
    await callback.message.edit_text(message_text, reply_markup=pets_menu())
    await callback.answer("Продано!")


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
    cur.execute(
        "SELECT user_id, username FROM users WHERE LOWER(REPLACE(TRIM(username), '@', '')) = LOWER(?) LIMIT 1",
        (target,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        await message.answer("❌ Пользователь не найден")
        return
    target_id = row[0]
    sender_id = message.from_user.id
    pet_id = (await state.get_data()).get("pet_id")
    slot_ok, gift_count = db.reserve_daily_gift_slot(sender_id, daily_limit=5)
    if not slot_ok:
        await message.answer("🚫 Лимит подарков исчерпан: максимум 5 успешных отправок в сутки (UTC), общий для бота и веба.")
        await state.clear()
        return
    if db.transfer_pet(pet_id, sender_id, target_id):
        await message.answer(f"✅ Питомец подарен @{target}! Подарков сегодня: {gift_count}/5 (UTC).")
        try:
            await bot.send_message(target_id, f"🎁 Вам подарили питомца!")
        except Exception:
            logger.exception("Не удалось выполнить необязательную операцию")
    else:
        db.release_daily_gift_slot(sender_id)
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


# ==================== ТЕКСТОВЫЕ ТРИГГЕРЫ: ПИТОМЦЫ И КАТАЛОГ ====================

@dp.message(F.text.lower().strip().in_({"питомцы", "питомец"}))
async def pets_word_trigger(message: Message):
    sent = await message.answer(
        f"🐾 Питомцы\nАктивные слоты: {db.count_active_pets(message.from_user.id)}/{db.get_pet_slot_limit(message.from_user.id)}\nВыбери действие:",
        reply_markup=pets_menu(),
    )
    message_owners[sent.message_id] = message.from_user.id


@dp.message(F.text.lower().strip() == "каталог")
async def catalog_word_trigger(message: Message):
    sent = await message.answer("📦 Каталог Lavender Dream\nВыбери товар:", reply_markup=catalog_menu())
    message_owners[sent.message_id] = message.from_user.id


@dp.callback_query(F.data == "pet_slot_buy")
async def pet_slot_buy_handler(callback: CallbackQuery):
    slots = db.get_pet_slot_limit(callback.from_user.id)
    if slots >= 15:
        await callback.answer("Уже куплены все 15 слотов.", show_alert=True)
        return
    price = 5000 * (slots - 2)
    ok, result_text = db.buy_pet_slot(callback.from_user.id)
    if not ok:
        await callback.answer(result_text, show_alert=True)
        return
    await callback.message.edit_text(
        f"🪹 {result_text}\n\n"
        f"Активные питомцы: {db.count_active_pets(callback.from_user.id)}/{db.get_pet_slot_limit(callback.from_user.id)}",
        reply_markup=pets_menu(),
    )
    await callback.answer("Слот куплен!")


# ==================== ДОПОЛНИТЕЛЬНЫЕ СЛОВЕСНЫЕ ТРИГГЕРЫ ====================

@dp.message(F.text.lower().strip().in_({"пэй", "перевести", "перевод"}))
async def transfer_word_trigger(message: Message, state: FSMContext):
    sent = await message.answer(
        "💸 Перевод LDC\nВведите username получателя без @:",
        reply_markup=cancel_button(),
    )
    message_owners[sent.message_id] = message.from_user.id
    await state.set_state(TransferStates.waiting_recipient)


@dp.message(Command("market"))
@dp.message(F.text.lower().strip().in_({"рынок ресурсов", "системный магазин", "магазин ресурсов"}))
async def system_market_word_trigger(message: Message):
    try:
        data = await asyncio.to_thread(world_events.get_system_market, message.from_user.id)
        lines = ["🏪 Системный магазин Lavender Dream", "", "🛒 Припасы NPC:"]
        builder = InlineKeyboardBuilder()
        for offer in data.get("offers", []):
            contents = ", ".join(f"{item['name']} ×{item['quantity']}" for item in offer["items"])
            lines.append(f"• {offer['name']} — {int(offer['price']):,} LDC ({contents})")
            builder.row(InlineKeyboardButton(
                text=f"Купить: {offer['name'][:24]} · {int(offer['price']):,} LDC",
                callback_data=f"market_buy_{offer['key']}",
            ))
        lines.extend(["", "📦 Твои ресурсы для продажи:"])
        inventory = data.get("inventory", [])
        for item in inventory:
            lines.append(f"• {item['name']} ×{item['quantity']} — {int(item['sell_price']):,} LDC/шт.")
            builder.row(InlineKeyboardButton(
                text=f"Продать 1: {item['name'][:24]}",
                callback_data=f"market_sell_{item['key']}",
            ))
            if int(item["quantity"]) >= 5:
                builder.row(InlineKeyboardButton(
                    text=f"Продать 5: {item['name'][:24]}",
                    callback_data=f"market_sell5_{item['key']}",
                ))
        if not inventory:
            lines.append("Пока нет ресурсов для продажи.")
        await message.answer("\\n".join(lines), reply_markup=builder.as_markup() if data.get("offers") or inventory else None)
    except Exception:
        logger.exception("Не удалось открыть системный магазин")
        await message.answer("Не удалось загрузить системный магазин. Попробуй позже.")


@dp.callback_query(F.data.startswith("market_buy_"))
async def system_market_buy_callback(callback: CallbackQuery):
    offer_key = callback.data[len("market_buy_"):]
    try:
        await callback.answer()
    except Exception:
        logger.debug("Callback системного магазина уже истёк", exc_info=True)
    try:
        result = await asyncio.to_thread(
            world_events.buy_system_market_offer, callback.from_user.id, offer_key
        )
        if callback.message:
            await callback.message.answer(
                f"🏪 Куплено: {result['offer']}. Списано {int(result['price']):,} LDC."
            )
    except ValueError as exc:
        if callback.message:
            await callback.message.answer(f"ℹ️ {exc}")
    except Exception:
        logger.exception("Не удалось купить предложение системного магазина")
        if callback.message:
            await callback.message.answer("Не удалось совершить покупку. Попробуй позже.")

@dp.callback_query(F.data.startswith("market_sell5_"))
async def system_market_sell5_callback(callback: CallbackQuery):
    item_key = callback.data[len("market_sell5_"):]
    try:
        await callback.answer()
    except Exception:
        logger.debug("Callback продажи ресурсов уже истёк", exc_info=True)
    try:
        result = await asyncio.to_thread(
            world_events.sell_system_market_item, callback.from_user.id, item_key, 5
        )
        if callback.message:
            await callback.message.answer(
                f"📦 Продано {result['amount']} × {result['item']}. Получено {int(result['reward_ldc']):,} LDC."
            )
    except ValueError as exc:
        if callback.message:
            await callback.message.answer(f"ℹ️ {exc}")
    except Exception:
        logger.exception("Не удалось продать ресурсы системному магазину")
        if callback.message:
            await callback.message.answer("Не удалось продать предметы. Попробуй позже.")

@dp.callback_query(F.data.startswith("market_sell_"))
async def system_market_sell_callback(callback: CallbackQuery):
    item_key = callback.data[len("market_sell_"):]
    try:
        await callback.answer()
    except Exception:
        logger.debug("Callback продажи ресурса уже истёк", exc_info=True)
    try:
        result = await asyncio.to_thread(
            world_events.sell_system_market_item, callback.from_user.id, item_key, 1
        )
        if callback.message:
            await callback.message.answer(
                f"📦 Продано: {result['item']}. Получено {int(result['reward_ldc']):,} LDC."
            )
    except ValueError as exc:
        if callback.message:
            await callback.message.answer(f"ℹ️ {exc}")
    except Exception:
        logger.exception("Не удалось продать ресурс системному магазину")
        if callback.message:
            await callback.message.answer("Не удалось продать предмет. Попробуй позже.")

@dp.message(Command("expeditions"))
@dp.message(F.text.lower().strip().in_({"экспедиции питомцев", "экспедиция питомца", "экспедиции"}))
async def pet_expeditions_word_trigger(message: Message):
    try:
        data = await asyncio.to_thread(world_events.list_pet_expeditions, message.from_user.id)
        lines = ["🧭 Экспедиции питомцев", ""]
        builder = InlineKeyboardBuilder()
        active = [e for e in data.get("expeditions", []) if e.get("status") == "active"]
        if active:
            for expedition in active:
                lines.append(
                    f"🐾 {expedition.get('pet_name', 'Питомец')} — {expedition.get('destination', '')}"
                )
                if expedition.get("can_claim"):
                    builder.row(InlineKeyboardButton(
                        text=f"🎁 Забрать награду #{expedition['id']}",
                        callback_data=f"petexp_claim_{expedition['id']}",
                    ))
                else:
                    lines.append(f"⏳ Осталось: {max(0, int(expedition.get('remaining_seconds') or 0)) // 60} мин.")
            lines.extend(["", "Сначала забери награду, затем можно отправить питомца снова."])
        else:
            pets = data.get("pets", [])
            if not pets:
                lines.append("У тебя пока нет доступных питомцев для экспедиции.")
            else:
                lines.append("Выбери питомца и маршрут:")
                for pet in pets[:4]:
                    for destination in data.get("destinations", []):
                        builder.row(InlineKeyboardButton(
                            text=f"{pet.get('pet_emoji', '🐾')} {pet.get('pet_name', 'Питомец')[:12]} · {destination['name'][:18]}",
                            callback_data=f"petexp_start_{pet['id']}_{destination['key']}",
                        ))
                lines.extend(["", "🌲 Лес — 30 мин. · 💎 Пещера — 60 мин. · 🏝️ Остров — 120 мин."])
        has_buttons = any(e.get("can_claim") for e in active) if active else bool(data.get("pets"))
        await message.answer("\\n".join(lines), reply_markup=builder.as_markup() if has_buttons else None)
    except Exception:
        logger.exception("Не удалось открыть экспедиции питомцев")
        await message.answer("Не удалось загрузить экспедиции. Попробуй позже.")


@dp.callback_query(F.data.startswith("petexp_start_"))
async def pet_expedition_start_callback(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        logger.debug("Callback запуска экспедиции уже истёк", exc_info=True)
    try:
        _, _, pet_id, destination = callback.data.split("_", 3)
        result = await asyncio.to_thread(
            world_events.start_pet_expedition, callback.from_user.id, pet_id, destination
        )
        if callback.message:
            await callback.message.answer(
                f"🧭 {result['pet_name']} отправился по маршруту {result['destination']}. "
                f"Вернётся примерно через {result['duration_minutes']} минут."
            )
    except ValueError as exc:
        if callback.message:
            await callback.message.answer(f"ℹ️ {exc}")
    except Exception:
        logger.exception("Не удалось начать экспедицию питомца")
        if callback.message:
            await callback.message.answer("Не удалось начать экспедицию. Попробуй позже.")

@dp.callback_query(F.data.startswith("petexp_claim_"))
async def pet_expedition_claim_callback(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        logger.debug("Callback награды экспедиции уже истёк", exc_info=True)
    try:
        expedition_id = int(callback.data[len("petexp_claim_"):])
        result = await asyncio.to_thread(
            world_events.claim_pet_expedition, callback.from_user.id, expedition_id
        )
        if callback.message:
            item = f" и предмет «{result['reward_item']}»" if result.get("reward_item") else ""
            await callback.message.answer(
                f"🎁 Экспедиция завершена: +{int(result['reward_ldc']):,} LDC{item}\n{result.get('outcome', '')}"
            )
    except ValueError as exc:
        if callback.message:
            await callback.message.answer(f"ℹ️ {exc}")
    except Exception:
        logger.exception("Не удалось забрать награду экспедиции")
        if callback.message:
            await callback.message.answer("Не удалось забрать награду. Попробуй позже.")

@dp.message(Command("achievements"))
@dp.message(F.text.lower().strip().in_({"достижения", "ачивки"}))
async def achievements_word_trigger(message: Message):
    try:
        data = await asyncio.to_thread(world_events.get_achievements, message.from_user.id)
        lines = ["🏆 Достижения Lavender Dream", ""]
        builder = InlineKeyboardBuilder()
        buttons = 0
        for item in data.get("achievements", []):
            done = item.get("completed")
            claimed = item.get("claimed")
            lines.append(f"{'🎁' if done and not claimed else '✅' if claimed else '▫️'} {item['title']} — {int(item.get('progress') or 0)}/{int(item['target'])}")
            if done and not claimed and buttons < 6:
                builder.row(InlineKeyboardButton(
                    text=f"🎁 {item['title'][:28]}",
                    callback_data=f"achievement_claim_{item['key']}",
                ))
                buttons += 1
        await message.answer("\\n".join(lines), reply_markup=builder.as_markup() if buttons else None)
    except Exception:
        logger.exception("Не удалось открыть достижения")
        await message.answer("Не удалось загрузить достижения. Попробуй позже.")


@dp.callback_query(F.data.startswith("achievement_claim_"))
async def achievement_claim_callback(callback: CallbackQuery):
    key = callback.data[len("achievement_claim_"):]
    try:
        await callback.answer()
    except Exception:
        logger.debug("Callback награды достижения уже истёк", exc_info=True)
    try:
        result = await asyncio.to_thread(world_events.claim_achievement, callback.from_user.id, key)
        if callback.message:
            await callback.message.answer(
                f"🏆 Достижение получено: +{int(result['reward_ldc']):,} LDC. "
                f"Открыт титул: {result.get('title') or '—'}."
            )
    except ValueError as exc:
        if callback.message:
            await callback.message.answer(f"ℹ️ {exc}")
    except Exception:
        logger.exception("Не удалось получить награду достижения")
        if callback.message:
            await callback.message.answer("Не удалось получить награду. Попробуй позже.")

@dp.message(Command("collection"))
@dp.message(F.text.lower().strip().in_({"коллекция", "моя коллекция", "бестиарий"}))
async def collection_word_trigger(message: Message):
    try:
        data = await asyncio.to_thread(world_events.get_collection, message.from_user.id)
        summary = data.get("summary", {})
        lines = [
            "📚 Коллекция Lavender Dream",
            f"🐾 Питомцев: {summary.get('pets', 0)}",
            f"🎒 Коллекционных предметов: {summary.get('items', 0)}",
            f"🏷️ Титулов: {summary.get('titles', 0)}",
        ]
        items = data.get("items", [])
        if items:
            lines.extend(["", "Предметы:"])
            lines.extend(f"• {item['item_name']}" for item in items[:10])
        titles = data.get("titles", [])
        builder = InlineKeyboardBuilder()
        title_buttons = 0
        if titles:
            lines.extend(["", "Титулы:"])
            for title in titles[:8]:
                lines.append(f"{'⭐ ' if title.get('is_active') else '• '}{title['title_name']}")
                if not title.get("is_active"):
                    builder.row(InlineKeyboardButton(
                        text=f"Выбрать: {title['title_name'][:24]}",
                        callback_data=f"title_equip_{title['title_key']}",
                    ))
                    title_buttons += 1
        await message.answer("\\n".join(lines), reply_markup=builder.as_markup() if title_buttons else None)
    except Exception:
        logger.exception("Не удалось открыть коллекцию")
        await message.answer("Не удалось загрузить коллекцию. Попробуй позже.")


@dp.callback_query(F.data.startswith("title_equip_"))
async def title_equip_callback(callback: CallbackQuery):
    key = callback.data[len("title_equip_"):]
    try:
        await callback.answer()
    except Exception:
        logger.debug("Callback выбора титула уже истёк", exc_info=True)
    try:
        result = await asyncio.to_thread(world_events.set_active_title, callback.from_user.id, key)
        if callback.message:
            await callback.message.answer(f"🏷️ Активный титул: {result['title_name']}")
    except ValueError as exc:
        if callback.message:
            await callback.message.answer(f"ℹ️ {exc}")
    except Exception:
        logger.exception("Не удалось выбрать титул")
        if callback.message:
            await callback.message.answer("Не удалось выбрать титул. Попробуй позже.")

@dp.message(Command("season"))
@dp.message(F.text.lower().strip().in_({"сезон", "сезоны"}))
async def season_word_trigger(message: Message):
    try:
        status = await asyncio.to_thread(world_events.get_season_status, message.from_user.id)
        season = status.get("season") or {}
        progress = status.get("progress") or {}
        ends = season.get("ends_at")
        if isinstance(ends, str):
            ends = datetime.fromisoformat(ends.replace("Z", "+00:00")).replace(tzinfo=None)
        remaining = max(0, int((ends - datetime.utcnow()).total_seconds())) if ends else 0
        lines = [
            f"🌸 {season.get('title', 'Сезон Lavender Dream')}",
            f"⏳ До конца: {remaining // 86400} дн. {(remaining % 86400) // 3600} ч.",
            f"🏅 Уровень: {int(progress.get('level') or 0)}",
            f"✨ Опыт: {int(progress.get('xp') or 0):,} XP",
            "",
            "📋 Сезонные задания:",
        ]
        builder = InlineKeyboardBuilder()
        reward_button_count = 0
        for task in status.get("tasks", []):
            mark = "✅" if task.get("claimed") else "🎁" if task.get("completed") else "▫️"
            lines.append(
                f"{mark} {task['title']} — {int(task.get('progress') or 0)}/{int(task.get('target') or 0)}"
            )
            if task.get("completed") and not task.get("claimed") and reward_button_count < 5:
                builder.row(InlineKeyboardButton(
                    text=f"🎁 {task['title'][:28]}",
                    callback_data=f"season_claim_{task['task_key']}",
                ))
                reward_button_count += 1
        for reward in status.get("level_rewards", []):
            if int(progress.get("level") or 0) >= int(reward.get("level") or 0) and not reward.get("claimed") and reward_button_count < 8:
                builder.row(InlineKeyboardButton(
                    text=f"🎁 Уровень {reward['level']}: {reward['description'][:22]}",
                    callback_data=f"season_level_claim_{reward['level']}",
                ))
                reward_button_count += 1
        lines.extend(["", "Награды за задания дают сезонный опыт и LDC."])
        await message.answer("\\n".join(lines), reply_markup=builder.as_markup() if reward_button_count else None)
    except Exception:
        logger.exception("Не удалось открыть сезон")
        await message.answer("Не удалось загрузить сезон. Попробуй позже.")


@dp.callback_query(F.data.startswith("season_level_claim_"))
async def season_level_claim_callback(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        logger.debug("Callback сезонной награды уровня уже истёк", exc_info=True)
    try:
        level = int(callback.data[len("season_level_claim_"):])
        result = await asyncio.to_thread(world_events.claim_season_level, callback.from_user.id, level)
        if callback.message:
            reward_text = f"+{int(result.get('amount') or 0):,} LDC" if result.get("reward_type") == "ldc" else result.get("reward", "Награда получена")
            await callback.message.answer(f"🌸 Награда уровня {level}: {reward_text}")
    except (ValueError, TypeError) as exc:
        if callback.message:
            await callback.message.answer(f"ℹ️ {exc}")
    except Exception:
        logger.exception("Не удалось получить награду уровня сезона")
        if callback.message:
            await callback.message.answer("Не удалось получить награду. Попробуй позже.")

@dp.callback_query(F.data.startswith("season_claim_"))
async def season_claim_callback(callback: CallbackQuery):
    task_key = callback.data[len("season_claim_"):]
    try:
        await callback.answer()
    except Exception:
        logger.debug("Callback сезонного задания уже истёк", exc_info=True)
    try:
        result = await asyncio.to_thread(world_events.claim_season_task, callback.from_user.id, task_key)
        if callback.message:
            await callback.message.answer(
                f"🌸 Сезонная награда: +{int(result['reward_ldc']):,} LDC и +{int(result['reward_xp'])} XP."
            )
    except ValueError as exc:
        if callback.message:
            await callback.message.answer(f"ℹ️ {exc}")
    except Exception:
        logger.exception("Не удалось получить сезонную награду")
        if callback.message:
            await callback.message.answer("Не удалось получить награду. Попробуй позже.")

@dp.message(F.text.lower().strip().in_({"задания", "задание"}))
async def tasks_word_trigger(message: Message):
    db.assign_daily_tasks(message.from_user.id)
    tasks = db.get_user_tasks(message.from_user.id)
    if not tasks:
        sent = await message.answer("📋 Заданий пока нет. Загляни позже!", reply_markup=tasks_menu())
        message_owners[sent.message_id] = message.from_user.id
        return
    from database import DAILY_TASKS
    lines = ["📋 Ежедневные задания:", ""]
    for task in tasks:
        info = DAILY_TASKS.get(task["task_type"], {})
        status = "✅ выполнено" if task["is_done"] else f"{task['progress']}/{task['target']}"
        lines.append(f"{info.get('name', task['task_type'])} — {status} (+{task['reward']} 🪙)")
        if info.get("desc"):
            lines.append(f"   {info['desc']}")
        lines.append("")
    sent = await message.answer("\n".join(lines), reply_markup=tasks_menu())
    message_owners[sent.message_id] = message.from_user.id


@dp.message(F.text.lower().strip().in_({"топ", "банк топ"}))
async def balance_top_word_trigger(message: Message):
    top_users = db.get_top_users(10)
    if not top_users:
        await message.answer("📊 Топ пока пуст.")
        return
    medals = ["🥇", "🥈", "🥉"]
    text = "🏦 Топ игроков по балансу LDC\n\n"
    for i, (uid, username, balance) in enumerate(top_users, 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        text += f"{medal} {db.get_display_name(uid)} — {balance:,} 🪙\n"
    await message.answer(text)


@dp.message(F.text.func(lambda text: isinstance(text, str) and text.strip().casefold() == "курс"))
async def lcc_rate_word_trigger(message: Message):
    rate = db.get_lcc_rate()
    await message.answer(f"💎 Текущий курс Lavender Crypto Coin (LCC): 1 LCC ≈ {rate:,.2f} LDC.")


@dp.message(F.text.func(lambda text: isinstance(text, str) and text.strip().casefold() == "колесо"))
async def wheel_word_trigger(message: Message):
    """Word trigger for the fortune wheel."""
    sent = await message.answer(
        "🎡 Колесо фортуны\n\nСтоимость вращения: 10 билетов колеса.\nКулдаун: раз в 3 часа.\nБилеты можно получить из сундуков, заданий, яиц питомцев и промокодов.\n\nИспытай удачу!",
        reply_markup=wheel_menu(),
    )
    message_owners[sent.message_id] = message.from_user.id


@dp.message(F.text.lower().strip().in_({"игры", "игра"}))
async def games_word_trigger(message: Message):
    sent = await message.answer("🎮 Выбери игру:", reply_markup=games_menu())
    message_owners[sent.message_id] = message.from_user.id


@dp.message(F.text.lower().regexp(r"^(?:монетка|казино|слот|слоты|кубик|кости|дротик|дартс|угадай число|число)(?:\s+.*)?$"))
async def game_word_trigger(message: Message):
    raw = message.text.strip().lower()
    parts = raw.split()
    word = parts[0]
    aliases = {
        "монетка": ("coin", "🪙 Монетка"), "казино": ("slot", "🎰 Казино / слот"),
        "слот": ("slot", "🎰 Слот"), "слоты": ("slot", "🎰 Слоты"),
        "кубик": ("dice", "🎲 Кубик"), "кости": ("dice", "🎲 Кубик"),
        "дротик": ("dart", "🎯 Дартс"), "дартс": ("dart", "🎯 Дартс"),
        "угадай число": ("number", "🃏 Угадай число"), "число": ("number", "🃏 Угадай число"),
    }
    # Support multi-word aliases before parsing arguments.
    if raw.startswith("угадай число"):
        word, parts = "угадай число", ["угадай число"] + raw[len("угадай число"):].strip().split()
    elif raw.startswith("слоты"):
        word = "слоты"
    game_type, title = aliases.get(word, ("slot", "🎰 Слот"))
    choice = None
    bet = None
    args = parts[1:] if word != "угадай число" else parts[1:]

    def parse_bet(value):
        match = re.fullmatch(r"([0-9]+(?:[.,][0-9]+)?)(к{1,3})?", value)
        if not match:
            return None
        number = float(match.group(1).replace(",", "."))
        multiplier = {"": 1, "к": 1_000, "кк": 1_000_000, "ккк": 1_000_000_000}[match.group(2) or ""]
        scaled = number * multiplier
        return int(scaled) if scaled.is_integer() and scaled <= 9_223_372_036_854_775_807 else None

    if game_type == "coin" and args and args[0] in {"о", "орел", "орёл", "р", "решка"}:
        choice = "Орел" if args.pop(0) in {"о", "орел", "орёл"} else "Решка"
    elif game_type == "dice" and args and args[0].isdigit() and 1 <= int(args[0]) <= 6:
        choice = int(args.pop(0))
    elif game_type == "number" and args and args[0].isdigit() and 1 <= int(args[0]) <= 10:
        choice = int(args.pop(0))

    if args:
        bet = parse_bet(args[-1])
    if bet is None:
        db.log_action(message.from_user.id, "security", f"Некорректная ставка/аргументы игры: game={game_type}; text={message.text[:180]!r}")
        if game_type in {"coin", "dice", "number"} and choice is None:
            hint = "Формат: монетка о 500 (или монетка р 1к)." if game_type == "coin" else ("Формат: кубик 4 500." if game_type == "dice" else "Формат: число 7 500.")
            await message.answer(f"{title}\n{hint}\nМинимальная ставка — 500 LDC.")
        else:
            await message.answer(f"{title}\nИспользуй «{word} [выбор] [ставка]» для игры. Минимальная ставка — 500 LDC.")
        return
    if bet < 500:
        db.log_action(message.from_user.id, "security", f"Ставка ниже минимума: game={game_type}; bet={bet}")
        await message.answer("❌ Минимальная ставка — 500 LDC.")
        return

    user_id = message.from_user.id
    balance = db.get_balance(user_id)
    if balance < bet:
        db.log_action(user_id, "security", f"Ставка превышает баланс: game={game_type}; bet={bet}; balance={balance}")
        await message.answer(f"❌ Недостаточно LDC. Твой баланс: {balance:,} LDC.")
        return
    # Validate all required arguments before consuming a play attempt.
    if game_type == "dice" and choice is None:
        await message.answer("🎲 Укажи число от 1 до 6: «кубик 4 500».")
        return
    if game_type == "coin" and choice is None:
        await message.answer("🪙 Укажи сторону: «монетка о 500» или «монетка р 500».")
        return
    if game_type == "number" and choice is None:
        await message.answer("🃏 Укажи число от 1 до 10: «число 7 500».")
        return

    allowed, remaining = db.register_game_play(user_id, game_type)
    if not allowed:
        db.log_action(user_id, "security", f"Попытка игры во время кулдауна: game={game_type}; remaining={remaining}s")
        await message.answer(f"⏳ Эта игра на перерыве ещё {format_time(remaining)}")
        return

    if game_type == "slot":
        net_change, result_text = play_slot(bet)
    elif game_type == "dice":
        net_change, result_text = play_dice(bet, choice)
    elif game_type == "coin":
        net_change, result_text = play_coin(bet, choice)
    elif game_type == "dart":
        net_change, result_text = play_dart(bet)
    else:
        net_change, result_text = play_number(bet, choice)

    if not db.update_balance(user_id, net_change):
        await message.answer("❌ Не удалось обновить баланс. Попробуй ещё раз.")
        return
    db.update_game_stats(user_id, net_change > 0, abs(net_change))
    try:
        db.assign_daily_tasks(user_id)
        db.update_task_progress(user_id, "games", 1)
        try:
            world_events.record_season_activity(user_id, "game", 1)
        except Exception:
            logger.exception("Не удалось обновить сезонный прогресс игры")
        db.log_action(user_id, "game", f"Игра {game_type}; ставка={bet}; изменение баланса={net_change}", amount=net_change)
    except Exception:
        logger.exception("Не удалось обновить статистику или задание игры")
    await message.answer(
        f"{title}\n💰 Ставка: {bet:,} LDC\n{result_text}\n\nБаланс: {db.get_balance(user_id):,} LDC",
        reply_markup=game_result_keyboard()
    )


# ==================== ПРОФЕССИИ ====================

@dp.callback_query(F.data == "professions_menu")
async def professions_menu_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "💼 Профессии\n\nВыберите действие:",
        reply_markup=professions_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "my_profession")
async def my_profession(callback: CallbackQuery, answer_callback: bool = True):
    prof = db.get_profession(callback.from_user.id)
    if not prof or not prof["profession"]:
        await callback.message.edit_text(
            "💼 У вас нет профессии.\nУстроиться стоит 1000 🪙.",
            reply_markup=professions_menu()
        )
        if answer_callback:
            await callback.answer()
        return

    from database import PROFESSIONS
    info = PROFESSIONS[prof["profession"]]
    salary = db.get_profession_salary(prof["profession"], prof["level"])
    level = prof["level"]
    days = prof["days_worked"]
    if level >= 5:
        bar = "▰▰▰▰▰▰▰ 100% (максимум)"
    else:
        filled = max(0, min(7, int((days / 7) * 7)))
        bar = "▰" * filled + "▱" * (7 - filled)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💼 Работать", callback_data="work_now"))
    builder.row(InlineKeyboardButton(text="❌ Уволиться", callback_data="fire_profession"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="professions_menu"))

    await callback.message.edit_text(
        f"💼 {info['name']}\n\n"
        f"Уровень: {level}/5\n"
        f"Зарплата: {salary} 🪙\n"
        f"Кулдаун: {info['cooldown_hours']} ч\n\n"
        f"📊 Прогресс до {level + 1 if level < 5 else level} уровня:\n"
        f"{bar} {days}/7 дней",
        reply_markup=builder.as_markup()
    )
    if answer_callback:
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
        await my_profession(callback, answer_callback=False)


@dp.callback_query(F.data == "work_now")
async def work_now(callback: CallbackQuery):
    user_id = callback.from_user.id
    balance_before = db.get_balance(user_id)
    ok, msg = db.work_profession(user_id)
    if not ok:
        await callback.answer(msg, show_alert=True)
        return

    # Логируем фактически начисленную сумму, включая бонусы друзей.
    try:
        earned = db.get_balance(user_id) - balance_before
        db.log_action(user_id, "work", f"Зарплата ({msg})", amount=earned)
    except Exception:
        logger.exception("Не удалось записать лог работы user_id=%s", user_id)

    await callback.answer(msg)
    await my_profession(callback, answer_callback=False)

@dp.callback_query(F.data == "fire_profession")
async def fire_profession(callback: CallbackQuery):
    conn = sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM professions WHERE user_id = ?", (callback.from_user.id,))
    conn.commit()
    conn.close()
    await callback.answer("✅ Вы уволены", show_alert=True)
    await callback.message.edit_text(
        "Вы уволились. Можешь устроиться на любую доступную профессию бесплатно.",
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
        desc = info.get("desc", "")
        text += f"{info['name']} — {status} (+{t['reward']} 🪙)\n"
        if desc:
            text += f"   _{desc}_\n"
        text += "\n"
    await callback.message.edit_text(text, reply_markup=tasks_menu(), parse_mode="Markdown")
    await callback.answer()


# ==================== КОЛЕСО ФОРТУНЫ ====================

@dp.callback_query(F.data == "wheel_menu")
async def wheel_menu_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎡 Колесо фортуны\n\n"
        "Стоимость вращения: 10 🎟️\n"
        f"Билеты у тебя: {db.get_wheel_tickets(callback.from_user.id)} 🎟️\nКулдаун: раз в 3 часа\n\n"
        "Билеты можно получить за некоторые задания, из сундуков, яиц и промокодов. Испытай удачу!",
        reply_markup=wheel_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "wheel_spin")
async def wheel_spin(callback: CallbackQuery):
    user_id = callback.from_user.id
    if db.get_wheel_tickets(user_id) < 10:
        await callback.answer("❌ Нужно 10 билетов колеса 🎟️", show_alert=True)
        return

    can, remaining = db.can_spin_wheel(user_id)
    if not can:
        await callback.answer(
            f"⏳ Колесо будет доступно через {format_time(remaining)}",
            show_alert=True
        )
        return

    if not db.change_wheel_tickets(user_id, -10):
        await callback.answer("❌ Нужно 10 билетов колеса 🎟️", show_alert=True)
        return
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
    db.log_action(user_id, "wheel", f"Колесо: {reward_text}", amount=0)
    await callback.answer("🎉")
    
# ==================== ПРОМОКОД ====================

@dp.message(F.text.func(lambda text: isinstance(text, str) and text.strip().casefold() == "промокод"))
async def promo_word(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("🎟️ Промокоды активируются только в личке бота.")
        return
    await message.answer("🎟️ Введите промокод:")
    await state.set_state(PromoStates.waiting_code)


@dp.message(PromoStates.waiting_code)
async def promo_activate(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("🎟️ Промокоды активируются только в личке бота.")
        await state.clear()
        return
    if not message.text or not message.text.strip():
        await message.answer("❌ Отправьте текстовый код промокода.")
        return

    code = message.text.strip().upper()
    if code in {"/CANCEL", "ОТМЕНА"}:
        await state.clear()
        await message.answer("❎ Ввод промокода отменён.")
        return

    try:
        ok, result = db.activate_promo(code, message.from_user.id)
    except Exception:
        logger.exception("Ошибка активации промокода user_id=%s code=%s", message.from_user.id, code)
        await message.answer("❌ Не удалось активировать промокод. Попробуйте позже.")
        await state.clear()
        return

    if ok:
        try:
            db.log_action(message.from_user.id, "promo", f"Промокод {code}", amount=0)
        except Exception:
            logger.exception("Не удалось записать лог промокода user_id=%s", message.from_user.id)
        await message.answer(f"✅ Промокод активирован!\nНаграда: {result}")
    else:
        await message.answer(f"❌ {result}")
    await state.clear()


async def _word_trigger_answer(message: Message, text: str, **kwargs):
    """Reply to a word trigger and identify the user who invoked it."""
    user = message.from_user
    if user and not user.is_bot:
        mention = f"@{user.username}" if user.username else user.full_name
        text = f"{mention}\n{text}"
    return await message.answer(text, **kwargs)


# ==================== ВОРД-ТРИГГЕРЫ ====================

@dp.message(F.text.lower() == "банк")
async def bank_word(message: Message):
    uid = message.from_user.id
    can, remaining = db.can_claim_bonus(uid)
    if can:
        bonus = db.claim_bonus(uid)
        next_cooldown = db.get_user_cooldown_seconds(uid, "bonus", 10800)
        await _word_trigger_answer(message, 
            f"🏦 Вы получили бонус {bonus} 🪙!\n"
            f"💰 Баланс: {db.get_balance(uid)} 🪙\n"
            f"Следующий бонус через {format_time(next_cooldown)}."
        )
    else:
        await _word_trigger_answer(message, f"⏳ Бонус ещё не доступен. Осталось: {format_time(remaining)}.")


@dp.message(F.text.lower() == "баланс")
async def balance_word(message: Message):
    uid = message.from_user.id
    await _word_trigger_answer(message, 
        f"💰 Ваш баланс: {db.get_balance(uid):,} Lavender Dream Coin\n"
        f"🏆 Место: #{db.get_user_rank(uid)}"
    )


@dp.message(F.text.lower() == "чистка окончена")
async def clean_finished(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        await _word_trigger_answer(message, "❌ Нет прав")
        return
    count = db.clear_all_immunities()
    await _word_trigger_answer(message, f"🧹 Чистка окончена! Аннулировано иммунитетов: {count}")


@dp.message(F.text.lower() == "топ нью")
async def top_new(message: Message):
    users = db.get_today_new_members(exclude_user_id=bot.id)
    if not users:
        await _word_trigger_answer(message, "📊 Топ Нью за сегодня: пусто")
        return
    text = "📊 Топ Нью за сегодня:\n\n"
    for i, (uid, uname, _) in enumerate(users, 1):
        text += f"{i}. @{uname or uid}\n"
    await _word_trigger_answer(message, text)


@dp.message(F.text.func(lambda text: isinstance(text, str) and text.strip().casefold() == "кто нью"))
async def who_new(message: Message):
    users = db.get_new_members(4, exclude_user_id=bot.id)
    if not users:
        await _word_trigger_answer(message, "📊 Кто Нью (4 дня): пусто")
        return
    text = "📊 Кто Нью (4 дня):\n\n"
    for i, (uid, uname, joined) in enumerate(users, 1):
        joined_text = joined.strftime("%Y-%m-%d %H:%M") if hasattr(joined, "strftime") else str(joined or "")[:16]
        text += f"{i}. @{uname or uid} — {joined_text}\n"
    await _word_trigger_answer(message, text)


@dp.message(F.text.lower() == "калл нью")
async def call_new(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        await _word_trigger_answer(message, "❌ Только админы")
        return
    users = db.get_new_members(1, exclude_user_id=bot.id)
    if not users:
        await _word_trigger_answer(message, "📢 Нет новых")
        return
    uid, uname, joined = users[0]
    joined_text = joined.strftime("%Y-%m-%d %H:%M") if hasattr(joined, "strftime") else str(joined)[:16]
    await _word_trigger_answer(message, f"📢 Нью в чате: @{uname or uid} ({joined_text})")

@dp.message(F.text.func(lambda text: isinstance(text, str) and text.strip().casefold() == "работать"))
async def work_word_handler(message: Message):
    """Ворд-триггер 'работать' — получить зарплату по профессии"""
    user_id = message.from_user.id

    if not db.get_user(user_id):
        username = message.from_user.username or str(user_id)
        db.create_user(user_id, username)

    user = db.get_user(user_id)
    if user and user["is_banned"]:
        await _word_trigger_answer(message, "🚫 Вы заблокированы.")
        return

    prof = db.get_profession(user_id)
    if not prof or not prof["profession"]:
        await _word_trigger_answer(message, 
            "💼 У вас нет профессии.\n"
            "Зайдите в раздел «💼 Профессии» в боте и устройтесь на работу."
        )
        return

    balance_before = db.get_balance(user_id)
    ok, msg = db.work_profession(user_id)
    if ok:
        # Прогресс-бар
        new_prof = db.get_profession(user_id)
        level = new_prof["level"]
        days = new_prof["days_worked"]
        if level >= 5:
            bar = "▰▰▰▰▰▰▰ 100% (максимум)"
        else:
            filled = max(0, min(7, int((days / 7) * 7)))
            bar = "▰" * filled + "▱" * (7 - filled) + f" {days}/7 дней"
        # Логирование не должно мешать отправке ответа после начисления зарплаты.
        try:
            earned = db.get_balance(user_id) - balance_before
            db.log_action(user_id, "work", f"Зарплата ({msg})", amount=earned)
        except Exception:
            logger.exception("Не удалось записать лог работы user_id=%s", user_id)
        await _word_trigger_answer(message, 
            f"💼 {msg}\n"
            f"📊 Прогресс: {bar}\n"
            f"💰 Баланс: {db.get_balance(user_id)} 🪙"
        )
    else:
        await _word_trigger_answer(message, f"⏳ {msg}")

@dp.message(F.text.lower().startswith("профиль"))
async def profile_word_handler(message: Message):
    """Свой профиль — с кнопками; профиль по username/ID или reply — без кнопок."""
    own_id = message.from_user.id
    raw_text = message.text.strip()
    target_id = own_id
    target_name = None
    parts = raw_text.split(maxsplit=1)

    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        target_name = message.reply_to_message.from_user.username
    elif len(parts) > 1:
        arg = parts[1].strip().lstrip("@")
        if arg.isdigit():
            target_id = int(arg)
        else:
            conn = sqlite3.connect(db.DB_NAME)
            cur = conn.cursor()
            cur.execute(
                "SELECT user_id, username FROM users "
                "WHERE LOWER(REPLACE(TRIM(username), '@', '')) = LOWER(?) LIMIT 1",
                (arg,),
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                await _word_trigger_answer(message, f"❌ Пользователь @{arg} не найден.")
                return
            target_id, target_name = row[0], row[1]

    user = db.get_user(target_id)
    if not user:
        await _word_trigger_answer(message, "❌ Профиль не найден.")
        return

    db.check_badges(target_id)
    balance = user["balance"]
    rank = db.get_user_rank(target_id)
    warns = db.get_warn_count(target_id)
    pets = db.get_user_pets(target_id)
    prof = db.get_profession(target_id)
    display_name = db.get_display_name(target_id)
    cosmetics = db.get_cosmetics(target_id)
    badges = db.get_badges(target_id)

    lines = [f"👤 Профиль {display_name}"]
    if cosmetics["status"] and cosmetics["status"] in db.STATUSES:
        lines.append(db.STATUSES[cosmetics["status"]]["name"])
    if badges:
        lines.append(" ".join(db.BADGES[b]["emoji"] for b in badges if b in db.BADGES))
    lines.extend(["", f"🪙 Баланс: {balance:,}", f"🏆 Место в топе: #{rank}", f"⚠️ Предупреждения: {warns}"])
    if prof and prof["profession"]:
        from database import PROFESSIONS
        lines.append(f"💼 Профессия: {PROFESSIONS[prof['profession']]['name']} (ур. {prof['level']})")
    if pets:
        lines.append(f"🐾 Питомцев: {len(pets)} (активных: {sum(1 for p in pets if p['is_active'])})")

    markup = None
    if target_id == own_id:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_profile"))
        builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_main"))
        markup = builder.as_markup()
    await _word_trigger_answer(message, "\n".join(lines), reply_markup=markup)


@dp.message(F.text.lower() == "банк пинг")
async def bank_ping_handler(message: Message):
    """Ворд-триггер 'банк пинг' — измеряет пинг до Telegram API"""
    import time
    try:
        start = time.monotonic()
        await bot.get_me()
        ping_ms = int((time.monotonic() - start) * 1000)

        if ping_ms < 200:
            status = "🟢 Отлично"
        elif ping_ms < 500:
            status = "🟡 Нормально"
        elif ping_ms < 1000:
            status = "🟠 Медленно"
        else:
            status = "🔴 Плохо"

        await _word_trigger_answer(message, 
            f"📡 Пинг до Telegram: {ping_ms} мс\n{status}"
        )
    except Exception as e:
        await _word_trigger_answer(message, f"❌ Не удалось измерить пинг: {e}")

@dp.message(F.text.lower() == "остров")
async def island_word(message: Message):
    user_id = message.from_user.id
    state = db.island_get(user_id)

    if not state["has_island"]:
        # Нет острова — просто текст
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text=f"🏝️ Купить остров — {db.ISLAND_PRICE:,} 🪙",
            callback_data="island_buy_island"
        ))
        await _word_trigger_answer(message, 
            "🏝️ У тебя пока нет острова.\n\n"
            "Купи его, чтобы строить домик, причал и корабль. "
            "Домик и корабль будут приносить пассивный доход.",
            reply_markup=builder.as_markup()
        )
        return

    # Есть остров — отправляем картинку
    try:
        path = build_island_image(user_id, state)
        photo = FSInputFile(path)
        caption = (
            f"🏝️ Твой остров\n\n"
            f"{'🏠 Домик: есть' if state['has_house'] else '🏠 Домик: нет'}\n"
            f"{'⚓ Причал: есть' if state['has_pier'] else '⚓ Причал: нет'}\n"
            f"{'🚢 Корабль: есть' if state['has_ship'] else '🚢 Корабль: нет'}\n\n"
            f"💰 Баланс: {db.get_balance(user_id):,} 🪙"
        )
        # Кнопки покупки того, чего нет
        builder = InlineKeyboardBuilder()
        if not state["has_house"]:
            builder.row(InlineKeyboardButton(
                text=f"🏠 Построить домик — {db.HOUSE_PRICE:,}",
                callback_data="island_buy_house"
            ))
        if not state["has_pier"]:
            builder.row(InlineKeyboardButton(
                text=f"⚓ Построить причал — {db.PIER_PRICE:,}",
                callback_data="island_buy_pier"
            ))
        if state["has_pier"] and not state["has_ship"]:
            builder.row(InlineKeyboardButton(
                text=f"🚢 Купить корабль — {db.SHIP_PRICE:,}",
                callback_data="island_buy_ship"
            ))

        if message.from_user and not message.from_user.is_bot:
            mention = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
            caption = f"{mention}\n{caption}"
        await message.answer_photo(photo, caption=caption,
                                   reply_markup=builder.as_markup() if builder._markup else None)

        # Удаляем временный файл
        try:
            os.remove(path)
        except:
            pass
    except FileNotFoundError as e:
        await _word_trigger_answer(message, f"❌ Ошибка: {e}. Проверь, что картинки лежат в images/")
    except Exception as e:
        await _word_trigger_answer(message, f"❌ Ошибка построения острова: {e}")


@dp.callback_query(F.data.startswith("island_buy_"))
async def island_buy_cb(callback: CallbackQuery):
    item = callback.data.replace("island_buy_", "")
    ok, msg = db.island_buy(callback.from_user.id, item)
    if ok:
        await callback.answer(f"✅ {msg}", show_alert=True)
        # Перерисовываем
        await callback.message.delete()
        await island_word(callback.message)
    else:
        await callback.answer(f"❌ {msg}", show_alert=True)

@dp.message(F.text.lower() == "подарки")
async def gifts_word(message: Message):
    user_id = message.from_user.id
    incoming = db.gift_list_incoming(user_id)

    if not incoming:
        await _word_trigger_answer(message, 
            "🎁 У тебя нет входящих подарков.\n\n"
            "Открыть/отправить подарки можно в веб-версии бота."
        )
        return

    text = f"🎁 **Входящие подарки: {len(incoming)}**\n\n"
    for g in incoming[:5]:
        text += f"• от @{g['from_username'] or g['from_user']} ({g['gift_type']})\n"
    text += "\nОткрой веб-версию → 🎁 Подарки, чтобы открыть."
    await _word_trigger_answer(message, text, parse_mode="Markdown")

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
    if not message.text:
        await message.answer("❌ Отправь username текстом.")
        return
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
        if not message.text:
            raise ValueError
        amount = int(message.text)
        if amount <= 0:
            await message.answer("❌ Сумма > 0")
            return
        if amount > 200_000:
            await message.answer("❌ Максимум за один перевод — 200 000 LDC.")
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
    except (ValueError, TypeError):
        await message.answer("❌ Введи сумму целым числом.")


@dp.callback_query(F.data == "confirm_transfer")
async def confirm_transfer(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    required = {"recipient_id", "recipient_name", "amount"}
    if not required.issubset(data):
        db.log_action(callback.from_user.id, "security", "Подтверждение перевода без активного состояния")
        await state.clear()
        await callback.answer("Перевод устарел. Начни заново.", show_alert=True)
        return
    rid = data["recipient_id"]
    rname = data["recipient_name"]
    amount = data["amount"]
    sid = callback.from_user.id
    ok, reason, count = db.transfer_coins_limited(sid, rid, amount, daily_limit=5)
    if not ok:
        if reason == "limit":
            db.log_action(sid, "security", f"Попытка перевода сверх суточного лимита; target_id={rid}; amount={amount}; count={count}")
            error_text = "🚫 Лимит исчерпан: максимум 5 успешных переводов в сутки (UTC)."
        elif reason == "max_amount":
            error_text = "❌ Максимум за один перевод — 200 000 LDC."
        elif reason == "balance":
            error_text = "❌ Недостаточно монет."
        elif reason == "recipient":
            error_text = "❌ Получатель не найден."
        else:
            db.log_action(sid, "security", f"Перевод отклонён; reason={reason}; target_id={rid}; amount={amount}")
            error_text = "❌ Перевод не выполнен: проверь данные и попробуй ещё раз."
        await callback.message.edit_text(error_text, reply_markup=back_to_main_menu())
        await state.clear()
        await callback.answer()
        return
    db.log_action(sid, "transfer", f"Перевод @{rname}; переводов сегодня {count}/5 UTC", amount=amount, target_user_id=rid)
    await callback.message.edit_text(
        f"✅ Переведено {amount} 🪙 @{rname}\nПереводов сегодня: {count}/5 (UTC)\nБаланс: {db.get_balance(sid)} 🪙",
        reply_markup=back_to_main_menu()
    )
    try:
        await bot.send_message(rid, f"💰 Вам перевели {amount} 🪙 от @{callback.from_user.username or sid}")
    except Exception:
        logger.exception("Не удалось выполнить необязательную операцию")
    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == "cancel_transfer")
async def cancel_transfer(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        db.log_action(callback.from_user.id, "cancel", "Отменён перевод")
    except Exception:
        logger.exception("Не удалось записать отмену перевода user_id=%s", callback.from_user.id)
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
        db.log_action(uid, "purchase", f"Покупка подтверждена владельцем: {item_type}", amount=price)
        await message.answer(f"✅ Покупка подтверждена")
        try:
            await bot.send_message(uid, f"✅ Покупка {item_type} подтверждена!")
        except Exception:
            logger.exception("Не удалось выполнить необязательную операцию")


@dp.message(F.text.startswith("/reject_"))
async def reject_purchase(message: Message):
    if message.from_user.id != config.OWNER_ID:
        return
    parts = message.text.split("_")
    if len(parts) >= 3:
        item_type = parts[1]
        try:
            uid = int(parts[2])
            db.log_action(uid, "purchase_reject", f"Покупка отклонена владельцем: {item_type}")
        except (TypeError, ValueError):
            logger.warning("Некорректная команда отклонения покупки: %r", message.text)
    await message.answer("❌ Отклонено")


# ==================== ОТМЕНА ====================

@dp.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        db.log_action(callback.from_user.id, "cancel", "Пользователь отменил действие")
    except Exception:
        logger.exception("Не удалось записать отмену действия user_id=%s", callback.from_user.id)
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
        logger.exception("Не удалось выполнить необязательную операцию")
    
    await callback.answer(f"🎉 Ты получил: {reward_text}", show_alert=True)
    
    # Удаляем из активных
    active_chests.pop(message_id, None)


@dp.message(Command("test_chest"))
async def test_chest(message: Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    await spawn_chest()
    await message.answer("✅ Сундук заспавнен в чате флуда")

# ==================== ТРЕКИНГ ЗАДАНИЙ ====================

def _track_group_message_tasks(
    user_id: int, username: str | None, *, is_reply: bool, is_photo: bool,
    is_audio: bool, is_night: bool,
):
    """Perform all synchronous profile/task database work in a worker thread."""
    user = db.get_user(user_id)
    if not user:
        db.create_user(user_id, username)
        user = db.get_user(user_id)
    elif username and user.get("username") != username:
        conn = sqlite3.connect(db.DB_NAME)
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
            conn.commit()
        finally:
            conn.close()
        user = db.get_user(user_id)

    if user and user["is_banned"]:
        return True, []

    completed = db.track_message_tasks(
        user_id,
        is_reply=is_reply,
        is_photo=is_photo,
        is_audio=is_audio,
        is_night=is_night,
    )
    return False, completed


@dp.message(F.chat.type.in_({"group", "supergroup"}), ~F.text.lower().startswith("кн "))
async def track_daily_tasks(message: Message, state: FSMContext):
    """Track group activity without blocking the bot's event loop on database I/O."""
    if message.from_user is None or message.from_user.is_bot:
        return

    current_state = await state.get_state()
    if current_state is not None:
        return

    user_id = message.from_user.id
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    try:
        is_banned, completed_tasks = await asyncio.to_thread(
            _track_group_message_tasks,
            user_id,
            message.from_user.username,
            is_reply=message.reply_to_message is not None,
            is_photo=message.photo is not None,
            is_audio=message.voice is not None or message.audio is not None,
            is_night=now.hour >= 20,
        )
        if is_banned:
            return

        if completed_tasks:
            username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
            for task in completed_tasks:
                notification = (
                    f"{username}, ты выполнил(а) ежедневное задание "
                    f"«{task['name']}» и получил(а) {task['reward']} 🪙"
                )
                try:
                    await bot.send_message(user_id, notification)
                except Exception as notify_error:
                    logger.info(
                        "Не удалось отправить личное уведомление о задании user_id=%s: %s",
                        user_id, notify_error,
                    )
                try:
                    await bot.send_message(config.FLUD_CHAT_ID, notification)
                except Exception as notify_error:
                    logger.warning("Не удалось отправить уведомление в чат флуда: %s", notify_error)
    except Exception:
        logger.exception("Ошибка трекинга ежедневных заданий user_id=%s", user_id)

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
                logger.exception("Не удалось выполнить необязательную операцию")


async def clear_old_members():
    while True:
        await asyncio.sleep(86400)
        db.clear_old_new_members(7)

async def blocked_users_cleanup_loop():
    """Remove users marked as having blocked the bot; they can re-register if they return."""
    while True:
        await asyncio.sleep(86400)
        try:
            removed = db.clear_blocked_users()
            if removed:
                logger.info("🧹 Удалено заблокировавших бота пользователей: %s", removed)
        except Exception:
            logger.exception("Не удалось очистить пользователей, заблокировавших бота")


async def daily_tasks_reset():
    """Сброс ежедневных заданий ровно в 05:00 по Москве."""
    while True:
        now = datetime.now(ZoneInfo("Europe/Moscow"))
        target = now.replace(hour=5, minute=0, second=0, microsecond=0)
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
            "В Lavender Dream появился сундук. Кто первый откроет — тому приз!\n\n"
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
                logger.exception("Не удалось выполнить необязательную операцию")
    except Exception as e:
        logger.error(f"Ошибка спавна сундука: {e}")


async def chest_scheduler():
    """Спавнит сундуки раз в 5–8 часов"""
    while True:
        # Случайное время ожидания от 5 до 8 часов
        wait = random.randint(18000, 28800)
        await asyncio.sleep(wait)
        await spawn_chest()

ISLAND_IMG_DIR = "images"
ISLAND_TEMP_DIR = "temp"


def build_island_image(user_id: int, state: dict) -> str:
    """Склеивает картинку острова с постройками.
    Возвращает путь к временному файлу."""
    os.makedirs(ISLAND_TEMP_DIR, exist_ok=True)

    # Базовый остров
    base_path = os.path.join(ISLAND_IMG_DIR, "island.png")
    if not os.path.exists(base_path):
        raise FileNotFoundError(f"Нет картинки {base_path}")

    base = Image.open(base_path).convert("RGBA")

    # Позиции для наложения (можно менять)
    # Формат: (x, y) — левый верхний угол картинки постройки
    POSITIONS = {
        "house": (int(base.width * 0.38), int(base.height * 0.32)),   # ближе к дорожке
        "pier":  (int(base.width * 0.72), int(base.height * 0.55)),   # на месте
        "ship":  (int(base.width * 0.85), int(base.height * 0.55)),   # правее и ниже причала
    }

    # Размеры построек (можно менять)
    SIZES = {
        "house": (int(base.width * 0.22), int(base.width * 0.22 * 0.7)),
        "pier":  (int(base.width * 0.25), int(base.width * 0.25 * 0.5)),
        "ship":  (int(base.width * 0.20), int(base.width * 0.20 * 0.7)),  # чуть меньше
    }

    layers = []
    if state.get("has_house"):
        layers.append(("house", "house.png"))
    if state.get("has_pier"):
        layers.append(("pier", "pier.png"))
    if state.get("has_ship"):
        layers.append(("ship", "ship.png"))

    for key, fname in layers:
        path = os.path.join(ISLAND_IMG_DIR, fname)
        if not os.path.exists(path):
            continue
        sprite = Image.open(path).convert("RGBA")
        size = SIZES.get(key, (sprite.width, sprite.height))
        sprite = sprite.resize(size, Image.LANCZOS)
        base.paste(sprite, POSITIONS.get(key, (0, 0)), sprite)

    out_path = os.path.join(ISLAND_TEMP_DIR, f"island_{user_id}.png")
    base.convert("RGB").save(out_path, "PNG")
    return out_path

async def notify_admins_purchase(buyer_id: int, item_type: str, price: int):
    """Шлёт ЛС всем админам/владельцу/тех.админам о покупке в каталоге."""
    item_name = db.get_item_name(item_type)
    buyer = db.get_user(buyer_id)
    buyer_name = f"@{buyer['username']}" if buyer and buyer.get("username") else f"ID{buyer_id}"

    text = (
        f"🛍️ Покупка в каталоге\n\n"
        f"👤 Покупатель: {buyer_name} (ID {buyer_id})\n"
        f"📦 Товар: {item_name}\n"
        f"💰 Цена: {price:,} 🪙\n"
        f"💼 Баланс после: {db.get_balance(buyer_id):,} 🪙"
    )

    targets = set(config.ADMIN_IDS) | set(config.TECH_ADMIN_IDS) | {config.OWNER_ID}
    for admin_id in targets:
        if admin_id == buyer_id:
            continue  # не слать самому себе
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.exception("Не удалось выполнить необязательную операцию")



@dp.callback_query(F.data == "world_event_claim")
async def world_event_claim_callback(callback: CallbackQuery):
    # Подтверждаем нажатие сразу: Telegram принимает callback-ответы лишь ограниченное время.
    # DB-функции синхронные, поэтому выполняем их в потоке, не блокируя event loop aiogram.
    try:
        await callback.answer()
    except Exception:
        logger.warning("Не удалось подтвердить callback мирового события", exc_info=True)

    try:
        result = await asyncio.to_thread(world_events.claim_event, callback.from_user.id)
        try:
            await asyncio.to_thread(
                world_events.record_season_activity, callback.from_user.id, "event", 1
            )
        except Exception:
            logger.exception("Награда события выдана, но сезонный прогресс не обновлён")

        if callback.message:
            await callback.message.answer(
                f"🎁 Получено: +{int(result['reward_ldc']):,} LDC и +{int(result['reward_xp'])} сезонного опыта."
            )
    except ValueError as exc:
        if callback.message:
            await callback.message.answer(f"ℹ️ {exc}")
    except Exception:
        logger.exception("Не удалось выдать награду глобального события")
        if callback.message:
            await callback.message.answer("Не удалось получить награду. Попробуй позже.")


async def announce_world_event(event: dict):
    message = (
        f"🌍 ГЛОБАЛЬНОЕ СОБЫТИЕ: {event.get('title', 'Событие')}\\n\\n"
        f"{event.get('description', '')}\\n\\n"
        f"⏳ Длительность: {max(1, int(event.get('remaining_seconds', 3600)) // 60)} мин.\\n"
        f"🎁 Награда за участие: {int(event.get('reward_ldc', 0)):,} LDC и "
        f"{int(event.get('reward_xp', 0))} сезонного опыта.\\n\\n"
        "Выполняй действия в Lavender Dream и нажми кнопку, чтобы забрать награду."
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎁 Забрать награду", callback_data="world_event_claim"))
    for user in db.get_all_users():
        uid = user.get("user_id")
        if not uid or user.get("is_banned"):
            continue
        try:
            await bot.send_message(int(uid), message, reply_markup=builder.as_markup())
        except Exception:
            # Пользователь мог заблокировать бота или запретить сообщения.
            logger.debug("Не удалось отправить уведомление о событии user_id=%s", uid)
        await asyncio.sleep(0.05)


def _event_datetime(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)


async def world_event_scheduler():
    """Database-backed scheduler: one global event at a time, with a 60-minute gap."""
    while True:
        await asyncio.sleep(30)
        try:
            if world_events.get_current_event():
                continue
            settings = await asyncio.to_thread(world_events.get_settings)
            now = datetime.utcnow()
            next_allowed = _event_datetime(settings.get("next_allowed_at"))
            if next_allowed and now < next_allowed:
                continue

            scheduled = await asyncio.to_thread(world_events.get_due_schedule)
            if scheduled:
                try:
                    event = world_events.start_event(
                        scheduled["event_type"],
                        duration_minutes=scheduled.get("duration_minutes") or 60,
                        reward_ldc=scheduled.get("reward_ldc") or 500,
                        reward_xp=scheduled.get("reward_xp") or 100,
                        title=scheduled.get("title"), description=scheduled.get("description"),
                        source="scheduled", created_by=scheduled.get("created_by"),
                        scheduled_id=scheduled["id"],
                    )
                    await announce_world_event(event)
                except RuntimeError:
                    # Another process may have started an event; re-check next cycle.
                    pass
                continue

            if not settings.get("random_enabled"):
                continue
            next_random = _event_datetime(settings.get("next_random_at"))
            if not next_random:
                conn = sqlite3.connect(os.environ.get("DB_PATH", db.DB_NAME))
                cur = conn.cursor()
                first_random = now + timedelta(minutes=random.randint(60, 240))
                cur.execute("UPDATE world_event_settings SET next_random_at = ? WHERE id = 1",
                            (first_random.isoformat(),))
                conn.commit()
                conn.close()
                continue
            if now >= next_random:
                event_type = random.choice(list(world_events.EVENT_TYPES.keys()))
                try:
                    event = world_events.start_event(
                        event_type,
                        duration_minutes=settings.get("default_duration_minutes") or 60,
                        reward_ldc=random.randint(300, 1500),
                        reward_xp=random.randint(75, 250),
                        source="random",
                    )
                    await announce_world_event(event)
                except RuntimeError:
                    pass
        except Exception:
            logger.exception("Ошибка планировщика глобальных событий")


# ==================== MAIN ====================

async def gifts_cleanup_loop():
    while True:
        await asyncio.sleep(86400)  # раз в день
        try:
            count = db.gift_cleanup_expired()
            if count > 0:
                logger.info(f"🎁 Очищено просроченных подарков: {count}")
        except Exception as e:
            logger.error(f"Ошибка очистки подарков: {e}")

async def islands_income_loop():
    while True:
        await asyncio.sleep(600)  # каждые 10 минут
        try:
            credited = db.islands_income_tick()
            for user_id, amount, type_, item_name in credited:
                try:
                    await bot.send_message(
                        user_id,
                        f"💰 Доход с острова!\n{item_name}: +{amount:,} 🪙"
                    )
                except Exception:
                    logger.exception("Не удалось выполнить необязательную операцию")
        except Exception as e:
            logger.error(f"Ошибка дохода острова: {e}")



async def halloween_event_scheduler():
    """Автоматически объявляет хэллоуинский ивент в полночь по московскому времени."""
    event_key = "halloween_2026"
    while True:
        now = datetime.now(ZoneInfo("Europe/Moscow"))
        if (now.year, now.month, now.day) == (2026, 10, 31) and not db.event_was_announced(event_key):
            try:
                await bot.send_message(
                    config.FLUD_CHAT_ID,
                    "🎃 ХЭЛЛОУИНСКИЙ ИВЕНТ LAVENDER DREAM! 👻\n\n"
                    "Праздник уже начался! Открой Mini App 31 октября и забери праздничную награду 500 🪙.\n"
                    "Ивент действует до 00:00 1 ноября."
                )
                db.mark_event_announced(event_key)
                logger.info("Хэллоуинский ивент объявлен")
            except Exception:
                logger.exception("Не удалось объявить хэллоуинский ивент")
        await asyncio.sleep(30)

async def main():
    # Подготавливаем схему один раз на запуск. Повторный вызов init_db()
    # выполнял те же миграции дважды и увеличивал время старта.
    from db_compat import init_db
    init_db()
    import database as db_module
    db_module.init_db()
    print_banner()
    logger.info("🚀 Lavender Dream Coin запускается...")
    db.remove_bot_from_new_members(bot.id)
    await bot.set_my_commands([
        {"command": "start", "description": "🏠 Запустить бота"},
        {"command": "help", "description": "ℹ️ Помощь"},
        {"command": "rules", "description": "📜 Правила Lavender Dream"},
        {"command": "feedback", "description": "✉️ Обратная связь"},
        {"command": "season", "description": "🌸 Сезон Lavender Dream"},
        {"command": "expeditions", "description": "🧭 Экспедиции питомцев"},
        {"command": "market", "description": "🏪 Системный магазин"},
        {"command": "achievements", "description": "🏆 Достижения"},
        {"command": "collection", "description": "📚 Коллекция и бестиарий"},
        {"command": "admin", "description": "👑 Админ-панель"},
    ])
    asyncio.create_task(weekly_clean_reminder())
    asyncio.create_task(clear_old_members())
    asyncio.create_task(blocked_users_cleanup_loop())
    asyncio.create_task(daily_tasks_reset())
    asyncio.create_task(pets_income())
    asyncio.create_task(cursed_egg_scheduler())
    asyncio.create_task(chest_scheduler())
    asyncio.create_task(islands_income_loop())
    asyncio.create_task(gifts_cleanup_loop())
    asyncio.create_task(halloween_event_scheduler())
    asyncio.create_task(world_event_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
